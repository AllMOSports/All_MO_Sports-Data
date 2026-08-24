# AllMOSports Team-JSON Build Pipeline

## Where these files go -- ALL of them go directly into All_MO_Sports-Data:

- `build_all_team_json.py` -> repo root of All_MO_Sports-Data
- `school_aliases.json` -> All_MO_Sports-Data/output/school_aliases.json
- `.github/workflows/build_team_json.yml` -> All_MO_Sports-Data/.github/workflows/build_team_json.yml

## ACTION NEEDED: school_aliases.json was never committed to the real repo

A verification pass against the real committed `output/football/*.json` files
found `output/school_aliases.json` returns 404 -- it was never actually
pushed. The script's `load_aliases()` silently proceeds with zero aliases
when the file is missing (rather than erroring), so the run completed
without any visible failure, but with real data loss:

- 20 team files that should exist (closed schools, charter schools, co-ops
  with no schools.json entry -- Barat Academy, Carnahan, Trinity Catholic,
  Wentworth Military Academy, etc.) were silently skipped entirely.
- Real, currently-active teams that had a co-op season at any point in
  2010-2025 are missing those specific seasons from their history. Confirmed
  concretely on Roosevelt: 12 stats_history seasons in the real output vs.
  15 in a correct run -- the 3 missing seasons are its known co-op years
  ("Roosevelt with Carnahan", "Roosevelt with Cleveland NJROTC").

**Fix**: commit `school_aliases.json` to `output/school_aliases.json`, then
re-run `build_all_team_json.py --sport football --season 2025` (and any
other sport already run without the alias file present). This version of
the script now prints a loud, impossible-to-miss warning if the alias file
is missing, so this specific failure mode won't go undetected again.

## Separate note: current_schedule was empty on all checked football teams

`output/football_schedule_2025.json` exists in the repo now with real data
(376 teams, confirmed games for Rockhurst/Carthage/Farmington/Festus), but
none of that made it into the committed team files. The script's schedule
path logic is confirmed correct against this exact file, so the most likely
explanation is a pipeline-ordering issue -- the team-json build ran before
the schedule file was committed that same run, not a bug in the script
itself. Re-running now (with both files in place) should resolve this;
worth double-checking your workflow's job dependencies so schedule scraping
is sequenced before this build step going forward.

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
  team-season records (when the alias file is actually present).
- Repo naming confirmed against the live AllMOSports org listing (3
  distinct naming systems found: year-range repos, single-year repos,
  static always-current repos).
- End-to-end run tested against real historical data for all 9 sports:
  3,675 team files written, ~2.4s total processing time.
- Workflow path resolution simulated locally against the exact single-
  checkout-plus-nested-subdirectory layout GitHub Actions will actually
  produce.
- Real committed football output cross-checked against local test output:
  found and root-caused the missing-alias-file issue documented above.

## Historical schedule data: lazy-loaded per-team-per-season files

To keep team pages loading fast, the full schedule history is NOT bundled
into the main `output/{sport}/{slug}.json` file. `current_schedule` there
only ever holds the season passed via `--season` (a fast-path duplicate of
the latest year). Every year that has a schedule file on disk gets its own
tiny file instead, one per team per season:

    output/{sport}/schedule_history/{slug}/{year}.json

Have the frontend fetch these on demand -- only when someone actually
clicks into a specific past season's schedule tab -- rather than on the
default page load. Verified end-to-end: correctly picks up every available
season automatically (no need to list years anywhere), and teams that
don't have data for a given year simply don't get a file for it.
