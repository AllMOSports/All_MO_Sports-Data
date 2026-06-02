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
# cloudscraper mimics a real Chrome browser and solves
# Cloudflare's JavaScript challenge automatically
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
 
    # Sanity check — if Cloudflare returned a challenge page it
    # will contain no <table> tags and will mention "checking"
    tables = soup.find_all('table')
    if not tables:
        page_text = soup.get_text()[:200]
        print(f"  No tables found. Page preview: {page_text!r}")
        return []
 
    # Use the table with the most header columns
    table   = max(tables, key=lambda t: len(t.find_all('th')))
    headers = table.find_all('th')
    print(f"  Table found with {len(headers)} columns")
 
    # Map column names to indices (strip any sort icons)
    col_map = {}
    for i, th in enumerate(headers):
        name = th.get_text().replace('⇅','').replace('↑','').replace('↓','').strip()
        col_map[name] = i
 
    rank_col   = col_map.get('OVR Rank',   0)
    school_col = col_map.get('School',     1)
    ovr_col    = col_map.get('OVR Rating', 8)
    print(f"  Columns → Rank:{rank_col}  School:{school_col}  OVR:{ovr_col}")
 
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
 
        # Wait between requests — avoids triggering the rate limiter
        if i < len(SPORTS) - 1:
            print(f"  Waiting {REQUEST_DELAY}s before next request...")
            time.sleep(REQUEST_DELAY)
 
    with open('rankings.json', 'w') as f:
        json.dump(output, f, indent=2)
 
    print("\nDone — rankings.json updated successfully.")
 
if __name__ == '__main__':
    main()
 
