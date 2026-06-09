#!/usr/bin/env python3
"""
Fetch US option data from riskalertsys.com and save as JSON.

Outputs:
  docs/dates.json                              - Available date list
  docs/archive/YYYY-MM-DD/index.json           - Historical summary for each day
  docs/archive/YYYY-MM-DD/detail/XX.json       - Historical detail for each day
"""

import json
import os
import re
import sys
import time
import concurrent.futures
from datetime import datetime

import requests

BASE_URL = "https://riskalertsys.com/~o~options/US/"
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")

# Will be set from website's "last updated" timestamp
TODAY = None
ARCHIVE_DIR = None
ARCHIVE_DETAIL_DIR = None

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})


def detect_date_from_html(html):
    """Extract the data date from website's 'last updated' timestamp."""
    m = re.search(r'last updated:\s*(\d{4})-(\d{2})-(\d{2})\s+\d{2}:\d{2}:\d{2}', html)
    if m:
        date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        print(f"  Detected data date from website: {date_str}")
        return date_str
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    print(f"  Could not detect date from website, using UTC: {date_str}")
    return date_str


def strip_html(text):
    """Remove all HTML tags and decode entities."""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ').strip()
    return text


def extract_td_texts(tr_html):
    """Extract plain text from all <td> cells in a <tr> row."""
    tds = re.findall(r'<td[^>]*>(.*?)</td>', tr_html, re.DOTALL)
    return [strip_html(td) for td in tds]


def fetch_main():
    """Fetch main page and parse summary list + date list."""
    print(f"[{datetime.now():%H:%M:%S}] Fetching main page...")
    resp = SESSION.get(BASE_URL + "index.php", timeout=120)
    resp.raise_for_status()
    html = resp.text

    # Detect data date from website
    global TODAY, ARCHIVE_DIR, ARCHIVE_DETAIL_DIR
    TODAY = detect_date_from_html(html)
    ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive", TODAY)
    ARCHIVE_DETAIL_DIR = os.path.join(ARCHIVE_DIR, "detail")
    os.makedirs(ARCHIVE_DETAIL_DIR, exist_ok=True)

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

    # Build date list from archive directories
    dates = build_dates_list()

    print(f"[{datetime.now():%H:%M:%S}] Parsed {len(summaries)} summaries, {len(dates)} archive dates")
    return summaries, dates


def build_dates_list():
    """Build date list from archive directories."""
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
    return dates


def fetch_detail(code):
    """Fetch detail page for a single code and return parsed data."""
    try:
        url = BASE_URL + f"index.php?ucode={code}"
        resp = SESSION.get(url, timeout=120)
        resp.raise_for_status()
        html = resp.text

        # Parse header info
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

        # Parse contract rows
        row_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)

        contracts = []
        for m in row_pattern.finditer(html):
            row = m.group(1)
            cols = extract_td_texts(row)
            if len(cols) < 13:
                continue

            call_opt = cols[0].strip()
            if not call_opt or call_opt == 'option':
                continue
            if '_Call' not in call_opt:
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
        print(f"  FAILED {code}: {e}")
        return code, None


def main():
    start_time = time.time()

    # 1. Fetch main page
    summaries, dates = fetch_main()

    if not summaries:
        print("ERROR: No summaries found, aborting")
        sys.exit(1)

    # Build index data
    index_data = {
        "updated": datetime.utcnow().isoformat() + "Z",
        "date": TODAY,
        "count": len(summaries),
        "data": summaries
    }

    # Save to docs/archive/YYYY-MM-DD/ only
    with open(os.path.join(ARCHIVE_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, separators=(',', ':'))
    print(f"Saved archive/{TODAY}/index.json ({len(summaries)} codes)")

    # Save date list
    dates = build_dates_list()
    with open(os.path.join(DOCS_DIR, "dates.json"), "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, separators=(',', ':'))
    print(f"Saved dates.json ({len(dates)} dates)")

    # 2. Fetch all detail pages concurrently
    codes = [s["code"] for s in summaries]
    success = 0
    fail = 0

    print(f"\nFetching {len(codes)} detail pages with 5 threads...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_detail, code): code for code in codes}
        for future in concurrent.futures.as_completed(futures):
            code, result = future.result()
            if result is not None:
                # Save to docs/archive/YYYY-MM-DD/detail/ only
                archive_filepath = os.path.join(ARCHIVE_DETAIL_DIR, f"{code}.json")
                with open(archive_filepath, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, separators=(',', ':'))

                success += 1
                if success % 10 == 0:
                    elapsed = time.time() - start_time
                    print(f"  Progress: {success + fail}/{len(codes)} ({elapsed:.0f}s)")
            else:
                fail += 1

    elapsed = time.time() - start_time
    print(f"\nDone! {success} success, {fail} failed, {elapsed:.0f}s total")
    print(f"Archive data: docs/archive/{TODAY}/")


if __name__ == "__main__":
    main()
