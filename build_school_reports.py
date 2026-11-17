#!/usr/bin/env python3
"""
All MO Sports: per-school weekly reports ("Follow your school" emails).

For every school someone follows, builds one section per sport:
  live       the season is going on: record, ranks, last week's games, next game
  wrapup     the season ended in the last few weeks: final record and rank
  preseason  the season starts soon: last season's record and rank
  offseason  nothing to say this week (left out of the email)

Then it assembles one email per subscriber from those sections, using each
subscriber's follows, for example:
  {"school": "st-vincent", "sports": "all"}                       everything
  {"school": "st-vincent", "sports": ["football", "girls_basketball"]}

"all" adds the school-wide extras (All MO Sports Cup, a school-wide big story).
A subscriber whose sports are all off-season this week gets no email.

Run:
  python build_school_reports.py --follows sample_follows.json
  python build_school_reports.py --school st-vincent --sports all
  python build_school_reports.py --school st-vincent --sports football,girls_basketball
  python build_school_reports.py --week-ending 2026-11-15 --follows follows.json

Writes newsletter_output/school_reports/<week-ending>/:
  emails/<id>.html     one ready-to-send HTML email per subscriber
  manifest.json        who gets what (subject, file, sports included), and who was skipped
  sections.json        every school-sport section that was built (for debugging)
  index.html           preview page listing every email
"""

import argparse
import datetime as dt
import html
import json
import os
import sys
from collections import Counter

import yaml

from build_weekly_newsletter import (fetch_json, load_games, is_played, records_from,
                                     fmt_rec, predict_football)

HERE = os.path.dirname(os.path.abspath(__file__))

SPORT_ORDER = ["football", "boys_soccer", "girls_volleyball", "fall_softball",
               "boys_basketball", "girls_basketball",
               "baseball", "girls_soccer", "spring_softball"]
STATE_ORDER = {"live": 0, "wrapup": 1, "preseason": 2, "offseason": 3, "none": 4}


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def resolve(path):
    """Config paths may be URLs or paths relative to this folder."""
    if not path or path.startswith("http"):
        return path
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def load(path, default=None):
    try:
        return fetch_json(resolve(path))
    except Exception as e:
        print(f"  (couldn't load {path}: {e})", file=sys.stderr)
        return default


def esc(s):
    return html.escape(str(s), quote=True)


def d(iso):
    return dt.date.fromisoformat(iso[:10])


def nice_date(iso, weekday=True):
    x = d(iso)
    return (x.strftime("%a, %b ") if weekday else x.strftime("%b ")) + str(x.day)


def ordinal_word(n):
    return {1: "", 2: "second-", 3: "third-"}.get(n, f"{n}th-")


def pct(p):
    if p is None:
        return None
    if p >= 0.995:
        return ">99%"
    if p < 0.01:
        return "<1%"
    return f"{round(p * 100)}%"


def teams_of(ratings):
    return ratings["teams"] if isinstance(ratings, dict) else ratings


# ----------------------------------------------------------------------
# Which team name(s) belong to a school in a sport
# ----------------------------------------------------------------------

def games_from_team_schedules(sched):
    """output/football_schedule_<year>.json ({teams: {name: [games]}}) -> one record per game."""
    seen, games = set(), []
    for team, rows in (sched.get("teams") or {}).items():
        for g in rows:
            key = (g["date"][:10],) + tuple(sorted([team, g["opponent"]]))
            if key in seen:
                continue
            seen.add(key)
            games.append({"date": g["date"][:10], "team1": team, "team2": g["opponent"],
                          "score1": g.get("team_score"), "score2": g.get("opp_score"),
                          "forfeit": bool(g.get("forfeit")), "overtime": bool(g.get("overtime"))})
    return games


def team_names_for(school, slug, names_in_sport, aliases):
    """Exact name, a co-op that includes the school ("Saxony Lutheran with
    St. Vincent"), or a name the alias file maps to this slug."""
    own = {school.get("name"), school.get("mshsaa_name")} - {None}
    found = []
    for n in names_in_sport:
        if n in own or aliases.get(n) == slug:
            found.append(n)
        elif " with " in n and any(p.strip() in own for p in n.split(" with ")):
            found.append(n)
    # Prefer the school's own team over a co-op if both exist.
    found.sort(key=lambda n: (n not in own, n))
    return found


def class_rank(teams, name):
    me = next((t for t in teams if t["school"] == name), None)
    if not me:
        return None, None, None
    same = sorted((t for t in teams if t.get("classification") == me.get("classification")),
                  key=lambda t: -(t.get("ovr_rating") or -999))
    rank = next(i + 1 for i, t in enumerate(same) if t["school"] == name)
    return me, rank, len(same)


# ----------------------------------------------------------------------
# Data that's shared by every school
# ----------------------------------------------------------------------

