"""
build_all_team_json.py

Generalized version of the Boys Basketball join script -- covers all 9
MSHSAA sports from one parameterized script instead of 9 separate copies.

Merges each sport's current-season ratings, current-season schedule,
ratings-history, and stats-history into one lean file per team-slug:
    output/{sport}/{slug}.json

Uses ONE shared alias file across all sports (not per-sport), since a
school's identity doesn't change based on which sport it's playing --
only the specific unmatched-name list per sport differs. This also
eliminates the class of bug we hit earlier where the same raw name
resolved differently in two different per-sport alias files.
    output/school_aliases.json

Run this as the LAST step in each sport's nightly pipeline, after that
sport's current-season ratings/schedule scrapers have already run.

Usage:
    python build_all_team_json.py --sport boys_basketball --season 2025
    python build_all_team_json.py --sport all --season 2025   # loop every sport
"""

import argparse
import json
import os
import re
from pathlib import Path

DATA_REPO = Path(".")  # this script lives at the root of All_MO_Sports-Data and runs from there
SCHOOLS_JSON_PATH = DATA_REPO / "output/schools.json"
SCHOOL_ALIASES_PATH = DATA_REPO / "output/school_aliases.json"  # single shared file, all sports

# --- Per-sport configuration -------------------------------------------------
# mode: "combined"   -> a single Ratings_History / Stats_History file per sport
#       "per_season" -> only individual per-year files exist, aggregate at read time
#
# history_prefix: filename prefix used by the combined history files
#                  (matches actual casing seen in each sport's repo folder)
# years_covered: (start_year, end_year) -- football starts in 2010, others in 2012
#
# NOTE: current_ratings_repo / current_schedule_path follow the Boys
# Basketball naming convention confirmed earlier. Verify these match each
# sport's actual repo naming before running -- flagging this explicitly
# since it wasn't confirmed for all 9 sports, only Boys Basketball.

SPORT_CONFIGS = {
    "boys_basketball": {
        "mode": "combined",
        "ratings_history_file": "Boys_Basketball_Ratings_History_{start}-{end}.json",
        "stats_history_file": "Boys_Basketball_Stats_History_{start}-{end}.json",
        "years_covered": (2012, 2025),
        "ratings_repo_style": "year_range",
        "schedule_style": "year_range",
    },
    "girls_basketball": {
        "mode": "combined",
        # confirmed fully lowercase throughout, NOT just the sport-name portion --
        # this is what the earlier bug missed by assuming a shared prefix+suffix pattern
        "ratings_history_file": "girls_basketball_ratings_history_{start}-{end}.json",
        "stats_history_file": "girls_basketball_stats_history_{start}-{end}.json",
        "years_covered": (2012, 2025),
        "ratings_repo_style": "year_range",
        "schedule_style": None,
    },
    "boys_soccer": {
        "mode": "combined",
        "ratings_history_file": "Boys_Soccer_Ratings_History_{start}-{end}.json",
        "stats_history_file": "Boys_Soccer_Stats_History_{start}-{end}.json",
        "years_covered": (2012, 2025),
        "ratings_repo_style": "single_year",
        "schedule_style": None,
    },
    "fall_softball": {
        "mode": "combined",
        "ratings_history_file": "Fall_Softball_Ratings_History_{start}-{end}.json",
        "stats_history_file": "Fall_Softball_Stats_History_{start}-{end}.json",
        "years_covered": (2012, 2025),
        "ratings_repo_style": "single_year",
        "schedule_style": None,
    },
    "girls_volleyball": {
        "mode": "combined",
        "ratings_history_file": "Girls_Volleyball_Ratings_History_{start}-{end}.json",
        "stats_history_file": "Girls_Volleyball_Stats_History_{start}-{end}.json",
        "years_covered": (2012, 2025),
        "ratings_repo_style": "single_year",
        "schedule_style": None,
    },
    "football": {
        "mode": "combined",
        "ratings_history_file": "Football_Ratings_History_{start}-{end}.json",
        "stats_history_file": "Football_Stats_History_{start}-{end}.json",
        "years_covered": (2010, 2025),  # starts 2 years earlier than every other sport
        "ratings_repo_style": "single_year_hyphenated",
        "schedule_style": "single_year",
    },
    "girls_soccer": {
        "mode": "per_season",
        "years_covered": (2012, 2025),
        "ratings_repo_style": "static",
        "schedule_style": None,
    },
    "spring_softball": {
        "mode": "per_season",
        "years_covered": (2013, 2025),  # no 2012 data exists for this sport
        "ratings_repo_style": "static",
        "schedule_style": None,
    },
    "baseball": {
        "mode": "per_season",
        "years_covered": (2012, 2025),
        "ratings_repo_style": "static",
        "schedule_style": None,
    },
}


