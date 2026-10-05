"""Browser check of the mobile view (0.11.0), for developers.

Starts missingarr on 127.0.0.1 with a throwaway database and made-up data,
opens every page at 390 x 844 (phone) and 1280 x 800 (desktop) and fails
when a phone page scrolls sideways, a visible control is smaller than
44 x 44 px, or the tab bar covers the end of a page. It opens the "More"
sheet and the Logs filter sheet and closes both with Escape, opens a help
text by tap (without toggling the switch the "?" sits in) and a row on the
Pre-filter page, checks that the desktop shows none of the mobile parts,
that the parts of a dashboard card's head do not overlap (both sizes), that
disabled buttons look dimmed and that the dashboard's imports link has the
accent color (0.11.1), and saves a screenshot of every page in both sizes.

Not part of pytest and not in the image. It needs Playwright in the
project's virtualenv:

    .venv/bin/pip install playwright
    .venv/bin/python -m playwright install chromium

(or set MOBILE_CHECK_CHROMIUM to an existing Chromium binary), then:

    .venv/bin/python scripts/mobile_check.py <screenshot-dir>

Nothing leaves 127.0.0.1: the instances point at 127.0.0.1:9, where nothing
listens, and the script answers the Imports page's requests itself.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 800}
LONG = "Some.Very.Long.Release.Name.With.Many.Dots.And.No.Spaces.2026.German.DL.1080p.WEB.H264-GROUP"
PAGES = [("dashboard", "/"), ("instances", "/instances"), ("new", "/instances/new"),
         ("edit", "/instances/{id}/edit"), ("history", "/history"), ("progressed", "/searched"),
         ("prefilter", "/checked-search"), ("imports", "/imports"), ("logs", "/logs"), ("help", "/help")]

SMALL_CONTROLS = r"""() => {
  const selector = 'a.btn, a.tab, button, input:not([type=hidden]):not([type=checkbox]), select, '
    + 'label.check-label, .toggle-label, .tooltip-icon, .sheet-link, .back-link, '
    + 'a[data-imports-dashboard], .navbar-brand';
  const bad = [];
  for (const el of document.querySelectorAll(selector)) {
    if (el.closest('.toast-container')) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;          // not shown
    if (r.width < 43.5 || r.height < 43.5) {
      const text = (el.textContent || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 30);
      bad.push(`${el.tagName.toLowerCase()}.${String(el.className).trim().replace(/\s+/g, '.')} "${text}" `
               + `${Math.round(r.width)}x${Math.round(r.height)}`);
    }
  }
  return bad;
}"""

OVERFLOW = "() => document.documentElement.scrollWidth - window.innerWidth"

COVERED = r"""() => {
  window.scrollTo(0, document.documentElement.scrollHeight);
  const bar = document.querySelector('.tabbar');
  if (!bar) return 'no tab bar';
  const top = bar.getBoundingClientRect().top;
  let lowest = 0;
  for (const e of document.querySelectorAll('main *')) {
    const r = e.getBoundingClientRect();
    if (r.height > 0 && r.bottom > lowest) lowest = r.bottom;
  }
  return lowest > top + 1 ? `content ends at ${Math.round(lowest)}, tab bar starts at ${Math.round(top)}` : '';
}"""

HELP_SHOWN = r"""() => {
  const s = getComputedStyle(document.activeElement, '::after');
  return s.content !== 'none' && s.position === 'fixed';
}"""

# Parts of a dashboard card's head that overlap each other (0.11.1: in one
# row the badges ran into the buttons of a card about 400 px wide).
CARD_OVERLAP = r"""() => {
  const found = [];
  for (const head of document.querySelectorAll('.icard-head')) {
    const parts = [...head.querySelectorAll('.icard-title > *, .icard-actions > *')]
      .map(e => [e, e.getBoundingClientRect()]).filter(([, r]) => r.width > 0 && r.height > 0);
    for (let i = 0; i < parts.length; i++) {
      for (let j = i + 1; j < parts.length; j++) {
        const [a, ra] = parts[i];
        const [b, rb] = parts[j];
        const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (w > 1 && h > 1) {
          found.push(`"${(a.textContent || '').trim().slice(0, 15)}" overlaps "${(b.textContent || '').trim().slice(0, 15)}"`);
        }
      }
    }
  }
  return found;
}"""

# Visible disabled buttons that do not look disabled.
UNDIMMED_DISABLED = r"""() => [...document.querySelectorAll('button:disabled')]
  .filter(b => b.getBoundingClientRect().width > 0 && parseFloat(getComputedStyle(b).opacity) >= 1)
  .map(b => (b.textContent || '').trim().slice(0, 20))"""

DASHBOARD_LINK_COLOR = r"""() => {
  const link = document.querySelector('a[data-imports-dashboard]');
  return link ? getComputedStyle(link).color : '';
}"""
ACCENT = "rgb(229, 114, 30)"


def card_problems(page, name: str) -> list[str]:
    return [f"{name}: card head: {item}" for item in page.evaluate(CARD_OVERLAP)]

IMPORTS = {
    "instances": [{
        "id": 1, "name": "radarr", "type": "radarr", "enabled": True,
        "queue_link": {"scheme": "http", "port": 9, "path": "/activity/queue"},
        "error": "", "starting": False,
        "downloads": [{
            "download_id": "dl-1", "title": LONG, "queue_ids": [11], "added": "2026-10-04T10:00:00Z",
            "size": 2_000_000_000, "download_client": "Example", "state": "importBlocked",
            "messages": ["Found matching movie via grab history, but release was matched to movie by ID. "
                         "Automatic import is not possible."],
            "movie_id": 7, "series_id": None, "episode_ids": [],
        }],
    }],
    "checked_at": "2026-10-04T10:05:00Z",
}
PROPOSAL = {
    "download_id": "dl-1", "title": LONG, "target": "Some Film (2021)",
    "candidates": [
        {"path": f"/downloads/{LONG}/{LONG}.mkv", "relative_path": f"{LONG}.mkv", "size": 1_900_000_000,
         "video": True, "target": "Some Film (2021)", "movie_id": 7, "series_id": None, "season_number": None,
         "episode_ids": [], "episode_numbers": [], "quality": "WEBDL-1080p", "languages": ["German", "English"],
         "release_group": "GROUP", "rejections": ["Not an upgrade for existing movie file(s)"]},
        {"path": f"/downloads/{LONG}/sample.txt", "relative_path": "sample.txt", "size": 100, "video": False,
         "target": "", "movie_id": None, "series_id": None, "season_number": None, "episode_ids": [],
         "episode_numbers": [], "quality": "", "languages": [], "release_group": "", "rejections": []},
    ],
    "importable": False, "why_not": "The app objects to a file", "uncovered_episodes": 0, "proposal_key": "k",
    "verdict": {"state": "foreign", "reasons": ["year"], "notes": [], "details": ["Year 2019 differs from 2021"],
                "error": ""},
    "cached": False,
}
COUNT = {"total": 1, "complete": True, "checked_at": "2026-10-04T10:05:00Z",
         "per_instance": [{"id": 1, "name": "radarr", "count": 1, "error": "", "starting": False}]}


def fake_imports(route):
    """Answers of /api/imports*: the page shows a real card, nothing is ever imported."""
    path = route.request.url.split("?")[0]
    if path.endswith("/proposal"):
        body = PROPOSAL
    elif path.endswith("/api/imports/count"):
        body = COUNT
    elif path.endswith("/api/imports"):
        body = IMPORTS
    else:
        route.abort()                 # Import and Discard are never sent
        return
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def seed(database: Path) -> dict:
    """Made-up data in a fresh database. Returns the enabled instance."""
    from backend.config import settings
    settings.database_url = str(database)
    from backend import database as schema, db
    schema.init_db()
    sonarr = db.instances.create({"name": "sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
                                  "api_key": "0" * 32, "enabled": True, "checked_search": "dry_run"})
    db.instances.create({"name": "radarr-with-a-rather-long-name", "type": "radarr",
                         "url": "http://127.0.0.1:9/radarr", "api_key": "1" * 32, "enabled": False})
    for level in ("info", "warn", "error", "debug"):
        db.activity.insert(sonarr["id"], "sonarr", level, f"Dry run: 12 title(s) checked, 0 would grab ({level})",
                           "search_missing")
    db.activity.insert(sonarr["id"], "sonarr", "warn",
                       f"Checked search for Some Show S05E17 – {LONG} failed: indexer failure during search",
                       "search_missing")
    run = db.history.start_run(sonarr["id"], "sonarr", "search_missing")
    for n in range(1, 4):
        db.history.record_submission(run, sonarr["id"], f"Some Show S01E0{n} – {LONG}", 100 + n, "episode",
                                     f"ep:{n}", 500 + n)
    db.history.finish_run(run, 3, 3)
    db.searched.add(sonarr["id"], "ep:9", f"Some Show S01E09 – {LONG}", "episode")
    chosen = {"title": LONG, "indexer": "Example", "score": 1250, "size": 1_500_000_000, "quality": "WEBDL-1080p",
              "verdict": "ok", "reasons": [], "notes": ["kept"], "chosen": True, "arr_choice": True}
    rejected = dict(chosen, verdict="rejected", reasons=["foreign title"], notes=[], chosen=False, arr_choice=False)
    db.checked_search_log.insert({"instance_id": sonarr["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": f"Some Show S02E01 – {LONG}", "outcome": "would_grab",
                                  "cache_key": "ep:21", "dry_run_round": sonarr["dry_run_round"],
                                  "arr_pick": LONG, "pick": chosen, "candidates": [chosen, rejected]})
    db.checked_search_log.insert({"instance_id": sonarr["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": "Some Show S02E02 – A Title", "outcome": "no_results",
                                  "cache_key": "ep:22", "dry_run_round": sonarr["dry_run_round"],
                                  "error_message": "8 release(s) returned, none approved — most frequent "
                                                   "rejections: Not wanted (8)"})
    return sonarr


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(database: Path, password: str, port: int) -> subprocess.Popen:
    env = dict(os.environ, DATABASE_URL=str(database), AUTH_USERNAME="admin", AUTH_PASSWORD=password,
               COOKIE_SECURE="false", SECRET_KEY="")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                               "--port", str(port)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(150):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return server
        except OSError:
            time.sleep(0.2)
    server.kill()
    raise SystemExit("missingarr did not start")


def login(page, base: str, password: str) -> None:
    page.goto(f"{base}/login")
    page.fill("input[name=username]", "admin")
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_url(f"{base}/")


def open_page(page, url: str, settle_ms: int = 1500) -> None:
    page.goto(url)
    page.wait_for_load_state("load")
    page.wait_for_timeout(settle_ms)          # Alpine, the first list loads, the count


def page_problems(page, name: str, tab_bar: bool = True) -> list[str]:
    problems = []
    wider = page.evaluate(OVERFLOW)
    if wider > 0:
        problems.append(f"{name}: page is {wider} px wider than the screen")
    problems += [f"{name}: too small: {item}" for item in page.evaluate(SMALL_CONTROLS)]
    if tab_bar:
        covered = page.evaluate(COVERED)
        if covered:
            problems.append(f"{name}: {covered}")
    return problems


def check_phone(browser, base: str, password: str, instance_id: int, out_dir: Path) -> list[str]:
    problems = []
    context = browser.new_context(viewport=PHONE, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.route("**/api/imports**", fake_imports)
    open_page(page, f"{base}/login", 300)
    problems += page_problems(page, "login", tab_bar=False)
    page.screenshot(path=str(out_dir / "phone-login.png"))
    login(page, base, password)
    for name, path in PAGES:
        open_page(page, base + path.format(id=instance_id))
        problems += page_problems(page, name)
        if name == "dashboard":
            problems += card_problems(page, name)
        if name == "imports":
            problems += [f"imports: disabled but not dimmed: {item}" for item in page.evaluate(UNDIMMED_DISABLED)]
        page.screenshot(path=str(out_dir / f"phone-{name}.png"), full_page=True)

    open_page(page, f"{base}/", 800)
    page.click(".tabbar button")
    page.wait_for_selector("#more-sheet", state="visible")
    page.wait_for_timeout(400)                # the sheet slides in (0.18 s)
    problems += [f"more sheet: too small: {item}" for item in page.evaluate(SMALL_CONTROLS)]
    page.screenshot(path=str(out_dir / "phone-more.png"))
    page.keyboard.press("Escape")
    page.wait_for_selector("#more-sheet", state="hidden")
    if page.evaluate("() => document.documentElement.classList.contains('sheet-open')"):
        problems.append("more sheet: the page stays locked after Escape")

    open_page(page, f"{base}/logs", 800)
    page.click(".filter-toggle")
    page.wait_for_selector("#filter-sheet.is-open", state="visible")
    page.wait_for_timeout(400)
    problems += [f"filter sheet: too small: {item}" for item in page.evaluate(SMALL_CONTROLS)]
    page.screenshot(path=str(out_dir / "phone-logs-filter.png"))
    page.keyboard.press("Escape")
    page.wait_for_selector("#filter-sheet.is-open", state="detached")

    open_page(page, f"{base}/instances/{instance_id}/edit", 800)
    switch = "() => document.getElementById('enabled').checked"
    before = page.evaluate(switch)
    page.locator(".tooltip-icon").first.click()     # the "?" inside the Enabled switch (0.11.1)
    if not page.evaluate(HELP_SHOWN):
        problems.append("help: a tap on ? shows no text")
    if page.evaluate(switch) != before:
        problems.append("help: a tap on the ? in a switch toggled the switch")
    page.screenshot(path=str(out_dir / "phone-help-text.png"))

    open_page(page, f"{base}/checked-search", 800)
    page.locator("tr.row-toggle").first.click()
    page.wait_for_selector("tr.row-detail", state="visible")
    page.screenshot(path=str(out_dir / "phone-prefilter-open.png"), full_page=True)
    context.close()
    return problems


def visible(page, selector: str) -> bool:
    found = page.locator(selector)
    return any(found.nth(i).is_visible() for i in range(found.count()))


def check_desktop(browser, base: str, password: str, instance_id: int, out_dir: Path) -> list[str]:
    problems = []
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.route("**/api/imports**", fake_imports)
    login(page, base, password)
    for name, path in PAGES:
        open_page(page, base + path.format(id=instance_id), 1000)
        for selector in (".tabbar", ".filter-toggle", ".page-label", "#more-sheet", ".sheet-done"):
            if visible(page, selector):
                problems.append(f"{name} (desktop): {selector} is visible")
        if not visible(page, ".nav-links"):
            problems.append(f"{name} (desktop): the top menu is hidden")
        if name == "dashboard":
            problems += card_problems(page, f"{name} (desktop)")
            color = page.evaluate(DASHBOARD_LINK_COLOR)
            if color != ACCENT:
                problems.append(f"{name} (desktop): imports link color {color}, expected {ACCENT}")
        page.screenshot(path=str(out_dir / f"desktop-{name}.png"), full_page=True)
    context.close()
    return problems


def check(out_dir: Path) -> list[str]:
    from playwright.sync_api import sync_playwright
    password = secrets.token_urlsafe(16)
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "missingarr.db"
        sonarr = seed(database)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        server = start(database, password, port)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get("MOBILE_CHECK_CHROMIUM") or None)
                try:
                    return (check_phone(browser, base, password, sonarr["id"], out_dir)
                            + check_desktop(browser, base, password, sonarr["id"], out_dir))
                finally:
                    browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    problems = check(out_dir)
    for problem in problems:
        print("FAIL", problem)
    print(f"{len(problems)} problem(s); screenshots in {out_dir}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
