#!/usr/bin/env python3
"""
Build_Football_History_json.py
 
Aggregates historical AllMOSports football ratings (2010-2024) from
per-year GitHub repos into a single consolidated JSON file that the
Sport Detail page's year dropdown can fetch once and slice client-side.
 
Output shape (football_history.json):
{
  "sport": "football",
  "years": [2010, 2011, ..., 2024],
  "teams": {
    "<slug>": {
      "school": "Lafayette (Wildwood)",
      "history": {
        "2010": {
          "ovr_rank": 87, "classification": 6, "district": 3,
          "ovr_rating": 16.65, "off_rating": 8.31, "off_rank": 91,
          "def_rating": 8.33, "def_rank": 92
        },
        "2011": {...},
        ...
      }
    },
    ...
  }
}
 
Usage:
  python Build_Football_History_json.py
"""
 
import json
import re
import unicodedata
import urllib.request
from pathlib import Path
 
# ---------- CONFIG ----------
ORG = "AllMOSports"
YEARS = range(2010, 2025)  # 2010-2024 inclusive
 
# CONFIRM/EDIT: repo containing football-ratings-2010.json actually also
# holds 2011 and 2019 files. If other years live in that SAME repo rather
# than their own "football-ratings-YYYY" repo, update this mapping.
YEAR_REPO_OVERRIDES = {
    # 2011: "football-ratings-2010",
    # 2019: "football-ratings-2010",
}
 
def repo_and_file_for_year(year: int):
    repo = YEAR_REPO_OVERRIDES.get(year, f"football-ratings-{year}")
    fname = f"football_ratings_{year}.json"
    return repo, fname
 
RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/{org}/{repo}/main/{fname}"
 
# Source of truth for slugs (your live schools.json feed) and the
# co-op / renamed-school alias table already used by add_slugs.py.
SCHOOLS_JSON_PATH = "schools.json"   # local path or raw.githubusercontent.com URL
ALIASES_JSON_PATH = "Aliases.json"   # local path or URL; optional
OUTPUT_PATH = "football_history.json"
 
 
# ---------- SLUG HELPERS ----------
def make_slug(name: str) -> str:
    """Fallback slug generator, only used when a school name can't be
    matched against schools.json. Mirrors the fixed make_slug() pattern
    (strips punctuation including parentheses) so it's at least internally
    consistent even if it doesn't line up with the canonical slug."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"\s+", "-", name.strip())
    return name
 
 
def load_json(path_or_url: str):
    if path_or_url.startswith("http"):
        with urllib.request.urlopen(path_or_url) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(path_or_url, "r", encoding="utf-8") as f:
        return json.load(f)
 
 
def build_name_to_slug_map(schools_json_path: str, aliases_json_path: str):
    """Build normalized-name -> slug lookup from schools.json.
 
    ASSUMED SHAPE (edit to match your real schools.json):
      {"schools": [{"name": "Lafayette (Wildwood)", "slug": "lafayette-wildwood"}, ...]}
    or just a top-level list of the same objects.
    """
    schools = load_json(schools_json_path)
    entries = schools.get("schools", schools) if isinstance(schools, dict) else schools
 
    name_to_slug = {}
    for entry in entries:
        nm = entry.get("name") or entry.get("school")
        slug = entry.get("slug")
        if nm and slug:
            name_to_slug[nm.strip().lower()] = slug
 
    aliases = {}
    if aliases_json_path and Path(aliases_json_path).exists():
        aliases = load_json(aliases_json_path)  # {"Old/Co-op Name": "Canonical Name"}
 
    return name_to_slug, aliases
 
 
def resolve_slug(school_name: str, name_to_slug: dict, aliases: dict):
    key = school_name.strip().lower()
    if key in name_to_slug:
        return name_to_slug[key]
 
    canonical = aliases.get(school_name) or aliases.get(school_name.strip())
    if canonical:
        canon_key = canonical.strip().lower()
        if canon_key in name_to_slug:
            return name_to_slug[canon_key]
 
    return None  # caller falls back to a derived slug
 
 
# ---------- MAIN AGGREGATION ----------
def main():
    name_to_slug, aliases = build_name_to_slug_map(SCHOOLS_JSON_PATH, ALIASES_JSON_PATH)
 
    history = {}
    unmatched = set()
    fetched_years = []
 
    for year in YEARS:
        repo, fname = repo_and_file_for_year(year)
        url = RAW_URL_TEMPLATE.format(org=ORG, repo=repo, fname=fname)
        print(f"Fetching {year}: {url}")
        try:
            data = load_json(url)
        except Exception as e:
            print(f"  !! Skipping {year}: {e}")
            continue
 
        fetched_years.append(year)
        for team in data.get("teams", []):
            school_name = team.get("school")
            if not school_name:
                continue
 
            slug = resolve_slug(school_name, name_to_slug, aliases)
            if not slug:
                unmatched.add(school_name)
                slug = make_slug(school_name)
 
            bucket = history.setdefault(slug, {"school": school_name, "history": {}})
            bucket["history"][str(year)] = {
                "ovr_rank": team.get("ovr_rank"),
                "classification": team.get("classification"),
                "district": team.get("district"),
                "ovr_rating": team.get("ovr_rating"),
                "off_rating": team.get("off_rating"),
                "off_rank": team.get("off_rank"),
                "def_rating": team.get("def_rating"),
                "def_rank": team.get("def_rank"),
            }
 
    output = {
        "sport": "football",
        "years": fetched_years,
        "teams": history,
    }
 
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
 
    print(f"\nWrote {OUTPUT_PATH}: {len(history)} teams across years {fetched_years}")
    if unmatched:
        print(f"\n{len(unmatched)} school names had no slug match in schools.json "
              f"(fell back to a derived slug — check these against Aliases.json):")
        for n in sorted(unmatched):
            print(f"  - {n}")
 
 
if __name__ == "__main__":
    main()
