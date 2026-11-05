"""All MO Sports Cup — year-long, all-sports school standings.
 
Scoring
  - In each sport, a team earns points from where it ranks within its own MSHSAA class
    in the current-season All MO Sports ratings (regular season and postseason count the same):
        points = 100 * (teams_in_class - rank + 1) / teams_in_class
    e.g. 15th of 123 -> 88.6, 1st -> 100.0
  - A school's cup score is the AVERAGE of its points across every sport it plays (100 = best possible,
    50 = middle of the pack). Every sport counts, so a weak program lowers the average.
  - MIN_SPORTS (4) is the minimum to qualify. It is not enforced until ENFORCE_MIN is set to True
    (planned once winter sports are in), because most schools only have 1-2 fall sports.
  - Co-op teams ("Tipton with Bunceton") are credited to the host school (the first name).
  - Cup class: the school's MSHSAA boys basketball class; all-girls schools use girls basketball;
    any school with neither is placed by enrollment.
 
Sports are listed in SPORTS below. A sport whose ratings file is missing (e.g. basketball before
its season starts) is skipped, so winter and spring sports join automatically once their
ratings files exist.
 
Usage (from the All_MO_Sports-Data repo root):
  python scripts/Build_All_MO_Sports_Cup.py output/cup
Sources can be local paths or https URLs. Set CUP_LOCAL_ROOT to read sibling repos from disk.
"""
import csv, datetime, json, os, sys, urllib.request, zoneinfo
 
SEASON = '2026-27'
MIN_SPORTS = 4
ENFORCE_MIN = False   # flip to True once enough sports are in (e.g. after basketball starts)
RAW = 'https://raw.githubusercontent.com/AllMOSports/{repo}/main/{path}'
 
# key, label, season group, repo, ratings file
SPORTS = [
    ('football',         'Football',          'Fall',   'football-ratings-2026',            'football_ratings_2026.json'),
    ('boys_soccer',      'Boys Soccer',       'Fall',   'Boys_Soccer_Ratings_2026',         'boys_soccer_ratings_2026.json'),
    ('girls_volleyball', 'Girls Volleyball',  'Fall',   'Girls_Volleyball_Ratings_2026',    'girls_volleyball_ratings_2026.json'),
    ('fall_softball',    'Fall Softball',     'Fall',   'Fall_Softball_Ratings_2026',       'fall_softball_ratings_2026.json'),
    ('boys_basketball',  'Boys Basketball',   'Winter', 'Boys_Basketball_Ratings_2026-2027', 'boys_basketball_ratings_2026-2027.json'),
    ('girls_basketball', 'Girls Basketball',  'Winter', 'Girls_Basketball_Ratings_2026-2027', 'girls_basketball_ratings_2026-2027.json'),
    ('baseball',         'Baseball',          'Spring', 'Baseball_Ratings_2027',            'baseball_ratings_2027.json'),
    ('girls_soccer',     'Girls Soccer',      'Spring', 'Girls_Soccer_Ratings_2027',        'girls_soccer_ratings_2027.json'),
    ('spring_softball',  'Spring Softball',   'Spring', 'Spring_Softball_Ratings_2027',     'spring_softball_ratings_2027.json'),
]
ENROLLMENT_SRC = ('football-ratings-2026', 'mshsaa_enrollments_2026-2027.json')
 
OUT = sys.argv[1] if len(sys.argv) > 1 else 'output/cup'
LOCAL = os.environ.get('CUP_LOCAL_ROOT', '')
os.makedirs(OUT, exist_ok=True)
 
 
def load(repo, path):
    """Read a JSON file from a sibling repo on disk (CUP_LOCAL_ROOT) or from GitHub."""
    if LOCAL:
        p = os.path.join(LOCAL, repo, path)
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)
    try:
        with urllib.request.urlopen(RAW.format(repo=repo, path=path), timeout=60) as r:
            return json.load(r)
    except Exception as e:
        print(f'  skip {repo}/{path}: {e}')
        return None
 
 