STATIC_REPO_NAMES = {
    "girls_soccer": "Girls-Soccer-Ratings",
    "spring_softball": "Spring-Softball-Rankings",
    "baseball": "baseball-ratings",
}


def resolve_ratings_repo_name(sport: str, season: int, config: dict) -> str | None:
    """
    Single source of truth for the current-season ratings repo name per sport.
    Used both by get_current_ratings_path() below and by the CLI
    --print-ratings-repo flag, so a CI workflow can `git clone` the right
    repo without re-implementing this naming logic in YAML -- that
    duplication is exactly the kind of thing that caused the earlier
    girls_basketball filename bug.
    """
    style = config["ratings_repo_style"]
    sport_title = "_".join(w.capitalize() for w in sport.split("_"))

    if style == "year_range":
        return f"{sport_title}_Ratings_{season}-{season+1}"
    if style == "single_year":
        return f"{sport_title}_Ratings_{season}"
    if style == "single_year_hyphenated":
        return f"{sport}-ratings-{season}"
    if style == "static":
        return STATIC_REPO_NAMES[sport]
    return None


def get_current_ratings_path(sport: str, season: int, config: dict) -> Path | None:
    """
    Returns the local path where this sport's current-season ratings file
    should be pulled/synced to before running this script (adjust the base
    directory to wherever you sync these repos locally). Returns None for
    static-repo sports since there's no per-season file to point to --
    those sports fetch straight from their one live repo instead.
    """
    style = config["ratings_repo_style"]
    repo_name = resolve_ratings_repo_name(sport, season, config)
    if repo_name is None:
        return None

    if style in ("year_range", "single_year", "single_year_hyphenated"):
        return Path(f"{repo_name}/{sport}_ratings_{season}.json")

    if style == "static":
        return Path(f"{repo_name}/ratings.json")

    return None


