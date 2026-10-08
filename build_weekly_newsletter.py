#!/usr/bin/env python3
"""
All MO Sports Weekly - newsletter draft builder
================================================

Pulls each in-season sport's ratings and games from GitHub, saves a weekly
ratings snapshot (so next week's issue can show rank movement), finds the
week's stories, and writes a DRAFT newsletter for a human to edit.

Nothing is sent. The output is a draft you edit before it goes out.

Outputs (in --out, default ./newsletter_output):
  snapshots/<run-date>/<sport>.json        ratings as of this run
  drafts/<week-ending>/newsletter_data.json every story the script found
  drafts/<week-ending>/draft_blocks.html   WordPress block markup (the draft)
  drafts/<week-ending>/preview.html        open in a browser to review

Usage:
  python build_weekly_newsletter.py                      # last full Mon-Sun week
  python build_weekly_newsletter.py --week-ending 2026-10-04
  python build_weekly_newsletter.py --no-snapshot        # test run, don't save a snapshot

Requires: Python 3.9+, PyYAML (pip install pyyaml)
"""

import argparse
import datetime as dt
import html
import json
import os
import sys
import urllib.request
from collections import defaultdict

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------

def fetch_json(url):
    """Fetch JSON from a URL or read it from a local path."""
    if url.startswith("http://") or url.startswith("https://"):
        req = urllib.request.Request(url, headers={"User-Agent": "AllMOSports-Newsletter/1.0", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(url, encoding="utf-8") as f:
        return json.load(f)


def to_iso(d):
    """'10/2/2026' or '2026-10-02' -> '2026-10-02'."""
    d = str(d).strip()
    if "/" in d:
        m, day, y = d.split("/")
        return f"{int(y):04d}-{int(m):02d}-{int(day):02d}"
    return d[:10]


def load_games(sport_key, cfg):
    """Return a flat list of games: {date, team1, team2, score1, score2, forfeit, overtime}.
    Unplayed games have score1/score2 = None."""
    if cfg.get("schedule_url"):
        # Football: per-team schedules -> dedupe into one record per game.
        sched = fetch_json(cfg["schedule_url"])
        seen, games = set(), []
        for team, info in sched.items():
            for g in info.get("games", []):
                opp = g["opponent"]
                key = (to_iso(g["date"]),) + tuple(sorted([team, opp]))
                if key in seen:
                    continue
                seen.add(key)
                played = bool(g.get("played"))
                games.append({
                    "date": to_iso(g["date"]),
                    "team1": team, "team2": opp,
                    "score1": g.get("my_score") if played else None,
                    "score2": g.get("opp_score") if played else None,
                    "forfeit": bool(g.get("forfeit")),
                    "overtime": bool(g.get("overtime")),
                })
        return games
    raw = fetch_json(cfg["games_url"])
    if isinstance(raw, dict):
        raw = raw.get("games", [])
    games = []
    for g in raw:
        games.append({
            "date": to_iso(g["date"]),
            "team1": g["team1"], "team2": g["team2"],
            "score1": g.get("score1"), "score2": g.get("score2"),
            "forfeit": bool(g.get("forfeit")),
            "overtime": bool(g.get("overtime")),
        })
    return games


def is_played(g):
    return g["score1"] is not None and g["score2"] is not None


# ----------------------------------------------------------------------
# Snapshots (for week-over-week rank movement)
# ----------------------------------------------------------------------

def save_snapshot(out_dir, run_date, sport_key, ratings):
    path = os.path.join(out_dir, "snapshots", run_date, f"{sport_key}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ratings, f, indent=1)


def load_previous_snapshot(out_dir, run_date, sport_key):
    """Most recent snapshot saved on a date BEFORE run_date, or None."""
    root = os.path.join(out_dir, "snapshots")
    if not os.path.isdir(root):
        return None, None
    dates = sorted(d for d in os.listdir(root) if d < run_date and os.path.exists(os.path.join(root, d, f"{sport_key}.json")))
    if not dates:
        return None, None
    d = dates[-1]
    with open(os.path.join(root, d, f"{sport_key}.json"), encoding="utf-8") as f:
        return json.load(f), d


# ----------------------------------------------------------------------
# Per-sport analysis
# ----------------------------------------------------------------------

TYPICAL_FOOTBALL = sorted([7 * k for k in range(15)] + [7 * k + 3 for k in range(15)])


def predict_football(a, b, league_average):
    """Same logic as the site's Matchup Predictor: returns (score_a, score_b)."""
    ra = a["off_rating"] - b["def_rating"] + league_average
    rb = b["off_rating"] - a["def_rating"] + league_average
    margin = ra - rb
    if ra < 0:
        rb += -ra; ra = 0
    if rb < 0:
        ra += -rb; rb = 0
    snap = lambda x: min(TYPICAL_FOOTBALL, key=lambda v: abs(x - v))
    sa, sb = snap(max(0, ra)), snap(max(0, rb))
    if sa == sb:
        if margin >= 0:
            sb = TYPICAL_FOOTBALL[max(0, TYPICAL_FOOTBALL.index(sb) - 1)]
        else:
            sa = TYPICAL_FOOTBALL[max(0, TYPICAL_FOOTBALL.index(sa) - 1)]
    return sa, sb


def records_from(games, until=None):
    rec = defaultdict(lambda: [0, 0, 0])
    for g in games:
        if not is_played(g):
            continue
        if until and g["date"] > until:
            continue
        for a, sa, sb in ((g["team1"], g["score1"], g["score2"]), (g["team2"], g["score2"], g["score1"])):
            rec[a][0 if sa > sb else (1 if sa < sb else 2)] += 1
    return rec


def fmt_rec(r):
    w, l, t = r
    return f"{w}-{l}" + (f"-{t}" if t else "")


def analyze_sport(sport_key, cfg, settings, week_start, week_end, out_dir, run_date, save_snap):
    ratings = fetch_json(cfg["ratings_url"])
    games = load_games(sport_key, cfg)
    exclude = set(settings.get("exclude_teams") or [])

    teams = [t for t in ratings["teams"] if t["school"] not in exclude]
    by_name = {t["school"]: t for t in teams}

    # Class ranks (by OVR within classification)
    class_rank = {}
    by_class = defaultdict(list)
    for t in teams:
        by_class[t["classification"]].append(t)
    for c, lst in by_class.items():
        for i, t in enumerate(sorted(lst, key=lambda x: -x["ovr_rating"])):
            class_rank[t["school"]] = i + 1

    # Previous snapshot -> rank movement and pre-game ratings for upsets
    prev, prev_date = load_previous_snapshot(out_dir, run_date, sport_key)
    prev_rank = {t["school"]: t["ovr_rank"] for t in prev["teams"]} if prev else {}
    pre_game = {t["school"]: t for t in prev["teams"]} if prev else by_name

    if save_snap:
        save_snapshot(out_dir, run_date, sport_key, ratings)

    rec = records_from(games)  # season record through the latest data

    def rank_of(name):
        t = by_name.get(name)
        return t["ovr_rank"] if t else None

    def movement(name):
        if not prev:
            return None
        now, before = rank_of(name), prev_rank.get(name)
        if before is None:
            return "new"
        return before - now  # positive = moved up

    # --- Top N ---
    top = []
    for t in sorted(teams, key=lambda x: x["ovr_rank"])[: settings["top_n"]]:
        top.append({
            "rank": t["ovr_rank"], "school": t["school"], "class": t["classification"],
            "record": fmt_rec(rec[t["school"]]), "ovr": t["ovr_rating"],
            "off": t.get("off_rating"), "def": t.get("def_rating"),
            "move": movement(t["school"]),
        })

    week_games = [g for g in games if week_start <= g["date"] <= week_end and is_played(g)]

    def describe(g):
        a, b = g["team1"], g["team2"]
        if g["score1"] >= g["score2"]:
            w, l, ws, ls = a, b, g["score1"], g["score2"]
        else:
            w, l, ws, ls = b, a, g["score2"], g["score1"]
        return {
            "date": g["date"], "winner": w, "loser": l, "w_score": ws, "l_score": ls,
            "tie": ws == ls, "overtime": g["overtime"], "forfeit": g["forfeit"],
            "w_rank": rank_of(w), "l_rank": rank_of(l),
        }

    # --- Big results: both teams ranked within cutoff ---
    cut = settings["results_rank_cutoff"]
    big = []
    for g in week_games:
        if g["forfeit"]:
            continue
        ra, rb = rank_of(g["team1"]), rank_of(g["team2"])
        if ra and rb and ra <= cut and rb <= cut:
            d = describe(g)
            d["sort"] = ra + rb
            big.append(d)
    big.sort(key=lambda d: d["sort"])
    # Keep the list varied: each team appears at most once (matters for
    # volleyball tournaments, where the same teams meet several times a day).
    picked, used = [], set()
    for d in big:
        if d["winner"] in used or d["loser"] in used:
            continue
        picked.append(d)
        used.update([d["winner"], d["loser"]])
        if len(picked) >= settings["results_max"]:
            break
    big = picked

    # --- No. 1 team's week ---
    number_one = teams and min(teams, key=lambda x: x["ovr_rank"])["school"]
    one_week = [describe(g) for g in week_games if number_one in (g["team1"], g["team2"])]

    # --- Upsets (pre-game ratings when a snapshot exists) ---
    ups = []
    for g in week_games:
        if g["forfeit"] or g["score1"] == g["score2"]:
            continue
        d = describe(g)
        wt, lt = pre_game.get(d["winner"]), pre_game.get(d["loser"])
        if not wt or not lt:
            continue
        gap = lt["ovr_rating"] - wt["ovr_rating"]
        if gap >= cfg.get("upset_min_gap", 0):
            d["gap"] = round(gap, 2)
            d["gap_basis"] = "pre-game" if prev else "current"
            ups.append(d)
    ups.sort(key=lambda d: -d["gap"])
    upsets = ups[: settings["upsets_max"]]

    # --- Unbeaten ---
    unbeaten = []
    for name, r in rec.items():
        if name in by_name and r[1] == 0 and r[0] >= settings["unbeaten_min_wins"]:
            unbeaten.append({"school": name, "record": fmt_rec(r), "rank": rank_of(name),
                             "class": by_name[name]["classification"]})
    unbeaten.sort(key=lambda u: u["rank"])

    # --- Games to watch: next 7 days ---
    nxt0 = (dt.date.fromisoformat(week_end) + dt.timedelta(days=1)).isoformat()
    nxt1 = (dt.date.fromisoformat(week_end) + dt.timedelta(days=7)).isoformat()
    wcut = settings["watch_rank_cutoff"]
    watch, seen = [], set()
    for g in games:
        if not (nxt0 <= g["date"] <= nxt1) or is_played(g):
            continue
        a, b = g["team1"], g["team2"]
        ra, rb = rank_of(a), rank_of(b)
        if not (ra and rb and ra <= wcut and rb <= wcut):
            continue
        key = (g["date"],) + tuple(sorted([a, b]))
        if key in seen:
            continue
        seen.add(key)
        fav, dog = (a, b) if by_name[a]["ovr_rating"] >= by_name[b]["ovr_rating"] else (b, a)
        item = {"date": g["date"], "team1": a, "team2": b, "rank1": ra, "rank2": rb, "favorite": fav, "sort": ra + rb}
        if cfg.get("predicted_scores"):
            sf, sd = predict_football(by_name[fav], by_name[dog], cfg.get("league_average", 26))
            if sf < sd:
                fav, dog, sf, sd = dog, fav, sd, sf
            item.update({"favorite": fav, "pick": f"{sf}–{sd}"})
        watch.append(item)
    watch.sort(key=lambda w: w["sort"])
    watch = watch[: settings["watch_max"]]

    # --- Featured teams ---
    featured = []
    for name in settings.get("featured_teams") or []:
        gs = [describe(g) for g in week_games if name in (g["team1"], g["team2"])]
        if name in by_name:
            featured.append({"school": name, "record": fmt_rec(rec[name]), "rank": rank_of(name),
                             "class": by_name[name]["classification"], "games": gs})

    return {
        "sport": sport_key, "label": cfg["label"], "unit": cfg.get("unit", "points"),
        "rankings_page": cfg.get("rankings_page"),
        "ratings_updated": ratings.get("last_updated"),
        "previous_snapshot": prev_date,
        "games_this_week": len(week_games),
        "top": top, "results": big, "number_one": {"school": number_one, "games": one_week},
        "upsets": upsets, "unbeaten": unbeaten, "watch": watch, "featured": featured,
        "_class_rank": class_rank,
    }


# ----------------------------------------------------------------------
# Cross-sport lead-story candidates
# ----------------------------------------------------------------------

def lead_candidates(sports):
    cands = []
    # Schools unbeaten in 2+ sports
    unb = defaultdict(list)
    for s in sports:
        for u in s["unbeaten"]:
            unb[u["school"]].append((s["label"], u["record"]))
    for school, lst in unb.items():
        if len(lst) >= 2:
            cands.append({
                "type": "multi_sport_unbeaten", "score": 100 + 10 * len(lst),
                "headline": f"{school} is unbeaten in {len(lst)} sports",
                "facts": [f"{lab}: {r}" for lab, r in lst],
            })
    for s in sports:
        # No. 1 lost
        one = s["number_one"]
        for g in one["games"]:
            if g["loser"] == one["school"] and not g["tie"]:
                cands.append({"type": "number_one_lost", "score": 90,
                              "headline": f"{s['label']}: No. 1 {one['school']} lost to {g['winner']}",
                              "facts": [f"{g['winner']} {g['w_score']}, {g['loser']} {g['l_score']} ({g['date']})"]})
        # New No. 1
        if s["top"] and s["top"][0]["move"] not in (None, 0):
            cands.append({"type": "new_number_one", "score": 95,
                          "headline": f"{s['label']} has a new No. 1: {s['top'][0]['school']}",
                          "facts": [f"Record {s['top'][0]['record']}"]})
        # Biggest upset
        for u in s["upsets"]:
            cands.append({"type": "upset", "score": 60 + min(30, u["gap"]),
                          "headline": f"{s['label']} upset: {u['winner']} over {u['loser']}",
                          "facts": [f"{u['winner']} {u['w_score']}, {u['loser']} {u['l_score']} ({u['date']})",
                                    f"No. {u['w_rank']} beat No. {u['l_rank']}; rating gap {u['gap']} {s['unit']}"]})
    cands.sort(key=lambda c: -c["score"])
    return cands


# ----------------------------------------------------------------------
# Writing: template sentences -> WordPress blocks
# ----------------------------------------------------------------------

def esc(s):
    return html.escape(tidy(s), quote=False)


def tidy(s):
    """Display cleanup: 'Belleville West(Belleville, IL)' -> 'Belleville West (Belleville, IL)'."""
    s = str(s)
    i = s.find("(")
    if i > 0 and s[i - 1] != " ":
        s = s[:i] + " " + s[i:]
    return s


LOGO_SLUGS = {}


def load_logo_slugs(settings):
    """School name -> logo slug, from schools.json (same lookup the site snippets use)."""
    url = settings.get("schools_url")
    if not url:
        return
    try:
        data = fetch_json(url).get("schools", {})
    except Exception as e:
        print(f"[logos] schools.json not loaded ({e}); continuing without logos", file=sys.stderr)
        return
    for slug, info in data.items():
        for k in ("name", "mshsaa_name"):
            if info.get(k):
                LOGO_SLUGS[info[k].lower().strip()] = slug


def logo_img(name, settings, size=24):
    base = settings.get("logo_base_url")
    if not base:
        return ""
    key = str(name).lower().strip()
    slug = LOGO_SLUGS.get(key)
    if not slug and " with " in key:
        slug = LOGO_SLUGS.get(key.split(" with ")[0].strip())
    if not slug:
        return ""
    return f'<img src="{base}{slug}.png" alt="" width="{size}" height="{size}" style="width:{size}px;height:{size}px;vertical-align:middle"/> '


def nice_date(iso):
    d = dt.date.fromisoformat(iso)
    mon = ["Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."][d.month - 1]
    return f"{d.strftime('%a')}., {mon} {d.day}"


def short_date(iso):
    d = dt.date.fromisoformat(iso)
    mon = ["Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."][d.month - 1]
    return f"{mon} {d.day}"


def team_ref(name, rank):
    return f"No. {rank} {name}" if rank else name


def move_text(m):
    if m is None:
        return ""
    if m == "new":
        return "new"
    if m > 0:
        return f"▲{m}"
    if m < 0:
        return f"▼{-m}"
    return "–"


# Block helpers (Gutenberg markup the WordPress editor opens as normal blocks)
def b_heading(text, level=2):
    return f'<!-- wp:heading {{"level":{level}}} -->\n<h{level} class="wp-block-heading">{text}</h{level}>\n<!-- /wp:heading -->'


def b_para(text, cls=None):
    if cls:
        return f'<!-- wp:paragraph {{"className":"{cls}"}} -->\n<p class="{cls}">{text}</p>\n<!-- /wp:paragraph -->'
    return f"<!-- wp:paragraph -->\n<p>{text}</p>\n<!-- /wp:paragraph -->"


def b_list(items):
    lis = "".join(f"<!-- wp:list-item -->\n<li>{i}</li>\n<!-- /wp:list-item -->\n" for i in items)
    return f'<!-- wp:list -->\n<ul class="wp-block-list">{lis}</ul>\n<!-- /wp:list -->'


def b_table(head, rows):
    th = "".join(f"<th>{h}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<!-- wp:table -->\n<figure class="wp-block-table"><table><thead><tr>{th}</tr></thead>'
            f"<tbody>{tr}</tbody></table></figure>\n<!-- /wp:table -->")


def b_sep():
    return '<!-- wp:separator -->\n<hr class="wp-block-separator has-alpha-channel-opacity"/>\n<!-- /wp:separator -->'


def b_buttons(links):
    btns = "".join(
        f'<!-- wp:button -->\n<div class="wp-block-button"><a class="wp-block-button__link wp-element-button" href="{esc(l["url"])}">{esc(l["label"])}</a></div>\n<!-- /wp:button -->\n'
        for l in links)
    return f"<!-- wp:buttons -->\n<div class=\"wp-block-buttons\">{btns}</div>\n<!-- /wp:buttons -->"


EDIT = "editor-note"  # paragraphs with this class are reminders for you; delete before sending


def result_sentence(r):
    if r["tie"]:
        return f"<strong>{esc(team_ref(r['winner'], r['w_rank']))} and {esc(team_ref(r['loser'], r['l_rank']))} tied {r['w_score']}–{r['l_score']}.</strong>"
    ot = " in overtime" if r["overtime"] else ""
    return (f"<strong>{esc(team_ref(r['winner'], r['w_rank']))} beat {esc(team_ref(r['loser'], r['l_rank']))}, "
            f"{r['w_score']}–{r['l_score']}{ot}</strong> ({short_date(r['date'])}).")


def build_blocks(data, settings):
    brand = settings["brand"]
    out = []
    ws, we = data["week_start"], data["week_end"]
    out.append(b_para(f"<strong>{esc(brand)} Weekly</strong> · Week of {short_date(ws)} – {short_date(we)}, {we[:4]}"))
    total = sum(s["games_this_week"] for s in data["sports"])
    labels = [s["label"].lower() for s in data["sports"]]
    sport_list = ", ".join(labels[:-1]) + (" and " + labels[-1] if len(labels) > 1 else labels[0])
    out.append(b_para(f"About {total:,} games were played across {sport_list} last week. Here's what mattered and which games to circle this week."))

    # Lead story placeholder with the top candidates
    out.append(b_heading("The big story"))
    if data["lead_candidates"]:
        c = data["lead_candidates"][0]
        out.append(b_para(f"✏️ <em>Write the lead story here. Suggested: {esc(c['headline'])}. Facts: {esc('; '.join(c['facts']))}.</em>", EDIT))
        others = data["lead_candidates"][1:4]
        if others:
            out.append(b_para("✏️ <em>Other options: " + esc(" | ".join(o["headline"] for o in others)) + ". Delete these notes before sending.</em>", EDIT))
    else:
        out.append(b_para("✏️ <em>Write the lead story here.</em>", EDIT))

    for s in data["sports"]:
        out.append(b_sep())
        link = f' <a href="{esc(s["rankings_page"])}">Full rankings</a>' if s.get("rankings_page") else ""
        out.append(b_heading(esc(s["label"])))
        if link:
            out.append(b_para(link.strip()))

        # Top N
        show_move = any(t["move"] is not None for t in s["top"])
        out.append(b_heading(f"Statewide top {len(s['top'])}", 3))
        ovr = lambda t: f"{t['ovr']:.2f}" if abs(t["ovr"]) < 20 else f"{t['ovr']:.1f}"
        if settings.get("top_style", "list") == "table":
            head = ["Rank", "Team", "Class", "Record", "OVR"] + (["Move"] if show_move else [])
            rows = []
            for t in s["top"]:
                row = [str(t["rank"]), logo_img(t["school"], settings, 20) + esc(t["school"]), f"Class {t['class']}", t["record"], ovr(t)]
                if show_move:
                    row.append(move_text(t["move"]))
                rows.append(row)
            out.append(b_table(head, rows))
        else:
            # Plain lines (not a table) so site plugins that add search boxes
            # to tables leave it alone, and it reads well in every email app.
            items = []
            for t in s["top"]:
                mv = move_text(t["move"]) if show_move else ""
                items.append(f"{logo_img(t['school'], settings)}<strong>{t['rank']}. {esc(t['school'])}</strong>"
                             f" · Class {t['class']} · {t['record']} · OVR {ovr(t)}" + (f" · {mv}" if mv else ""))
            # One paragraph with line breaks: no bullets next to the logos.
            out.append(b_para("<br>".join(items)))

        # What happened
        items = [result_sentence(r) for r in s["results"]]
        one = s["number_one"]
        if one["games"]:
            covered = {(r["winner"], r["loser"], r["date"]) for r in s["results"]}
            extra = [g for g in one["games"] if (g["winner"], g["loser"], g["date"]) not in covered]
            if extra:
                parts = []
                for g in extra:
                    if g["tie"]:
                        parts.append(f"tied {g['loser'] if g['winner'] == one['school'] else g['winner']} {g['w_score']}–{g['l_score']}")
                    elif g["winner"] == one["school"]:
                        parts.append(f"beat {g['loser']} {g['w_score']}–{g['l_score']}")
                    else:
                        parts.append(f"lost to {g['winner']} {g['l_score']}–{g['w_score']}")
                items.append(f"<strong>No. 1 {esc(one['school'])}</strong> " + esc("; ".join(parts)) + ".")
        if items:
            out.append(b_heading("What happened", 3))
            out.append(b_list(items))

        # Upset
        for u in s["upsets"]:
            out.append(b_heading("Upset of the week", 3))
            basis = "going in" if u["gap_basis"] == "pre-game" else "by current ratings"
            out.append(b_para(
                f"<strong>{esc(u['winner'])} {u['w_score']}, {esc(u['loser'])} {u['l_score']}</strong> ({short_date(u['date'])}). "
                f"No. {u['w_rank']} beat No. {u['l_rank']}; {esc(u['loser'])} rated about {u['gap']:.1f} {s['unit']} better {basis}."))

        # Unbeaten
        unb = s["unbeaten"][: settings["unbeaten_max_listed"]]
        if unb:
            out.append(b_heading("Still unbeaten", 3))
            names = ", ".join(f"{esc(u['school'])} ({u['record']})" for u in unb)
            more = len(s["unbeaten"]) - len(unb)
            tail = f", plus {more} more" if more > 0 else ""
            out.append(b_para(f"{names}{tail}."))

        # Featured
        for f in s["featured"]:
            out.append(b_heading(f"Featured: {esc(f['school'])}", 3))
            gl = [result_sentence(g) for g in f["games"]] or ["No games this week."]
            out.append(b_para(f"{esc(f['school'])} is {f['record']}, No. {f['rank']} in the state and No. {s['_class_rank'].get(f['school'], '?')} in Class {f['class']}."))
            out.append(b_list(gl))

        # Games to watch
        if s["watch"]:
            out.append(b_heading("Games to watch", 3))
            items = []
            for w in s["watch"]:
                pick = f" Our pick: {esc(w['favorite'])} {w['pick']}." if w.get("pick") else f" Favorite: {esc(w['favorite'])}."
                items.append(f"<strong>{esc(team_ref(w['team1'], w['rank1']))} vs. {esc(team_ref(w['team2'], w['rank2']))}</strong>, {nice_date(w['date'])}.{pick}")
            out.append(b_list(items))

    cta = settings.get("closing_cta") or {}
    if cta.get("enabled"):
        out.append(b_sep())
        out.append(b_heading(esc(cta["heading"])))
        out.append(b_para(esc(cta["text"])))
        out.append(b_buttons(cta.get("links", [])))

    out.append(b_sep())
    out.append(b_para(
        f"Rankings use {esc(brand)} overall ratings, which account for strength of schedule. "
        f'<a href="{esc(settings["site_url"])}/ratings-explained/">How our ratings work</a>. '
        f"Want your team featured? Reply with the school you follow."))
    return "\n\n".join(out)


PREVIEW_CSS = """
body{margin:0;background:#eceae4;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1b2430;line-height:1.55}
.banner{max-width:680px;margin:20px auto 0;padding:10px 16px;background:#fff6dd;border:1px solid #e8b84b;border-radius:8px;font-size:14px}
.mail{max-width:640px;margin:16px auto 40px;background:#fff;border:1px solid #e3e1da;border-radius:10px;padding:8px 28px 28px}
.mast{margin:0 -28px 12px;padding:22px 28px;background:#14304d;color:#fff;border-radius:10px 10px 0 0}
.mast b{display:block;color:#e8b84b;letter-spacing:.12em;text-transform:uppercase;font-size:13px}
.mast span{font-size:28px;font-weight:800}
h2{color:#14304d;font-size:24px;margin:22px 0 6px}
h3{color:#8a6510;font-size:13px;letter-spacing:.08em;text-transform:uppercase;margin:16px 0 6px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:#7d8794;font-size:12px;border-bottom:2px solid #e4e1da;padding:4px 6px}
td{border-bottom:1px solid #e4e1da;padding:6px}
ul{padding-left:20px}li{margin:4px 0}
hr{border:none;border-top:6px solid #eceae4;margin:20px -28px}
a{color:#14304d}
.editor-note{background:#fff6dd;border-left:4px solid #e8b84b;padding:8px 10px;font-size:14px}
.wp-block-buttons{display:flex;gap:8px;flex-wrap:wrap}
.wp-block-button__link{display:inline-block;background:#e8b84b;color:#14304d;font-weight:700;text-decoration:none;padding:8px 14px;border-radius:999px}
"""


def build_preview(blocks, data, settings):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(settings['brand'])} Weekly draft · {data['week_end']}</title>
<style>{PREVIEW_CSS}</style></head><body>
<div class="banner"><strong>Draft preview.</strong> Built {esc(data['generated'])}. Yellow notes are reminders for you and should be deleted before sending.</div>
<div class="mail"><div class="mast"><b>{esc(settings['brand'])} Weekly</b><span>Fall Sports Roundup</span></div>
{blocks}
</div></body></html>"""


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def last_full_week(today):
    """Mon-Sun week that ended most recently before `today`."""
    end = today - dt.timedelta(days=today.weekday() + 1)  # last Sunday
    return end - dt.timedelta(days=6), end


def strip_private(sport):
    return {k: v for k, v in sport.items() if not k.startswith("_")}


def main():
    ap = argparse.ArgumentParser(description="Build the All MO Sports weekly newsletter draft.")
    ap.add_argument("--config", default=os.path.join(HERE, "newsletter_config.yaml"))
    ap.add_argument("--out", default=os.path.join(HERE, "newsletter_output"))
    ap.add_argument("--week-ending", help="Sunday the week ends on, YYYY-MM-DD (default: last full week)")
    ap.add_argument("--no-snapshot", action="store_true", help="Don't save this run's ratings snapshot")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    today = dt.date.today()
    if args.week_ending:
        we = dt.date.fromisoformat(args.week_ending)
        ws = we - dt.timedelta(days=6)
    else:
        ws, we = last_full_week(today)
    week_start, week_end, run_date = ws.isoformat(), we.isoformat(), today.isoformat()

    load_logo_slugs(settings)
    sports = []
    for key, cfg in settings["sports"].items():
        if not cfg.get("enabled"):
            continue
        print(f"[{key}] loading...", flush=True)
        try:
            s = analyze_sport(key, cfg, settings, week_start, week_end, args.out, run_date, not args.no_snapshot)
        except Exception as e:  # one sport failing shouldn't kill the issue
            print(f"[{key}] FAILED: {e}", file=sys.stderr)
            continue
        print(f"[{key}] {s['games_this_week']} games, {len(s['results'])} big results, "
              f"{len(s['upsets'])} upset(s), {len(s['unbeaten'])} unbeaten, {len(s['watch'])} to watch")
        sports.append(s)

    if not sports:
        sys.exit("No sport data loaded; nothing to build.")

    data = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "week_start": week_start, "week_end": week_end,
        "sports": sports,
        "lead_candidates": lead_candidates(sports),
    }

    blocks = build_blocks(data, settings)
    draft_dir = os.path.join(args.out, "drafts", week_end)
    os.makedirs(draft_dir, exist_ok=True)
    with open(os.path.join(draft_dir, "newsletter_data.json"), "w", encoding="utf-8") as f:
        json.dump({**data, "sports": [strip_private(s) for s in sports]}, f, indent=1)
    with open(os.path.join(draft_dir, "draft_blocks.html"), "w", encoding="utf-8") as f:
        f.write(blocks)
    with open(os.path.join(draft_dir, "preview.html"), "w", encoding="utf-8") as f:
        f.write(build_preview(blocks, data, settings))

    print(f"\nDraft written to {draft_dir}")
    print("Lead story ideas:")
    for c in data["lead_candidates"][:5]:
        print(f"  - {c['headline']}")


if __name__ == "__main__":
    main()
