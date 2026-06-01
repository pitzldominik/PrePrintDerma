#!/usr/bin/env python3
"""
Fetch dermatology preprints from bioRxiv and medRxiv APIs.
Robust pagination with detailed logging.
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
    "User-Agent": "preprint-derma-bot/2.0 (github-pages research tool)",
    "Accept": "application/json"
})

BIO_TERMS  = ["dermato", "skin", "venerol"]
MONTHS_BACK = 18
PAGE_DELAY  = 0.5   # seconds between requests

def date_range(months=MONTHS_BACK):
    end   = date.today()
    start = end - timedelta(days=months * 30)
    return start.isoformat(), end.isoformat()

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

def fetch_page(server, start, end, cursor, retries=3):
    url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}/json"
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            # API returns messages list on error
            msgs = data.get("messages", [])
            if msgs:
                status = msgs[0].get("status", "")
                count  = msgs[0].get("count", 0)
                total  = msgs[0].get("total", 0)
                print(f"    API: status={status}, count={count}, total={total}")
            return data.get("collection") or []
        except Exception as e:
            print(f"    Attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return []

def fetch_server(server, start, end, filter_fn, label):
    results  = []
    seen     = set()
    cursor   = 0
    page     = 0
    empty_streak = 0

    print(f"\n── {label} ({start} → {end}) ──")

    while True:
        page += 1
        print(f"  Page {page} (cursor={cursor}) ...", end=" ", flush=True)
        collection = fetch_page(server, start, end, cursor)

        if not collection:
            empty_streak += 1
            print(f"empty (streak={empty_streak})")
            if empty_streak >= 2:
                print(f"  → stopping after {empty_streak} empty pages")
                break
            cursor += 100
            time.sleep(PAGE_DELAY)
            continue

        empty_streak = 0
        matched = 0
        for p in collection:
            if not filter_fn(p):
                continue
            doi = p.get("doi", "")
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
                "doi":                  doi,
                "url":                  f"https://doi.org/{doi}" if doi else "",
                "category":             p.get("category", ""),
                "abstract":             p.get("abstract", ""),
            })

        print(f"{len(collection)} records, {matched} matched → {len(results)} total")

        if len(collection) < 100:
            print(f"  → last page reached")
            break

        cursor += 100
        time.sleep(PAGE_DELAY)

    print(f"  ✓ {label}: {len(results)} preprints collected")
    return results

def bio_filter(p):
    text = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
    return any(t in text for t in BIO_TERMS)

def med_filter(p):
    return "dermatology" in (p.get("category") or "").lower()

def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else MONTHS_BACK
    start, end = date_range(months)
    print(f"Date range: {start} → {end}  ({months} months)")

    all_results = []
    all_results.extend(fetch_server("biorxiv", start, end, bio_filter,  "bioRxiv"))
    all_results.extend(fetch_server("medrxiv", start, end, med_filter,  "medRxiv"))

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
    print(f"\n✓ Saved {len(unique)} records to data.json")

    # Summary
    bio = sum(1 for r in unique if r["source"] == "bioRxiv")
    med = sum(1 for r in unique if r["source"] == "medRxiv")
    print(f"  bioRxiv: {bio}, medRxiv: {med}")

    if len(unique) == 0:
        print("\n⚠ WARNING: 0 records saved. Possible causes:")
        print("  - API temporarily unavailable")
        print("  - Date range too narrow")
        print("  - Filter terms too restrictive")
        sys.exit(1)

if __name__ == "__main__":
    main()
