"""
ALL MO Sports — Automated Rankings Updater
==========================================
Uses cloudscraper to bypass Cloudflare bot protection,
and adds delays between requests to avoid rate limiting.
"""
 
import cloudscraper
from bs4 import BeautifulSoup
import json
import time
from datetime import datetime, timezone
 
# ── CONFIG ────────────────────────────────────────────────────
TOP_N         = 5    # teams to show per sport on the homepage
REQUEST_DELAY = 5    # seconds to wait between each sport fetch
 
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
 
# ── SCRAPER SETUP ─────────────────────────────────────────────
scraper = cloudscraper.create_scraper(
    browser={
        'browser':  'chrome',
        'platform': 'windows',
        'mobile':   False,
    }
)
 
# ── PARSE ─────────────────────────────────────────────────────
def get_top_n(sport):
    url = sport['url']
    print(f"  Fetching {sport['name']}...")
 
    try:
        resp = scraper.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR: {e}")
        return []
 
    soup = BeautifulSoup(resp.text, 'html.parser')
 
    tables = soup.find_all('table')
    if not tables:
        print(f"  No tables found. Page snippet: {soup.get_text()[:150]!r}")
        return []
 
    # Use the table with the most columns
    table   = max(tables, key=lambda t: len(t.find_all('th')))
    all_ths = table.find_all('th')
    print(f"  Table found with {len(all_ths)} columns")
 
    # Print ALL header names so we can see the exact column structure
    header_names = [th.get_text().replace('⇅','').replace('↑','').replace('↓','').strip()
                    for th in all_ths]
    print(f"  Headers: {header_names}")
 
    # Map column names to indices
    col_map = {name: i for i, name in enumerate(header_names)}
 
    rank_col   = col_map.get('OVR Rank',   0)
    school_col = col_map.get('School',     1)
    ovr_col    = col_map.get('OVR Rating', 8)
    print(f"  Columns -> Rank:{rank_col}  School:{school_col}  OVR:{ovr_col}")
 
    # Get data rows from tbody; fall back to any row containing cells
    tbody = table.find('tbody')
    rows  = tbody.find_all('tr') if tbody else [
        r for r in table.find_all('tr') if r.find(['td', 'th'])
    ]
    print(f"  Data rows found: {len(rows)}")
 
    # Debug: print the first row's raw content so we can see cell structure
    if rows:
        first_cells_raw = [str(c)[:60] for c in rows[0].find_all(['td', 'th'])[:4]]
        print(f"  First row sample: {first_cells_raw}")
 
    teams = []
    for row in rows[:TOP_N]:
        # FIX: use both <td> and <th> tags so tables that use <th>
        # for data cells (common in some WordPress table plugins) still work
        cells = row.find_all(['td', 'th'])
 
        if len(cells) < 3:
            continue
 
        school = cells[school_col].get_text().strip() if len(cells) > school_col else ''
        rank   = cells[rank_col].get_text().strip()   if len(cells) > rank_col   else ''
        ovr    = cells[ovr_col].get_text().strip()    if len(cells) > ovr_col    else ''
 
        # Skip rows that look like sub-headers (school cell same as a header name)
        if not school or school in ('School', 'Team', 'Name'):
            print(f"  Skipping row — school cell: {school!r}")
            continue
 
        teams.append({'rank': rank, 'school': school, 'ovr': ovr})
 
    print(f"  Got {len(teams)} teams")
    return teams
 
# ── MAIN ─────────────────────────────────────────────────────
def main():
    print("ALL MO Sports — Rankings Updater")
    print("=================================")
 
    output = {
        'updated': datetime.now(timezone.utc).strftime('%B %-d, %Y'),
    }
 
    for i, sport in enumerate(SPORTS):
        teams = get_top_n(sport)
        output[sport['abbr']] = {
            'name':    sport['name'],
            'season':  sport['season'],
            'badgeBg': sport['badgeBg'],
            'badgeFg': sport['badgeFg'],
            'url':     sport['url'],
            'teams':   teams,
        }
 
        if i < len(SPORTS) - 1:
            print(f"  Waiting {REQUEST_DELAY}s...\n")
            time.sleep(REQUEST_DELAY)
 
    with open('rankings.json', 'w') as f:
        json.dump(output, f, indent=2)
 
    print("\nDone — rankings.json updated successfully.")
 
if __name__ == '__main__':
    main()
