"""
UR X HOSTING - private HTML / ZIP website hosting panel.

* One login only:  username "ereshx"  /  password "ERESH7"   (no registration, nothing else works)
* Inside the panel you upload a single .html or a .zip -> the site is live for EVERYBODY at /s/<name>/
* No bots, no processes, no shell: this app only stores and serves static files.
"""
import os
import re
import json
import time
import hmac
import shutil
import zipfile
import secrets
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (Flask, render_template, request, redirect, url_for, session, flash,
                   abort, send_file, jsonify)

# ───────────────────────── settings ─────────────────────────
SITE_NAME = os.environ.get("SITE_NAME", "UR X HOSTING")
ADMIN_USER = "ereshx"
ADMIN_PASS = "ERESH7"

MAX_SITE_UPLOAD = 40 * 1024 * 1024       # one uploaded file
MAX_SITE_UNPACKED = 150 * 1024 * 1024    # total size after unzip
MAX_SITE_FILES = 3000                    # files inside a zip
MAX_SITE_VERSIONS = 10                   # old versions kept per site
LOGIN_TRIES = 8                          # wrong logins allowed ...
LOGIN_WINDOW = 10 * 60                   # ... per IP within this many seconds

BASE_DIR = Path(__file__).parent
# Where uploaded sites live. On Render point DATA_DIR at the persistent disk (e.g. /var/data),
# otherwise everything is wiped on each restart/deploy.
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR)
SITES_DIR = DATA_DIR / "sites"
SITE_VERSIONS_DIR = DATA_DIR / "site_versions"
SITES_FILE = DATA_DIR / "sites.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SITES_DIR.mkdir(exist_ok=True)
SITE_VERSIONS_DIR.mkdir(exist_ok=True)
ON_RENDER = bool(os.environ.get("RENDER"))


def _secret():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    f = DATA_DIR / ".secret_key"
    if f.exists():
        return f.read_text().strip()
    key = secrets.token_hex(32)
    f.write_text(key)
    try:
        f.chmod(0o600)
    except Exception:
        pass
    return key


app = Flask(__name__)
if ON_RENDER or os.environ.get("BEHIND_PROXY"):
    # Render terminates HTTPS in front of the app: trust one proxy hop for scheme/host/client IP
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = _secret()
app.config.update(
    SESSION_COOKIE_SECURE=ON_RENDER,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    MAX_CONTENT_LENGTH=MAX_SITE_UPLOAD + 2 * 1024 * 1024,
)

LOCK = threading.RLock()
SITE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_FAILS = {}


# ───────────────────────── helpers ─────────────────────────
def load_sites():
    try:
        return json.loads(SITES_FILE.read_text())
    except Exception:
        return {}


def save_sites(meta):
    tmp = SITES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=1))
    tmp.replace(SITES_FILE)


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def dir_stats(path):
    files = size = 0
    for f in path.rglob("*"):
        if f.is_file():
            files += 1
            size += f.stat().st_size
    return files, size


def client_ip():
    # remote_addr is the real client (ProxyFix fixes it behind Render); X-Forwarded-For from the
    # browser itself must never be trusted, or the login lock could be bypassed.
    return request.remote_addr or ""


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def site_or_404(name):
    meta = load_sites()
    if not SITE_NAME_RE.match(name or "") or name not in meta:
        abort(404)
    m = meta[name]
    return {"name": name, "url": "/s/" + name + "/", "versions": m.get("versions", []),
            "files": m.get("files", 0), "size": m.get("size", 0), "size_h": human(m.get("size", 0)),
            "created": (m.get("created") or "")[:10], "updated": (m.get("updated") or "").replace("T", " ")[:16]}


@app.before_request
def _csrf_guard():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
            flash("Session expired. Please try again.", "bad")
            return redirect(request.referrer or url_for("login"))


@app.context_processor
def _inject():
    ctx = {"site_name": SITE_NAME, "csrf_token": csrf_token,
           "username": ADMIN_USER if session.get("auth") else None}
    if session.get("auth"):
        try:
            du = shutil.disk_usage(str(DATA_DIR))
            ctx["disk_pct"] = round(du.used / du.total * 100)
        except Exception:
            ctx["disk_pct"] = 0
        ctx["site_count"] = len(load_sites())
    return ctx


