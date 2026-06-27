"""
scrape_mshsaa_season_records.py
 
Scrapes MSHSAA's official "Season Records" page for a given sport and
writes out a clean, structured JSON file with one entry per team.
 
This solves a specific, narrow problem: your own scoreboard CSV doesn't
include out-of-state opponents, so a team's W-L record and points
scored/allowed computed from your CSV can be incomplete (confirmed for
Liberty North: your CSV showed 6-5 / 11 games, MSHSAA's official page
shows 7-5 / 12 games). This script pulls the OFFICIAL, complete season
totals straight from MSHSAA instead of recomputing them from your own
game log.
 
WHAT THIS DOES NOT DO:
- It does not scrape individual games or build a schedule/game-by-game
  table. That was intentionally descoped (see prior discussion) because
  it doesn't generalize cleanly to sports played nightly (basketball,
  baseball, etc).
- It does not touch ratings (OVR/OFF/DEF/SOS). Those still come from
  your existing ratings pipeline. This script only fills in record,
  points-for, points-against, and the derived per-game stats.
 
URL PATTERN:
  https://www.mshsaa.org/Activities/SeasonRecords.aspx?alg={SPORT_ALG}
  The `alg` query param selects the sport/activity. Confirmed:
    19 = football
  The alg values for your other 8 sports are NOT yet confirmed -- see
  the SPORT_ALG_MAP placeholder below. Find each by visiting the MSHSAA
  season records page in a browser, selecting the sport from whatever
  dropdown/filter the page offers, and reading the resulting `alg=`
  value out of the URL.
 
IMPORTANT - UNVERIFIED AGAINST LIVE HTML:
  This parser was written by inspecting a text rendering of the page,
  not the live HTML/DOM directly (network restrictions in the build
  environment). The CSS selectors below are a best-effort guess at a
  standard ASP.NET GridView table structure (which is what MSHSAA's
  .aspx URL pattern strongly suggests). Run this once manually and
  inspect the output before wiring it into a nightly Action -- see the
  "FIRST RUN CHECKLIST" at the bottom of this file.
"""
 
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
 
import requests
from bs4 import BeautifulSoup
 
# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
 
BASE_URL = "https://www.mshsaa.org/Activities/SeasonRecords.aspx"
 
# All 9 sports confirmed (alg values provided directly, not independently
# verified by inspecting each sport's page). IMPORTANT: the column
# structure (12 cells, same order: PF/PA/PPG/OPPG/MOV/Wins/Losses/Win%/
# Points) was only verified against football's page. Other sports may
# use a different column layout/count on MSHSAA's site (e.g. a sport
# without a meaningful "MOV" concept, or extra columns). Run each sport
# once manually and check games_played/wins/losses look sane before
# trusting the output -- the parser will silently skip any row that
# doesn't have exactly 12 <td> cells (see EXPECTED_CELL_COUNT), which
# protects against corrupted data but will also just produce a smaller
# (or empty) teams list if a sport's table shape differs, without
# raising an obvious error beyond "fewer teams than expected."
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
 
OUTPUT_DIR = "output/mshsaa_records"
 
REQUEST_HEADERS = {
    # A real browser UA reduces the chance of being blocked/served a
    # different (e.g. mobile, or bot-detection) page.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
 
# ---------------------------------------------------------------------------
# COLUMN SCHEMAS
# ---------------------------------------------------------------------------
# MSHSAA uses (at least) two different column layouts depending on the
# sport's scoring model:
#
# SCHEMA A -- "points" sports (football, basketball, baseball, softball,
# soccer): School, Class, District, PF, PA, PPG, OPPG, MOV, Wins, Losses,
# Win% [, Points]. 11 cells normally, 12 when the trailing MSHSAA-points
# column is present (confirmed: football=12, everything else so far=11).
# Confirmed directly via real page source for: football, boys_basketball,
# baseball, fall_softball, boys_soccer. Per the user, girls_basketball,
# spring_softball, and girls_soccer are expected to match their
# counterpart sport's structure (not independently verified, but a
# reasonable inference -- these are paired men's/women's or spring/fall
# versions of sports already confirmed).
#
# SCHEMA B -- "sets" sports (volleyball): School, Class, District, a
# single ratio stat, Wins, Losses, Win%. 7 cells. Volleyball doesn't have
# a points-for/against concept the way football/basketball do -- the
# single numeric column here (e.g. "2.37" for Oak Grove) is NOT yet
# identified with certainty. Strong candidates based on typical
# volleyball stat-tracking conventions: sets won per match, or a
# kill/set ratio. Whatever it is, it is NOT the same quantity as PF/PA
# in Schema A, so it's stored under a distinct, deliberately generic
# field name (set_ratio) rather than being mislabeled as points_for.
# CONFIRM WITH MSHSAA'S OWN COLUMN HEADER before treating this field as
# anything more specific than "some per-match ratio MSHSAA computes."
 
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
    "set_ratio": 3,
    "wins": 4,
    "losses": 5,
    "win_pct": 6,
}
SCHEMA_B_VALID_CELL_COUNTS = {7}
 