# ---- school identity ----------------------------------------------------
SCH = json.load(open('output/schools.json'))['schools']
SLUG = dict(json.load(open('school_aliases.json')))
NAME = {}
for slug, v in SCH.items():
    for k in ('mshsaa_name', 'name'):
        if v.get(k):
            SLUG[v[k]] = slug
    NAME[slug] = v.get('name') or v.get('mshsaa_name') or slug
 
 
def host(team_name):
    return team_name.split(' with ')[0].strip()
 
 
def slug_of(name):
    return SLUG.get(name) or name.lower().replace("'", '').replace('.', '').replace('(', '').replace(')', '').replace(' ', '-')
 
 
# ---- cup classes ----------------------------------------------------------
CLS = json.load(open('classifications.json'))['sports']
BB = {t['name']: t['class'] for t in CLS['Boys Basketball']['teams']}
GB = {t['name']: t['class'] for t in CLS['Girls Basketball']['teams']}
enr_raw = load(*ENROLLMENT_SRC) or {'teams': []}
ENR = {t['name']: t['enrollment'] for t in enr_raw['teams']}
 
# Enrollment cut-offs per boys basketball class, for schools without a basketball class
_bb_enr = {}
for t in CLS['Boys Basketball']['teams']:
    if t.get('enrollment'):
        _bb_enr.setdefault(t['class'], []).append(t['enrollment'])
CLASS_MIN = {c: min(v) for c, v in _bb_enr.items()}
 
 
def cup_class(name):
    if name in BB:
        return BB[name], 'boys basketball'
    if name in GB:
        return GB[name], 'girls basketball'
    e = ENR.get(name)
    if e:
        c = 1
        for k in sorted(CLASS_MIN):
            if e >= CLASS_MIN[k]:
                c = k
        return c, 'enrollment'
    return None, 'unknown'
 
 
# ---- points per sport ------------------------------------------------------
schools = {}
sports_meta = []
for key, label, group, repo, path in SPORTS:
    data = load(repo, path)
    if not data or not data.get('teams'):
        sports_meta.append(dict(key=key, label=label, season=group, included=False, updated=None))
        continue
    teams = [t for t in data['teams'] if t.get('classification')]
    by_class = {}
    for t in teams:
        by_class.setdefault(t['classification'], []).append(t)
    for c, grp in by_class.items():
        grp.sort(key=lambda t: -t['ovr_rating'])
        n = len(grp)
        rank, prev = 0, None
        for i, t in enumerate(grp):
            if t['ovr_rating'] != prev:
                rank, prev = i + 1, t['ovr_rating']
            h = host(t['school'])
            sl = slug_of(h)
            s = schools.setdefault(sl, dict(slug=sl, school=NAME.get(sl, h), mshsaa_name=h, sports={}))
            pts = round(100 * (n - rank + 1) / n, 1)
            prev_entry = s['sports'].get(key)
            if prev_entry is None or pts > prev_entry['points']:  # a school with two teams keeps the better one
                s['sports'][key] = dict(points=pts, rank=rank, of=n, cls=c, team=t['school'], ovr=t['ovr_rating'])
    sports_meta.append(dict(key=key, label=label, season=group, included=True, updated=data.get('last_updated'), teams=len(teams)))
    print(f'{label}: {len(teams)} teams, {len(by_class)} classes')
 
included = [m['key'] for m in sports_meta if m['included']]
 
# ---- totals ----------------------------------------------------------------
rows = []
for s in schools.values():
    c, basis = cup_class(s['mshsaa_name'])
    s['cup_class'], s['class_basis'] = c, basis
    s['enrollment'] = ENR.get(s['mshsaa_name'])
    pts = [v['points'] for v in s['sports'].values()]
    s['sports_played'] = len(pts)
    s['score'] = round(sum(pts) / len(pts), 1) if pts else 0
    s['qualified'] = (len(pts) >= MIN_SPORTS) if ENFORCE_MIN else True
    rows.append(s)
 
 
