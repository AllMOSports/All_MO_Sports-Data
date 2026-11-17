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

## Things to update each season

- `school_reports.football.rounds`: the postseason Friday dates.
- `school_reports.sports.*.start/end`: approximate first and last game dates for winter and spring sports.
- When basketball ratings go live, add basketball to the newsletter `sports:` block. Its sections then switch from "Up next" to full live sections, with no code changes.