class Data:
    def __init__(self, settings, rcfg, out_dir, as_of, week_start, week_end):
        self.settings, self.rcfg = settings, rcfg
        self.out_dir, self.as_of = out_dir, as_of
        self.week_start, self.week_end = week_start, week_end
        print("Loading shared files...", flush=True)
        sch = load(rcfg["schools_file"], {})
        self.schools = sch.get("schools", sch)
        if isinstance(self.schools, list):
            self.schools = {s["slug"]: s for s in self.schools}
        self.aliases = load(rcfg.get("aliases_file"), {}) or {}
        self.cup = load(rcfg.get("cup_file"), None)
        self.previews = load(rcfg.get("football", {}).get("previews_url"), None)
        self.odds = load(rcfg.get("football", {}).get("playoff_odds_url"), None)
        self.fb_sched = load(rcfg.get("football", {}).get("team_schedule_file"), None)
        self._live, self._hist, self._official = {}, {}, {}

    # --- live sports -------------------------------------------------
    def live(self, sport):
        """Current + previous ratings and the games list for a sport that's
        turned on in the newsletter config. None if the sport isn't live."""
        if sport in self._live:
            return self._live[sport]
        cfg = (self.settings.get("sports") or {}).get(sport)
        if not cfg or not cfg.get("enabled"):
            self._live[sport] = None
            return None
        print(f"[{sport}] loading ratings and games...", flush=True)
        cur, cur_date = self.snapshot(sport, on_or_before=self.as_of, max_age=1)
        if cur is None:
            cur, cur_date = fetch_json(cfg["ratings_url"]), "live"
        prev, prev_date = self.snapshot(sport, on_or_before=(d(self.as_of) - dt.timedelta(days=5)).isoformat(), max_age=9)
        try:
            if sport == "football" and self.fb_sched:
                games = games_from_team_schedules(self.fb_sched)   # includes playoff games
            else:
                games = load_games(sport, cfg)
        except Exception as e:
            print(f"[{sport}] games failed: {e}", file=sys.stderr)
            games = []
        info = {"cfg": cfg, "cur": cur, "prev": prev, "cur_date": cur_date, "prev_date": prev_date,
                "games": games, "records": records_from(games, until=self.week_end),
                "league_average": cur.get("league_average") if isinstance(cur, dict) else None}
        self._live[sport] = info
        return info

    def snapshot(self, sport, on_or_before, max_age):
        root = os.path.join(self.out_dir, "snapshots")
        if not os.path.isdir(root):
            return None, None
        dates = sorted(x for x in os.listdir(root)
                       if x <= on_or_before and os.path.exists(os.path.join(root, x, f"{sport}.json")))
        if not dates or (d(on_or_before) - d(dates[-1])).days > max_age:
            return None, None
        with open(os.path.join(root, dates[-1], f"{sport}.json"), encoding="utf-8") as f:
            return json.load(f), dates[-1]

    def official_record(self, sport, team):
        """(wins, losses, ties) from MSHSAA's season records page, if we have it."""
        if sport not in self._official:
            folder = self.rcfg.get("season_records_dir")
            raw = load(os.path.join(folder, f"{sport}_season_records.json"), None) if folder else None
            rows = raw if isinstance(raw, list) else next((v for v in (raw or {}).values() if isinstance(v, list)), [])
            self._official[sport] = {r["school"]: (r.get("wins") or 0, r.get("losses") or 0, r.get("ties") or 0) for r in rows if r.get("school")}
        return self._official[sport].get(team)

    # --- history -----------------------------------------------------
    def history(self, sport):
        if sport not in self._hist:
            f = (self.rcfg.get("history_files") or {}).get(sport)
            self._hist[sport] = load(f, None) if f else None
        return self._hist[sport]

    def all_names(self, sport):
        live = self.live(sport)
        if live:
            return [t["school"] for t in teams_of(live["cur"])]
        return []


# ----------------------------------------------------------------------
# Sections
# ----------------------------------------------------------------------

def story(score, headline, text=""):
    return {"score": score, "headline": headline, "text": text}


def history_context(data, sport, slug, wins, losses, ovr):
    """Where this season's wins and OVR rank among the school's seasons in our data."""
    h = data.history(sport)
    if not h:
        return None
    team = (h.get("teams") or {}).get(slug)
    if not team:
        return None
    seasons = team.get("history", {})
    past = [(int(y), v) for y, v in seasons.items() if v.get("wins") is not None]
    if not past:
        return None
    first = min(y for y, _ in past)
    win_list = sorted((v["wins"] for _, v in past), reverse=True)
    beat = [y for y, v in past if v["wins"] > wins]
    tie = [y for y, v in past if v["wins"] == wins]
    ovr_better = [y for y, v in past if (v.get("ovr_rating") or -999) > ovr] if ovr is not None else []
    return {"first": first, "seasons": len(past), "wins_rank": len(beat) + 1,
            "better_years": sorted(beat), "tied_years": sorted(tie),
            "ovr_rank": len(ovr_better) + 1 if ovr is not None else None,
            "ovr_better_years": sorted(ovr_better), "max_wins": win_list[0]}


def football_round(rcfg, iso):
    """Postseason round for a football game date, from the config's round dates."""
    rounds = (rcfg.get("football") or {}).get("rounds") or {}
    x = d(iso)
    for day, label in rounds.items():
        if abs((x - d(str(day))).days) <= 1:
            return label
    return None