# Which schema each sport uses. If a sport isn't listed here, schema A is
# assumed (the more common case) -- but see the per-sport confirmation
# notes above before trusting an unlisted sport blindly.
SPORT_SCHEMA = {
    "football": "A",
    "baseball": "A",
    "boys_basketball": "A",
    "girls_basketball": "A",  # inferred from boys_basketball, not independently confirmed
    "boys_soccer": "A",
    "girls_soccer": "A",  # inferred from boys_soccer, not independently confirmed
    "fall_softball": "A",
    "spring_softball": "A",  # inferred from fall_softball, not independently confirmed
    "girls_volleyball": "B",
}
 
 
# ---------------------------------------------------------------------------
# PARSING
# ---------------------------------------------------------------------------
 
def fetch_page(sport_alg):
    url = f"{BASE_URL}?alg={sport_alg}"
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text
 
 
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
 
 
def parse_season_records_html(html, sport_key):
    """
    Parse the MSHSAA season records table into a list of team dicts.
 
    Selector confirmed against real page source: rows are
    <tr class="fs_tablecolumn" data-classification="N" data-district="N">.
 
    Dispatches to one of two schemas based on SPORT_SCHEMA[sport_key]:
      Schema A (points sports -- football, basketball, baseball,
      softball, soccer): 11 or 12 cells, PF/PA/PPG/OPPG/MOV present.
      Schema B (volleyball): 7 cells, a single set_ratio stat instead
      of points-for/against (volleyball doesn't track points the same
      way). See the SCHEMA_A_*/SCHEMA_B_* constants above for details.
 
    The school name lives in the first cell alongside a logo <img>
    inside a nested <span> -- get_text() on that cell ignores the image
    and returns just the team name correctly, for both schemas.
    """
    schema = SPORT_SCHEMA.get(sport_key, "A")
 
    soup = BeautifulSoup(html, "html.parser")
 
    rows = soup.find_all("tr", class_=ROW_SELECTOR_CLASS)
    if not rows:
        raise RuntimeError(
            f"No <tr class=\"{ROW_SELECTOR_CLASS}\"> rows found on the page. "
            f"MSHSAA may have changed their markup, or the alg= value/sport "
            f"may be wrong. Inspect the raw HTML manually (View Page Source, "
            f"not browser DevTools, to rule out JS-rendered content)."
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
 
 
def _common_fields(row, cells, cell_index):
    """Fields shared by both schemas: school name/id, classification, district."""
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
        # Row-level data attributes are the canonical classification/
        # district (numeric, including "0" for 8-Man) -- prefer these
        # over the text cell, which renders 8-Man as the literal string
        # "8-Man" instead of a number.
        "district": parse_number(raw_district_attr) or parse_number(
            cells[cell_index["district"]].get_text(strip=True)
        ),
    }
 
 
def _parse_schema_a_row(row, cells):
    """Points sports: football, basketball, baseball, softball, soccer."""
    if len(cells) not in SCHEMA_A_VALID_CELL_COUNTS:
        # Skip malformed/unexpected rows rather than guessing at a
        # shifted column mapping -- better to silently drop one row
        # than silently corrupt many.
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
    """
    Volleyball: no points-for/against. A single ratio stat instead --
    stored as set_ratio since its exact definition (sets-per-match? a
    kill ratio?) isn't confirmed. Do not assume this is equivalent to
    points_for/ppg from schema A.
    """
    if len(cells) not in SCHEMA_B_VALID_CELL_COUNTS:
        return None
 
    base = _common_fields(row, cells, SCHEMA_B_CELL_INDEX)
    if base is None:
        return None
 
    ci = SCHEMA_B_CELL_INDEX
 
    team = {
        **base,
        "set_ratio": parse_number(cells[ci["set_ratio"]].get_text(strip=True)),
        "wins": parse_number(cells[ci["wins"]].get_text(strip=True)),
        "losses": parse_number(cells[ci["losses"]].get_text(strip=True)),
        "win_pct": parse_number(cells[ci["win_pct"]].get_text(strip=True)),
        "games_played": None,
    }
    if team["wins"] is not None and team["losses"] is not None:
        team["games_played"] = team["wins"] + team["losses"]
    return team
 
 
def _extract_school_id(name_cell):
    """
    Pull MSHSAA's own internal school ID out of the schedule link, e.g.
    '/MySchool/Schedule.aspx?s=907&alg=19' -> 907.
 
    This ID is potentially a more reliable join key than name-matching
    against your own aliases.json, since it's MSHSAA's own stable
    identifier rather than a freeform string. Not used elsewhere in this
    script yet, but captured now since it's free to grab while we're
    already parsing this cell.
    """
    link = name_cell.find("a", href=True)
    if not link:
        return None
    match = re.search(r"[?&]s=(\d+)", link["href"])
    return int(match.group(1)) if match else None
 
 
# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
 
