"""
compute_schedule_performance.py

Enriches football_schedule_2025.json (or any sport's equivalent schedule
file, same shape) with a predicted score and three performance deltas for
every game -- feeding the "Best OFF Performance" / "Best DEF Performance" /
"Best OVR Performance" tags on the Sport Detail Page.

-----------------------------------------------------------------------
MODEL -- read this before trusting the numbers
-----------------------------------------------------------------------
Predicted score uses each team's season-average points-for/points-against
(ppg/papg from schools.json), NOT the off_rating/def_rating power ratings.
That's a deliberate choice: ppg/papg are plain point-per-game averages, so
"predicted 27, actual 41" means exactly what it looks like. off_rating/
def_rating are regularized, shrinkage-adjusted power ratings on a
different internal scale -- reusing them here would require guessing at
scale conversions this script has no authoritative source for.

    predicted_team_score = (team_ppg + opponent_papg) / 2
    predicted_opp_score  = (opponent_ppg + team_papg) / 2

    off_delta = actual_team_score - predicted_team_score   (offense: how many
                more points than expected this team put up)
    def_delta = predicted_opp_score - actual_opp_score     (defense: how many
                fewer points than expected this team allowed)
    ovr_delta = off_delta + def_delta                       (combined swing
                versus expectation -- actual margin minus predicted margin)

CIRCULARITY CAVEAT: ppg/papg are season-END averages, computed from the very
games this script is "predicting" -- a team's one blowout is already baked
into the average used to predict that same blowout, which tends to flatten
deltas for exactly the games that should look most impressive. This is a
known, accepted limitation for a completed season used purely to give site
visitors a sense of "which games stood out" -- not a rigorous pre-game
prediction. If/when week-by-week rolling ratings exist, swap the ppg/papg
lookup below for a same-week snapshot and nothing else in this script needs
to change.
-----------------------------------------------------------------------

Usage:
    python compute_schedule_performance.py \
        --schedule football_schedule_2025.json \
        --schools schools.json \
        --sport football

    # Write to a different file instead of overwriting the schedule file:
    python compute_schedule_performance.py \
        --schedule football_schedule_2025.json \
        --schools schools.json \
        --sport football \
        --output football_schedule_2025_enriched.json

Expects schools.json in the same shape the Sport Detail Page already reads:
    { "schools": { "<slug>": { "name": "...", "sports": {
        "football": { "ppg": 27.4, "papg": 14.2, ... }, ... } } } }

A game is left with predicted_team_score/predicted_opp_score/off_delta/
def_delta/ovr_delta all null when either side's ppg/papg can't be resolved
(unrated/out-of-state opponent, or a team missing from schools.json) --
same graceful-degradation convention as the JS side of this feature.
"""

import argparse
import json
import re
import sys
from pathlib import Path


def normalize_name(name):
    """Same normalization as normalizeSchoolName() in the Sport Detail Page
    JS: lowercase, strip everything but letters/digits. Keeps this script's
    name matching consistent with the client-side lookups."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def round1(n):
    if n is None:
        return None
    return round(n * 10) / 10


def build_ppg_papg_lookup(schools_data, sport_key):
    """{ normalizedName: {"ppg": x, "papg": y} } for every school that has
    both fields for this sport. A school missing either field (or missing
    the sport entirely) is simply left out of the lookup -- games involving
    them just don't get predictions, they aren't dropped from the schedule."""
    lookup = {}
    schools = schools_data.get("schools", {})
    for slug, school in schools.items():
        name = school.get("name")
        if not name:
            continue
        sports = school.get("sports") or {}
        sport_data = sports.get(sport_key)
        if not sport_data:
            continue
        ppg = sport_data.get("ppg")
        papg = sport_data.get("papg")
        if ppg is None or papg is None:
            continue
        lookup[normalize_name(name)] = {"ppg": ppg, "papg": papg}
    return lookup


def compute_game_performance(team_stats, opp_stats, team_score, opp_score):
    """Returns a dict of the five new fields for one game, or all-None
    fields if either side's stats are missing or a score is missing."""
    empty = {
        "predicted_team_score": None,
        "predicted_opp_score": None,
        "off_delta": None,
        "def_delta": None,
        "ovr_delta": None,
    }
    if team_stats is None or opp_stats is None:
        return empty
    if team_score is None or opp_score is None:
        return empty

    predicted_team_score = round1((team_stats["ppg"] + opp_stats["papg"]) / 2)
    predicted_opp_score = round1((opp_stats["ppg"] + team_stats["papg"]) / 2)
    off_delta = round1(team_score - predicted_team_score)
    def_delta = round1(predicted_opp_score - opp_score)
    ovr_delta = round1(off_delta + def_delta)

    return {
        "predicted_team_score": predicted_team_score,
        "predicted_opp_score": predicted_opp_score,
        "off_delta": off_delta,
        "def_delta": def_delta,
        "ovr_delta": ovr_delta,
    }


def enrich_schedule(schedule_data, ppg_papg_lookup):
    teams = schedule_data.get("teams", {})
    total_games = 0
    predicted_games = 0

    for team_name, games in teams.items():
        team_stats = ppg_papg_lookup.get(normalize_name(team_name))
        for game in games:
            total_games += 1
            opp_stats = ppg_papg_lookup.get(normalize_name(game.get("opponent", "")))
            perf = compute_game_performance(
                team_stats, opp_stats, game.get("team_score"), game.get("opp_score")
            )
            game.update(perf)
            if perf["ovr_delta"] is not None:
                predicted_games += 1

    return total_games, predicted_games


def main():
    parser = argparse.ArgumentParser(
        description="Add predicted scores and OFF/DEF/OVR performance deltas to a schedule JSON."
    )
    parser.add_argument("--schedule", required=True, help="Path to football_schedule_<year>.json")
    parser.add_argument("--schools", required=True, help="Path to schools.json")
    parser.add_argument("--sport", required=True, help="sportKey to read from schools.json, e.g. football")
    parser.add_argument("--output", default=None, help="Output path (default: overwrite --schedule in place)")
    args = parser.parse_args()

    schedule_path = Path(args.schedule)
    schools_path = Path(args.schools)

    with open(schedule_path, encoding="utf-8") as f:
        schedule_data = json.load(f)
    with open(schools_path, encoding="utf-8") as f:
        schools_data = json.load(f)

    ppg_papg_lookup = build_ppg_papg_lookup(schools_data, args.sport)
    print(f"Built ppg/papg lookup for {len(ppg_papg_lookup)} teams (sport={args.sport}).")

    total_games, predicted_games = enrich_schedule(schedule_data, ppg_papg_lookup)
    print(f"Processed {total_games} team-game rows; {predicted_games} got a prediction "
          f"({total_games - predicted_games} skipped -- one or both sides missing ppg/papg).")

    out_path = Path(args.output) if args.output else schedule_path
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(schedule_data, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
