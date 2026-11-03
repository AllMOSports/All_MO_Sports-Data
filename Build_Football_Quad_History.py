"""
Build_Football_Quad_History.py

Historical Quad 1-4 records for Missouri football, 2010-2025, using the same
rules as the live 2026 quad records (build_quad_records_football_2026.py):

- For each season and class (1-6), the 75th / 50th / 25th percentile of that
  class's OVR ratings are the quad cutoffs.
- Every opponent is judged against the cutoffs of the TEAM'S OWN class.
      opp OVR >= 75th pct -> Q1,  >= 50th -> Q2,  >= 25th -> Q3,  else Q4
  A rating exactly on a cutoff goes to the higher quad.
- Ratings are each season's FINAL ratings.
- Regular season + postseason games count.
- Opponents with no rating that season (out-of-state teams) are not counted.

Also tags each team-season with its state tournament finish (Champion,
Runner-up, Final Four) so you can see what quad resumes the best teams had.

Inputs (this repo):
  output/football_schedule_{YEAR}.json
  output/mshsaa_historical_records/football/Football_Ratings_History_2010-2025.json
  output/state_tournament_history.json

Outputs (output/mshsaa_historical_records/football/):
  Football_Quad_Records_History_2010-2025.json   every team-season + cutoffs
  Football_Quad_Records_History_2010-2025.csv    same, one row per team-season
  Football_Quad_History_Summary_2010-2025.csv    profile of champions / runners-up /
                                                 Final Four teams vs. everyone else
  Football_Quad_History_Summary_2010-2025.json   same summary, plus leaderboards

Standard library only.
"""

import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

FIRST_YEAR, LAST_YEAR = 2010, 2025

SCHEDULE_PATH = "output/football_schedule_{year}.json"
RATINGS_PATH = "output/mshsaa_historical_records/football/Football_Ratings_History_2010-2025.json"
TOURNAMENT_PATH = "output/state_tournament_history.json"

OUT_DIR = "output/mshsaa_historical_records/football"
OUT_JSON = os.path.join(OUT_DIR, f"Football_Quad_Records_History_{FIRST_YEAR}-{LAST_YEAR}.json")
OUT_CSV = os.path.join(OUT_DIR, f"Football_Quad_Records_History_{FIRST_YEAR}-{LAST_YEAR}.csv")
OUT_SUMMARY_CSV = os.path.join(OUT_DIR, f"Football_Quad_History_Summary_{FIRST_YEAR}-{LAST_YEAR}.csv")
OUT_SUMMARY_JSON = os.path.join(OUT_DIR, f"Football_Quad_History_Summary_{FIRST_YEAR}-{LAST_YEAR}.json")

# Names in state_tournament_history.json that differ from the ratings file.
TOURNAMENT_ALIASES = {
    "DeSmet": "De Smet Jesuit",
    "St. Mary's St. Louis": "St. Mary's South Side",
}

QUADS = ("q1", "q2", "q3", "q4")
FINISH_ORDER = ["Champion", "Runner-up", "Final Four"]


