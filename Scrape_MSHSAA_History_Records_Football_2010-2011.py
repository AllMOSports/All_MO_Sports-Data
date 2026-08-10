"""
scrape_mshsaa_history_records_football_2010_2011.py
 
Scoped-down variant of scrape_mshsaa_history_records.py: FOOTBALL ONLY,
2010 and 2011 seasons only. Same parsing logic, same output shape, same
output folder layout (output/mshsaa_historical_records/football/football_{year}.json)
as the 2012-2025 backfill, so the merged output plugs straight into
Build_Football_History_json.py without any changes there.
 
WHY A SEPARATE SCRIPT INSTEAD OF JUST RUNNING THE ORIGINAL WITH
--start-year 2010 --end-year 2011 --sports football:
That absolutely works too and is the simpler option if you trust the
schema this far back. This standalone version exists so the extra risk
flagged below is impossible to miss, and so a bad run against unfamiliar
markup can't be mistaken for a routine re-run of the main backfill script.
 
*** READ BEFORE RUNNING ***
The original script's own checklist calls 2012 "the riskiest part" of
its default 2012-2025 range, specifically because older MSHSAA pages
might not share the confirmed-2025-26 table markup (different cell
counts, no data-classification/data-district attributes, missing
mshsaa_points column, etc). 2010 and 2011 are two years further back
than that already-flagged risk zone. This script reuses the exact same
parsing logic unchanged, so if MSHSAA's 2010/2011 pages have a
different structure, you'll get a loud RuntimeError (or a suspicious
0-team result) rather than silently-wrong data -- but there's a real
chance this needs manual schema adjustments after you see what actually
comes back. Run it, check the manifest, and if either year comes back
empty or clearly wrong, pull the live page HTML for that year/alg combo
(View Page Source on
https://www.mshsaa.org/Activities/SeasonRecords.aspx?alg=19&year=2010 )
and compare cell counts against SCHEMA_A_CELL_INDEX below before trusting
the output.
 
Usage:
  python3 scrape_mshsaa_history_records_football_2010_2011.py
  python3 scrape_mshsaa_history_records_football_2010_2011.py --force   # re-scrape even if files exist
"""
 
import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
 
import requests
from bs4 import BeautifulSoup
 
# ---------------------------------------------------------------------------
# CONFIG (scoped: football only, 2010-2011 only)
# ---------------------------------------------------------------------------
 
BASE_URL = "https://www.mshsaa.org/Activities/SeasonRecords.aspx"
 
SPORT_KEY = "football"
SPORT_ALG = 19  # from SPORT_ALG_MAP in the original script
 
YEARS = [2010, 2011]
 
OUTPUT_DIR_DEFAULT = "output/mshsaa_historical_records"
 
REQUEST_DELAY_SECONDS = 1.5
 
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
 
MAX_FETCH_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 5
 
# ---------------------------------------------------------------------------
# COLUMN SCHEMA (football = Schema A, same as the original script)
# ---------------------------------------------------------------------------
 
ROW_SELECTOR_CLASS = "fs_tablecolumn"
 
SCHEMA_A_CELL_INDEX = {
    "classification_label": 1,
    "district": 2,
    "points_for": 3,
    "points_against": 4,
    "ppg": 5,
    "oppg": 6,
    "mov": 7,
    "wins": 8,
    "losses": 9,
    "win_pct": 10,
    "mshsaa_points": 11,  # only present when cell count is 12
}
SCHEMA_A_VALID_CELL_COUNTS = {11, 12}
 
 
# ---------------------------------------------------------------------------
# PARSING (unchanged from scrape_mshsaa_history_records.py)
# ---------------------------------------------------------------------------
 
def parse_number(raw):
    """Coerce a stat cell to int or float. Returns None for blank/dash cells."""
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if text == "" or text == "-" or text == "--":
        return None
    try:
        if "." in text or "%" in text:
            return float(text.replace("%", ""))
        return int(text)
    except ValueError:
        return None
 
 
def _extract_school_id(name_cell):
    link = name_cell.find("a", href=True)
    if not link:
        return None
    match = re.search(r"[?&]s=(\d+)", link["href"])
    return int(match.group(1)) if match else None
 
 
