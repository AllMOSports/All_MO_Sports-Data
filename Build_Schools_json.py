"""
build_schools_json.py
 
Builds schools.json by merging TWO separate data sources per sport:
 
  SOURCE 1 -- output/mshsaa_records/<sport>.json
    Produced by scrape_mshsaa_season_records.py. Provides:
    games_played, wins, losses, win_pct, ppg, oppg (raw field names from
    the scraper -- renamed to papg below to match the site's naming),
    and pdpg (computed here as ppg - oppg, not present in the source).
    NOT used for volleyball's ppg/oppg, since volleyball doesn't have
    those fields at all (see DISPLAY_FIELDS_BY_SCHEMA below).
 
  SOURCE 2 -- per-sport ratings repos (e.g. baseball-ratings/ratings.json)
    Provides ovr_rating, off_rating, def_rating (and eventually
    sos_rating, once that effort exists -- it doesn't yet). This is a
    COMPLETELY SEPARATE pipeline from the MSHSAA scrape above. The only
    sport's real URL that's actually been confirmed is baseball
    (https://github.com/AllMOSports/baseball-ratings). The other 8 URLs
    in RATINGS_REPO_URLS below are CONSTRUCTED by guessing the same
    naming pattern -- per the user, these need to be individually
    verified/corrected. Treat every non-baseball URL as a placeholder
    until confirmed.
 
OUTPUT: schools.json
  {
    "generated": "<iso timestamp>",
    "schools": {
      "<slug>": {
        "name": "<canonical/cleaned name>",
        "mshsaa_name": "<raw name as scraped, pre-alias>",
        "sports": {
          "<sport_key>": {
            # Always present:
            "games_played": int, "wins": int, "losses": int, "win_pct": float,
            # Only present for non-volleyball sports (schema A):
            "ppg": float, "papg": float, "pdpg": float,
            # Only present once ratings data exists for that sport:
            "ovr_rating": float, "off_rating": float, "def_rating": float,
            "sos_rating": float | None
          },
          ...
        }
      },
      ...
    },
    "ranges": {
      "<sport_key>": {
        "ovr_rating": {"min": ..., "max": ...},
        "off_rating": {"min": ..., "max": ...},
        "def_rating": {"min": ..., "max": ...},
        "sos_rating": {"min": ..., "max": ...}
      },
      ...
    }
  }
 
  IMPORTANT: ranges are computed ONLY from ratings data (ovr/off/def/sos)
  -- never from ppg/papg/pdpg. Those are displayed as plain numbers on
  the team page, not as scaled bars, per the user's explicit direction.
  Ranges are also computed per-sport, never combined across sports.
 
  IMPORTANT: a sport key is omitted entirely from a school's "sports"
  dict if that school doesn't field that sport at all (confirmed
  preference: omit, don't show as null/empty).
"""
 
import json
from datetime import datetime, timezone
from pathlib import Path
 
import requests
 
# ---------------------------------------------------------------------------
# CONFIG -- edit this section as real paths/URLs are confirmed
# ---------------------------------------------------------------------------
 
# Source 1: MSHSAA scraper output. Confirmed path (user-provided,
# 2026-06-27): output/mshsaa_records/<sport>.json, committed by the
# GitHub Action in scrape-mshsaa-records.yml.
MSHSAA_RECORDS_DIR = "output/mshsaa_records"
 
