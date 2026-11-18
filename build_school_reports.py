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
import re
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


def clean_name(n):
    """'Murphysboro(Murphysboro, IL)' -> 'Murphysboro (IL)' for out-of-state opponents."""
    if not n:
        return n
    m = re.match(r"^(.*?)\s*\(([^()]*),\s*([A-Z]{2})\)\s*$", n)
    return f"{m.group(1).strip()} ({m.group(3)})" if m else n


def team_names_for(school, slug, names_in_sport, aliases):
    """Exact name, a co-op that includes the school ("Saxony Lutheran with
    St. Vincent"), or a name the alias file maps to this slug."""
    own = {school.get("name"), school.get("mshsaa_name")} - {None}
    found = []
    for n in names_in_sport:
        if n in own or aliases.get(n) == slug:
            found.append(n)
        elif " with " in n and any(p.strip() in own for part in n.split(" with ") for p in part.split(",")):
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
        self.quads = load(rcfg.get("football", {}).get("quad_records_url"), None)
        self.seeds = load(rcfg.get("football", {}).get("seed_projections_url"), None)
        self._live, self._hist, self._official = {}, {}, {}
        self._sos, self._past = {}, {}

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

    def snapshots(self, sport):
        """Every saved ratings snapshot for a sport, oldest first: [(date, {school: row}, league_average)]."""
        key = ("snaps", sport)
        if key not in self._sos:
            out = []
            root = os.path.join(self.out_dir, "snapshots")
            if os.path.isdir(root):
                for x in sorted(os.listdir(root)):
                    f = os.path.join(root, x, f"{sport}.json")
                    if x <= self.as_of and os.path.exists(f):
                        with open(f, encoding="utf-8") as fh:
                            raw = json.load(fh)
                        out.append((x, {t["school"]: t for t in teams_of(raw)},
                                    raw.get("league_average") if isinstance(raw, dict) else None))
            self._sos[key] = out
        return self._sos[key]

    def pregame(self, sport, team, opp, game_date):
        """Projection from the latest snapshot taken on or before the game date
        (so the game itself isn't baked into the ratings). None if none is close enough."""
        snaps = [x for x in self.snapshots(sport) if x[0] <= game_date and (d(game_date) - d(x[0])).days <= 14]
        if snaps:
            date, rows, la = snaps[-1]
            if team in rows and opp in rows:
                pr = project(self.rcfg, sport, rows[team], rows[opp], la)
                if pr:
                    pr["source"] = f"ratings of {date}"
                    return pr
        if sport == "football" and self.fb_sched:          # fallback: the football schedule's own projection
            g = next((x for x in (self.fb_sched.get("teams") or {}).get(team, [])
                      if x["date"][:10] == game_date and x.get("predicted_team_score") is not None), None)
            if g:
                return {"a": g["predicted_team_score"], "b": g["predicted_opp_score"],
                        "score": (round(g["predicted_team_score"]), round(g["predicted_opp_score"])),
                        "source": "football schedule projection"}
        return None

    def trend(self, sport, team):
        """Week-by-week class ranks (OVR, OFF, DEF) and statewide schedule-strength rank."""
        out = []
        live = self.live(sport)
        snaps = self.snapshots(sport)
        for i, (date, rows, la) in enumerate(snaps):
            if i + 1 < len(snaps) and (d(snaps[i + 1][0]) - d(date)).days <= 2:
                continue
            me = rows.get(team)
            if not me:
                continue
            same = [t for t in rows.values() if t.get("classification") == me.get("classification")]
            rk = lambda field: (1 + sum(1 for t in same if (t.get(field) if t.get(field) is not None else -999)
                                        > (me.get(field) if me.get(field) is not None else -999)))
            pt = {"date": date, "ovr": rk("ovr_rating"), "class_size": len(same), "ovr_rating": me.get("ovr_rating")}
            if me.get("off_rating") is not None:
                pt["off"], pt["def"] = rk("off_rating"), rk("def_rating")
            sos = self.sos_on(sport, date, rows, live["games"] if live else []).get(team)
            if sos:
                pt["sos"], pt["sos_of"] = sos[0], sos[1]
            out.append(pt)
        return out

    def sos_on(self, sport, date, rows, games):
        key = ("sos_on", sport, date)
        if key not in self._sos:
            opps = {}
            for g in games:
                if not is_played(g) or g["date"] > date:
                    continue
                for a, b in ((g["team1"], g["team2"]), (g["team2"], g["team1"])):
                    if b in rows and rows[b].get("ovr_rating") is not None:
                        opps.setdefault(a, []).append(rows[b]["ovr_rating"])
            avg = {t: sum(v) / len(v) for t, v in opps.items() if len(v) >= 3 and t in rows}
            order = sorted(avg, key=lambda t: -avg[t])
            self._sos[key] = {t: (i + 1, len(order)) for i, t in enumerate(order)}
        return self._sos[key]

    def official_record(self, sport, team):
        """(wins, losses, ties) from MSHSAA's season records page, if we have it.
        Only used when those records were scraped around this report's date,
        so re-running an old week counts that week's games instead."""
        if sport not in self._official:
            folder = self.rcfg.get("season_records_dir")
            raw = load(os.path.join(folder, f"{sport}_season_records.json"), None) if folder else None
            rows = raw if isinstance(raw, list) else next((v for v in (raw or {}).values() if isinstance(v, list)), [])
            scraped = str((raw or {}).get("scraped_at", ""))[:10] if isinstance(raw, dict) else ""
            if scraped and not (-3 <= (d(scraped) - d(self.as_of)).days <= 8):
                rows = []
            self._official[sport] = {r["school"]: (r.get("wins") or 0, r.get("losses") or 0, r.get("ties") or 0) for r in rows if r.get("school")}
        return self._official[sport].get(team)

    def sos(self, sport):
        """Schedule strength: average OVR of opponents played, ranked statewide
        (teams with 3+ rated opponents). {team: (rank, of, avg)}"""
        if sport in self._sos:
            return self._sos[sport]
        live = self.live(sport)
        out = {}
        if live:
            ovr = {t["school"]: t.get("ovr_rating") for t in teams_of(live["cur"])}
            opps = {}
            for g in live["games"]:
                if not is_played(g) or g["date"] > self.week_end:
                    continue
                for a, b in ((g["team1"], g["team2"]), (g["team2"], g["team1"])):
                    if ovr.get(b) is not None:
                        opps.setdefault(a, []).append(ovr[b])
            avg = {t: sum(v) / len(v) for t, v in opps.items() if len(v) >= 3 and t in ovr}
            order = sorted(avg, key=lambda t: -avg[t])
            out = {t: (i + 1, len(order), avg[t]) for i, t in enumerate(order)}
        self._sos[sport] = out
        return out

    def past_games(self, sport):
        """Every past season's per-team schedule for a sport: {team_name: [(season, game, file_teams)]}."""
        if sport in self._past:
            return self._past[sport]
        import glob
        pattern = (self.rcfg.get("program_history") or {}).get("schedule_glob", "output/{sport}_schedule_*.json")
        we = d(self.week_end)
        cur_start = we.year if we.month >= 8 else we.year - 1     # school year the email falls in

        def is_current(season):
            if "-" in season:                                     # winter: "2026-2027"
                return int(season[:4]) >= cur_start
            return int(season) >= we.year                         # fall/spring: "2026"
        by_team = {}
        for f in sorted(glob.glob(resolve(pattern.format(sport=sport)))):
            season = os.path.basename(f).rsplit("_", 1)[-1][:-5]          # "2019" or "2019-2020"
            if not season[:4].isdigit() or is_current(season):
                continue                                                # only past seasons
            try:
                with open(f, encoding="utf-8") as fh:
                    teams = (json.load(fh) or {}).get("teams") or {}
            except Exception:
                continue
            for name, rows in teams.items():
                by_team.setdefault(name, []).append((season, rows, teams))
        self._past[sport] = by_team
        return by_team

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

