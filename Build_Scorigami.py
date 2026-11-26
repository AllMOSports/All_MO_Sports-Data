"""
Build_Scorigami.py
==================
Builds the data files behind the All MO Sports "Scorigami" page for
Football, Boys Basketball and Girls Basketball.

Scorigami = a final score that has never happened before. For each sport this
writes every distinct final score with how many times it has happened, the
first game and the most recent game, plus the current season's brand-new
scores ("Scorigami Club").

Sources
  football          history: output/football_schedule_<yyyy>.json (this repo, 2010 ..)
                    current: football-ratings-<yyyy>/football_games_<yyyy>_all.json
  boys_basketball   history: output/boys_basketball_schedule_<yyyy>-<yyyy+1>.json (this repo, 2012-13 ..)
                    current: Boys_Basketball_Ratings_<yyyy>-<yyyy+1>/boys_basketball_games_<yyyy>_all.json
  girls_basketball  history: Girls_Basketball_Ratings_<yyyy>-<yyyy+1>/girls_basketball_scoreboard_<yyyy>.csv
                    current: Girls_Basketball_Ratings_<yyyy>-<yyyy+1>/girls_basketball_games_<yyyy>_all.json

Output
  output/scorigami/<sport>_scorigami.json

Usage
  python scripts/Build_Scorigami.py                      # all sports, current seasons below
  python scripts/Build_Scorigami.py football             # one sport
  python scripts/Build_Scorigami.py --football-season 2027 --basketball-season 2027
  python scripts/Build_Scorigami.py --email              # also email a summary (needs GMAIL_USER,
                                                         # GMAIL_APP_PASSWORD; REPORT_TO optional)

Seasons are keyed by the year they START (basketball 2026 = 2026-27).
Excluded: forfeits (flagged), ties, duplicate games, games against JV / C-team /
freshman teams, and basketball "scores"
where the winner has 2 points or fewer (MSHSAA forfeit entries such as 2-0 / 1-0).
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, 'output', 'scorigami')
RAW = 'https://raw.githubusercontent.com/AllMOSports/{repo}/main/{path}'
SUB_VARSITY = re.compile(r'\b(JV|J\.V\.|Junior Varsity|C[- ]Team|Freshm[ae]n|Sophomore)\b', re.I)


def fetch(repo, path, as_json=True):
    with urllib.request.urlopen(RAW.format(repo=repo, path=path), timeout=90) as r:
        data = r.read().decode('utf-8-sig')
    return json.loads(data) if as_json else data


def game(season, date, t1, s1, t2, s2):
    """Normalize one game; returns None when it should be skipped."""
    if s1 is None or s2 is None:
        return None
    s1, s2 = int(s1), int(s2)
    if s1 == s2:
        return None
    if s1 > s2:
        return {'season': season, 'date': date, 'w': t1, 'l': t2, 'ws': s1, 'ls': s2}
    return {'season': season, 'date': date, 'w': t2, 'l': t1, 'ws': s2, 'ls': s1}


def from_schedule_file(path, season):
    """This repo's per-team schedule files (each game appears for both teams)."""
    out = []
    teams = json.load(open(path, encoding='utf-8'))['teams']
    for team, sched in teams.items():
        for g in sched:
            opp = g.get('opponent')
            if not opp or g.get('result') not in ('W', 'L'):
                continue
            if g.get('team_score') is None or g.get('opp_score') is None:
                continue
            if g['result'] == 'W':
                out.append(game(season, g['date'], team, max(g['team_score'], g['opp_score']), opp, min(g['team_score'], g['opp_score'])))
            else:
                out.append(game(season, g['date'], opp, max(g['team_score'], g['opp_score']), team, min(g['team_score'], g['opp_score'])))
    return [g for g in out if g]


def from_games_all(rows, season):
    """Current-season flat list: team1/score1/team2/score2/forfeit."""
    out = []
    for x in rows:
        if x.get('forfeit'):
            continue
        g = game(season, x['date'], x['team1'], x.get('score1'), x['team2'], x.get('score2'))
        if g:
            out.append(g)
    return out


