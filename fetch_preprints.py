#!/usr/bin/env python3
"""
Fetch dermatology preprints from bioRxiv and medRxiv APIs.
Uses WEEKLY chunks to stay within the API's 100-results-per-page limit.
Large date ranges (months) cause the API to throttle to ~30 results;
weekly chunks reliably return up to 100 per page.
"""

import json
import re
import time
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "preprint-derma-bot/3.0",
    "Accept": "application/json"
})

BIO_TERMS   = ["dermato", "skin", "venerol"]
MONTHS_BACK = 18
PAGE_DELAY  = 0.35

# ── Date helpers ─────────────────────────────────────────────────

def weekly_chunks(months=MONTHS_BACK):
    """Return list of (start, end) 7-day windows covering the period, newest first."""
    chunks = []
    end = date.today()
    start_limit = end - timedelta(days=months * 30)
    while end > start_limit:
        start = max(end - timedelta(days=6), start_limit)
        chunks.append((start.isoformat(), end.isoformat()))
        end = start - timedelta(days=1)
    return chunks  # newest first

# ── API fetch ────────────────────────────────────────────────────

def fetch_page(server, start, end, cursor, retries=3):
    # Use api.medrxiv.org as it's the canonical host for both servers
    url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            return data.get("collection") or [], data.get("messages", [{}])[0]
        except Exception as e:
            print(f"      attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return [], {}

def fetch_chunk(server, start, end, filter_fn, seen):
    """Paginate through one weekly window. Returns new matching records."""
    results = []
    cursor  = 0
    page    = 0

    while True:
        page += 1
        collection, msg = fetch_page(server, start, end, cursor)

        if not collection:
            break

        count = msg.get("count", len(collection))
        matched = 0
        for p in collection:
            if not filter_fn(p):
                continue
            doi = p.get("doi", "") or p.get("title", "")
            if doi in seen:
                continue
            seen.add(doi)
            matched += 1
            authors_str, corr, email = parse_authors(p.get("authors", ""))
            results.append({
                "date":                 p.get("date", ""),
                "source":               "bioRxiv" if server == "biorxiv" else "medRxiv",
                "title":                p.get("title", ""),
                "authors":              authors_str,
                "corresponding_author": corr,
                "email":                email,
                "doi":                  p.get("doi", ""),
                "url":                  f"https://doi.org/{p['doi']}" if p.get("doi") else "",
                "category":             p.get("category", ""),
                "abstract":             p.get("abstract", ""),
            })

        if len(collection) < 100:
            break
        cursor += 100
        time.sleep(PAGE_DELAY)

    return results

# ── Author parsing ───────────────────────────────────────────────

def extract_email(text):
    m = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", text or "", re.I)
    return m.group(0) if m else ""

def parse_authors(raw):
    if not raw:
        return "", "", ""
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    email = extract_email(raw)
    last  = parts[-1] if parts else ""
    corr  = re.sub(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", "", last, flags=re.I)
    corr  = re.sub(r"[<>()\[\]]", "", corr).strip()
    return "; ".join(parts), corr or (parts[0] if parts else ""), email

# ── Filters ──────────────────────────────────────────────────────

def bio_filter(p):
    text = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
    return any(t in text for t in BIO_TERMS)

def med_filter(p):
    return "dermatology" in (p.get("category") or "").lower()

# ── Main fetch ───────────────────────────────────────────────────

def fetch_server(server, months, filter_fn, label):
    chunks  = weekly_chunks(months)
    seen    = set()
    results = []
    total_weeks = len(chunks)

    print(f"\n── {label} ({months} months = {total_weeks} weekly chunks) ──")

    for i, (start, end) in enumerate(chunks):
        new = fetch_chunk(server, start, end, filter_fn, seen)
        results.extend(new)
        if new or (i % 4 == 0):  # print every 4 weeks or when matches found
            print(f"  Week {i+1:3d}/{total_weeks}  {start}→{end}  +{len(new):3d} matches  total={len(results)}")
        time.sleep(PAGE_DELAY)

    print(f"  ✓ {label}: {len(results)} preprints found")
    return results

def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else MONTHS_BACK
    print(f"Fetching last {months} months in weekly chunks …")
    print(f"Approx. {months * 4} API windows per server\n")

    all_results = []
    all_results.extend(fetch_server("biorxiv", months, bio_filter, "bioRxiv"))
    all_results.extend(fetch_server("medrxiv", months, med_filter, "medRxiv"))

    # Global dedup
    seen, unique = set(), []
    for r in all_results:
        key = r["doi"] or r["title"]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(key=lambda x: x["date"], reverse=True)

    out = {
        "fetched_at": date.today().isoformat(),
        "months":     months,
        "total":      len(unique),
        "records":    unique,
    }

    Path("data.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    bio = sum(1 for r in unique if r["source"] == "bioRxiv")
    med = sum(1 for r in unique if r["source"] == "medRxiv")
    print(f"\n✓ Saved {len(unique)} records  (bioRxiv: {bio}, medRxiv: {med})")

    if len(unique) == 0:
        print("⚠ 0 records — check API availability or filter terms")
        sys.exit(1)

if __name__ == "__main__":
    main()