# Source 2: per-sport ratings repos. ALL 9 verified directly against
# the live raw.githubusercontent.com URLs on 2026-06-27 (each returned
# HTTP 200 with real team data). Note the inconsistent naming/casing
# across repos and that football uses a different filename than the
# rest -- these are not typos, they're just how the repos are actually
# named on GitHub.
RATINGS_REPO_URLS = {
    "football": "https://raw.githubusercontent.com/AllMOSports/football-ratings-2025/main/football_ratings_2025.json",
    "baseball": "https://raw.githubusercontent.com/AllMOSports/baseball-ratings/main/ratings.json",
    "boys_basketball": "https://raw.githubusercontent.com/AllMOSports/Boys_Basketball_Ratings_2025-2026/main/ratings.json",
    "girls_basketball": "https://raw.githubusercontent.com/AllMOSports/Girls_Basketball_Ratings_2025-2026/main/ratings.json",
    "boys_soccer": "https://raw.githubusercontent.com/AllMOSports/Boys_Soccer_Ratings_2025/main/ratings.json",
    "girls_soccer": "https://raw.githubusercontent.com/AllMOSports/Girls-Soccer-Ratings/main/ratings.json",
    "girls_volleyball": "https://raw.githubusercontent.com/AllMOSports/Girls_Volleyball_Rankings_2025/main/ratings.json",
    "fall_softball": "https://raw.githubusercontent.com/AllMOSports/Fall_Softball_Ratings_2025/main/ratings.json",
    "spring_softball": "https://raw.githubusercontent.com/AllMOSports/Spring-Softball-Rankings/main/ratings.json",
}
 
ALIASES_PATH = "data/aliases.json"
OUTPUT_PATH = "output/schools.json"
 
ALL_SPORTS = list(RATINGS_REPO_URLS.keys())
 
# Sports whose MSHSAA records use Schema B (volleyball-style: no
# points_for/points_against at all). Everything not listed here is
# assumed Schema A. Mirrors SPORT_SCHEMA in scrape_mshsaa_season_records.py
# -- kept as a separate constant here since this script doesn't import
# that module (they may run in different repos/contexts).
SCHEMA_B_SPORTS = {"girls_volleyball"}
 
 
# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
 
