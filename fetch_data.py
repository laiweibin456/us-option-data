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

# Full-day US market closures (NYSE/Nasdaq). Published as docs/holidays.json so
# clients don't have to guess holidays from duplicated prices.
US_MARKET_HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-04-02", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

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


def parse_header(html):
    """Parse the "CallVol ... USD <price>" summary line into a header dict.

    Every field is matched independently: the source site emits blanks and
    "USD null" for some historical dates, and the old all-in-one regex
    returned an empty header (and therefore a lost underlyingPrice) as soon
    as a single value was missing.
    """
    idx = html.find('CallVol:')
    if idx < 0:
        return {}
    chunk = re.sub(r'<[^>]+>', ' ', html[idx:idx + 500])
    chunk = chunk.replace('&nbsp;', ' ')
    chunk = re.sub(r'\s+', ' ', chunk)

    header = {}
    for key, pattern in (
            ("callVolume", r'CallVol:\s*([\d.]+)'),
            ("putVolume", r'PutVol:\s*([\d.]+)'),
            ("cpRatio", r'CPRatio:\s*([\d.]+)'),
            ("callOi", r'CallOI:\s*([\d.]+)'),
            ("putOi", r'PutOI:\s*([\d.]+)'),
            ("cputOiRatio", r'CPutOIRatio:\s*([\d.]+)'),
    ):
        m = re.search(pattern, chunk)
        if m:
            header[key] = float(m.group(1))

    m = re.search(r'USD\s*([\d,]+(?:\.\d+)?)', chunk)
    if m:
        header["underlyingPrice"] = float(m.group(1).replace(',', ''))
    return header


def write_holidays(dates):
    """Publish the trading-holiday list for every year covered by the data."""
    years = {d["date"][:4] for d in dates}
    holidays = sorted(h for h in US_MARKET_HOLIDAYS if h[:4] in years)
    payload = {
        "updated": datetime.utcnow().isoformat() + "Z",
        "holidays": holidays,
    }
    path = os.path.join(DOCS_DIR, "holidays.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    print(f"Saved holidays.json ({len(holidays)} holidays)")


def fetch_detail(code, date_str=None):
    """Fetch detail page for a single code and return parsed data.
    If date_str is provided, fetches historical data for that date."""
    try:
        if date_str:
            url = BASE_URL + f"index.php?ucode={code}&date={date_str}"
        else:
            url = BASE_URL + f"index.php?ucode={code}"
        resp = SESSION.get(url, timeout=120)
        resp.raise_for_status()
        html = resp.text

        # Parse header info
        header = parse_header(html)

        # Historical pages may omit the USD price ("USD null"). Only the live
        # page has it, and it is valid only when we are fetching today's data.
        if header.get("underlyingPrice", 0) <= 0 and (date_str is None or date_str == TODAY):
            try:
                live = SESSION.get(BASE_URL + f"index.php?ucode={code}", timeout=120)
                live.raise_for_status()
                live_price = parse_header(live.text).get("underlyingPrice", 0)
                if live_price > 0:
                    header["underlyingPrice"] = live_price
                    print(f"  {code}: recovered USD price {live_price} from live page")
            except Exception as e:
                print(f"  {code}: live page price fallback failed: {e}")

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


def is_low_quality_data(summaries):
    """Check if summary data is low quality (most codes have zero volume/OI).

    Returns True if more than half of codes have all key volume/OI fields as 0,
    which indicates the website is showing empty/placeholder data (e.g. on weekends).
    """
    if not summaries:
        return True
    zero_count = 0
    for s in summaries:
        if (s.get('cDVol', 0) == 0 and s.get('cDOI', 0) == 0 and
                s.get('pDVol', 0) == 0 and s.get('pDOI', 0) == 0 and
                s.get('netDOI', 0) == 0):
            zero_count += 1
    return zero_count > len(summaries) // 2


def main():
    start_time = time.time()

    # 1. Fetch main page
    summaries, dates = fetch_main()

    if not summaries:
        print("ERROR: No summaries found, aborting")
        sys.exit(1)

    index_path = os.path.join(ARCHIVE_DIR, "index.json")

    # Row-count guard: refuse to replace a full day with a stub page.
    # (e.g. Labor Day 2026-09-07 produced only 1 code and wiped the archive)
    previous_count = 0
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                previous_count = int(json.load(f).get("count", 0))
        except Exception:
            previous_count = 0
    if previous_count and len(summaries) < previous_count * 0.5:
        print(f"WARNING: only {len(summaries)} codes today vs {previous_count} in existing data.")
        print(f"Refusing to overwrite {index_path}.")
        return

    # Check data quality - skip if mostly zeros and existing data present
    # This prevents weekend runs from overwriting good Friday data with zeros
    if is_low_quality_data(summaries):
        zero_count = sum(1 for s in summaries
                         if s.get('cDVol', 0) == 0 and s.get('cDOI', 0) == 0 and
                         s.get('pDVol', 0) == 0 and s.get('pDOI', 0) == 0 and
                         s.get('netDOI', 0) == 0)
        if os.path.exists(index_path):
            print(f"WARNING: Low quality data ({zero_count}/{len(summaries)} codes have zero volume/OI).")
            print(f"Existing data preserved at {index_path}. Skipping save.")
            return
        else:
            print(f"WARNING: Low quality data ({zero_count}/{len(summaries)} codes have zero volume/OI).")
            print("No existing data, saving anyway.")

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
    write_holidays(dates)

    # 2. Fetch all detail pages concurrently
    codes = [s["code"] for s in summaries]
    success = 0
    fail = 0

    # Build a lookup from code to summary dict
    summary_map = {s["code"]: s for s in summaries}

    print(f"\nFetching {len(codes)} detail pages with 5 threads...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_detail, code, TODAY): code for code in codes}
        for future in concurrent.futures.as_completed(futures):
            code, result = future.result()
            if result is not None:
                # Save to docs/archive/YYYY-MM-DD/detail/ only
                archive_filepath = os.path.join(ARCHIVE_DETAIL_DIR, f"{code.lower()}.json")
                with open(archive_filepath, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, separators=(',', ':'))

                # Add underlyingPrice to summary
                price = result.get("header", {}).get("underlyingPrice", 0)
                if price > 0 and code in summary_map:
                    summary_map[code]["underlyingPrice"] = price

                success += 1
                if success % 10 == 0:
                    elapsed = time.time() - start_time
                    print(f"  Progress: {success + fail}/{len(codes)} ({elapsed:.0f}s)")
            else:
                fail += 1

    # Re-save index.json with underlyingPrice added
    index_data["data"] = summaries
    with open(os.path.join(ARCHIVE_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, separators=(',', ':'))
    print(f"Updated archive/{TODAY}/index.json with underlyingPrice")

    elapsed = time.time() - start_time
    print(f"\nDone! {success} success, {fail} failed, {elapsed:.0f}s total")
    print(f"Archive data: docs/archive/{TODAY}/")


if __name__ == "__main__":
    main()
