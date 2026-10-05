"""Mobile view (0.11.0): tab bar, "More" sheet, touch targets, lists and
filter sheets. These tests check the markup and the scripts; the layout
itself is checked in a browser by scripts/mobile_check.py."""

import json
import subprocess
from pathlib import Path

import pytest

from backend import db  # noqa: F401  (used by later tests in this file)
from backend.config import settings
from backend.tooltips import TOOLTIPS  # noqa: F401
from tests.test_g5_pages import NODE, client, component_script, make_instance, run_node, tags  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("/", "/instances", "/instances/new", "/history", "/searched", "/checked-search", "/imports", "/logs",
         "/help")
TABS = ["/", "/imports", "/checked-search", "/logs"]


def tabbar(page):
    """(tag, attrs) of everything inside <nav class="tabbar">."""
    start = page.index('<nav class="tabbar"')
    return tags(page[start:page.index("</nav>", start)])


def more_sheet(page):
    """The HTML of the "More" sheet, up to its end marker."""
    start = page.rindex("<div", 0, page.index('id="more-sheet"'))
    return page[start:page.index("<!-- /more-sheet -->")]


# ── Tab bar and "More" sheet ─────────────────────────────────────────────────

@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_one_tab_bar_with_four_links_and_more(client, path):
    page = client.get(path).text
    assert page.count('<nav class="tabbar"') == 1, path
    items = tabbar(page)
    assert [a["href"] for t, a in items if t == "a"] == TABS
    buttons = [a for t, a in items if t == "button"]
    assert len(buttons) == 1
    assert buttons[0]["@click"] == "$store.sheet.open('more', $el)"
    assert (buttons[0]["aria-controls"], buttons[0]["aria-haspopup"]) == ("more-sheet", "dialog")


@pytest.mark.parametrize("path, active", [
    ("/", "/"), ("/imports", "/imports"), ("/checked-search", "/checked-search"), ("/logs", "/logs"),
    ("/history", "more"), ("/searched", "more"), ("/help", "more"), ("/instances", "more"),
    ("/instances/new", "more"),
])
def test_the_active_tab_is_marked(client, path, active):
    items = [a for t, a in tabbar(client.get(path).text) if t in ("a", "button")]
    assert [a.get("href", "more") for a in items if a.get("aria-current") == "page"] == [active]
    for a in items:
        assert ("active" in a["class"].split()) == (a.get("href", "more") == active), a


def test_the_edit_page_marks_more(client):
    inst = make_instance()
    items = [a for t, a in tabbar(client.get(f"/instances/{inst['id']}/edit").text) if t in ("a", "button")]
    assert [a.get("href", "more") for a in items if a.get("aria-current") == "page"] == ["more"]


def test_more_sheet_holds_the_other_pages_the_version_and_sign_out(client):
    part = more_sheet(client.get("/logs").text)
    items = tags(part)
    root = items[0][1]
    assert (root["id"], root["role"], root["aria-modal"]) == ("more-sheet", "dialog", "true")
    assert root["x-show"] == "$store.sheet.name === 'more'"
    assert root["style"] == "display:none;"
    links = [a for t, a in items if t == "a"]
    assert [a["href"] for a in links] == ["/instances", "/history", "/searched", "/help",
                                          "https://github.com/gomaaz/missingarr"]
    assert (links[-1]["target"], links[-1]["rel"]) == ("_blank", "noopener")
    assert [a for t, a in items if t == "form"] == [
        {"method": "post", "action": "/logout", "hx-boost": "false", "style": "margin:0;"}]
    assert f"v{settings.version}" in part
    closes = [a for t, a in items if t == "button" and a.get("@click") == "$store.sheet.close()"]
    assert len(closes) == 1 and closes[0]["aria-label"] == "Close"


def test_more_sheet_marks_the_current_page(client):
    part = more_sheet(client.get("/history").text)
    assert [a["href"] for t, a in tags(part) if t == "a" and a.get("aria-current") == "page"] == ["/history"]


