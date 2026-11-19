# Weekly school report emails (sent from Gmail)

Every Monday, after the newsletter run, followers get one email covering every school they
follow, sent from **allmosports63775@gmail.com**.

```
 GitHub "School reports" run (automatic, after "Weekly newsletter draft")
   1. asks WordPress who follows what ── account numbers + schools only, no email addresses
   2. builds one email per follower
   3. uploads the emails to the private Cloudflare bucket (never to GitHub)
   4. tells WordPress "this week's emails are ready"
 WordPress
   5. sends them through WP Mail SMTP → Gmail, 20 at a time, at most 400 a day
      (anything left over goes the next morning at 6:00)
```

Email addresses never leave WordPress except to go to Gmail.

## The pieces

| File | Where | What it does |
|---|---|---|
| `school_emails_wpcode.php` | New WPCode PHP snippet | Followers list for GitHub, the sending queue, unsubscribe page, email on/off, the `[amos_my_schools]` page |
| `school_follows_wpcode.php` | Replace your follows snippet | `/me` now includes level, features and the email setting; the old admin export with email addresses is removed |
| `cloudflare/worker.js` | Paste into the Worker again | Adds the `system` level (WordPress only) and serves the email files |
| `access.json` | Repo | `emails/` is readable only by WordPress itself, not even by admin accounts |
| `build_school_reports.py`, `newsletter_config.yaml` | Repo | Private email folder, postal address in the footer, emails link to `/my-schools/` |
| `.github/workflows/school-reports.yml` | Repo | Gets followers, uploads emails, tells WordPress to send |

## Modes (in the email snippet)

| `AMOS_EMAIL_MODE` | Who gets an email |
|---|---|
| `'off'` | Nobody |
| `'test'` (the default) | Site admins and the accounts in `AMOS_EMAIL_TEST_USERS` only |
| `'live'` | Every follower who hasn't turned emails off |

Everything else runs the same in every mode, so test mode is a true dress rehearsal.

## One-time setup

### 1. Gmail: an app password
WordPress signs in to Gmail with an "app password," a separate 16-letter password that only
works for sending mail and can be revoked anytime without changing your real password.

