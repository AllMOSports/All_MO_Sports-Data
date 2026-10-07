# All MO Sports Weekly: newsletter draft builder

Builds a draft of the free weekly newsletter from the ratings and games files
already on GitHub. Nothing is sent automatically; you edit the draft first.

## Files

| File | What it is |
|---|---|
| `build_weekly_newsletter.py` | The script |
| `newsletter_config.yaml` | Settings: which sports are on, list sizes, upset thresholds, featured teams, closing links |
| `.github/workflows/weekly-newsletter-draft.yml` | Runs the script every Monday at 6 AM Central |

## Where to put it

Copy the `newsletter/` folder into the `All_MO_Sports-Data` repo, and move the
workflow file to `.github/workflows/` at the root of that repo.

## Run it yourself

```
pip install pyyaml
cd newsletter
python build_weekly_newsletter.py                         # last full Mon–Sun week
python build_weekly_newsletter.py --week-ending 2026-10-04
python build_weekly_newsletter.py --no-snapshot           # test without saving a snapshot
```

## What it writes

In `newsletter_output/`:

- `snapshots/<date>/<sport>.json`: that day's ratings. Next week's run compares
  against these to show rank movement (▲2, ▼1, new) and to measure upsets with
  pre-game ratings. **The first issue has no movement column; it appears from
  the second week on.**
- `drafts/<week-ending>/preview.html`: open in a browser to read the draft.
- `drafts/<week-ending>/draft_blocks.html`: the draft as WordPress blocks.
- `drafts/<week-ending>/newsletter_data.json`: everything the script found,
  including lead-story ideas, in case you want more than the draft shows.

## Weekly routine (until the WordPress step is connected)

1. Monday morning, open the newest `preview.html` and skim it.
2. In WordPress, add a new post, open the **Code editor** (⋮ menu → Code editor),
   and paste in `draft_blocks.html`. Switch back to the Visual editor.
3. Replace the yellow "✏️" notes with your lead story, then cut or add anything.
4. Publish and send to subscribers.

## Changing what's included

Everything is in `newsletter_config.yaml`:

- Turn a sport off when its season ends: `enabled: false`.
- Add winter sports later by copying a sport block and pointing it at the
  basketball ratings and games files.
- `featured_teams: ["Helias Catholic"]` adds a section with that team's week.
- `upset_min_gap` is per sport because the rating scales differ (football points
  vs. soccer goals).