# ---------------------------------------------------------------- helpers
def percentile(sorted_vals, pct):
    """Linear-interpolated percentile (same method as numpy's default)."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def class_cutoffs(teams):
    by_class = defaultdict(list)
    for t in teams:
        if t.get("classification") is not None and t.get("ovr_rating") is not None:
            by_class[t["classification"]].append(float(t["ovr_rating"]))
    out = {}
    for cls, vals in by_class.items():
        vals.sort()
        out[cls] = {"q1": round(percentile(vals, 75), 2),
                    "q2": round(percentile(vals, 50), 2),
                    "q3": round(percentile(vals, 25), 2),
                    "team_count": len(vals)}
    return out


def quad_for(opp_ovr, cut):
    if opp_ovr >= cut["q1"]:
        return "q1"
    if opp_ovr >= cut["q2"]:
        return "q2"
    if opp_ovr >= cut["q3"]:
        return "q3"
    return "q4"


def load_finishes():
    """{(year, school): "Champion" | "Runner-up" | "Final Four"}"""
    with open(TOURNAMENT_PATH, encoding="utf-8") as f:
        teams = json.load(f)["teams"]
    finishes = {}
    keys = (("final_four_appearances", "Final Four"),
            ("runner_up_finishes", "Runner-up"),
            ("state_championships", "Champion"))   # later keys win
    for school, sports in teams.items():
        fb = sports.get("football")
        if not fb:
            continue
        name = TOURNAMENT_ALIASES.get(school, school)
        for key, label in keys:
            for entry in fb.get(key, {}).get("years", []):
                year = int(str(entry["year"])[:4])   # "2017-2018" -> 2017 season
                if FIRST_YEAR <= year <= LAST_YEAR:
                    finishes[(year, name)] = label
    return finishes


# ---------------------------------------------------------------- build
def build_season(year, season_ratings, finishes):
    teams = season_ratings["teams"]
    rating = {t["school"]: t for t in teams}
    cutoffs = class_cutoffs(teams)

    # class rank by OVR
    class_rank = {}
    by_class = defaultdict(list)
    for t in teams:
        if t.get("classification") is not None:
            by_class[t["classification"]].append(t)
    for rows in by_class.values():
        for i, t in enumerate(sorted(rows, key=lambda x: -x["ovr_rating"]), 1):
            class_rank[t["school"]] = i

    with open(SCHEDULE_PATH.format(year=year), encoding="utf-8") as f:
        schedule = json.load(f)["teams"]

    out = []
    skipped = defaultdict(int)
    for school, games in schedule.items():
        t = rating.get(school)
        if t is None or t.get("classification") is None:
            skipped["team_unrated_or_unclassified"] += 1
            continue
        cut = cutoffs[t["classification"]]
        rec = {q: [0, 0] for q in QUADS}
        full = [0, 0]
        for g in games:
            if g.get("result") not in ("W", "L"):
                skipped["no_result_or_tie"] += 1
                continue
            won = g["result"] == "W"
            full[0 if won else 1] += 1
            opp = rating.get(g["opponent"])
            if opp is None:
                skipped["unrated_opponent"] += 1   # out-of-state, etc.
                continue
            q = quad_for(float(opp["ovr_rating"]), cut)
            rec[q][0 if won else 1] += 1

        row = {
            "year": year,
            "school": school,
            "classification": t["classification"],
            "district": t.get("district"),
            "ovr_rating": t["ovr_rating"],
            "ovr_rank": t.get("ovr_rank"),
            "class_rank": class_rank.get(school),
            "record": f"{full[0]}-{full[1]}",
            "finish": finishes.get((year, school), ""),
        }
        for q in QUADS:
            row[q] = f"{rec[q][0]}-{rec[q][1]}"
            row[f"{q}_wins"], row[f"{q}_losses"] = rec[q]
        out.append(row)

    out.sort(key=lambda r: (r["classification"], r["class_rank"] or 999))
    return {"year": year, "cutoffs": {str(k): v for k, v in sorted(cutoffs.items())},
            "teams": out}, skipped


# ---------------------------------------------------------------- summary
def profile(rows):
    n = len(rows)
    if not n:
        return None
    avg = lambda k: round(sum(r[k] for r in rows) / n, 2)
    pct = lambda cond: round(100.0 * sum(1 for r in rows if cond(r)) / n, 1)
    q1w, q1l = sum(r["q1_wins"] for r in rows), sum(r["q1_losses"] for r in rows)
    return {
        "teams": n,
        "avg_q1_wins": avg("q1_wins"),
        "avg_q1_losses": avg("q1_losses"),
        "q1_win_pct": round(100.0 * q1w / (q1w + q1l), 1) if q1w + q1l else None,
        "pct_with_q1_win": pct(lambda r: r["q1_wins"] > 0),
        "pct_unbeaten_in_q1": pct(lambda r: r["q1_wins"] + r["q1_losses"] > 0 and r["q1_losses"] == 0),
        "pct_with_q2_loss": pct(lambda r: r["q2_losses"] > 0),
        "pct_with_q3_q4_loss": pct(lambda r: r["q3_losses"] + r["q4_losses"] > 0),
        "pct_ranked_1_in_class": pct(lambda r: r["class_rank"] == 1),
    }


def build_summary(all_rows):
    groups = {
        "Champion": [r for r in all_rows if r["finish"] == "Champion"],
        "Runner-up": [r for r in all_rows if r["finish"] == "Runner-up"],
        "Final Four (lost in semis)": [r for r in all_rows if r["finish"] == "Final Four"],
        "All Final Four teams": [r for r in all_rows if r["finish"]],
        "Everyone else": [r for r in all_rows if not r["finish"]],
    }
    table = []
    for scope in ["All classes"] + [f"Class {c}" for c in range(1, 7)]:
        for name, rows in groups.items():
            if scope != "All classes":
                c = int(scope.split()[-1])
                rows = [r for r in rows if r["classification"] == c]
            p = profile(rows)
            if p:
                table.append({"scope": scope, "group": name, **p})

    def brief(r):
        return {k: r[k] for k in ("year", "school", "classification", "record",
                                  "q1", "q2", "q3", "q4", "class_rank", "finish")}

    played_q1 = [r for r in all_rows if r["q1_wins"] + r["q1_losses"] > 0]
    leaders = {
        "most_q1_wins": [brief(r) for r in sorted(
            all_rows, key=lambda r: (-r["q1_wins"], r["q1_losses"]))[:25]],
        "unbeaten_in_q1_most_wins": [brief(r) for r in sorted(
            [r for r in played_q1 if r["q1_losses"] == 0],
            key=lambda r: -r["q1_wins"])[:25]],
        "champions_with_q3_q4_loss": [brief(r) for r in all_rows
                                      if r["finish"] == "Champion"
                                      and r["q3_losses"] + r["q4_losses"] > 0],
        "champions_with_no_q1_win": [brief(r) for r in all_rows
                                     if r["finish"] == "Champion" and r["q1_wins"] == 0],
    }
    return table, leaders


# ---------------------------------------------------------------- main
def main():
    with open(RATINGS_PATH, encoding="utf-8") as f:
        ratings = {s["year"]: s for s in json.load(f)["seasons"]}
    finishes = load_finishes()

    seasons, all_rows = [], []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        if year not in ratings or not os.path.exists(SCHEDULE_PATH.format(year=year)):
            print(f"{year}: missing ratings or schedule -- skipped")
            continue
        season, skipped = build_season(year, ratings[year], finishes)
        seasons.append(season)
        all_rows.extend(season["teams"])
        tagged = sum(1 for r in season["teams"] if r["finish"])
        print(f"{year}: {len(season['teams'])} teams, {tagged} Final Four teams tagged, "
              f"skipped {dict(skipped)}")

    missing = sorted(k for k in finishes if k[1] not in
                     {r["school"] for r in all_rows if r["year"] == k[0]})
    if missing:
        print(f"  ! tournament finishes with no matching team-season: {missing}")

    now = datetime.now(ZoneInfo("America/Chicago")).strftime("%B %d, %Y at %I:%M %p")
    method = ("Quads use the 75th/50th/25th percentile final OVR rating of the team's own "
              "class that season. Regular season + postseason. Opponents with no rating "
              "that season (out-of-state) not counted.")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"sport": "football", "years_covered": [FIRST_YEAR, LAST_YEAR],
                   "generated": now, "method": method, "seasons": seasons}, f, indent=1)

    cols = ["year", "classification", "district", "class_rank", "ovr_rank", "school",
            "ovr_rating", "record", "q1", "q2", "q3", "q4", "finish"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    table, leaders = build_summary(all_rows)
    with open(OUT_SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
        w.writeheader()
        w.writerows(table)
    with open(OUT_SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump({"generated": now, "method": method, "profiles": table,
                   "leaders": leaders}, f, indent=1)

    print("\nAll classes:")
    for row in table:
        if row["scope"] == "All classes":
            print(f"  {row['group']:<28} n={row['teams']:<5} Q1 W-L avg "
                  f"{row['avg_q1_wins']}-{row['avg_q1_losses']}  "
                  f"unbeaten in Q1 {row['pct_unbeaten_in_q1']}%  "
                  f"Q2 loss {row['pct_with_q2_loss']}%  "
                  f"Q3/Q4 loss {row['pct_with_q3_q4_loss']}%  "
                  f"#1 in class {row['pct_ranked_1_in_class']}%")
    print(f"\nWrote {OUT_JSON}\n      {OUT_CSV}\n      {OUT_SUMMARY_CSV}\n      {OUT_SUMMARY_JSON}")


if __name__ == "__main__":
    main()
