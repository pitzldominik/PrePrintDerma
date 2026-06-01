#!/usr/bin/env python3
"""
Fetch dermatology preprints from bioRxiv and medRxiv APIs.
Saves results to data.json (read by index.html).
Covers the last 18 months; the frontend filters by date/search term.
"""

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "preprint-derma-bot/1.0 (github-pages research tool)"})

BIO_TERMS = ["dermato", "skin", "venerol"]
MONTHS_BACK = 18
MAX_PAGES   = 30        # 30 × 100 = 3 000 records per source max
PAGE_DELAY  = 0.4       # seconds between requests (be polite)


def date_range():
    end   = date.today()
    start = end - timedelta(days=MONTHS_BACK * 30)
    return start.isoformat(), end.isoformat()


def extract_email(text: str) -> str:
    m = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", text, re.I)
    return m.group(0) if m else ""


def parse_authors(raw: str):
    """Return (authors_str, corresponding_author, email)."""
    if not raw:
        return "", "", ""
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    email = extract_email(raw)
    last  = parts[-1] if parts else ""
    corr  = re.sub(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", "", last, flags=re.I)
    corr  = re.sub(r"[<>()\[\]]", "", corr).strip()
    return "; ".join(parts), corr or (parts[0] if parts else ""), email


def fetch_server(server: str, start: str, end: str, filter_fn) -> list[dict]:
    results = []
    seen_dois: set[str] = set()
    cursor = 0

    for page in range(MAX_PAGES):
        url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
        print(f"  [{server}] page {page + 1}: {url}")
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ERROR: {e}")
            break

        collection = data.get("collection") or []
        if not collection:
            break

        for p in collection:
            if not filter_fn(p):
                continue
            doi = p.get("doi", "")
            if doi in seen_dois:
                continue
            seen_dois.add(doi)

            authors_str, corr, email = parse_authors(p.get("authors", ""))
            results.append({
                "date":                p.get("date", ""),
                "source":              "bioRxiv" if server == "biorxiv" else "medRxiv",
                "title":               p.get("title", ""),
                "authors":             authors_str,
                "corresponding_author": corr,
                "email":               email,
                "doi":                 doi,
                "url":                 f"https://doi.org/{doi}" if doi else "",
                "category":            p.get("category", ""),
                "abstract":            p.get("abstract", ""),
            })

        print(f"    → {len(collection)} records, {len(results)} matching so far")
        if len(collection) < 100:
            break
        cursor += 100
        time.sleep(PAGE_DELAY)

    return results


def bio_filter(p: dict) -> bool:
    text = (p.get("title", "") + " " + p.get("abstract", "")).lower()
    return any(t in text for t in BIO_TERMS)


def med_filter(p: dict) -> bool:
    return "dermatology" in (p.get("category", "") or "").lower()


def main():
    start, end = date_range()
    print(f"Date range: {start} → {end}")

    all_results: list[dict] = []

    print("\n── bioRxiv ──")
    all_results.extend(fetch_server("biorxiv", start, end, bio_filter))

    print("\n── medRxiv ──")
    all_results.extend(fetch_server("medrxiv", start, end, med_filter))

    # Global deduplication by DOI
    seen: set[str] = set()
    unique = []
    for r in all_results:
        key = r["doi"] or r["title"]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # Sort newest first
    unique.sort(key=lambda x: x["date"], reverse=True)

    out = {
        "fetched_at": date.today().isoformat(),
        "total":      len(unique),
        "records":    unique,
    }

    Path("data.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Saved {len(unique)} records to data.json")


if __name__ == "__main__":
    main()