# Kinds that report a one-time event: repeating the kind is fine, only the
# exact same headline counts as a repeat.
EVENT_KINDS = {"district_title", "state_title", "playoff_win", "playoff_loss", "eliminated", "upset", "best_win",
               "beat_projection"}


def project(rcfg, sport, me, opp, la):
    """Same formula as the Matchup Predictor: OFF - opponent DEF + league average.
    Returns raw expected points for each side plus a display score (None for
    volleyball, where only the favorite is shown)."""
    if la is None or me.get("off_rating") is None or opp.get("off_rating") is None \
            or me.get("def_rating") is None or opp.get("def_rating") is None:
        return None
    a = me["off_rating"] - opp["def_rating"] + la
    b = opp["off_rating"] - me["def_rating"] + la
    if sport == "football":
        return {"a": a, "b": b, "score": predict_football(me, opp, la)}
    if rcfg["sports"][sport].get("project_scores"):
        sc = (max(0, round(a)), max(0, round(b)))
        return {"a": a, "b": b, "score": sc if sc[0] != sc[1] else None}
    return {"a": a, "b": b, "score": None}


def story(kind, score, headline, text=""):
    return {"kind": kind, "score": score, "headline": headline, "text": text}


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
    if len(past) < 6:          # too little history to call anything a program record
        return None
    first = min(y for y, _ in past)
    win_list = sorted((v["wins"] for _, v in past), reverse=True)
    beat = [y for y, v in past if v["wins"] > wins]
    tie = [y for y, v in past if v["wins"] == wins]
    ovr_better = [y for y, v in past if (v.get("ovr_rating") or -999) > ovr] if ovr is not None else []
    gp = wins + losses
    my_pct = wins / gp if gp else 0
    pcts = [v["wins"] / (v["wins"] + (v.get("losses") or 0)) for _, v in past if v["wins"] + (v.get("losses") or 0)]
    pct_rank = sum(1 for p in pcts if p > my_pct + 1e-9) + 1
    return {"first": first, "seasons": len(past), "wins_rank": len(beat) + 1, "pct_rank": pct_rank,
            "season_wins": sorted((y, v["wins"]) for y, v in past),
            "better_years": sorted(beat), "tied_years": sorted(tie),
            "ovr_rank": len(ovr_better) + 1 if ovr is not None else None,
            "ovr_better_years": sorted(ovr_better), "max_wins": win_list[0]}


