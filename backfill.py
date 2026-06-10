#!/usr/bin/env python3
"""
Backfill historical data from riskalertsys.com archive pages.

Fetches summary + detail data for each available date and saves to:
  docs/archive/YYYY-MM-DD/index.json
  docs/archive/YYYY-MM-DD/detail/XX.json

Usage:
  python backfill.py          # Backfill all available dates
  python backfill.py --days 15  # Only last 15 days
  python backfill.py --date 2026-06-02  # Only specific date
"""

import json
import os
import re
import sys
import time
import argparse
import concurrent.futures
from datetime import datetime, timedelta

import requests

BASE_URL = "https://riskalertsys.com/~o~options/US/"
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})


def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ').strip()
    return text


def extract_td_texts(tr_html):
    tds = re.findall(r'<td[^>]*>(.*?)</td>', tr_html, re.DOTALL)
    return [strip_html(td) for td in tds]


def get_available_dates():
    """Get list of available archive dates from main page."""
    print(f"[{datetime.now():%H:%M:%S}] Fetching available dates from main page...")
    resp = SESSION.get(BASE_URL + "index.php", timeout=120)
    resp.raise_for_status()
    html = resp.text

    dates = []
    date_pattern = re.compile(r"""href=['"]archive/(\d{4}-\d{2}-\d{2})\.html['"]""", re.IGNORECASE)
    for m in date_pattern.finditer(html):
        date_str = m.group(1)
        dates.append(date_str)

    print(f"  Found {len(dates)} archive dates: {dates}")
    return dates


def fetch_archive_summary(date_str):
    """Fetch archive page for a date and parse summary data.
    If the date is today (not in archive), fetch from main page instead."""
    try:
        # Try archive page first
        url = BASE_URL + f"archive/{date_str}.html"
        print(f"[{datetime.now():%H:%M:%S}] Fetching archive {date_str}...")
        resp = SESSION.get(url, timeout=120)

        if resp.status_code == 404:
            # Date not in archive, try main page (today's data)
            print(f"  {date_str} not in archive, trying main page...")
            url = BASE_URL + "index.php"
            resp = SESSION.get(url, timeout=120)

        resp.raise_for_status()
        html = resp.text

        row_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
        summaries = []

        for m in row_pattern.finditer(html):
            row = m.group(1)
            cols = extract_td_texts(row)
            if len(cols) >= 14:
                code = cols[0].strip()
                name = cols[1].strip()
                if code in ('', 'option', 'Code') or name in ('', 'name'):
                    continue

                def pf(s):
                    try: return float(s.replace(',', '').replace('%', ''))
                    except: return 0.0

                summaries.append({
                    "code": code,
                    "name": name,
                    "iv1d": pf(cols[2]),
                    "iv7d": pf(cols[3]),
                    "iv30d": pf(cols[4]),
                    "cDVol": pf(cols[5]),
                    "cDOI": pf(cols[6]),
                    "spCD": pf(cols[7]),
                    "pDVol": pf(cols[8]),
                    "pDOI": pf(cols[9]),
                    "spPD": pf(cols[10]),
                    "netDV": pf(cols[11]),
                    "netDOI": pf(cols[12]),
                    "netSpD": pf(cols[13]),
                })

        print(f"  {date_str}: {len(summaries)} codes")
        return date_str, summaries
    except Exception as e:
        print(f"  FAILED {date_str}: {e}")
        return date_str, None


def fetch_detail(code, date_str=None):
    """Fetch detail page for a single code.
    If date_str is provided, fetches historical data for that date."""
    try:
        if date_str:
            url = BASE_URL + f"index.php?ucode={code}&date={date_str}"
        else:
            url = BASE_URL + f"index.php?ucode={code}"
        resp = SESSION.get(url, timeout=120)
        resp.raise_for_status()
        html = resp.text

        header = {}
        callvol_idx = html.find('CallVol:')
        if callvol_idx >= 0:
            header_chunk = html[callvol_idx:callvol_idx + 500]
            header_chunk = re.sub(r'<[^>]+>', ' ', header_chunk)
            header_chunk = header_chunk.replace('&nbsp;', ' ')
            header_chunk = re.sub(r'\s+', ' ', header_chunk)

            header_match = re.search(
                r'CallVol:\s*([\d.]+)\s*PutVol:\s*([\d.]+)\s*CPRatio:\s*([\d.]+)\s*'
                r'CallOI:\s*([\d.]+)\s*PutOI:\s*([\d.]+)\s*CPutOIRatio:\s*([\d.]+)\s*'
                r'USD\s*([\d.]+)',
                header_chunk
            )
            if header_match:
                header = {
                    "callVolume": float(header_match.group(1)),
                    "putVolume": float(header_match.group(2)),
                    "cpRatio": float(header_match.group(3)),
                    "callOi": float(header_match.group(4)),
                    "putOi": float(header_match.group(5)),
                    "cputOiRatio": float(header_match.group(6)),
                    "underlyingPrice": float(header_match.group(7)),
                }
            else:
                header_match2 = re.search(
                    r'CallVol:\s*([\d.]+)\s*PutVol:\s*([\d.]+)\s*CPRatio:\s*([\d.]+)\s*'
                    r'CallOI:\s*([\d.]+)\s*PutOI:\s*([\d.]+)\s*CPutOIRatio:\s*([\d.]+)',
                    header_chunk
                )
                if header_match2:
                    header = {
                        "callVolume": float(header_match2.group(1)),
                        "putVolume": float(header_match2.group(2)),
                        "cpRatio": float(header_match2.group(3)),
                        "callOi": float(header_match2.group(4)),
                        "putOi": float(header_match2.group(5)),
                        "cputOiRatio": float(header_match2.group(6)),
                        "underlyingPrice": 0,
                    }

        row_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
        contracts = []
        for m in row_pattern.finditer(html):
            row = m.group(1)
            cols = extract_td_texts(row)
            if len(cols) < 13:
                continue
            call_opt = cols[0].strip()
            if not call_opt or call_opt == 'option' or '_Call' not in call_opt:
                continue

            def pf(s):
                try: return float(s.replace(',', '').replace('%', ''))
                except: return 0.0

            def pi(s):
                try: return int(float(s.replace(',', '')))
                except: return 0

            put_opt = cols[7].strip() if len(cols) > 7 else ""
            contracts.append({
                "callOption": call_opt,
                "callPrice": pf(cols[1]),
                "callDelta": pf(cols[2]),
                "callVolume": pi(cols[3]),
                "callIv": pf(cols[4]),
                "callOi": pi(cols[5]),
                "putOption": put_opt,
                "putPrice": pf(cols[8]) if len(cols) > 8 else 0,
                "putDelta": pf(cols[9]) if len(cols) > 9 else 0,
                "putVolume": pi(cols[10]) if len(cols) > 10 else 0,
                "putIv": pf(cols[11]) if len(cols) > 11 else 0,
                "putOi": pi(cols[12]) if len(cols) > 12 else 0,
            })

        return code, {"header": header, "contracts": contracts}
    except Exception as e:
        print(f"  FAILED detail {code}: {e}")
        return code, None


