# All MO Sports: per-school weekly reports

`build_school_reports.py` builds the "Follow your school" Monday emails: one
HTML email per subscriber, made from the schools and sports they follow.
It sits next to `build_weekly_newsletter.py`, reuses its loaders and
snapshots, and reads its settings from the `school_reports:` block in
`newsletter_config.yaml`.

Nothing is sent. It writes files you can look over first.

## Files

| File | What it is |
|---|---|
| `build_school_reports.py` | The script |
| `newsletter_config.yaml` | New `school_reports:` block at the bottom (season dates, football rounds, history files, colors) |
| `sample_follows.json` | Test subscribers covering the main cases |
| `school_report_snippet.html` | WPCode HTML snippet: the team hub page |
| `school_follows_wpcode.php` | WPCode PHP snippet: follows, picks, follows export |

## Run it

```
python build_school_reports.py --follows sample_follows.json
python build_school_reports.py --follows sample_follows.json --week-ending 2026-11-15
python build_school_reports.py --school st-vincent --sports all
python build_school_reports.py --school st-vincent --sports football,girls_basketball
```

Run it after the Monday newsletter run so that day's ratings snapshot exists.
Pillow (`pip install pillow`) is optional; with it, header colors come from
each school's logo. Without it, every email uses the default colors.

## Follows format

```json
[
 {"id": "123", "email": "parent@example.com",
  "follows": [{"school": "st-vincent", "sports": "all"},
              {"school": "perryville", "sports": ["girls_basketball"]}]}
]
```

`school` is the slug from `schools.json`. `sports` is `"all"` or a list of any of:
`football, boys_soccer, girls_volleyball, fall_softball, boys_basketball,
girls_basketball, baseball, girls_soccer, spring_softball`.

- **"all"** includes every sport plus the All MO Sports Cup, and the big story can come from any of them.
- **A list** includes only those sports. The big story comes only from those sports, and the Cup is left out.
- **Co-ops are found automatically.** St. Vincent's boys soccer is "Saxony Lutheran with St. Vincent".

## How each sport is shown

| State | When | In the email |
|---|---|---|
| live | the team played last week or plays in the next 7 days | record, class and state rank (with change from last week), OVR, last week's games, next game with projection |
| wrapup | the season ended within `wrapup_days` of the sport's last game statewide | one line under "Seasons wrapped" |
| preseason | sport not live yet, within `preseason_days` of its `start` date | last season's record and class rank under "Up next" |
| offseason | anything else | left out |

**Skip rule:** a subscriber whose followed sports are all off-season gets no email that week. They're listed under `skipped` in the manifest.

## The big story

Each section suggests storylines with a score. The email uses the highest-scoring one, and the next three are saved in the manifest as `other_story_ideas`.

| Score | Storyline |
|---|---|
| 100 | State title |
| 95 | District title |
| 85 | Won a playoff round |
| 80 | Season ended in a named football round |
| 74 / 58 | Knocked out of the playoffs (top-10 in class / others) |
| 78 | Most wins in our records |
| 70 | Unbeaten |
| 62 | Upset (beat a team 10+ spots higher in class) |
| up to 75 | Climbed 3+ spots in class |
| 55 | 2nd- or 3rd-most wins in our records |
| 45–49 | Top-5 in class |
| 20–40 | Preseason outlook |
| 40 | Cup climb of 5+ |
| 30 | Season wrap-up |

Change the numbers in `add_live_stories()` if a different story should win.

## Data it uses

- **Current and last-week ratings:** `newsletter_output/snapshots/` (falls back to the live ratings URLs).
- **Games:** the newsletter config's games URLs. Football uses `output/football_schedule_2026.json` because it includes playoff games.
- **W-L records:** MSHSAA's official numbers from `output/season_records/`, falling back to counting the games file.
- **Football:** `matchup_previews_2026.json` (projection, win chance, series) and `playoff_odds_2026.json`.
- **History:** `*_history.json`, for "most wins since…".
- **Last season (winter/spring outlooks):** `output/schools.json`.
- **Cup:** `output/cup/all_mo_sports_cup_2026-27.json`.

## Output

`newsletter_output/school_reports/<week-ending>/`