def test_one_backdrop_closes_on_tap_and_escape(client):
    backdrops = [a for t, a in tags(client.get("/").text) if "sheet-backdrop" in a.get("class", "").split()]
    assert len(backdrops) == 1
    assert backdrops[0]["@click"] == "$store.sheet.close()"
    assert backdrops[0]["@keydown.escape.window"] == "$store.sheet.close()"
    assert backdrops[0]["x-show"] == "$store.sheet.name !== null"


@pytest.mark.parametrize("path", PAGES)
def test_imports_counter_twice_but_one_loader(client, path):
    page = client.get(path).text
    counters = [a for t, a in tags(page) if "data-imports-count" in a]
    assert [c["class"] for c in counters] == ["badge badge-error", "badge badge-error tab-badge"], path
    assert all("hidden" in c for c in counters)
    assert page.count("startImportsCount()") == 1


def test_viewport_leaves_room_for_the_home_indicator(client):
    metas = [a for t, a in tags(client.get("/").text) if t == "meta" and a.get("name") == "viewport"]
    assert metas[0]["content"] == "width=device-width, initial-scale=1.0, viewport-fit=cover"


def test_login_page_has_no_tab_bar(client):
    client.cookies.clear()
    page = client.get("/login").text
    assert "tabbar" not in page and "more-sheet" not in page


# ── Sheet store (app.js in node) ─────────────────────────────────────────────

SHEET_PRELUDE = r"""
const fs = require('fs');
const vm = require('vm');
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};
globalThis.EventSource = function () {};
globalThis.window = globalThis;
globalThis.location = { pathname: '/', search: '', href: 'http://t/' };
const listeners = {};
const added = [];
const classes = new Set();
const focused = [];
const node = (name) => ({ name, isConnected: true, focus() { focused.push(name); } });
const sheets = {};
globalThis.document = {
  documentElement: { classList: { add: c => classes.add(c), remove: c => classes.delete(c) } },
  addEventListener: (name, fn) => { added.push(name); listeners[name] = fn; },
  getElementById: (id) => sheets[id] || null,
  querySelectorAll: () => [],
};
const stores = {};
globalThis.Alpine = { store: (name, value) => { if (value !== undefined) stores[name] = value; return stores[name]; },
                      nextTick: (fn) => fn(), $data: () => ({}) };
const source = fs.readFileSync('static/js/app.js', 'utf8');
vm.runInThisContext(source);
vm.runInThisContext(source);   // hx-boost runs the file again after every navigation
listeners['alpine:init']();
const sheet = Alpine.store('sheet');
const out = {};
"""


