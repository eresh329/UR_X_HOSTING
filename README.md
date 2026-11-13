# UR X HOSTING
Private HTML & ZIP website hosting panel (green theme). No bots, no processes — it only stores and serves static files.

    pip install -r requirements.txt
    python app.py            # http://localhost:5000     (PORT=8080 python app.py to change the port)

## Login
Only one account works — no registration:

    username: ereshx
    password: ERESH7

Anything else is rejected (8 wrong tries per IP -> 10 minute lock). Change them at the top of `app.py` (`ADMIN_USER`, `ADMIN_PASS`).

## Using it
1. Sign in -> **New site**: type a name and upload one `.html` or a `.zip` (a ZIP needs an `index.html`; one wrapper folder is flattened automatically).
2. The site is live for **everybody** at `/s/<name>/` — no login needed to view it.
3. Per site: Overview (live link, preview), Files (upload a new version), Versions (download / restore), Settings (rename / delete).

## Safety
- zip-slip, zip-bomb and symlink protection; upload limits at the top of `app.py` (40 MB upload, 150 MB unpacked, 3000 files).
- Hosted pages run inside a CSP sandbox, so a hosted page can never touch the panel's login cookie.
  (Because of that, a hosted page cannot use localStorage/cookies.)
- All panel actions are CSRF-protected.

## Data (back these up)
`sites/`, `site_versions/`, `sites.json`, `.secret_key`

To share links with other people the app must be reachable from the internet (VPS IP, domain, or a tunnel such as Cloudflare Tunnel).

## Deploy on Render
1. Push this folder to a (private) GitHub repo — `app.py` must be at the repo root.
2. Render > New > **Web Service** > pick the repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 1 --threads 8 --timeout 120`
   - Environment: `SECRET_KEY` = any long random text
3. **Keep uploaded sites**: Render's disk is wiped on every restart/deploy. Use a paid instance, add a
   Disk (mount path `/var/data`, 1 GB) and set `DATA_DIR=/var/data`.
   (Free instance = works for testing, but sites vanish on restart and the app sleeps after ~15 min idle.)
4. Open `https://<your-service>.onrender.com`, log in, and your sites are live at `/s/<name>/`.

Or use the included `render.yaml` (New > Blueprint) which sets all of the above.
