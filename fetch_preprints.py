#!/usr/bin/env python3
"""
Fetch dermatology preprints from bioRxiv and medRxiv APIs.
Queries in monthly chunks to avoid API throttling (API caps at 30 results
for large date ranges but returns up to 100 for narrow ranges).
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

BIO_TERMS   = ["dermato", "skin", "venerol"]
MONTHS_BACK = 18
PAGE_DELAY  = 0.4   # seconds between requests

# ── Date helpers ─────────────────────────────────────────────────

def monthly_chunks(months=MONTHS_BACK):
    """Return list of (start, end) pairs, one per month, newest first."""
    chunks = []
    end = date.today()
    for _ in range(months):
        start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
        # For the current (partial) month use today as end
        chunks.append((start.isoformat(), end.isoformat()))
        end = start - timedelta(days=1)
    return chunks  # newest first

# ── API fetch ────────────────────────────────────────────────────

def fetch_page(server, start, end, cursor, retries=3):
    url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            msgs = data.get("messages", [])
            if msgs and attempt == 0:
                m = msgs[0]
                print(f"      status={m.get('status')} count={m.get('count')} total={m.get('total')}")
            return data.get("collection") or []
        except Exception as e:
            print(f"      attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return []

def fetch_chunk(server, start, end, filter_fn, seen):
    """Fetch one date chunk, paginating until exhausted. Returns new records."""
    results = []
    cursor  = 0
    page    = 0

    while True:
        page += 1
        print(f"    [{start}→{end}] page {page} cursor={cursor} ...", end=" ", flush=True)
        collection = fetch_page(server, start, end, cursor)

        if not collection:
            print("empty → done")
            break

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

        print(f"{len(collection)} records, {matched} new matches")

        if len(collection) < 100:
            break   # last page of this chunk
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

# ── Main fetch per server ────────────────────────────────────────

def fetch_server(server, months, filter_fn, label):
    chunks  = monthly_chunks(months)
    seen    = set()
    results = []

    print(f"\n── {label} ({months} months, {len(chunks)} chunks) ──")

    for i, (start, end) in enumerate(chunks):
        new = fetch_chunk(server, start, end, filter_fn, seen)
        results.extend(new)
        print(f"  Chunk {i+1}/{len(chunks)}: +{len(new)} → {len(results)} total")
        time.sleep(PAGE_DELAY)

    print(f"  ✓ {label}: {len(results)} preprints")
    return results

# ── Entry point ──────────────────────────────────────────────────

def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else MONTHS_BACK
    print(f"Fetching last {months} months in monthly chunks …")

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
