"""
scrape_district_basketball_brackets.py
 
Scrapes MSHSAA District Tournament bracket pages for Boys Basketball
(alg=5) across all Classes (1-6), Districts (1-16), and seasons
(year=2012 through year=2025, i.e. the 2012-2013 through 2025-2026
seasons) and extracts every game in every bracket.
 
URL pattern:
  https://www.mshsaa.org/Activities/DistrictTournaments.aspx?alg=5&class={C}&district={D}&year={Y}
 
Not every Class/District combination is valid in every year (district
counts per class shift over time as classifications are reorganized).
Invalid combinations are detected and skipped automatically -- the
script does not assume a fixed grid.
 
Output: a single JSON file containing one record per valid bracket
found, each with its full list of games. The script is resumable:
if the output file already exists, already-scraped (class, district,
year) combinations are skipped, so a killed/interrupted run can just
be re-launched.
 
Usage:
  python scrape_district_basketball_brackets.py \
      --start-year 2012 --end-year 2025 \
      --classes 1 2 3 4 5 6 --districts 1-16 \
      --output boys_basketball_district_brackets_2012-2026.json \
      --delay 1.5
 
  # Resume an interrupted run (just re-run the same command):
  python scrape_district_basketball_brackets.py --output boys_basketball_district_brackets_2012-2026.json
"""
 
import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
 
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
 
BASE_URL = "https://www.mshsaa.org/Activities/DistrictTournaments.aspx"
 
