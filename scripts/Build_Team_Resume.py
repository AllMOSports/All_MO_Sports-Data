"""
Build_Team_Resume.py

Team Resume data for the All MO Sports "Team Resume" page.

Per team (only teams with an MSHSAA classification AND district):
  - Overall record        every played game, incl. out-of-state + forfeits
  - WAC                   Wins Above Contender (the resume score, a total)
  - Q1-Q4 records         same quads as build_quad_records_football_2026.py
  - SOS + SOS rank        avg opponent OVR, ranked within the team's class
  - Projected district seed

Wins Above Contender (WAC)
--------------------------
The "Contender" benchmark for a class is a team rated at that class's
75th-percentile OVR (the Q1 cutoff). For every counted game:

    p   = chance the Contender beats this opponent
        = NormalCDF( (contender_ovr - opponent_ovr) / SIGMA )
    WAC += result - p          (result: win 1, tie 0.5, loss 0)

(OVR = OFF + DEF, so the predicted margin between two teams is just the OVR
difference -- the same math as the Matchup Predictor.) Opponents are judged
against the TEAM'S OWN class benchmark, same as the quads. WAC is a season
TOTAL. Margin never enters it.

Projected district seed
-----------------------
1. Inside each district, WAC and OVR are turned into percentiles
   (best team = 1.0, worst = 0.0), then
       seed_score = 0.60 * WAC pct + 0.40 * OVR pct
   Ties broken by WAC, then OVR.
2. Head-to-head jumps: working top-down, if the lower of two BACK-TO-BACK
   teams has a winning head-to-head record (> .500) against the team right
   above it, they swap. Passes repeat until a full pass makes no swaps.
   A 1-1 split never swaps. This always finishes: every swap removes one
   "team sits above a team that owns it head-to-head" pair and never adds one.

What counts (WAC, quads, SOS, head-to-head)
-------------------------------------------
- Played regular-season AND postseason games.
- Both teams MSHSAA-classified and present in the ratings file.
  Out-of-state opponents and Missouri programs outside the MSHSAA postseason
  are NOT counted (they still count in the overall record).
- Forfeits are not counted (COUNT_FORFEITS), unplayed games skipped.

Usage (from the All_MO_Sports-Data repo root):
    python scripts/Build_Team_Resume.py                 # all sports in SPORTS
    python scripts/Build_Team_Resume.py football        # one or more sport keys
Sources are read from GitHub raw URLs. Set TEAM_RESUME_LOCAL_ROOT to a folder
holding the sibling repos to read from disk instead.

Outputs: output/team_resume/team_resume_{sport}_{season}.json / .csv
Standard library only.
"""

import csv
import json
import math
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

RAW = "https://raw.githubusercontent.com/AllMOSports/{repo}/main/{path}"
LOCAL = os.environ.get("TEAM_RESUME_LOCAL_ROOT", "")
OUT_DIR = "output/team_resume"

COUNT_FORFEITS = False
CONTENDER_PCT = 75        # benchmark = this percentile OVR of the team's class
WAC_WEIGHT = 0.60         # seed_score blend
OVR_WEIGHT = 0.40

# key, label, season label, repo, ratings file, games file, sigma
# sigma = SD of (actual margin - predicted margin). In-sample residual SD is
# ~13.3 football / ~11.4 basketball; these are padded for out-of-sample use.
SPORTS = [
    ("football", "Football", "2026",
     "football-ratings-2026", "football_ratings_2026.json",
     "football_games_2026_all.json", 15.0),
    ("boys_basketball", "Boys Basketball", "2026-27",
     "Boys_Basketball_Ratings_2026-2027", "boys_basketball_ratings_2026.json",
     "boys_basketball_games_2026_all.json", 12.5),
    ("girls_basketball", "Girls Basketball", "2026-27",
     "Girls_Basketball_Ratings_2026-2027", "girls_basketball_ratings_2026.json",
     "girls_basketball_games_2026_all.json", 12.5),
]