def load_local_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
 
 
def fetch_remote_json(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()
 
 
def load_aliases(path):
    p = Path(path)
    if not p.exists():
        print(f"  [warn] no aliases file found at {path} -- proceeding with no aliasing")
        return {}
    return load_local_json(p)
 
 
def canonical_name(raw_name, aliases):
    key = raw_name.strip().lower()
    return aliases.get(key, raw_name.strip())
 
 
def make_slug(name):
    slug = name.lower().strip()
    slug = slug.replace("'", "").replace(".", "")
    slug = "-".join(slug.split())
    return slug
 
 
def safe_min_max(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"min": None, "max": None}
    return {"min": min(values), "max": max(values)}
 
 
def round2(n):
    return None if n is None else round(n, 2)
 
 
# ---------------------------------------------------------------------------
# MSHSAA RECORDS (Source 1) -- produces games/wins/losses/win_pct and,
# for schema A sports, ppg/papg/pdpg
# ---------------------------------------------------------------------------
 
def load_mshsaa_record_fields(sport_key, mshsaa_dir):
    """
    Returns {raw_school_name: {display fields...}} for one sport, reading
    from the MSHSAA scraper's output. Field selection here is the site's
    DISPLAY scope, not the full scraped data -- mshsaa_points and the raw
    "mov" field from the scraper are read (mov needed to compute pdpg for
    schema A; for schema B, "mov" IS the displayed stat directly per the
    user, since volleyball's mov already equals sets-won-per-match minus
    sets-lost-per-match) but not separately exposed, except where noted.
    """
    path = Path(mshsaa_dir) / f"{sport_key}.json"
    if not path.exists():
        print(f"  [warn] {sport_key}: no MSHSAA records file at {path}")
        return {}
 
    data = load_local_json(path)
    teams = data.get("teams", [])
 
    is_schema_b = sport_key in SCHEMA_B_SPORTS
    out = {}
 
    for t in teams:
        raw_name = t.get("school")
        if not raw_name:
            continue
 
        if is_schema_b:
            # Volleyball: only games/wins/losses/win_pct are displayed.
            # No ppg/papg/pdpg -- those fields don't exist for this sport.
            fields = {
                "games_played": t.get("games_played"),
                "wins": t.get("wins"),
                "losses": t.get("losses"),
                "win_pct": round2(t.get("win_pct")),
            }
        else:
            ppg = t.get("ppg")
            oppg = t.get("oppg")
            pdpg = (ppg - oppg) if (ppg is not None and oppg is not None) else None
            fields = {
                "games_played": t.get("games_played"),
                "wins": t.get("wins"),
                "losses": t.get("losses"),
                "win_pct": round2(t.get("win_pct")),
                "ppg": round2(ppg),
                "papg": round2(oppg),  # site-facing name for "points allowed per game"
                "pdpg": round2(pdpg),
            }
 
        out[raw_name] = fields
 
    return out
 
 
# ---------------------------------------------------------------------------
# RATINGS (Source 2) -- produces ovr/off/def/sos_rating
# ---------------------------------------------------------------------------
 
def load_ratings_fields(sport_key, url):
    """
    Returns {raw_school_name: {ovr_rating, off_rating, def_rating,
    sos_rating}} for one sport, fetched from that sport's ratings repo.
 
    sos_rating will be None for every team right now -- that pipeline
    (Track 2) doesn't exist yet. Included here so the schema doesn't need
    to change later when it does.
    """
    try:
        data = fetch_remote_json(url)
    except Exception as e:
        print(f"  [warn] {sport_key}: failed to fetch ratings from {url} -- {e}")
        return {}
 
    teams = data.get("teams", [])
    out = {}
 
    for t in teams:
        raw_name = t.get("school")
        if not raw_name:
            continue
        out[raw_name] = {
            "ovr_rating": t.get("ovr_rating"),
            "off_rating": t.get("off_rating"),
            "def_rating": t.get("def_rating"),
            "sos_rating": t.get("sos_rating"),  # always None today -- Track 2 not built
        }
 
    return out
 
 
# ---------------------------------------------------------------------------
# MERGE
# ---------------------------------------------------------------------------
 
def build_schools_json(mshsaa_dir, ratings_urls, aliases_path, output_path):
    aliases = load_aliases(aliases_path)
 
    schools = {}
    ranges = {}
    skipped = []
 
    for sport_key in ALL_SPORTS:
        print(f"Processing {sport_key}...")
 
        mshsaa_fields = load_mshsaa_record_fields(sport_key, mshsaa_dir)
        ratings_fields = load_ratings_fields(sport_key, ratings_urls[sport_key])
 
        # Build a slug -> merged-fields map directly, keyed by the
        # CANONICAL slug rather than the raw name. This matters because
        # the two sources don't always spell a school's name identically
        # (e.g. MSHSAA's scrape might say "Tarkio with Fairfax" while the
        # ratings repo says "Tarkio" -- same physical school, different
        # raw string). Keying by raw name first (as an earlier version of
        # this script did) caused the same school to be processed twice,
        # once per source's spelling, which triggered a spurious
        # "duplicate" warning even though there was no real collision --
        # confirmed by checking that zero cases exist where two
        # GENUINELY DIFFERENT raw names collide within a single sport's
        # own file. Keying by slug from the start merges these correctly
        # in one pass with no false positives.
        merged_by_slug = {}  # slug -> {"display_name", "raw_names": set(), "fields": {}}
 
        for raw_name, fields in mshsaa_fields.items():
            display_name = canonical_name(raw_name, aliases)
            slug = make_slug(display_name)
            entry = merged_by_slug.setdefault(
                slug, {"display_name": display_name, "raw_names": set(), "fields": {}}
            )
            entry["raw_names"].add(raw_name)
            entry["fields"].update(fields)
 
        for raw_name, fields in ratings_fields.items():
            display_name = canonical_name(raw_name, aliases)
            slug = make_slug(display_name)
            entry = merged_by_slug.setdefault(
                slug, {"display_name": display_name, "raw_names": set(), "fields": {}}
            )
            entry["raw_names"].add(raw_name)
            entry["fields"].update(fields)
 
        print(f"  {len(mshsaa_fields)} teams from MSHSAA records, "
              f"{len(ratings_fields)} teams from ratings, "
              f"{len(merged_by_slug)} unique schools after merge")
 
        ovr_values, off_values, def_values, sos_values = [], [], [], []
 
        for slug, entry in merged_by_slug.items():
            display_name = entry["display_name"]
 
            if slug not in schools:
                # Prefer a raw MSHSAA-style name for mshsaa_name when
                # there's a choice -- pick the longest raw name seen
                # (co-op names are typically longer/more descriptive
                # than a ratings repo's possibly-already-cleaned name).
                raw_for_display = max(entry["raw_names"], key=len)
                schools[slug] = {
                    "name": display_name,
                    "mshsaa_name": raw_for_display,
                    "sports": {},
                }
 
            sport_entry = entry["fields"]
 
            if not sport_entry:
                skipped.append({"sport": sport_key, "raw_name": display_name,
                                 "reason": "no data from either source"})
                continue
 
            schools[slug]["sports"][sport_key] = sport_entry
 
            ovr_values.append(sport_entry.get("ovr_rating"))
            off_values.append(sport_entry.get("off_rating"))
            def_values.append(sport_entry.get("def_rating"))
            sos_values.append(sport_entry.get("sos_rating"))
 
        # Ranges computed ONLY from ratings fields, per-sport. Never from
        # ppg/papg/pdpg -- those are plain numbers on the page, not bars.
        ranges[sport_key] = {
            "ovr_rating": safe_min_max(ovr_values),
            "off_rating": safe_min_max(off_values),
            "def_rating": safe_min_max(def_values),
            "sos_rating": safe_min_max(sos_values),  # will be {None, None} until Track 2 exists
        }
 
    output = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schools": schools,
        "ranges": ranges,
    }
 
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, sort_keys=True)
 
    print(f"\nWrote {len(schools)} schools across {len(ALL_SPORTS)} sports to {output_path}")
 
    if skipped:
        print(f"\n{len(skipped)} item(s) flagged during build:")
        for s in skipped[:20]:
            print(f"  - [{s['sport']}] {s['raw_name']}: {s['reason']}")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")
 
    return output
 
 
