"""
Scrape_MSHSAA_Season_Records_2026-2027.py
 
Scrapes MSHSAA's "Season Records" page for the CURRENT season, all 9 sports:
  https://www.mshsaa.org/Activities/SeasonRecords.aspx?alg={alg}
 
Schemas below are CONFIRMED against real View Page Source for all 9 sports
(Football, Boys Soccer, Fall Softball, Girls Volleyball from live 2026-27
in-progress pages; Baseball, Boys/Girls Basketball, Girls Soccer, Spring
Softball from their most recently completed 2025-26 pages, since those
sports haven't started yet -- format should carry over, but re-verify with
a fresh View Page Source once each one's 2026-27 page goes live, in case
MSHSAA changes anything between seasons).
 
There is no raw "Win%" column on 4 of the 9 sports (see SPORT_SCHEMA below)
-- where it's missing, win_pct is computed here from Win/Loss/Tie using the
tie-counts-as-half-a-win convention. Confirm this convention against a real
tied team if you want certainty; MSHSAA doesn't document it anywhere I
could find.
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
 
# Confirmed via View Page Source, one per sport, 2026-08-31/09-01:
#   POINTS      - 12 cells, ends in "Points" (MSHSAA seeding points, no raw Win%)
#   WINPCT      - 12 cells, ends in raw "Win%" text (e.g. "92.86%")
#   NOPCT       - 11 cells, ends at "Tie" -- no Win%, no Points column at all
#   VOLLEYBALL  - 8 cells, no PF/PA/PPG/OPPG, has MOV and raw Win%
SPORT_SCHEMA = {
    "football": "POINTS",
    "baseball": "WINPCT",
    "boys_basketball": "NOPCT",
    "girls_basketball": "NOPCT",
    "boys_soccer": "WINPCT",
    "girls_soccer": "WINPCT",
    "fall_softball": "WINPCT",
    "spring_softball": "WINPCT",
    "girls_volleyball": "VOLLEYBALL",
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
ROW_SELECTOR_CLASS = "fs_tablecolumn"
 
# ---------------------------------------------------------------------------
# CELL INDEX MAPS (all confirmed against raw HTML, not rendered text)
# ---------------------------------------------------------------------------
 
POINTS_CELL_INDEX = {
    "classification_label": 1, "district": 2, "points_for": 3, "points_against": 4,
    "ppg": 5, "oppg": 6, "mov": 7, "wins": 8, "losses": 9, "ties": 10,
    "mshsaa_points": 11,
}
POINTS_VALID_COUNTS = {12}
 
WINPCT_CELL_INDEX = {
    "classification_label": 1, "district": 2, "points_for": 3, "points_against": 4,
    "ppg": 5, "oppg": 6, "mov": 7, "wins": 8, "losses": 9, "ties": 10,
    "win_pct_raw": 11,
}
WINPCT_VALID_COUNTS = {12}
 
NOPCT_CELL_INDEX = {
    "classification_label": 1, "district": 2, "points_for": 3, "points_against": 4,
    "ppg": 5, "oppg": 6, "mov": 7, "wins": 8, "losses": 9, "ties": 10,
}
NOPCT_VALID_COUNTS = {11}
 
VOLLEYBALL_CELL_INDEX = {
    "classification_label": 1, "district": 2, "mov": 3,
    "wins": 4, "losses": 5, "ties": 6, "win_pct_raw": 7,
}
VOLLEYBALL_VALID_COUNTS = {8}
 
 
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
 
 
def _compute_win_pct(wins, losses, ties):
    gp = sum(v for v in (wins, losses, ties) if v is not None) or None
    if not gp:
        return None, gp
    # tie = half a win convention -- unconfirmed against MSHSAA's own math,
    # spot check a tied team's displayed % if you need certainty
    return round((((wins or 0) + 0.5 * (ties or 0)) / gp) * 100, 2), gp
 
 
def _parse_points_or_winpct_row(row, cells, cell_index, has_raw_pct):
    ci = cell_index
    base = _common_fields(row, cells, ci)
    if base is None:
        return None
 
    wins = parse_number(cells[ci["wins"]].get_text(strip=True))
    losses = parse_number(cells[ci["losses"]].get_text(strip=True))
    ties = parse_number(cells[ci["ties"]].get_text(strip=True))
 
    team = {
        **base,
        "points_for": parse_number(cells[ci["points_for"]].get_text(strip=True)),
        "points_against": parse_number(cells[ci["points_against"]].get_text(strip=True)),
        "ppg": parse_number(cells[ci["ppg"]].get_text(strip=True)),
        "oppg": parse_number(cells[ci["oppg"]].get_text(strip=True)),
        "mov": parse_number(cells[ci["mov"]].get_text(strip=True)),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "mshsaa_points": None,
        "win_pct": None,
        "games_played": None,
    }
 
    if has_raw_pct:
        team["win_pct"] = parse_number(cells[ci["win_pct_raw"]].get_text(strip=True))
        team["games_played"] = sum(v for v in (wins, losses, ties) if v is not None) or None
    else:
        team["mshsaa_points"] = parse_number(cells[ci["mshsaa_points"]].get_text(strip=True))
        team["win_pct"], team["games_played"] = _compute_win_pct(wins, losses, ties)
 
    return team
 
 
def _parse_nopct_row(row, cells):
    ci = NOPCT_CELL_INDEX
    base = _common_fields(row, cells, ci)
    if base is None:
        return None
 
    wins = parse_number(cells[ci["wins"]].get_text(strip=True))
    losses = parse_number(cells[ci["losses"]].get_text(strip=True))
    ties = parse_number(cells[ci["ties"]].get_text(strip=True))
    win_pct, games_played = _compute_win_pct(wins, losses, ties)
 
    return {
        **base,
        "points_for": parse_number(cells[ci["points_for"]].get_text(strip=True)),
        "points_against": parse_number(cells[ci["points_against"]].get_text(strip=True)),
        "ppg": parse_number(cells[ci["ppg"]].get_text(strip=True)),
        "oppg": parse_number(cells[ci["oppg"]].get_text(strip=True)),
        "mov": parse_number(cells[ci["mov"]].get_text(strip=True)),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "mshsaa_points": None,
        "win_pct": win_pct,
        "games_played": games_played,
    }
 
 
def _parse_volleyball_row(row, cells):
    ci = VOLLEYBALL_CELL_INDEX
    base = _common_fields(row, cells, ci)
    if base is None:
        return None
 
    wins = parse_number(cells[ci["wins"]].get_text(strip=True))
    losses = parse_number(cells[ci["losses"]].get_text(strip=True))
    ties = parse_number(cells[ci["ties"]].get_text(strip=True))
 
    return {
        **base,
        "points_for": None,
        "points_against": None,
        "ppg": None,
        "oppg": None,
        "mov": parse_number(cells[ci["mov"]].get_text(strip=True)),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "mshsaa_points": None,
        "win_pct": parse_number(cells[ci["win_pct_raw"]].get_text(strip=True)),
        "games_played": sum(v for v in (wins, losses, ties) if v is not None) or None,
    }
 
 
def parse_season_records_html(html, sport_key):
    schema = SPORT_SCHEMA.get(sport_key, "WINPCT")
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr", class_=ROW_SELECTOR_CLASS)
 
    teams = []
    for row in rows:
        cells = row.find_all("td")
        n = len(cells)
 
        if schema == "POINTS" and n in POINTS_VALID_COUNTS:
            team = _parse_points_or_winpct_row(row, cells, POINTS_CELL_INDEX, has_raw_pct=False)
        elif schema == "WINPCT" and n in WINPCT_VALID_COUNTS:
            team = _parse_points_or_winpct_row(row, cells, WINPCT_CELL_INDEX, has_raw_pct=True)
        elif schema == "NOPCT" and n in NOPCT_VALID_COUNTS:
            team = _parse_nopct_row(row, cells)
        elif schema == "VOLLEYBALL" and n in VOLLEYBALL_VALID_COUNTS:
            team = _parse_volleyball_row(row, cells)
        else:
            team = None  # cell count didn't match expected schema -- skip, don't guess
 
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
        "schema": SPORT_SCHEMA.get(sport_key),
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
        print(f"Scraping {sport_key} (alg={sport_alg}, schema={SPORT_SCHEMA[sport_key]})...")
        try:
            team_count, out_path = scrape_sport(sport_key, sport_alg, output_dir)
        except Exception as e:
            print(f"  [ERROR] {e}")
            continue
 
        if team_count == 0:
            print(f"  -> 0 teams (expected if {sport_key}'s season hasn't started)")
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