def from_scoreboard_csv(text, season):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            g = game(season, r['Date'], r['Home Team'], int(r['Home Score']), r['Away Team'], int(r['Away Score']))
        except (ValueError, KeyError, TypeError):
            continue
        if g:
            out.append(g)
    return out


# ---------------------------------------------------------------- sports
def load_football(cur):
    games = []
    for y in range(2010, cur):
        p = os.path.join(ROOT, 'output', f'football_schedule_{y}.json')
        if os.path.exists(p):
            games += from_schedule_file(p, y)
        else:
            print(f'  ! football {y}: {p} missing')
    games += from_games_all(fetch(f'football-ratings-{cur}', f'football_games_{cur}_all.json'), cur)
    return games


def load_boys_basketball(cur):
    games = []
    for y in range(2012, cur):
        p = os.path.join(ROOT, 'output', f'boys_basketball_schedule_{y}-{y + 1}.json')
        if os.path.exists(p):
            games += from_schedule_file(p, y)
        else:
            print(f'  ! boys basketball {y}: {p} missing')
    games += from_games_all(fetch(f'Boys_Basketball_Ratings_{cur}-{cur + 1}', f'boys_basketball_games_{cur}_all.json'), cur)
    return games


def load_girls_basketball(cur):
    games = []
    for y in range(2012, cur):
        try:
            txt = fetch(f'Girls_Basketball_Ratings_{y}-{y + 1}', f'girls_basketball_scoreboard_{y}.csv', as_json=False)
            games += from_scoreboard_csv(txt, y)
        except Exception as e:  # keep going if one season is unavailable
            print(f'  ! girls basketball {y}: {e}')
    games += from_games_all(fetch(f'Girls_Basketball_Ratings_{cur}-{cur + 1}', f'girls_basketball_games_{cur}_all.json'), cur)
    return games


SPORTS = {
    'football': {'name': 'Football', 'first': 2010, 'two_year': False, 'loader': load_football, 'basketball': False},
    'boys_basketball': {'name': 'Boys Basketball', 'first': 2012, 'two_year': True, 'loader': load_boys_basketball, 'basketball': True},
    'girls_basketball': {'name': 'Girls Basketball', 'first': 2012, 'two_year': True, 'loader': load_girls_basketball, 'basketball': True},
}


# ---------------------------------------------------------------- manual corrections
# Key: (sport, date, team A, team B). Value: (team A score, team B score) for a
# corrected final, or 'REMOVE' to drop the game. Team order does not matter for
# matching; scores follow the order the teams are written here.
# Source: Tyler's review of Scorigami_Scores_Review.xlsx (Nov 26, 2026).
CORRECTIONS = {
    # Boys basketball
    ('boys_basketball', '2025-02-24', 'Normandy Collaborative', 'Clayton'): (54, 60),
    ('boys_basketball', '2017-01-09', 'Monroe City', 'Louisiana'): (67, 47),
    ('boys_basketball', '2026-02-13', 'Metro', 'McKinley Classical Leadership'): (46, 77),
    ('boys_basketball', '2017-02-10', 'Lindbergh', 'Oakville'): (64, 38),
    ('boys_basketball', '2013-02-12', 'Liberty', "Lee's Summit North"): (56, 55),
    # Girls basketball
    ('girls_basketball', '2013-02-12', 'Oakville', 'Webster Groves'): (36, 62),
    ('girls_basketball', '2025-01-22', 'Normandy Collaborative', 'Riverview Gardens'): (19, 46),
    ('girls_basketball', '2024-01-23', 'Normandy Collaborative',
     'Collegiate School of Med-Bio Science with Central Visual and Performing Arts'): (48, 22),
    ('girls_basketball', '2017-01-09', 'Monroe City', 'Louisiana'): (58, 22),
    ('girls_basketball', '2013-12-20', 'Lockwood', 'Marionville'): (35, 52),
    ('girls_basketball', '2018-02-05', 'California', 'Versailles'): 'REMOVE',
    ('girls_basketball', '2012-12-13', 'Lexington', 'Crest Ridge'): (20, 56),
}


