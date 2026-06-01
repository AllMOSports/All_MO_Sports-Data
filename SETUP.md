# ALL MO Sports — GitHub Auto-Update Setup

Follow these steps once. After setup, rankings update automatically every night.

---

## Step 1 — Create a free GitHub account
Go to https://github.com and sign up if you don't have an account.

---

## Step 2 — Create a new repository
1. Click the **+** button in the top right → **New repository**
2. Name it something like `allmosports-data`
3. Set it to **Public**
4. Click **Create repository**

---

## Step 3 — Upload these files to the repository
Upload all four files in this folder to your new repository:
- `rankings.json`
- `update_rankings.py`
- `.github/workflows/update-rankings.yml`

To upload:
1. On your repository page click **Add file → Upload files**
2. Drag all files in (including the `.github/workflows/` folder structure)
3. Click **Commit changes**

---

## Step 4 — Run the updater for the first time
1. Go to the **Actions** tab in your repository
2. Click **Update Rankings** in the left sidebar
3. Click **Run workflow → Run workflow**
4. Wait about 60 seconds for it to finish
5. Go back to your repository — `rankings.json` should now have real data

---

## Step 5 — Install the homepage widget
1. Open `allmosports-homepage-widget.html` in a text editor
2. Replace `YOUR_GITHUB_USERNAME` with your actual GitHub username
3. Replace `YOUR_REPO_NAME` with `allmosports-data` (or whatever you named it)
4. In WordPress, add a **Custom HTML** block to your homepage
5. Paste the entire file contents into it
6. Save the page

---

## That's it — you're done!

The GitHub Action runs every night at midnight Central time.
It fetches your rankings pages, extracts the top 5, and updates `rankings.json`.
Your homepage widget reads that file automatically.

---

## If you ever want to trigger an update manually
Go to your GitHub repository → **Actions** → **Update Rankings** → **Run workflow**

## If you add a new sport
Edit `update_rankings.py` and add the sport to the `SPORTS` list.
Also add the sport to the `SPORTS` config in `allmosports-homepage-widget.html`.