def football_round(rcfg, iso):
    """Postseason round for a football game date, from the config's round dates."""
    rounds = (rcfg.get("football") or {}).get("rounds") or {}
    x = d(iso)
    for day, label in rounds.items():
        if 0 <= (x - d(str(day))).days <= 1:     # Friday or Saturday (not a Thanksgiving Thursday game)
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
                     # A game after the report week counts as not yet played (matters for re-runs of past weeks).
                     "played": is_played(g) and g["date"] <= data.week_end, "forfeit": g.get("forfeit"), "ot": g.get("overtime")})
    rec = list(data.official_record(sport, team) or live["records"].get(team, [0, 0, 0]))
    played = [g for g in mine if g["played"] and g["date"] <= data.week_end]
    this_week = [g for g in played if data.week_start <= g["date"] <= data.week_end]
    horizon = (d(data.week_end) + dt.timedelta(days=7)).isoformat()
    upcoming = [g for g in mine if not g["played"] and data.week_end < g["date"] <= horizon]
    if not upcoming:   # e.g. a Thanksgiving rivalry game two weeks out
        far = (d(data.week_end) + dt.timedelta(days=21)).isoformat()
        upcoming = [g for g in mine if not g["played"] and data.week_end < g["date"] <= far][:1]
    last_played = played[-1]["date"] if played else None
    any_future = any(not g["played"] and g["date"] > data.week_end for g in mine)

    # Is the season over for this team?
    wrapup_days = rcfg.get("wrapup_days", 21)
    # A finished team stays in "Seasons wrapped" until a few weeks after the
    # sport's last game statewide (state finals), not just its own last game.
    sport_last = max((g["date"] for g in live["games"] if is_played(g) and g["date"] <= data.week_end), default=None)
    sport_has_future = any(g["date"] > data.week_end for g in live["games"])
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

    # Every game this season with its pre-game projection (for the result
    # breakdown, season-best tags and the report page's schedule table).
    cutoff = meta.get("blowout_margin") or 999
    season_games = []
    for g in mine:
        if not g["played"] and g["date"] <= data.week_end:
            continue                     # a past game with no score (cancelled or never reported)
        row = {"date": g["date"], "opp": clean_name(g["opp"]), "opp_raw": g["opp"], "played": g["played"],
               "us": g["us"] if g["played"] else None, "them": g["them"] if g["played"] else None,
               "opp_class_rank": opp_rank(g["opp"]),
               "round": football_round(rcfg, g["date"]) if sport == "football" else None}
        if g["played"]:
            row["result"] = "W" if g["us"] > g["them"] else ("L" if g["us"] < g["them"] else "T")
        pr = data.pregame(sport, team, g["opp"], g["date"])
        if pr:
            row["proj"] = list(pr["score"]) if pr.get("score") else None
            row["proj_favored"] = team if pr["a"] >= pr["b"] else clean_name(g["opp"])
            if pr.get("score") and (min(pr["a"], pr["b"]) < 0 or abs(pr["a"] - pr["b"]) >= cutoff):
                row["proj"] = None                      # don't print a lopsided or impossible score
                row["heavy_favorite"] = row["proj_favored"]
            # Compare only against sensible projections (no negative or blowout ones).
            if g["played"] and pr.get("score") and min(pr["a"], pr["b"]) >= 0 and abs(pr["a"] - pr["b"]) < cutoff:
                row["off_delta"] = round(g["us"] - pr["a"])
                row["def_delta"] = round(pr["b"] - g["them"])
                row["margin_delta"] = round((g["us"] - g["them"]) - (pr["a"] - pr["b"]))
        season_games.append(row)
    sec["season_games"] = season_games
    sec["trend"] = data.trend(sport, team)
    compared = [x for x in season_games if x.get("off_delta") is not None]

    for g in this_week:
        row = next(x for x in season_games if x["date"] == g["date"] and x["opp_raw"] == g["opp"])
        r = {**row, "forfeit": g["forfeit"], "ot": g["ot"]}
        tags = []
        if len(compared) >= 4 and r.get("off_delta") is not None:
            if r["off_delta"] == max(x["off_delta"] for x in compared) and r["off_delta"] > 0:
                tags.append("best offensive game of the season")
            if r["def_delta"] == max(x["def_delta"] for x in compared) and r["def_delta"] > 0:
                tags.append("best defensive game of the season")
            if not tags and r["margin_delta"] == max(x["margin_delta"] for x in compared) and r["margin_delta"] > 0:
                tags.append("best all-around game of the season")
        if r.get("result") == "W":
            if r.get("proj_favored"):
                if r["proj_favored"] != team:
                    tags.insert(0, "won as the underdog")
        r["tags"] = tags
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
        if n.get("proj") and abs(n["proj"][0] - n["proj"][1]) >= (meta.get("blowout_margin") or 999):
            n["heavy_favorite"] = team if n["proj"][0] > n["proj"][1] else g["opp"]
        n["meetings"] = last_meetings(data, sport, slug, team, g["opp"], mine)
        # Same key for both schools' pages, so picks on a game add up in one place.
        n["game_key"] = f"{sport}|{g['date']}|" + "|".join(sorted([team, g["opp"]]))
        n["opp_raw"] = g["opp"]                       # exact name in game_key (picks); "opp" gets cleaned for display
        for k in ("opp", "favored", "heavy_favorite"):
            if n.get(k):
                n[k] = clean_name(n[k])
        sec["next"].append(n)

    # Knocked out of the playoffs: lost its last game, nothing left on its
    # schedule, while other teams in the sport still have games to play.
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
    sec["season_year"] = int(data.week_end[:4])
    if sec.get("classification") and sec.get("district"):
        mates = sorted((t for t in teams if t.get("classification") == sec["classification"] and t.get("district") == sec["district"]),
                       key=lambda t: -(t.get("ovr_rating") or -999))
        sec["district_table"] = [{"team": t["school"], "class_rank": class_rank(teams, t["school"])[1],
                                  "record": fmt_rec(list(data.official_record(sport, t["school"]) or live["records"].get(t["school"], [0, 0, 0]))),
                                  "ovr": t.get("ovr_rating"), "off": t.get("off_rating"), "def": t.get("def_rating"),
                                  "me": t["school"] == team} for t in mates]
    remaining = sum(1 for g in mine if not g["played"] and g["date"] > data.week_end)
    sec["sport_finished"] = (sport_last or "") >= data.week_start and not sport_has_future
    add_season_arc(data, sec, sport, team, teams, played, remaining, school)
    add_live_stories(sec, rcfg, school)
    # Is there anything new this week? (used by the quiet-week rule)
    sec["news"] = bool(this_week) or bool(upcoming and upcoming[0]["date"] <= horizon) \
        or (state == "live" and sec["class_rank_prev"] is not None and sec["class_rank_prev"] != sec["class_rank"]) \
        or (state == "wrapup" and ((last_played or "") >= data.week_start
                                   or ((sport_last or "") >= data.week_start and not sport_has_future)))
    if state in ("offseason", "none"):
        sec["stories"] = []
    return sec


def season_phase(rcfg, sport, week_end, played_n, state):
    """early / mid / late / playoffs for a live team; otherwise the state itself."""
    if state != "live":
        return state
    ps = rcfg["sports"][sport].get("postseason_start")
    we = d(week_end)
    if ps and we >= d(str(ps)):
        return "playoffs"
    if ps and (d(str(ps)) - we).days <= 21:
        return "late"
    return "early" if played_n <= 3 else "mid"