def apply_corrections(key, raw):
    fixes = {}
    for (sp, d, a, b), v in CORRECTIONS.items():
        if sp == key:
            fixes[(d, frozenset((a, b)))] = (a, b, v)
    used, out = set(), []
    for g in raw:
        k = (g['date'], frozenset((g['w'], g['l'])))
        fx = fixes.get(k)
        if fx is None:
            out.append(g)
            continue
        used.add(k)
        a, b, v = fx
        if v == 'REMOVE':
            continue
        sa, sb = v
        if sa == sb:
            continue
        w, l, ws, ls = (a, b, sa, sb) if sa > sb else (b, a, sb, sa)
        out.append(dict(g, w=w, l=l, ws=ws, ls=ls))
    for k in fixes:
        if k not in used:
            print(f'  ! correction not matched ({key}): {k[0]} {sorted(k[1])}')
    print(f'  {key}: {len(used)} of {len(fixes)} corrections applied')
    return out


def label(y, two_year):
    return f'{y}-{str(y + 1)[2:]}' if two_year else str(y)


def build(key, cur):
    cfg = SPORTS[key]
    raw = apply_corrections(key, cfg['loader'](cur))
    seen, games = set(), []
    for g in raw:
        if SUB_VARSITY.search(g['w']) or SUB_VARSITY.search(g['l']):   # JV / C-team / freshman opponents
            continue
        if cfg['basketball']:
            if g['ws'] <= 2:          # forfeit entries (2-0, 1-0, 2-1)
                continue
        k = (g['date'], tuple(sorted([g['w'], g['l']])), g['ws'], g['ls'])
        if k in seen:
            continue
        seen.add(k)
        games.append(g)
    # Second pass: the same game listed under two spellings of one team
    # (e.g. "Sumner" vs "Sumner(St. Louis, MO)"). Same date + same score + a
    # shared team = duplicate; keep the cleaner (parenthesis-free) names.
    def messy(g):
        return ('(' in g['w']) + ('(' in g['l'])
    kept, by_side = [], {}
    for g in games:
        hit = by_side.get((g['date'], g['ws'], g['ls'], 'w', g['w'])) or by_side.get((g['date'], g['ws'], g['ls'], 'l', g['l']))
        if hit is not None:
            if messy(g) < messy(kept[hit]):
                kept[hit] = g
                by_side[(g['date'], g['ws'], g['ls'], 'w', g['w'])] = hit
                by_side[(g['date'], g['ws'], g['ls'], 'l', g['l'])] = hit
            continue
        by_side[(g['date'], g['ws'], g['ls'], 'w', g['w'])] = len(kept)
        by_side[(g['date'], g['ws'], g['ls'], 'l', g['l'])] = len(kept)
        kept.append(g)
    games = sorted(kept, key=lambda g: (g['date'], g['w'], g['l']))

    scores, new_by = {}, {}
    for g in games:
        sk = f"{g['ws']}-{g['ls']}"
        ref = {'d': g['date'], 'w': g['w'], 'l': g['l']}
        s = scores.get(sk)
        if s is None:
            scores[sk] = {'n': 1, 'y': g['season'], 'first': ref, 'last': ref}
            new_by[g['season']] = new_by.get(g['season'], 0) + 1
        else:
            s['n'] += 1
            s['last'] = ref

    club = [{'score': sk, 'date': s['first']['d'], 'w': s['first']['w'], 'l': s['first']['l']}
            for sk, s in scores.items() if s['y'] == cur]
    club.sort(key=lambda c: (c['date'], c['w']))
    cur_games = [g for g in games if g['season'] == cur]
    common = max(scores.items(), key=lambda kv: kv[1]['n'])
    seasons = list(range(cfg['first'], cur + 1))
    data = {
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'sport': key,
        'sport_name': cfg['name'],
        'first_season': cfg['first'],
        'first_label': label(cfg['first'], cfg['two_year']),
        'season': cur,
        'season_labels': {str(y): label(y, cfg['two_year']) for y in seasons},
        'through': max(g['date'] for g in cur_games) if cur_games else None,
        'totals': {'games': len(games), 'unique': len(scores), 'new_this_season': len(club),
                   'games_this_season': len(cur_games)},
        'most_common': {'score': common[0], 'n': common[1]['n']},
        'new_by_season': {str(y): new_by.get(y, 0) for y in seasons},
        'club': club,
        'scores': scores,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f'{key}_scorigami.json')
    # What is new since the last run (for the daily email). Scores posted late
    # still count, because this compares against the previous output file.
    new_since = None
    if os.path.exists(path):
        try:
            old_keys = set(json.load(open(path, encoding='utf-8'))['scores'])
            new_since = [{'score': sk, 'date': s['first']['d'], 'w': s['first']['w'], 'l': s['first']['l']}
                         for sk, s in scores.items() if sk not in old_keys]
            new_since.sort(key=lambda c: (c['date'], c['w']))
        except Exception:
            new_since = None
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    print(f'{cfg["name"]}: {len(games):,} games, {len(scores):,} distinct scores, '
          f'{len(club)} new in {label(cur, cfg["two_year"])} -> {path} ({os.path.getsize(path) / 1024:.0f} KB)')
    return {'name': cfg['name'], 'season_label': label(cur, cfg['two_year']), 'new_since': new_since,
            'season_total': len(club)}