def build_live_section(data, sport, slug, school, team, live):
    rcfg = data.rcfg
    meta = rcfg["sports"][sport]
    teams = teams_of(live["cur"])
    me, crank, csize = class_rank(teams, team)
    prev_me, prev_crank, _ = class_rank(teams_of(live["prev"]), team) if live["prev"] else (None, None, None)
    games = [g for g in live["games"] if team in (g["team1"], g["team2"])]
    mine = []
    for g in sorted(games, key=lambda g: g["date"]):
        us_first = g["team1"] == team
        mine.append({"date": g["date"], "opp": g["team2"] if us_first else g["team1"],
                     "us": g["score1"] if us_first else g["score2"],
                     "them": g["score2"] if us_first else g["score1"],
                     "played": is_played(g), "forfeit": g.get("forfeit"), "ot": g.get("overtime")})
    rec = list(data.official_record(sport, team) or live["records"].get(team, [0, 0, 0]))
    played = [g for g in mine if g["played"] and g["date"] <= data.week_end]
    this_week = [g for g in played if data.week_start <= g["date"] <= data.week_end]
    horizon = (d(data.week_end) + dt.timedelta(days=7)).isoformat()
    upcoming = [g for g in mine if not g["played"] and data.week_end < g["date"] <= horizon]
    last_played = played[-1]["date"] if played else None
    any_future = any(not g["played"] and g["date"] > data.week_end for g in mine)

    # Is the season over for this team?
    wrapup_days = rcfg.get("wrapup_days", 21)
    # A finished team stays in "Seasons wrapped" until a few weeks after the
    # sport's last game statewide (state finals), not just its own last game.
    sport_last = max((g["date"] for g in live["games"] if is_played(g) and g["date"] <= data.week_end), default=None)
    ref = max(x for x in (last_played, sport_last) if x) if (last_played or sport_last) else None
    if not this_week and not upcoming and not any_future:
        if last_played and ref and (d(data.week_end) - d(ref)).days <= wrapup_days:
            state = "wrapup"
        elif last_played:
            state = "offseason"
        else:
            state = "none"
    else:
        state = "live"

    by_name = {t["school"]: t for t in teams}
    opp_rank = lambda n: class_rank(teams, n)[1] if n in by_name else None
    sec = {"sport": sport, "label": meta["label"], "emoji": meta.get("emoji", ""), "state": state,
           "team": team, "co_op": team if " with " in team else None,
           "classification": me.get("classification") if me else None,
           "district": me.get("district") if me else None,
           "class_rank": crank, "class_size": csize, "class_rank_prev": prev_crank,
           "state_rank": me.get("ovr_rank") if me else None, "state_size": len(teams),
           "ovr": me.get("ovr_rating") if me else None,
           "ovr_prev": prev_me.get("ovr_rating") if prev_me else None,
           "record": fmt_rec(rec), "wins": rec[0], "losses": rec[1],
           "results": [], "next": [], "notes": [], "stories": []}

    # Football: projections the team was given for last week's games.
    fb_pred = {}
    if sport == "football" and data.fb_sched:
        for g in (data.fb_sched.get("teams") or {}).get(team, []):
            fb_pred[g["date"]] = g

    for g in this_week:
        res = "W" if g["us"] > g["them"] else ("L" if g["us"] < g["them"] else "T")
        r = {"date": g["date"], "opp": g["opp"], "us": g["us"], "them": g["them"], "result": res,
             "opp_class_rank": opp_rank(g["opp"]), "forfeit": g["forfeit"], "ot": g["ot"],
             "round": football_round(rcfg, g["date"]) if sport == "football" else None}
        p = fb_pred.get(g["date"])
        if p and p.get("predicted_team_score") is not None:
            r["off_delta"] = round(g["us"] - p["predicted_team_score"])
            r["def_delta"] = round(p["predicted_opp_score"] - g["them"])
        sec["results"].append(r)

    for g in upcoming[:2]:
        n = {"date": g["date"], "opp": g["opp"], "opp_class_rank": opp_rank(g["opp"]),
             "opp_record": fmt_rec(live["records"][g["opp"]]) if g["opp"] in live["records"] else None,
             "round": football_round(rcfg, g["date"]) if sport == "football" else None}
        opp = by_name.get(g["opp"])
        if opp and me:
            n["opp_classification"] = opp.get("classification")
        if sport == "football" and data.previews:
            pv = next((x for x in data.previews.get("games", [])
                       if x["date"] == g["date"] and set(x["teams"]) == {team, g["opp"]}), None)
            if pv:
                i = pv["teams"].index(team)
                n["proj"] = (pv["projection"]["score"][i], pv["projection"]["score"][1 - i])
                n["win_prob"] = pv["projection"]["win_prob"][i]
                n["round"] = pv.get("round") or n["round"]
                h2h = pv.get("h2h")
                if h2h:
                    n["h2h"] = {"since": h2h["since"], "w": h2h["wins"][i], "l": h2h["wins"][1 - i]}
                n["preview_id"] = pv["id"]
        if "proj" not in n and opp and me:
            la = live.get("league_average")
            if sport == "football":
                n["proj"] = predict_football(me, opp, la or rcfg["football"].get("league_average", 26))
            elif meta.get("project_scores") and la is not None and me.get("off_rating") is not None:
                a = me["off_rating"] - opp["def_rating"] + la
                b = opp["off_rating"] - me["def_rating"] + la
                n["proj"] = (max(0, round(a)), max(0, round(b)))
                if n["proj"][0] == n["proj"][1]:
                    n["proj"] = None
                    n["favored"] = team if a > b else g["opp"]
            else:
                n["favored"] = team if me["ovr_rating"] >= opp["ovr_rating"] else g["opp"]
        sec["next"].append(n)

    # Knocked out of the playoffs: lost its last game, nothing left on its
    # schedule, while other teams in the sport still have games to play.
    sport_has_future = any(not is_played(g) and g["date"] > data.week_end for g in live["games"])
    if this_week and not any_future and sport_has_future and this_week[-1]["us"] < this_week[-1]["them"]:
        last = sec["results"][-1]
        sec["eliminated"] = {"opp": last["opp"], "us": last["us"], "them": last["them"], "round": last.get("round")}

    # Football playoff odds (only while the team is still alive).
    if sport == "football" and data.odds and state == "live":
        cls = (data.odds.get("classes") or {}).get(f"class{sec['classification']}", {})
        row = next((t for t in cls.get("teams", []) if t["team"] == team), None)
        if row:
            sec["odds"] = {k: row.get(k + "_display") for k in ("district_title", "final_four", "state_final", "state_title")}

    hc = history_context(data, sport, slug, rec[0], rec[1], sec["ovr"]) if sec["co_op"] is None else None
    sec["history"] = hc
    add_live_stories(sec, rcfg, school)
    if state in ("offseason", "none"):
        sec["stories"] = []
    return sec


