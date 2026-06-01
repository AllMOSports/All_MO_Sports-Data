"""
ALL MO Sports — Automated Rankings Updater
==========================================
This script is run automatically every night by GitHub Actions.
It fetches each sport's rankings page, extracts the top 5 teams,
and saves the result to rankings.json.

To add or remove sports, edit the SPORTS list below.
To change how many teams appear in the snapshot, change TOP_N.
"""

import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────
TOP_N = 5   # how many teams to show per sport on the homepage

SPORTS = [
    {
        'abbr':   'FTB',
        'name':   'Football',
        'season': '2025 season',
        'url':    'https://allmosports.com/ftb-2025-all-classes-rankings/',
        'badgeBg':'#e6f1fb',
        'badgeFg':'#185fa5',
    },
    {
        'abbr':   'BBB',
        'name':   'Boys Basketball',
        'season': '2025-26 season',
        'url':    'https://allmosports.com/2025-2026-boys-basketball-all-teams-rankings/',
        'badgeBg':'#faeeda',
        'badgeFg':'#854f0b',
    },
    {
        'abbr':   'GBB',
        'name':   'Girls Basketball',
        'season': '2025-26 season',
        'url':    'https://allmosports.com/girls-basketball-all-classes-rankings-25-26/',
        'badgeBg':'#fbeaf0',
        'badgeFg':'#993556',
    },
    {
        'abbr':   'BSB',
        'name':   'Baseball',
        'season': '2026 season',
        'url':    'https://allmosports.com/baseball-rankings-all-teams-2026-season/',
        'badgeBg':'#e1f5ee',
        'badgeFg':'#0f6e56',
    },
    {
        'abbr':   'GSC',
        'name':   'Girls Soccer',
        'season': '2026 season',
        'url':    'https://allmosports.com/girls-soccer-rankings-all-classes-2026/',
        'badgeBg':'#eaf3de',
        'badgeFg':'#3b6d11',
    },
    {
        'abbr':   'SFT',
        'name':   'Spring Softball',
        'season': '2026 season',
        'url':    'https://allmosports.com/spring-softball-rankings-all-classes-2026/',
        'badgeBg':'#faece7',
        'badgeFg':'#993c1d',
    },
]

# ── FETCH HELPERS ─────────────────────────────────────────────
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}

def get_top_n(sport):
    url = sport['url']
    print(f"  Fetching {sport['name']} from {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching {sport['name']}: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Find the table with the most header columns
    tables = soup.find_all('table')
    if not tables:
        print(f"  No tables found on page for {sport['name']}")
        return []

    table = max(tables, key=lambda t: len(t.find_all('th')))

    # Build column name → index map (strip any sort icons)
    col_map = {}
    for i, th in enumerate(table.find_all('th')):
        name = th.get_text().replace('⇅','').replace('↑','').replace('↓','').strip()
        col_map[name] = i

    rank_col   = col_map.get('OVR Rank',   0)
    school_col = col_map.get('School',     1)
    ovr_col    = col_map.get('OVR Rating', 8)

    print(f"  Columns found: {list(col_map.keys())[:6]}...")
    print(f"  Using → Rank:{rank_col}  School:{school_col}  OVR:{ovr_col}")

    # Get data rows
    tbody = table.find('tbody')
    rows  = tbody.find_all('tr') if tbody else [
        r for r in table.find_all('tr') if r.find('td')
    ]

    teams = []
    for row in rows[:TOP_N]:
        cells = row.find_all('td')
        if len(cells) < 3:
            continue
        school = cells[school_col].get_text().strip() if len(cells) > school_col else ''
        if not school:
            continue
        teams.append({
            'rank':   cells[rank_col].get_text().strip() if len(cells) > rank_col else '',
            'school': school,
            'ovr':    cells[ovr_col].get_text().strip()  if len(cells) > ovr_col  else '',
        })

    print(f"  Found {len(teams)} teams")
    return teams

# ── MAIN ─────────────────────────────────────────────────────
def main():
    print("ALL MO Sports — Rankings Updater")
    print("=================================")

    output = {
        'updated': datetime.now(timezone.utc).strftime('%B %-d, %Y'),
    }

    for sport in SPORTS:
        teams = get_top_n(sport)
        output[sport['abbr']] = {
            'name':    sport['name'],
            'season':  sport['season'],
            'badgeBg': sport['badgeBg'],
            'badgeFg': sport['badgeFg'],
            'url':     sport['url'],
            'teams':   teams,
        }

    with open('rankings.json', 'w') as f:
        json.dump(output, f, indent=2)

    print("\nDone — rankings.json updated successfully.")

if __name__ == '__main__':
    main()
