#!/usr/bin/env python3
"""
Fetch dermatology preprints — v5
- Europe PMC for search/discovery (correct counts, real keyword search)
- bioRxiv/medRxiv JATS XML API for corresponding author email
- medRxiv: ONLY official "Dermatology" category (matches website exactly)
- bioRxiv: title/abstract keyword search (dermato*, skin, venerol*)
"""

import json, re, sys, time
from datetime import date, timedelta
from pathlib import Path

import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "preprint-derma/5.0",
    "Accept":     "application/json",
})

MONTHS_BACK = 18
PAGE_DELAY  = 0.4

# ── Dates ────────────────────────────────────────────────────────

def date_range(months=MONTHS_BACK):
    today  = date.today()
    cutoff = today - timedelta(days=months * 30)
    return cutoff.isoformat(), today.isoformat()

# ── Europe PMC ───────────────────────────────────────────────────

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

def epmc_all(query, label):
    """Fetch all pages from Europe PMC for a query."""
    results, cursor, page = [], "*", 0
    while True:
        page += 1
        try:
            r = SESSION.get(EPMC, params={
                "query":      query,
                "resultType": "core",
                "pageSize":   1000,
                "format":     "json",
                "cursorMark": cursor,
                "sort":       "P_PDATE_D desc",
            }, timeout=30)
            r.raise_for_status()
            d        = r.json()
            batch    = d.get("resultList", {}).get("result", [])
            next_cur = d.get("nextCursorMark", "")
            hits     = d.get("hitCount", 0)
            if page == 1:
                print(f"  API hitCount: {hits}")
            if not batch:
                break
            results.extend(batch)
            print(f"  Page {page}: +{len(batch)} → {len(results)} total")
            if not next_cur or next_cur == cursor or len(results) >= hits:
                break
            cursor = next_cur
            time.sleep(PAGE_DELAY)
        except Exception as e:
            print(f"  ERROR page {page}: {e}")
            break
    print(f"  ✓ {label}: {len(results)} records from Europe PMC")
    return results

# ── Email from JATS XML ──────────────────────────────────────────

EMAIL_CACHE = {}

def fetch_email_from_xml(doi, server="biorxiv"):
    """
    Fetch corresponding author email from bioRxiv/medRxiv JATS XML.
    API: https://api.biorxiv.org/xml/{server}/{doi}
    Email is in <corresp> or <email> tags in the XML.
    """
    if doi in EMAIL_CACHE:
        return EMAIL_CACHE[doi]

    # Try both XML endpoints
    urls = [
        f"https://www.biorxiv.org/content/{doi}.source.xml",
        f"https://www.medrxiv.org/content/{doi}.source.xml",
    ]
    for url in urls:
        try:
            r = SESSION.get(url, timeout=15)
            if r.status_code != 200:
                continue
            text = r.text
            # Look for <email> tags (most reliable)
            emails = re.findall(r"<email[^>]*>(.*?)</email>", text, re.DOTALL)
            if emails:
                email = emails[0].strip()
                EMAIL_CACHE[doi] = email
                return email
            # Look for email pattern in <corresp> blocks
            corresps = re.findall(r"<corresp[^>]*>(.*?)</corresp>", text, re.DOTALL | re.IGNORECASE)
            for c in corresps:
                m = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", c)
                if m:
                    EMAIL_CACHE[doi] = m.group(0)
                    return m.group(0)
            # Broad search in full XML
            m = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", text)
            if m:
                email = m.group(0)
                # Skip generic emails
                if not any(x in email.lower() for x in ["biorxiv", "medrxiv", "rxivist", "doi"]):
                    EMAIL_CACHE[doi] = email
                    return email
        except Exception:
            pass

    EMAIL_CACHE[doi] = ""
    return ""

# ── Author parsing from Europe PMC ──────────────────────────────

def parse_authors(record):
    authors_raw = (record.get("authorList") or {}).get("author") or []
    if not authors_raw:
        raw   = record.get("authorString", "").rstrip(".")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return raw, parts[-1] if parts else "", ""

    names = []
    for a in authors_raw:
        first = a.get("firstName", "")
        last  = a.get("lastName",  "")
        full  = f"{last} {first}".strip() if last else first
        names.append(full)

    corr = names[-1] if names else ""
    return "; ".join(names), corr, ""   # email fetched separately

# ── Normalise Europe PMC record ──────────────────────────────────

