"""
merge_team_history.py

Builds one team_history_{sport}.json per sport for the "Team History" tab,
by joining each sport's Stats History file (record, PPG, PAPG) with its
Ratings History file (OFF/DEF/OVR), then computing self-relative ranks
(this team's best-ever OVR/OFF/DEF season, not statewide).

Usage:
    python merge_team_history.py                # run all configured sports
    python merge_team_history.py boys_basketball # run just one sport

Each sport is config-driven so the same join/rank logic handles all 9 sports
despite the schema differences (scoring stats present or not, football's
mshsaa_points column, etc.) -- only SPORT_CONFIG changes per sport, not the
core logic.
"""

import json
import os
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Per-sport config. `has_scoring` controls whether PPG/PAPG/PDPG are pulled
# from the stats file (football, basketball, baseball, soccer, softball) or
# omitted in favor of MOV (volleyball, per the known schema gap).
#
# File paths follow the pattern you've used elsewhere:
#   output/mshsaa_historical_records/{sport}/{Sport_Name}_Stats_History_2012-2025.json
#   output/mshsaa_historical_records/{sport}/{Sport_Name}_Ratings_History_2012-2025.json
#
# CONFIRM before running for real: exact filenames/years for the 7 sports
# not shown here -- football likely covers 2010-2025 (your rating engine
# adaptation note), not 2012-2025, so double check that range per sport.
# ---------------------------------------------------------------------------

DATA_ROOT = "output/mshsaa_historical_records"  # relative to All_MO_Sports-Data repo root

SPORT_CONFIG = {
    "boys_basketball": {
        "display_name": "Boys Basketball",
        "stats_file": f"{DATA_ROOT}/boys_basketball/Boys_Basketball_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/boys_basketball/Boys_Basketball_Ratings_History_2012-2025.json",
        "has_scoring": True,
    },
    "girls_basketball": {
        "display_name": "Girls Basketball",
        "stats_file": f"{DATA_ROOT}/girls_basketball/Girls_Basketball_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/girls_basketball/Girls_Basketball_Ratings_History_2012-2025.json",
        "has_scoring": True,
    },
    "football": {
        "display_name": "Football",
        "stats_file": f"{DATA_ROOT}/football/Football_Stats_History_2010-2025.json",
        "ratings_file": f"{DATA_ROOT}/football/Football_Ratings_History_2010-2025.json",
        "has_scoring": True,
    },
    "boys_soccer": {
        "display_name": "Boys Soccer",
        "stats_file": f"{DATA_ROOT}/boys_soccer/Boys_Soccer_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/boys_soccer/Boys_Soccer_Ratings_History_2012-2025.json",
        "has_scoring": True,
    },
    "girls_soccer": {
        "display_name": "Girls Soccer",
        "stats_file": f"{DATA_ROOT}/girls_soccer/Girls_Soccer_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/girls_soccer/Girls_Soccer_Ratings_History_2012-2025.json",
        "has_scoring": True,
    },
    "baseball": {
        "display_name": "Baseball",
        "stats_file": f"{DATA_ROOT}/baseball/Baseball_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/baseball/Baseball_Ratings_History_2012-2025.json",
        "has_scoring": True,
    },
    "spring_softball": {
        "display_name": "Spring Softball",
        "stats_file": f"{DATA_ROOT}/spring_softball/Spring_Softball_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/spring_softball/Spring_Softball_Ratings_History_2012-2025.json",
        "has_scoring": True,
    },
    "fall_softball": {
        "display_name": "Fall Softball",
        "stats_file": f"{DATA_ROOT}/fall_softball/Fall_Softball_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/fall_softball/Fall_Softball_Ratings_History_2012-2025.json",
        "has_scoring": True,
    },
    "girls_volleyball": {
        "display_name": "Girls Volleyball",
        "stats_file": f"{DATA_ROOT}/girls_volleyball/Girls_Volleyball_Stats_History_2012-2025.json",
        "ratings_file": f"{DATA_ROOT}/girls_volleyball/Girls_Volleyball_Ratings_History_2012-2025.json",
        "has_scoring": False,  # no PF/PA/PPG/OPPG -- MOV only
    },
}