# ---------------------------------------------------------------- daily email
PAGE_URL = 'https://allmosports.com/scorigami/'   # change if the page lives elsewhere
MON = ['Jan.', 'Feb.', 'March', 'April', 'May', 'June', 'July', 'Aug.', 'Sept.', 'Oct.', 'Nov.', 'Dec.']


def nice_date(d):
    y, m, dd = d.split('-')
    return f'{MON[int(m) - 1]} {int(dd)}'


def email_report(results, failed):
    total = sum(len(r['new_since'] or []) for r in results.values())
    today = datetime.now(timezone.utc).astimezone().strftime('%b %d, %Y')
    subject = f'All MO Sports Scorigami: {total} new' + (' (with errors)' if failed else '')
    lines = [f'Scorigami report for {today}', '',
             f'{total} new Scorigami{"" if total == 1 else "s"} since the last run.', '']
    for key, r in results.items():
        ns = r['new_since']
        if ns is None:
            lines.append(f"{r['name']}: first run, nothing to compare against yet.")
        else:
            lines.append(f"{r['name']}: {len(ns)} new ({r['season_total']} so far in {r['season_label']})")
            for c in ns:
                lines.append(f"   {c['score']}  {c['w']} def. {c['l']} ({nice_date(c['date'])})")
        lines.append('')
    for key in failed:
        lines.append(f'!! {SPORTS[key]["name"]} did not build. Check the GitHub Actions log.')
    lines.append(f'Full page: {PAGE_URL}')
    return subject, '\n'.join(lines)


def send_email(subject, body):
    import smtplib
    from email.message import EmailMessage
    user = os.environ.get('GMAIL_USER')
    pw = os.environ.get('GMAIL_APP_PASSWORD')
    to = os.environ.get('REPORT_TO') or user
    if not user or not pw:
        print('(email not sent: GMAIL_USER / GMAIL_APP_PASSWORD not set)')
        print(subject); print(body)
        return
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, f'All MO Sports <{user}>', to
    msg.set_content(body)
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=60) as smtp:
        smtp.login(user, pw)
        smtp.send_message(msg)
    print(f'email sent to {to}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sports', nargs='*', default=list(SPORTS))
    ap.add_argument('--football-season', type=int, default=2026)
    ap.add_argument('--basketball-season', type=int, default=2026, help='start year, 2026 = 2026-27')
    ap.add_argument('--email', action='store_true', help='email a summary of new Scorigamis since the last run')
    a = ap.parse_args()
    failed, results = [], {}
    for key in a.sports:
        if key not in SPORTS:
            sys.exit(f'unknown sport {key}; choose from {", ".join(SPORTS)}')
        cur = a.football_season if key == 'football' else a.basketball_season
        try:
            results[key] = build(key, cur)
        except Exception as e:
            failed.append(key)
            print(f'!! {key} failed: {e}')
    if a.email:
        try:
            send_email(*email_report(results, failed))
        except Exception as e:
            print(f'!! email failed: {e}')
            failed.append('email')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