- `emails/<id>.html`: ready-to-send HTML (inline styles, table layout, hosted logo images). `{{unsubscribe_url}}` is left as a merge tag for your email service.
- `manifest.json`: subject, file and sections for each email, plus who was skipped and why.
- `sections.json`: every school-sport section that was built, for checking numbers.
- `index.html`: a list of every email.

## Keeping it fresh week to week

### 1. Headline memory
`newsletter_output/school_reports/state.json` records which story led each email. It's keyed by the follow set (for example `st-vincent:all`), never by person or email address, so it's safe in a public repo.

- **Same kind of story:** if it led within `headline_memory_days` (21), it loses `repeat_penalty` (40) points, so something new leads.
- **One-time events are exempt.** A playoff win or an upset can lead two weeks in a row. Only the exact same headline counts against them.
- **Which runs save it:** only runs with `--follows`. Use `--no-state` to test without saving. `--school` and `--all-schools` runs never save it.

### 2. Season arc
Each sport's `postseason_start` (approximate, in the config) sets the phase for each live team:

| Phase | When | Extra content |
|---|---|---|
| early | first 3 games | best win so far |
| mid | after that | best win, schedule strength (shown only if top half statewide), football Quad records, district table |
| late | 3 weeks before districts | district table, "top-rated team in the district" story, football projected seed |
| playoffs | from `postseason_start` | rounds, playoff odds |
| wrapup | season over | "Seasons wrapped" row, with "best rating since…" when it applies |

### 3. Milestone watch
When 1–3 more wins would give the program its most wins in 5+ years, or the most in our records, and enough games are left, the email adds a "Milestone watch" line. That can also be the headline.

### 4. This week in program history
The school's best win from the coming week (Mon–Sun) in any past season, for the sports it follows. The search uses `output/{sport}_schedule_*.json`, set under `program_history:` in the config. The best win is chosen in this order: wins over teams that finished with a winning record first, then by beating the projection, then by margin. It's a low-scoring story, so it only leads in quiet weeks.

### 5. District-mates
In mid and late season, a table of every team in the same class and district, sorted by rating, with record and class rank.

### 6. Quiet-week rule
An email counts as having news if a followed team played, has a game in the next week, or moved in class rank (in-season teams only). A season that ended that week, or a preseason preview's first week or start week, also counts. With no news, the follower is skipped, unless their last email was `quiet_resend_days` (28) or more ago; then they get a monthly check-in (`"monthly": true` in the manifest).

### Subject-line rules
Some things never become the subject line: a losing season's wrap-up or playoff exit, a drop in rank, and a preseason preview for a team that finished in the bottom half of its class. They still appear inside the email.

### Re-running past weeks
`--week-ending` on an old week treats games after that week as unplayed, and counts W-L from the games files, because the MSHSAA season records are only used near their scrape date. Some files only exist for today, such as Quad records, seed projections and the Cup, so back-tests show today's values for those.

## Results vs. the projection, and next games

- **Pre-game projections.** Every game is projected from the last ratings snapshot taken before it (within 14 days), using the Matchup Predictor formula (OFF − opponent DEF + league average). The game itself is never baked in. Football falls back to the schedule file's projection when no snapshot is close enough.
- **The email's "Last week" lines** show the projected score and how far the offense and defense were above (+) or below (−) it. Lopsided or impossible projections (beyond `blowout_margin`, or a negative score) are left out.
- **Tags:**
  - "best offensive / defensive / all-around game of the season", when the team has at least 4 comparable games
  - "won as the underdog"
- **New story:** winning by `beat_margin` or more over the projection: "beats its projection by 17 in a 42–25 win".
- **Next game:** last meeting, plus the series record in our data (current and past schedule files), and the matchup-preview link from `preview_page`.
- **"Class rank by week" bars:** a plain HTML table, so it shows in every email client.

## Web report page: the team hub (one per school)

`--pages` writes `output/school_reports/<slug>.json` for every school, plus `index.json` for the school search. It takes about 15 seconds for all schools; the workflow runs it every time and commits the files.

### What visitors see (`/school-report/?school=st-vincent`)

- **A team bar** in school colors that stays at the top of the screen, with a **Follow** button.
- **The week's big story.**
- **Sport tabs:**
  - a green dot marks sports in season
  - "soon" marks sports starting soon
  - finished seasons are labeled "final"
  - the page opens on the first sport in season, and `#football` etc. in the address opens a specific tab
