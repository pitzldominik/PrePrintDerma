#!/usr/bin/env python3
"""
Fetch dermatology preprints via Europe PMC REST API.
- No API key required
- Real keyword search (not blind pagination)
- Up to 1000 results per page
- Covers bioRxiv + medRxiv

bioRxiv query : SRC:PPR AND PUBLISHER:biorxiv AND (dermato* OR skin OR venerol*)
medRxiv query : SRC:PPR AND PUBLISHER:medrxiv AND dermatology (title/abstract)
                + category filter applied locally since Europe PMC has the subject area

API docs: https://europepmc.org/RestfulWebService
"""

import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE_URL   = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
SESSION    = requests.Session()
SESSION.headers.update({
    "User-Agent": "preprint-derma/4.0 (github-pages; contact: preprint-derma)",
    "Accept":     "application/json",
})

MONTHS_BACK = 18
PAGE_SIZE   = 1000   # Europe PMC allows up to 1000
PAGE_DELAY  = 0.5

# ── Date helper ──────────────────────────────────────────────────

def start_date(months=MONTHS_BACK):
    d = date.today() - timedelta(days=months * 30)
    return d.strftime("%Y-%m-%d")

# ── Europe PMC search ────────────────────────────────────────────

def epmc_search(query, cursor_mark="*", retries=3):
    """One page of Europe PMC results. Returns (results_list, next_cursor, hit_count)."""
    params = {
        "query":       query,
        "resultType":  "core",
        "pageSize":    PAGE_SIZE,
        "format":      "json",
        "cursorMark":  cursor_mark,
        "sort":        "P_PDATE_D desc",
    }
    for attempt in range(retries):
        try:
            r = SESSION.get(BASE_URL, params=params, timeout=30)
            r.raise_for_status()
            d = r.json()
            results   = d.get("resultList", {}).get("result", [])
            next_cur  = d.get("nextCursorMark", "")
            hit_count = d.get("hitCount", 0)
            return results, next_cur, hit_count
        except Exception as e:
            print(f"    attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return [], "", 0

def epmc_all(query, label):
    """Paginate through all Europe PMC results for a query."""
    all_results = []
    cursor      = "*"
    page        = 0

    while True:
        page += 1
        results, next_cursor, hit_count = epmc_search(query, cursor)

        if page == 1:
            print(f"  Total hits reported by API: {hit_count}")

        if not results:
            break

        all_results.extend(results)
        print(f"  Page {page}: +{len(results)} records → {len(all_results)} fetched")

        # Stop if we've got everything or cursor didn't advance
        if not next_cursor or next_cursor == cursor or len(all_results) >= hit_count:
            break

        cursor = next_cursor
        time.sleep(PAGE_DELAY)

    print(f"  ✓ {label}: {len(all_results)} records fetched")
    return all_results

# ── Author / email parsing ───────────────────────────────────────

def extract_email(text):
    if not text:
        return ""
    m = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", text, re.I)
    return m.group(0) if m else ""

def parse_epmc_authors(record):
    """
    Europe PMC provides authorList.author with firstName, lastName, affiliation.
    The corresponding author is often flagged or is the last author.
    """
    authors_raw = (record.get("authorList") or {}).get("author") or []
    if not authors_raw:
        # Fallback: authorString
        raw = record.get("authorString", "")
        parts = [p.strip() for p in raw.rstrip(".").split(",") if p.strip()]
        corr  = parts[-1] if parts else ""
        return raw, corr, ""

    names = []
    corr_name  = ""
    corr_email = ""

    for a in authors_raw:
        first = a.get("firstName", "")
        last  = a.get("lastName",  "")
        full  = f"{last} {first}".strip() if last else first
        names.append(full)

        # Check for corresponding author flag
        if a.get("authorAffiliationsList"):
            affs = a["authorAffiliationsList"].get("authorAffiliation", [])
            for aff in affs:
                email = extract_email(aff.get("affiliation", ""))
                if email and not corr_email:
                    corr_email = email
                    corr_name  = full

    authors_str = "; ".join(names)
    if not corr_name and names:
        corr_name = names[-1]   # default: last author

    # Also try fullTextUrlList for email hints
    if not corr_email:
        corr_email = extract_email(record.get("abstractText", ""))

    return authors_str, corr_name, corr_email

# ── Record normaliser ────────────────────────────────────────────

def normalise(record, source_label):
    doi = record.get("doi", "")
    authors_str, corr, email = parse_epmc_authors(record)

    # Publication date: firstPublicationDate or pubYear
    pub_date = record.get("firstPublicationDate", "") or record.get("pubYear", "")

    return {
        "date":                 pub_date,
        "source":               source_label,
        "title":                record.get("title", "").rstrip("."),
        "authors":              authors_str,
        "corresponding_author": corr,
        "email":                email,
        "doi":                  doi,
        "url":                  f"https://doi.org/{doi}" if doi else
                                record.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url", ""),
        "abstract":             record.get("abstractText", ""),
    }

# ── Main ─────────────────────────────────────────────────────────

def main():
    months  = int(sys.argv[1]) if len(sys.argv) > 1 else MONTHS_BACK
    cutoff  = start_date(months)
    today   = date.today().isoformat()

    print(f"Fetching preprints from {cutoff} to {today} ({months} months)")
    print(f"Using Europe PMC REST API — no API key needed\n")

    all_results = []
    seen_dois   = set()

    # ── bioRxiv: keyword search ──────────────────────────────────
    # dermato* wildcard not supported directly; use OR expansion
    bio_query = (
        f"SRC:PPR AND PUBLISHER:biorxiv AND "
        f"(TITLE:dermatol* OR TITLE:dermatitis OR TITLE:dermatos* OR "
        f"TITLE:skin OR TITLE:venerol* OR "
        f"ABSTRACT:dermatol* OR ABSTRACT:dermatitis OR ABSTRACT:dermatos* OR "
        f"ABSTRACT:skin OR ABSTRACT:venerol*) AND "
        f"FIRST_PDATE:[{cutoff} TO {today}]"
    )
    print(f"── bioRxiv query ──")
    print(f"  {bio_query}\n")
    bio_records = epmc_all(bio_query, "bioRxiv")

    for r in bio_records:
        doi = r.get("doi", "") or r.get("title", "")
        if doi in seen_dois:
            continue
        seen_dois.add(doi)
        all_results.append(normalise(r, "bioRxiv"))

    time.sleep(PAGE_DELAY)

    # ── medRxiv: dermatology subject area ────────────────────────
    # Europe PMC indexes the medRxiv category in the subject field
    med_query = (
        f"SRC:PPR AND PUBLISHER:medrxiv AND "
        f"(TITLE:dermatol* OR TITLE:dermatitis OR TITLE:dermatos* OR "
        f"TITLE:skin OR TITLE:venerol* OR "
        f"ABSTRACT:dermatol* OR ABSTRACT:dermatitis OR ABSTRACT:dermatos* OR "
        f"ABSTRACT:skin OR ABSTRACT:venerol* OR "
        f"SUBJECT:dermatology) AND "
        f"FIRST_PDATE:[{cutoff} TO {today}]"
    )
    print(f"\n── medRxiv query ──")
    print(f"  {med_query}\n")
    med_records = epmc_all(med_query, "medRxiv")

    for r in med_records:
        doi = r.get("doi", "") or r.get("title", "")
        if doi in seen_dois:
            continue
        seen_dois.add(doi)
        all_results.append(normalise(r, "medRxiv"))

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

    bio = sum(1 for r in all_results if r["source"] == "bioRxiv")
    med = sum(1 for r in all_results if r["source"] == "medRxiv")
    print(f"\n✓ Saved {len(all_results)} records to data.json")
    print(f"  bioRxiv: {bio}  |  medRxiv: {med}")

    if len(all_results) == 0:
        print("\n⚠ 0 records — check API or query syntax")
        sys.exit(1)


if __name__ == "__main__":
    main()