# ───────────────────────── upload processing ─────────────────────────
def _safe_extract_zip(zpath, dest):
    """Extract a zip with zip-slip, zip-bomb and symlink protection."""
    dest = dest.resolve()
    total = 0
    with zipfile.ZipFile(zpath) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > MAX_SITE_FILES:
            raise ValueError("ZIP has too many files.")
        if sum(m.file_size for m in members) > MAX_SITE_UNPACKED:
            raise ValueError("ZIP is too large when unpacked.")
        for m in members:
            name = m.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts) or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError("ZIP contains an unsafe path.")
            if parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
                continue
            if (m.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # skip symlinks
            target = dest.joinpath(*parts).resolve()
            if dest not in target.parents:
                raise ValueError("ZIP contains an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_SITE_UNPACKED:
                        raise ValueError("ZIP is too large when unpacked.")
                    out.write(chunk)


def _normalize_root(root):
    """Make sure root/index.html exists (flatten one wrapper folder / rename a lone .html)."""
    if (root / "index.html").is_file():
        return True
    entries = list(root.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for e in list(inner.iterdir()):
            shutil.move(str(e), str(root / e.name))
        inner.rmdir()
        if (root / "index.html").is_file():
            return True
    htmls = [e for e in root.iterdir() if e.is_file() and e.suffix.lower() in (".html", ".htm")]
    if len(htmls) == 1:
        htmls[0].rename(root / "index.html")
        return True
    return False


def _snapshot(name):
    """Zip the current site into site_versions/<name>/<n>.zip and return the new versions list."""
    vdir = SITE_VERSIONS_DIR / name
    vdir.mkdir(parents=True, exist_ok=True)
    versions = list(load_sites().get(name, {}).get("versions", []))
    n = (max(versions) + 1) if versions else 1
    shutil.make_archive(str(vdir / str(n)), "zip", str(SITES_DIR / name))
    versions.append(n)
    while len(versions) > MAX_SITE_VERSIONS:
        old = versions.pop(0)
        try:
            (vdir / f"{old}.zip").unlink()
        except Exception:
            pass
    return versions


def _swap_in(name, site_tmp):
    """Replace/create sites/<name> with site_tmp (keeping a version of the old one)."""
    meta = load_sites()
    existing = meta.get(name)
    final = SITES_DIR / name
    versions = list(existing.get("versions", [])) if existing else []
    if existing and final.is_dir():
        versions = _snapshot(name)
        shutil.rmtree(final, ignore_errors=True)
    site_tmp.rename(final)
    nfiles, nsize = dir_stats(final)
    now = datetime.now().isoformat(timespec="seconds")
    meta = load_sites()
    meta[name] = {"created": (existing or {}).get("created", now), "updated": now,
                  "versions": versions, "files": nfiles, "size": nsize}
    save_sites(meta)


def ingest_upload(name, f, update):
    """Validate + store an uploaded .html/.zip. Raises ValueError with a user-friendly message."""
    if not SITE_NAME_RE.match(name):
        raise ValueError("Site name: 1-40 chars, a-z, 0-9 and dashes only.")
    if not f or not f.filename:
        raise ValueError("Choose an HTML or ZIP file.")
    ext = Path(f.filename).suffix.lower()
    if ext not in (".html", ".htm", ".zip"):
        raise ValueError("Only HTML or ZIP files (.html / .htm / .zip) are allowed.")
    with LOCK:
        exists = name in load_sites()
        if update and not exists:
            raise ValueError("Site not found.")
        if not update and exists:
            raise ValueError("That site name is already taken.")
        tmp_root = Path(tempfile.mkdtemp(prefix=".up-", dir=str(SITES_DIR)))
        try:
            raw = tmp_root / "_upload.bin"
            size = 0
            with open(raw, "wb") as out:
                while True:
                    chunk = f.stream.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_SITE_UPLOAD:
                        raise ValueError("File is too large (max 40 MB).")
                    out.write(chunk)
            if size == 0:
                raise ValueError("The file is empty.")
            site_tmp = tmp_root / "site"
            site_tmp.mkdir()
            if ext == ".zip":
                if not zipfile.is_zipfile(raw):
                    raise ValueError("That is not a valid ZIP file.")
                try:
                    _safe_extract_zip(raw, site_tmp)
                except zipfile.BadZipFile:
                    raise ValueError("That is not a valid ZIP file.")
                if not _normalize_root(site_tmp):
                    raise ValueError("ZIP must contain an index.html (or a single .html file).")
            else:
                shutil.copyfile(raw, site_tmp / "index.html")
            _swap_in(name, site_tmp)
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)


def list_site_files(name, limit=500):
    root = SITES_DIR / name
    out = []
    if not root.is_dir():
        return out, 0
    allf = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p.relative_to(root)).lower())
    for p in allf[:limit]:
        rel = p.relative_to(root).as_posix()
        out.append({"path": rel, "size_h": human(p.stat().st_size)})
    return out, len(allf)