def scrape_sport(sport_key, sport_alg):
    print(f"Fetching {sport_key} (alg={sport_alg})...")
    html = fetch_page(sport_alg)
 
    teams = parse_season_records_html(html, sport_key)
    print(f"  Parsed {len(teams)} teams")
 
    if len(teams) == 0:
        print(
            f"  [WARNING] Zero teams parsed for {sport_key}. The page "
            f"structure likely differs from what this script expects. "
            f"See the FIRST RUN CHECKLIST in this file's docstring."
        )
 
    output = {
        "sport": sport_key,
        "source_url": f"{BASE_URL}?alg={sport_alg}",
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams,
    }
 
    out_path = Path(OUTPUT_DIR) / f"{sport_key}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
 
    print(f"  Wrote {out_path}")
    return output
 
 
def main():
    if not SPORT_ALG_MAP:
        print("SPORT_ALG_MAP is empty -- nothing to scrape.")
        sys.exit(1)
 
    results = {}
    for sport_key, sport_alg in SPORT_ALG_MAP.items():
        if sport_alg is None:
            print(f"Skipping {sport_key}: no alg value set yet")
            continue
        try:
            results[sport_key] = scrape_sport(sport_key, sport_alg)
        except Exception as e:
            print(f"  [ERROR] Failed to scrape {sport_key}: {e}")
            # Don't let one sport's failure kill the whole run -- continue
            # to the next sport, and let the workflow step decide whether
            # a partial failure should fail the job (see workflow YAML).
            continue
 
    succeeded = len(results)
    attempted = len([v for v in SPORT_ALG_MAP.values() if v is not None])
    print(f"\nDone: {succeeded}/{attempted} sports scraped successfully.")
 
    if succeeded < attempted:
        sys.exit(1)  # non-zero exit so GitHub Actions flags the run as failed
 
 
if __name__ == "__main__":
    main()
 
 
# ---------------------------------------------------------------------------
# STATUS LOG (kept here instead of a separate changelog, since this
# script lives and is edited in one place)
# ---------------------------------------------------------------------------
#
# 2026-06-27, run 1: First real GitHub Actions run, all 9 sports.
#   - football: 347/347 teams parsed correctly (verified: Liberty North
#     7-5, 359 PF, 302 PA -- exact match to manual spot-check).
#   - All 8 other sports: 0 teams.
#   Root cause: those tables have 11 <td> cells (no trailing "Points"
#   column), parser hard-required exactly 12 and silently dropped every
#   row. Fixed by accepting both 11 and 12 cells (SCHEMA_A_VALID_CELL_COUNTS),
#   with mshsaa_points populated only when present. Verified against a
#   real boys_basketball fragment (Bunker, Rockhurst) -- correct.
#
# 2026-06-27, run 2: Real HTML fragments provided for baseball,
#   fall_softball, boys_soccer, and girls_volleyball.
#   - baseball, fall_softball, boys_soccer: confirmed 11-cell, same
#     schema as boys_basketball (Schema A). No changes needed beyond
#     the run-1 fix -- these should now parse correctly.
#   - girls_volleyball: confirmed DIFFERENT structure entirely -- only
#     7 cells (School, Class, District, a single ratio stat, Wins,
#     Losses, Win%). No points-for/against concept at all. This is now
#     handled as a separate "Schema B" with its own field
#     (set_ratio) rather than being forced into football's field names.
#   Per the user, girls_basketball/spring_softball/girls_soccer are
#   expected (not independently verified) to mirror their already-
#   confirmed counterpart (boys_basketball/fall_softball/boys_soccer).
#
#   REMAINING UNCERTAINTY: set_ratio's exact definition (sets-won ratio?
#   kill ratio? something else?) is not confirmed -- it's stored under a
#   deliberately generic name rather than guessed at. If MSHSAA's column
#   header for that cell is visible anywhere on the live page (e.g. as
#   a <th> title or tooltip), capturing that would resolve this.
#
# ---------------------------------------------------------------------------
# CHECKLIST (read before enabling the nightly workflow)
# ---------------------------------------------------------------------------
#
# 1. Run this script: `python3 scrape_mshsaa_season_records.py`
# 2. For EACH sport's output file, check team count is non-zero and
#    plausible, and spot-check one team you can verify by eye.
# 3. Football reference (confirmed 2026-06-27): Liberty North
#    wins=7, losses=5, points_for=359, points_against=302,
#    mshsaa_school_id=907.
# 4. If a sport still returns 0 teams:
#    - First check: is requests.get() even reaching the real page?
#      Print len(html) and search it for "fs_tablecolumn" -- if that
#      string isn't present at all, the issue is the fetch (possible
#      bot-detection/blocking), not the parser, and likely needs
#      Playwright instead of plain requests.
#    - If "fs_tablecolumn" IS present but team count is still 0: that
#      sport likely has a third, not-yet-seen cell count/schema. Get a
#      real HTML fragment from that sport's page specifically (View
#      Page Source, find a row, copy ~30-40 lines) rather than assuming
#      it matches an already-confirmed sport.