# alg=5 -> Boys Basketball, alg=6 -> Girls Basketball (kept for future use)
SPORT_ALG = {
    "boys_basketball": 5,
    "girls_basketball": 6,
}
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
 
 
def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
 
 
def fetch_page(session: requests.Session, class_num: int, district: int, year: int, alg: int) -> str | None:
    """Fetch raw HTML for one bracket page. Returns None on hard failure."""
    params = {"alg": alg, "class": class_num, "district": district, "year": year}
    try:
        resp = session.get(BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        log.warning(f"Request failed for class={class_num} district={district} year={year}: {exc}")
        return None
 
 
def parse_bracket(html: str, class_num: int, district: int, year: int, sport: str) -> dict | None:
    """
    Parse a bracket page's HTML into a structured record.
    Returns None if this class/district/year combination has no valid
    bracket (i.e. it doesn't exist for that year).
    """
    soup = BeautifulSoup(html, "lxml")
 
    bracket_container = soup.find("div", class_="tournamentBracketContainer")
    if bracket_container is None:
        return None
 
    bracket_div = bracket_container.find("div", class_=re.compile(r"tournamentBracket\s+bracket\d+Rounds"))
    if bracket_div is None:
        return None
 
    bracket_class = next((c for c in bracket_div["class"] if "Rounds" in c), None)
    rounds_match = re.search(r"bracket(\d+)Rounds", bracket_class or "")
    num_rounds = int(rounds_match.group(1)) if rounds_match else None
 
    header_div = bracket_div.find("div", class_="bracketHeader")
    header_text = header_div.get_text(" | ", strip=True) if header_div else None
 
    # Round names in left-to-right (earliest -> latest) order, e.g.
    # ["Quarterfinals", "Semifinals", "Final", "Champion"]
    round_headers = [th.get_text(strip=True) for th in bracket_div.select("table thead th")]
 
    # ---- Parse team/score cells, grouped by game number (g1, g2, ...) ----
    games_raw: dict[int, list[dict]] = {}
    for td in bracket_div.select('td[class*="team"]'):
        classes = td.get("class", [])
        g_match = next((c for c in classes if re.fullmatch(r"g\d+", c)), None)
        if not g_match:
            continue
        game_num = int(g_match[1:])
 
        school_tag = td.find("a", class_="school")
        if not school_tag:
            continue  # bye / placeholder cell
 
        seed_tag = td.find("span", class_="seed")
        score_tag = td.find("span", class_="score")
        school_href = school_tag.get("href", "")
        school_id_match = re.search(r"[?&]s=(\d+)", school_href)
 
        games_raw.setdefault(game_num, []).append({
            "seed": seed_tag.get_text(strip=True) if seed_tag else None,
            "school": school_tag.get_text(strip=True),
            "school_id": school_id_match.group(1) if school_id_match else None,
            "score": score_tag.get_text(strip=True) if score_tag else None,
            "winner": "winner" in classes,
            "position": "top" if "top" in classes else ("bottom" if "bottom" in classes else None),
        })
 
    # ---- Date/time + tournament id metadata per game ----
    game_meta: dict[int, dict] = {}
    for a in bracket_div.select('td[class*="time"] a'):
        m = re.search(r"tournament=(\d+)&id=(\d+)", a.get("href", ""))
        if m:
            tid, gid = m.groups()
            game_meta[int(gid)] = {"tournament_id": tid, "date_time": a.get_text(strip=True)}
 
    if not games_raw:
        return None
 
    # ---- Assemble final game list with round names ----
    games = []
    for game_num, teams in games_raw.items():
        if len(teams) != 2:
            # Incomplete/bye game -- record what we have but flag it
            round_name = _round_name_for_game(game_num, round_headers)
            games.append({
                "game_num": game_num,
                "round": round_name,
                "tournament_id": game_meta.get(game_num, {}).get("tournament_id"),
                "date_time": game_meta.get(game_num, {}).get("date_time"),
                "incomplete": True,
                "teams": teams,
            })
            continue
 
        top = next((t for t in teams if t["position"] == "top"), teams[0])
        bottom = next((t for t in teams if t["position"] == "bottom"), teams[1])
        round_name = _round_name_for_game(game_num, round_headers)
 
        games.append({
            "game_num": game_num,
            "round": round_name,
            "tournament_id": game_meta.get(game_num, {}).get("tournament_id"),
            "date_time": game_meta.get(game_num, {}).get("date_time"),
            "incomplete": False,
            "team1": top["school"],
            "team1_id": top["school_id"],
            "team1_seed": top["seed"],
            "team1_score": top["score"],
            "team1_winner": top["winner"],
            "team2": bottom["school"],
            "team2_id": bottom["school_id"],
            "team2_seed": bottom["seed"],
            "team2_score": bottom["score"],
            "team2_winner": bottom["winner"],
        })
 
    champ_tag = bracket_div.find("td", class_="champion")
    champion = None
    champion_id = None
    if champ_tag:
        champ_school = champ_tag.find("a", class_="school")
        if champ_school:
            champion = champ_school.get_text(strip=True)
            m = re.search(r"[?&]s=(\d+)", champ_school.get("href", ""))
            champion_id = m.group(1) if m else None
 
    games.sort(key=lambda g: g["game_num"])
 
    return {
        "sport": sport,
        "class": class_num,
        "district": district,
        "year": year,
        "season_label": f"{year}-{year + 1}",
        "num_rounds": num_rounds,
        "header_text": header_text,
        "champion": champion,
        "champion_id": champion_id,
        "games": games,
    }
 
 
def _round_name_for_game(game_num: int, round_headers: list[str]) -> str | None:
    """
    Games are numbered such that game 1 is always the final, games 2-3
    are the semifinals, games 4-7 are the quarterfinals, etc. (i.e.
    round_number = floor(log2(game_num)) + 1, counting from the final).
    round_headers is ordered earliest-round-first, e.g.
    ["Quarterfinals", "Semifinals", "Final", "Champion"].
    """
    if not round_headers:
        return None
    total_rounds = len(round_headers) - 1  # drop trailing "Champion" column
    round_number_from_final = game_num.bit_length()  # 1->1, 2,3->2, 4-7->3, ...
    idx = total_rounds - round_number_from_final
    if 0 <= idx < len(round_headers):
        return round_headers[idx]
    return None
 
 
def load_existing(output_path: Path) -> tuple[list[dict], set[tuple[int, int, int]]]:
    if not output_path.exists():
        return [], set()
    with open(output_path, "r", encoding="utf-8") as f:
        records = json.load(f)
    done = {(r["class"], r["district"], r["year"]) for r in records}
    log.info(f"Resuming: {len(records)} brackets already scraped, will skip those combinations.")
    return records, done
 
 
def save(records: list[dict], output_path: Path) -> None:
    tmp_path = output_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    tmp_path.replace(output_path)
 
 
def parse_district_range(spec: str) -> list[int]:
    """Parses '1-16' or '1,3,5' or a single '4' into a list of ints."""
    if "-" in spec:
        lo, hi = spec.split("-")
        return list(range(int(lo), int(hi) + 1))
    return [int(x) for x in spec.split(",")]
 
 
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2012)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--classes", type=int, nargs="+", default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--districts", type=str, default="1-16", help="e.g. '1-16' or '1,2,5'")
    parser.add_argument("--sport", type=str, default="boys_basketball", choices=list(SPORT_ALG.keys()))
    parser.add_argument("--output", type=str, default="boys_basketball_district_brackets_2012-2026.json")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds to sleep between requests")
    parser.add_argument("--checkpoint-every", type=int, default=25, help="Save to disk every N new brackets")
    args = parser.parse_args()
 
    districts = parse_district_range(args.districts)
    alg = SPORT_ALG[args.sport]
    output_path = Path(args.output)
 
    records, done = load_existing(output_path)
    session = build_session()
 
    total_combos = (args.end_year - args.start_year + 1) * len(args.classes) * len(districts)
    attempted = 0
    found = 0
    skipped_existing = 0
    invalid = 0
    new_since_checkpoint = 0
 
    log.info(f"Starting sweep: years {args.start_year}-{args.end_year}, "
             f"classes {args.classes}, districts {districts} ({total_combos} combinations to check)")
 
    for year in range(args.start_year, args.end_year + 1):
        for class_num in args.classes:
            for district in districts:
                attempted += 1
                key = (class_num, district, year)
                if key in done:
                    skipped_existing += 1
                    continue
 
                html = fetch_page(session, class_num, district, year, alg)
                if html is None:
                    invalid += 1
                    time.sleep(args.delay)
                    continue
 
                record = parse_bracket(html, class_num, district, year, args.sport)
                if record is None:
                    invalid += 1
                    log.info(f"  no bracket: class={class_num} district={district} year={year}")
                else:
                    found += 1
                    new_since_checkpoint += 1
                    records.append(record)
                    log.info(f"  OK: class={class_num} district={district} year={year} "
                             f"-> {len(record['games'])} games, champion={record['champion']}")
 
                if new_since_checkpoint >= args.checkpoint_every:
                    save(records, output_path)
                    new_since_checkpoint = 0
                    log.info(f"Checkpoint saved ({len(records)} brackets so far).")
 
                time.sleep(args.delay)
 
    save(records, output_path)
    log.info(
        f"Done. Attempted={attempted} Found={found} Invalid/empty={invalid} "
        f"SkippedAlreadyDone={skipped_existing} -> {output_path} ({len(records)} total brackets)"
    )
 
 
if __name__ == "__main__":
    sys.exit(main())