def add_live_stories(sec, rcfg, school):
    name = school["name"]
    label = sec["label"].lower()
    cls = sec["classification"]
    S = sec["stories"]
    # Postseason results (football round labels come from the config).
    for r in sec["results"]:
        rnd = r.get("round")
        if not rnd:
            continue
        if r["result"] == "W" and rnd.lower().startswith("district final"):
            S.append(story(95, f"{name} wins the Class {cls} District {school_district(sec)} {label} title",
                           f"The {label} team beat {r['opp']} {r['us']}–{r['them']} in the district final."))
        elif r["result"] == "W" and rnd.lower() == "state final":
            S.append(story(100, f"{name} wins the Class {cls} {label} state title!",
                           f"A {r['us']}–{r['them']} win over {r['opp']} in the state final."))
        elif r["result"] == "W":
            nxt = sec["next"][0]["round"] if sec["next"] else None
            S.append(story(85, f"{name} {label} wins its {rnd.lower()}" + (f" and moves on to the {nxt.lower()}" if nxt else ""),
                           f"Beat {r['opp']} {r['us']}–{r['them']}."))
        elif r["result"] == "L":
            S.append(story(80, f"{name}'s {label} season ends in the {rnd.lower()} at {sec['record']}",
                           f"Lost to {r['opp']} {r['us']}–{r['them']}."))
    el = sec.get("eliminated")
    if el and not el.get("round"):     # football rounds are handled above
        top = sec["class_rank"] and sec["class_rank"] <= 10
        S.append(story(74 if top else 58, f"{name} {label}'s season ends at {sec['record']}",
                       f"A {el['us']}–{el['them']} playoff loss to {el['opp']}"
                       + (f" ends a season that finished #{sec['class_rank']} in Class {cls}." if sec["class_rank"] else ".")))
    # Unbeaten
    if sec["losses"] == 0 and sec["wins"] >= 5:
        S.append(story(70, f"{name} {label} is still unbeaten at {sec['record']}"))
    # Win totals in the school's history
    hc = sec.get("history")
    if hc and sec["wins"] >= 6:
        if hc["wins_rank"] == 1:
            S.append(story(78, f"{name} {label} has its most wins in our records ({sec['wins']}, back to {hc['first']})"))
        elif hc["wins_rank"] <= 3:
            S.append(story(55, f"{name} {label}'s {sec['wins']} wins are its {ordinal_word(hc['wins_rank'])}most since {hc['first']}"))
    # Rank movement
    if sec["class_rank"] and sec["class_rank_prev"]:
        mv = sec["class_rank_prev"] - sec["class_rank"]
        if abs(mv) >= 3:
            word = "climbs" if mv > 0 else "slips"
            S.append(story(min(75, 42 + 3 * abs(mv)) if mv > 0 else 35,
                           f"{name} {label} {word} {abs(mv)} spots to #{sec['class_rank']} in Class {cls}"))
    # Upset win: beat a team ranked 10+ spots better in its own class list
    for r in sec["results"]:
        if r["result"] == "W" and r["opp_class_rank"] and sec["class_rank"] and r["opp_class_rank"] + 10 <= sec["class_rank"]:
            S.append(story(62, f"{name} {label} upsets #{r['opp_class_rank']} {r['opp']}", f"{r['us']}–{r['them']}."))
    # Highly ranked
    if sec["class_rank"] and sec["class_rank"] <= 5:
        S.append(story(50 - sec["class_rank"], f"{name} {label} is ranked #{sec['class_rank']} in Class {cls}"))
    if sec["state"] == "wrapup":
        S.append(story(30, f"{name} {label} wraps up at {sec['record']}"))


def school_district(sec):
    return sec.get("district") or "?"


def build_offline_section(data, sport, slug, school):
    """Sports that aren't live this week: preseason preview (last season's line)
    or nothing, based on the season dates in the config."""
    rcfg = data.rcfg
    meta = rcfg["sports"][sport]
    start = d(str(meta["start"])) if meta.get("start") else None
    end = d(str(meta["end"])) if meta.get("end") else None
    we = d(data.week_end)
    pre_days = rcfg.get("preseason_days", 21)
    last = (school.get("sports") or {}).get(sport)
    sec = {"sport": sport, "label": meta["label"], "emoji": meta.get("emoji", ""), "team": school["name"],
           "co_op": None, "results": [], "next": [], "notes": [], "stories": []}
    if not last:
        sec["state"] = "none"
        return sec
    if start and 0 <= (start - we).days <= pre_days:
        sec["state"] = "preseason"
    elif start and end and start <= we <= end:
        sec["state"] = "preseason"   # season started but ratings aren't live yet
    else:
        sec["state"] = "offseason"
    # Last season's line and class rank from schools.json
    rows = [(s["sports"][sport].get("ovr_rating"), sl, s["sports"][sport].get("classification"))
            for sl, s in data.schools.items()
            if sport in (s.get("sports") or {}) and s["sports"][sport].get("ovr_rating") is not None]
    cls = last.get("classification")
    same = sorted([r for r in rows if r[2] == cls], key=lambda r: -r[0])
    crank = next((i + 1 for i, r in enumerate(same) if r[1] == slug), None)
    sec.update({"classification": cls, "class_rank": crank, "class_size": len(same),
                "record": f"{last.get('wins', 0)}-{last.get('losses', 0)}",
                "wins": last.get("wins"), "losses": last.get("losses"),
                "last_season": meta.get("last_season_label", "last season")})
    hc = history_context(data, sport, slug, last.get("wins") or 0, last.get("losses") or 0, None)
    if hc and (last.get("wins") or 0) > hc["max_wins"]:
        sec["notes"].append(f"the most wins in our records, which start in {hc['first']}–{str(hc['first'] + 1)[2:]}"
                            if meta.get("season") == "winter" else f"the most wins in our records, which start in {hc['first']}")
    if sec["state"] == "preseason" and crank:
        sc = 40 if crank <= 5 else (32 if crank <= 15 else 20)
        sec["stories"].append(story(sc, f"{school['name']} {meta['label'].lower()} opens after a #{crank} finish in Class {cls}"))
    return sec


def build_section(data, sport, slug):
    school = data.schools[slug]
    live = data.live(sport)
    if live:
        names = team_names_for(school, slug, data.all_names(sport), data.aliases)
        if names:
            return build_live_section(data, sport, slug, school, names[0], live)
        sec = {"sport": sport, "label": data.rcfg["sports"][sport]["label"], "state": "none",
               "results": [], "next": [], "stories": [], "notes": []}
        return sec
    return build_offline_section(data, sport, slug, school)