if __name__ == "__main__":
    build_schools_json(MSHSAA_RECORDS_DIR, RATINGS_REPO_URLS, ALIASES_PATH, OUTPUT_PATH)
 
 
# ---------------------------------------------------------------------------
# FIRST RUN CHECKLIST
# ---------------------------------------------------------------------------
#
# 1. Confirm the 9 MSHSAA record files actually exist locally at
#    output/mshsaa_records/<sport>.json before running this (i.e. run
#    scrape_mshsaa_season_records.py first, or copy its output here).
# 2. RATINGS_REPO_URLS -- all 9 verified directly (HTTP 200, real team
#    data) on 2026-06-27. No further URL fixes should be needed unless
#    a repo is renamed/moved later.
# 3. Run: `python3 build_schools_json.py`
# 4. Check output/schools.json:
#    - Liberty North should have entries for football, baseball,
#      boys_basketball, girls_basketball, boys_soccer, girls_soccer,
#      girls_volleyball, fall_softball -- but NOT spring_softball
#      (confirmed they don't field that team).
#    - Liberty North's football entry should show games_played=12,
#      wins=7, losses=5, ppg=29.92, papg=25.17, pdpg=4.75 (matches the
#      verified MSHSAA scrape from earlier).
#    - Liberty North's girls_volleyball entry should show ONLY
#      games_played, wins, losses, win_pct -- no ppg/papg/pdpg keys at
#      all (schema B).
#    - ovr_rating/off_rating/def_rating will be None/missing for every
#      school until RATINGS_REPO_URLS is fixed for that sport.
#    - sos_rating will be None for every school in every sport -- this
#      is expected, Track 2 doesn't exist yet.
# 5. ranges.<sport>.ovr_rating etc. should show {"min": null, "max": null}
#    until real ratings data is flowing in for that sport.