def assign(rank_key, items):
    # qualified schools first, then by score; ties broken by more sports played
    items.sort(key=lambda s: (not s['qualified'], -s['score'], -s['sports_played'], s['school']))
    r, prev = 0, None
    for i, s in enumerate(items):
        key = (s['qualified'], s['score'], s['sports_played'])
        if key != prev:
            r, prev = i + 1, key
        s[rank_key] = r
 
 
assign('rank', rows)
for c in sorted({s['cup_class'] for s in rows if s['cup_class']}):
    assign('class_rank', [s for s in rows if s['cup_class'] == c])
 
# ---- movement vs. about a week ago -------------------------------------------
today = datetime.datetime.now(zoneinfo.ZoneInfo('America/Chicago')).date()
hist_path = os.path.join(OUT, f'all_mo_sports_cup_history_{SEASON}.json')
hist = json.load(open(hist_path)) if os.path.exists(hist_path) else {}
hist[today.isoformat()] = {s['slug']: [s['rank'], s['score']] for s in rows}
dates = sorted(hist)
base = None
for d in dates:
    if datetime.date.fromisoformat(d) <= today - datetime.timedelta(days=7):
        base = d
if base is None:
    earlier = [d for d in dates if d < today.isoformat()]
    base = earlier[0] if earlier else None
for s in rows:
    old = hist.get(base, {}).get(s['slug']) if base else None
    s['change'] = (old[0] - s['rank']) if old else None
json.dump(hist, open(hist_path, 'w'), separators=(',', ':'))
 
# ---- write -------------------------------------------------------------------
rows.sort(key=lambda s: (s['rank'], s['school']))
out = dict(
    season=SEASON,
    generated=datetime.datetime.now(zoneinfo.ZoneInfo('America/Chicago')).strftime('%Y-%m-%dT%H:%M:%S%z'),
    min_sports=MIN_SPORTS,
    min_enforced=ENFORCE_MIN,
    change_since=base,
    sports=sports_meta,
    method='Points per sport = 100 x (teams in class - rank + 1) / teams in class, using current All MO Sports ratings. '
           f'Score = average points across every sport the school plays (minimum {MIN_SPORTS} sports to qualify'
           f'{"" if ENFORCE_MIN else ", not yet enforced"}). Co-ops credited to the host school. Cup class = boys basketball class '
           '(girls basketball for all-girls schools, enrollment otherwise).',
    schools=[{k: s[k] for k in ('rank', 'change', 'slug', 'school', 'cup_class', 'class_rank', 'class_basis', 'enrollment',
                                'score', 'sports_played', 'qualified', 'sports')} for s in rows],
)
with open(os.path.join(OUT, f'all_mo_sports_cup_{SEASON}.json'), 'w') as f:
    json.dump(out, f, separators=(',', ':'))
 
with open(os.path.join(OUT, f'all_mo_sports_cup_{SEASON}.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    head = ['rank', 'change', 'school', 'cup_class', 'class_rank', 'enrollment', 'score', 'sports_played', 'qualified']
    for k in included:
        head += [f'{k}_points', f'{k}_class_rank']
    w.writerow(head)
    for s in rows:
        line = [s['rank'], s['change'] if s['change'] is not None else '', s['school'], s['cup_class'], s.get('class_rank'),
                s['enrollment'], s['score'], s['sports_played'], s['qualified']]
        for k in included:
            v = s['sports'].get(k)
            line += [v['points'], f"{v['rank']} of {v['of']} (C{v['cls']})"] if v else ['', '']
        w.writerow(line)
 
print(f'{len(rows)} schools · sports included: {", ".join(included)} · change vs {base}')
for s in rows[:10]:
    print(f"{s['rank']:>3}. {s['school']:<28} C{s['cup_class']} {s['score']:>6}  ({s['sports_played']} sports)")