QUADS = ("q1", "q2", "q3", "q4")


# ---------------------------------------------------------------- loading --

def load(repo, path):
    if LOCAL:
        p = os.path.join(LOCAL, repo, path)
        if not os.path.exists(p):
            print(f"  missing {p}")
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    try:
        with urllib.request.urlopen(RAW.format(repo=repo, path=path), timeout=60) as r:
            return json.load(r)
    except Exception as e:
        print(f"  skip {repo}/{path}: {e}")
        return None


def load_slugs():
    """school name -> slug, from output/schools.json + school_aliases.json."""
    slug = {}
    try:
        with open("school_aliases.json", encoding="utf-8") as f:
            slug.update(dict(json.load(f)))
    except Exception:
        pass
    try:
        with open("output/schools.json", encoding="utf-8") as f:
            for s, v in json.load(f)["schools"].items():
                for k in ("mshsaa_name", "name"):
                    if v.get(k):
                        slug[v[k]] = s
    except Exception as e:
        print(f"  no slugs ({e})")
    return slug


# ------------------------------------------------------------------- math --

def percentile(sorted_vals, pct):
    """Linear-interpolated percentile (numpy default) -- same as the quad script."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def quad_for(opp_ovr, cut):
    if opp_ovr >= cut["q1"]:
        return "q1"
    if opp_ovr >= cut["q2"]:
        return "q2"
    if opp_ovr >= cut["q3"]:
        return "q3"
    return "q4"


def pct_scores(values):
    """{key: value} -> {key: percentile 0..1}, best value = 1.0. Ties share the
    average rank. A one-team district gets 1.0."""
    n = len(values)
    if n == 1:
        return {k: 1.0 for k in values}
    order = sorted(values, key=lambda k: -values[k])
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0          # 0-based
        for k in order[i:j + 1]:
            out[k] = (n - 1 - avg_rank) / (n - 1)
        i = j + 1
    return out


def rank_within(teams, key, groupkey, reverse=True):
    """Competition rank (1, 2, 2, 4) of key within each group; None values unranked."""
    groups = defaultdict(list)
    for t in teams:
        if t[key] is not None:
            groups[t[groupkey]].append(t)
    ranks = {}
    for g in groups.values():
        g.sort(key=lambda t: t[key], reverse=reverse)
        prev, prev_rank = None, 0
        for i, t in enumerate(g, 1):
            if t[key] != prev:
                prev_rank, prev = i, t[key]
            ranks[t["school"]] = prev_rank
    return ranks


# ----------------------------------------------------------- head-to-head --

def h2h_jumps(order, h2h):
    """Top-down adjacent passes until nothing changes.
    order: list of schools, best first. h2h[(a, b)] = [a_wins, b_wins] over
    counted games. Returns (new order, set of schools that jumped up)."""
    order = list(order)
    jumped = set()
    changed = True
    while changed:
        changed = False
        for i in range(len(order) - 1):
            upper, lower = order[i], order[i + 1]
            lw, uw = h2h.get((lower, upper), (0, 0))
            games = lw + uw
            if games and lw / games > 0.5:
                order[i], order[i + 1] = lower, upper
                jumped.add(lower)
                changed = True
    return order, jumped


# ------------------------------------------------------------------ build --

def build_sport(cfg, slugs):
    key, label, season, repo, ratings_path, games_path, sigma = cfg
    print(f"\n== {label} {season} ==")
    ratings = load(repo, ratings_path)
    games = load(repo, games_path)
    if not ratings or not games:
        print("  no data -- skipped")
        return None
    if isinstance(games, dict):
        games = games.get("games", [])

    # Teams in a classification AND district only.
    teams = [t for t in ratings.get("teams", [])
             if t.get("classification") is not None
             and t.get("district") is not None
             and t.get("ovr_rating") is not None]
    if not teams:
        print("  no rated teams yet -- skipped")
        return None
    by_school = {t["school"]: t for t in teams}

    # Class benchmarks / quad cutoffs (same percentiles as the quad script).
    by_class = defaultdict(list)
    for t in teams:
        by_class[t["classification"]].append(float(t["ovr_rating"]))
    cutoffs = {}
    for cls, vals in by_class.items():
        vals.sort()
        cutoffs[cls] = {
            "contender_ovr": round(percentile(vals, CONTENDER_PCT), 2),
            "q1": round(percentile(vals, 75), 2),
            "q2": round(percentile(vals, 50), 2),
            "q3": round(percentile(vals, 25), 2),
            "team_count": len(vals),
        }

    overall = {s: [0, 0, 0] for s in by_school}           # W, L, T (all games)
    quads = {s: {q: [0, 0] for q in QUADS} for s in by_school}
    wac = defaultdict(float)
    opp_ovrs = defaultdict(list)
    log = defaultdict(list)
    h2h = defaultdict(lambda: [0, 0])
    skipped = defaultdict(int)

    for g in games:
        s1, s2 = g.get("score1"), g.get("score2")
        if s1 is None or s2 is None:
            continue
        t1, t2 = g.get("team1"), g.get("team2")

        # Overall record: every played game (forfeits and out-of-state included).
        for team, ts, os_ in ((t1, s1, s2), (t2, s2, s1)):
            if team in overall:
                overall[team][0 if ts > os_ else 1 if ts < os_ else 2] += 1

        if g.get("forfeit") and not COUNT_FORFEITS:
            skipped["forfeit"] += 1
            continue
        r1, r2 = by_school.get(t1), by_school.get(t2)
        if r1 is None or r2 is None:
            skipped["unrated_or_unclassified_opponent"] += 1
            continue

        for team, opp, ts, os_ in ((r1, r2, s1, s2), (r2, r1, s2, s1)):
            school = team["school"]
            cut = cutoffs[team["classification"]]
            opp_ovr = float(opp["ovr_rating"])
            result = 1.0 if ts > os_ else 0.0 if ts < os_ else 0.5
            p = norm_cdf((cut["contender_ovr"] - opp_ovr) / sigma)
            wac[school] += result - p
            opp_ovrs[school].append(opp_ovr)
            q = quad_for(opp_ovr, cut)
            if result == 1.0:
                quads[school][q][0] += 1
            elif result == 0.0:
                quads[school][q][1] += 1
            if result != 0.5:
                h2h[(school, opp["school"])][0 if result == 1.0 else 1] += 1
            log[school].append({
                "date": g.get("date"),
                **({"week": g["week"]} if g.get("week") is not None else {}),
                "opponent": opp["school"],
                "opp_class": opp["classification"],
                "opp_ovr": opp["ovr_rating"],
                "quad": q.upper(),
                "result": "W" if result == 1.0 else "L" if result == 0.0 else "T",
                "score": f"{ts}-{os_}",
                "contender_win_prob": round(p, 3),
                "wac": round(result - p, 3) + 0.0,
            })

    # Per-team rows.
    rows = []
    for t in teams:
        s = t["school"]
        w, l, ti = overall[s]
        rows.append({
            "school": s,
            **({"slug": slugs[s]} if s in slugs else ({"slug": t["slug"]} if t.get("slug") else {})),
            "classification": t["classification"],
            "district": t["district"],
            "record": f"{w}-{l}" + (f"-{ti}" if ti else ""),
            "wins": w, "losses": l, "ties": ti,
            "wac": round(wac[s], 2) if log[s] else None,
            "ovr_rating": t["ovr_rating"],
            "ovr_rank": t.get("ovr_rank"),
            **{q: f"{quads[s][q][0]}-{quads[s][q][1]}" for q in QUADS},
            "sos": round(sum(opp_ovrs[s]) / len(opp_ovrs[s]), 2) if opp_ovrs[s] else None,
        })

    wac_rank = rank_within(rows, "wac", "classification")
    sos_rank = rank_within(rows, "sos", "classification")
    for r in rows:
        r["wac_rank_class"] = wac_rank.get(r["school"])
        r["sos_rank_class"] = sos_rank.get(r["school"])

    # Projected district seeds.
    districts = defaultdict(list)
    for r in rows:
        districts[(r["classification"], r["district"])].append(r)
    for members in districts.values():
        wac_vals = {r["school"]: (r["wac"] if r["wac"] is not None else -999.0) for r in members}
        ovr_vals = {r["school"]: float(r["ovr_rating"]) for r in members}
        wp, op = pct_scores(wac_vals), pct_scores(ovr_vals)
        for r in members:
            r["seed_score"] = round(WAC_WEIGHT * wp[r["school"]] + OVR_WEIGHT * op[r["school"]], 4)
        order = [r["school"] for r in sorted(
            members, key=lambda r: (-r["seed_score"], -wac_vals[r["school"]], -ovr_vals[r["school"]]))]
        final, jumped = h2h_jumps(order, h2h)
        pre = {s: i + 1 for i, s in enumerate(order)}
        for r in members:
            r["points_seed"] = pre[r["school"]]
            r["projected_seed"] = final.index(r["school"]) + 1
            r["h2h_jump"] = r["school"] in jumped and r["projected_seed"] < r["points_seed"]
            r["district_teams"] = len(members)

    for r in rows:
        r["games"] = sorted(log[r["school"]], key=lambda x: x["date"] or "")

    rows.sort(key=lambda r: (r["classification"], r["wac_rank_class"] or 9999, r["school"]))

    payload = {
        "sport": key,
        "sport_label": label,
        "season": season,
        "last_updated": datetime.now(ZoneInfo("America/Chicago")).strftime("%B %d, %Y at %I:%M %p"),
        "ratings_last_updated": ratings.get("last_updated"),
        "sigma": sigma,
        "contender_percentile": CONTENDER_PCT,
        "seed_weights": {"wac": WAC_WEIGHT, "ovr": OVR_WEIGHT},
        "method": (
            "WAC (Wins Above Contender): for each game, result minus the chance a "
            f"Contender (a team at the {CONTENDER_PCT}th-percentile OVR of the team's own class) "
            "would win that game; summed over the season. Quads use the 75/50/25th "
            "percentile OVR of the team's own class. SOS = average opponent OVR, ranked "
            "within class. Current ratings; regular season + postseason. Out-of-state / "
            "non-MSHSAA opponents and forfeits not counted (overall record includes them). "
            "Projected seed = 60% WAC + 40% OVR (district percentiles), then head-to-head jumps."
        ),
        "cutoffs": {str(k): v for k, v in sorted(cutoffs.items())},
        "teams": rows,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = f"team_resume_{key}_{season}"
    with open(os.path.join(OUT_DIR, stem + ".json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    cols = ["classification", "district", "projected_seed", "points_seed", "h2h_jump",
            "school", "record", "wac", "wac_rank_class", "q1", "q2", "q3", "q4",
            "sos", "sos_rank_class", "ovr_rating", "seed_score"]
    with open(os.path.join(OUT_DIR, stem + ".csv"), "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(sorted(rows, key=lambda r: (r["classification"], r["district"], r["projected_seed"])))

    counted = sum(len(v) for v in log.values()) // 2
    jumps = sum(1 for r in rows if r["h2h_jump"])
    print(f"  {len(rows)} teams, {counted} counted games, skipped {dict(skipped)}")
    print(f"  {jumps} head-to-head jump(s)")
    for cls, c in sorted(cutoffs.items()):
        top = next(r for r in rows if r["classification"] == cls)
        print(f"  Class {cls}: Contender OVR {c['contender_ovr']}  |  #1 WAC {top['school']} {top['wac']:+.2f}")
    return payload


def main():
    wanted = set(sys.argv[1:])
    slugs = load_slugs()
    for cfg in SPORTS:
        if wanted and cfg[0] not in wanted:
            continue
        build_sport(cfg, slugs)


if __name__ == "__main__":
    main()
