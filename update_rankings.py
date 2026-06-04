"""
ALL MO Sports — Automated Rankings Updater
==========================================
Uses Playwright (headless Chrome) to fully render each page
including JavaScript/AJAX-loaded table data before scraping.
This handles both static tables and dynamically loaded ones.
"""
 
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
 
# ── CONFIG ────────────────────────────────────────────────────
TOP_N         = 5    # teams to show per sport on the homepage
REQUEST_DELAY = 4    # seconds between sport fetches
 
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
    # ── NEW SPORTS ────────────────────────────────────────────
    # IMPORTANT: Verify these URLs match your actual page slugs.
    # Update the 'url' for each sport if the slug is different.
    {
        'abbr':   'BSC',
        'name':   'Boys Soccer',
        'season': '2026 season',
        'url':    'https://allmosports.com/boys-soccer-rankings-all-classes-2026/',
        'badgeBg':'#ede9fe',
        'badgeFg':'#4c1d95',
    },
    {
        'abbr':   'GVB',
        'name':   'Girls Volleyball',
        'season': '2025 season',
        'url':    'https://allmosports.com/girls-volleyball-rankings-all-classes-2025/',
        'badgeBg':'#fdf2ff',
        'badgeFg':'#7e22ce',
    },
    {
        'abbr':   'FST',
        'name':   'Fall Softball',
        'season': '2025 season',
        'url':    'https://allmosports.com/fall-softball-rankings-all-classes-2025/',
        'badgeBg':'#e0f2fe',
        'badgeFg':'#0369a1',
    },
]
 
# ── PARSE HTML → TOP N TEAMS ──────────────────────────────────
def parse_top_n(html, sport_name):
    soup   = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
 
    if not tables:
        print(f"  No tables found in rendered page for {sport_name}")
        return []
 
    # Pick the table with the most header columns
    table   = max(tables, key=lambda t: len(t.find_all('th')))
    all_ths = table.find_all('th')
    print(f"  Table found with {len(all_ths)} columns")
 
    header_names = [
        th.get_text().replace('⇅','').replace('↑','').replace('↓','').strip()
        for th in all_ths
    ]
    print(f"  Headers: {header_names}")
 
    col_map    = {name: i for i, name in enumerate(header_names)}
    rank_col   = col_map.get('OVR Rank',   col_map.get('RANK', 0))
    school_col = col_map.get('School',     col_map.get('SCHOOL', 1))
    ovr_col    = col_map.get('OVR Rating', col_map.get('ADJ. OVR Rating', 8))
    print(f"  Columns -> Rank:{rank_col}  School:{school_col}  OVR:{ovr_col}")
 
    # Get data rows
    tbody = table.find('tbody')
    rows  = tbody.find_all('tr') if tbody else [
        r for r in table.find_all('tr') if r.find(['td','th'])
    ]
    print(f"  Data rows found: {len(rows)}")
 
    # Sanity check — if still showing loading placeholder, bail
    if rows and 'Loading' in rows[0].get_text():
        print(f"  Still showing loading placeholder — AJAX did not complete in time")
        return []
 
    teams = []
    for row in rows[:TOP_N]:
        cells  = row.find_all(['td', 'th'])
        if len(cells) < 3:
            continue
        school = cells[school_col].get_text().strip() if len(cells) > school_col else ''
        rank   = cells[rank_col].get_text().strip()   if len(cells) > rank_col   else ''
        ovr    = cells[ovr_col].get_text().strip()    if len(cells) > ovr_col    else ''
        if not school or school in ('School', 'SCHOOL', 'Team', 'Name'):
            continue
        teams.append({'rank': rank, 'school': school, 'ovr': ovr})
 
    print(f"  Got {len(teams)} teams")
    return teams
 
# ── FETCH WITH PLAYWRIGHT (HEADLESS CHROME) ───────────────────
def get_top_n(sport, page):
    url = sport['url']
    print(f"\n  Fetching {sport['name']}...")
    try:
        page.goto(url, wait_until='networkidle', timeout=60000)
 
        try:
            page.wait_for_function(
                "document.querySelectorAll('table tbody tr').length > 1",
                timeout=10000
            )
        except Exception:
            print(f"  Table did not grow beyond 1 row within 10s — using what we have")
 
        html = page.content()
        return parse_top_n(html, sport['name'])
 
    except Exception as e:
        print(f"  ERROR loading page: {e}")
        return []
 
# ── MAIN ─────────────────────────────────────────────────────
def main():
    print("ALL MO Sports — Rankings Updater")
    print("=================================")
 
    central = ZoneInfo('America/Chicago')
    output = {
        'updated': datetime.now(central).strftime('%B %-d, %Y'),
    }
 
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1280, 'height': 900},
        )
        page = context.new_page()
 
        for i, sport in enumerate(SPORTS):
            teams = get_top_n(sport, page)
            output[sport['abbr']] = {
                'name':    sport['name'],
                'season':  sport['season'],
                'badgeBg': sport['badgeBg'],
                'badgeFg': sport['badgeFg'],
                'url':     sport['url'],
                'teams':   teams,
            }
 
            if i < len(SPORTS) - 1:
                print(f"  Waiting {REQUEST_DELAY}s...")
                time.sleep(REQUEST_DELAY)
 
        browser.close()
 
    with open('rankings.json', 'w') as f:
        json.dump(output, f, indent=2)
 
    print("\nDone — rankings.json updated successfully.")
 
if __name__ == '__main__':
    main()