def run_sheet_js(tmp_path, body):
    case = tmp_path / "sheet.js"
    case.write_text(SHEET_PRELUDE + "(async () => {\n" + body +
                    "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(case)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sheet_store_opens_one_sheet_and_gives_the_focus_back(tmp_path):
    out = run_sheet_js(tmp_path, """
sheets['more-sheet'] = { querySelector: s => (s === 'a[href]' ? node('instances-link') : null) };
sheets['filter-sheet'] = { querySelector: s => (s === 'select, input' ? node('first-select') : null) };
out.start = sheet.name;
sheet.open('more', node('more-tab'));
out.opened = [sheet.name, [...classes], focused.slice()];
sheet.close();
out.closed = [sheet.name, [...classes], focused.slice()];
sheet.close();
out.again = focused.length;
const gone = node('filter-button'); gone.isConnected = false;
sheet.open('filter', gone);
out.filter = focused.slice(-1);
listeners['htmx:beforeSwap']({ detail: { boosted: false } });
out.poll = sheet.name;
listeners['htmx:beforeSwap']({ detail: { boosted: true } });
out.navigation = [sheet.name, [...classes], focused.slice(-1)];
out.hooks = added.filter(n => n === 'htmx:beforeSwap').length;
sheet.open('more', node('more-tab'));
listeners['htmx:historyRestore']({ detail: {} });
out.restore = [sheet.name, [...classes]];
out.restoreHooks = added.filter(n => n === 'htmx:historyRestore').length;
""")
    assert out["start"] is None
    assert out["opened"] == ["more", ["sheet-open"], ["instances-link"]]
    assert out["closed"] == [None, [], ["instances-link", "more-tab"]]
    assert out["again"] == 2                       # closing twice changes nothing
    assert out["filter"] == ["first-select"]
    assert out["poll"] == "filter"                 # a card poll (not boosted) leaves it open
    assert out["navigation"] == [None, [], ["first-select"]]   # the old opener is gone: no focus
    assert out["hooks"] == 1                       # registered once, although app.js ran twice
    assert out["restore"] == [None, []]            # Back/Forward (history restore) closes the sheet
    assert out["restoreHooks"] == 1


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_help_box_closes_when_tapping_beside_it(tmp_path):
    out = run_sheet_js(tmp_path, """
let blurs = 0;
const icon = { classList: { contains: c => c === 'tooltip-icon' }, blur() { blurs++; } };
document.activeElement = icon;
listeners['pointerdown']({ target: { closest: () => icon } });
out.inside = blurs;
listeners['pointerdown']({ target: { closest: () => null } });
out.outside = blurs;
document.activeElement = null;
listeners['pointerdown']({ target: { closest: () => null } });
listeners['pointerdown']({});
out.safe = blurs;
out.hooks = added.filter(n => n === 'pointerdown').length;
""")
    assert out == {"inside": 0, "outside": 1, "safe": 1, "hooks": 1}


# ── Touch targets, help on tap, form and login ───────────────────────────────

@pytest.mark.parametrize("path", ["/instances/new", "/imports"])
def test_help_icons_open_on_tap(client, path):
    icons = [a for t, a in tags(client.get(path).text) if "tooltip-icon" in a.get("class", "").split()]
    assert icons, path
    for icon in icons:
        assert (icon["tabindex"], icon["role"]) == ("0", "button"), icon


def test_form_has_a_back_link_and_an_action_row(client):
    page = client.get("/instances/new").text
    assert [a["href"] for t, a in tags(page) if "back-link" in a.get("class", "").split()] == ["/instances"]
    row = page[page.index('class="form-actions"'):]
    assert row.index('href="/instances"') < row.index('id="save-btn"')


def test_form_checkboxes_sit_in_large_labels(client):
    last_label = None
    for tag, attrs in tags(client.get("/instances/new").text):
        if tag == "label":
            last_label = attrs
        elif tag == "input" and attrs.get("type") == "checkbox":
            classes = (last_label or {}).get("class", "").split()
            assert "check-label" in classes or "toggle-label" in classes, attrs.get("id")


def test_login_remember_me_is_one_tap_target(client):
    client.cookies.clear()
    page = client.get("/login").text
    labels = [a for t, a in tags(page) if t == "label" and "check-label" in a.get("class", "").split()]
    assert len(labels) == 1 and labels[0]["for"] == "remember"
    start = page.index('class="check-label"')
    assert (page.index('name="remember"', start) < page.index("Remember me for 30 days", start)
            < page.index("</label>", start))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_tap_on_a_help_icon_keeps_the_focus_on_it(tmp_path):
    # The "?" sits inside a <label>: without the handler the tap would focus
    # the label's field (keyboard on a phone) or toggle its checkbox.
    out = run_sheet_js(tmp_path, """
const icon = node('icon');
let prevented = 0;
const click = (target) => listeners['click']({ target, preventDefault() { prevented += 1; } });
click({ closest: (s) => (s === '.tooltip-icon' ? icon : null) });
click({ closest: () => null });
out.result = [prevented, focused.slice()];
out.hooks = added.filter(n => n === 'click').length;
""")
    assert out["result"] == [1, ["icon"]]
    assert out["hooks"] == 1                       # registered once, although app.js ran twice


# ── Dashboard cards ──────────────────────────────────────────────────────────

def test_card_head_has_the_mobile_layout_hooks(client):
    make_instance()
    page = client.get("/").text
    head = page.index('class="card-header icard-head"')
    title = page.index('class="icard-title"', head)
    actions = page.index('class="icard-actions"', title)
    badge = page.index("data-status-badge", actions)
    test_button = page.index('onclick="testCardConnection(', actions)
    edit = page.index('/edit" class="btn btn-secondary btn-sm">Edit</a>', actions)
    assert head < title < actions < badge < test_button < edit
    assert 'class="icard-url"' in page


# ── Tables as lists ──────────────────────────────────────────────────────────

ROLES = {"c-main", "c-pill", "c-meta", "c-extra", "c-hide"}


def stack_tables(page):
    """HTML of each <table> whose class has table-stack, up to its first </table>."""
    found, at = [], 0
    while (at := page.find("table-stack", at)) >= 0:
        start = page.rindex("<table", 0, at)
        found.append(page[start:page.index("</table>", at)])
        at += len("table-stack")
    return found


def row_roles(html):
    """For every <tr> in html: the role classes of each of its <td>."""
    rows = []
    for tag, attrs in tags(html):
        if tag == "tr":
            rows.append([])
        elif tag == "td" and rows:
            rows[-1].append(sorted(set(attrs.get("class", "").split()) & ROLES))
    return rows


def assert_roles_valid(page):
    tables = stack_tables(page)
    assert tables
    for html in tables:
        for row in row_roles(html):
            assert all(len(cell) <= 1 for cell in row), row
            flat = [role for cell in row for role in cell]
            assert flat.count("c-main") <= 1 and flat.count("c-pill") <= 1, row


def test_logs_rows_become_list_entries(client):
    page = client.get("/logs").text
    assert_roles_valid(page)
    [table] = stack_tables(page)
    assert [["c-meta"], ["c-meta"], ["c-pill"], ["c-meta"], ["c-main"]] in row_roles(table)


def test_history_rows_become_list_entries(client):
    page = client.get("/history").text
    assert_roles_valid(page)
    [table] = stack_tables(page)
    assert [["c-meta"], ["c-meta"], ["c-main"], ["c-meta"], ["c-meta"], ["c-meta"], ["c-pill"], ["c-extra"]] \
        in row_roles(table)
    assert [a["data-label"] for t, a in tags(table) if t == "td" and "data-label" in a] == ["Verified"]


def test_progressed_rows_become_list_entries(client):
    make_instance()
    page = client.get("/searched").text
    assert_roles_valid(page)
    [table] = stack_tables(page)
    assert [["c-meta"], ["c-meta"], ["c-main"]] in row_roles(table)


def test_instances_rows_become_list_entries(client):
    make_instance()
    page = client.get("/instances").text
    assert_roles_valid(page)
    [table] = stack_tables(page)
    assert [["c-pill"], ["c-main"], ["c-meta"], ["c-meta"], ["c-meta"], ["c-meta"], ["c-extra"]] \
        in row_roles(table)


def test_help_field_names_wrap_on_a_phone(client):
    page = client.get("/help").text
    cells = [a for t, a in tags(page) if t == "td" and a.get("class") == "help-field"]
    assert len(cells) == len(TOOLTIPS)
    assert all("nowrap" not in a["style"] for a in cells)
    table = page.index("<thead><tr><th>Field</th>")
    assert page.rindex('<div style="overflow-x:auto;">', 0, table) > page.rindex("Field Reference", 0, table)


@pytest.mark.parametrize("path, label", [
    ("/logs", "'Page ' + (page + 1) + ' / ' + pageCount()"),
    ("/searched", "'Page ' + (tablePage + 1) + ' / ' + pageCount()"),
])
def test_pagers_show_page_x_of_y_on_a_phone(client, path, label):
    make_instance()
    items = tags(client.get(path).text)
    labels = [a for t, a in items if "page-label" in a.get("class", "").split()]
    assert [(a["class"], a["x-text"]) for a in labels] == [("m-only page-label", label)]
    assert any("page-num" in a.get("class", "").split() for t, a in items if t == "button")
    assert any("pager" in a.get("class", "").split() for t, a in items if t == "div")


# ── Filter sheet and the Pre-filter list ─────────────────────────────────────

def filter_bar(page):
    start = page.rindex("<div", 0, page.index('id="filter-sheet"'))
    return page[start:page.index("<!-- /filter-sheet -->")]


@pytest.mark.parametrize("path, done", [
    ("/logs", None),
    ("/history", "'Show ' + total + ' items'"),
    ("/checked-search", "'Show ' + total + ' titles'"),
])
def test_filter_pages_have_a_filter_button_and_a_sheet(client, path, done):
    page = client.get(path).text
    items = tags(page)
    toggles = [a for t, a in items if t == "button" and "filter-toggle" in a.get("class", "").split()]
    assert len(toggles) == 1
    assert toggles[0]["@click"] == "$store.sheet.open('filter', $el)"
    assert toggles[0]["aria-controls"] == "filter-sheet"
    assert "m-only" in toggles[0]["class"].split()
    bars = [a for t, a in items if a.get("id") == "filter-sheet"]
    assert len(bars) == 1
    assert bars[0]["class"] == "filter-bar"
    assert bars[0][":class"] == "{ 'is-open': $store.sheet.name === 'filter' }"
    assert bars[0][":role"] == "$store.sheet.name === 'filter' ? 'dialog' : null"
    bar = filter_bar(page)
    assert bar.index("sheet-head m-only") < bar.index("<select")
    closes = [a for t, a in tags(bar) if t == "button" and a.get("@click") == "$store.sheet.close()"]
    dones = [a for a in closes if "sheet-done" in a.get("class", "").split()]
    assert len(closes) == 2 and len(dones) == 1
    if done is None:
        assert "x-text" not in dones[0] and ">Done</button>" in bar
    else:
        assert dones[0]["x-text"] == done


def test_logs_keeps_live_on_the_page_and_the_old_order_on_a_pc(client):
    page = client.get("/logs").text
    bar = filter_bar(page)
    assert "toggleEnabled()" not in bar
    for click in ("$store.logs.toggleDebug()", "$store.logs.clear()", "clearAllLogs()"):
        assert click in bar, click
    items = tags(page)
    live = [a for t, a in items if a.get("@click") == "$store.logs.toggleEnabled()"]
    assert len(live) == 1 and "o-1" in live[0]["class"].split()
    for t, a in items:
        if a.get("@click") in ("$store.logs.toggleDebug()", "$store.logs.clear()") or a.get("onclick") == "clearAllLogs()":
            assert "o-2" in a["class"].split(), a


@pytest.mark.parametrize("path", ["/history", "/checked-search"])
def test_search_field_stays_outside_the_sheet(client, path):
    page = client.get(path).text
    assert 'x-model="search"' not in filter_bar(page)
    row = page.rindex('class="filter-row"', 0, page.index('id="filter-sheet"'))
    assert row < page.index('x-model="search"') < page.index('id="filter-sheet"')


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_logs_filter_button_counts_the_changed_filters(client, tmp_path):
    script = component_script(client.get("/logs").text, "function logsTable()")
    out = run_node(tmp_path, script, """
const t = logsTable(); t.$store = { logs: { debug: false } };
out.start = t.activeFilters();
t.filterLevel = 'warn'; t.perPage = 50; t.$store.logs.debug = true;
out.changed = t.activeFilters();
""")
    assert (out["start"], out["changed"]) == (0, 3)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_history_filter_button_counts_the_changed_filters(client, tmp_path):
    script = component_script(client.get("/history").text, "function historyPage()")
    out = run_node(tmp_path, script, """
const h = historyPage();
out.start = h.activeFilters();
h.instance = '2'; h.skill = 'search_missing';
out.changed = h.activeFilters();
h.search = 'x';
out.search = h.activeFilters();
""")
    assert (out["start"], out["changed"], out["search"]) == (0, 2, 2)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_prefilter_filter_button_counts_the_changed_filters(client, tmp_path):
    script = component_script(client.get("/checked-search").text, "function checkedSearchPage()")
    out = run_node(tmp_path, script, """
const p = checkedSearchPage();
out.start = p.activeFilters();
p.outcome = 'error'; p.currentRound = false; p.onlyDifferences = true;
out.changed = p.activeFilters();
""")
    assert (out["start"], out["changed"]) == (0, 3)


def test_prefilter_counters_rows_and_candidates_become_list_entries(client):
    inst = make_instance(checked_search="dry_run")
    db.checked_search_log.insert({"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": "A", "outcome": "would_grab", "cache_key": "mov:1",
                                  "dry_run_round": inst["dry_run_round"]})
    page = client.get("/checked-search").text
    assert_roles_valid(page)
    summary, rows, candidates = stack_tables(page)
    assert [["c-meta"], ["c-meta"], ["c-main"], ["c-pill"]] in row_roles(summary)
    assert [["c-meta"], ["c-meta"], ["c-meta"], ["c-main"], ["c-pill"], ["c-extra"], ["c-extra"], ["c-extra"]] \
        in row_roles(rows)
    assert [["c-main"], ["c-meta"], ["c-meta"], ["c-meta"], ["c-meta"], ["c-pill"], ["c-extra"], ["c-extra"]] \
        in row_roles(candidates)
    labels = [a["data-label"] for t, a in tags(page) if t == "td" and "data-label" in a]
    assert labels == ["*arr would grab", "Filter grabs", "Rejected", "Reasons", "Notes"]
    items = tags(page)
    toggles = [a for t, a in items if t == "tr" and "row-toggle" in a.get("class", "").split()]
    assert toggles and toggles[0][":class"] == "{ 'is-open': open[row.id] }"
    assert toggles[0]["@click"] == "toggle(row.id)"
    details = [a for t, a in items if t == "tr" and a.get("class") == "row-detail"]
    assert details and details[0]["x-show"] == "open[row.id]"
    assert any(a.get("class") == "reset-row" for t, a in items if t == "div")
    checks = [a for t, a in tags(filter_bar(page)) if t == "label" and "check-label" in a.get("class", "").split()]
    assert len(checks) == 2

# ── Imports cards ────────────────────────────────────────────────────────────

def test_imports_card_actions_and_proposal_fit_a_phone(client):
    page = client.get("/imports").text
    assert_roles_valid(page)
    [table] = stack_tables(page)
    assert [["c-main"], ["c-meta"], ["c-meta"], ["c-meta"], ["c-meta"], ["c-extra"]] in row_roles(table)
    items = tags(page)
    objections = [a for t, a in items if t == "td" and a.get("data-label") == "Objections"]
    assert objections and objections[0][":class"] == "{ 'c-warn': (c.rejections || []).length }"
    actions = page.index('class="imp-actions"')
    assert actions < page.index('@click="importDownload(inst, d)"', actions) < page.index('@click="discard(inst, d)"', actions)
    labels = [a for t, a in items if t == "label" and "check-label" in a.get("class", "").split()]
    assert len(labels) == 1
    assert page.index('class="imp-actions"') < page.index('class="check-label"')
    assert 'class="imp-verdict"' in page


# ── 0.11.1: follow-ups ───────────────────────────────────────────────────────

def test_form_switches_get_a_help_icon_on_phones(client):
    # The switches Enabled, Missing and Upgrades carry their help on the label
    # (hover only); on a phone a "?" inside the label opens it on tap.
    items = tags(client.get("/instances/new").text)
    starts = [i for i, (t, a) in enumerate(items) if t == "label" and "toggle-label" in a.get("class", "").split()]
    assert len(starts) == 3
    for start in starts:
        # label > input, then the "?" right after the input (inside the label)
        (input_tag, _), (tag, icon) = items[start + 1], items[start + 2]
        assert (input_tag, tag) == ("input", "span"), items[start]
        assert icon["class"].split() == ["tooltip-icon", "m-only"]
        assert icon["data-tooltip"] == items[start][1]["data-tooltip"]
        assert (icon["tabindex"], icon["role"]) == ("0", "button")