OUTPUT_DIR = f"{DATA_ROOT}/team_history"  # writes team_history_{sport}.json here


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def index_stats_by_year(stats_doc):
    """{year: {school_name: team_stats_dict}}"""
    out = {}
    for season in stats_doc["seasons"]:
        year = season["year"]
        out[year] = {team["school"]: team for team in season["teams"]}
    return out


def build_season_entry(rating_team, stats_team, has_scoring):
    """One school-season row for the output file."""
    entry = {
        "classification": rating_team.get("classification"),
        "district": rating_team.get("district"),
        "off_rating": rating_team.get("off_rating"),
        "def_rating": rating_team.get("def_rating"),
        "ovr_rating": rating_team.get("ovr_rating"),
        # self-relative ranks filled in later, once all seasons for this
        # school are collected
        "off_rank": None,
        "def_rank": None,
        "ovr_rank": None,
    }

    if stats_team is not None:
        wins = stats_team.get("wins")
        losses = stats_team.get("losses")
        entry["record"] = f"{wins}-{losses}" if wins is not None and losses is not None else None
        if has_scoring:
            ppg = stats_team.get("ppg")
            papg = stats_team.get("oppg")
            entry["ppg"] = ppg
            entry["papg"] = papg
            entry["pdpg"] = round(ppg - papg, 2) if ppg is not None and papg is not None else None
        else:
            # Volleyball-style sports: no PPG/PAPG available, fall back to MOV
            entry["mov"] = stats_team.get("mov")
    else:
        entry["record"] = None
        if has_scoring:
            entry["ppg"] = entry["papg"] = entry["pdpg"] = None
        else:
            entry["mov"] = None

    return entry


def compute_self_relative_ranks(seasons):
    """
    Ranks each school's own seasons against each other (1 = that school's
    best-ever season for that metric), not against other schools statewide.
    Higher rating = better for OFF, DEF, and OVR in this rating system.
    """
    for metric, rank_key in (("off_rating", "off_rank"), ("def_rating", "def_rank"), ("ovr_rating", "ovr_rank")):
        ranked = sorted(
            (s for s in seasons if s.get(metric) is not None),
            key=lambda s: s[metric],
            reverse=True,
        )
        for i, season in enumerate(ranked, start=1):
            season[rank_key] = i


def merge_sport(sport_key, config):
    if not os.path.exists(config["stats_file"]) or not os.path.exists(config["ratings_file"]):
        print(f"[skip] {sport_key}: file(s) not found -- confirm path in SPORT_CONFIG")
        return None

    stats_doc = load_json(config["stats_file"])
    ratings_doc = load_json(config["ratings_file"])
    stats_by_year = index_stats_by_year(stats_doc)

    schools = {}
    unmatched = []  # (year, school_name) present in ratings but not in stats that year

    for season in ratings_doc["seasons"]:
        year = season["year"]
        year_stats = stats_by_year.get(year, {})

        for rating_team in season["teams"]:
            school_name = rating_team["school"]
            stats_team = year_stats.get(school_name)
            if stats_team is None:
                unmatched.append((year, school_name))

            entry = build_season_entry(rating_team, stats_team, config["has_scoring"])
            entry["year"] = year

            schools.setdefault(school_name, []).append(entry)

    for school_name, seasons in schools.items():
        seasons.sort(key=lambda s: s["year"])
        compute_self_relative_ranks(seasons)

    output = {
        "sport": sport_key,
        "display_name": config["display_name"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "has_scoring": config["has_scoring"],
        "schools": schools,
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = f"{OUTPUT_DIR}/team_history_{sport_key}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"[ok] {sport_key}: wrote {out_path} ({len(schools)} schools)")
    if unmatched:
        print(f"     {len(unmatched)} school-seasons in ratings had no stats match -- review these:")
        for year, name in unmatched[:20]:
            print(f"       {year}  {name}")
        if len(unmatched) > 20:
            print(f"       ... and {len(unmatched) - 20} more")

    return out_path


def main():
    requested = sys.argv[1:] or list(SPORT_CONFIG.keys())
    for sport_key in requested:
        if sport_key not in SPORT_CONFIG:
            print(f"[error] unknown sport '{sport_key}' -- check SPORT_CONFIG keys")
            continue
        merge_sport(sport_key, SPORT_CONFIG[sport_key])


if __name__ == "__main__":
    main()