# ───────────────────────── public pages ─────────────────────────
@app.route("/")
def landing():
    if session.get("auth"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html", total_sites=len(load_sites()))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("auth"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        ip = client_ip()
        now = time.time()
        fails = [t for t in _FAILS.get(ip, []) if now - t < LOGIN_WINDOW]
        _FAILS[ip] = fails
        if len(fails) >= LOGIN_TRIES:
            wait = int((LOGIN_WINDOW - (now - fails[0])) / 60) + 1
            flash(f"Too many wrong attempts. Try again in {wait} min.", "bad")
            return render_template("login.html"), 429
        # phone keyboards often add a trailing space / capital letter, so ends are trimmed and the
        # username ignores case. The password must still be exactly the right one.
        u_in = request.form.get("username", "").strip()
        p_in = request.form.get("password", "").strip()
        ok_u = hmac.compare_digest(u_in.lower().encode(), ADMIN_USER.lower().encode())
        ok_p = hmac.compare_digest(p_in.encode(), ADMIN_PASS.encode())
        if ok_u and ok_p:
            _FAILS.pop(ip, None)
            session.clear()
            session.permanent = True
            session["auth"] = True
            nxt = request.args.get("next", "")
            if not (nxt.startswith("/") and not nxt.startswith("//")):
                nxt = url_for("dashboard")
            return redirect(nxt)
        # shown only in the server console (never the typed password)
        print(f"[login] FAILED from {ip}: username {'OK' if ok_u else 'WRONG (got ' + repr(u_in[:20]) + ')'}, "
              f"password {'OK' if ok_p else 'WRONG (length ' + str(len(p_in)) + ', expected ' + str(len(ADMIN_PASS)) + ')'}", flush=True)
        fails.append(now)
        _FAILS[ip] = fails
        if len(_FAILS) > 5000:
            for k in list(_FAILS)[:2500]:
                _FAILS.pop(k, None)
        flash("Wrong username or password.", "bad")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


# ───────────────────────── panel ─────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    meta = load_sites()
    sites = []
    for n, m in meta.items():
        sites.append({"name": n, "url": "/s/" + n + "/", "files": m.get("files", 0), "size_h": human(m.get("size", 0)),
                      "versions": len(m.get("versions", [])), "updated": (m.get("updated") or "").replace("T", " ")[:16]})
    sites.sort(key=lambda s: s["updated"], reverse=True)
    total_size = sum(m.get("size", 0) for m in meta.values())
    try:
        du = shutil.disk_usage(str(DATA_DIR))
        disk_pct = round(du.used / du.total * 100)
        data_pct = min(100, round(total_size / du.total * 100)) if du.total else 0
        free_h = human(du.free)
    except Exception:
        disk_pct = data_pct = 0
        free_h = "-"
    return render_template("dashboard.html", nav="home", sites=sites, total=len(sites),
                           versions=sum(s["versions"] for s in sites), files=sum(s["files"] for s in sites),
                           total_size_h=human(total_size), disk_pct=disk_pct, data_pct=data_pct, free_h=free_h)


@app.route("/new", methods=["GET", "POST"])
@login_required
def site_new():
    if request.method == "POST":
        name = (request.form.get("siteName") or "").strip().lower().replace(" ", "-")
        try:
            ingest_upload(name, request.files.get("siteFile"), update=False)
        except ValueError as e:
            flash(str(e), "bad")
            return render_template("site_new.html", nav="new", name_value=request.form.get("siteName", "")), 400
        flash("Your site is live!", "ok")
        return redirect(url_for("site_overview", name=name))
    return render_template("site_new.html", nav="new", name_value="")


@app.route("/site/<name>")
@login_required
def site_overview(name):
    s = site_or_404(name)
    return render_template("site_overview.html", sname=name, hosted=s, active="overview", origin=request.host_url.rstrip("/"))


@app.route("/site/<name>/files")
@login_required
def site_files(name):
    s = site_or_404(name)
    files, total = list_site_files(name)
    return render_template("site_files.html", sname=name, hosted=s, active="files", files=files, total=total)


@app.route("/site/<name>/update", methods=["POST"])
@login_required
def site_update(name):
    site_or_404(name)
    try:
        ingest_upload(name, request.files.get("siteFile"), update=True)
        flash("New version is live!", "ok")
    except ValueError as e:
        flash(str(e), "bad")
    return redirect(url_for("site_files", name=name))


@app.route("/site/<name>/versions")
@login_required
def site_versions(name):
    s = site_or_404(name)
    vers = []
    for v in reversed(s["versions"]):
        zp = SITE_VERSIONS_DIR / name / f"{v}.zip"
        vers.append({"n": v, "size_h": human(zp.stat().st_size) if zp.is_file() else "-"})
    return render_template("site_versions.html", sname=name, hosted=s, active="versions", vers=vers)


@app.route("/site/<name>/version/<int:v>")
@login_required
def site_version_download(name, v):
    s = site_or_404(name)
    zp = SITE_VERSIONS_DIR / name / f"{v}.zip"
    if v not in s["versions"] or not zp.is_file():
        abort(404)
    return send_file(zp, as_attachment=True, download_name=f"{name}-v{v}.zip")


@app.route("/site/<name>/restore/<int:v>", methods=["POST"])
@login_required
def site_restore(name, v):
    s = site_or_404(name)
    zp = SITE_VERSIONS_DIR / name / f"{v}.zip"
    if v not in s["versions"] or not zp.is_file():
        abort(404)
    with LOCK:
        tmp_root = Path(tempfile.mkdtemp(prefix=".up-", dir=str(SITES_DIR)))
        try:
            site_tmp = tmp_root / "site"
            site_tmp.mkdir()
            _safe_extract_zip(zp, site_tmp)
            if not _normalize_root(site_tmp):
                raise ValueError("That version has no index.html.")
            _swap_in(name, site_tmp)
            flash(f"Version {v} restored.", "ok")
        except (ValueError, zipfile.BadZipFile) as e:
            flash(str(e), "bad")
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
    return redirect(url_for("site_versions", name=name))


@app.route("/site/<name>/settings")
@login_required
def site_settings(name):
    s = site_or_404(name)
    return render_template("site_settings.html", sname=name, hosted=s, active="settings")


@app.route("/site/<name>/rename", methods=["POST"])
@login_required
def site_rename(name):
    site_or_404(name)
    new = (request.form.get("newName") or "").strip().lower().replace(" ", "-")
    if not SITE_NAME_RE.match(new):
        flash("Site name: 1-40 chars, a-z, 0-9 and dashes only.", "bad")
        return redirect(url_for("site_settings", name=name))
    if new == name:
        return redirect(url_for("site_settings", name=name))
    with LOCK:
        meta = load_sites()
        if new in meta:
            flash("That site name is already taken.", "bad")
            return redirect(url_for("site_settings", name=name))
        (SITES_DIR / name).rename(SITES_DIR / new)
        if (SITE_VERSIONS_DIR / name).is_dir():
            (SITE_VERSIONS_DIR / name).rename(SITE_VERSIONS_DIR / new)
        meta[new] = meta.pop(name)
        save_sites(meta)
    flash("Site renamed. Its link has changed too.", "ok")
    return redirect(url_for("site_settings", name=new))


@app.route("/site/<name>/delete", methods=["GET", "POST"])
@login_required
def site_delete(name):
    s = site_or_404(name)
    if request.method == "POST":
        with LOCK:
            shutil.rmtree(SITES_DIR / name, ignore_errors=True)
            shutil.rmtree(SITE_VERSIONS_DIR / name, ignore_errors=True)
            meta = load_sites()
            meta.pop(name, None)
            save_sites(meta)
        flash(f"{name} was deleted.", "ok")
        return redirect(url_for("dashboard"))
    return render_template("site_delete.html", sname=name, hosted=s, active="settings")


# ───────────────────────── public: serve hosted sites ─────────────────────────
@app.route("/s/<name>")
def site_redirect(name):
    return redirect("/s/" + name + "/", 301)


@app.route("/s/<name>/", defaults={"sub": ""})
@app.route("/s/<name>/<path:sub>")
def site_serve(name, sub):
    if not SITE_NAME_RE.match(name):
        abort(404)
    root = (SITES_DIR / name).resolve()
    if not root.is_dir():
        abort(404)
    target = (root / sub).resolve()
    if target != root and root not in target.parents:
        abort(404)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        abort(404)
    resp = send_file(target, conditional=True, max_age=0)
    # Hosted pages run in a sandbox with an opaque origin: scripts, forms and popups work,
    # but a site can never read this panel's cookies or call its endpoints.
    resp.headers["Content-Security-Policy"] = ("sandbox allow-scripts allow-forms allow-popups "
                                               "allow-modals allow-downloads allow-pointer-lock allow-presentation")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ───────────────────────── errors ─────────────────────────
def _err(code, title, message):
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(404)
def _404(e):
    return _err(404, "Page not found", "This page or site does not exist (or it was deleted).")


@app.errorhandler(413)
def _413(e):
    flash("That upload is too large (max 40 MB).", "bad")
    return redirect(request.referrer or (url_for("dashboard") if session.get("auth") else url_for("landing")))


@app.errorhandler(500)
def _500(e):
    return _err(500, "Something broke", "Unexpected error. Try again in a moment.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