def _common_fields(row, cells, cell_index):
    school_name = cells[0].get_text(strip=True)
    if not school_name:
        return None
 
    raw_classification_attr = row.get("data-classification")
    raw_district_attr = row.get("data-district")
    classification_label = cells[cell_index["classification_label"]].get_text(strip=True)
 
    return {
        "school": school_name,
        "mshsaa_school_id": _extract_school_id(cells[0]),
        "classification_code": parse_number(raw_classification_attr),
        "classification_label": classification_label or None,
        "district": parse_number(raw_district_attr) or parse_number(
            cells[cell_index["district"]].get_text(strip=True)
        ),
    }
 
 
def _parse_schema_a_row(row, cells):
    if len(cells) not in SCHEMA_A_VALID_CELL_COUNTS:
        return None
 
    base = _common_fields(row, cells, SCHEMA_A_CELL_INDEX)
    if base is None:
        return None
 
    ci = SCHEMA_A_CELL_INDEX
    has_points_column = len(cells) >= 12
 
    team = {
        **base,
        "points_for": parse_number(cells[ci["points_for"]].get_text(strip=True)),
        "points_against": parse_number(cells[ci["points_against"]].get_text(strip=True)),
        "ppg": parse_number(cells[ci["ppg"]].get_text(strip=True)),
        "oppg": parse_number(cells[ci["oppg"]].get_text(strip=True)),
        "mov": parse_number(cells[ci["mov"]].get_text(strip=True)),
        "wins": parse_number(cells[ci["wins"]].get_text(strip=True)),
        "losses": parse_number(cells[ci["losses"]].get_text(strip=True)),
        "win_pct": parse_number(cells[ci["win_pct"]].get_text(strip=True)),
        "mshsaa_points": (
            parse_number(cells[ci["mshsaa_points"]].get_text(strip=True))
            if has_points_column else None
        ),
        "games_played": None,
    }
    if team["wins"] is not None and team["losses"] is not None:
        team["games_played"] = team["wins"] + team["losses"]
    return team
 
 
def parse_season_records_html(html):
    soup = BeautifulSoup(html, "html.parser")
 
    rows = soup.find_all("tr", class_=ROW_SELECTOR_CLASS)
    if not rows:
        raise RuntimeError(
            f"No <tr class=\"{ROW_SELECTOR_CLASS}\"> rows found on the page. "
            f"Either this season has no data for football, MSHSAA's markup "
            f"differs for this year (likely, this far back), or the alg/year "
            f"combo is wrong. Pull the live HTML for this URL and compare "
            f"against SCHEMA_A_CELL_INDEX before assuming this is a dead end."
        )
 
    teams = []
    for row in rows:
        cells = row.find_all("td")
        team = _parse_schema_a_row(row, cells)
        if team is not None:
            teams.append(team)
 
    return teams
 
 
# ---------------------------------------------------------------------------
# FETCHING
# ---------------------------------------------------------------------------
 
def fetch_page(sport_alg, year):
    url = f"{BASE_URL}?alg={sport_alg}&year={year}"
    last_error = None
 
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text, url
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < MAX_FETCH_ATTEMPTS:
                print(
                    f"    [retry {attempt}/{MAX_FETCH_ATTEMPTS - 1}] "
                    f"fetch failed ({e}), waiting {RETRY_BACKOFF_SECONDS}s..."
                )
                time.sleep(RETRY_BACKOFF_SECONDS)
 
    raise RuntimeError(f"Failed to fetch {url} after {MAX_FETCH_ATTEMPTS} attempts: {last_error}")
 
 
def season_label(year):
    """2010 -> '2010-2011'"""
    return f"{year}-{year + 1}"
 
 
# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
 
def scrape_football_year(year, output_dir):
    out_path = Path(output_dir) / SPORT_KEY / f"{SPORT_KEY}_{year}.json"
 
    html, url = fetch_page(SPORT_ALG, year)
    teams = parse_season_records_html(html)
 
    output = {
        "sport": SPORT_KEY,
        "year": year,
        "season_label": season_label(year),
        "source_url": url,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams,
    }
 
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
 
    return len(teams), str(out_path)
 
 
