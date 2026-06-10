#!/usr/bin/env python3
"""
Update existing index.json files to add underlyingPrice from detail JSON files.
No network requests needed - reads local files only.
"""

import json
import os
import sys

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")


def main():
    if not os.path.isdir(ARCHIVE_DIR):
        print("No archive directory found")
        return

    updated_count = 0

    for date_dir in sorted(os.listdir(ARCHIVE_DIR)):
        date_path = os.path.join(ARCHIVE_DIR, date_dir)
        if not os.path.isdir(date_path):
            continue

        index_path = os.path.join(date_path, "index.json")
        detail_dir = os.path.join(date_path, "detail")

        if not os.path.exists(index_path):
            continue

        # Read index.json
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)

        # Build price lookup from detail files
        price_map = {}
        if os.path.isdir(detail_dir):
            for fname in os.listdir(detail_dir):
                if not fname.endswith(".json"):
                    continue
                code = fname[:-5]  # Remove .json
                detail_path = os.path.join(detail_dir, fname)
                try:
                    with open(detail_path, "r", encoding="utf-8") as f:
                        detail = json.load(f)
                    price = detail.get("header", {}).get("underlyingPrice", 0)
                    if price > 0:
                        price_map[code] = price
                except:
                    pass

        if not price_map:
            print(f"  {date_dir}: no detail prices found, skipping")
            continue

        # Update summaries
        changed = 0
        for item in index_data.get("data", []):
            code = item.get("code", "")
            if code in price_map:
                old_price = item.get("underlyingPrice", 0)
                item["underlyingPrice"] = price_map[code]
                if old_price == 0:
                    changed += 1

        # Re-save
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, separators=(',', ':'))

        print(f"  {date_dir}: added {changed} prices ({len(price_map)} total)")
        updated_count += 1

    print(f"\nUpdated {updated_count} index.json files")


if __name__ == "__main__":
    main()