def add_season_arc(data, sec, sport, team, teams, played, remaining, school):
    """Phase-specific extras, so the email reads differently as a season goes on:
    early = best win so far, mid = schedule strength (+ Quad record for football),
    late = district outlook (+ seed projection for football), every phase = milestone watch."""
    rcfg = data.rcfg
    name, label = school["name"], sec["label"].lower()
    phase = season_phase(rcfg, sport, data.week_end, len(played), sec["state"])
    sec["phase"] = phase
    cls, dist = sec.get("classification"), sec.get("district")

    # Best win so far: beat the opponent ranked highest within its own class.
    if phase in ("early", "mid"):
        best = None
        for g in played:
            if g["us"] > g["them"]:
                _, r, n = class_rank(teams, g["opp"])
                if r and (best is None or r < best["rank"]):
                    opp = next(t for t in teams if t["school"] == g["opp"])
                    best = {"opp": clean_name(g["opp"]), "rank": r, "cls": opp.get("classification"),
                            "us": g["us"], "them": g["them"], "date": g["date"]}
        if best:
            sec["best_win"] = best
            if best["rank"] <= 10 and best["date"] >= data.week_start:
                sec["stories"].append(story("best_win", 52, f"{name} {label} beats #{best['rank']} {best['opp']} for its best win of the season",
                                            f"{best['us']}–{best['them']} over the #{best['rank']} team in Class {best['cls']}."))

    # Schedule strength (and football Quad records) from midseason on.
    if phase in ("mid", "late", "playoffs"):
        sos = data.sos(sport).get(team)
        if sos and sos[0] <= sos[1] / 2:          # only show it when it's a point of pride
            sec["sos"] = {"rank": sos[0], "of": sos[1]}
            if sos[0] <= max(5, sos[1] // 20):
                sec["stories"].append(story("sos", 36, f"{name} {label} has played the #{sos[0]} toughest schedule in Missouri"))
        if sport == "football" and data.quads:
            q = next((t for t in data.quads.get("teams", []) if t.get("school") == team), None)
            if q:
                sec["quads"] = [q.get("q1"), q.get("q2"), q.get("q3"), q.get("q4")]

    # District outlook: where the team rates among its district.
    if phase in ("mid", "late") and cls and dist:
        mates = [t for t in teams if t.get("classification") == cls and t.get("district") == dist]
        mates.sort(key=lambda t: -(t.get("ovr_rating") or -999))
        recs = data.live(sport)["records"]
        sec["district_mates"] = [{"team": t["school"], "class_rank": class_rank(teams, t["school"])[1],
                                  "record": fmt_rec(list(data.official_record(sport, t["school"]) or recs.get(t["school"], [0, 0, 0]))),
                                  "me": t["school"] == team} for t in mates]
        my_pos = next(i + 1 for i, t in enumerate(mates) if t["school"] == team)
        sec["district_pos"] = (my_pos, len(mates))
        if phase == "late" and my_pos == 1 and len(mates) >= 3:
            sec["stories"].append(story("district_top", 46, f"{name} {label} heads into districts as the top-rated team in Class {cls} District {dist}"))
    if phase == "late" and sport == "football" and data.seeds:
        dd = (data.seeds.get("districts") or {}).get(f"class{cls}_district{dist}", {})
        row = next((t for t in dd.get("teams", []) if t.get("team") == team), None)
        if row and row.get("seed_probs"):
            probs = row["seed_probs"]
            likely = max(range(len(probs)), key=lambda i: probs[i])
            sec["seed_watch"] = {"seed": likely + 1, "prob": probs[likely], "top2": row.get("top2_prob")}

    # Milestone watch: a few more wins would be the program's most since some year.
    hc = sec.get("history")
    if hc and remaining and phase != "wrapup":
        season_year = int(data.week_end[:4])
        for n in range(1, min(3, remaining) + 1):
            tot = sec["wins"] + n
            ge = [y for y, w in hc["season_wins"] if w >= tot]
            if not ge or season_year - max(ge) >= 5:
                words = {1: "One more win", 2: "Two more wins", 3: "Three more wins"}[n]
                if not ge:
                    txt = f"{words} would give {name} the most {label} wins in our records, which go back to {hc['first']}."
                    sc = 44
                else:
                    txt = f"{words} would give {name} its most {label} wins since {max(ge)}."
                    sc = 34
                sec["milestone"] = txt
                gap = f"{['one', 'two', 'three'][n - 1]} win{'s' if n > 1 else ''}"
                head = (f"{name} {label} is {gap} from its most since {max(ge)}" if ge
                        else f"{name} {label} is {gap} from a program record")
                sec["stories"].append(story("milestone", sc, head, txt))
                break


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
            S.append(story("district_title", 95, f"{name} wins the Class {cls} District {school_district(sec)} {label} title",
                           f"The {label} team beat {r['opp']} {r['us']}–{r['them']} in the district final."))
        elif r["result"] == "W" and rnd.lower() == "state final":
            S.append(story("state_title", 100, f"{name} wins the Class {cls} {label} state title!",
                           f"A {r['us']}–{r['them']} win over {r['opp']} in the state final."))
        elif r["result"] == "W":
            nxt = sec["next"][0]["round"] if sec["next"] else None
            S.append(story("playoff_win", 85, f"{name} {label} wins its {rnd.lower()}" + (f" and moves on to the {nxt.lower()}" if nxt else ""),
                           f"Beat {r['opp']} {r['us']}–{r['them']}."))
        elif r["result"] == "L" and sec["wins"] > sec["losses"]:     # only a winning season's end is a headline
            S.append(story("playoff_loss", 80, f"{name}'s {label} season ends in the {rnd.lower()} at {sec['record']}",
                           f"Lost to {r['opp']} {r['us']}–{r['them']}."))
    big = rcfg["sports"][sec["sport"]].get("beat_margin")
    for r in sec["results"]:
        if big and r["result"] == "W" and (r.get("margin_delta") or 0) >= big:
            S.append(story("beat_projection", 50, f"{name} {label} beats its projection by {r['margin_delta']} in a {r['us']}–{r['them']} win over {r['opp']}",
                           f"Our ratings projected {r['proj'][0]}–{r['proj'][1]} before the game."))
    el = sec.get("eliminated")
    if el and not el.get("round") and sec["wins"] > sec["losses"]:     # football rounds are handled above
        top = sec["class_rank"] and sec["class_rank"] <= 10
        S.append(story("eliminated", 74 if top else 58, f"{name} {label}'s season ends at {sec['record']}",
                       f"A {el['us']}–{el['them']} playoff loss to {el['opp']}"
                       + (f" ends a season that finished #{sec['class_rank']} in Class {cls}." if sec["class_rank"] else ".")))
    # Unbeaten
    if sec["losses"] == 0 and sec["wins"] >= 5:
        S.append(story("unbeaten", 70, f"{name} {label} is still unbeaten at {sec['record']}"))
    # Win totals in the school's history
    hc = sec.get("history")
    # Only a record if the win percentage is also near the top: some sports play
    # more games now than in early seasons, which inflates raw win totals.
    if hc and sec["wins"] >= 6 and hc.get("pct_rank", 99) <= 2:
        if hc["wins_rank"] == 1:
            S.append(story("most_wins", 78, f"{name} {label} has its most wins in our records ({sec['wins']}, back to {hc['first']})"))
        elif hc["wins_rank"] <= 3:
            S.append(story("nth_wins", 55, f"{name} {label}'s {sec['wins']} wins are its {ordinal_word(hc['wins_rank'])}most since {hc['first']}"))
    # Rank movement
    if sec["class_rank"] and sec["class_rank_prev"]:
        mv = sec["class_rank_prev"] - sec["class_rank"]
        if mv >= 3:      # drops still show in the rank box, but never as the subject line
            S.append(story("rank_move", min(75, 42 + 3 * mv),
                           f"{name} {label} climbs {mv} spots to #{sec['class_rank']} in Class {cls}"))
    # Upset win: beat a team ranked 10+ spots better in its own class list
    for r in sec["results"]:
        if r["result"] == "W" and r["opp_class_rank"] and sec["class_rank"] and r["opp_class_rank"] + 10 <= sec["class_rank"]:
            S.append(story("upset", 62, f"{name} {label} upsets #{r['opp_class_rank']} {r['opp']}", f"{r['us']}–{r['them']}."))
    # Highly ranked
    if sec["class_rank"] and sec["class_rank"] <= 5:
        verb = "finishes the season" if sec["state"] == "wrapup" else "is ranked"
        S.append(story("top5", 68 if sec["class_rank"] == 1 else 50 - sec["class_rank"],     # a #1 finish beats most stories
                       f"{name} {label} {verb} #{sec['class_rank']} in Class {cls}"))
    if sec["state"] == "wrapup" and hc and hc.get("ovr_rank") == 1:
        S.append(story("best_rating", 66, f"{name} {label} finishes with its best rating in our records",
                       f"The highest rating for this program since our data starts in {hc['first']}."))
    # Only a winning season makes the wrap-up a headline; a rough season still
    # shows under "Seasons wrapped", just not as the subject line.
    if sec["state"] == "wrapup" and sec.get("news") is None and sec.get("sport_finished") and sec["class_rank"] \
            and sec["class_rank"] <= (sec["class_size"] or 0) / 2:
        S.append(story("final_rank", 34, f"{name} {label} finishes #{sec['class_rank']} in Class {cls}"))
    if sec["state"] == "wrapup" and sec["wins"] > sec["losses"]:
        S.append(story("wrapup", 30, f"{name} {label} wraps up at {sec['record']}"))


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
    same = sorted([r for r in rows if r[2] == cls], key=lambda r: -r[0]) if cls else []
    crank = next((i + 1 for i, r in enumerate(same) if r[1] == slug), None) if cls else None
    sec.update({"classification": cls, "class_rank": crank, "class_size": len(same),
                "record": f"{last.get('wins', 0)}-{last.get('losses', 0)}",
                "wins": last.get("wins"), "losses": last.get("losses"),
                "last_season": meta.get("last_season_label", "last season"),
                "starts": str(meta["start"]) if meta.get("start") else None})
    hc = history_context(data, sport, slug, last.get("wins") or 0, last.get("losses") or 0, None)
    if hc and (last.get("wins") or 0) > hc["max_wins"] and hc.get("pct_rank", 99) <= 2 \
            and (last.get("wins") or 0) > (last.get("losses") or 0):
        sec["notes"].append(f"the most wins in our records, which start in {hc['first']}–{str(hc['first'] + 1)[2:]}"
                            if meta.get("season") == "winter" else f"the most wins in our records, which start in {hc['first']}")
    days_to_start = (start - we).days if start else None
    sec["news"] = sec["state"] == "preseason" and days_to_start is not None and (
        days_to_start > pre_days - 7 or 0 <= days_to_start <= 7)      # first week shown, or the week it starts
    if sec["state"] == "preseason" and crank and crank <= len(same) / 2:   # top half of the class only
        sc = 40 if crank <= 5 else (32 if crank <= 15 else 20)
        sec["stories"].append(story("preseason", sc, f"{school['name']} {meta['label'].lower()} opens after a #{crank} finish in Class {cls}"))
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


def last_meetings(data, sport, slug, team, opp, current_rows, limit=3):
    """Most recent meetings with this opponent: this season first, then past seasons."""
    out = [{"season": int(data.week_end[:4]), "date": g["date"], "us": g["us"], "them": g["them"]}
           for g in current_rows if g["opp"] == opp and g["played"]]
    school = data.schools[slug]
    own = {school.get("name"), school.get("mshsaa_name"), team} - {None}
    past = data.past_games(sport)
    for n in past:
        if n in own or data.aliases.get(n) == slug:
            for season, rows, _ in past[n]:
                for g in rows:
                    if g.get("opponent") == opp and g.get("team_score") is not None:
                        out.append({"season": int(season[:4]), "date": g["date"][:10],
                                    "us": g["team_score"], "them": g["opp_score"]})
    out.sort(key=lambda x: x["date"], reverse=True)
    seen, uniq = set(), []
    for x in out:
        if x["date"] not in seen:
            seen.add(x["date"])
            uniq.append(x)
    w = sum(1 for x in uniq if x["us"] > x["them"])
    l = sum(1 for x in uniq if x["us"] < x["them"])
    return {"games": uniq[:limit], "w": w, "l": l, "since": min((x["season"] for x in uniq), default=None)} if uniq else None


def program_history(data, slug, sport):
    """The school's best win from the coming week (Mon-Sun) in a past season."""
    school = data.schools[slug]
    own = {school.get("name"), school.get("mshsaa_name")} - {None}
    past = data.past_games(sport)
    names = [n for n in past if n in own or data.aliases.get(n) == slug
             or (" with " in n and any(p.strip() in own for part in n.split(" with ") for p in part.split(",")))]
    a = d(data.as_of)
    best = None
    for n in names:
        for season, rows, teams in past[n]:
            for g in rows:
                if g.get("result") != "W" or g.get("team_score") is None or g.get("forfeit"):
                    continue
                if re.search(r"\(.*,\s*[A-Z]{2}\)\s*$", g.get("opponent", "")):   # "Piggott(Piggott, AR)": out-of-state entry
                    continue
                gd = d(g["date"])
                hit = False
                for yr in (a.year - 1, a.year, a.year + 1):
                    try:
                        if 0 <= (gd.replace(year=yr) - a).days <= 6:
                            hit = True
                    except ValueError:
                        pass
                if not hit:
                    continue
                opp_rows = teams.get(g["opponent"]) or []
                ow = sum(1 for x in opp_rows if x.get("result") == "W")
                ol = sum(1 for x in opp_rows if x.get("result") == "L")
                key = (ow > ol, g.get("ovr_delta") or 0, g["team_score"] - g["opp_score"])
                if best is None or key > best["key"]:
                    best = {"key": key, "year": gd.year, "date": g["date"], "sport": sport, "opp": clean_name(g["opponent"]),
                            "us": g["team_score"], "them": g["opp_score"],
                            "opp_record": f"{ow}-{ol}" if ow + ol else None, "opp_winning": ow > ol}
    return best


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
        out["stories"].append(story("cup_climb", 40, f"{data.schools[slug]['name']} climbs {ch} spots in the All MO Sports Cup"))
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
    lum_hex = lambda h: lum(tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)))
    if accent == primary or abs(lum_hex(accent) - lum_hex(primary)) < 70:   # label must stand out on the header
        accent = default[1] if abs(lum_hex(default[1]) - lum_hex(primary)) >= 70 else "#ffffff"
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
    out.append(rank_sparkline(sec.get("trend") or [], primary))

    if sec["results"]:
        lines = []
        for r in sec["results"]:
            opp = esc(r["opp"]) + (f" (#{r['opp_class_rank']} in class)" if r.get("opp_class_rank") else "")
            line = f'<strong>{esc(score_text(r))}</strong> vs {opp}'
            if r.get("round"):
                line += f' · {esc(r["round"])}'
            if r.get("off_delta") is not None:
                od, dd = r["off_delta"], r["def_delta"]
                sign = lambda v: f"+{v}" if v > 0 else (f"−{abs(v)}" if v < 0 else "even")
                line += (f'<br><span style="color:#5b6577;">Projected {r["proj"][0]}–{r["proj"][1]}. '
                         f'Offense {sign(od)}, defense {sign(dd)} vs. the projection.</span>')
            elif r.get("proj_favored") and not r.get("proj") and r["result"] in ("W", "L"):
                fav = r["proj_favored"] == sec["team"]
                if r["result"] == "L" and fav:
                    line += '<br><span style="color:#5b6577;">Lost as the favorite.</span>'
            for t in r.get("tags") or []:
                line += (f'<br><span style="display:inline-block;margin-top:3px;background:#e8f3ea;color:#1e7b34;'
                         f'font-size:11px;font-weight:bold;padding:2px 8px;border-radius:10px;">{esc(t.capitalize())}</span>')
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
        if n.get("heavy_favorite"):
            fav = n["heavy_favorite"]
            body.append(f"Our ratings have <strong>{esc(fav)}</strong> heavily favored.")
            n = {**n, "proj": None, "win_prob": None, "favored": None}
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
        mt = n.get("meetings")
        if mt and mt["games"]:
            g0 = mt["games"][0]
            res = "W" if g0["us"] > g0["them"] else ("L" if g0["us"] < g0["them"] else "T")
            body.append(f"Last meeting: {res} {g0['us']}–{g0['them']} ({nice_date(g0['date'], weekday=False)}, {g0['date'][:4]}).")
            if not n.get("h2h") and mt["w"] + mt["l"] >= 3:
                body.append(f"Series since {mt['since']}: {mt['w']}–{mt['l']}.")
        link = rcfg["sports"][sec["sport"]].get("preview_page")
        out.append(f'<div style="border:2px solid {primary};border-radius:10px;padding:12px 14px;margin-top:14px;">'
                   f'<div style="font-size:11px;color:{primary};font-weight:bold;text-transform:uppercase;letter-spacing:.5px;">{esc(tag)}</div>'
                   f'<div style="font-size:16px;font-weight:bold;margin:4px 0;">{esc(sec["team"])} vs {esc(n["opp"])}</div>'
                   f'<div style="font-size:13px;color:#3d4657;line-height:1.6;">{" ".join(body)}</div>'
                   + (f'<div style="margin-top:8px;"><a href="{esc(link)}" style="font-size:13px;color:{primary};font-weight:bold;text-decoration:none;">See the matchup preview →</a></div>' if link else "")
                   + '</div>')

    if sec.get("eliminated"):
        out.append('<div style="font-size:13px;color:#5b6577;margin-top:12px;">Season over. Thanks for following this team.</div>')

    extras = []
    b = sec.get("best_win")
    if b:
        extras.append(f"Best win so far: {b['us']}–{b['them']} over {b['opp']} (#{b['rank']} in Class {b['cls']}).")
    if sec.get("sos"):
        extras.append(f"Schedule strength: #{sec['sos']['rank']} of {sec['sos']['of']} statewide.")
    if sec.get("quads") and any(sec["quads"]):
        extras.append("Quad records: " + " · ".join(f"Q{i + 1} {q}" for i, q in enumerate(sec["quads"]) if q) + ".")
    sw = sec.get("seed_watch")
    if sw:
        extras.append(f"Most likely district seed: #{sw['seed']} ({pct(sw['prob'])})"
                      + (f"; chance of a top-2 seed: {pct(sw['top2'])}." if sw.get("top2") is not None else "."))
    if extras:
        out.append('<div style="font-size:13px;color:#3d4657;line-height:1.7;margin-top:12px;">'
                   + "".join(f"<div>{esc(x)}</div>" for x in extras) + "</div>")
    if sec.get("district_mates"):
        rows = []
        for i, t in enumerate(sec["district_mates"][:8]):
            st = "font-weight:bold;" if t["me"] else ""
            rows.append(f'<tr><td style="padding:4px 0;color:#8a92a3;{st}" width="22">{i + 1}</td>'
                        f'<td style="padding:4px 0;{st}">{esc(t["team"])}</td>'
                        f'<td align="right" style="padding:4px 0;color:#5b6577;{st}">{esc(t["record"])}</td>'
                        f'<td align="right" style="padding:4px 0;color:#5b6577;white-space:nowrap;{st}" width="90">#{t["class_rank"]} in class</td></tr>')
        out.append(f'<div style="font-size:12px;color:#5b6577;margin-top:14px;">Class {sec["classification"]} District {sec["district"]}, by rating</div>'
                   f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;border-top:1px solid #f0f2f6;">{"".join(rows)}</table>')
    if sec.get("milestone"):
        out.append(f'<div style="border-left:3px solid {primary};padding:6px 10px;margin-top:12px;font-size:13px;color:#3d4657;border-radius:0;">'
                   f'<strong>Milestone watch:</strong> {esc(sec["milestone"])}</div>')

    # Only show playoff odds while they mean something (Final Four chance of 1% or more).
    if sec.get("odds") and sec["odds"].get("final_four") not in (None, "0%", "<1%") and not sec.get("eliminated"):
        o = sec["odds"]
        out.append(f'<div style="font-size:12px;color:#5b6577;margin-top:10px;">Playoff odds: Final Four {esc(o.get("final_four") or "–")} · '
                   f'State title {esc(o.get("state_title") or "–")}</div>')

    hc = sec.get("history")
    if hc and sec["wins"] >= 5 and hc["wins_rank"] <= 3 and hc.get("pct_rank", 99) <= 3:
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


def rank_sparkline(trend, color):
    """Class rank by week as a row of bars (plain HTML table, so it shows in
    every email client; taller bar = better rank)."""
    pts = trend[-8:]
    if len(pts) < 3:
        return ""
    cells, labels = [], []
    for p in pts:
        h = max(4, round(40 * (p["class_size"] - p["ovr"] + 1) / p["class_size"]))
        cells.append(f'<td valign="bottom" align="center" style="padding:0 3px;"><div style="width:22px;height:{h}px;'
                     f'background:{color};border-radius:3px 3px 0 0;font-size:0;line-height:0;">&nbsp;</div></td>')
        labels.append(f'<td align="center" style="padding:2px 3px 0;font-size:10px;color:#5b6577;">#{p["ovr"]}</td>')
    return ('<div style="margin-top:12px;"><div style="font-size:12px;color:#5b6577;margin-bottom:4px;">Class rank by week</div>'
            f'<table role="presentation" cellpadding="0" cellspacing="0"><tr style="height:42px;">{"".join(cells)}</tr>'
            f'<tr>{"".join(labels)}</tr></table></div>')


def render_wrapups(secs):
    rows = []
    for s in secs:
        co = f' <span style="color:#8a92a3;">(co-op)</span>' if s.get("co_op") else ""
        rank = f" · #{s['class_rank']} of {s['class_size']} in Class {s['classification']}" if s.get("class_rank") else ""
        note = ""
        hc = s.get("history")
        if hc and hc.get("ovr_rank") == 1:
            note = f"Best rating in our records (since {hc['first']})"
        elif hc and hc.get("ovr_better_years") and max(hc["ovr_better_years"]) <= int(s.get("season_year", 9999)) - 4:
            note = f"Best rating since {max(hc['ovr_better_years'])}"
        rows.append(f'<tr><td style="padding:6px 0;border-bottom:1px solid #f0f2f6;">{esc(s["label"])}{co}'
                    + (f'<div style="font-size:11px;color:#1e7b34;">{esc(note)}</div>' if note else "")
                    + f'</td><td align="right" style="padding:6px 0;border-bottom:1px solid #f0f2f6;color:#5b6577;" valign="top">{esc(s["record"] + rank)}</td></tr>')
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


def render_school_block(data, slug, secs, cup, show_header_label, history_item=None):
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
    url = (rcfg.get("report_page_url") or "").replace("{slug}", slug)
    if url:
        parts.append(f'<tr><td style="padding:16px 22px;border-bottom:1px solid #e6e9ef;" align="center">'
                     f'<a href="{esc(url)}" style="display:inline-block;background:{primary};color:#ffffff;font-size:14px;font-weight:bold;'
                     f'text-decoration:none;padding:10px 18px;border-radius:8px;">See the full {esc(data.schools[slug]["name"])} report →</a>'
                     '<div style="font-size:11px;color:#6b7385;margin-top:6px;">Trend charts, every game vs. our projection, and the district table</div></td></tr>')
    if history_item:
        h = history_item
        lab = rcfg["sports"][h["sport"]]["label"]
        extra = f" {h['opp']} finished {h['opp_record']} that season." if h.get("opp_winning") and h.get("opp_record") else ""
        parts.append('<tr><td style="padding:20px 22px;border-bottom:1px solid #e6e9ef;">'
                     '<div style="font-size:16px;font-weight:bold;margin-bottom:4px;">📜 This week in program history</div>'
                     f'<div style="font-size:13px;line-height:1.6;color:#3d4657;"><strong>{esc(nice_date(h["date"], weekday=False))}, {h["year"]} · {esc(lab)}:</strong> '
                     f'{esc(data.schools[slug]["name"])} beat {esc(h["opp"])} {h["us"]}–{h["them"]}.{esc(extra)}</div></td></tr>')
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

PAGE_FIELDS = ["sport", "label", "state", "phase", "team", "co_op", "classification", "district", "record",
               "class_rank", "class_size", "class_rank_prev", "state_rank", "state_size", "ovr", "ovr_prev",
               "season_games", "trend", "next", "district_table", "milestone", "best_win", "sos", "quads",
               "seed_watch", "odds", "eliminated", "last_season", "notes", "starts", "results"]


def strip_raw(rows):
    return [{k: v for k, v in r.items() if k != "opp_raw"} for r in rows]


def page_sport(x):
    out = {f: (strip_raw(x.get(f)) if f == "season_games" else x.get(f)) for f in PAGE_FIELDS if x.get(f) is not None}
    if x.get("stories"):
        out["headline"] = max(x["stories"], key=lambda st: st["score"])["headline"]   # the sport's own top story
    return out


def build_pages(data, sections, cups, out_folder):
    """One JSON file per school for the web report page, plus index.json for the school search."""
    rcfg = data.rcfg
    os.makedirs(out_folder, exist_ok=True)
    index = []
    for slug in sorted(data.schools):
        secs = []
        for k in SPORT_ORDER:
            if (slug, k) not in sections:
                try:
                    sections[(slug, k)] = build_section(data, k, slug)
                except Exception as e:
                    print(f"[page {slug}/{k}] FAILED: {e}", file=sys.stderr)
                    continue
            sec = sections[(slug, k)]
            if sec["state"] in ("live", "wrapup", "preseason"):
                secs.append(sec)
        if not secs:
            continue
        secs.sort(key=lambda x: (STATE_ORDER[x["state"]], SPORT_ORDER.index(x["sport"])))
        stories = [st for x in secs for st in x["stories"]]
        cup = cups.get(slug) if slug in cups else cup_section(data, slug)
        if cup:
            stories += cup["stories"]
        hist = None
        for k in (rcfg.get("program_history") or {}).get("sports", []):
            h = program_history(data, slug, k)
            if h and (hist is None or h["key"] > hist["key"]):
                hist = h
        primary, accent = logo_colors(slug, rcfg)
        page = {"slug": slug, "school": data.schools[slug]["name"], "week_end": data.week_end, "as_of": data.as_of,
                "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "colors": {"primary": primary, "accent": accent},
                "logo": (rcfg.get("logo_base_url") or "") + slug + ".png",
                "big_story": max(stories, key=lambda st: st["score"]) if stories else None,
                "sports": [page_sport(x) for x in secs],
                "cup": {k: v for k, v in cup.items() if k != "stories"} if cup else None,
                "history": {k: v for k, v in hist.items() if k != "key"} if hist else None}
        with open(os.path.join(out_folder, f"{slug}.json"), "w", encoding="utf-8") as f:
            json.dump(page, f, separators=(",", ":"), default=str)
        index.append({"slug": slug, "name": page["school"]})
    with open(os.path.join(out_folder, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"week_end": data.week_end, "schools": index}, f, separators=(",", ":"))
    return len(index)


def follow_signature(follows):
    """Same follows -> same email, so memory is keyed by the follow set, not the person."""
    parts = sorted(f"{fo['school']}:{'all' if fo['sports'] == 'all' else ','.join(sorted(fo['sports']))}" for fo in follows)
    return "|".join(parts)


def last_full_week(today):
    end = today - dt.timedelta(days=today.weekday() + 1)   # previous Sunday
    return end - dt.timedelta(days=6), end


def main():
    ap = argparse.ArgumentParser(description="Build per-school weekly report emails.")
    ap.add_argument("--config", default=os.path.join(HERE, "newsletter_config.yaml"))
    ap.add_argument("--out", default=os.path.join(HERE, "newsletter_output"))
    ap.add_argument("--week-ending", help="Sunday the week ends on, YYYY-MM-DD (default: last full week)")
    ap.add_argument("--follows", help="JSON file: [{id, email, follows: [{school, sports}]}]")
    ap.add_argument("--all-schools", action="store_true",
                    help="Test: one 'everything' email for every school in schools.json")
    ap.add_argument("--school", help="Quick test: one school slug")
    ap.add_argument("--pages", action="store_true",
                    help="Also write the web report data (one JSON per school) to school_reports.report_data_dir")
    ap.add_argument("--pages-out", help="Folder for --pages output (default: the config's report_data_dir)")
    ap.add_argument("--no-state", action="store_true",
                    help="Don't update headline memory / last-sent dates (state.json). Test runs never update it.")
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

    if args.all_schools:
        sch = load(rcfg["schools_file"], {})
        sch = sch.get("schools", sch)
        slugs = sorted(sch) if isinstance(sch, dict) else sorted(x["slug"] for x in sch)
        subs = [{"id": f"{slug}", "email": "", "follows": [{"school": slug, "sports": "all"}]} for slug in slugs]
    elif args.school:
        subs = [{"id": f"{args.school}-{args.sports.replace(',', '-')}", "email": "",
                 "follows": [{"school": args.school,
                              "sports": "all" if args.sports == "all" else args.sports.split(",")}]}]
    elif args.follows:
        with open(args.follows, encoding="utf-8") as f:
            subs = json.load(f)
    elif args.pages:
        subs = []
    else:
        sys.exit("Give --follows FILE, --school SLUG, --all-schools or --pages.")

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
    if subs:
        os.makedirs(os.path.join(out_dir, "emails"), exist_ok=True)
    manifest = {"week_start": ws.isoformat(), "week_end": we.isoformat(), "as_of": as_of,
                "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "emails": [], "skipped": []}

    state_path = os.path.join(args.out, "school_reports", "state.json")
    state = {"headlines": {}, "last_sent": {}}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    save_state = bool(args.follows) and not args.no_state
    week_end = we.isoformat()
    hist_cache = {}

    for sub in subs:
        sig = follow_signature(sub["follows"])
        blocks, stories, included, news = [], [], [], False
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
            # This week in program history: best match across the followed sports.
            hist = None
            for k in keys:
                if k in (rcfg.get("program_history") or {}).get("sports", []):
                    if (slug, k) not in hist_cache:
                        hist_cache[(slug, k)] = program_history(data, slug, k)
                    h = hist_cache[(slug, k)]
                    if h and (hist is None or h["key"] > hist["key"]):
                        hist = h
            cup = cups.get(slug) if fo["sports"] == "all" else None
            h, primary, accent = render_school_block(data, slug, secs, cup, multi, hist)
            blocks.append((slug, h, primary, accent))
            for sec in secs:
                for st in sec["stories"]:
                    stories.append({**st, "sport": sec["sport"]})
                included.append(f"{slug}/{sec['sport']}:{sec['state']}")
                news = news or bool(sec.get("news"))
            if cup:
                stories += [{**st, "sport": "cup"} for st in cup["stories"]]
            if hist:
                lab = rcfg["sports"][hist["sport"]]["label"].lower()
                stories.append({"kind": "history", "sport": hist["sport"], "score": 22,
                                "headline": f"This week in {hist['year']}: {data.schools[slug]['name']} {lab} beat {hist['opp']} {hist['us']}–{hist['them']}",
                                "text": ""})
        # Skip rule: nothing live, wrapping up or about to start.
        if not blocks:
            unknown = [fo["school"] for fo in sub["follows"] if fo["school"] not in data.schools]
            reason = (f"unknown school slug: {', '.join(unknown)}" if unknown and len(unknown) == len(sub["follows"])
                      else "no followed sport is in season this week")
            manifest["skipped"].append({"id": sub["id"], "reason": reason})
            continue
        # Quiet-week rule: nothing new for these sports -> skip, but send at
        # least once every few weeks (a monthly check-in) so followers don't go dark.
        sent_before = [w for w in state["last_sent"].get(sig, []) if w < week_end]
        monthly = False
        if not news:
            gap = rcfg.get("quiet_resend_days", 28)
            if sent_before and (d(week_end) - d(max(sent_before))).days < gap:
                manifest["skipped"].append({"id": sub["id"], "reason": "quiet week: nothing new for the followed sports"})
                continue
            monthly = bool(sent_before)
        # Headline memory: the same story kind (or the same exact headline) in
        # the last few weeks counts against it, so something new leads.
        lookback = rcfg.get("headline_memory_days", 21)
        past = [h for h in state["headlines"].get(sig, [])
                if h["week_end"] < week_end and (d(week_end) - d(h["week_end"])).days <= lookback]
        for st in stories:
            st["base_score"] = st["score"]
            if any(h["headline"] == st["headline"] for h in past):
                st["score"] -= 50
            elif st["kind"] not in EVENT_KINDS and any(h["kind"] == st["kind"] and h.get("sport") == st.get("sport") for h in past):
                st["score"] -= rcfg.get("repeat_penalty", 40)
        stories = [st for st in stories if st["score"] > 0] or []
        big = max(stories, key=lambda st: st["score"]) if stories else None
        page = render_email(data, sub, blocks, big, week_label)
        fn = f"{sub['id']}.html"
        with open(os.path.join(out_dir, "emails", fn), "w", encoding="utf-8") as f:
            f.write(page)
        if big:
            subject = big["headline"]
        else:
            first = data.schools[blocks[0][0]]["name"]
            soon = [x.split(":")[0].split("/")[1] for x in included if x.endswith(":preseason")]
            sp = {rcfg["sports"][k].get("season") for k in soon}
            subject = (f"Your {first} report: {'basketball' if sp == {'winter'} else 'a new'} season is almost here"
                       if soon else f"Your {first} weekly report")
        manifest["emails"].append({"id": sub["id"], "email": sub.get("email", ""), "subject": subject,
                                   "file": f"emails/{fn}", "sections": included, "news": news, "monthly": monthly,
                                   "other_story_ideas": [st["headline"] for st in sorted(stories, key=lambda st: -st["score"])[1:4]]})
        # Remember what was sent (keyed by the follow set, never by person).
        hl = [h for h in state["headlines"].get(sig, []) if h["week_end"] != week_end]
        if big:
            hl.append({"week_end": week_end, "kind": big["kind"], "sport": big.get("sport"), "headline": big["headline"]})
        state["headlines"][sig] = [h for h in hl if (d(week_end) - d(h["week_end"])).days <= 120]
        state["last_sent"][sig] = sorted(set(sent_before + [week_end]))[-12:]
        print(f"  {sub['id']}: {subject}" + ("  [monthly]" if monthly else ""))

    if not subs:                      # pages-only run: leave the week's email files alone
        if args.pages:
            folder = args.pages_out or resolve(rcfg.get("report_data_dir", "output/school_reports"))
            n = build_pages(data, sections, cups, folder)
            print(f"Report pages: {n} school files → {folder}")
        return
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    if save_state:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
    if args.pages:
        folder = args.pages_out or resolve(rcfg.get("report_data_dir", "output/school_reports"))
        n = build_pages(data, sections, cups, folder)
        print(f"Report pages: {n} school files → {folder}")
    with open(os.path.join(out_dir, "sections.json"), "w", encoding="utf-8") as f:
        json.dump({f"{a}/{b}": v for (a, b), v in sections.items()}, f, indent=1, default=str)
    idx = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>School reports preview</title></head>"
           "<body style='font-family:Arial,sans-serif;background:#f4f5f8;padding:20px;'>",
           f"<h1 style='font-size:20px;'>School reports · week ending {esc(we.isoformat())}</h1>"]
    idx.append(f"<p>{len(manifest['emails'])} emails, {len(manifest['skipped'])} skipped</p>"
               "<table style='border-collapse:collapse;font-size:13px;background:#fff;'>"
               "<tr><th align='left' style='padding:6px;'>Email</th><th align='left' style='padding:6px;'>Subject</th>"
               "<th align='left' style='padding:6px;'>Sections</th></tr>")
    for e in manifest["emails"]:
        secs = ", ".join(x.split("/", 1)[1] for x in e["sections"])
        idx.append(f"<tr style='border-top:1px solid #ddd;'><td style='padding:6px;'><a href='{esc(e['file'])}'>{esc(e['id'])}</a></td>"
                   f"<td style='padding:6px;'>{esc(e['subject'])}</td><td style='padding:6px;color:#666;'>{esc(secs)}</td></tr>")
    idx.append("</table>")
    for s in manifest["skipped"]:
        idx.append(f"<p style='color:#888;'>{esc(s['id'])}: skipped ({esc(s['reason'])})</p>")
    idx.append("</body></html>")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(idx))
    print(f"\n{len(manifest['emails'])} email(s), {len(manifest['skipped'])} skipped → {out_dir}")


if __name__ == "__main__":
    main()
