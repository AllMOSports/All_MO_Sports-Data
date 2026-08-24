# AllMOSports Team-JSON Build Pipeline

## Where these files go -- ALL of them go directly into All_MO_Sports-Data:

- `build_all_team_json.py` -> repo root of All_MO_Sports-Data
- `school_aliases.json` -> All_MO_Sports-Data/output/school_aliases.json
- `.github/workflows/build_team_json.yml` -> All_MO_Sports-Data/.github/workflows/build_team_json.yml

The script and the data/output it reads and writes all live in one repo.
The workflow checks that repo out once, then checks out the relevant
current-season ratings repo as a subdirectory inside it (present on disk
for the script to read, never committed -- the commit step only stages
`output/{sport}/`).

## Setup
1. Commit all 3 files above into All_MO_Sports-Data at the paths shown.
2. From each sport's existing nightly ratings-generation workflow (wherever
   that lives -- e.g. the Boys_Soccer_Ratings repo), add a final job that
   calls this reusable workflow, passing that sport's own already-tracked
   season value:

   ```yaml
   jobs:
     generate-ratings:
       # ... existing steps that produce this season's ratings ...

     build-team-json:
       needs: generate-ratings
       uses: AllMOSports/All_MO_Sports-Data/.github/workflows/build_team_json.yml@main
       with:
         sport: boys_soccer
         season: 2025
       secrets: inherit
   ```

   Do not hardcode season values anywhere else -- each sport's pipeline
   already knows its own correct season (fall/winter/spring sports label
   seasons differently).

## Known gaps as of this build
- **Football**: 2026-season ratings file doesn't exist yet (generator still
  in progress) -- script degrades gracefully and still writes full
  2010-2025 historical data per team.
- **Schedule data**: only Boys Basketball and Football have schedule
  scrapers built. The other 7 sports will simply have an empty
  `current_schedule` field until that infrastructure exists.
- **5 unprefixed `basketball_ratings_*` repos** (2015-16 through 2019-20)
  in the AllMOSports org are confirmed NOT part of this pipeline -- created
  April 2026, different schema (no classification/district), intentionally
  excluded. Consider archiving them on GitHub to avoid future confusion.

## Validation performed
- Full 9-sport alias resolution: 0 unmatched names across ~4,400 total
  team-season records.
- Repo naming confirmed against the live AllMOSports org listing (3
  distinct naming systems found: year-range repos, single-year repos,
  static always-current repos).
- End-to-end run tested against real historical data for all 9 sports:
  3,675 team files written, ~2.4s total processing time.
- Workflow path resolution simulated locally against the exact single-
  checkout-plus-nested-subdirectory layout GitHub Actions will actually
  produce (corrected after an earlier draft assumed a separate sibling
  repo for the script, which would have broken path resolution).
