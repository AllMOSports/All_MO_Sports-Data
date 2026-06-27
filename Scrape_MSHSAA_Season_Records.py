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
 
# Confirmed: football = 19. Other sports TBD -- fill these in once you
# find the correct alg value for each (see docstring above).
SPORT_ALG_MAP = {
    "football": 19,
    # "boys_basketball": None,
    # "girls_basketball": None,
    # "boys_soccer": None,
    # "girls_soccer": None,
    # "baseball": None,
    # "softball": None,
    # "girls_volleyball": None,
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
 
# Expected column order on the MSHSAA season records table, based on the
# header row: School | Class | District | PF | PA | PPG | OPPG | MOV |
# Wins | Losses | Win% | Points
EXPECTED_COLUMNS = [
    "school", "classification", "district", "points_for", "points_against",
    "ppg", "oppg", "mov", "wins", "losses", "win_pct", "mshsaa_points",
]
 
 
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
 
    NOTE: the exact table/row selectors here are a best-effort guess
    (ASP.NET GridView tables commonly render as <table id="...GridView...">
    with plain <tr>/<td> rows, no special classes). If this returns zero
    rows on a real run, open the page's "view source" in a browser, find
    the actual table, and update the selector below accordingly -- the
    rest of the parsing logic (cell order, number coercion) should not
    need to change.
    """
    soup = BeautifulSoup(html, "html.parser")
 
    table = soup.find("table", id=re.compile(r"GridView", re.IGNORECASE))
    if table is None:
        table = soup.find("table")  # fallback: just grab the first table
    if table is None:
        raise RuntimeError(
            "No <table> found on the page at all -- the page may require "
            "JavaScript to render the table client-side, or the URL/alg "
            "value may be wrong. Inspect the raw HTML manually."
        )
 
    rows = table.find_all("tr")
    teams = []
 
    for row in rows:
        cells = row.find_all("td")
        if not cells or len(cells) < len(EXPECTED_COLUMNS):
            continue  # header row or malformed row, skip
 
        # School name is often wrapped in an <a> tag (a link to the
        # team's own page) -- get_text() handles that transparently.
        values = [c.get_text(strip=True) for c in cells[: len(EXPECTED_COLUMNS)]]
 
        record = dict(zip(EXPECTED_COLUMNS, values))
 
        if not record.get("school"):
            continue
 
        team = {
            "school": record["school"],
            "classification": record["classification"] or None,
            "district": parse_number(record["district"]),
            "points_for": parse_number(record["points_for"]),
            "points_against": parse_number(record["points_against"]),
            "ppg": parse_number(record["ppg"]),
            "oppg": parse_number(record["oppg"]),
            "mov": parse_number(record["mov"]),
            "wins": parse_number(record["wins"]),
            "losses": parse_number(record["losses"]),
            "win_pct": parse_number(record["win_pct"]),
            "games_played": None,  # computed below
        }
 
        if team["wins"] is not None and team["losses"] is not None:
            team["games_played"] = team["wins"] + team["losses"]
 
        teams.append(team)
 
    return teams
 
 
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
# 1. Run this script manually once: `python3 scrape_mshsaa_season_records.py`
# 2. Open output/mshsaa_records/football.json and check:
#    - Is the team count roughly what you'd expect (~300+ for football)?
#    - Spot check Liberty North: wins=7, losses=5, points_for=362,
#      points_against=299, ppg=30.17, oppg=24.92 (matches the live page
#      as of this writing -- if your scrape shows something very
#      different, the column mapping is probably off by one).
# 3. If team count is 0 or values look shifted/wrong:
#    - The table selector (`table.find_all(...)`) almost certainly needs
#      adjusting to match MSHSAA's actual HTML. View page source in a
#      browser and find the real table id/class.
#    - Also double check EXPECTED_COLUMNS still matches the page's
#      header row order -- if MSHSAA changes column order this breaks
#      silently in a way that looks like valid data, so the Liberty
#      North spot-check above is your safety net.
# 4. Once verified working, find the alg= values for your other 8 sports
#    and fill in SPORT_ALG_MAP before relying on this for anything but
#    football.
