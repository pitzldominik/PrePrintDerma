#!/usr/bin/env python3
"""
Fetch dermatology preprints — v6 FINAL

medRxiv : api.biorxiv.org/details/medrxiv?category=dermatology
          → official category filter, returns author_corresponding directly
bioRxiv : Europe PMC keyword search (dermato*, skin, venerol*)
          → real full-text search across all ~10k preprints/day

Email strategy:
  - biorxiv/medrxiv API returns author_corresponding_institution but NOT email
  - We fetch email from the JATS XML path returned by the API (jatsxml field)
    Format: https://www.biorxiv.org/content/{doi}.full.xml
"""

import json, re, sys, time
from datetime import date, timedelta
from pathlib import Path
import requests

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "preprint-derma/6.0", "Accept": "application/json"})

MONTHS_BACK = 18
PAGE_DELAY  = 0.35

# ── Helpers ──────────────────────────────────────────────────────

def date_range(months=MONTHS_BACK):
    today  = date.today()
    cutoff = today - timedelta(days=months * 30)
    return cutoff.isoformat(), today.isoformat()

def weekly_chunks(cutoff_str, today_str):
    """Split date range into 7-day windows (avoids API throttling)."""
    chunks, end = [], date.fromisoformat(today_str)
    start_limit = date.fromisoformat(cutoff_str)
    while end > start_limit:
        start = max(end - timedelta(days=6), start_limit)
        chunks.append((start.isoformat(), end.isoformat()))
        end = start - timedelta(days=1)
    return chunks

def get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"    attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None

def extract_email(text):
    if not text:
        return ""
    m = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", text, re.I)
    if m:
        e = m.group(0)
        # Skip institutional/generic addresses
        if not any(x in e.lower() for x in ["biorxiv", "medrxiv", "example", "doi.org"]):
            return e
    return ""

# ── Email from JATS XML ──────────────────────────────────────────

def fetch_email(doi, server):
    """Fetch corresponding author email from JATS XML."""
    base = "https://www.biorxiv.org" if server == "biorxiv" else "https://www.medrxiv.org"
    for suffix in [".full.xml", ".article.xml"]:
        r = get(f"{base}/content/{doi}{suffix}")
        if not r or r.status_code != 200:
            continue
        text = r.text
        # <email> tags are most reliable
        for tag in re.findall(r"<email[^>]*>(.*?)</email>", text, re.DOTALL):
            e = extract_email(tag.strip())
            if e:
                return e
        # <corresp> blocks
        for block in re.findall(r"<corresp[^>]*>(.*?)</corresp>", text, re.DOTALL | re.I):
            e = extract_email(block)
            if e:
                return e
    return ""

# ── medRxiv via biorxiv API with category filter ─────────────────

def fetch_medrxiv_dermatology(cutoff, today):
    """
    Uses the official category querystring:
    api.biorxiv.org/details/medrxiv/{start}/{end}/{cursor}?category=dermatology
    Returns author_corresponding directly — no XML needed for name.
    We still fetch XML for email.
    """
    chunks  = weekly_chunks(cutoff, today)
    results = []
    seen    = set()
    total   = len(chunks)

    print(f"\n── medRxiv (category=dermatology, {total} weekly chunks) ──")

    for i, (start, end) in enumerate(chunks):
        cursor = 0
        while True:
            url = f"https://api.biorxiv.org/details/medrxiv/{start}/{end}/{cursor}"
            r   = get(url, params={"category": "dermatology"})
            if not r:
                break
            data       = r.json()
            collection = data.get("collection") or []
            if not collection:
                break
            for p in collection:
                doi = p.get("doi", "")
                if not doi or doi in seen:
                    continue
                seen.add(doi)
                results.append({
                    "_doi":    doi,
                    "_server": "medrxiv",
                    "date":    p.get("date", ""),
                    "source":  "medRxiv",
                    "title":   p.get("title", ""),
                    "authors": p.get("authors", ""),
                    "corresponding_author": p.get("author_corresponding", ""),
                    "email":   "",
                    "doi":     doi,
                    "url":     f"https://doi.org/{doi}",
                    "abstract": p.get("abstract", ""),
                })
            if len(collection) < 100:
                break
            cursor += 100
            time.sleep(PAGE_DELAY)

        if (i + 1) % 8 == 0 or (i + 1) == total:
            print(f"  Chunk {i+1}/{total} done — {len(results)} records so far")
        time.sleep(PAGE_DELAY)

    print(f"  ✓ medRxiv: {len(results)} dermatology preprints")
    return results

# ── bioRxiv via Europe PMC keyword search ────────────────────────

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

