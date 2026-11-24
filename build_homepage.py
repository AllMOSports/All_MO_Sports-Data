#!/usr/bin/env python3
"""
All MO Sports - homepage data builder
=====================================

Writes output/homepage/now.json, the one file the homepage snippet reads for
everything that changes with the calendar:

  right_now         which module the "Right now in Missouri" spot shows
  playoffs          football title odds per class (during the state series)
  games_tonight     Games to Watch Tonight: today's best games, all sports
  games_last_night  yesterday's best games with final scores vs our projection
  up_next           sports starting soon (the "Up next" card)

It reuses the newsletter's loaders and the sports listed in
newsletter_config.yaml, so adding basketball there adds it here too.
Homepage-only settings live in the `homepage:` block at the bottom of that file.

Usage:
  python build_homepage.py                  # today (America/Chicago)
  python build_homepage.py --day 2026-12-04 # pretend it's another day (testing)

Requires: Python 3.9+, PyYAML
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

import yaml

from build_weekly_newsletter import fetch_json, load_games, is_played, predict_football

HERE = os.path.dirname(os.path.abspath(__file__))

GENDER = {
    "football": "Boys", "boys_soccer": "Boys", "boys_basketball": "Boys", "baseball": "Boys",
    "girls_volleyball": "Girls", "fall_softball": "Girls", "girls_basketball": "Girls",
    "girls_soccer": "Girls", "spring_softball": "Girls",
}

DEFAULTS = {
    "out_file": "output/homepage/now.json",
    "games_max": 8,                 # rows in Games to Watch Tonight
    "free_projections": None,       # None = every projection shown; a number = only the top N (paid tier later)
    "playoff_window_days": 7,       # show football odds when a state-series game is this close
    "playoff_teams_per_class": 4,
    "override": None,               # {module, title, text, link, link_label, until: YYYY-MM-DD}
    "preview_url": "",              # e.g. https://allmosports.com/matchup-preview/?id={id} (football previews)
    "cup_url": "https://allmosports.com/all-mo-sports-cup/",
    "cup_released": False,
}


# ----------------------------------------------------------------------
# Names -> school slugs (for links and logos)
# ----------------------------------------------------------------------

def build_slug_lookup(schools_path, aliases_path):
    look = {}
    with open(schools_path, encoding="utf-8") as f:
        schools = json.load(f)["schools"]
    for slug, s in schools.items():
        for n in (s.get("name"), s.get("mshsaa_name")):
            if n:
                look[n.lower()] = slug
    if os.path.exists(aliases_path):
        with open(aliases_path, encoding="utf-8") as f:
            for n, slug in json.load(f).items():
                look.setdefault(n.lower(), slug)
    return look


def write_school_index(schools_path, out_path):
    """Small search list for the homepage box: [[slug, name, other name], ...].
    schools.json is over 1 MB; this is about 30 KB."""
    with open(schools_path, encoding="utf-8") as f:
        schools = json.load(f)["schools"]
    rows = []
    for slug, s in sorted(schools.items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
        name = s.get("name") or slug
        other = s.get("mshsaa_name") if s.get("mshsaa_name") not in (None, name) else ""
        rows.append([slug, name, other])
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, separators=(",", ":"))


def slug_for(name, look):
    if not name:
        return None
    key = name.lower()
    if key in look:
        return look[key]
    if " with " in key:                         # co-op: credit the host school
        host = key.split(" with ")[0].strip()
        if host in look:
            return look[host]
    if key.endswith(")") and " (" in key:       # "Name (Town)" variants
        base = key.rsplit(" (", 1)[0]
        if base in look:
            return look[base]
    return None


# ----------------------------------------------------------------------
# Ratings helpers
# ----------------------------------------------------------------------

def class_ranks(teams):
    """{school: (rank_in_class, class_size)} by OVR within classification."""
    by_class = defaultdict(list)
    for t in teams:
        by_class[t.get("classification")].append(t)
    out = {}
    for c, lst in by_class.items():
        lst = sorted(lst, key=lambda x: -x["ovr_rating"])
        for i, t in enumerate(lst):
            out[t["school"]] = (i + 1, len(lst))
    return out


def project(sport, a, b, league_average, settings_cfg):
    """Projected score (a, b) with the Matchup Predictor's formula, or None
    when the sport has no offense/defense ratings (volleyball)."""
    if a.get("off_rating") is None or b.get("off_rating") is None:
        return None
    if a.get("def_rating") is None or b.get("def_rating") is None:
        return None
    if sport == "football":
        return list(predict_football(a, b, league_average or settings_cfg.get("league_average", 26)))
    la = league_average or 0
    sa = max(0, a["off_rating"] - b["def_rating"] + la)
    sb = max(0, b["off_rating"] - a["def_rating"] + la)
    ra, rb = int(round(sa)), int(round(sb))
    if ra == rb:                                # no projected ties: nudge the favorite
        if sa >= sb:
            ra += 1
        else:
            rb += 1
    return [ra, rb]


def stdev(values):
    if len(values) < 2:
        return 1.0
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1)) or 1.0


def watch_score(ra, rb, ovr_gap, spread, same_district, postseason):
    """0-1+. Quality (both teams high in their own class) counts most, then how
    close the game should be. A district game or a playoff game gets a bonus."""
    pa = (ra[0] - 1) / max(ra[1] - 1, 1)        # 0 = best in class, 1 = last
    pb = (rb[0] - 1) / max(rb[1] - 1, 1)
    quality = (1 - (0.35 * min(pa, pb) + 0.65 * max(pa, pb))) ** 2
    closeness = 1 / (1 + (ovr_gap / (0.35 * spread)) ** 2)
    score = 0.8 * quality + 0.2 * closeness
    if same_district:
        score += 0.05
    if postseason:
        score += 0.05
    return round(score, 4)


# ----------------------------------------------------------------------
# Games to Watch Tonight
# ----------------------------------------------------------------------

def sport_games(sport, cfg, day, yesterday, look, rcfg):
    """Scored games for `day` and `yesterday` in one sport."""
    ratings = fetch_json(cfg["ratings_url"])
    games = load_games(sport, cfg)
    teams = ratings["teams"]
    by_name = {t["school"]: t for t in teams}
    ranks = class_ranks(teams)
    spread = stdev([t["ovr_rating"] for t in teams])
    la = ratings.get("league_average") or cfg.get("league_average")
    postseason_start = str((rcfg.get("sports", {}).get(sport) or {}).get("postseason_start") or "")
    label = (rcfg.get("sports", {}).get(sport) or {}).get("label") or cfg.get("label") or sport

    rows = {day: [], yesterday: []}
    seen = set()
    for g in games:
        if g["date"] not in rows:
            continue
        a, b = by_name.get(g["team1"]), by_name.get(g["team2"])
        if a is None or b is None:
            continue                            # an unrated team (out of state, not classified)
        key = (g["date"],) + tuple(sorted([g["team1"], g["team2"]]))
        if key in seen:
            continue
        seen.add(key)
        ra, rb = ranks[a["school"]], ranks[b["school"]]
        # Better-rated team first
        if a["ovr_rating"] < b["ovr_rating"]:
            a, b, ra, rb = b, a, rb, ra
            s1, s2 = g["score2"], g["score1"]
        else:
            s1, s2 = g["score1"], g["score2"]
        same_district = bool(a.get("district")) and a.get("district") == b.get("district") \
            and a.get("classification") == b.get("classification")
        post = bool(postseason_start) and g["date"] >= postseason_start
        scores_on = sport == "football" or bool((rcfg.get("sports", {}).get(sport) or {}).get("project_scores"))
        proj = project(sport, a, b, la, cfg) if scores_on else None
        row = {
            "sport": sport, "sport_label": label, "gender": GENDER.get(sport, ""),
            "date": g["date"],
            "teams": [
                {"name": a["school"], "slug": slug_for(a["school"], look), "class": a.get("classification"),
                 "class_rank": ra[0], "class_size": ra[1]},
                {"name": b["school"], "slug": slug_for(b["school"], look), "class": b.get("classification"),
                 "class_rank": rb[0], "class_size": rb[1]},
            ],
            "favored": 0,
            "proj": proj,
            "district_game": same_district,
            "postseason": post,
            "score": watch_score(ra, rb, abs(a["ovr_rating"] - b["ovr_rating"]), spread, same_district, post),
        }
        if is_played(g):
            row["final"] = [s1, s2]
            if s1 != s2:
                row["correct"] = s1 > s2        # the favored team (listed first) won
        rows[g["date"]].append(row)
    return rows, ratings.get("last_updated")


def pick_top(rows, n, free_n):
    rows = sorted(rows, key=lambda r: -r["score"])[:n]
    for i, r in enumerate(rows):
        if free_n is not None and i >= free_n:
            r["proj"] = None
            r["locked"] = True
    return rows


def game_id(r):
    return (r["sport"], r["date"]) + tuple(sorted(t["name"] for t in r["teams"]))


def last_night_from_previous(path, yesterday, last_night_rows):
    """Yesterday's list exactly as the homepage showed it (pre-game projections),
    with final scores filled in. None if yesterday's file isn't available."""
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        return None
    if prev.get("day") != yesterday:
        prev_rows = prev.get("games_last_night") if prev.get("day") == (
            dt.date.fromisoformat(yesterday) + dt.timedelta(days=1)).isoformat() else None
        return prev_rows                      # re-run on the same day: keep what we had
    finals = {game_id(r): r for r in last_night_rows if "final" in r}
    out = []
    for r in prev.get("games_tonight", []):
        done = finals.get(game_id(r))
        if done is None:
            continue                            # postponed, or no score reported yet
        r = dict(r)
        if [t["name"] for t in r["teams"]] == [t["name"] for t in done["teams"]]:
            r["final"] = done["final"]
        else:
            r["final"] = list(reversed(done["final"]))
        a, b = r["final"]
        r.pop("correct", None)
        if a != b:
            r["correct"] = a > b               # the favored team is listed first
        out.append(r)
    return out


# ----------------------------------------------------------------------
# Football playoffs
# ----------------------------------------------------------------------

def football_playoffs(rcfg, day, hp, look):
    fb = rcfg.get("football") or {}
    rounds = fb.get("rounds") or {}
    url = fb.get("playoff_odds_url")
    if not url or not rounds:
        return None
    d = dt.date.fromisoformat(day)
    window = dt.timedelta(days=hp["playoff_window_days"])
    upcoming = None
    for friday, name in sorted(rounds.items()):
        f = dt.date.fromisoformat(str(friday))
        if "State" not in name:
            continue
        # A round is "on" from the day after the previous Saturday through its own Saturday
        if f - window <= d <= f + dt.timedelta(days=1):
            upcoming = (str(friday), name)
            break
    if upcoming is None:
        return None
    odds = fetch_json(url)
    classes = []
    for key in sorted(odds.get("classes", {})):
        c = odds["classes"][key]
        alive = [t for t in c.get("teams", []) if (t.get("state_title") or 0) > 0 or (t.get("state_final") or 0) > 0]
        alive.sort(key=lambda t: -(t.get("state_title") or 0))
        classes.append({
            "class": c.get("class"),
            "teams": [{"name": t["team"], "slug": slug_for(t["team"], look), "record": t.get("record"),
                       "state_final": t.get("state_final_display"), "state_title": t.get("state_title_display"),
                       "state_title_raw": t.get("state_title")}
                      for t in alive[: hp["playoff_teams_per_class"]]],
            "alive": len(alive),
        })
    return {"round": upcoming[1], "date": upcoming[0], "simulations": odds.get("simulations"),
            "updated": odds.get("generated"), "classes": classes}


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="YYYY-MM-DD (default: today in Missouri)")
    ap.add_argument("--config", default=os.path.join(HERE, "newsletter_config.yaml"))
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    hp = dict(DEFAULTS)
    hp.update(settings.get("homepage") or {})
    rcfg = settings.get("school_reports") or {}

    day = args.day or dt.datetime.now().strftime("%Y-%m-%d")   # workflow runs with TZ=America/Chicago
    yesterday = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    look = build_slug_lookup(os.path.join(HERE, rcfg.get("schools_file", "output/schools.json")),
                             os.path.join(HERE, rcfg.get("aliases_file", "school_aliases.json")))

    tonight, last_night, updated = [], [], {}
    for sport, cfg in (settings.get("sports") or {}).items():
        if not cfg.get("enabled"):
            continue
        try:
            rows, stamp = sport_games(sport, cfg, day, yesterday, look, rcfg)
        except Exception as e:                      # one sport failing never blanks the homepage
            print(f"[{sport}] skipped: {e}", file=sys.stderr)
            continue
        tonight += rows[day]
        last_night += rows[yesterday]
        updated[sport] = stamp
        print(f"[{sport}] {len(rows[day])} rated games today, {len(rows[yesterday])} yesterday")

    games_tonight = pick_top(tonight, hp["games_max"], hp["free_projections"])
    games_last_night = last_night_from_previous(os.path.join(HERE, hp["out_file"]), yesterday, last_night)
    if games_last_night is None:
        games_last_night = pick_top([r for r in last_night if "final" in r], hp["games_max"], None)

    playoffs = None
    try:
        playoffs = football_playoffs(rcfg, day, hp, look)
    except Exception as e:
        print(f"[playoffs] skipped: {e}", file=sys.stderr)

    # Up next: sports starting within preseason_days
    up_next = []
    d = dt.date.fromisoformat(day)
    for sport, sc in (rcfg.get("sports") or {}).items():
        start = sc.get("start")
        if not start:
            continue
        start = start if isinstance(start, dt.date) else dt.date.fromisoformat(str(start))
        days = (start - d).days
        if 0 < days <= rcfg.get("preseason_days", 21):
            up_next.append({"sport": sport, "label": sc.get("label"), "start": start.isoformat(), "days": days})

    # Which module leads "Right now"
    override = hp.get("override")
    if override and str(override.get("until", "9999-12-31")) >= day:
        right_now = dict(override)
        right_now.setdefault("module", "custom")
    elif playoffs:
        right_now = {"module": "playoffs"}
    elif games_tonight:
        right_now = {"module": "games_tonight"}
    elif games_last_night:
        right_now = {"module": "games_last_night"}
    else:
        right_now = {"module": "cup"}

    out = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "day": day,
        "right_now": right_now,
        "playoffs": playoffs,
        "games_tonight": games_tonight,
        "games_last_night": games_last_night,
        "up_next": up_next,
        "cup": {"released": bool(hp["cup_released"]), "url": hp["cup_url"]},
        "preview_url": hp["preview_url"],
        "ratings_updated": updated,
    }
    path = os.path.join(HERE, hp["out_file"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_school_index(os.path.join(HERE, rcfg.get("schools_file", "output/schools.json")),
                       os.path.join(os.path.dirname(path), "school_index.json"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"Wrote {path}: right now = {right_now['module']}, "
          f"{len(games_tonight)} games tonight, {len(games_last_night)} last night")


if __name__ == "__main__":
    main()