def load_manifest(manifest_path):
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
 
 
def save_manifest(manifest_path, manifest):
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
 
 
def main():
    parser = argparse.ArgumentParser(
        description="Backfill MSHSAA football season records for 2010 and 2011 only."
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY_SECONDS,
        help="Base seconds to sleep between requests.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=OUTPUT_DIR_DEFAULT,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-scrape and overwrite years that already have an output file.",
    )
    args = parser.parse_args()
 
    output_dir = Path(args.output_dir)
    # Shares the SAME manifest.json as the main 2012-2025 backfill script,
    # so this run's results show up alongside everything else rather than
    # in a separate, easy-to-forget-about file.
    manifest_path = output_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
 
    print(f"Backfilling football for {len(YEARS)} season(s): {YEARS}\n")
 
    completed = 0
    skipped = 0
    failed = 0
 
    for year in YEARS:
        manifest_key = f"{SPORT_KEY}_{year}"
        out_path = output_dir / SPORT_KEY / f"{SPORT_KEY}_{year}.json"
 
        if out_path.exists() and not args.force:
            print(f"[skip] football {season_label(year)} -- already scraped")
            skipped += 1
            continue
 
        print(f"[fetch] football {season_label(year)} (alg={SPORT_ALG}, year={year})...")
        try:
            team_count, written_path = scrape_football_year(year, output_dir)
            status = "ok" if team_count > 0 else "empty"
            manifest[manifest_key] = {
                "sport": SPORT_KEY,
                "year": year,
                "season_label": season_label(year),
                "status": status,
                "team_count": team_count,
                "output_path": written_path,
                "error": None,
            }
            print(f"    -> {team_count} teams parsed")
            if team_count == 0:
                print(
                    "    [WARNING] 0 teams -- page may have no data for "
                    "this year, or markup differs from the confirmed schema. "
                    "Check manually before trusting this file."
                )
            completed += 1
        except Exception as e:
            manifest[manifest_key] = {
                "sport": SPORT_KEY,
                "year": year,
                "season_label": season_label(year),
                "status": "failed",
                "team_count": None,
                "output_path": None,
                "error": str(e),
            }
            print(f"    [ERROR] {e}")
            failed += 1
 
        save_manifest(manifest_path, manifest)
        time.sleep(args.delay + random.uniform(0, 0.5))
 
    print(
        f"\nDone. {completed} scraped, {skipped} skipped (already had a file), "
        f"{failed} failed. See {manifest_path} for the full breakdown."
    )
    if failed > 0:
        sys.exit(1)
 
 
if __name__ == "__main__":
    main()
 
 
# ---------------------------------------------------------------------------
# CHECKLIST
# ---------------------------------------------------------------------------
#
# 1. Run it:
#      python3 scrape_mshsaa_history_records_football_2010_2011.py
#    Only 2 requests, ~3-4 seconds apart. Should finish in a few seconds.
#
# 2. Check manifest.json for football_2010 and football_2011:
#      - status should be "ok", not "empty" or "failed"
#      - team_count should be in the same ballpark as football_2012's
#        (your existing 2012 file has 359 teams -- 2010/2011 should be
#        roughly similar, not wildly different)
#
# 3. Spot-check one team you know by eye against the actual site:
#      https://www.mshsaa.org/Activities/SeasonRecords.aspx?alg=19&year=2010
#    Compare wins/losses/points for a team you recognize against what
#    landed in output/mshsaa_historical_records/football/football_2010.json
#
# 4. If either year comes back "empty" or throws the RuntimeError about
#    missing <tr class="fs_tablecolumn"> rows, that's the older-markup
#    risk flagged above -- pull the live page's HTML (View Page Source)
#    for that specific URL and compare the actual cell count/structure
#    against SCHEMA_A_CELL_INDEX in this script. It may need a second
#    schema variant (SCHEMA_A_OLD or similar) if MSHSAA's table had a
#    different column layout that far back.
#
# 5. Once both years look right, football_2010.json and football_2011.json
#    sit in the same output/mshsaa_historical_records/football/ folder as
#    2012-2025, so Build_Football_History_json.py's existing records-fetch
#    logic picks them up automatically -- no changes needed there.
 