def fetch_biorxiv_epmc(cutoff, today):
    query = (
        f"SRC:PPR AND PUBLISHER:biorxiv AND "
        f"(TITLE:dermatol* OR TITLE:dermatitis OR TITLE:dermatos* OR "
        f" TITLE:skin OR TITLE:venerol* OR "
        f" ABSTRACT:dermatol* OR ABSTRACT:dermatitis OR ABSTRACT:dermatos* OR "
        f" ABSTRACT:skin OR ABSTRACT:venerol*) AND "
        f"FIRST_PDATE:[{cutoff} TO {today}]"
    )
    print(f"\n── bioRxiv (Europe PMC keyword search) ──")
    print(f"  Query: {query}")

    results, cursor, page = [], "*", 0
    while True:
        page += 1
        r = get(EPMC, params={
            "query": query, "resultType": "core",
            "pageSize": 1000, "format": "json",
            "cursorMark": cursor, "sort": "P_PDATE_D desc",
        })
        if not r:
            break
        d        = r.json()
        batch    = d.get("resultList", {}).get("result", [])
        next_cur = d.get("nextCursorMark", "")
        hits     = d.get("hitCount", 0)
        if page == 1:
            print(f"  API hitCount: {hits}")
        if not batch:
            break
        for rec in batch:
            doi = rec.get("doi", "")
            # Parse authors from authorList
            authors_raw = (rec.get("authorList") or {}).get("author") or []
            names = []
            for a in authors_raw:
                fn = a.get("firstName",""); ln = a.get("lastName","")
                names.append(f"{ln} {fn}".strip() if ln else fn)
            authors_str = "; ".join(names) or rec.get("authorString","").rstrip(".")
            corr = names[-1] if names else ""
            results.append({
                "_doi":    doi,
                "_server": "biorxiv",
                "date":    rec.get("firstPublicationDate","") or str(rec.get("pubYear","")),
                "source":  "bioRxiv",
                "title":   rec.get("title","").rstrip("."),
                "authors": authors_str,
                "corresponding_author": corr,
                "email":   "",
                "doi":     doi,
                "url":     f"https://doi.org/{doi}" if doi else "",
                "abstract": rec.get("abstractText",""),
            })
        print(f"  Page {page}: +{len(batch)} → {len(results)} total")
        if not next_cur or next_cur == cursor or len(results) >= hits:
            break
        cursor = next_cur
        time.sleep(PAGE_DELAY)

    print(f"  ✓ bioRxiv: {len(results)} records")
    return results

# ── Email enrichment ─────────────────────────────────────────────

def enrich_emails(records):
    total = len(records)
    found = 0
    print(f"\n── Email enrichment via JATS XML ({total} records) ──")
    for i, rec in enumerate(records):
        doi    = rec.get("_doi","")
        server = rec.get("_server","biorxiv")
        if doi:
            email = fetch_email(doi, server)
            if email:
                rec["email"] = email
                found += 1
        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"  {i+1}/{total} — emails found: {found}")
        time.sleep(0.15)
    print(f"  ✓ Emails found: {found}/{total} ({100*found//total if total else 0}%)")

# ── Main ─────────────────────────────────────────────────────────

def main():
    months        = int(sys.argv[1]) if len(sys.argv) > 1 else MONTHS_BACK
    cutoff, today = date_range(months)
    print(f"Date range: {cutoff} → {today}  ({months} months)\n")

    all_results = []
    seen_dois   = set()

    # medRxiv first (official category filter)
    med = fetch_medrxiv_dermatology(cutoff, today)
    for r in med:
        k = r["doi"] or r["title"]
        if k not in seen_dois:
            seen_dois.add(k)
            all_results.append(r)

    # bioRxiv via Europe PMC
    bio = fetch_biorxiv_epmc(cutoff, today)
    for r in bio:
        k = r["doi"] or r["title"]
        if k not in seen_dois:
            seen_dois.add(k)
            all_results.append(r)

    # Enrich emails
    enrich_emails(all_results)

    # Clean internal keys, sort
    for r in all_results:
        r.pop("_doi", None)
        r.pop("_server", None)
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

    bio_n   = sum(1 for r in all_results if r["source"] == "bioRxiv")
    med_n   = sum(1 for r in all_results if r["source"] == "medRxiv")
    email_n = sum(1 for r in all_results if r["email"])
    print(f"\n✓ Saved {len(all_results)} records to data.json")
    print(f"  bioRxiv: {bio_n}  |  medRxiv: {med_n}  |  with email: {email_n}")
    if not all_results:
        sys.exit(1)

if __name__ == "__main__":
    main()
