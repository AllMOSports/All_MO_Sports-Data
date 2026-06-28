"""
scrape_mshsaa_history_records.py
 
Scrapes MSHSAA's "Season Records" page for ALL NINE sports across
MULTIPLE SEASONS (2012-2013 through the most recently completed season),
writing one JSON file per sport-per-season.
 
This is a SEPARATE, STANDALONE script from
scrape_mshsaa_season_records.py (the daily/nightly current-season
scraper). It does not import from or modify that script, and it writes
to its own output folder so it can never clobber the current-season
data. The two scripts duplicate some constants (SPORT_ALG_MAP, the
column schemas) on purpose -- this script is meant to be run
occasionally/manually for a backfill, not wired into the same nightly
Action as the current-season scraper, so keeping it decoupled seemed
safer than sharing code that, if edited for one purpose, could silently
break the other.
 
URL PATTERN (confirmed by user 2026-06-28):
  https://www.mshsaa.org/Activities/SeasonRecords.aspx?alg={SPORT_ALG}&year={YEAR}
  Example: boys basketball, 2017-2018 season:
    https://www.mshsaa.org/Activities/SeasonRecords.aspx?alg=5&year=2017
 
WHAT "year" MEANS (assumption -- see checklist before trusting this):
  year=2017 is treated as meaning the "2017-2018" school year, for
  EVERY sport, fall or spring. This is confirmed for boys basketball
  (a winter sport) via the reference URL above. It has NOT been
  independently confirmed for fall sports (football, fall softball) or
  spring sports (baseball, boys/girls soccer, spring softball, and
  girls volleyball is fall) -- it's a reasonable inference since
  MSHSAA almost certainly keys everything to one school-year value
  rather than maintaining sport-specific year semantics, but "reasonable
  inference" is not the same as "confirmed." Run the small test batch
  in the checklist below before kicking off the full 9-sport x 14-year
  run.
 
WHAT THIS DOES NOT DO:
  Same scope limits as the current-season scraper: no game-by-game
  schedule data, no ratings/OVR/SOS. Pure official season-totals
  backfill, sport by sport, year by year.
 
IMPORTANT - UNVERIFIED AGAINST LIVE HISTORICAL HTML:
  The parsing logic (schemas, cell counts, row selector) is copied
  from the current-season scraper, which IS confirmed against live
  2025-26 HTML. Whether MSHSAA used the exact same table markup back
  in, say, 2012-2013 is unknown -- older seasons could have fewer
  columns, no data-classification/data-district attributes, or a
  different schema entirely. This script is built to fail loudly per
  sport/year (logged in manifest.json) rather than silently producing
  wrong data, but you should still spot-check before trusting it.
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
# CONFIG
# ---------------------------------------------------------------------------
 
BASE_URL = "https://www.mshsaa.org/Activities/SeasonRecords.aspx"
 
# Duplicated from scrape_mshsaa_season_records.py on purpose -- see module
# docstring. Keep in sync manually if alg values ever change.
SPORT_ALG_MAP = {
    "football": 19,
    "baseball": 3,
    "boys_basketball": 5,
    "girls_basketball": 6,
    "boys_soccer": 33,
    "girls_soccer": 34,
    "girls_volleyball": 57,
    "fall_softball": 38,
    "spring_softball": 68,
}
 
# First season you want data for, and the most recently COMPLETED season
# as of when this script is run. 2025 means the "2025-2026" school year
# (which wrapped up this spring -- today is 2026-06-28). Bump END_YEAR up
# by 1 each summer once that year's spring sports have finished.
START_YEAR_DEFAULT = 2012
END_YEAR_DEFAULT = 2025
 
OUTPUT_DIR_DEFAULT = "output/mshsaa_historical_records"
 
# Seconds to sleep between requests (plus a little random jitter) so this
# doesn't hammer MSHSAA's server with ~126 rapid-fire requests in a row.
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
# COLUMN SCHEMAS (duplicated from scrape_mshsaa_season_records.py)
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
 
SCHEMA_B_CELL_INDEX = {
    "classification_label": 1,
    "district": 2,
    "mov": 3,
    "wins": 4,
    "losses": 5,
    "win_pct": 6,
}
SCHEMA_B_VALID_CELL_COUNTS = {7}
 
SPORT_SCHEMA = {
    "football": "A",
    "baseball": "A",
    "boys_basketball": "A",
    "girls_basketball": "A",
    "boys_soccer": "A",
    "girls_soccer": "A",
    "fall_softball": "A",
    "spring_softball": "A",
    "girls_volleyball": "B",
}
 
 
# ---------------------------------------------------------------------------
# PARSING (same logic as the current-season scraper)
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
 
 
def _parse_schema_b_row(row, cells):
    if len(cells) not in SCHEMA_B_VALID_CELL_COUNTS:
        return None
 
    base = _common_fields(row, cells, SCHEMA_B_CELL_INDEX)
    if base is None:
        return None
 
    ci = SCHEMA_B_CELL_INDEX
 
    team = {
        **base,
        "mov": parse_number(cells[ci["mov"]].get_text(strip=True)),
        "wins": parse_number(cells[ci["wins"]].get_text(strip=True)),
        "losses": parse_number(cells[ci["losses"]].get_text(strip=True)),
        "win_pct": parse_number(cells[ci["win_pct"]].get_text(strip=True)),
        "games_played": None,
    }
    if team["wins"] is not None and team["losses"] is not None:
        team["games_played"] = team["wins"] + team["losses"]
    return team
 
 
def parse_season_records_html(html, sport_key):
    schema = SPORT_SCHEMA.get(sport_key, "A")
    soup = BeautifulSoup(html, "html.parser")
 
    rows = soup.find_all("tr", class_=ROW_SELECTOR_CLASS)
    if not rows:
        raise RuntimeError(
            f"No <tr class=\"{ROW_SELECTOR_CLASS}\"> rows found on the page. "
            f"Either this season has no data for this sport, MSHSAA's markup "
            f"differs for this year, or the alg/year combo is wrong."
        )
 
    teams = []
    for row in rows:
        cells = row.find_all("td")
        if schema == "B":
            team = _parse_schema_b_row(row, cells)
        else:
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
    """2017 -> '2017-2018'"""
    return f"{year}-{year + 1}"
 
 
# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
 
def scrape_sport_year(sport_key, sport_alg, year, output_dir):
    out_path = Path(output_dir) / sport_key / f"{sport_key}_{year}.json"
 
    html, url = fetch_page(sport_alg, year)
    teams = parse_season_records_html(html, sport_key)
 
    output = {
        "sport": sport_key,
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
        description="Backfill MSHSAA season records across multiple seasons."
    )
    parser.add_argument(
        "--sports",
        type=str,
        default=None,
        help=(
            "Comma-separated list of sport keys to scrape (e.g. "
            "'boys_basketball,football'). Default: all 9 sports."
        ),
    )
    parser.add_argument("--start-year", type=int, default=START_YEAR_DEFAULT)
    parser.add_argument("--end-year", type=int, default=END_YEAR_DEFAULT)
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
 
    if args.sports:
        sport_keys = [s.strip() for s in args.sports.split(",") if s.strip()]
        unknown = [s for s in sport_keys if s not in SPORT_ALG_MAP]
        if unknown:
            print(f"Unknown sport key(s): {unknown}. Valid keys: {list(SPORT_ALG_MAP.keys())}")
            sys.exit(1)
    else:
        sport_keys = list(SPORT_ALG_MAP.keys())
 
    if args.start_year > args.end_year:
        print(f"--start-year ({args.start_year}) is after --end-year ({args.end_year}).")
        sys.exit(1)
 
    years = list(range(args.start_year, args.end_year + 1))
    output_dir = Path(args.output_dir)
    manifest_path = output_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
 
    total_jobs = len(sport_keys) * len(years)
    print(
        f"Backfilling {len(sport_keys)} sport(s) x {len(years)} season(s) "
        f"= {total_jobs} sport-season combinations.\n"
    )
 
    completed = 0
    skipped = 0
    failed = 0
 
    for sport_key in sport_keys:
        sport_alg = SPORT_ALG_MAP[sport_key]
        for year in years:
            manifest_key = f"{sport_key}_{year}"
            out_path = output_dir / sport_key / f"{sport_key}_{year}.json"
 
            if out_path.exists() and not args.force:
                print(f"[skip] {sport_key} {season_label(year)} -- already scraped")
                skipped += 1
                continue
 
            print(f"[fetch] {sport_key} {season_label(year)} (alg={sport_alg}, year={year})...")
            try:
                team_count, written_path = scrape_sport_year(
                    sport_key, sport_alg, year, output_dir
                )
                status = "ok" if team_count > 0 else "empty"
                manifest[manifest_key] = {
                    "sport": sport_key,
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
                        "this sport/year, or its markup differs. Check manually."
                    )
                completed += 1
            except Exception as e:
                manifest[manifest_key] = {
                    "sport": sport_key,
                    "year": year,
                    "season_label": season_label(year),
                    "status": "failed",
                    "team_count": None,
                    "output_path": None,
                    "error": str(e),
                }
                print(f"    [ERROR] {e}")
                failed += 1
 
            # Save the manifest after every single job, not just at the end,
            # so an interrupted run doesn't lose the record of what already
            # succeeded.
            save_manifest(manifest_path, manifest)
 
            # Be polite to MSHSAA's server -- small random jitter on top of
            # the base delay so requests aren't perfectly periodic.
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
# CHECKLIST (read before running the full 9-sport x 14-year backfill)
# ---------------------------------------------------------------------------
#
# 1. Run a SMALL test batch first, not the full backfill:
#      python3 scrape_mshsaa_history_records.py --sports boys_basketball --start-year 2017 --end-year 2018
#    This hits exactly 2 URLs and lets you check:
#      - team_count looks plausible for both years in manifest.json
#      - the season_label matches what you'd expect (2017 -> "2017-2018")
#      - spot-check one team's W-L against something you can verify by eye
#
# 2. Try one OLD year for a sport you know well, e.g.:
#      python3 scrape_mshsaa_history_records.py --sports football --start-year 2012 --end-year 2012
#    This is the riskiest part -- 2012-2013 markup may differ from the
#    confirmed-2025-26 schema. If team_count comes back 0 or clearly wrong,
#    get a real HTML fragment from that specific page (View Page Source)
#    before trusting any other old years.
#
# 3. Try one SPRING sport (baseball, boys/girls soccer, spring_softball) to
#    sanity-check the year-semantics assumption from the docstring -- i.e.
#    confirm year=2017 baseball really is "played in spring 2018" data, not
#    "played in spring 2017."
#
# 4. Only after 1-3 look right, run the full backfill:
#      python3 scrape_mshsaa_history_records.py
#    (defaults to all 9 sports, 2012-2025, output/mshsaa_historical_records/)
#    This is 126 requests at ~1.5-2s apart -- expect it to take roughly
#    4-5 minutes. If it gets interrupted, just re-run the same command;
#    --force is NOT needed to resume, since already-written files are
#    skipped automatically.
#
# 5. Check manifest.json afterward for any "status": "failed" or "empty"
#    entries before treating the backfill as complete.
