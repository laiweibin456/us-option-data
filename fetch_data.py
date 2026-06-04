#!/usr/bin/env python3
"""
Fetch US option data from riskalertsys.com and save as JSON.

Outputs:
  docs/index.json    - Main summary list (all 172 codes)
  docs/dates.json    - Available date list
  docs/detail/XX.json - Detail page for each code (e.g., docs/detail/SOXS.json)
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
DETAIL_DIR = os.path.join(DOCS_DIR, "detail")

# Ensure output directories exist
os.makedirs(DETAIL_DIR, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})


def fetch_main():
    """Fetch main page and parse summary list + date list."""
    print(f"[{datetime.now():%H:%M:%S}] Fetching main page...")
    resp = SESSION.get(BASE_URL + "index.php", timeout=120)
    resp.raise_for_status()
    html = resp.text

    # Parse summary rows
    row_pattern = re.compile(
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>\s*'
        r'<td[^>]*>(?:<nobr>)?(.*?)(?:</nobr>)?</td>.*?</tr>',
        re.DOTALL
    )

    summaries = []
    for m in row_pattern.finditer(html):
        cols = [re.sub(r'<[^>]+>', '', c).strip() for c in m.groups()]
        if len(cols) >= 14:
            code_match = re.search(r'ucode=([^"\']+)', cols[0])
            code = code_match.group(1) if code_match else cols[0]
            name_match = re.search(r'>([^<]+)<', cols[1])
            name = name_match.group(1) if name_match else cols[1]

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

    # Parse date list
    date_pattern = re.compile(r'<a[^>]*href="archive/([^"]+)"[^>]*>([^<]+)</a>')
    dates = []
    for m in date_pattern.finditer(html):
        date_file = m.group(1)
        date_label = m.group(2).strip()
        dates.append({"label": date_label, "file": date_file})

    print(f"[{datetime.now():%H:%M:%S}] Parsed {len(summaries)} summaries, {len(dates)} dates")
    return summaries, dates


def fetch_detail(code):
    """Fetch detail page for a single code and return parsed data."""
    try:
        url = BASE_URL + f"index.php?ucode={code}"
        resp = SESSION.get(url, timeout=120)
        resp.raise_for_status()
        html = resp.text

        # Parse header info
        header = {}
        header_match = re.search(
            r'CallVol:([\d.]+)\s*PutVol:([\d.]+)\s*CPRatio:([\d.]+)\s*'
            r'CallOI:([\d.]+)\s*PutOI:([\d.]+)\s*CPutOIRatio:([\d.]+)\s*'
            r'USD\s*([\d.]+)',
            html
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

        # Parse contract rows
        detail_pattern = re.compile(
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>\s*'
            r'<td[^>]*>(?:<nobr>)?([^<]*)(?:</nobr>)?</td>.*?</tr>',
            re.DOTALL
        )

        contracts = []
        for m in detail_pattern.finditer(html):
            cols = [c.strip() for c in m.groups()]

            def pf(s):
                try: return float(s.replace(',', '').replace('%', ''))
                except: return 0.0

            def pi(s):
                try: return int(float(s.replace(',', '')))
                except: return 0

            contracts.append({
                "callOption": cols[0],
                "callPrice": pf(cols[1]),
                "callDelta": pf(cols[2]),
                "callVolume": pi(cols[3]),
                "callIv": pf(cols[4]),
                "callOi": pi(cols[5]),
                "putOption": cols[6],
                "putPrice": pf(cols[7]),
                "putDelta": pf(cols[8]),
                "putVolume": pi(cols[9]),
                "putIv": pf(cols[10]),
                "putOi": pi(cols[11]),
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

    # Save main list
    index_data = {
        "updated": datetime.utcnow().isoformat() + "Z",
        "count": len(summaries),
        "data": summaries
    }
    with open(os.path.join(DOCS_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, separators=(',', ':'))
    print(f"Saved index.json ({len(summaries)} codes)")

    # Save date list
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
                filepath = os.path.join(DETAIL_DIR, f"{code}.json")
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, separators=(',', ':'))
                success += 1
                if success % 10 == 0:
                    elapsed = time.time() - start_time
                    print(f"  Progress: {success + fail}/{len(codes)} ({elapsed:.0f}s)")
            else:
                fail += 1

    elapsed = time.time() - start_time
    print(f"\nDone! {success} success, {fail} failed, {elapsed:.0f}s total")


if __name__ == "__main__":
    main()
