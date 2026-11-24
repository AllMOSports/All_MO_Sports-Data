# All MO Sports homepage ("Your Team First"), stage 1

## Files

| File | Where it goes |
|---|---|
| `build_homepage.py` | Repo root of `All_MO_Sports-Data` |
| `.github/workflows/build-homepage.yml` | Same repo |
| `homepage` block | Append to the bottom of `newsletter_config.yaml` (below) |
| `homepage_snippet.html` | WPCode, new HTML snippet |

The builder writes two files the snippet reads:

- `output/homepage/now.json`: what "Right now" shows, Games to Watch Tonight, last night's results, Up next
- `output/homepage/school_index.json`: the search list (about 20 KB, instead of the 1.2 MB `schools.json`)

The team card reads the existing `output/school_reports/{slug}.json`, so nothing new is needed for it.

## Install, in order

1. **Push the three repo files** and append the config block below.
2. **Run it once** from the Actions tab: Build homepage data → Run workflow. Check that `output/homepage/now.json` appears.
3. **Add two cron-job.org jobs** that trigger `build-homepage.yml` the same way as the daily picks job:
   - **6:05 AM Central**, after the 5:49 AM picks lock, so tonight's list is ready for the morning
   - **11:30 PM Central**, which fills in tonight's final scores
4. **WPCode:** add `homepage_snippet.html` as an HTML snippet with Auto Insert off, and put its shortcode on a draft copy of the homepage first.
5. **Theme:** set that page's template to full width with no page title. The snippet breaks out of a narrow content column on its own, but a theme page title would sit above the hero.
6. **Check on your phone**, then swap it in as the homepage. Remove the old featured-sport block, the two promo cards and the italic paragraph; the new page replaces all of them.

## Config block (append to newsletter_config.yaml)

```yaml
homepage:
  out_file: output/homepage/now.json
  games_max: 8                # rows in Games to Watch Tonight
  free_projections: null      # null = show every projection; e.g. 3 = only the top 3 (paid tier later)
  playoff_window_days: 7      # show football title odds when a state-series game is this close
  playoff_teams_per_class: 4
  preview_url: ""             # Matchup Preview link pattern, for later
  cup_url: https://allmosports.com/all-mo-sports-cup/
  cup_released: false         # true = Cup card shows live standings instead of "Coming soon"
  override: null              # put something specific in "Right now" until a date (example in the file)
```

## What runs itself

- **"Right now"** picks its module every run, in this order: your `override` (until its date), football title odds during the state series, Games to Watch Tonight, last night's results, then the Cup card ("Coming soon: under construction" until `cup_released: true`).
- **Games to Watch Tonight** covers every sport under `sports:` in the config. **Adding boys and girls basketball there adds them to the homepage too**; nothing else changes. Games are ranked mostly by both teams' rank within their own class, then by how close the projection is, with a small bonus for district and playoff games.
- **Last night's results** grade the projection that was shown before the game, not one recalculated with post-game ratings.
- **Projections** use the Matchup Predictor formula: football snaps to typical football scores. Sports with `project_scores` in `school_reports.sports` show a score; volleyball shows who's favored.
- **Up next** reads each sport's `start` date in `school_reports.sports`, so basketball shows "starts in N days" on its own.
- **Returning visitors:** the school a visitor picks is remembered in their browser and shown first next time. Logged-in follows can replace this in stage 2.

## Paid tier later

Set `free_projections: 3` and only the top 3 games keep their projection; the rest show "Members" and an "Unlock all of tonight's projected scores" link (to /register/ for now). Until those projections move to the Cloudflare member data, they're still in the public JSON, so treat this as a preview of the paywall rather than a real lock.

## Navigation (WordPress Site Editor → Navigation)

| Menu | Pages |
|---|---|
| Rankings | Football, Boys Basketball, Girls Basketball, Boys Soccer, Girls Volleyball, Fall Softball, Baseball, Spring Softball, Girls Soccer, then All-Time Rankings, Size-Adjusted Rankings, All MO Sports Cup |
| Scores & Playoffs | Scoreboard, Football Playoff Hub, Bracket Predictor, District Points Predictor |
| Schools | Find Your School / School Reports |
| Tools | Matchup Predictors, All-Time Bracket Challenge, Classification Projections, Scorigami |
| About | How Our Ratings Work, Contact |

Known URLs: `/football-rankings/`, `/boys-soccer-rankings/`, `/girls-volleyball-rankings/`, `/fall-softball-rankings/`, `/boys-basketball-rankings/`, `/girls-basketball-rankings/`, `/baseball-rankings-all-teams-2026-season/`, `/spring-softball-rankings-all-classes-2026/`, `/girls-soccer-rankings-all-classes-2026/`, `/all-time-rankings/`, `/football-playoff-hub/`, `/football-postseason-bracket-predictor/`, `/district-points-predictor/`, `/hypothetical-matchups-predictors/`, `/football-all-time-brackets/`, `/projected-classifications/`, `/school-report/`, `/how-our-ratings-work/`, `/contact/`.

Still needed: Scoreboard, Size-Adjusted Rankings, Scorigami and All MO Sports Cup page URLs. Also, the current Contact menu item points to the old `/centered-heading-with-contact-form/`.
