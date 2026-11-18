# Member-only data (Cloudflare data server)

Free data keeps loading straight from GitHub, as it does today. Anything for members only is
served by a small **Cloudflare Worker** that reads from a private **R2 bucket**. Visitors with
the right level get it in about the time a GitHub file takes; nobody else can get it at all.

```
            free data ─────────────────────────────────────────────►  GitHub (public repo)
 browser ─┤
            member data ── ?pass=… ──►  Cloudflare Worker ──►  R2 bucket (private)
                              ▲                                   ▲
            WordPress prints a signed 2-hour pass         GitHub Actions upload
            for logged-in visitors (no extra request)      private_output/ after each build
```

## The pieces

| File | What it does |
|---|---|
| `access.json` | **Who can see what.** Levels: `public`, `account`, `pro`, `program`. Each feature gets a level; each folder on the data server gets a feature. The only file to edit when you move something behind Pro. |
| `cloudflare/worker.js` | The data server. Checks the pass, looks up the file's level in `access.json`, serves the file. |
| `school_follows_wpcode.php` | Works out each visitor's level from their Paid Member Subscriptions plan, gives logged-in visitors a pass (`window.AMOS.pass`), adds `level` and `features` to `/me`, and adds `GET /wp-json/amos/v1/pass` for a fresh pass. Use `amos_can('feature')` in any PHP. |
| `school_report_snippet.html` | The team hub loads its member-only parts (trend charts, district table) from the data server. |
| `build_school_reports.py` + `newsletter_config.yaml` | With `private_data.enabled: true`, the member-only fields move out of the public school files into `private_output/school_reports/`. |
| `.github/workflows/school-reports.yml` | Uploads `private_output/` and `access.json` to R2 after each build. Skips quietly until the secrets exist. |
| `.github/workflows/sync-access.yml` | Uploads `access.json` whenever you change it. |
| `.gitignore` | Keeps `private_output/` out of the public repo. |

Levels today: every logged-in account is `account`, site admins are `program`, logged-out
visitors are `public`. Paid plans map to `pro` or `program` once you create them (step 9).

## One-time setup

Do these in order. Until step 8, the site works exactly as it does now.

