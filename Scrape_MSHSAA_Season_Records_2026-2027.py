"""
scrape_mshsaa_season_records.py
 
Scrapes MSHSAA's "Season Records" page for the CURRENT season, all 9 sports:
  https://www.mshsaa.org/Activities/SeasonRecords.aspx?alg={alg}
(no &year= param -- omitting it returns the current/in-progress season)
 
Parsing logic (row selector, cell schemas, school-ID extraction) is carried
over as-is from scrape_mshsaa_history_records.py, which has this confirmed
against live 2025-26 HTML. If you already have a working
scrape_mshsaa_season_records.py, prefer that one and use this only as a
reference/merge candidate -- see the chat note.
 
Sport coverage: includes all 9 sports now (not just the 4 fall sports
currently in season) so nothing needs to be added later. Winter/spring
sports (baseball, boys/girls basketball, girls soccer, spring softball)
will simply come back with 0 teams until their season's data appears on
MSHSAA's site -- that's expected and logged as "no data yet", not treated
as a failure.
"""
 
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
 
import requests
from bs4 import BeautifulSoup
 
# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
 
BASE_URL = "https://www.mshsaa.org/Activities/SeasonRecords.aspx"
 
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
 
OUTPUT_DIR_DEFAULT = "output/season_records"
 
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
# COLUMN SCHEMAS (carried over from scrape_mshsaa_history_records.py)
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
 
# Girls Volleyball is scored in sets, not points -- MSHSAA doesn't publish
# PF/PA/PPG/OPPG for it, so it uses the reduced schema. All other sports
# use the full schema. Revisit this if MSHSAA changes their markup.
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
# PARSING
# ---------------------------------------------------------------------------
 
def parse_number(raw):
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if text in ("", "-", "--"):
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
 
    classification_label = cells[cell_index["classification_label"]].get_text(strip=True)
 
    return {
        "school": school_name,
        "mshsaa_school_id": _extract_school_id(cells[0]),
        "classification_code": parse_number(row.get("data-classification")),
        "classification_label": classification_label or None,
        "district": parse_number(row.get("data-district"))
        or parse_number(cells[cell_index["district"]].get_text(strip=True)),
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
    teams = []
    for row in rows:
        cells = row.find_all("td")
        team = _parse_schema_b_row(row, cells) if schema == "B" else _parse_schema_a_row(row, cells)
        if team is not None:
            teams.append(team)
    return teams
 
 
# ---------------------------------------------------------------------------
# FETCHING
# ---------------------------------------------------------------------------
 
def fetch_page(sport_alg):
    url = f"{BASE_URL}?alg={sport_alg}"
    last_error = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text, url
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < MAX_FETCH_ATTEMPTS:
                print(f"    [retry {attempt}/{MAX_FETCH_ATTEMPTS - 1}] fetch failed ({e}), waiting {RETRY_BACKOFF_SECONDS}s...")
                time.sleep(RETRY_BACKOFF_SECONDS)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_FETCH_ATTEMPTS} attempts: {last_error}")
 
 
# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
 
def scrape_sport(sport_key, sport_alg, output_dir):
    out_path = Path(output_dir) / f"{sport_key}_season_records.json"
    html, url = fetch_page(sport_alg)
    teams = parse_season_records_html(html, sport_key)
 
    output = {
        "sport": sport_key,
        "source_url": url,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    return len(teams), str(out_path)
 
 
def main():
    output_dir = Path(OUTPUT_DIR_DEFAULT)
    all_records = {}
 
    for sport_key, sport_alg in SPORT_ALG_MAP.items():
        print(f"Scraping {sport_key} (alg={sport_alg})...")
        try:
            team_count, out_path = scrape_sport(sport_key, sport_alg, output_dir)
        except Exception as e:
            print(f"  [ERROR] {e}")
            continue
 
        if team_count == 0:
            print(f"  -> 0 teams (no data yet -- expected if {sport_key}'s season hasn't started)")
        else:
            print(f"  -> {team_count} teams -> {out_path}")
 
        with open(out_path) as f:
            all_records[sport_key] = json.load(f)["teams"]
 
        time.sleep(REQUEST_DELAY_SECONDS)
 
    combined_path = output_dir / "all_sports_season_records.json"
    with open(combined_path, "w") as f:
        json.dump(all_records, f, indent=2)
    print(f"\nWrote combined file: {combined_path}")
 
 
if __name__ == "__main__":
    main()