def cup_section(data, slug):
    if not data.cup:
        return None
    row = next((s for s in data.cup.get("schools", []) if s.get("slug") == slug), None)
    if not row:
        return None
    sports = [data.rcfg["sports"].get(k, {}).get("label", k).lower() for k in (row.get("sports") or {})]
    out = {"rank": row["rank"], "change": row.get("change"), "class_rank": row.get("class_rank"),
           "cup_class": row.get("cup_class"), "score": row.get("score"), "counted": sports,
           "of": len(data.cup.get("schools", [])), "stories": []}
    ch = row.get("change") or 0
    if ch >= 5:
        out["stories"].append(story(40, f"{data.schools[slug]['name']} climbs {ch} spots in the All MO Sports Cup"))
    return out


# ----------------------------------------------------------------------
# Email rendering
# ----------------------------------------------------------------------

def logo_colors(slug, rcfg):
    """Two brand colors from the school's logo (dark one for the header, bright
    one for accents). Falls back to the config defaults."""
    over = (rcfg.get("school_colors") or {}).get(slug)
    if over:
        return over["primary"], over["accent"]
    default = (rcfg.get("default_colors") or {}).get("primary", "#0b2a55"), (rcfg.get("default_colors") or {}).get("accent", "#fdb930")
    try:
        from PIL import Image
    except ImportError:
        return default
    path = os.path.join(resolve(rcfg.get("logo_dir", "logos_main2")), f"{slug}.png")
    if not os.path.exists(path):
        return default
    im = Image.open(path).convert("RGBA")
    im.thumbnail((80, 80))
    px = [p[:3] for p in (im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()) if p[3] > 200]
    q = lambda c: tuple((v // 24) * 24 + 12 for v in c)
    lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    sat = lambda c: max(c) - min(c)
    counts = Counter(q(c) for c in px if sat(c) > 40)   # ignore white, black and grays
    if not counts:
        return default
    cols = [c for c, _ in counts.most_common(8)]
    dark = [c for c in cols if lum(c) < 110]
    bright = [c for c in cols if lum(c) >= 110]
    hexc = lambda c: "#%02x%02x%02x" % c
    primary = hexc(dark[0]) if dark else default[0]
    accent = hexc(bright[0]) if bright else (hexc(dark[1]) if len(dark) > 1 else default[1])
    if accent == primary:
        accent = default[1]
    return primary, accent


def stat_box(label, value, sub="", sub_color="#5b6577"):
    return (f'<td width="33%" style="padding:0 4px;" valign="top"><div style="background:#f3f5f9;border-radius:8px;padding:10px;">'
            f'<div style="font-size:11px;color:#5b6577;">{esc(label)}</div>'
            f'<div style="font-size:22px;font-weight:bold;">{esc(value)}</div>'
            f'<div style="font-size:11px;color:{sub_color};">{sub}</div></div></td>')


def move_html(prev, cur, lower_is_better=True):
    if prev is None or cur is None or prev == cur:
        return "no change", "#5b6577"
    better = cur < prev if lower_is_better else cur > prev
    arrow = "▲" if better else "▼"
    color = "#1e7b34" if better else "#b3261e"
    n = abs(prev - cur)
    if isinstance(n, float):
        return f"{arrow} from {prev:.2f}", color
    return f"{arrow} {n} from #{prev}", color


def score_text(r):
    s = f"{r['result']} {r['us']}–{r['them']}"
    if r.get("ot"):
        s += " (OT)"
    if r.get("forfeit"):
        s += " (forfeit)"
    return s


def render_live(sec, primary, rcfg):
    nm = sec["label"]
    head_bits = [sec["record"]]
    if sec.get("classification"):
        head_bits.append(f"Class {sec['classification']}")
    out = [f'<tr><td style="padding:20px 22px;border-bottom:1px solid #e6e9ef;">',
           f'<div style="font-size:16px;font-weight:bold;">{sec.get("emoji", "")} {esc(nm)} '
           f'<span style="font-weight:normal;color:#5b6577;font-size:13px;">· {esc(" · ".join(head_bits))}</span></div>']
    if sec.get("co_op"):
        out.append(f'<div style="font-size:12px;color:#8a92a3;margin-top:2px;">Co-op team: {esc(sec["co_op"])}</div>')
    mv_txt, mv_col = move_html(sec.get("class_rank_prev"), sec.get("class_rank"))
    ovr_sub, ovr_col = ("", "#5b6577")
    if sec.get("ovr_prev") is not None and sec.get("ovr") is not None and round(sec["ovr_prev"], 2) != round(sec["ovr"], 2):
        ovr_sub, ovr_col = move_html(sec["ovr_prev"], sec["ovr"], lower_is_better=False)
    out.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:12px 0 0 -4px;"><tr>')
    out.append(stat_box(f"Class {sec.get('classification')} rank", f"#{sec['class_rank']}" if sec.get("class_rank") else "–",
                        f"of {sec['class_size']} · {mv_txt}", mv_col))
    out.append(stat_box("Statewide", f"#{sec['state_rank']}" if sec.get("state_rank") else "–", f"of {sec['state_size']} teams"))
    out.append(stat_box("OVR rating", f"{sec['ovr']:.2f}" if sec.get("ovr") is not None else "–", ovr_sub, ovr_col))
    out.append('</tr></table>')

    if sec["results"]:
        lines = []
        for r in sec["results"]:
            opp = esc(r["opp"]) + (f" (#{r['opp_class_rank']} in class)" if r.get("opp_class_rank") else "")
            line = f'<strong>{esc(score_text(r))}</strong> vs {opp}'
            if r.get("round"):
                line += f' · {esc(r["round"])}'
            if r.get("off_delta") is not None:
                parts = []
                if abs(r["off_delta"]) >= 3:
                    parts.append(f"scored {abs(r['off_delta'])} {'more' if r['off_delta'] > 0 else 'fewer'} than projected")
                if abs(r["def_delta"]) >= 3:
                    parts.append(f"allowed {abs(r['def_delta'])} {'fewer' if r['def_delta'] > 0 else 'more'}")
                if parts:
                    line += f'<br><span style="color:#5b6577;">{esc(", ".join(parts).capitalize())}.</span>'
            lines.append(f'<div style="margin-top:4px;">{line}</div>')
        out.append(f'<div style="font-size:13px;color:#3d4657;line-height:1.6;margin-top:12px;">'
                   f'<div style="font-size:12px;color:#5b6577;">Last week</div>{"".join(lines)}</div>')
    elif sec["state"] == "live":
        out.append('<div style="font-size:13px;color:#5b6577;margin-top:12px;">No games last week.</div>')

    for n in sec["next"]:
        tag = "Next game" + (f" · {n['round']}" if n.get("round") else "") + f" · {nice_date(n['date'])}"
        opp_bits = []
        if n.get("opp_record"):
            opp_bits.append(n["opp_record"])
        if n.get("opp_class_rank") and n.get("opp_classification"):
            opp_bits.append(f"#{n['opp_class_rank']} in Class {n['opp_classification']}")
        body = []
        if opp_bits:
            body.append(f"{esc(n['opp'])}: {esc(', '.join(opp_bits))}.")
        if n.get("proj"):
            a, b = n["proj"]
            lead = f"{sec['team']} {a}, {n['opp']} {b}" if a >= b else f"{n['opp']} {b}, {sec['team']} {a}"
            body.append(f"Projection: <strong>{esc(lead)}</strong>.")
        elif n.get("favored"):
            body.append(f"Our ratings favor <strong>{esc(n['favored'])}</strong>.")
        if n.get("win_prob") is not None:
            body.append(f"Win chance: <strong>{pct(n['win_prob'])}</strong>.")
        if n.get("h2h") and (n["h2h"]["w"] + n["h2h"]["l"]) >= 3:
            body.append(f"Series since {n['h2h']['since']}: {n['h2h']['w']}–{n['h2h']['l']}.")
        link = rcfg["sports"][sec["sport"]].get("preview_page")
        out.append(f'<div style="border:2px solid {primary};border-radius:10px;padding:12px 14px;margin-top:14px;">'
                   f'<div style="font-size:11px;color:{primary};font-weight:bold;text-transform:uppercase;letter-spacing:.5px;">{esc(tag)}</div>'
                   f'<div style="font-size:16px;font-weight:bold;margin:4px 0;">{esc(sec["team"])} vs {esc(n["opp"])}</div>'
                   f'<div style="font-size:13px;color:#3d4657;line-height:1.6;">{" ".join(body)}</div>'
                   + (f'<div style="margin-top:8px;"><a href="{esc(link)}" style="font-size:13px;color:{primary};font-weight:bold;text-decoration:none;">See the matchup preview →</a></div>' if link else "")
                   + '</div>')

    if sec.get("eliminated"):
        out.append('<div style="font-size:13px;color:#5b6577;margin-top:12px;">Season over. Thanks for following this team.</div>')

    if sec.get("odds") and any(v for v in sec["odds"].values()) and not sec.get("eliminated"):
        o = sec["odds"]
        out.append(f'<div style="font-size:12px;color:#5b6577;margin-top:10px;">Playoff odds: Final Four {esc(o.get("final_four") or "–")} · '
                   f'State title {esc(o.get("state_title") or "–")}</div>')

    hc = sec.get("history")
    if hc and sec["wins"] >= 5 and hc["wins_rank"] <= 3:
        if hc["wins_rank"] == 1 and not hc["tied_years"]:
            txt = f"{sec['wins']} wins is the most for this program since our data starts in {hc['first']}."
        else:
            others = hc["better_years"] + hc["tied_years"]
            txt = (f"{sec['wins']} wins: only {', '.join(map(str, hc['better_years']))} had more since {hc['first']}"
                   if hc["better_years"] else f"{sec['wins']} wins matches the program's best since {hc['first']}")
            if hc["tied_years"] and hc["better_years"]:
                txt += f", and {', '.join(map(str, hc['tied_years']))} had the same"
            txt += "."
        out.append(f'<div style="background:#fff7e3;border-radius:8px;padding:10px 12px;margin-top:12px;font-size:13px;line-height:1.6;color:#5c4300;">{esc(txt)}</div>')
    out.append('</td></tr>')
    return "".join(out)


def render_wrapups(secs):
    rows = []
    for s in secs:
        co = f' <span style="color:#8a92a3;">(co-op)</span>' if s.get("co_op") else ""
        rank = f" · #{s['class_rank']} of {s['class_size']} in Class {s['classification']}" if s.get("class_rank") else ""
        rows.append(f'<tr><td style="padding:6px 0;border-bottom:1px solid #f0f2f6;">{esc(s["label"])}{co}</td>'
                    f'<td align="right" style="padding:6px 0;border-bottom:1px solid #f0f2f6;color:#5b6577;">{esc(s["record"] + rank)}</td></tr>')
    return ('<tr><td style="padding:20px 22px;border-bottom:1px solid #e6e9ef;">'
            '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;">🏁 Seasons wrapped</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;">{"".join(rows)}</table></td></tr>')


def render_preseason(secs):
    lines = []
    for s in secs:
        rank = f", #{s['class_rank']} of {s['class_size']} in Class {s['classification']}" if s.get("class_rank") else ""
        note = f" ({s['notes'][0]})" if s.get("notes") else ""
        lines.append(f'<div style="margin-top:4px;"><strong>{esc(s["label"])}:</strong> went {esc(s["record"])}{esc(rank)} '
                     f'in {esc(s["last_season"])}{esc(note)}.</div>')
    return ('<tr><td style="padding:20px 22px;border-bottom:1px solid #e6e9ef;">'
            '<div style="font-size:16px;font-weight:bold;margin-bottom:4px;">📅 Up next</div>'
            f'<div style="font-size:13px;line-height:1.7;color:#3d4657;">{"".join(lines)}'
            '<div style="color:#5b6577;margin-top:4px;">Ratings arrive with the first games of the season.</div></div></td></tr>')


def render_cup(cup):
    ch = cup.get("change") or 0
    col = "#1e7b34" if ch > 0 else ("#b3261e" if ch < 0 else "#5b6577")
    mv = f"▲ {ch}" if ch > 0 else (f"▼ {abs(ch)}" if ch < 0 else "no change")
    counted = " and ".join(cup["counted"]) if len(cup["counted"]) <= 2 else ", ".join(cup["counted"])
    return ('<tr><td style="padding:20px 22px;border-bottom:1px solid #e6e9ef;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td><div style="font-size:16px;font-weight:bold;">🏆 All MO Sports Cup</div>'
            f'<div style="font-size:12px;color:#5b6577;margin-top:2px;">#{cup["class_rank"]} in Class {cup["cup_class"]} · counting {esc(counted)} so far</div></td>'
            f'<td align="right"><div style="font-size:22px;font-weight:bold;">#{cup["rank"]}</div>'
            f'<div style="font-size:11px;color:{col};">{mv} · score {cup["score"]}</div></td></tr></table></td></tr>')


def render_school_block(data, slug, secs, cup, show_header_label):
    """All sections for one followed school. Returns (html, primary_color)."""
    rcfg = data.rcfg
    primary, accent = logo_colors(slug, rcfg)
    parts = []
    if show_header_label:
        parts.append(f'<tr><td style="padding:14px 22px 0;font-size:13px;font-weight:bold;color:{primary};text-transform:uppercase;letter-spacing:.5px;">'
                     f'{esc(data.schools[slug]["name"])}</td></tr>')
    live = [s for s in secs if s["state"] == "live"]
    wrap = [s for s in secs if s["state"] == "wrapup"]
    pre = [s for s in secs if s["state"] == "preseason"]
    for s in live:
        parts.append(render_live(s, primary, rcfg))
    if wrap:
        parts.append(render_wrapups(wrap))
    if pre:
        parts.append(render_preseason(pre))
    if cup:
        parts.append(render_cup(cup))
    return "".join(parts), primary, accent


def render_email(data, sub, blocks, big, week_label):
    """blocks: list of (slug, html, primary, accent)."""
    rcfg = data.rcfg
    site = data.settings.get("site_url", "https://allmosports.com")
    first_slug, _, primary, accent = blocks[0]
    names = [data.schools[b[0]]["name"] for b in blocks]
    title = f"The {names[0]} Report" if len(names) == 1 else "Your school report"
    sub_line = ("Following all sports" if sub["follows"][0]["sports"] == "all"
                else "Following " + ", ".join(rcfg["sports"][k]["label"].lower() for k in sub["follows"][0]["sports"])) if len(names) == 1 \
        else " · ".join(names)
    logo_base = rcfg.get("logo_base_url", "")
    logo = (f'<td valign="middle" align="right" width="78"><img src="{esc(logo_base + first_slug + ".png")}" width="72" alt="{esc(names[0])} logo" '
            f'style="display:block;background:#ffffff;border-radius:10px;padding:6px;"></td>') if logo_base and len(names) == 1 else ""
    big_html = ""
    if big:
        big_html = (f'<tr><td style="padding:20px 22px;border-bottom:1px solid #e6e9ef;">'
                    f'<div style="font-size:12px;color:#8a6200;font-weight:bold;text-transform:uppercase;letter-spacing:.5px;">The big story</div>'
                    f'<div style="font-size:20px;font-weight:bold;line-height:1.3;margin:4px 0 6px;">{esc(big["headline"])}</div>'
                    + (f'<div style="font-size:14px;line-height:1.6;color:#3d4657;">{esc(big["text"])}</div>' if big.get("text") else "")
                    + '</td></tr>')
    manage = rcfg.get("manage_url", site + "/account/")
    body = "".join(b[1] for b in blocks)
    preheader = big["headline"] if big else f"Your {names[0]} report"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} – All MO Sports</title></head>
<body style="margin:0;padding:0;background:#eef0f4;font-family:Arial,Helvetica,sans-serif;color:#1b2333;">
<div style="display:none;max-height:0;overflow:hidden;">{esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef0f4;"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #d9dde5;">
<tr><td style="background:{primary};padding:18px 22px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td valign="middle">
<div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{accent};font-weight:bold;">All MO Sports · {esc(week_label)}</div>
<div style="font-size:24px;font-weight:bold;color:#ffffff;margin-top:4px;">{esc(title)}</div>
<div style="font-size:13px;color:#e6ebf5;margin-top:2px;">{esc(sub_line)}</div></td>{logo}</tr></table></td></tr>
<tr><td style="height:5px;background:{accent};font-size:0;line-height:0;">&nbsp;</td></tr>
{big_html}
{body}
<tr><td style="padding:16px 22px;font-size:11px;color:#6b7385;line-height:1.7;">
<a href="{esc(manage)}" style="color:{primary};">Follow another school</a> · <a href="{esc(manage)}" style="color:{primary};">Choose sports</a> · <a href="{{{{unsubscribe_url}}}}" style="color:{primary};">Unsubscribe</a><br>
Know someone who'd want this? Forward it, since anyone can follow a school at <a href="{esc(site)}" style="color:{primary};">allmosports.com</a>.<br>
Ratings as of {esc(nice_date(data.as_of, weekday=False))}. Projections come from the All MO Sports rating model.
</td></tr></table></td></tr></table></body></html>"""


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def last_full_week(today):
    end = today - dt.timedelta(days=today.weekday() + 1)   # previous Sunday
    return end - dt.timedelta(days=6), end


def main():
    ap = argparse.ArgumentParser(description="Build per-school weekly report emails.")
    ap.add_argument("--config", default=os.path.join(HERE, "newsletter_config.yaml"))
    ap.add_argument("--out", default=os.path.join(HERE, "newsletter_output"))
    ap.add_argument("--week-ending", help="Sunday the week ends on, YYYY-MM-DD (default: last full week)")
    ap.add_argument("--follows", help="JSON file: [{id, email, follows: [{school, sports}]}]")
    ap.add_argument("--school", help="Quick test: one school slug")
    ap.add_argument("--sports", default="all", help='Quick test: "all" or a comma list of sport keys')
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    rcfg = settings["school_reports"]

    if args.week_ending:
        we = dt.date.fromisoformat(args.week_ending)
        ws = we - dt.timedelta(days=6)
    else:
        ws, we = last_full_week(dt.date.today())
    as_of = (we + dt.timedelta(days=1)).isoformat()   # the Monday the email goes out
    week_label = f"Week of {nice_date(as_of, weekday=False)}, {d(as_of).year}"

    if args.school:
        subs = [{"id": f"{args.school}-{args.sports.replace(',', '-')}", "email": "",
                 "follows": [{"school": args.school,
                              "sports": "all" if args.sports == "all" else args.sports.split(",")}]}]
    elif args.follows:
        with open(args.follows, encoding="utf-8") as f:
            subs = json.load(f)
    else:
        sys.exit("Give --follows FILE or --school SLUG.")

    data = Data(settings, rcfg, args.out, as_of, ws.isoformat(), we.isoformat())

    # Which school-sport sections do we need? Build each one once.
    needed = {}
    for s in subs:
        for fo in s["follows"]:
            if fo["school"] not in data.schools:
                print(f"Unknown school slug: {fo['school']}", file=sys.stderr)
                continue
            keys = SPORT_ORDER if fo["sports"] == "all" else [k for k in SPORT_ORDER if k in fo["sports"]]
            needed.setdefault(fo["school"], set()).update(keys)
    sections = {}
    for slug, keys in needed.items():
        for k in sorted(keys, key=SPORT_ORDER.index):
            try:
                sections[(slug, k)] = build_section(data, k, slug)
            except Exception as e:
                print(f"[{slug}/{k}] FAILED: {e}", file=sys.stderr)
                sections[(slug, k)] = {"sport": k, "state": "none", "stories": []}
    cups = {slug: cup_section(data, slug) for slug in needed}

    out_dir = os.path.join(args.out, "school_reports", we.isoformat())
    os.makedirs(os.path.join(out_dir, "emails"), exist_ok=True)
    manifest = {"week_start": ws.isoformat(), "week_end": we.isoformat(), "as_of": as_of,
                "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "emails": [], "skipped": []}

    for sub in subs:
        blocks, stories, included = [], [], []
        multi = len(sub["follows"]) > 1
        for fo in sub["follows"]:
            slug = fo["school"]
            if slug not in data.schools:
                continue
            keys = SPORT_ORDER if fo["sports"] == "all" else [k for k in SPORT_ORDER if k in fo["sports"]]
            secs = [sections[(slug, k)] for k in keys]
            secs = [s for s in secs if s["state"] in ("live", "wrapup", "preseason")]
            secs.sort(key=lambda s: (STATE_ORDER[s["state"]], SPORT_ORDER.index(s["sport"])))
            if not secs:
                continue
            cup = cups.get(slug) if fo["sports"] == "all" else None
            h, primary, accent = render_school_block(data, slug, secs, cup, multi)
            blocks.append((slug, h, primary, accent))
            for s in secs:
                stories += s["stories"]
                included.append(f"{slug}/{s['sport']}:{s['state']}")
            if cup:
                stories += cup["stories"]
        # Skip rule: nothing live, wrapping up or about to start.
        if not blocks:
            unknown = [fo["school"] for fo in sub["follows"] if fo["school"] not in data.schools]
            reason = (f"unknown school slug: {', '.join(unknown)}" if unknown and len(unknown) == len(sub["follows"])
                      else "no followed sport is in season this week")
            manifest["skipped"].append({"id": sub["id"], "reason": reason})
            continue
        big = max(stories, key=lambda s: s["score"]) if stories else None
        page = render_email(data, sub, blocks, big, week_label)
        fn = f"{sub['id']}.html"
        with open(os.path.join(out_dir, "emails", fn), "w", encoding="utf-8") as f:
            f.write(page)
        subject = (big["headline"] if big else f"Your {data.schools[blocks[0][0]]['name']} report")
        manifest["emails"].append({"id": sub["id"], "email": sub.get("email", ""), "subject": subject,
                                   "file": f"emails/{fn}", "sections": included,
                                   "other_story_ideas": [s["headline"] for s in sorted(stories, key=lambda s: -s["score"])[1:4]]})
        print(f"  {sub['id']}: {subject}")

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    with open(os.path.join(out_dir, "sections.json"), "w", encoding="utf-8") as f:
        json.dump({f"{a}/{b}": v for (a, b), v in sections.items()}, f, indent=1, default=str)
    idx = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>School reports preview</title></head>"
           "<body style='font-family:Arial,sans-serif;background:#f4f5f8;padding:20px;'>",
           f"<h1 style='font-size:20px;'>School reports · week ending {esc(we.isoformat())}</h1>"]
    for e in manifest["emails"]:
        idx.append(f"<p><a href='{esc(e['file'])}'>{esc(e['id'])}</a> · {esc(e['subject'])}</p>")
    for s in manifest["skipped"]:
        idx.append(f"<p style='color:#888;'>{esc(s['id'])}: skipped ({esc(s['reason'])})</p>")
    idx.append("</body></html>")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(idx))
    print(f"\n{len(manifest['emails'])} email(s), {len(manifest['skipped'])} skipped → {out_dir}")


if __name__ == "__main__":
    main()