def get_current_schedule_path(sport: str, season: int, config: dict) -> Path | None:
    style = config.get("schedule_style")
    if style == "year_range":
        return DATA_REPO / f"output/{sport}_schedule_{season}-{season+1}.json"
    if style == "single_year":
        return DATA_REPO / f"output/{sport}_schedule_{season}.json"
    return None  # not built yet for this sport


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[().,'-]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_name_to_slug_map(schools_json: dict) -> dict:
    name_to_slug = {}
    for slug, record in schools_json["schools"].items():
        for candidate in (record.get("name"), record.get("mshsaa_name")):
            if candidate:
                name_to_slug[normalize_name(candidate)] = slug
    return name_to_slug


def load_aliases() -> dict:
    """Single shared alias file across all 9 sports."""
    if not SCHOOL_ALIASES_PATH.exists():
        return {}
    return load_json(SCHOOL_ALIASES_PATH)


def resolve_slug(school_name: str, name_to_slug: dict, aliases: dict, unmatched: set) -> str | None:
    key = normalize_name(school_name)
    if key in name_to_slug:
        return name_to_slug[key]

    if school_name in aliases:
        return aliases[school_name]  # may legitimately be None -- a resolved answer, not a miss

    unmatched.add(school_name)
    return None


def load_ratings_and_stats_history(sport: str, config: dict):
    """
    Returns (ratings_seasons, stats_seasons) as lists of {"year": int, "teams": [...]}
    regardless of whether the sport uses a combined history file or per-season files.
    """
    sport_folder = DATA_REPO / f"output/mshsaa_historical_records/{sport}"
    start_year, end_year = config["years_covered"]

    if config["mode"] == "combined":
        ratings_fname = config["ratings_history_file"].format(start=start_year, end=end_year)
        stats_fname = config["stats_history_file"].format(start=start_year, end=end_year)
        ratings = load_json(sport_folder / ratings_fname)
        stats = load_json(sport_folder / stats_fname)
        ratings_seasons = [
            {"year": None, "teams": s["teams"]} for s in ratings["seasons"]
        ]
        # stats history seasons already carry "year" per earlier inspection
        stats_seasons = stats["seasons"]
        return ratings_seasons, stats_seasons

    # per_season mode: only one flavor of file exists (stats-shaped), used for both
    seasons = []
    for fname in sorted(os.listdir(sport_folder)):
        if not re.match(rf"{sport}_\d{{4}}\.json$", fname):
            continue
        d = load_json(sport_folder / fname)
        seasons.append(d)
    return seasons, seasons  # same data serves both roles for these 3 sports


def process_sport(sport: str, season: int, schools_json: dict, name_to_slug: dict, aliases: dict):
    config = SPORT_CONFIGS[sport]
    unmatched = set()

    output_dir = DATA_REPO / f"output/{sport}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- current-season ratings ---
    current_ratings_path = get_current_ratings_path(sport, season, config)
    current_by_slug = {}
    if current_ratings_path and current_ratings_path.exists():
        current_ratings = load_json(current_ratings_path)
        for team in current_ratings["teams"]:
            slug = resolve_slug(team["school"], name_to_slug, aliases, unmatched)
            if slug:
                current_by_slug[slug] = team
    elif current_ratings_path is None:
        print(f"  [{sport}] WARNING: no ratings_repo_style configured, skipping current_season field")
    else:
        print(f"  [{sport}] WARNING: current-season ratings not found at {current_ratings_path}, skipping current_season field")

    # --- ratings + stats history ---
    ratings_seasons, stats_seasons = load_ratings_and_stats_history(sport, config)

    ratings_hist_by_slug = {}
    for s in ratings_seasons:
        for team in s["teams"]:
            slug = resolve_slug(team["school"], name_to_slug, aliases, unmatched)
            if slug:
                ratings_hist_by_slug.setdefault(slug, []).append(team)

    stats_hist_by_slug = {}
    for s in stats_seasons:
        year = s.get("year")
        for team in s["teams"]:
            slug = resolve_slug(team["school"], name_to_slug, aliases, unmatched)
            if slug:
                entry = dict(team)
                if year is not None:
                    entry["season"] = year
                stats_hist_by_slug.setdefault(slug, []).append(entry)

    # --- current-season schedule ---
    current_schedule_path = get_current_schedule_path(sport, season, config)
    schedule_by_slug = {}
    if current_schedule_path and current_schedule_path.exists():
        current_schedule_raw = load_json(current_schedule_path)
        for team_name, games in current_schedule_raw["teams"].items():
            slug = resolve_slug(team_name, name_to_slug, aliases, unmatched)
            if slug:
                schedule_by_slug[slug] = games
    elif current_schedule_path is None:
        print(f"  [{sport}] NOTE: no schedule scraper built for this sport yet, skipping current_schedule field")
    else:
        print(f"  [{sport}] WARNING: current-season schedule not found at {current_schedule_path}, skipping current_schedule field")

    # --- write merged per-team files ---
    all_slugs = set(current_by_slug) | set(ratings_hist_by_slug) | set(stats_hist_by_slug)
    for slug in all_slugs:
        merged = {
            "slug": slug,
            "sport": sport,
            "school": schools_json["schools"].get(slug, {}).get("name"),
            "current_season": current_by_slug.get(slug),
            "ratings_history": ratings_hist_by_slug.get(slug, []),
            "stats_history": stats_hist_by_slug.get(slug, []),
            "current_schedule": schedule_by_slug.get(slug, []),
        }
        with open(output_dir / f"{slug}.json", "w", encoding="utf-8") as f:
            json.dump(merged, f, separators=(",", ":"))

    if unmatched:
        print(f"  [{sport}] WARNING: {len(unmatched)} unresolved names (add these to school_aliases.json):")
        for name in sorted(unmatched):
            print(f"      - {name}")
    print(f"  [{sport}] wrote {len(all_slugs)} team files")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=list(SPORT_CONFIGS.keys()) + ["all"])
    parser.add_argument("--season", type=int, required=True, help="Season start year, e.g. 2025 for 2025-26")
    parser.add_argument(
        "--print-ratings-repo",
        action="store_true",
        help="Print the current-season ratings repo name for this sport/season and exit "
             "(no other files needed). Used by CI to know what to 'git clone' before running "
             "the real build -- keeps repo-naming logic in one place instead of duplicating it in YAML.",
    )
    args = parser.parse_args()

    if args.print_ratings_repo:
        if args.sport == "all":
            parser.error("--print-ratings-repo requires a single --sport, not 'all'")
        repo_name = resolve_ratings_repo_name(args.sport, args.season, SPORT_CONFIGS[args.sport])
        print(repo_name if repo_name else "")
        return

    schools_json = load_json(SCHOOLS_JSON_PATH)
    name_to_slug = build_name_to_slug_map(schools_json)
    aliases = load_aliases()

    sports_to_run = list(SPORT_CONFIGS.keys()) if args.sport == "all" else [args.sport]

    for sport in sports_to_run:
        print(f"Processing {sport}...")
        process_sport(sport, args.season, schools_json, name_to_slug, aliases)


if __name__ == "__main__":
    main()