def normalise(record, source_label):
    doi = record.get("doi", "")
    authors_str, corr, _ = parse_authors(record)
    pub_date = record.get("firstPublicationDate", "") or str(record.get("pubYear", ""))

    return {
        "date":                 pub_date,
        "source":               source_label,
        "title":                record.get("title", "").rstrip("."),
        "authors":              authors_str,
        "corresponding_author": corr,
        "email":                "",    # filled in second pass
        "doi":                  doi,
        "url":                  f"https://doi.org/{doi}" if doi else "",
        "abstract":             record.get("abstractText", ""),
    }

# ── Enrich with emails ───────────────────────────────────────────

def enrich_emails(records):
    """Second pass: fetch email from JATS XML for each record."""
    total = len(records)
    print(f"\n── Fetching emails from JATS XML ({total} records) ──")
    found = 0
    for i, rec in enumerate(records):
        doi = rec.get("doi", "")
        if not doi:
            continue
        server = "medrxiv" if rec["source"] == "medRxiv" else "biorxiv"
        email  = fetch_email_from_xml(doi, server)
        if email:
            rec["email"] = email
            found += 1
        if (i + 1) % 20 == 0 or (i + 1) == total:
            print(f"  {i+1}/{total} processed, {found} emails found so far")
        time.sleep(0.2)  # polite rate limiting
    print(f"  ✓ Emails found: {found}/{total}")

# ── Main ─────────────────────────────────────────────────────────

def main():
    months       = int(sys.argv[1]) if len(sys.argv) > 1 else MONTHS_BACK
    cutoff, today = date_range(months)

    print(f"Date range: {cutoff} → {today}  ({months} months)\n")

    all_results = []
    seen_dois   = set()

    # ── medRxiv: official Dermatology category only ──────────────
    # Matches what medrxiv.org/collection/dermatology shows
    med_query = (
        f"SRC:PPR AND PUBLISHER:medrxiv AND "
        f"SUBJECT:\"Dermatology\" AND "
        f"FIRST_PDATE:[{cutoff} TO {today}]"
    )
    print(f"── medRxiv (Dermatology category) ──")
    print(f"  Query: {med_query}")
    med_records = epmc_all(med_query, "medRxiv")

    for r in med_records:
        doi = r.get("doi", "") or r.get("title", "")
        if doi in seen_dois:
            continue
        seen_dois.add(doi)
        all_results.append(normalise(r, "medRxiv"))

    time.sleep(PAGE_DELAY)

    # ── bioRxiv: keyword search in title + abstract ──────────────
    bio_query = (
        f"SRC:PPR AND PUBLISHER:biorxiv AND "
        f"(TITLE:dermatol* OR TITLE:dermatitis OR TITLE:dermatos* OR "
        f" TITLE:skin OR TITLE:venerol* OR "
        f" ABSTRACT:dermatol* OR ABSTRACT:dermatitis OR ABSTRACT:dermatos* OR "
        f" ABSTRACT:skin OR ABSTRACT:venerol*) AND "
        f"FIRST_PDATE:[{cutoff} TO {today}]"
    )
    print(f"\n── bioRxiv (keyword search) ──")
    print(f"  Query: {bio_query}")
    bio_records = epmc_all(bio_query, "bioRxiv")

    for r in bio_records:
        doi = r.get("doi", "") or r.get("title", "")
        if doi in seen_dois:
            continue
        seen_dois.add(doi)
        all_results.append(normalise(r, "bioRxiv"))

    # ── Enrich with emails from JATS XML ─────────────────────────
    enrich_emails(all_results)

    # ── Sort & save ──────────────────────────────────────────────
    all_results.sort(key=lambda x: x["date"], reverse=True)

    out = {
        "fetched_at": today,
        "months":     months,
        "total":      len(all_results),
        "records":    all_results,
    }

    Path("data.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    bio   = sum(1 for r in all_results if r["source"] == "bioRxiv")
    med   = sum(1 for r in all_results if r["source"] == "medRxiv")
    email = sum(1 for r in all_results if r["email"])
    print(f"\n✓ Saved {len(all_results)} records")
    print(f"  bioRxiv: {bio}  |  medRxiv: {med}  |  with email: {email}")

    if len(all_results) == 0:
        print("⚠ 0 records — check API or query")
        sys.exit(1)

if __name__ == "__main__":
    main()