1. Sign in to Google as **allmosports63775@gmail.com** → **Manage your Google Account → Security**.
2. Turn on **2-Step Verification** if it isn't already (Google requires it for app passwords).
3. Search the account settings for **App passwords** (or go to myaccount.google.com/apppasswords).
4. Name it `All MO Sports website` → **Create**. Copy the 16 letters (spaces don't matter).
   You'll only see it once. Don't put it in chat or in GitHub.

### 2. WordPress: WP Mail SMTP
1. **Plugins → Add New** → search **WP Mail SMTP** (by WPForms) → Install → Activate. Skip its setup wizard.
2. **WP Mail SMTP → Settings**:
   - **From Email:** `allmosports63775@gmail.com`, and tick **Force From Email**
   - **From Name:** `All MO Sports`, and tick **Force From Name**
   - **Mailer:** **Other SMTP**
   - **SMTP Host:** `smtp.gmail.com` · **Encryption:** TLS · **SMTP Port:** `587`
   - **Authentication:** on · **SMTP Username:** `allmosports63775@gmail.com` · **SMTP Password:** the app password
   - **Save Settings**
3. **WP Mail SMTP → Tools → Email Test** → send to your personal email. If it arrives, sending works.
   If it fails, send me the error it shows.

This also fixes WordPress's own emails (password resets, sign-up confirmations), which will now
come from your Gmail too.

### 3. A key for GitHub to ask for the followers list
1. Make a second random value the same way as the pass secret (PowerShell):
   ```powershell
   $b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ([BitConverter]::ToString($b) -replace '-','')
   ```
2. Add it to your private secrets snippet in WPCode, under the pass secret:
   ```php
   if ( ! defined( 'AMOS_FOLLOWS_TOKEN' ) ) {
   	define( 'AMOS_FOLLOWS_TOKEN', 'paste-the-new-value-here' );
   }
   ```
3. GitHub → `All_MO_Sports-Data` → **Settings → Secrets and variables → Actions → New repository secret**:
   name `WP_FOLLOWERS_KEY`, same value.

### 4. Cloudflare: update the Worker code
**Workers & Pages → amos-data → Edit code** → select all, paste the new `cloudflare/worker.js` → **Deploy**.
(Your variables and the bucket link stay as they are.)

### 5. WordPress: snippets and the My schools page
1. Replace your follows PHP snippet with the new `school_follows_wpcode.php`. Keep your
   `AMOS_DATA_URL` line (`https://amos-data.allmosports63775.workers.dev`).
2. **Add Snippet → PHP Snippet** → paste `school_emails_wpcode.php` (drop a first `<?php` line if
   there is one) → Auto Insert, Run Everywhere → Active → Save.
3. In that snippet, put your personal test account's number in the test list:
   ```php
   define( 'AMOS_EMAIL_TEST_USERS', array( 17 ) );
   ```
   To find the number: **Users → All Users** → click the account → the address bar shows `user_id=17`.
4. **Pages → Add New** → title **My schools**, address (slug) `my-schools` → add a **Shortcode**
   block with `[amos_my_schools]` → Publish. Followers use it to change sports, unfollow, and
   turn emails on or off. Every email links to it.
5. **Settings → General → Timezone:** make sure it's **Chicago**, so "tomorrow at 6:00" means 6:00 Central.

### 6. Repo files
Upload `access.json`, `build_school_reports.py`, `newsletter_config.yaml`,
`.github/workflows/school-reports.yml` and `cloudflare/worker.js`. Changing `access.json` runs
**Sync access rules** on its own.

In `newsletter_config.yaml`, fill in `mailing_address:` under `school_reports:`. US anti-spam law
(CAN-SPAM) asks for a postal address in emails like these. A P.O. box keeps your home address private.

### 7. Optional but recommended: keep WordPress's clock running
WordPress only runs scheduled jobs when someone visits the site, so a quiet Monday morning could
delay sending. You already use cron-job.org: add a job that opens
`https://allmosports.com/wp-cron.php` every 5 minutes.

## Test it

1. Make sure your personal account follows a school and its email setting is on (My schools page).
2. **Actions → School reports → Run workflow**: Whose emails = **real followers from WordPress**,
   tick **Send** → Run.
3. In the run's log:
   - **Get followers from WordPress:** `N followers`
   - **Upload member data to Cloudflare:** `Uploaded N email files.`
   - **Tell WordPress to send:** `{"week":"…","queued":N,"mode":"test"}`
4. Within a few minutes your personal inbox gets the email. (Visit any page of the site if it's slow.)
5. Try the email's **Unsubscribe** link: you should get a confirm page, then "You're unsubscribed."
   Turn emails back on from **My schools**.
6. To see how sending is going, open any page of the site logged in as admin, press F12 → Console:
   ```js
   fetch(AMOS.rest + "email-status", {headers: {"X-WP-Nonce": AMOS.nonce}}).then(r => r.json()).then(console.log)
   ```
   It shows the mode, how many are left, how many went today, and per week: sent, skipped
   (and why), failed.

Running the test again for the same week sends nothing new. Each account gets each week once.

## Going live
In the email snippet change `define( 'AMOS_EMAIL_MODE', 'test' );` to `'live'`. From the next
Monday on, the run after "Weekly newsletter draft" sends to everyone automatically.

## Things to know
- **Gmail's limit.** A regular Gmail account sends roughly 500 emails a day. The cap is 400
  (`AMOS_EMAIL_DAILY_CAP`); past that, the rest go out the next morning. Once you're regularly near
  that, switch to an `@allmosports.com` sending address through a mail service. Only the WP Mail
  SMTP settings change.
- **Your Sent folder** in Gmail will hold a copy of every email.
- **Unsubscribe** works two ways: Gmail's own "Unsubscribe" button next to the sender name (one
  click), and the link in the email footer (a confirm button, so email security scanners that
  open links can't unsubscribe anyone by accident). Unsubscribing stops emails only; people keep
  following their schools on the site.
- **Quiet weeks:** followers whose sports have nothing new are skipped, as before.
- **Test runs** with the sample follows file still work the old way: nothing is sent, and the
  emails are attached to the run as a zip.