def main():
    parser = argparse.ArgumentParser(description="Backfill historical option data")
    parser.add_argument("--days", type=int, default=0, help="Only backfill last N days")
    parser.add_argument("--date", type=str, help="Only backfill specific date (YYYY-MM-DD)")
    parser.add_argument("--summary-only", action="store_true", help="Only fetch summary, skip detail pages")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing data")
    args = parser.parse_args()

    start_time = time.time()

    # Get available dates
    all_dates = get_available_dates()

    # Filter dates
    if args.date:
        # Allow specific date even if not in archive list (e.g., today)
        target_dates = [args.date]
    elif args.days > 0:
        cutoff = (datetime.utcnow() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        target_dates = [d for d in all_dates if d >= cutoff]
    else:
        target_dates = all_dates

    # Skip dates that already have COMPLETE data (summary + detail), unless --force
    dates_to_fetch = []
    for d in target_dates:
        if args.force:
            dates_to_fetch.append(d)
            continue
        archive_dir = os.path.join(DOCS_DIR, "archive", d)
        index_file = os.path.join(archive_dir, "index.json")
        detail_dir = os.path.join(archive_dir, "detail")
        # Check if detail directory exists and has files
        detail_complete = False
        if os.path.isdir(detail_dir):
            detail_files = [f for f in os.listdir(detail_dir) if f.endswith('.json')]
            if len(detail_files) >= 50:  # At least 50 detail files means it's populated
                detail_complete = True
        if os.path.exists(index_file) and detail_complete:
            print(f"  Skipping {d} (already complete)")
        else:
            dates_to_fetch.append(d)

    if not dates_to_fetch:
        print("All dates already have data, nothing to backfill.")
        return

    print(f"\nWill backfill {len(dates_to_fetch)} dates: {dates_to_fetch}")

    # Fetch archive summaries
    for date_str in dates_to_fetch:
        date_str, summaries = fetch_archive_summary(date_str)
        if summaries is None:
            continue

        archive_dir = os.path.join(DOCS_DIR, "archive", date_str)
        archive_detail_dir = os.path.join(archive_dir, "detail")
        os.makedirs(archive_detail_dir, exist_ok=True)

        index_data = {
            "updated": datetime.utcnow().isoformat() + "Z",
            "date": date_str,
            "count": len(summaries),
            "data": summaries
        }

        with open(os.path.join(archive_dir, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, separators=(',', ':'))
        print(f"  Saved archive/{date_str}/index.json ({len(summaries)} codes)")

        if args.summary_only:
            continue

        # Fetch detail pages for this date
        codes = [s["code"] for s in summaries]
        success = 0
        fail = 0

        print(f"  Fetching {len(codes)} detail pages for {date_str}...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_detail, code, date_str): code for code in codes}
            for future in concurrent.futures.as_completed(futures):
                code, result = future.result()
                if result is not None:
                    filepath = os.path.join(archive_detail_dir, f"{code}.json")
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, separators=(',', ':'))
                    success += 1
                    if success % 20 == 0:
                        elapsed = time.time() - start_time
                        print(f"    Progress: {success + fail}/{len(codes)} ({elapsed:.0f}s)")
                else:
                    fail += 1

        print(f"  {date_str} details: {success} success, {fail} failed")

    # Update dates.json
    archive_base = os.path.join(DOCS_DIR, "archive")
    dates = []
    if os.path.isdir(archive_base):
        for dirname in sorted(os.listdir(archive_base), reverse=True):
            dirpath = os.path.join(archive_base, dirname)
            if os.path.isdir(dirpath) and re.match(r'\d{4}-\d{2}-\d{2}', dirname):
                try:
                    dt = datetime.strptime(dirname, "%Y-%m-%d")
                    label = dt.strftime("%b %d")
                except:
                    label = dirname
                dates.append({"label": label, "date": dirname})

    with open(os.path.join(DOCS_DIR, "dates.json"), "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, separators=(',', ':'))
    print(f"\nUpdated dates.json ({len(dates)} dates)")

    elapsed = time.time() - start_time
    print(f"\nBackfill complete! {elapsed:.0f}s total")


if __name__ == "__main__":
    main()