- **Each sport's tab:**
  - record and class rank
  - three stat boxes
  - **the season strip:** one tile per game. Dark green means a win that beat the projected margin, light green a win, red a loss, and a dashed outline an upcoming game. Tap a tile for the score, projection and margin against it.
  - the next game, with the projection (or "heavily favored"), the series record and **pick buttons** showing pick percentages
  - milestone watch, best win, Quad records and seed watch
- **For followers only:** trend charts, every game against the projection, and the district table. Everyone else sees a blurred preview with a **Follow free** button.
- **Preseason tabs** show last season's record and rank, the start date and a Follow button.
- **At the bottom:** the All MO Sports Cup and program history.

The lock is a soft one: the data files are public on GitHub, so the lock steers people toward following rather than securing anything. Real paid-only content will need a server-side check later.

### Files and setup

| File | WPCode settings |
|---|---|
| `school_report_snippet.html` | HTML Snippet, Insert Method **Shortcode**. Put the shortcode on the page with the slug `school-report`. |
| `school_follows_wpcode.php` | PHP Snippet, Insert Method **Auto Insert**, Location **Run Everywhere**. |

Also check these WordPress settings:
1. **Time zone:** Settings → General should be **Chicago**, so picks close on game day in Missouri time.
2. **After sign-up:** set Paid Member Subscriptions to send people back where they came from after registering, or at least to a page that links back. The page also adds a `redirect_to` value to the sign-up link.

### How follows work

- **Logged out, tap Follow:** the page saves the school in an `amos_follow` cookie (good for 1 day) and opens `/register/`. When the account is created, or when someone with an account logs in, the PHP snippet saves the follow.
- **Logged in, tap Follow:** it's saved right away, and the charts and tables unlock.
- **Where follows are stored:** user meta `amos_follows`, in the same format as `sample_follows.json`: `[{"school": "st-vincent", "sports": "all"}]`.
- **School check:** the PHP snippet accepts only schools listed in the published `index.json`, cached for 12 hours.

### How picks work

- **Saving:** logged-in users pick a side, and can change it until game day. A pick is stored in user meta `amos_picks`, keyed by the game ID `sport|date|Team A|Team B`, which is the same on both schools' pages.
- **Totals:** public pick counts per game are kept in the `amos_pick_counts` option, which drops games older than 60 days.
- **Leaderboard:** not built yet. The picks are saved, so one can be added later.

### API (`/wp-json/amos/v1/`)

| Route | Who | What |
|---|---|---|
| `GET me` | anyone | the logged-in user's follows and picks |
| `POST follow` `{school, sports}` | logged in | follow ("all" or a list of sport keys) |
| `POST unfollow` `{school}` | logged in | stop following |
| `POST pick` `{game, team}` | logged in | pick a winner (until the game date) |
| `GET picks?games=…` | anyone | pick counts |
| `GET follows-export` | admins | every user's follows plus email, for the weekly email job |

`follows-export` contains email addresses. Keep its output out of the public repo.

Neither snippet uses `&&` operators.

### Data clean-up in this version

- **Past games with no score** (cancelled or never reported) are dropped instead of showing as "upcoming".
- **Out-of-state opponents** are shortened, e.g. "Murphysboro(Murphysboro, IL)" becomes "Murphysboro (IL)", in emails, pages, meetings and program history.
- **Preseason tabs** say "Last season: 22-7" instead of showing last season's record as this season's.

**Trend charts** start from the first weekly snapshot (Oct. 7 for fall 2026). When two snapshots are 2 days apart or less, only the later one is used.

## Things to update each season

- `school_reports.football.rounds`: the postseason Friday dates.
- `school_reports.sports.*.start/end`: approximate first and last game dates for winter and spring sports.
- `school_reports.sports.*.postseason_start`: the first day of district play for each sport. These are approximate; check them.
- When basketball ratings go live, add basketball to the newsletter `sports:` block. Its sections then switch from "Up next" to full live sections, with no code changes.

## Member-only data

The trend charts and district table can be served only to members through a Cloudflare data server. See README_member_data.md.
