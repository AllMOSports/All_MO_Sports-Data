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
 
# Confirmed real structure (verified against actual page source, 2026-06-27):
#   <tr class="fs_tablecolumn" data-classification="6" data-district="8">
#     <td class="large">
#       <span class="schoolicon" title="..."><img ...></span>
#       <a href="/MySchool/Schedule.aspx?s=907&alg=19">Liberty North</a>
#     </td>
#     <td>...classification (repeated, text)...</td>
#     <td>...district (repeated, text)...</td>
#     <td>PF</td><td>PA</td><td>PPG</td><td>OPPG</td><td>MOV</td>
#     <td>Wins</td><td>Losses</td><td>Win%</td><td>Points</td>
#   </tr>
# 12 <td> cells total. classification/district are ALSO present as row
# attributes (data-classification, data-district) -- those are read
# directly off the <tr> rather than parsed from cell text, since the
# attribute is cleaner (e.g. "0" for 8-Man instead of the string "8-Man").
# We keep the human-readable classification label too, since "0" isn't
# meaningful on its own for an 8-Man team.
ROW_SELECTOR_CLASS = "fs_tablecolumn"
 
# td index (0-based) for each stat, AFTER the first (school name) cell.
# Cell 0 = school name+logo, then 11 more stat cells in this order:
CELL_INDEX = {
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
    "mshsaa_points": 11,
}
EXPECTED_CELL_COUNT = 12
 
 
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
 
 
def parse_season_records_html(html):
    """
    Parse the MSHSAA season records table into a list of team dicts.
 
    Selector confirmed against real page source: rows are
    <tr class="fs_tablecolumn" data-classification="N" data-district="N">,
    each containing exactly 12 <td> cells. See CELL_INDEX above for the
    column mapping. The school name lives in the first cell alongside a
    logo <img> inside a nested <span> -- get_text() on that cell ignores
    the image and returns just the team name correctly.
    """
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
        if len(cells) != EXPECTED_CELL_COUNT:
            # Skip malformed/unexpected rows rather than guessing at a
            # shifted column mapping -- better to silently drop one row
            # than silently corrupt many.
            continue
 
        school_name = cells[0].get_text(strip=True)
        if not school_name:
            continue
 
        # Row-level data attributes are the canonical classification/
        # district (numeric, including "0" for 8-Man) -- prefer these
        # over the text cell, which renders 8-Man as the literal string
        # "8-Man" instead of a number.
        raw_classification_attr = row.get("data-classification")
        raw_district_attr = row.get("data-district")
 
        classification_label = cells[CELL_INDEX["classification_label"]].get_text(strip=True)
 
        team = {
            "school": school_name,
            "mshsaa_school_id": _extract_school_id(cells[0]),
            "classification_code": parse_number(raw_classification_attr),
            "classification_label": classification_label or None,
            "district": parse_number(raw_district_attr) or parse_number(
                cells[CELL_INDEX["district"]].get_text(strip=True)
            ),
            "points_for": parse_number(cells[CELL_INDEX["points_for"]].get_text(strip=True)),
            "points_against": parse_number(cells[CELL_INDEX["points_against"]].get_text(strip=True)),
            "ppg": parse_number(cells[CELL_INDEX["ppg"]].get_text(strip=True)),
            "oppg": parse_number(cells[CELL_INDEX["oppg"]].get_text(strip=True)),
            "mov": parse_number(cells[CELL_INDEX["mov"]].get_text(strip=True)),
            "wins": parse_number(cells[CELL_INDEX["wins"]].get_text(strip=True)),
            "losses": parse_number(cells[CELL_INDEX["losses"]].get_text(strip=True)),
            "win_pct": parse_number(cells[CELL_INDEX["win_pct"]].get_text(strip=True)),
            "mshsaa_points": parse_number(cells[CELL_INDEX["mshsaa_points"]].get_text(strip=True)),
            "games_played": None,  # computed below
        }
 
        if team["wins"] is not None and team["losses"] is not None:
            team["games_played"] = team["wins"] + team["losses"]
 
        teams.append(team)
 
    return teams
 
 
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
 
    teams = parse_season_records_html(html)
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
# FIRST RUN CHECKLIST (read before enabling the nightly workflow)
# ---------------------------------------------------------------------------
#
# Row/cell selectors below were verified against real page source on
# 2026-06-27 (a pasted HTML fragment from View Page Source around the
# Liberty North row). That fragment confirmed: rows are
# <tr class="fs_tablecolumn" data-classification="N" data-district="N">,
# each with exactly 12 <td> cells, school name+logo in the first cell.
# This is NOT the same as a live end-to-end run -- the script has never
# successfully fetched the live page itself (network-restricted build
# environment), only parsed a hand-pasted fragment of real markup. The
# fetch (requests.get) and the auth/headers/blocking behavior of the
# live site are still unverified.
#
# 1. Run this script manually once: `python3 scrape_mshsaa_season_records.py`
# 2. Open output/mshsaa_records/football.json and check:
#    - Is the team count roughly what you'd expect (~300+ for football)?
#    - Spot check Liberty North: wins=7, losses=5, points_for=359,
#      points_against=302, ppg=29.92, oppg=25.17, mshsaa_school_id=907
#      (confirmed against real page source as of 2026-06-27 -- NOTE this
#      does not exactly match an earlier JS-rendered fetch of the same
#      page, which showed 362/299/30.17/24.92; if your own run shows yet
#      a third set of numbers, that's likely just the season continuing
#      to update rather than a parsing bug -- cross-check wins/losses
#      first since those are least likely to be a parsing artifact).
# 3. If team count is 0:
#    - Most likely cause: requests.get() is being blocked or served a
#      different page than a real browser gets (some ASP.NET sites gate
#      on cookies, a session token, or bot-detection headers). Try
#      printing len(html) and searching it for "fs_tablecolumn" -- if
#      that string isn't present in what requests.get() returned at all,
#      the issue is in the fetch, not the parser, and likely needs
#      Playwright (like your existing ratings scraper uses) instead of
#      plain requests.
# 4. Once verified working, find the alg= values for your other 8 sports
#    and fill in SPORT_ALG_MAP before relying on this for anything but
#    football.