### 1. Cloudflare account and bucket
1. Sign up at cloudflare.com (free). You don't need to move your domain or DNS.
2. In the dashboard, open **R2 Object Storage**. Cloudflare asks for a payment method to turn R2
   on, even on the free tier; at your size you should stay inside the free allowance (check
   Cloudflare's current R2 and Workers limits on their pricing page).
3. **Create bucket** → name it `amos-member-data` → default settings. Leave public access **off**.

### 2. A key for GitHub to upload with
1. R2 → **Manage API tokens** → **Create API token**.
2. Permission: **Object Read & Write**, limited to the `amos-member-data` bucket.
3. Copy the **Access Key ID**, **Secret Access Key**, and your **Account ID** (shown on the R2
   overview page). The secret is shown once.

### 3. GitHub secrets
In the `All_MO_Sports-Data` repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add four:

| Name | Value |
|---|---|
| `R2_ACCOUNT_ID` | your Cloudflare Account ID |
| `R2_ACCESS_KEY_ID` | the Access Key ID |
| `R2_SECRET_ACCESS_KEY` | the Secret Access Key |
| `R2_BUCKET` | `amos-member-data` |

Then **Actions → Sync access rules → Run workflow**. It should finish green. That puts the
access rules in the bucket.

### 4. The pass secret
Make one long random value, at least 48 letters and numbers, with a password manager's
generator. Don't put it in chat, email, or the repo. It goes in exactly two places: the Worker
(step 5) and WordPress (step 6).

### 5. The Worker
1. **Workers & Pages → Create → Create Worker** → name it `amos-data` → **Deploy**.
2. **Edit code** → delete the sample → paste all of `cloudflare/worker.js` → **Deploy**.
3. **Settings → Bindings → Add → R2 bucket**: variable name `DATA`, bucket `amos-member-data`.
4. **Settings → Variables and Secrets → Add**:
   - Type **Text**, name `ALLOWED_ORIGINS`, value `https://allmosports.com,https://www.allmosports.com`
   - Type **Secret**, name `PASS_SECRET`, value: the secret from step 4
5. Copy the Worker's address (like `https://amos-data.yourname.workers.dev`) and open
   `…/health` in a browser. You should see `{"ok":true}`.

### 6. WordPress
1. Add the secret to WordPress. Best place: `wp-config.php`, above the "That's all, stop editing" line:
   ```php
   define( 'AMOS_PASS_SECRET', 'paste-the-secret-here' );
   ```
   If your host doesn't let you edit `wp-config.php`, make a separate WPCode PHP snippet
   (Auto Insert, Run Everywhere) with just that line, and keep it private.
2. Replace the PHP snippet with the new `school_follows_wpcode.php`, and in it set:
   ```php
   define( 'AMOS_DATA_URL', 'https://amos-data.yourname.workers.dev' );
   ```
3. Replace the team hub HTML snippet with the new `school_report_snippet.html`.
4. Check: logged in, open `/wp-json/amos/v1/pass`. You should see a `pass`, your `level`
   (`program`, since you're an admin) and `features`.

### 7. Upload the files
Upload to the repo: `access.json`, `.gitignore`, `cloudflare/`, `build_school_reports.py`,
`newsletter_config.yaml`, both workflow files, and this README.

### 8. Switch it on
1. In `newsletter_config.yaml`, under `school_reports:` → `private_data:`, change
   `enabled: false` to `enabled: true` and commit.
2. **Actions → School reports → Run workflow** (defaults are fine).
3. In the run's log, the **Upload member data to Cloudflare** step should say
   `Uploaded 574 member files`.
4. Open `/school-report/?school=st-vincent` logged in, following St. Vincent. The trend charts
   and district table should appear, and in the browser's Network tab you'll see one request to
   your `workers.dev` address.

From now on the public files on GitHub no longer contain the trend or district table.

### 9. Later: paid plans
1. Create the plans in Paid Member Subscriptions and note each plan's ID.
2. In the PHP snippet: `define( 'AMOS_PLAN_LEVELS', array( 123 => 'pro', 124 => 'program' ) );`
3. Make a page at `/pro/` (the "See Pro" button points there).
4. In `access.json`, change a feature's level (for example `"school_deep": "pro"`) and commit.
   The Worker picks it up within 5 minutes and WordPress within an hour.

## Adding a new member-only data set
1. Have the build script write it under `private_output/<folder>/`.
2. Add the folder and a feature to `access.json`:
   `"files": {"school_reports/": "school_deep", "team_history/": "team_history"}`,
   `"features": {…, "team_history": "account"}`.
3. Have the workflow that builds it run the same upload step as `school-reports.yml`.
4. In the page snippet, load it with the same pass pattern (`memberJSON("team_history/<slug>.json")`)
   and check `canUse("team_history")` to decide between the content and the upgrade box.

## Account sharing
- Each account can be logged in on **3 devices** at once (`AMOS_MAX_SESSIONS` in the PHP snippet).
  Logging in on a 4th signs out the oldest. Program plans get 10 (`AMOS_MAX_SESSIONS_PROGRAM`);
  site admins have no limit.
- Passes last 2 hours (`AMOS_PASS_HOURS`), so a signed-out device loses member data soon after.
- The limit uses WordPress's own login-session list. If a host or plugin swaps that out, the
  limit quietly turns itself off rather than breaking logins.

## How safe is it?
- Passes are signed with HMAC-SHA256 and expire after 2 hours. Changing the level or the
  expiry breaks the signature, so the Worker rejects it. Tested: no pass, expired, tampered,
  wrong level, unlisted file, `..` paths, and the rules file itself are all refused.
- Someone who is legitimately logged in could copy their own pass and use it for up to 2 hours.
  That's the normal trade-off for any paywall and isn't worth more machinery.
- If a plan lapses, the next pass reflects it; an old pass stops working within 2 hours.
- If the secret ever leaks: make a new one and update it in both places. Every old pass stops working at once.
- Files committed to GitHub before step 8 stay in the repo's history. Only data from then on is private.
