"""Static page, stylesheet and script of the local dashboard, kept as Python strings.

They live in a ``.py`` module so the skill bundle and plugin mirror (``scripts/sync_skill.py``
copies ``reachweaver/*.py``) ship them with no extra file type. The page is served under a
per-run token path with a strict CSP (no inline script or style), so all code is in ``APP_JS`` and
all styling in ``APP_CSS``. Data from the API (post text, errors, URLs) is always inserted with
``textContent``/``createTextNode``, never as HTML. Icons are simple shapes drawn here, not a
third-party icon set.
"""
from __future__ import annotations

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="referrer" content="no-referrer">
<title>ReachWeaver Dashboard</title>
<link rel="stylesheet" href="app.css">
<link rel="icon" href="icon.svg" type="image/svg+xml">
</head>
<body>
<a class="skip" id="skip" href="#main">Skip to content</a>
<div class="shell">
<aside class="side" id="side" aria-label="Sections">
  <div class="brand"><span class="mark" aria-hidden="true">RW</span><span>ReachWeaver</span></div>
  <span class="readonly" title="This dashboard cannot approve, schedule, pause or publish">Read-only view</span>
  <a class="writecta" href="#/write" data-route="write"><span data-icon="pen"></span><span>Write a new post</span></a>
  <nav aria-label="Sections" id="nav">
    <p class="navgroup">Work</p>
    <a href="#/" data-route="overview"><span data-icon="home"></span><span class="lbl">Overview</span><span class="count" data-count="overview"></span></a>
    <a href="#/queue" data-route="queue"><span data-icon="queue"></span><span class="lbl">Queue</span><span class="count" data-count="queue"></span></a>
    <a href="#/schedule" data-route="schedule"><span data-icon="calendar"></span><span class="lbl">Schedule</span><span class="count" data-count="schedule"></span></a>
    <a href="#/ideas" data-route="ideas"><span data-icon="idea"></span><span class="lbl">Ideas</span></a>
    <a href="#/claude" data-route="claude"><span data-icon="chat"></span><span class="lbl">Work with Claude</span></a>
    <p class="navgroup">Record</p>
    <a href="#/posted" data-route="posted"><span data-icon="posted"></span><span class="lbl">Posted</span></a>
    <a href="#/results" data-route="results"><span data-icon="results"></span><span class="lbl">Results</span></a>
    <a href="#/campaigns" data-route="campaigns"><span data-icon="campaigns"></span><span class="lbl">Campaigns</span></a>
    <p class="navgroup">Setup</p>
    <a href="#/accounts" data-route="accounts"><span data-icon="accounts"></span><span class="lbl">Accounts</span></a>
    <a href="#/distribution" data-route="distribution"><span data-icon="channels"></span><span class="lbl">Channels</span></a>
  </nav>
  <p class="sidefoot muted">Nothing is sent from here. Approve, schedule and publish in your terminal.</p>
</aside>
<div class="scrim" id="scrim" hidden></div>
<div class="page">
<header class="top">
  <button type="button" class="iconbtn menu" id="menu" aria-label="Open the section menu" aria-expanded="false" aria-controls="side"><span data-icon="menu"></span></button>
  <span class="brand small"><span class="mark" aria-hidden="true">RW</span></span>
  <div class="searchbox">
    <span data-icon="search"></span>
    <input type="search" id="gq" placeholder="Search everything" aria-label="Search posts, campaigns and shared records" maxlength="100">
    <button type="button" class="kbdhint" id="palettebtn" title="Jump to a page or post (Ctrl K)"><kbd>Ctrl K</kbd></button>
  </div>
  <div class="tools">
    <span id="updated" class="muted updated"></span>
    <button type="button" class="iconbtn" id="refresh" aria-label="Refresh" title="Refresh (r)"><span data-icon="refresh"></span></button>
    <button type="button" class="iconbtn" id="settingsbtn" popovertarget="settings" aria-label="Settings" title="Settings"><span data-icon="settings"></span></button>
  </div>
  <div id="settings" class="pop" popover aria-label="Settings">
    <p class="pophead">Theme</p>
    <div class="seg" role="group" aria-label="Theme" id="theme">
      <button type="button" data-theme-choice="auto">Auto</button><button type="button" data-theme-choice="light">Light</button><button type="button" data-theme-choice="dark">Dark</button>
    </div>
    <p class="pophead">Updates</p>
    <label class="auto"><input type="checkbox" id="auto"> Auto-refresh every 20 seconds</label>
    <label class="auto" title="A browser notification when a send needs review or something new blocks readiness; counts only, never post text"><input type="checkbox" id="alerts"> Alerts (counts only, never post text)</label>
    <p class="pophead">Help</p>
    <button type="button" id="keys" class="linkbtn">Keyboard shortcuts (?)</button>
  </div>
</header>
<main id="main" tabindex="-1"><p class="muted">Loading&hellip;</p></main>
<footer class="foot">
  <span class="muted">Local view of your own state. Nothing is sent from here; run commands in your terminal.</span>
</footer>
</div>
</div>
<nav class="tabs" aria-label="Main sections">
  <a href="#/" data-route="overview" class="tab"><span data-icon="home"></span><span>Overview</span><span class="count" data-count="overview"></span></a>
  <a href="#/queue" data-route="queue" class="tab"><span data-icon="queue"></span><span>Queue</span><span class="count" data-count="queue"></span></a>
  <a href="#/schedule" data-route="schedule" class="tab" id="scheduletab" hidden><span data-icon="calendar"></span><span>Schedule</span><span class="count" data-count="schedule"></span></a>
  <a href="#/posted" data-route="posted" class="tab"><span data-icon="posted"></span><span>Posted</span></a>
  <a href="#/results" data-route="results" class="tab"><span data-icon="results"></span><span>Results</span></a>
  <button type="button" class="tab" id="more" aria-controls="side" aria-expanded="false"><span data-icon="more"></span><span>More</span></button>
</nav>
<div id="toast" role="status" aria-live="polite"></div>
<script src="app.js"></script>
</body>
</html>
"""

# Brand tokens, defined once. The stylesheet below starts with them as CSS custom properties (--rw-accent, ...), and
# the browser composer (``share --web``) can mirror the same look by importing BRAND or BRAND_CSS from this module.
BRAND = {
    "accent": "#0b6e69",       # light theme accent (buttons, links, the RW mark)
    "accent-dark": "#3cc2b8",  # dark theme accent
    "radius": "12px",          # cards and panels
    "font": 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    "mono": "ui-monospace, SFMono-Regular, Menlo, monospace",
    "wordmark": "ReachWeaver",
}
BRAND_CSS = (":root { " + " ".join(f"--rw-{name}: {value if name != 'wordmark' else chr(34) + value + chr(34)};"
                                   for name, value in BRAND.items()) + " }\n")

# The tab icon: the same "RW" mark as the header, drawn as plain shapes (no text, no font, no script).
ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" '
            f'fill="{BRAND["accent"]}"/><path d="M7 23V9h5a4 4 0 0 1 0 8H7m5 0 4 6M16 9l2.5 14L21 13l2.5 10L26 9" '
            'fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')

APP_CSS = BRAND_CSS + r""":root {
  --bg: #f5f6f8; --panel: #ffffff; --ink: #1b1f24; --muted: #5b6573; --line: #dfe3e9; --hover: #f2f5f8;
  --accent: var(--rw-accent); --accent-ink: #ffffff; --accent-soft: #e2f2f0; --accent-strong: #08544f;
  --ok: #166a2e; --warn: #7d5400; --bad: #b42318; --info: #1f5fbf;
  --ok-bg: #e6f4ea; --warn-bg: #fff4d6; --bad-bg: #fde8e6; --info-bg: #e5eefb; --code-bg: #eef1f5;
  --shadow: 0 1px 2px rgba(16, 24, 40, .06), 0 1px 3px rgba(16, 24, 40, .08);
  --radius: var(--rw-radius); --gutter: 16px; --side: 232px; --tap: 44px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1317; --panel: #171c22; --ink: #e8ecf1; --muted: #9aa5b3; --line: #2b333d; --hover: #1d242c;
    --accent: var(--rw-accent-dark); --accent-ink: #06201d; --accent-soft: #123532; --accent-strong: #7fe0d8;
    --ok: #56d364; --warn: #e3b341; --bad: #ff7b72; --info: #79b8ff;
    --ok-bg: #12301b; --warn-bg: #3a2c08; --bad-bg: #3d1512; --info-bg: #12263f; --code-bg: #0c1014;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4);
  }
}
:root[data-theme="dark"] {
  --bg: #0f1317; --panel: #171c22; --ink: #e8ecf1; --muted: #9aa5b3; --line: #2b333d; --hover: #1d242c;
  --accent: var(--rw-accent-dark); --accent-ink: #06201d; --accent-soft: #123532; --accent-strong: #7fe0d8;
  --ok: #56d364; --warn: #e3b341; --bad: #ff7b72; --info: #79b8ff;
  --ok-bg: #12301b; --warn-bg: #3a2c08; --bad-bg: #3d1512; --info-bg: #12263f; --code-bg: #0c1014;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4);
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 var(--rw-font); }
.skip { position: absolute; left: -999px; top: 0; background: var(--accent); color: var(--accent-ink); padding: 8px 12px; z-index: 50; }
.skip:focus { left: 8px; top: 8px; display: inline-flex; align-items: center; min-height: var(--tap); }
a { color: var(--accent); }
:focus-visible { outline: 3px solid var(--info); outline-offset: 2px; border-radius: 4px; }
.ico { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; flex: none; vertical-align: -3px; }
[hidden] { display: none !important; }

/* ---- shell: sidebar + page */
.shell { display: grid; grid-template-columns: var(--side) minmax(0, 1fr); min-height: 100vh; }
.side { position: sticky; top: 0; height: 100vh; overflow-y: auto; background: var(--panel); border-right: 1px solid var(--line);
  padding: 16px 12px; display: flex; flex-direction: column; gap: 6px; z-index: 30; }
.brand { display: flex; align-items: center; gap: 9px; font-weight: 700; font-size: 16px; padding: 0 6px; }
.mark { display: inline-grid; place-items: center; width: 30px; height: 30px; border-radius: 9px;
  background: var(--accent); color: var(--accent-ink); font-size: 12px; letter-spacing: .5px; flex: none; }
.readonly { align-self: flex-start; margin: 2px 6px 6px; font-size: 12px; padding: 2px 10px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); }
.side nav { display: flex; flex-direction: column; gap: 2px; }
.navgroup { margin: 12px 10px 4px; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--muted); font-weight: 600; }
.side nav a { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: 9px; color: var(--ink); text-decoration: none; }
.side nav a:hover { background: var(--hover); }
.side nav a[aria-current="page"] { background: var(--accent-soft); color: var(--accent-strong); font-weight: 600; }
.side nav a .lbl { flex: 1; }
.sidefoot { margin-top: auto; font-size: 12px; padding: 12px 6px 0; }
.count { min-width: 20px; padding: 0 6px; border-radius: 999px; font-size: 12px; line-height: 20px; text-align: center; font-weight: 700;
  background: var(--code-bg); color: var(--muted); font-variant-numeric: tabular-nums; }
.count:empty { display: none; }
.count.c-bad { background: var(--bad); color: #fff; } .count.c-warn { background: var(--warn-bg); color: var(--warn); }
.page { min-width: 0; display: flex; flex-direction: column; }
.top { position: sticky; top: 0; z-index: 20; display: flex; align-items: center; gap: 10px; padding: 10px 24px;
  background: var(--bg); border-bottom: 1px solid var(--line); }
.top .brand.small, .top .menu { display: none; }
.searchbox { position: relative; flex: 0 1 420px; display: flex; align-items: center; }
.searchbox > .ico { position: absolute; left: 10px; color: var(--muted); pointer-events: none; }
.searchbox input[type="search"] { width: 100%; padding: 7px 70px 7px 34px; }
.kbdhint { position: absolute; right: 4px; border: 0; background: none; padding: 2px 4px; }
.tools { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.updated { font-size: 12px; margin-right: 6px; }
button, select, input { font: inherit; color: inherit; }
button { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; cursor: pointer; }
button:hover { border-color: var(--accent); }
.iconbtn { display: inline-grid; place-items: center; width: 36px; height: 36px; padding: 0; border-radius: 9px; }
button.linkbtn { border: 0; background: none; color: var(--accent); padding: 0; text-decoration: underline; }
select, input[type="text"], input[type="search"] { background: var(--panel); border: 1px solid var(--line); border-radius: 9px; padding: 7px 10px; min-width: 0; }
label.auto { display: flex; align-items: center; gap: 8px; font-size: 14px; margin: 4px 0; }
.pop { margin: 0; inset: 58px 20px auto auto; width: 290px; background: var(--panel); color: var(--ink); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 6px 16px 14px; box-shadow: 0 12px 32px rgba(0, 0, 0, .18); }
.pophead { margin: 12px 0 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--muted); font-weight: 600; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 9px; overflow: hidden; }
.seg button { border: 0; border-radius: 0; padding: 5px 12px; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); }
.tabs { display: none; }
.scrim { position: fixed; inset: 0; background: rgba(0, 0, 0, .4); z-index: 25; }

main { width: 100%; max-width: 1180px; margin: 0 auto; padding: 22px 24px 40px; flex: 1; }
main:focus { outline: none; }
main[aria-busy="true"] { cursor: progress; }
.foot { max-width: 1180px; width: 100%; margin: 0 auto; padding: 0 24px 24px; font-size: 13px; }
h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -.2px; }
h1, h2, h3, .lede, .pv-head, .tl .ta, .sumline { overflow-wrap: anywhere; }
h2 { font-size: 16px; margin: 26px 0 10px; }
h3 { font-size: 15px; margin: 18px 0 8px; }
.muted { color: var(--muted); }
.lede { margin: 0 0 18px; color: var(--muted); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 18px; margin: 0 0 14px; box-shadow: var(--shadow); }
.card > .ct, .late > .ct { margin: 0 0 10px; font-size: 15px; font-weight: 700; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; letter-spacing: 0; }
.card > .ct .count { margin-left: 2px; }
.card > .ct a { margin-left: auto; font-size: 13px; font-weight: 400; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)); gap: 14px; margin: 0 0 14px; align-items: start; }
.grid2 > .card { margin: 0; }
.banner { display: flex; gap: 12px; align-items: flex-start; border-radius: var(--radius); padding: 12px 16px; margin: 0 0 16px; border: 1px solid var(--line); }
.banner > .ico { width: 22px; height: 22px; margin-top: 1px; }
.banner b { display: block; }
.banner.ok { background: var(--ok-bg); color: var(--ok); }
.banner.attention { background: var(--warn-bg); color: var(--warn); }
.banner.blocked { background: var(--bad-bg); color: var(--bad); }
.banner small { display: block; color: var(--ink); margin-top: 2px; font-size: 13px; }
.chip { display: inline-block; max-width: 100%; font-size: 12px; padding: 1px 9px; border-radius: 999px; border: 1px solid currentColor; white-space: normal; overflow-wrap: anywhere; line-height: 18px; }
.chip.draft { color: var(--muted); } .chip.approved, .chip.scheduled { color: var(--info); }
.chip.sending { color: var(--warn); } .chip.needs_review { color: var(--bad); font-weight: 700; }
.chip.published, .chip.published_manual { color: var(--ok); } .chip.cancelled { color: var(--muted); text-decoration: line-through; }
.chip.ok { color: var(--ok); } .chip.attention, .chip.warn { color: var(--warn); } .chip.blocked, .chip.bad { color: var(--bad); }
.chip.info { color: var(--info); }
.chip.solid.bad { background: var(--bad); border-color: var(--bad); color: #fff; }
.pf { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; background: var(--muted); }
.avatar { display: inline-grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; color: #fff; font-weight: 700; background: var(--muted); flex: none; }
.pf-bluesky { background: #1185fe; } .pf-mastodon { background: #6364ff; } .pf-linkedin { background: #0a66c2; }
.pf-x { background: #3b3f45; } .pf-threads { background: #5b5f66; } .pf-instagram { background: #d62976; }
.pf-youtube { background: #e62117; } .pf-tiktok { background: #25c2c9; } .pf-facebook { background: #1877f2; }
.pf-reddit { background: #ff4500; } .pf-hackernews { background: #ff6600; } .pf-telegram { background: #229ed9; }
.pf-github { background: #6e5494; } .pf-pinterest { background: #c8232c; } .pf-other { background: var(--muted); }

/* ---- lists of posts: one compact row each; the whole row opens the post */
.rows { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); margin: 0 0 14px; }
.card .rows { box-shadow: none; margin: 0; border: 0; border-radius: 0; }
.row { position: relative; display: grid; grid-template-columns: 124px minmax(0, 1fr) auto; grid-template-areas: "st main when";
  gap: 2px 14px; padding: 11px 16px; border-top: 1px solid var(--line); align-items: start; }
.row:first-child { border-top: 0; }
.row:hover { background: var(--hover); }
.row[hidden] { display: none; }
.row.sel { outline: 3px solid var(--info); outline-offset: -3px; background: var(--hover); }
.row > .st { grid-area: st; padding-top: 1px; }
.row > .main { grid-area: main; min-width: 0; }
.row > .when { grid-area: when; text-align: right; font-size: 13px; color: var(--muted); white-space: nowrap; }
.row .txt { color: var(--ink); text-decoration: none; font-weight: 500; overflow-wrap: anywhere;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
a.txt::after { content: ""; position: absolute; inset: 0; }
a.txt:hover { text-decoration: underline; }
.row .over, .row .when a { position: relative; z-index: 1; }
.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 12px; font-size: 13px; color: var(--muted); margin-top: 2px; }
.row .bad { color: var(--bad); font-size: 13px; margin-top: 2px; }
.grouphead { padding: 7px 16px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; color: var(--muted);
  background: var(--code-bg); border-top: 1px solid var(--line); }
.grouphead:first-child { border-top: 0; }
.more-link { display: block; padding: 9px 16px; border-top: 1px solid var(--line); font-size: 14px; }

.tablewrap { overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); margin: 0 0 14px; }
.card .tablewrap { box-shadow: none; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 9px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
td { overflow-wrap: anywhere; }
tr[hidden] { display: none; }
tr.sel { outline: 3px solid var(--info); outline-offset: -3px; background: var(--code-bg); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.row-link { color: var(--ink); text-decoration: none; display: block; }
.row-link:hover { text-decoration: underline; }
.warnbadge { display: inline-block; margin-left: 6px; font-size: 12px; color: var(--warn); border: 1px solid var(--warn); border-radius: 999px; padding: 0 8px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; align-items: center; }
.filters input[type="text"] { flex: 1 1 220px; max-width: 420px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
button.chipbtn { border-radius: 999px; padding: 3px 12px; font-size: 13px; }
button.chipbtn[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.chipbtn .n { opacity: .75; margin-left: 4px; font-variant-numeric: tabular-nums; }
.empty { text-align: center; padding: 30px 16px; color: var(--muted); }
.empty > .ico { width: 30px; height: 30px; color: var(--accent); margin-bottom: 6px; }
.empty b { display: block; color: var(--ink); font-size: 16px; margin-bottom: 4px; }
.allclear { display: flex; gap: 10px; align-items: center; color: var(--ok); font-weight: 600; }
.allclear .ico { width: 22px; height: 22px; }
.exact { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--code-bg); border: 1px solid var(--line);
  border-radius: 9px; padding: 12px; margin: 0; font: 14px/1.5 var(--rw-mono); }
.meter { display: flex; align-items: center; gap: 10px; margin: 8px 0 0; font-size: 13px; color: var(--muted); }
.meter .track { flex: 1; max-width: 260px; height: 8px; border-radius: 999px; background: var(--code-bg); border: 1px solid var(--line); overflow: hidden; }
.meter .bar { height: 100%; background: var(--ok); }
.meter.tight .bar { background: var(--warn); } .meter.over .bar { background: var(--bad); }
.meter.over { color: var(--bad); font-weight: 700; }
dl.kv { display: grid; grid-template-columns: minmax(110px, 200px) 1fr; gap: 5px 14px; margin: 0; }
dl.kv dt { color: var(--muted); } dl.kv dd { margin: 0; overflow-wrap: anywhere; min-width: 0; }
.aside dl.kv { grid-template-columns: 1fr; gap: 0; } .aside dl.kv dt { font-size: 12px; margin-top: 8px; } .aside dl.kv dt:first-child { margin-top: 0; }
code, pre.json { font: 13px/1.45 var(--rw-mono); }
pre.json { background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px; padding: 10px; overflow-x: auto; margin: 6px 0 0; max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
.cmd { display: flex; gap: 8px; align-items: center; background: var(--code-bg); border: 1px solid var(--line); border-radius: 9px; padding: 6px 8px 6px 12px; margin: 6px 0; }
.cmd code { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.cmd button { flex: none; }
.next ol { margin: 0; padding-left: 20px; } .next li { margin: 0 0 10px; } .next li:last-child { margin: 0; }
.note { color: var(--muted); font-size: 13px; }
.status-card { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: flex-start; border-left: 4px solid var(--muted); }
.status-card.needs_review { border-left-color: var(--bad); background: var(--bad-bg); }
.status-card.approved, .status-card.scheduled { border-left-color: var(--info); }
.status-card.published { border-left-color: var(--ok); }
.status-card .what { flex: 1 1 320px; min-width: 0; } .status-card .what strong { display: block; font-size: 16px; }
.pager { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin: 0 0 12px; }
.pager a { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; }
.reason { padding: 9px 0; border-bottom: 1px solid var(--line); } .reason:last-child { border: 0; padding-bottom: 0; } .reason:first-of-type { padding-top: 0; }
.reason .lvl { font-weight: 700; margin-right: 6px; }
.reason .lvl.blocked { color: var(--bad); } .reason .lvl.attention { color: var(--warn); }
details { margin: 8px 0; } summary { cursor: pointer; color: var(--muted); }
.error { background: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad); border-radius: var(--radius); padding: 12px 16px; margin: 0 0 12px; }
.warn { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn); border-radius: var(--radius); padding: 12px 16px; margin: 0 0 12px; }
#toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); background: var(--ink); color: var(--bg);
  padding: 8px 16px; border-radius: 999px; font-size: 14px; opacity: 0; pointer-events: none; z-index: 60; }
#toast.show { opacity: 1; }
dialog { background: var(--panel); color: var(--ink); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 22px; max-width: 480px; }
dialog::backdrop { background: rgba(0, 0, 0, .45); }
dialog h2 { margin-top: 0; }
dialog table td { border: 0; padding: 3px 10px 3px 0; }
kbd { font: 12px var(--rw-mono); background: var(--code-bg); border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; color: var(--muted); }

/* ---- command palette */
dialog.palette { width: min(620px, calc(100vw - 32px)); max-width: none; padding: 0; margin-top: 12vh; overflow: hidden; }
.palette input { width: 100%; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; padding: 15px 18px; font-size: 16px; background: transparent; }
.palette input:focus-visible { outline: none; }
.palette ul { list-style: none; margin: 0; padding: 6px; max-height: 52vh; overflow-y: auto; }
.palette li { display: flex; gap: 10px; align-items: center; padding: 8px 10px; border-radius: 8px; cursor: pointer; min-width: 0; }
.palette li .pt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.palette li .pk { font-size: 12px; color: var(--muted); }
.palette li[aria-selected="true"] { background: var(--accent-soft); color: var(--accent-strong); }
.palette .phint { margin: 0; padding: 8px 18px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); }

/* ---- overview */
.stack { display: flex; height: 12px; border-radius: 999px; overflow: hidden; background: var(--code-bg); margin: 4px 0 12px; }
.stack span { height: 100%; }
.stack span + span { border-left: 2px solid var(--panel); }
.s-needs_review { background: var(--bad); } .s-sending { background: var(--warn); } .s-approved { background: #5b8def; }
.s-scheduled { background: var(--info); } .s-draft { background: #9aa5b3; } .s-published, .s-published_manual { background: var(--ok); } .s-cancelled { background: var(--line); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; }
.legend a { display: inline-flex; align-items: center; gap: 6px; color: var(--ink); text-decoration: none; font-size: 14px; }
.legend a:hover { text-decoration: underline; }
.legend .sw { width: 10px; height: 10px; border-radius: 3px; }
.legend b { font-variant-numeric: tabular-nums; }
.kindtag { font-size: 12px; font-weight: 600; white-space: nowrap; }
.kindtag.t-bad { color: var(--bad); } .kindtag.t-warn { color: var(--warn); } .kindtag.t-info { color: var(--info); }
.flag { display: block; font-size: 13px; color: var(--warn); } .flag.bad { color: var(--bad); font-weight: 600; }
.src { display: inline-block; max-width: 100%; font-size: 12px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); overflow-wrap: anywhere; }
.suggest ol { margin: 6px 0 0; padding-left: 20px; } .suggest li { margin: 0 0 10px; }
.late > .ct { margin-bottom: 6px; }

/* ---- post page */
.post-grid { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 20px; align-items: start; }
.aside { position: sticky; top: 76px; }
.aside .card { margin-bottom: 12px; }
.preview { display: grid; grid-template-columns: 40px minmax(0, 1fr); gap: 12px; background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; }
.pv-head { display: flex; flex-wrap: wrap; gap: 0 8px; align-items: baseline; }
.pv-head b { font-size: 15px; } .pv-head span { color: var(--muted); font-size: 14px; }
.pv-text { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 2px; }
.pv-link, .pv-tag { color: var(--info); }
.tl { list-style: none; margin: 0; padding: 0 0 0 16px; border-left: 2px solid var(--line); }
.tl li { position: relative; padding: 0 0 14px 12px; }
.tl li:last-child { padding-bottom: 0; }
.tl li::before { content: ""; position: absolute; left: -24px; top: 5px; width: 10px; height: 10px; border-radius: 50%; background: var(--panel); border: 2px solid var(--muted); }
.tl li.ok::before { border-color: var(--ok); background: var(--ok); } .tl li.bad::before { border-color: var(--bad); background: var(--bad); }
.tl li.info::before { border-color: var(--info); }
.tl .ta { font-weight: 600; } .tl .td { font-size: 13px; color: var(--muted); overflow-wrap: anywhere; }
.spark { display: block; width: 100%; max-width: 420px; height: 96px; color: var(--accent); }
.spark .axis { stroke: var(--line); stroke-width: 1; }
.spark polyline { fill: none; stroke: currentColor; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
.spark circle { fill: var(--panel); stroke: currentColor; stroke-width: 2; }
.hbar { height: 6px; border-radius: 999px; background: var(--code-bg); margin-top: 5px; max-width: 220px; overflow: hidden; }
.hbar span { display: block; height: 100%; background: var(--accent); }

/* ---- schedule */
.viewswitch { display: inline-flex; gap: 6px; margin: 0 0 12px; }
a.chipbtn { border: 1px solid var(--line); border-radius: 999px; padding: 3px 12px; font-size: 13px; text-decoration: none; color: var(--ink); background: var(--panel); }
a.chipbtn[aria-current="true"] { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.cal { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.cal .dow { font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); padding: 0 4px; }
.cal .cell { background: var(--panel); border: 1px solid var(--line); border-radius: 9px; min-height: 96px; padding: 6px; min-width: 0; }
.cal .cell.past { opacity: .55; } .cal .cell.today { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
.cal .cell.picked { box-shadow: inset 0 0 0 2px var(--info); }
.cal .num { font-size: 12px; color: var(--muted); display: block; margin-bottom: 4px; text-decoration: none; }
.cal a.num { color: var(--accent); font-weight: 600; }
.cal .item { display: flex; align-items: center; gap: 5px; font-size: 12px; text-decoration: none; color: var(--ink); white-space: nowrap; overflow: hidden;
  border-radius: 5px; padding: 1px 4px; margin: 2px 0; background: var(--info-bg); }
.cal .item .dot { width: 7px; height: 7px; }
.cal .item span:last-child { overflow: hidden; text-overflow: ellipsis; }
.cal .item time { font-variant-numeric: tabular-nums; color: var(--muted); }
.cal .item.overdue { background: var(--warn-bg); } .cal .item.expired { background: var(--bad-bg); }
.cal .more { font-size: 12px; }
.day { margin: 20px 0 8px; font-size: 15px; } .day small { color: var(--muted); font-weight: 400; margin-left: 6px; }
.legendrow { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 10px 0; font-size: 13px; color: var(--muted); }
.legendrow span { display: inline-flex; align-items: center; gap: 6px; }
.legendrow i { width: 12px; height: 12px; border-radius: 3px; display: inline-block; background: var(--info-bg); border: 1px solid var(--line); }
.legendrow i.ov { background: var(--warn-bg); } .legendrow i.ex { background: var(--bad-bg); }

/* ---- cards grid: campaigns, accounts, channels */
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 290px), 1fr)); gap: 12px; margin: 0 0 14px; }
.cards > .card { margin: 0; display: flex; flex-direction: column; gap: 6px; }
a.card { color: var(--ink); text-decoration: none; }
a.card:hover { border-color: var(--accent); }
.card .title { font-weight: 700; font-size: 16px; overflow-wrap: anywhere; }
.acct { display: flex; gap: 12px; align-items: center; }
.acct .acctwho { min-width: 0; } .acct .acctwho b { display: block; overflow-wrap: anywhere; }
.rank { display: inline-grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: var(--accent-soft); color: var(--accent-strong); font-weight: 700; font-size: 13px; flex: none; }
.chan-head { display: flex; gap: 10px; align-items: center; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; }

/* ---- work with Claude: composer, prompt cards, owner-only cards, activity feed, ideas */
textarea { font: inherit; color: inherit; background: var(--panel); border: 1px solid var(--line); border-radius: 9px; padding: 8px 10px;
  width: 100%; min-width: 0; resize: vertical; display: block; }
textarea.promptout { font: 13px/1.45 var(--rw-mono); background: var(--code-bg); color: var(--ink); }
button.primary { display: inline-flex; align-items: center; gap: 6px; background: var(--accent); color: var(--accent-ink); border-color: var(--accent); font-weight: 600; }
button.primary:hover { background: var(--accent-strong); border-color: var(--accent-strong); }
.composer { display: flex; flex-direction: column; gap: 8px; }
.composer > label { font-weight: 600; font-size: 14px; }
.composer .row2 { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; }
.composer .row2 label { font-size: 14px; color: var(--muted); }
.composer .row2 select { flex: 1 1 200px; width: 100%; min-width: 0; max-width: 100%; text-overflow: ellipsis; }
.grid2 > .card, .composer, .pcard { min-width: 0; }  /* a long option or prompt line must not widen the page */
.composer details { margin: 0; }
.olabel { font-size: 13px; color: var(--muted); margin: 10px 0 6px; }
.composer .olabel { margin: 0 0 4px; }
.pact { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.copied { font-size: 13px; color: var(--ok); font-weight: 600; }
.pcards { display: grid; gap: 10px; }
.pcard { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: var(--panel); display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.pcard .pt { display: flex; align-items: center; gap: 8px; } .pcard .pt .ico, .card > .ct > .ico { color: var(--accent); }
.pcard details { margin: 0; } .pcard summary { font-size: 13px; }
.pcard .promptout, .row .promptout { margin-top: 6px; }
.later:empty { display: none; }
.steps { list-style: none; display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 150px), 1fr)); gap: 10px; padding: 0; margin: 0 0 16px; counter-reset: step; }
.steps li { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 10px 14px 10px 48px; counter-increment: step; font-size: 14px; }
.steps li::before { content: counter(step); position: absolute; left: 12px; top: 10px; width: 26px; height: 26px; border-radius: 50%;
  background: var(--accent-soft); color: var(--accent-strong); font-weight: 700; font-size: 13px; display: grid; place-items: center; }
.owners { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 360px), 1fr)); gap: 12px; margin: 0 0 14px; align-items: start; }
.owners > .card { margin: 0; }
.late .owners { grid-template-columns: 1fr; gap: 10px; } .late .owner { box-shadow: none; padding: 12px 14px; }
.owner { border-left: 4px solid var(--info); } .owner.t-bad { border-left-color: var(--bad); } .owner.t-warn { border-left-color: var(--warn); }
.ohead { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; font-size: 14px; min-width: 0; }
.ohead > * { min-width: 0; max-width: 100%; }
.owner, .owners > .card, .meta > * { min-width: 0; max-width: 100%; }
.otext { margin: 6px 0 0; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.why { display: flex; gap: 8px; align-items: flex-start; margin: 8px 0 2px; font-size: 13px; color: var(--muted); }
.why .ico { width: 16px; height: 16px; margin-top: 1px; color: var(--warn); }
.ocmd { margin-top: 8px; } .ocmd .olabel { margin: 0; color: var(--ink); }
.who { display: inline-block; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; padding: 0 7px; border-radius: 999px; line-height: 18px; }
.who.you { background: var(--warn-bg); color: var(--warn); } .who.claude { background: var(--ok-bg); color: var(--ok); }
.feed { list-style: none; margin: 0; padding: 0; }
.feed li { display: grid; grid-template-columns: 12px minmax(0, 1fr); gap: 10px; padding: 8px 0; border-top: 1px solid var(--line); }
.feed li:first-child { border-top: 0; padding-top: 2px; }
.fdot { width: 10px; height: 10px; border-radius: 50%; margin-top: 6px; background: var(--line); }
.feed .t-ok .fdot { background: var(--ok); } .feed .t-bad .fdot { background: var(--bad); } .feed .t-warn .fdot { background: var(--warn); } .feed .t-info .fdot { background: var(--info); }
.fmain { min-width: 0; font-size: 14px; }
.fx { display: block; color: var(--ink); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
a.fx:hover { text-decoration: underline; }
.tag { font-size: 12px; color: var(--accent-strong); background: var(--accent-soft); border-radius: 999px; padding: 0 8px; }
.row .pact { margin-top: 6px; } .row details { margin: 4px 0 0; }

/* ---- overview: one next step, a calm note unless something is really wrong */
.nextstep { border-left: 4px solid var(--accent); }
.nextstep .lead { font-size: 17px; font-weight: 600; margin: 0 0 4px; overflow-wrap: anywhere; }
.nextstep .cmd { font-size: 15px; }
.nextstep .cmd button { font-weight: 600; }
.alsosteps { list-style: none; margin: 14px 0 0; padding: 0; border-top: 1px solid var(--line); }
.alsosteps li { padding: 10px 0 0; min-width: 0; }
.alsosteps .cmd { margin: 4px 0 0; }
.calm { display: flex; gap: 10px; align-items: flex-start; margin: 0 0 16px; padding: 10px 14px; border: 1px solid var(--line);
  border-radius: var(--radius); background: var(--panel); color: var(--muted); font-size: 14px; }
.calm .ico { color: var(--accent); margin-top: 2px; }
.calm.ok .ico { color: var(--ok); }
.problems { margin: 6px 0 0; padding-left: 18px; color: var(--ink); font-size: 14px; }
.techdetails { margin: 18px 0 0; }
.techdetails > summary { font-weight: 600; color: var(--ink); }
.cmd button.done { border-color: var(--ok); color: var(--ok); }

/* ---- results: one line for the chosen window, then the top posts */
.sumline { font-size: 17px; font-weight: 600; margin: 0 0 4px; }
.tops { margin: 10px 0 0; padding-left: 20px; } .tops li { margin: 0 0 8px; overflow-wrap: anywhere; }
.postid { display: block; font-size: 12px; font-weight: 400; color: var(--muted); margin-top: 4px; }
.postid code { font-size: 12px; }
.writecta { display: flex; align-items: center; gap: 10px; margin: 0 4px 8px; padding: 9px 12px; border-radius: 10px; min-height: var(--tap);
  background: var(--accent); color: var(--accent-ink); text-decoration: none; font-weight: 600; }
.writecta:hover { background: var(--accent-strong); }
.side nav a.writecta[aria-current="page"] { background: var(--accent-strong); color: var(--accent-ink); }
/* "Write a new post" as a page button: the Overview's top (phones, where the sidebar is a drawer), the next step and
   the Write page. Under `studio` it is a real link to the composer; on its own the dashboard links to #/write. */
.writebtn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 44px; padding: 10px 18px;
  border-radius: 10px; background: var(--accent); color: var(--accent-ink); text-decoration: none; font-weight: 700; }
.writebtn:hover { background: var(--accent-strong); color: var(--accent-ink); }
.writebtn .ico { width: 18px; height: 18px; }
.overtop { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px 14px; flex-wrap: wrap; }
.overtop .writebtn { display: none; }
.linkline { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin: 8px 0; }

/* ---- loading skeleton */
.sk { border-radius: 8px; background: var(--code-bg); margin: 0 0 12px; }
.sk.h { height: 30px; width: 220px; } .sk.l { height: 16px; width: 60%; } .sk.b { height: 120px; }

@media (max-width: 860px) {
  /* the sidebar becomes a drawer; a bottom tab bar holds the four busiest sections */
  .shell { display: block; }
  .side { position: fixed; left: 0; top: 0; bottom: 0; height: auto; width: 280px; max-width: 86vw; transform: translateX(-105%); box-shadow: 0 0 40px rgba(0, 0, 0, .3); }
  .side.open { transform: none; }
  .overtop .writebtn { display: inline-flex; }  /* the sidebar's button is in the closed drawer here */
  .top { padding: 8px 12px; gap: 8px; }
  .top .menu { display: inline-grid; } .top .brand.small { display: flex; padding: 0; }
  .iconbtn { width: var(--tap); height: var(--tap); flex: none; }
  .side nav a { min-height: var(--tap); }
  .searchbox { flex: 1 1 auto; }
  .kbdhint, .updated, #refresh { display: none; }
  .searchbox input[type="search"] { padding-right: 10px; }
  main { padding: 16px var(--gutter) 96px; }
  .foot { padding-bottom: 96px; }
  .tabs { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); position: fixed; left: 0; right: 0; bottom: 0; z-index: 20;
    background: var(--panel); border-top: 1px solid var(--line); padding: 4px 4px 8px; }
  .tab { position: relative; display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 6px 2px; min-height: var(--tap); border: 0; border-radius: 10px;
    background: none; color: var(--muted); text-decoration: none; font-size: 11px; }
  .tab .ico { width: 22px; height: 22px; }
  .tab[aria-current="page"] { color: var(--accent-strong); background: var(--accent-soft); }
  .tab .count { position: absolute; top: 2px; left: calc(50% + 6px); min-width: 18px; line-height: 18px; font-size: 11px; padding: 0 5px; }
  .post-grid { grid-template-columns: 1fr; }
  .aside { position: static; }
  .pop { inset: 54px 12px auto auto; }
}
@media (max-width: 640px) {
  h1 { font-size: 21px; }
  dl.kv { grid-template-columns: 1fr; } dl.kv dt { margin-top: 6px; }
  .row { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "st when" "main main"; padding: 10px 14px; gap: 4px 10px; }
  .tablewrap { border: 0; background: none; overflow: visible; box-shadow: none; }
  table, tbody, tr, td { display: block; width: 100%; }
  thead { position: absolute; left: -9999px; }
  tr { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); margin: 0 0 10px; padding: 6px 0; }
  td { border: 0; padding: 4px 14px; }
  td[data-label]::before { content: attr(data-label); display: block; font-size: 11px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  td.num { text-align: left; }
  .cal { grid-template-columns: 1fr; } .cal .dow, .cal .cell.blank { display: none; } .cal .cell { min-height: 0; }
  .cal .item { white-space: normal; }
  .grid2 { grid-template-columns: 1fr; }
  /* touch targets of at least 44 x 44 CSS pixels (WCAG 2.5.5) on a phone */
  button, select, input[type="text"], input[type="search"], a.chipbtn, button.chipbtn { min-height: var(--tap); }
  a.chipbtn, button.chipbtn, button.linkbtn { display: inline-flex; align-items: center; justify-content: center; min-width: var(--tap); }
  button.linkbtn { min-height: var(--tap); }
  summary { padding: 12px 0; min-height: var(--tap); }
  .row .txt, .row-link { min-height: var(--tap); }
  main a:not(.txt):not(.card):not(.row-link):not(.more-link):not(.fx):not(.item):not(.num):not(.more) {
    display: inline-flex; align-items: center; min-height: var(--tap); min-width: var(--tap); }
  .more-link { display: flex; align-items: center; min-height: var(--tap); }
  .feed a.fx { display: block; line-height: var(--tap); }
  .pager { flex-wrap: wrap; }
}
@media print {
  /* A post sheet printed or saved as PDF: the exact text and the approval binding, no controls. */
  :root, :root[data-theme="dark"] { --bg: #fff; --panel: #fff; --ink: #000; --muted: #444; --line: #bbb; --code-bg: #fff; --shadow: none; }
  .top, footer, .pager, .side, .tabs, .scrim, .aside .cmd button, #toast, .skip, button { display: none !important; }
  .claudecard, .askcard, .pcards { display: none !important; }
  .shell { display: block; }
  main { max-width: none; padding: 0; }
  .post-grid { display: block; }
  .card, .exact, pre.json, .tablewrap, .preview { break-inside: avoid; }
  a[href^="http"]::after { content: " (" attr(href) ")"; font-size: 11px; }
}
@media (prefers-reduced-motion: no-preference) {
  .card, button, .side nav a, .row { transition: border-color .12s, background .12s; }
  #toast { transition: opacity .18s; }
  .side { transition: transform .2s ease; }
  .sk { background: linear-gradient(90deg, var(--code-bg), var(--line), var(--code-bg)); background-size: 200% 100%; animation: shimmer 1.2s linear infinite; }
  @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
}
"""

APP_JS = r""""use strict";
// All data from /api is inserted with textContent/createTextNode: never as HTML.
const main = document.getElementById("main");
const REASON_HELP = {
  emergency_pause_active: "The emergency stop is on, so nothing new will be sent. Resume deliberately in your terminal when you are ready.",
  needs_review: "A send ended in an unknown state. Check the native platform yourself, then record the outcome with the reconcile command. Never just resend.",
  stale_sending: "A send started a while ago and never finished. Treat it like an uncertain send: check the platform first.",
  overdue_scheduled: "A scheduled post is past its time. It is only sent by a worker you run.",
  approval_rebind_required: "An approval no longer matches the stored text or settings. Review the post and approve it again.",
  credentials_missing: "Some credential environment variables are not set. Only their names are shown here.",
  rolling_budget_exhausted: "The 24-hour send budget for this account is used up.",
  provider_cooldown_active: "The platform asked us to wait; sends for this account are paused until the cooldown ends.",
  accounts_config_unloadable: "The accounts file could not be loaded. Add an account with reachweaver account-add bluesky (public details only), or check the file with reachweaver doctor.",
  no_accounts_configured: "No accounts are configured yet. Drafts can still be exported for manual posting.",
  manual_platform_has_queued_posts: "Posts are queued for a platform that is manual-only; post them yourself from the export.",
  registry_entry_stale: "The platform capability research is old. Re-check the platform before relying on it.",
  research_facts_stale: "Some research facts about this platform (formats, timing, profile tips) are over 120 days old. Run registry-age to see which, and re-check them before relying on them.",
  research_registry_invalid: "A bundled research file failed its checks, so its platform facts are not used. Reinstall or update ReachWeaver.",
  approval_key_unavailable: "Keyed approvals are on but the key is not readable in this process.",
  caps_file_invalid: "The send-limits file is invalid.",
  capability_registry_invalid: "The capability registry failed validation.",
  linkedin_authorization_expired: "The LinkedIn authorization has expired. Re-run the consent, export the new token and update authorization_expires_at.",
  linkedin_authorization_warning: "The LinkedIn authorization expires within a week. Re-authorize soon.",
  linkedin_authorization_unknown: "authorization_expires_at is not recorded for LinkedIn, so expiry cannot be warned about. Record it.",
  instagram_authorization_expired: "The Instagram token has expired. Refresh or regenerate it and update authorization_expires_at.",
  instagram_authorization_warning: "The Instagram token expires within a week. Refresh it soon.",
  instagram_authorization_unknown: "authorization_expires_at is not recorded for Instagram, so expiry cannot be warned about. Record it.",
  threads_authorization_expired: "The Threads token has expired. Refresh or regenerate it and update authorization_expires_at.",
  threads_authorization_warning: "The Threads token expires within a week. Refresh it soon.",
  threads_authorization_unknown: "authorization_expires_at is not recorded for Threads, so expiry cannot be warned about. Record it.",
  instagram_authorization_expiry_invalid: "authorization_expires_at for Instagram cannot be read, so the token is treated as expired. Fix the date in the accounts file.",
  threads_authorization_expiry_invalid: "authorization_expires_at for Threads cannot be read, so the token is treated as expired. Fix the date in the accounts file.",
  linkedin_version_unsupported: "The LinkedIn API version in your accounts file (linkedin_version) is a year old or more, so automatic LinkedIn sending is refused. Posting by hand is not affected. Fix: put the 'Latest Version' from LinkedIn's versioning page (see docs/LINKEDIN.md) into linkedin_version in config/accounts.json.",
  linkedin_version_ageing: "The LinkedIn API version (linkedin_version) is getting old. Fix: move it to a newer version from LinkedIn's versioning page (docs/LINKEDIN.md) before it stops working.",
  credential_unreadable: "A credential file or variable exists but cannot be read (for example its permissions). Only its name is shown. Fix: check the file named here; ReachWeaver never shows the value.",
  provider_cooldown_ceiling_clamped: "A platform asked for a wait longer than 24 hours; ReachWeaver waits 24 hours at most. Fix: look at the account on the platform before anything else is sent.",
  ai_disclosure_registry_aging: "The bundled AI-label rules were checked a while ago. Fix: re-check the platform pages they cite (reachweaver registry-age) before relying on the AI-label check.",
  ai_disclosure_registry_invalid: "The bundled AI-label rules failed their checks, so a post with media needs an explicit AI-label answer. Fix: reinstall or update ReachWeaver.",
  queued_posts_for_unconfigured_accounts: "Some approved or scheduled posts belong to an account that is no longer in your accounts file, so they cannot be sent. Fix: add the account back, or cancel those posts in your terminal.",
  approval_replay_reapproval_required: "A post's approval was already used once (or came back from an older copy of the database). Fix: approve it again in your terminal before it can be sent.",
  approval_withdrawn_reapproval_required: "A post's approval was withdrawn earlier, yet the post is approved again in this database (an older copy). Fix: approve it again in your terminal before it can be sent.",
  approval_nonce_missing_reapproval_required: "A post was approved by an older version without the one-time approval record. Fix: approve it again in your terminal before it can be sent.",
  approval_ledger_broken: "The approval record (state/approval-ledger.jsonl) does not match the database, so the affected sends are refused. Fix: restore the matching file from your backup; never edit it by hand.",
  approval_ledger_unavailable: "The approval record (state/approval-ledger.jsonl) is missing or unreadable, so approved posts cannot be checked and are not sent. Fix: restore it next to the database from your backup."
};
const STATUS_LABEL = { draft: "draft", approved: "approved", scheduled: "scheduled", sending: "sending",
  needs_review: "needs review", published: "published", cancelled: "cancelled", published_manual: "posted by you" };
const STATUS_ORDER = ["needs_review", "sending", "approved", "scheduled", "draft", "published", "published_manual", "cancelled"];
const SENT_STATUSES = ["sending", "published", "needs_review"];
const STATUS_MEANING = {
  draft: ["Draft", "Not approved yet. Nothing can be sent until you approve this exact text."],
  approved: ["Approved", "Approved and waiting. A worker you run decides when it is sent; you can preview it with a dry run."],
  scheduled: ["Scheduled", "Approved with a due time. It is only sent by a worker you run."],
  sending: ["Sending", "A send is in progress or was interrupted. If it stays here, treat it as uncertain."],
  needs_review: ["Needs your review", "A send ended in an unknown state. Check the native platform first; never resend blindly."],
  published: ["Published", "Sent. Open the remote post to confirm it looks right."],
  cancelled: ["Cancelled", "This post will never be sent."],
  published_manual: ["Posted by you", "You posted this yourself and recorded it, so its results can be measured. ReachWeaver did not send it and never will."]
};
const STATUS_GROUP = { needs_review: "Needs your review", sending: "Sending", approved: "Approved, waiting", scheduled: "Scheduled",
  draft: "Drafts", published: "Published", cancelled: "Cancelled", published_manual: "Posted by you" };
// Plain words for readiness codes on the page; the code itself stays in the details for searching the docs.
const REASON_TITLE = {
  emergency_pause_active: "Emergency stop is on", needs_review: "A send ended in an unknown state (uncertain send — check the platform)",
  stale_sending: "A send never finished (uncertain send — check the platform)", overdue_scheduled: "A scheduled post is past its time",
  approval_rebind_required: "An approval no longer matches its post", credentials_missing: "Not connected (only needed for automatic sending)",
  no_accounts_configured: "No accounts set up (only needed for automatic sending)", registry_entry_stale: "Platform research is getting old",
  research_facts_stale: "Some platform facts are getting old", manual_platform_has_queued_posts: "Approved posts for a platform you post to by hand",
  provider_cooldown_active: "A platform asked to wait", rolling_budget_exhausted: "Today's send limit is used up",
  accounts_config_unloadable: "The accounts file cannot be read", caps_file_invalid: "The send-limits file is invalid",
  linkedin_version_unsupported: "LinkedIn API version too old (only for automatic sending)",
  linkedin_version_ageing: "LinkedIn API version is getting old (only for automatic sending)"
};
function reasonTitle(code) { return REASON_TITLE[code] || code.replace(/_/g, " "); }
const ADAPTER_LABEL = { live_text: "can send automatically", backend: "sends through a scheduler", manual: "you post by hand", other: "other" };
// Why a post is on the "Needs you" list (ui_data.ATTENTION_KINDS): label, what to do, colour.
const KIND = {
  needs_review: ["Needs review", "Check the platform, then reconcile in your terminal. Never resend blindly.", "bad"],
  sending: ["Sending", "In progress or interrupted. If it stays, treat it as uncertain.", "warn"],
  approval_expired: ["Approval expired", "Approve it again before it can be sent.", "bad"],
  overdue: ["Past its time", "Due and not sent. Check it still makes sense before a worker runs.", "warn"],
  expires_before_due: ["Approval expires first", "The approval runs out before the due time.", "warn"],
  draft: ["Waiting for approval", "Nothing is sent until you approve this exact text.", "info"]
};

// ---- small DOM helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
// Icons: plain strokes on a 24x24 grid, drawn for this page.
const SVGNS = "http://www.w3.org/2000/svg";
const ICONS = {
  home: ["M3 11l9-7 9 7", "M5 10v10h5v-6h4v6h5V10"], queue: ["M9 6h12", "M9 12h12", "M9 18h12", "M4 6h.01", "M4 12h.01", "M4 18h.01"],
  calendar: ["M4 6h16v14H4z", "M4 10h16", "M8 3v4", "M16 3v4"], posted: ["M4 12l5 5L20 6"],
  results: ["M5 20V12", "M12 20V5", "M19 20v-6"], campaigns: ["M12 3L3 8l9 5 9-5z", "M3 13l9 5 9-5"],
  accounts: ["M12 4a4 4 0 1 1 0 8a4 4 0 1 1 0-8z", "M4 21c1-4 4-6 8-6s7 2 8 6"],
  channels: ["M12 3a9 9 0 1 1 0 18a9 9 0 1 1 0-18z", "M3 12h18", "M12 3c3.5 3.5 3.5 14.5 0 18", "M12 3c-3.5 3.5-3.5 14.5 0 18"],
  search: ["M11 4a7 7 0 1 1 0 14a7 7 0 1 1 0-14z", "M20 20l-4-4"], refresh: ["M20 12a8 8 0 1 1-2.4-5.7", "M20 4v5h-5"],
  settings: ["M4 7h9", "M17 7h3", "M4 17h3", "M11 17h9", "M15 5v4", "M9 15v4"], menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  more: ["M5 12h.01", "M12 12h.01", "M19 12h.01"], alert: ["M12 4L2 20h20z", "M12 10v4", "M12 17h.01"],
  check: ["M12 3a9 9 0 1 1 0 18a9 9 0 1 1 0-18z", "M8 12l3 3 5-6"], clock: ["M12 3a9 9 0 1 1 0 18a9 9 0 1 1 0-18z", "M12 7v5l3 2"],
  external: ["M14 4h6v6", "M20 4l-9 9", "M18 14v6H4V6h6"], back: ["M15 5l-7 7 7 7"], fwd: ["M9 5l7 7-7 7"], stop: ["M6 6h12v12H6z"],
  idea: ["M9 18h6", "M10 21h4", "M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"],
  chat: ["M4 5h16v11H9l-5 4z", "M8 9h8", "M8 12h5"], copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  lock: ["M6 11h12v9H6z", "M8.5 11V8a3.5 3.5 0 0 1 7 0v3"], pen: ["M4 20h4L19 9l-4-4L4 16z", "M13 7l4 4"]
};
function icon(name) {
  const svg = document.createElementNS(SVGNS, "svg");
  for (const [k, v] of [["viewBox", "0 0 24 24"], ["class", "ico"], ["aria-hidden", "true"], ["focusable", "false"]]) svg.setAttribute(k, v);
  for (const d of ICONS[name] || []) { const p = document.createElementNS(SVGNS, "path"); p.setAttribute("d", d); svg.append(p); }
  return svg;
}
function svgEl(tag, attrs) { const el = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v)); return el; }
function safeLink(url) { // only http(s) from stored data becomes a link
  try { const u = new URL(url); return (u.protocol === "https:" || u.protocol === "http:") ? u.href : null; } catch (e) { return null; }
}
function chip(status) { return h("span", { class: "chip " + status }, STATUS_LABEL[status] || status); }
function unknown(v) { return v == null || v === "" ? h("span", { class: "muted" }, "unknown") : String(v); }
function empty(title, hint, ico) { return h("div", { class: "card empty" }, ico ? icon(ico) : null, h("b", null, title), hint); }
function card(title, ...kids) { return h("div", { class: "card" }, h("h2", { class: "ct" }, title), kids); }
// An example time for a command, in this browser's time zone with its UTC offset, so it is never a stale date:
// tomorrow at 09:00 for a time ahead (schedule), or this hour for a time already passed (when results were read).
function exampleTime(past) {
  const d = new Date(Date.now() + (past ? 0 : 86400000)); d.setMinutes(0, 0, 0); if (!past) d.setHours(9);
  const p = n => String(Math.abs(n)).padStart(2, "0"), off = -d.getTimezoneOffset();
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":00:00" + (off < 0 ? "-" : "+") + p(Math.trunc(off / 60)) + ":" + p(off % 60);
}
const PLATFORMS = ["bluesky", "mastodon", "linkedin", "x", "threads", "instagram", "youtube", "tiktok", "facebook", "reddit", "hackernews", "telegram", "github", "pinterest"];
function pfClass(platform) { return "pf-" + (PLATFORMS.includes(platform) ? platform : "other"); }
function pf(platform) { return h("span", { class: "pf" }, h("span", { class: "dot " + pfClass(platform), "aria-hidden": "true" }), platform || "unknown"); }
function avatar(name, platform) {
  const letter = (String(name || "").replace(/^[^A-Za-z0-9]+/, "")[0] || "?").toUpperCase();
  return h("span", { class: "avatar " + pfClass(platform), "aria-hidden": "true" }, letter);
}
function postHref(id) { return "#/post/" + encodeURIComponent(id); }
function table(head, rows) {
  rows.forEach(tr => { [...tr.children].forEach((td, i) => { if (head[i] && !td.hasAttribute("data-label")) td.setAttribute("data-label", head[i]); }); });
  return h("div", { class: "tablewrap" }, h("table", null,
    h("thead", null, h("tr", null, head.map(t => h("th", { scope: "col" }, t)))),
    h("tbody", null, rows)));
}
function kv(obj) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of Object.entries(obj || {})) {
    dl.append(h("dt", null, k.replace(/_/g, " ")));
    dl.append(h("dd", null, v !== null && typeof v === "object" && !v.nodeType ? h("pre", { class: "json" }, JSON.stringify(v, null, 2)) : v && v.nodeType ? v : unknown(v)));
  }
  return dl;
}
// One compact row of a post list. The text link covers the whole row; other links sit above it.
function row(o) {
  return h("div", { class: "row", "data-text": (o.search || "").toLowerCase() },
    h("div", { class: "st" }, o.badge),
    h("div", { class: "main" },
      o.href ? h("a", { class: "txt", href: o.href }, o.text || "(empty)") : h("span", { class: "txt" }, o.text || h("span", { class: "muted" }, "(no text recorded)")),
      o.secret ? h("span", { class: "warnbadge" }, "looks like a secret") : null,
      o.meta && o.meta.length ? metaLine(o.meta) : null, o.extra),
    h("div", { class: "when" }, o.right));
}
function metaLine(items) { return h("div", { class: "meta" }, items.flat().filter(m => m != null && m !== "").map(m => m.nodeType ? m : h("span", null, m))); }
function rows(list) { return h("div", { class: "rows" }, list); }

// ---- relative times: "2 hours ago", with the exact timestamp on hover; refreshed every minute
const RTF = (typeof Intl !== "undefined" && Intl.RelativeTimeFormat) ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }) : null;
function ago(iso) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const secs = Math.round((t - Date.now()) / 1000), abs = Math.abs(secs);
  if (abs < 45) return "just now";
  for (const [unit, size] of [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60]]) {
    if (abs >= size) { const n = Math.round(secs / size); return RTF ? RTF.format(n, unit) : (n < 0 ? Math.abs(n) + " " + unit + "(s) ago" : "in " + n + " " + unit + "(s)"); }
  }
  return "just now";
}
function when(iso, missing) {
  if (!iso) return h("span", { class: "muted" }, missing || "unknown");
  return h("time", { datetime: iso, title: iso, "data-rel": "1" }, ago(iso) || iso);
}
setInterval(() => { for (const t of document.querySelectorAll("time[data-rel]")) { const text = ago(t.getAttribute("datetime")); if (text && t.textContent !== text) t.textContent = text; } }, 60000);

// ---- feedback
let toastTimer = null;
function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message; el.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 1800);
}
async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); toast("Copied " + what); return true; } catch (e) { toast("Could not copy; select the text and copy it"); return false; }
}
function command(text) {
  const btn = h("button", { type: "button", "aria-label": "Copy command: " + text }, "Copy");
  btn.addEventListener("click", () => copy(text, "command").then(ok => { if (!ok) return;
    btn.textContent = "Copied"; btn.classList.add("done"); setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1600); }));
  return h("div", { class: "cmd" }, h("code", null, text), btn);
}
// ---- `reachweaver studio`: the composer it runs beside this dashboard. Its full local URL comes from api/studio (it
// carries the composer's own token, in the fragment) and is only ever put in a link; both pages send no referrer.
// null when this dashboard runs alone (`ui`): then "Write a new post" stays a command to copy.
let COMPOSER = null;
const COMPOSER_URL = /^http:\/\/127\.0\.0\.1:\d{1,5}\/#[A-Za-z0-9_-]{30,}$/;
function composerHref(ideaId) { return COMPOSER + (ideaId ? "&idea=" + encodeURIComponent(ideaId) : ""); }
// The composer is another origin (its own port), so it cannot read this page's theme from browser storage: a link to
// it carries the Settings choice in its fragment ("&theme=dark"), added when the link is followed so a later change
// counts. The fragment is never sent to a server; the composer keeps the choice and drops it from its address.
function withTheme(url) {
  if (!COMPOSER || typeof url !== "string" || !url.startsWith(COMPOSER)) return url;
  return url.replace(/&theme=[a-z]+/g, "") + "&theme=" + (THEMES.includes(theme) ? theme : "auto");
}
function themeOnComposerLink(e) {
  const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
  if (a && COMPOSER) { const href = a.getAttribute("href"); if (href && href.startsWith(COMPOSER)) a.setAttribute("href", withTheme(href)); }
}
document.addEventListener("click", themeOnComposerLink, true);
document.addEventListener("auxclick", themeOnComposerLink, true);
function writeLink(label, ideaId) {
  return h("a", { class: "writebtn", href: COMPOSER ? composerHref(ideaId) : "#/write", rel: COMPOSER ? "noopener noreferrer" : null }, icon("pen"), label);
}
async function loadStudio() {
  try { const d = await api("studio"); if (d && typeof d.composer === "string" && COMPOSER_URL.test(d.composer)) COMPOSER = d.composer; } catch (e) { /* the dashboard alone */ }
  if (!COMPOSER) return;
  const cta = document.querySelector(".writecta");
  if (cta) { cta.href = COMPOSER; cta.rel = "noopener noreferrer"; cta.removeAttribute("data-route"); }
  for (const p of PAGES) if (p[1] === "#/write") p[1] = COMPOSER;
}
async function api(path) {
  const res = await fetch("api/" + path, { headers: { Accept: "application/json" }, cache: "no-store" });
  let body = null;
  try { body = await res.json(); } catch (e) { /* non-JSON error */ }
  if (!res.ok) throw new Error((body && body.error) || ("HTTP " + res.status));
  return body;
}
function setBadge(counts) { // the tab title carries the number of sends that need a human; null keeps the last value
  if (!counts) return;
  const n = counts.needs_review || 0;
  document.title = (n ? "(" + n + ") " : "") + "ReachWeaver Dashboard";
}
// Navigation counts from /api/attention: red when a send needs review or an approval expired.
function setCount(name, n, level) {
  for (const el of document.querySelectorAll('[data-count="' + name + '"]')) {
    el.textContent = n ? String(n) : ""; el.className = "count" + (n && level ? " c-" + level : "");
    el.title = n ? n + " need(s) you" : "";
  }
}
function applyBadges(a) {
  const c = a.counts || {}, k = a.kinds || {};
  setBadge(c);
  setCount("overview", a.total, k.needs_review || k.approval_expired ? "bad" : "warn");
  setCount("queue", c.needs_review || 0, "bad");
  setCount("schedule", (k.overdue || 0) + (k.approval_expired || 0), k.approval_expired ? "bad" : "warn");
  // The phone's bottom bar shows Schedule only when something is approved, scheduled or sending; otherwise it is under More.
  const tab = document.getElementById("scheduletab");
  if (tab) tab.hidden = !((c.approved || 0) + (c.scheduled || 0) + (c.sending || 0));
}
function refreshBadges() { api("attention").then(applyBadges).catch(() => { /* badges are a convenience */ }); }

// ---- Overview
// "Your next step": one action chosen by the same logic as `reachweaver start`, then at most three others.
// A step this browser can do itself is a link: writing a post (the composer, when `studio` runs it) and your ideas
// (this dashboard's Ideas page). Everything else, including every owner-only command, stays a command to copy.
// The ideas step's note names commands; in the browser the Ideas page has a "Write a post from this idea" link per idea
// under `studio`, and a command per idea alone.
const IDEAS_NOTE_STUDIO = "Pick one and write it in the composer.";
const IDEAS_NOTE_ALONE = "Pick one on the Ideas page: each has a command that opens it in the composer.";
function stepBlock(step, lead) {
  const link = step.opens === "composer" && COMPOSER ? writeLink("Open the composer") :
    step.opens === "ideas" ? h("a", { class: "tap", href: "#/ideas" }, "See your ideas") : null;
  const note = step.opens === "ideas" ? (COMPOSER ? IDEAS_NOTE_STUDIO : IDEAS_NOTE_ALONE) : step.note;
  return [h(lead ? "p" : "div", { class: lead ? "lead" : null }, step.label),
    link ? h("div", { class: "linkline" }, link) : step.command ? command(step.command) : null,
    step.owner_only && !link ? h("div", { class: "note" }, h("span", { class: "who you" }, "You run this"), " in your own terminal; Claude never does.") : null,
    note ? h("div", { class: "note" }, note) : null];
}
// One "Write a new post" on the Overview. When the next step is a terminal command (approving drafts stays in your
// terminal), the card itself offers what the browser can do: "Or write a new post". Then the "Also" list and the
// page-top button leave it out. Returns [card, whether the card holds a write link].
function nextStepCard(guide, att) {
  // Drafts or uncertain sends are already the step itself: the Queue link is only added when it says something new.
  const covered = guide.state === "drafts" || guide.state === "needs_review";
  const waiting = att && att.total && !covered ? h("p", { class: "note" }, h("a", { class: "tap", href: "#/queue" },
    att.total + (att.total === 1 ? " post is" : " posts are") + " waiting for you: open the Queue")) : null;
  const primary = guide.primary, writes = primary.opens === "composer" && Boolean(COMPOSER);
  const terminal = primary.opens !== "composer" && primary.opens !== "ideas" && Boolean(primary.command);
  const also = guide.secondary.filter(st => st.opens !== "composer");
  const orWrite = terminal ? h("div", { class: "linkline orwrite" }, h("span", { class: "muted" }, "Not at your terminal?"), writeLink("Or write a new post")) : null;
  const card = h("section", { class: "card nextstep", "aria-labelledby": "nextstep-h" },
    h("h2", { class: "ct", id: "nextstep-h" }, "Your next step"), stepBlock(primary, true), orWrite,
    also.length ? [h("h3", { class: "olabel" }, "Also"), h("ul", { class: "alsosteps" }, also.map(st => h("li", null, stepBlock(st))))] : null,
    waiting);
  return [card, writes || terminal];
}
// The per-status numbers live once, in the "All posts" card; waiting drafts once, in "Your next step".
function countsSentence(counts) {
  const total = STATUS_ORDER.reduce((n, k) => n + (counts[k] || 0), 0);
  return total ? total + (total === 1 ? " post." : " posts.") : "No posts yet.";
}
function openDetails() { const d = document.getElementById("why"); if (d) { d.open = true; d.scrollIntoView({ block: "start" }); const s = d.querySelector("summary"); if (s) s.focus(); } }
// Yellow or red only for a real problem (the emergency stop, an uncertain send, something blocked); otherwise one calm line.
function statusBanner(s) {
  const hl = s.headline, paused = s.global.emergency_paused;
  const why = h("button", { type: "button", class: "linkbtn" }, "Details");
  why.addEventListener("click", openDetails);
  if (hl.level === "blocked" || hl.level === "attention") {
    const seen = new Map();
    for (const r of hl.problems) { if (paused && r.code === "emergency_pause_active") continue;  // the title says it
      if (!seen.has(r.code)) seen.set(r.code, []); if (r.account_id) seen.get(r.code).push(r.account_id); }
    return h("div", { class: "banner " + hl.level, role: "status" }, icon(paused ? "stop" : "alert"),
      h("div", null, h("b", null, paused ? "Emergency stop is ON: nothing new will be sent." : hl.level === "blocked" ? "Something is blocked" : "Something needs your attention"),
        h("ul", { class: "problems" }, [...seen.entries()].map(([code, accts]) => h("li", null, reasonTitle(code) + (accts.length ? " (" + accts.join(", ") + ")" : "")))),
        h("small", null, why)));
  }
  if (hl.level === "ok") return h("p", { class: "calm ok" }, icon("check"), h("span", null, "Everything checks out. ", why));
  return h("p", { class: "calm" }, icon("check"), h("span", null, "Automatic sending isn't set up — you don't need it to write or post by hand. ",
    hl.setup_notes ? why : null));
}
function accountStatus(a) {
  if (a.status === "ok") return h("span", { class: "chip ok" }, "ready");
  if (a.credentials_missing.length && a.status !== "blocked") return h("span", { class: "chip draft" }, "Not connected (only needed for automatic sending)");
  return h("span", { class: "chip " + a.status }, a.status === "blocked" ? "blocked" : "needs attention");
}
function detailsSection(s) {
  const problems = new Set(s.headline.problems.map(r => r.code));
  const groups = new Map();  // one row per (level, code); the accounts it affects are listed together
  for (const r of s.reasons) { const key = r.level + "/" + r.code; if (!groups.has(key)) groups.set(key, { r, accounts: [] }); if (r.account_id) groups.get(key).accounts.push(r.account_id); }
  const reasons = groups.size ? h("div", { class: "card" }, [...groups.values()].map(({ r, accounts }) => h("div", { class: "reason" },
    problems.has(r.code) ? h("span", { class: "lvl " + r.level }, r.level === "blocked" ? "Blocked" : "Attention") : h("span", { class: "lvl" }, "Note"),
    reasonTitle(r.code), accounts.length ? " (" + accounts.join(", ") + ")" : "",
    h("div", { class: "muted" }, REASON_HELP[r.code] || "See docs/TROUBLESHOOTING.md for this code.", " ", h("code", null, r.code))))) :
    h("div", { class: "card" }, h("div", { class: "allclear" }, icon("check"), "Nothing needs attention."));
  const accounts = s.accounts.length ? [table(["Account", "Platform", "Status", "Sent / 24h", "Needs review"],
    s.accounts.map(a => h("tr", null, h("td", null, a.account_id), h("td", null, pf(a.platform)), h("td", null, accountStatus(a)),
      h("td", { class: "num" }, a.budget.attempts_rolling_24h + " / " + a.budget.max_per_24h), h("td", { class: "num" }, a.needs_review)))),
    h("details", null, h("summary", null, "Technical details: how each account posts"), h("div", { class: "card" }, s.accounts.map(a => h("div", { class: "reason" },
      h("b", null, a.account_id), ": " + (ADAPTER_LABEL[a.adapter] || a.adapter) + " (adapter ", h("code", null, a.adapter), ")",
      a.credentials_missing.length ? h("div", { class: "note" }, "Not set (names only): " + a.credentials_missing.join(", ")) : null))))] :
    empty("No accounts set up", "Only needed for automatic sending. Writing and posting by hand work without them.", "accounts");
  return h("details", { class: "techdetails", id: "why" },
    h("summary", null, "Setup and safety details" + (s.reasons.length ? " (" + s.reasons.length + ")" : "")),
    h("h3", null, "Why this status"), reasons,
    h("div", { class: "grid2" }, lateCard("Files and approvals", safetyNodes), lateCard("Suggestions", suggestionNodes, "suggest")),
    h("h3", null, "Accounts"), accounts,
    h("h3", null, "Sends that failed"), h("div", { class: "card" }, s.dead_letter.total ? kv(s.dead_letter.by_code) : "None."),
    h("p", { class: "note" }, s.note));
}
function statusCard(counts) {
  const total = STATUS_ORDER.reduce((n, k) => n + (counts[k] || 0), 0);
  const bar = h("div", { class: "stack", role: "img", "aria-label": STATUS_ORDER.map(k => (counts[k] || 0) + " " + STATUS_LABEL[k]).join(", ") });
  for (const k of STATUS_ORDER) if (counts[k]) { const seg = h("span", { class: "s-" + k, title: counts[k] + " " + STATUS_LABEL[k] }); seg.style.width = (100 * counts[k] / total) + "%"; bar.append(seg); }
  return h("div", { class: "card" }, h("h2", { class: "ct" }, "All posts", h("span", { class: "muted" }, String(total))), total ? bar : null,
    h("div", { class: "legend" }, STATUS_ORDER.map(k => h("a", { href: k === "scheduled" ? "#/schedule" : "#/queue?status=" + k },
      h("span", { class: "sw s-" + k }), h("b", null, counts[k] || 0), STATUS_LABEL[k]))));
}
async function comingNodes() {
  const d = await api("schedule");
  const list = d.upcoming.slice(0, 5);
  if (!list.length) return [h("p", { class: "muted" }, "Nothing scheduled in the next " + d.days + " days.")];
  return [rows(list.map(p => row({ badge: clock(p.scheduled_at, true), href: postHref(p.id), text: p.excerpt, secret: p.looks_like_secret,
    meta: [pf(p.platform), p.account_id], right: when(p.scheduled_at) }))),
    d.upcoming.length > 5 || d.later ? h("a", { class: "more-link", href: "#/schedule" }, "All scheduled posts") : null];
}
async function recentNodes() {
  const d = await api("journal");
  const list = d.entries.slice(0, 5);
  if (!list.length) return [h("p", { class: "muted" }, "Nothing posted yet.")];
  return [rows(list.map(postedRow)), h("a", { class: "more-link", href: "#/posted" }, "Everything posted")];
}
async function viewOverview() {
  const [s, guide, att] = await Promise.all([api("overview"), api("next"), api("attention")]);
  const counts = s.global.status_counts || {};
  applyBadges(att);
  // Top to bottom: what is wrong (only when something really is), the one next step, what is coming and what went out.
  // Every list the old Overview repeated (drafts, owner commands, activity) is one tap away on Queue and Work with Claude.
  const [next, cardWrites] = nextStepCard(guide, att);
  return [h("div", { class: "overtop" }, h("div", null, h("h1", null, "Overview"),
      h("p", { class: "lede" }, countsSentence(counts), " Checked ", when(s.observed_at), ".")), cardWrites ? null : writeLink("Write a new post")),
    statusBanner(s), next,
    h("div", { class: "grid2" }, lateCard("Coming up", comingNodes), lateCard("Recently posted", recentNodes)),
    statusCard(counts), askCard(), detailsSection(s)];
}

// The Overview's short way into Claude Code: a compact composer and two ready-made prompts.
function askCard() {
  return h("div", { class: "card askcard" }, h("h2", { class: "ct" }, icon("chat"), "Ask Claude", h("a", { href: "#/claude" }, "More on the Claude page")),
    composer({ compact: true, page: "overview" }), h("div", { class: "olabel" }, "Or start from"), pagePrompts("page=overview", true));
}
// Cards filled after the page is shown, so a slow or failing check never blocks the Overview.
function lateCard(title, loader, extra) {
  const head = () => h("h2", { class: "ct" }, title);
  const box = h("div", { class: "card late" + (extra ? " " + extra : "") }, head(), h("div", { class: "sk l" }), h("div", { class: "sk l" }));
  loader().then(nodes => { const kids = nodes.flat().filter(Boolean); if (!kids.length) { box.hidden = true; return; } box.replaceChildren(head(), ...kids); })
    .catch(err => { box.replaceChildren(head(), h("p", { class: "muted" }, "Could not load this check: " + err.message)); });
  return box;
}
const LEDGER_CLASS = { ok: "ok", absent: "ok", not_checked: "attention", broken: "blocked", unavailable: "blocked" };
const LEDGER_LABEL = { ok: "Approval record: OK", absent: "Approval record: nothing recorded yet", not_checked: "Approval record: not checked",
  broken: "Approval record: does not match", unavailable: "Approval record: cannot be read" };
const CHECK_LEVEL = { blocking: ["blocked", "Problem"], warning: ["attention", "Attention"], info: ["", "Note"] };
async function safetyNodes() {
  const d = await api("safety");
  const out = [h("div", { class: "reason" }, h("span", { class: "chip " + (LEDGER_CLASS[d.ledger.status] || "attention") }, LEDGER_LABEL[d.ledger.status] || "Approval record: " + d.ledger.status.replace(/_/g, " ")), " ", d.ledger.sentence)];
  for (const p of d.ledger.problems) out.push(h("div", { class: "reason" },
    p.post_id ? h("a", { href: postHref(p.post_id) }, "Post " + p.post_id) : "The ledger", ": ", p.sentence));
  out.push(h("div", { class: "reason" }, h("span", { class: "chip " + (d.integrity === "ok" ? "ok" : "blocked") }, "Database check: " + (d.integrity || "unknown")),
    " ", d.workspace ? "Workspace folder: " + d.workspace : "Not a marked workspace folder."));
  const check = c => { const [cls, label] = CHECK_LEVEL[c.level] || ["", c.level];
    return h("div", { class: "reason" }, h("span", { class: "lvl " + cls }, label), h("code", null, c.code), h("div", null, c.detail), h("div", { class: "muted" }, c.advice)); };
  const notes = d.checks.filter(c => c.level === "info");
  out.push(d.checks.filter(c => c.level !== "info").map(check));
  if (notes.length) out.push(h("details", null, h("summary", null, notes.length + (notes.length === 1 ? " note" : " notes")), notes.map(check)));
  out.push(h("p", { class: "note" }, "Checked ", when(d.checked_at), "; rechecked at most once a minute. The same check as ", h("code", null, "reachweaver workspace"), "."));
  return out;
}
async function suggestionNodes() {
  const d = await api("suggest");
  if (!d.actions.length) return [];
  return [h("p", { class: "muted" }, "Offline rules over your local database, not a reach prediction. Each needs a human; nothing here changes anything."),
    h("ol", null, d.actions.map(a => h("li", null, h("span", { class: "src" }, a.priority), " ", h("b", null, a.title), a.platform ? " (" + a.platform + ")" : "",
      h("div", null, a.next_step), h("div", { class: "note" }, "Evidence: " + a.evidence_source.replace(/_/g, " ") + " · " + a.confidence.replace(/_/g, " "))))),
    d.more ? h("p", { class: "note" }, d.more + " more: ", h("code", null, "reachweaver suggest --include-db")) : null];
}

// ---- Schedule (agenda by your local day; the UTC time on hover)
function localDay(iso) { const d = new Date(iso); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
function dayLabel(iso) {
  try { return new Date(iso).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }); } catch (e) { return localDay(iso); }
}
function dayTitle(iso) {
  const key = localDay(iso), today = localDay(new Date().toISOString()), tomorrow = localDay(new Date(Date.now() + 86400000).toISOString());
  const label = dayLabel(iso), near = key === today ? "Today" : key === tomorrow ? "Tomorrow" : null;
  return h("h3", { class: "day" }, near || label, near ? h("small", null, label) : null);
}
function clock(iso, strong) {
  let text = iso;
  try { text = new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }); } catch (e) { /* keep the ISO time */ }
  return h(strong ? "b" : "span", null, h("time", { datetime: iso, title: iso + " (UTC)" }, text));
}
function agendaRows(items, timeCell) {
  return items.map(p => row({ badge: timeCell(p), href: postHref(p.id), text: p.excerpt, secret: p.looks_like_secret,
    meta: [chip(p.status), pf(p.platform), p.account_id, p.campaign ? "campaign " + p.campaign : null],
    extra: [p.approval_expired ? h("span", { class: "flag bad" }, "Approval expired: approve it again before it can be sent.") :
      p.expires_before_due ? h("span", { class: "flag" }, "Approval expires before this time (", when(p.approval_expires_at), ").") : null,
      p.last_error ? h("div", { class: "bad" }, "Last error: " + p.last_error) : null],
    right: p.scheduled_at ? when(p.scheduled_at) : null }));
}
function viewSwitch(month) {
  return h("div", { class: "viewswitch", role: "group", "aria-label": "Schedule view" },
    h("a", { class: "chipbtn", href: "#/schedule", "aria-current": month ? null : "true" }, "List"),
    h("a", { class: "chipbtn", href: "#/schedule?view=month", "aria-current": month ? "true" : null }, "Month"));
}
const CAL_WEEKS = 5;
function calendar(d, picked) { // 5 weeks from this Monday, in local time; overdue posts sit on the day they were due
  const start = new Date(); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  const today = localDay(new Date().toISOString()), byDay = new Map();
  for (const p of [...d.overdue, ...d.upcoming]) { const k = localDay(p.scheduled_at); if (!byDay.has(k)) byDay.set(k, []); byDay.get(k).push(p); }
  const cells = [];
  let names = [];
  try { names = [...Array(7)].map((_, i) => new Date(2024, 0, 1 + i).toLocaleDateString(undefined, { weekday: "short" })); } catch (e) { names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; }
  for (const n of names) cells.push(h("div", { class: "dow" }, n));
  for (let i = 0; i < CAL_WEEKS * 7; i++) {
    const day = new Date(start); day.setDate(start.getDate() + i);
    const key = localDay(day.toISOString()), items = (byDay.get(key) || []).sort((a, b) => a.scheduled_at < b.scheduled_at ? -1 : 1);
    const label = day.getDate() === 1 || i === 0 ? day.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : String(day.getDate());
    const dayLink = "#/schedule?view=month&day=" + key, text = key === today ? "Today · " + label : label;
    cells.push(h("div", { class: "cell" + (key < today ? " past" : "") + (key === today ? " today" : "") + (key === picked ? " picked" : "") + (items.length ? "" : " blank"),
      "aria-label": day.toDateString() + ": " + items.length + " post(s)" },
      items.length ? h("a", { class: "num", href: dayLink, title: "List this day" }, text) : h("span", { class: "num" }, text),
      items.slice(0, 3).map(p => h("a", { class: "item" + (d.overdue.includes(p) ? " overdue" : "") + (p.approval_expired ? " expired" : ""),
        href: postHref(p.id), title: p.platform + " · " + p.excerpt }, h("span", { class: "dot " + pfClass(p.platform), "aria-hidden": "true" }), clock(p.scheduled_at), h("span", null, p.excerpt || "(empty)"))),
      items.length > 3 ? h("a", { class: "more", href: dayLink }, "+" + (items.length - 3) + " more") : null));
  }
  return h("div", { class: "cal" }, cells);
}
async function viewSchedule(params) {
  const month = !!params && params.get("view") === "month";
  const picked = month && /^\d{4}-\d{2}-\d{2}$/.test(params.get("day") || "") ? params.get("day") : null;
  const d = await api("schedule" + (month ? "?days=" + (CAL_WEEKS * 7 + 7) : ""));
  const out = [h("h1", null, "Schedule"),
    h("p", { class: "lede" }, "Approved posts by due time, in your local time (UTC on hover). A post is only sent by a worker you run; ", h("code", null, "reachweaver pause"), " stops every send."),
    viewSwitch(month)];
  if (month) {
    out.push(calendar(d, picked), h("div", { class: "legendrow" }, h("span", null, h("i"), "scheduled"), h("span", null, h("i", { class: "ov" }), "past its time, not sent"),
      h("span", null, h("i", { class: "ex" }), "approval expired"), h("span", null, "Approved with no time (" + d.unscheduled.length + "): see the List view.")));
    if (picked) {
      const items = [...d.overdue, ...d.upcoming].filter(p => localDay(p.scheduled_at) === picked);
      out.push(h("h2", null, dayLabel(picked + "T12:00:00")), items.length ? rows(agendaRows(items, p => clock(p.scheduled_at, true))) : h("p", { class: "muted" }, "Nothing on this day."));
    }
    if (d.sending.length) out.push(h("p", { class: "warn" }, d.sending.length + " send(s) in progress; see the List view."));
    return out;
  }
  if (d.overdue.length) out.push(h("h2", null, "Past their time (" + d.overdue.length + ")"),
    h("div", { class: "warn" }, "These were due and have not been sent. Check whether each still makes sense before a worker runs."),
    rows(agendaRows(d.overdue, p => clock(p.scheduled_at, true))));
  if (d.sending.length) out.push(h("h2", null, "Sending now (" + d.sending.length + ")"),
    h("p", { class: "muted" }, "If one stays here, treat it as uncertain: check the platform before anything else."),
    rows(agendaRows(d.sending, () => h("span", { class: "muted" }, "in progress"))));
  out.push(h("h2", null, "Next " + d.days + " days"));
  if (d.upcoming.length) {
    const days = new Map();
    for (const p of d.upcoming) { const k = localDay(p.scheduled_at); if (!days.has(k)) days.set(k, []); days.get(k).push(p); }
    for (const items of days.values()) out.push(dayTitle(items[0].scheduled_at), rows(agendaRows(items, p => clock(p.scheduled_at, true))));
  } else out.push(empty("Nothing scheduled in the next " + d.days + " days", h("span", null, "Give an approved post a time in your terminal: ", h("code", null, "reachweaver schedule POST_ID --at " + exampleTime())), "calendar"));
  if (d.later) out.push(h("p", { class: "muted" }, d.later + " more scheduled after that. The Queue lists them all."));
  out.push(h("h2", null, "Approved, no time set (" + d.unscheduled.length + ")"));
  out.push(d.unscheduled.length ? rows(agendaRows(d.unscheduled, p => h("span", { class: "note" }, "approved ", when(p.approved_at))))
    : h("p", { class: "muted" }, "None."));
  if (d.approval_max_age_hours === 0) out.push(h("div", { class: "warn" }, "The approval time limit setting cannot be read, so every approval counts as expired (fail-closed). Check approval-mode in your terminal."));
  else if (d.approval_max_age_hours != null) out.push(h("p", { class: "note" }, "Approvals expire after " + d.approval_max_age_hours + " hours (approval-mode)."));
  return out;
}

// ---- Posted (the journal: what was sent, and what you recorded as shared by hand)
function postedRow(e) {
  const link = safeLink(e.remote_url), files = Object.entries(e.files || {}), measured = e.source === "posted_by_hand";
  const byHand = e.source === "by_hand" || measured; // measured: a published_manual record with its own post page
  return row({ search: e.excerpt + " " + e.id + " " + (e.account || "") + " " + e.platform + " " + (e.campaign || "") + " " + (e.note || ""),
    badge: byHand ? h("span", { class: "src" }, "posted by you") : chip(e.status),
    href: byHand && !measured ? null : postHref(e.id), text: e.excerpt, secret: e.looks_like_secret,
    meta: [pf(e.platform), e.account || "account not recorded", byHand ? "not sent by ReachWeaver" : "approved by " + (e.approved_by || "unknown"),
      files.length ? files.map(([k, n]) => n + " " + k).join(", ") : null],
    extra: [!byHand && e.status !== "published" ? h("div", { class: "note" }, "possibly posted: check the platform") : null,
      e.note && !measured ? h("div", { class: "muted" }, "Note: " + e.note) : null],  // a measured one's note only repeats the badge
    right: [h("div", null, when(e.when)), link ? h("a", { class: "over", href: link, rel: "noopener noreferrer", target: "_blank" }, "Open ", icon("external")) : h("span", { class: "muted" }, "no link")] });
}
function listSearch(rowsList, placeholder, total, suffix) {
  const search = h("input", { type: "text", id: "search", placeholder, "aria-label": "Search the listed posts" });
  const shown = h("span", { class: "muted", role: "status", "aria-live": "polite" });
  const count = () => { shown.textContent = rowsList.filter(r => !r.hidden).length + " of " + total + (suffix || "") + " shown"; };
  search.addEventListener("input", () => { const t = search.value.trim().toLowerCase();
    rowsList.forEach(r => { r.hidden = t !== "" && !r.dataset.text.includes(t); r.classList.remove("sel"); });
    for (const head of document.querySelectorAll(".grouphead")) { let n = head.nextElementSibling, any = false;
      while (n && !n.classList.contains("grouphead")) { if (!n.hidden) any = true; n = n.nextElementSibling; } head.hidden = !any; }
    queueSel = -1; count(); });
  count();
  return [search, shown];
}
function accountMenu(ids, account, go) {
  const acc = h("select", { "aria-label": "Account filter" }, h("option", { value: "" }, "All accounts"), ids.map(id => h("option", { value: id, selected: id === account }, id)));
  acc.addEventListener("change", () => go(acc.value));
  return acc;
}
async function viewPosted(params) {
  const account = params.get("account") || "";
  const [d, accounts] = await Promise.all([api("journal" + (account ? "?account=" + encodeURIComponent(account) : "")), api("accounts")]);
  const ids = accounts.accounts.map(a => a.id);
  if (account && !ids.includes(account)) ids.push(account);
  const acc = accountMenu(ids, account, v => { location.hash = "#/posted" + (v ? "?account=" + encodeURIComponent(v) : ""); });
  const list = d.entries.map(postedRow);
  queueRows = list; queueSel = -1;
  return [h("h1", null, "Posted"), h("p", { class: "lede" }, "Everything that went out, newest first: what ReachWeaver sent and what you posted yourself and recorded."),
    h("div", { class: "filters" }, acc, listSearch(list, "Search what was posted ( / )", d.shown, d.truncated ? " (newest " + d.shown + " only)" : "")),
    list.length ? rows(list) :
      empty(account ? "Nothing posted from this account yet" : "Nothing posted yet", h("span", null, "Posted it yourself? Record it with ",
        h("code", null, "reachweaver posted LINK --likes N"), ". Export the record with ", h("code", null, "reachweaver journal --format markdown"), "."), "posted"),
    h("p", { class: "note" }, d.note)];
}

// ---- Results (only what was recorded from the platforms' own analytics; unknown is never zero)
const NUM = (typeof Intl !== "undefined" && Intl.NumberFormat) ? new Intl.NumberFormat() : { format: n => String(n) };
function metricLine(metrics, keys) { // "views 1,204 · likes 31" from exact values; the rest summarised, never as 0
  const exact = [], other = [];
  for (const k of keys) { const m = metrics[k]; if (!m) continue;
    if (m.state === "exact") exact.push(k + " " + NUM.format(m.value));
    else if (m.state === "estimated" && m.value != null) other.push(k + " ~" + NUM.format(m.value) + " (platform estimate)");
    else if (m.state !== "unknown") other.push(k + " " + m.state.replace(/_/g, " ")); }
  return [exact.length ? h("div", null, exact.join(" · ")) : h("div", { class: "muted" }, "no exact values recorded"),
    other.length ? h("div", { class: "note" }, other.join(" · ")) : null];
}
function hbar(value, max, label) { // a bar of an exact recorded value against the largest one shown
  if (value == null || !max) return null;
  const fill = h("span"); fill.style.width = Math.max(2, Math.round(100 * value / max)) + "%";
  return h("div", { class: "hbar", role: "img", "aria-label": label }, fill);
}
function snapshotCell(s, keys, barKey, max) {
  if (!s) return h("span", { class: "muted" }, "not recorded");
  const m = barKey && s.metrics[barKey];
  return [metricLine(s.metrics, keys), m && m.state === "exact" ? hbar(m.value, max, barKey + " " + m.value + " of the largest " + max) : null,
    h("div", { class: "note" }, "recorded ", when(s.observed_at), s.age_hours != null ? " · " + s.age_hours + " h after posting" : "", " · " + s.source)];
}
// The time window of the Results page: a read-only query parameter (?since=7d|30d|all); "This week" by default.
const RESULT_WINDOWS = [["7d", "This week"], ["30d", "30 days"], ["all", "All"]];
function resultsSummary(d, label) {
  const s = d.summary, parts = [s.posts + (s.posts === 1 ? " post" : " posts")];
  for (const k of d.metrics) if (s.totals[k] != null) parts.push(NUM.format(s.totals[k]) + " " + k);
  const byId = new Map(d.posts.map(p => [p.id, p]));
  const top = s.top.map(id => byId.get(id)).filter(Boolean);
  return h("section", { class: "card", "aria-labelledby": "sum-h" }, h("h2", { class: "ct", id: "sum-h" }, label),
    h("p", { class: "sumline" }, parts.join(" · ")),
    h("p", { class: "note" }, parts.length > 1 ? "Numbers you recorded from each platform's own analytics (latest record per post)." :
      s.posts ? "No numbers recorded yet for these posts; unknown is not zero." : "Nothing went out in this window."),
    top.length ? [h("h3", { class: "olabel" }, "Top posts by likes, replies and reposts"), h("ol", { class: "tops" }, top.map(p => h("li", null,
      h("a", { class: "tap", href: postHref(p.id) }, p.excerpt || "(empty)"), h("div", { class: "note" }, NUM.format(p.interactions) + " interactions · ", pf(p.platform), " · ", when(p.when)))))] : null);
}
async function viewResults(params) {
  const account = params.get("account") || "", platform = params.get("platform") || "";
  const since = RESULT_WINDOWS.some(([k]) => k === params.get("since")) ? params.get("since") : "7d";
  const q = new URLSearchParams({ since }); if (account) q.set("account", account); if (platform) q.set("platform", platform);
  const [d, accounts] = await Promise.all([api("results?" + q), api("accounts")]);
  const go = (a, p, w) => { const n = new URLSearchParams(); if (w && w !== "7d") n.set("since", w); if (a) n.set("account", a); if (p) n.set("platform", p); location.hash = "#/results" + (n.toString() ? "?" + n : ""); };
  const ids = accounts.accounts.map(a => a.id); if (account && !ids.includes(account)) ids.push(account);
  const acc = accountMenu(ids, account, v => go(v, platform, since));
  const names = [...new Set(d.platforms.map(p => p.platform).concat(platform ? [platform] : []))];
  const plat = h("select", { "aria-label": "Platform filter" }, h("option", { value: "" }, "All platforms"), names.map(n => h("option", { value: n, selected: n === platform }, n)));
  plat.addEventListener("change", () => go(account, plat.value, since));
  const windows = h("div", { class: "chips", role: "group", "aria-label": "Time window" }, RESULT_WINDOWS.map(([k, label]) => {
    const b = h("button", { type: "button", class: "chipbtn", "aria-pressed": k === since ? "true" : "false" }, label);
    b.addEventListener("click", () => go(account, platform, k)); return b; }));
  const label = (RESULT_WINDOWS.find(([k]) => k === since) || [])[1] || "All";
  const out = [h("h1", null, "Results"), h("p", { class: "lede" }, "How your posts did, from what you recorded from each platform's own analytics. Unknown is shown as unknown, never as zero."),
    windows, h("div", { class: "filters" }, acc, plat), resultsSummary(d, since === "all" ? "All posts" : label)];
  if (!d.posts.length && since !== "all") out.push(h("p", { class: "muted" }, h("a", { class: "tap", href: "#/results?since=all" }, "Show every post instead")));
  // Columns only for metrics with a recorded value somewhere; the others are named once instead of a wall of "unknown".
  const used = d.metrics.filter(k => d.platforms.some(p => p.metrics[k] && (p.metrics[k].value != null || p.metrics[k].estimated)));
  const unused = d.metrics.filter(k => !used.includes(k));
  if (d.platforms.length) {
    out.push(h("h2", null, "By platform"), h("p", { class: "note" }, "Totals of exact values over " + d.platform_totals_scope + "; the coverage says how many posts have a value."));
    out.push(table(["Platform", "Published", "With data", ...used], d.platforms.map(p => h("tr", null,
      h("td", null, pf(p.platform)), h("td", { class: "num" }, p.published, p.posted_by_hand ? h("div", { class: "note" }, p.posted_by_hand + " posted by you") : null), h("td", { class: "num" }, p.with_snapshot),
      used.map(k => { const m = p.metrics[k]; return h("td", { class: "num" }, m.value == null ? h("span", { class: "muted" }, "unknown") : NUM.format(m.value),
        h("div", { class: "note" }, m.coverage + (m.estimated ? " · ~" + NUM.format(m.estimated.sum) + " est." : ""))); })))));
    if (unused.length) out.push(h("p", { class: "note" }, (used.length ? "Nothing recorded yet for: " : "No value recorded yet for any metric: ") + unused.join(", ") + ". They stay unknown, not zero."));
  }
  if (d.insights && d.posts.length) {
    out.push(h("h2", null, "What works for you"));
    out.push(d.insights.lines.length ? h("ol", null, d.insights.lines.map(l => h("li", null, l.text,
      h("div", { class: "note" }, l.account_id + " · " + pf(l.platform))))) :
      h("p", { class: "muted" }, "Not enough results yet to compare your posts: about 10 posts per account need a result recorded around 7 days after posting."));
    out.push(h("p", { class: "note" }, d.insights.note));
  }
  const barKey = used[0] || null;
  const max = barKey ? Math.max(0, ...d.posts.map(p => p.latest && p.latest.metrics[barKey] && p.latest.metrics[barKey].state === "exact" ? p.latest.metrics[barKey].value : 0)) : 0;
  out.push(h("h2", null, "Posts (" + d.with_snapshot + " of " + d.shown + " with a record)"));
  out.push(d.posts.length ? table(["Posted", "Text", "Account", "Latest record"], d.posts.map(p => h("tr", null,
    h("td", null, when(p.when)),
    h("td", null, h("a", { class: "row-link", href: postHref(p.id) }, p.excerpt || "(empty)"),
      p.posted_by_hand ? h("div", null, h("span", { class: "src" }, "posted by you")) : null,
      p.campaign ? h("div", { class: "note" }, "campaign " + p.campaign) : null),
    h("td", null, p.account || "account unknown", h("div", { class: "muted" }, pf(p.platform))),
    h("td", null, snapshotCell(p.latest, d.metrics, barKey, max))))) :
    empty("Nothing published yet", h("span", null, "Results appear here once a post is published and you record its numbers. Posted it yourself? Make it measurable with ",
      h("code", null, "reachweaver posted LINK --likes N"), "."), "results"));
  if (d.posts.length && !d.with_snapshot) out.push(h("div", { class: "card" }, h("h2", { class: "ct" }, "Record results in your terminal"),
    command("reachweaver metrics POST_ID --likes 4 --views 120"),
    h("p", { class: "note" }, "Or import a native export with reachweaver import-metrics --csv FILE. Bluesky and Mastodon public counters: reachweaver metrics-fetch --online.")));
  if (d.truncated) out.push(h("p", { class: "muted" }, "Showing the newest " + d.shown + " published posts."));
  out.push(h("p", { class: "note" }, d.note));
  return out;
}

// ---- Campaigns (a campaign groups posts for review; each post is still approved and sent on its own)
function statusChips(counts) {
  return STATUS_ORDER.filter(k => counts[k]).map(k => [h("span", { class: "chip " + k }, counts[k] + " " + STATUS_LABEL[k]), " "]);
}
function miniStack(counts) {
  const total = STATUS_ORDER.reduce((n, k) => n + (counts[k] || 0), 0), bar = h("div", { class: "stack", "aria-hidden": "true" });
  for (const k of STATUS_ORDER) if (counts[k]) { const seg = h("span", { class: "s-" + k }); seg.style.width = (100 * counts[k] / total) + "%"; bar.append(seg); }
  return total ? bar : null;
}
async function viewCampaigns() {
  const d = await api("campaigns");
  const out = [h("h1", null, "Campaigns"), h("p", { class: "lede" }, "Posts grouped by their campaign name, most recent first. Each post is still approved and sent on its own.")];
  out.push(d.campaigns.length ? h("div", { class: "cards" }, d.campaigns.map(c => h("a", { class: "card", href: "#/campaign?name=" + encodeURIComponent(c.campaign) },
    h("div", { class: "title" }, c.campaign), h("div", { class: "muted" }, c.posts + (c.posts === 1 ? " post" : " posts"), " · last activity ", when(c.last_activity)),
    miniStack(c.status_counts), h("div", null, statusChips(c.status_counts)),
    metaLine([c.platforms.map(pf), c.accounts.length ? c.accounts.join(", ") : null]),
    c.staged_variants ? h("div", { class: "note" }, c.staged_variants + " staged variant(s)") : null))) :
    empty("No campaigns yet", h("span", null, "Give drafts a campaign name, e.g. ", h("code", null, "reachweaver draft --account A --text '...' --campaign launch"), ", or stage a campaign file with campaign-stage."), "campaigns"));
  if (d.posts_without_campaign) out.push(h("p", { class: "muted" }, d.posts_without_campaign + " post(s) have no campaign name; they are in the Queue."));
  return out;
}
async function viewCampaign(params) {
  const name = params.get("name") || "";
  const d = await api("campaign?name=" + encodeURIComponent(name));
  const keys = ["views", "likes", "replies", "reposts", "clicks"];
  return [h("div", { class: "pager" }, h("a", { href: "#/campaigns" }, icon("back"), "Campaigns")),
    h("h1", null, "Campaign ", h("code", null, d.campaign)), miniStack(d.status_counts), h("p", null, statusChips(d.status_counts)),
    rows(d.posts.map(p => row({ badge: chip(p.status), href: postHref(p.id), text: p.excerpt, secret: p.looks_like_secret,
      meta: [pf(p.platform), p.account_id, p.chars + " characters", p.variant ? "variant " + p.variant.variant_id : null],
      extra: [p.last_error ? h("div", { class: "bad" }, "Last error: " + p.last_error) : null,
        p.results ? h("div", { class: "note" }, keys.filter(k => p.results[k] != null).map(k => k + " " + NUM.format(p.results[k])).join(" · ") || "no exact values", " · recorded ", when(p.results.observed_at))
          : p.status === "published" ? h("div", { class: "note" }, "results not recorded") : null],
      right: p.status === "published" ? "published" : p.scheduled_at ? when(p.scheduled_at) : h("span", { class: "muted" }, "not scheduled") }))),
    h("p", { class: "note" }, d.note)];
}

// ---- Search across posts, campaigns and hand-shared records (server-side, read-only)
async function viewSearch(params) {
  const q = (params.get("q") || "").trim();
  const box = document.getElementById("gq"); if (box && box.value !== q) box.value = q;
  if (!q) return [h("h1", null, "Search"), empty("Type in the search box", "Search finds posts in any status, campaign names and the posts you posted yourself and recorded.", "search")];
  const d = await api("search?q=" + encodeURIComponent(q));
  const out = [h("h1", null, "Search"), h("p", { class: "lede" }, "Results for “" + d.query + "”: " + d.posts_total + " post(s), " + d.campaigns.length + " campaign(s)" +
    (d.shared_total ? ", " + d.shared_total + " other post(s) you recorded." : "."))];
  if (d.campaigns.length) out.push(h("h2", null, "Campaigns"), h("div", { class: "chips" }, d.campaigns.map(c => h("a", { class: "chipbtn", href: "#/campaign?name=" + encodeURIComponent(c) }, c))));
  if (d.posts.length) {
    // A post you made by hand shows when you posted it ("Posted by you · 4 days ago"), not when you recorded it.
    const list = d.posts.map(p => row({ search: p.excerpt, badge: chip(p.status), href: postHref(p.id), text: p.excerpt, secret: p.looks_like_secret,
      meta: [pf(p.platform), p.account_id, p.campaign ? "campaign " + p.campaign : null],
      right: p.posted_at ? h("span", { title: "When it went out" }, "posted ", when(p.posted_at)) : h("span", { title: "When it was written" }, "written ", when(p.created_at)) }));
    queueRows = list; queueSel = -1;
    out.push(h("h2", null, "Posts"), rows(list));
  }
  if (d.posts_total > d.posts.length) out.push(h("p", { class: "muted" }, "Showing the newest " + d.posts.length + " of " + d.posts_total + " posts; refine the search to narrow them."));
  if (d.shared.length) out.push(h("h2", null, "Other posts you recorded"), rows(d.shared.map(e => { const link = safeLink(e.url);
    return row({ badge: h("span", { class: "src" }, "posted by you"), text: e.excerpt, meta: [pf(e.platform), e.account_id || null],
      extra: e.note ? h("div", { class: "muted" }, "Note: " + e.note) : null,
      right: [h("div", null, when(e.when)), link ? h("a", { class: "over", href: link, rel: "noopener noreferrer", target: "_blank" }, "Open ", icon("external")) : h("span", { class: "muted" }, "no link")] }); })));
  if (!d.posts.length && !d.campaigns.length && !d.shared.length) out.push(empty("Nothing matches", "Search looks at post text, ids, accounts, platforms, campaign names, notes and links.", "search"));
  return out;
}

// ---- Queue (keyboard: j/k move, Enter opens, / searches)
let queueRows = [], queueSel = -1;
function select(delta) {
  const visible = queueRows.filter(r => !r.hidden);
  if (!visible.length) return;
  const current = visible.indexOf(queueRows[queueSel]);
  const next = Math.max(0, Math.min(visible.length - 1, current < 0 ? (delta > 0 ? 0 : visible.length - 1) : current + delta));
  queueRows.forEach(r => r.classList.remove("sel"));
  visible[next].classList.add("sel");
  queueSel = queueRows.indexOf(visible[next]);
  visible[next].scrollIntoView({ block: "nearest" });
}
async function viewQueue(params) {
  const status = params.get("status") || "", account = params.get("account") || "";
  const q = new URLSearchParams(); if (status) q.set("status", status); if (account) q.set("account", account);
  const [data, accounts, att] = await Promise.all([api("posts" + (q.toString() ? "?" + q : "")), api("accounts"), api("attention").catch(() => null)]);
  if (att) applyBadges(att);
  const counts = att ? att.counts : {};
  const go = (nextStatus, nextAccount) => { const p = new URLSearchParams(); if (nextStatus) p.set("status", nextStatus); if (nextAccount) p.set("account", nextAccount);
    location.hash = "#/queue" + (p.toString() ? "?" + p : ""); };
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Filter by status" },
    [["", "all"], ...STATUS_ORDER.map(k => [k, STATUS_LABEL[k]])].map(([k, label]) => {
      const n = k ? counts[k] || 0 : Object.values(counts).reduce((a, b) => a + b, 0);
      const b = h("button", { type: "button", class: "chipbtn", "aria-pressed": k === status ? "true" : "false" }, label, att ? h("span", { class: "n" }, n) : null);
      b.addEventListener("click", () => go(k, account)); return b; }));
  const ids = accounts.accounts.map(a => a.id);
  if (account && !ids.includes(account)) ids.push(account);  // keep the menu truthful for an account not in the config
  const acc = accountMenu(ids, account, v => go(status, v));
  const list = data.posts.map(p => row({ search: p.excerpt + " " + p.id + " " + p.account_id + " " + p.platform + " " + (p.campaign || ""),
    badge: chip(p.status), href: postHref(p.id), text: p.excerpt, secret: p.looks_like_secret,
    meta: [pf(p.platform), p.account_id, p.campaign ? "campaign " + p.campaign : null],
    extra: p.last_error ? h("div", { class: "bad" }, "Last error: " + p.last_error) : null,
    right: p.scheduled_at ? h("span", { title: "Scheduled time" }, "due ", when(p.scheduled_at)) : h("span", { title: "Not scheduled" }, "created ", when(p.created_at)) }));
  queueRows = list; queueSel = -1;
  const grouped = []; let last = null;  // the list is already in attention order; a heading starts each status
  data.posts.forEach((p, i) => { if (!status && p.status !== last) { grouped.push(h("div", { class: "grouphead" }, STATUS_GROUP[p.status] || p.status)); last = p.status; } grouped.push(list[i]); });
  const body = data.posts.length ? rows(grouped) :
    (status || account ? empty("No posts match these filters", "Clear a filter above to see more.", "queue") :
      empty("No posts yet", "Create one in your terminal: reachweaver draft --account ACCOUNT --text 'Your text'", "queue"));
  return [h("h1", null, "Queue"),
    h("p", { class: "lede" }, "Posts needing a human come first. Use j and k to move, Enter to open."),
    chips, h("div", { class: "filters" }, acc, listSearch(list, "Search these posts ( / )", data.total, data.truncated ? " (list capped at " + data.shown + ")" : "")), body];
}

// ---- One post
function reviewSection(title, value) {
  if (value == null) return null;
  return h("details", null, h("summary", null, title), h("pre", { class: "json" }, JSON.stringify(value, null, 2)));
}
function lengthMeter(length) {
  if (!length) return null;
  const pct = Math.min(100, Math.round(length.ratio * 100));
  const bar = h("div", { class: "bar" }); bar.style.width = pct + "%";
  return h("div", { class: "meter " + length.state, role: "img", "aria-label": length.used + " of " + length.limit + " " + length.unit },
    h("div", { class: "track" }, bar),
    h("span", null, length.used + " / " + length.limit + " " + length.unit + (length.state === "over" ? ": OVER THE LIMIT" : length.state === "tight" ? ": close to the limit" : "")));
}
// An offline look-alike of the post: the exact text with links, hashtags and mentions tinted. Not the platform's rendering.
function previewText(text) {
  return String(text).split(/(https?:\/\/[^\s]+|[#@][\w.-]*\w)/).map((part, i) => i % 2 ? h("span", { class: part[0] === "#" || part[0] === "@" ? "pv-tag" : "pv-link" }, part) : part);
}
function previewCard(p, acct) {
  const handle = acct && acct.handle ? acct.handle : p.account_id;
  return h("div", { class: "preview" }, avatar(handle, p.platform),
    h("div", null, h("div", { class: "pv-head" }, h("b", null, p.account_id), h("span", null, handle !== p.account_id ? handle : null), h("span", null, "· " + p.platform)),
      h("div", { class: "pv-text" }, previewText(p.content))));
}
// A line through the recorded exact values of one metric by hours after posting; nothing drawn from fewer than two.
function spark(results) {
  const exactPoints = k => results.filter(r => r.age_hours != null && r.metrics[k] && r.metrics[k].state === "exact").map(r => [r.age_hours, r.metrics[k].value]).sort((a, b) => a[0] - b[0]);
  const key = ["views", "impressions", "likes", "replies", "reposts", "clicks"].find(k => exactPoints(k).length >= 2);
  if (!key) return null;
  const pts = exactPoints(key), W = 420, H = 96, P = 8;
  const x0 = pts[0][0], x1 = pts[pts.length - 1][0], yMax = Math.max(...pts.map(p => p[1])) || 1;
  const xy = pts.map(([x, y]) => [P + (W - 2 * P) * (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0)), H - P - (H - 2 * P) * (y / yMax)]);
  const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, class: "spark", role: "img",
    "aria-label": key + " by hours after posting: " + pts.map(([x, y]) => y + " at " + x + " h").join(", ") });
  svg.append(svgEl("line", { x1: P, y1: H - P, x2: W - P, y2: H - P, class: "axis" }), svgEl("polyline", { points: xy.map(p => p.join(",")).join(" ") }));
  for (const [x, y] of xy) svg.append(svgEl("circle", { cx: x, cy: y, r: 3.5 }));
  return h("div", { class: "card" }, h("h2", { class: "ct" }, key + " over time"), svg,
    h("p", { class: "note" }, pts.map(([x, y]) => NUM.format(y) + " at " + x + " h").join(" · ") + ". Exact values you recorded; nothing is interpolated beyond the points."));
}
const EVENT_TONE = { published: "ok", needs_review: "bad", approve: "info", schedule: "info", cancel: "bad", reconcile: "ok" };
// Each event as a plain sentence from the server (ui_data.event_sentence); the stored detail only behind a toggle.
function timeline(events) {
  return h("ol", { class: "tl" }, events.map(e => h("li", { class: EVENT_TONE[e.action] || e.tone || "" },
    h("div", null, h("span", { class: "ta" }, e.label || e.action.replace(/_/g, " ")), " ", h("span", { class: "note" }, when(e.at))),
    e.sentence ? h("div", { class: "td" }, e.sentence) : null,
    e.detail ? h("details", null, h("summary", null, "Technical details"), h("div", { class: "td" }, e.detail)) : null)));
}
function dateText(iso) {
  try { return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }); } catch (e) { return String(iso).slice(0, 10); }
}
function markNav(name) { // a post page highlights the section it belongs to (Posted for a post that went out)
  for (const a of document.querySelectorAll("a[data-route]")) { if (a.dataset.route === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }
}
function firstWords(text, max) {
  const flat = String(text || "").split(/\s+/).filter(Boolean).join(" ");
  if (flat.length <= max) return flat || "(empty post)";
  const cut = flat.slice(0, max), at = cut.lastIndexOf(" ");
  return (at > max / 2 ? cut.slice(0, at) : cut) + "…";
}
let neighbours = { prev: null, next: null };
async function viewPost(id) {
  const [p, list, accts, prompts] = await Promise.all([api("posts/" + encodeURIComponent(id)), api("posts"), api("accounts").catch(() => ({ accounts: [] })),
    api("prompts?page=post&post=" + encodeURIComponent(id)).catch(() => null)]);
  const ids = list.posts.map(x => x.id), at = ids.indexOf(id);
  neighbours = { prev: at > 0 ? ids[at - 1] : null, next: at >= 0 && at < ids.length - 1 ? ids[at + 1] : null };
  const link = safeLink(p.remote_url), meaning = STATUS_MEANING[p.status] || [p.status, ""];
  const acct = accts.accounts.find(a => a.id === p.account_id && a.platform === p.platform);
  const first = p.next_steps[0], owner = p.owner_action;
  const ownerCmds = new Set(owner ? owner.commands.map(c => c.command) : []);
  const rest = p.next_steps.slice(1).filter(s => !ownerCmds.has(s.command));  // the owner card shows those
  const wentOut = p.status === "published" || p.status === "published_manual", byYou = p.status === "published_manual";
  markNav(wentOut ? "posted" : "queue");
  const pager = h("div", { class: "pager" }, wentOut ? h("a", { href: "#/posted" }, icon("back"), "Posted") : h("a", { href: "#/queue" }, icon("back"), "Queue"), h("span", null,
    neighbours.prev ? h("a", { href: postHref(neighbours.prev), title: "Previous post ( [ )" }, icon("back"), "Previous") : null,
    neighbours.prev && neighbours.next ? "  ·  " : null,
    neighbours.next ? h("a", { href: postHref(neighbours.next), title: "Next post ( ] )" }, "Next", icon("fwd")) : null));
  const left = [h("div", { class: "card status-card " + p.status },
    h("div", { class: "what" }, h("strong", null, meaning[0]), h("span", null, meaning[1]), (p.status === "approved" || p.status === "scheduled") ? h("div", { class: "note" }, p.approval_summary) : null),
    first ? h("div", { class: "what" }, h("div", null, "Next: " + first.label), command(first.command), first.note ? h("div", { class: "note" }, first.note) : null) : null)];
  if (owner) left.push(ownerCard(owner, new Set(first ? [first.command] : [])));
  if (p.content_looks_like_secret) left.push(h("div", { class: "warn" }, "This text contains something that looks like a password, token or key. Check it before approving or sending."));
  if (p.length && p.length.state === "over") left.push(h("div", { class: "error" }, "This text is over the platform limit, so it would be refused when sent."));
  if (p.last_error) left.push(h("div", { class: "error" }, "Last error: " + p.last_error));
  if (p.repeat_check && p.repeat_check.findings && p.repeat_check.findings.length) {
    left.push(h("div", { class: p.repeat_check.blocking ? "error" : "warn" }, h("strong", null, "Repeat check: "),
      p.repeat_check.blocking + " blocking, " + p.repeat_check.warnings + " warning(s). Approving a blocking repeat needs --allow-repeat in your terminal.",
      p.repeat_check.findings.slice(0, 3).map(f => h("div", null, f.message))));
  }
  const copyBtn = h("button", { type: "button" }, "Copy text");
  copyBtn.addEventListener("click", () => copy(p.content, "text"));
  left.push(h("h2", null, "Exact text"), h("pre", { class: "exact" }, p.content), lengthMeter(p.length), h("p", null, copyBtn));
  left.push(h("h2", null, "Preview"), previewCard(p, acct),
    h("p", { class: "note" }, "An offline look-alike to read the post as a reader would. The platform's own rendering (link cards, line breaks, truncation) may differ; the exact text above is what is approved."));
  // `show` only re-checks the binding while a post waits (approved/scheduled); after a send starts its
  // "not_approved" default would read as "never approved", so the page says what it means instead.
  const approval = Object.assign({}, p.approval);
  if (SENT_STATUSES.includes(p.status) && approval.binding === "not_approved") approval.binding = "not re-checked after the send started";
  if (!byYou) left.push(h("details", null, h("summary", null, "Approval details"), h("div", { class: "card" }, kv(approval))));
  if (p.platform_preview) left.push(h("details", null, h("summary", null, "Platform preview (offline)"), h("div", { class: "card" }, kv(p.platform_preview))));
  if (!byYou) left.push(h("details", null, h("summary", null, "Source, schedule and API cost"), h("div", { class: "card" },
    kv({ source_url: p.source_url, scheduled_at: p.scheduled_at || "not scheduled", approved_at: p.approved_at || "not approved", api_cost: p.api_cost }))));
  for (const [t, v] of [["Media", p.media], ["Video", p.video], ["Reply", p.reply]]) { const s = reviewSection(t, v); if (s) left.push(s); }
  if (p.results && p.results.length) {
    const keys = ["views", "impressions", "likes", "replies", "reposts", "saves", "clicks", "signups"];
    left.push(h("h2", null, "Recorded results"), spark(p.results), table(["Recorded", "After posting", "Source", "Values"], p.results.map(r => h("tr", null,
      h("td", null, when(r.observed_at)), h("td", null, r.age_hours != null ? r.age_hours + " h" : h("span", { class: "muted" }, "unknown")),
      h("td", null, r.source), h("td", null, metricLine(r.metrics, keys))))),
      h("p", { class: "note" }, "From the platform's own analytics as you recorded them; unknown is never shown as zero."));
  } else if (p.status === "published" || p.status === "published_manual") left.push(h("p", { class: "muted" }, "No results recorded for this post yet. Record them with reachweaver metrics " + p.id + " ..."));
  left.push(h("h2", null, "Timeline"), p.events.length ? h("div", { class: "card" }, timeline(p.events)) : h("p", { class: "muted" }, "No events."));
  const aside = [prompts && prompts.cards.length ? h("div", { class: "card claudecard" }, h("h2", { class: "ct" }, icon("chat"), "Work with Claude"),
      promptCards(prompts.cards, true), h("p", { class: "note" }, h("a", { href: "#/claude?post=" + encodeURIComponent(p.id) }, "Ask something else about this post"))) : null,
    rest.length ? h("div", { class: "card" }, h("h2", { class: "ct" }, "More commands for your terminal"),
      rest.map(s => h("div", null, h("div", { class: "note" }, s.label), command(s.command)))) : null,
    h("div", { class: "card" }, kv(Object.assign({ account: p.account_id || "not recorded", platform: pf(p.platform),
      campaign: p.campaign ? h("a", { href: "#/campaign?name=" + encodeURIComponent(p.campaign) }, p.campaign) : "none" },
      byYou ? { posted: h("span", null, "Posted by you", p.posted_at ? [" on ", h("time", { datetime: p.posted_at, title: p.posted_at }, dateText(p.posted_at))] : "", " (not sent by ReachWeaver)") } :
        { scheduled: when(p.scheduled_at, "not scheduled"), approved: p.approved_at ? when(p.approved_at) : "not approved" })),
      link ? h("p", null, h("a", { class: "tap", href: link, rel: "noopener noreferrer", target: "_blank" }, wentOut ? "Open it on " + p.platform + " " : "Open remote post ", icon("external"))) : null),
    h("p", { class: "note" }, "This page cannot approve, schedule, pause or publish; run commands in your terminal.")];
  return [pager, h("h1", null, firstWords(p.content, 70), " ", chip(p.status), h("span", { class: "postid" }, "Post ", h("code", null, p.id))),
    h("p", { class: "lede" }, pf(p.platform), " · " + (p.account_id || "account not recorded") + (p.campaign ? " · campaign " + p.campaign : "")),
    h("div", { class: "post-grid" }, h("div", null, left.flat()), h("aside", { class: "aside", "aria-label": "Next step and facts" }, aside))];
}

// ---- Accounts and channels
async function viewAccounts() {
  const d = await api("accounts");
  return [h("h1", null, "Accounts"), h("p", { class: "lede" }, "Configured accounts. Credential values are never shown."),
    d.error ? h("div", { class: "error" }, d.error) : null,
    h("p", { class: "muted" }, "Credentials are needed only for live sending. Drafting, review and dry runs work without them, and so does posting by hand."),
    pagePrompts("page=accounts"),
    d.accounts.length ? h("div", { class: "cards" }, d.accounts.map(a => h("div", { class: "card" },
      h("div", { class: "acct" }, avatar(a.handle || a.id, a.platform), h("div", { class: "acctwho" }, h("b", null, a.id), h("span", { class: "muted" }, a.handle || "handle not set"))),
      h("div", { class: "badges" }, h("span", { class: "chip info" }, a.platform), h("span", { class: "chip draft" }, ADAPTER_LABEL[a.adapter] || a.adapter),
        a.credentials_missing.length ? h("span", { class: "chip draft" }, "Not connected (only needed for automatic sending)") : h("span", { class: "chip ok" }, "connected: all set")),
      h("details", null, h("summary", null, "Technical details"), h("div", { class: "note" }, "Adapter: ", h("code", null, a.adapter + (a.backend ? " (" + a.backend + ")" : ""))),
        a.credentials_missing.length ? h("div", { class: "note" }, a.credentials_missing.length + " missing (names only): " + a.credentials_missing.join(", ")) : null),
      h("div", { class: "meta" }, h("a", { class: "over", href: "#/queue?account=" + encodeURIComponent(a.id) }, "Queue"), h("a", { href: "#/posted?account=" + encodeURIComponent(a.id) }, "Posted"),
        h("a", { href: "#/results?account=" + encodeURIComponent(a.id) }, "Results"))))) :
      empty("No accounts yet", "Add one in your terminal: reachweaver account-add bluesky (public details only). Posting by hand needs no account.", "accounts")];
}
async function viewDistribution(params) {
  const meta = await api("distribution/markets");
  const region = params.get("region") || "global", goal = params.get("goal") || "awareness";
  const rsel = h("select", { "aria-label": "Market" }, meta.markets.map(m => h("option", { value: m.id, selected: m.id === region }, m.label)));
  const gsel = h("select", { "aria-label": "Goal" }, meta.goals.map(x => h("option", { value: x, selected: x === goal }, x)));
  const go = () => { location.hash = "#/distribution?region=" + encodeURIComponent(rsel.value) + "&goal=" + encodeURIComponent(gsel.value); };
  rsel.addEventListener("change", go); gsel.addEventListener("change", go);
  const plan = await api("distribution?region=" + encodeURIComponent(region) + "&goal=" + encodeURIComponent(goal) + "&limit=10");
  const chans = (plan.channels || []).map(c => { const src = safeLink(c.official_source); return h("div", { class: "card" },
    h("div", { class: "chan-head" }, h("span", { class: "rank" }, c.priority), h("div", null, h("b", null, c.platform), h("div", { class: "note" }, c.content_category))),
    h("div", { class: "badges" }, h("span", { class: "chip " + (c.reachweaver_delivery === "manual_only" ? "draft" : "info") }, c.reachweaver_delivery === "manual_only" ? "manual only" : c.reachweaver_delivery),
      c.requires_human_only ? h("span", { class: "chip attention" }, "human-only") : null, h("span", { class: "chip draft" }, "native: " + c.native_schedule)),
    c.native_schedule_scope ? h("div", { class: "note" }, c.native_schedule_scope) : null,
    src ? h("a", { href: src, rel: "noopener noreferrer", target: "_blank" }, "Official source ", icon("external")) : h("span", { class: "muted" }, "no official source recorded")); });
  return [h("h1", null, "Channels"), h("p", { class: "lede" }, "Offline research hypotheses, not predictions, account connections or publishing permission."),
    h("div", { class: "filters" }, rsel, gsel),
    pagePrompts("page=distribution&region=" + encodeURIComponent(region) + "&goal=" + encodeURIComponent(goal)),
    h("div", { class: "card" }, h("div", { class: "badges" }, h("span", { class: "chip attention" }, plan.confidence ? String(plan.confidence).replace(/_/g, " ") : "confidence unknown"),
      h("span", { class: "chip draft" }, "reviewed " + (plan.reviewed_on || "unknown")), h("span", { class: "chip draft" }, "audience time zone: " + (plan.audience_timezone || "unknown"))),
      plan.localization_warning ? h("p", { class: "muted" }, plan.localization_warning) : null,
      h("details", null, h("summary", null, "Plan details"), kv({ status: plan.status, market: plan.market, goal: plan.goal, confidence: plan.confidence,
        reviewed_on: plan.reviewed_on, audience_timezone: plan.audience_timezone, account_connections: "unverified (this view never contacts a platform)" }))),
    chans.length ? h("div", { class: "cards" }, chans) : h("div", { class: "card muted" }, "No channels."),
    plan.guardrails ? h("p", { class: "muted" }, plan.guardrails) : null];
}

// ---- Work with Claude: prompts for your own Claude Code session. The dashboard calls no model and runs nothing;
// prompts are built by the server from your local ids (ui_prompts.py) and copied by you.
const OWNER_TONE = { needs_review: "bad", sending: "warn", approval_expired: "bad", expires_before_due: "warn", approved: "info", draft: "info" };
const DRAFT_MAX = 4000;
let composerState = { text: null, about: "" };
function draftText() { if (composerState.text === null) composerState.text = store("rw-draft") || ""; return composerState.text; }
function saveDraft(text) { composerState.text = text; store("rw-draft", text.slice(0, DRAFT_MAX)); }
function copiedLine() { return h("span", { class: "copied", role: "status", "aria-live": "polite" }); }
function promptBox(text, rows) {
  const box = h("textarea", { class: "promptout", readonly: true, rows: rows || 10, spellcheck: "false", "aria-label": "Prompt for Claude Code" });
  box.value = text || "";
  return box;
}
// Copy with the clipboard API; when it is unavailable or refused, select the prompt so Ctrl C / Cmd C copies it.
function copyPrompt(text, box, live) {
  const say = msg => { if (live) { live.textContent = ""; live.textContent = msg; } else toast(msg); };
  const fallback = () => {
    if (box) { const d = box.closest("details"); if (d) d.open = true; box.focus(); box.select(); }
    say("Selected: press Ctrl C (Cmd C on a Mac) to copy");
  };
  if (!text) { say("Nothing to copy yet"); return; }
  if (!navigator.clipboard || !navigator.clipboard.writeText) { fallback(); return; }
  navigator.clipboard.writeText(text).then(() => say("Copied. Paste it into Claude Code."), fallback);
}
function copyButton(label, getText, box, live) {
  const btn = h("button", { type: "button", class: "primary", "aria-label": label }, icon("copy"), "Copy prompt");
  btn.addEventListener("click", () => copyPrompt(getText(), box, live));
  return btn;
}
// One ready-made prompt: title, what it does, Copy, and the full text to read first.
function promptCard(card, compact) {
  const box = promptBox(card.prompt, 12), live = copiedLine();
  return h("div", { class: "pcard" }, h("div", { class: "pt" }, icon("chat"), h("b", null, card.title)),
    compact ? null : h("div", { class: "note" }, card.summary),
    h("div", { class: "pact" }, copyButton("Copy prompt: " + card.title, () => card.prompt, box, live), live),
    h("details", null, h("summary", null, "Show the prompt"), box));
}
function promptCards(cards, compact) { return cards.length ? h("div", { class: "pcards" }, cards.map(c => promptCard(c, compact))) : null; }
// Filled after the page is shown, so a slow or failing request never blocks the page around it.
function later(loader) {
  const box = h("div", { class: "later" }, h("div", { class: "sk l" }));
  loader().then(nodes => { const kids = [nodes].flat().filter(Boolean); if (!kids.length) box.hidden = true; box.replaceChildren(...kids); })
    .catch(err => { box.replaceChildren(h("p", { class: "muted" }, "Could not load: " + err.message)); });
  return box;
}
function pagePrompts(query, compact) { return later(async () => promptCards((await api("prompts?" + query)).cards, compact)); }
// The composer: what you type, plus what it is about, becomes a prompt built from real ids on the server.
function composer(o) {
  const id = o.compact ? "askq" : "ask";
  const ta = h("textarea", { id, class: "asktext", rows: o.compact ? 3 : 5, maxlength: DRAFT_MAX, "aria-describedby": id + "-hint",
    placeholder: "What should Claude do? For example: tighten my newest draft and check its claims" });
  ta.value = draftText();
  let about = null;
  if (o.choices && o.choices.length > 1) {
    about = h("select", { id: id + "-about", "aria-label": "What the prompt is about" });
    let group = null;
    for (const [value, label, heading] of o.choices) {
      if (heading && (!group || group.label !== heading)) { group = h("optgroup", { label: heading }); about.append(group); }
      (heading ? group : about).append(h("option", { value, selected: value === (o.about || "") }, label));
    }
    about.addEventListener("change", () => { composerState.about = about.value; build(); });
  }
  const out = promptBox("", o.compact ? 7 : 16), live = copiedLine();
  let timer = null, seq = 0;
  function build() {
    const q = new URLSearchParams({ intent: "ask", page: o.page }), text = ta.value.trim(), pick = about ? about.value : (o.about || "");
    if (text) q.set("text", text.slice(0, DRAFT_MAX));
    if (pick) { const i = pick.indexOf(":"); q.set(pick.slice(0, i), pick.slice(i + 1)); }
    const mine = ++seq;
    return api("prompt?" + q).then(d => { if (mine === seq) out.value = d.prompt; })
      .catch(err => { if (mine === seq) out.value = "Could not build the prompt: " + err.message; });
  }
  ta.addEventListener("input", () => { saveDraft(ta.value); clearTimeout(timer); timer = setTimeout(build, 300); });
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); clearTimeout(timer); build().then(() => copyPrompt(out.value, out, live)); } });
  const clear = h("button", { type: "button" }, "Clear");
  clear.addEventListener("click", () => { ta.value = ""; saveDraft(""); build(); ta.focus(); });
  build();
  return h("div", { class: "composer" }, h("label", { for: id }, "What do you want?"), ta,
    h("div", { class: "note", id: id + "-hint" }, "Ctrl Enter (Cmd Enter) copies the prompt. Your words go only to this local dashboard, which builds the prompt and stores nothing; an unsent draft is kept in this browser."),
    about ? h("div", { class: "row2" }, h("label", { for: about.id }, "About"), about) : null,
    o.compact ? h("details", null, h("summary", null, "Show the prompt"), out) : h("div", null, h("div", { class: "olabel" }, "Prompt for Claude Code"), out),
    h("div", { class: "pact" }, copyButton("Copy prompt", () => out.value, out, live), clear, live));
}
// Owner-only cards: the exact commands, who runs each, and one line why it is yours.
function ownerCard(a, skip) {
  const tone = OWNER_TONE[a.kind] || "info", commands = a.commands || [a];
  return h("div", { class: "card owner t-" + tone },
    h("div", { class: "ohead" }, h("span", { class: "chip " + (tone === "info" ? "info" : tone) }, a.title || a.label),
      skip ? h("span", { class: "muted" }, "Only you can do this") : [a.post_id ? h("a", { href: postHref(a.post_id) }, "Post " + a.post_id) : null,
        a.platform ? pf(a.platform) : null, a.account_id || null]),
    a.excerpt ? h("div", { class: "otext" }, a.excerpt) : null,
    a.looks_like_secret ? h("span", { class: "warnbadge" }, "looks like a secret") : null,
    h("p", { class: "why" }, icon("lock"), h("span", null, a.why)),
    commands.filter(c => !skip || !skip.has(c.command)).map(c => h("div", { class: "ocmd" },
      h("div", { class: "olabel" }, h("span", { class: "who " + (c.owner_only ? "you" : "claude") }, c.owner_only ? "You run this" : "Claude can run this"), " ", a.commands ? c.label : ""),
      command(c.command), c.note ? h("div", { class: "note" }, c.note) : null)));
}
function ownerList(d, max) {
  const cards = [...d.general.map(g => ownerCard(g)), ...d.posts.slice(0, max || d.posts.length).map(a => ownerCard(a))];
  return cards.length ? h("div", { class: "owners" }, cards) : null;
}
// Activity: the audit log and the ideas inbox, newest first. Auto-refresh (Settings) keeps it following a session.
function feedList(items) {
  return h("ol", { class: "feed" }, items.map(e => h("li", { class: "t-" + (e.tone || "none") }, h("span", { class: "fdot", "aria-hidden": "true" }),
    h("div", { class: "fmain" }, h("div", null, h("b", null, e.label), e.by ? " by " + e.by : "", " · ", h("span", { class: "note" }, when(e.at))),
      e.excerpt ? h(e.post_id || e.idea_id ? "a" : "span", { class: "fx", href: e.post_id ? postHref(e.post_id) : e.idea_id ? "#/ideas" : null }, e.excerpt) : null,
      e.looks_like_secret ? h("span", { class: "warnbadge" }, "looks like a secret") : null,
      metaLine([e.platform ? pf(e.platform) : null, e.account_id, !e.excerpt && e.post_id ? "post " + e.post_id : null, e.idea_id])))));
}
function followHint() {
  if (auto.checked) return h("p", { class: "note" }, "Auto-refresh is on: this follows a Claude Code session every 20 seconds.");
  const on = h("button", { type: "button", class: "linkbtn" }, "Turn on auto-refresh");
  on.addEventListener("click", () => { auto.checked = true; auto.dispatchEvent(new Event("change")); toast("Auto-refresh on"); render(); });
  return h("p", { class: "note" }, "To follow a Claude Code session as it works: ", on, " (every 20 seconds, paused while you type).");
}
async function activityNodes(max) {
  const d = await api("activity");
  return [d.ideas_error ? h("div", { class: "warn" }, "The ideas inbox could not be read: " + d.ideas_error) : null,
    d.items.length ? feedList(d.items.slice(0, max || d.items.length)) : h("p", { class: "muted" }, "Nothing recorded yet. Drafts, approvals, sends and ideas appear here."),
    max && d.items.length > max ? h("a", { class: "more-link", href: "#/claude" }, "All recent activity") : null,
    max ? null : h("p", { class: "note" }, d.note)];
}
function howSteps() {
  return h("ol", { class: "steps" }, h("li", null, h("b", null, "Say what you want"), h("div", { class: "note" }, "Type it, or pick a ready-made prompt.")),
    h("li", null, h("b", null, "Copy the prompt"), h("div", { class: "note" }, "It names /reachweaver, the ids and the first commands.")),
    h("li", null, h("b", null, "Paste it into Claude Code"), h("div", { class: "note" }, "In the terminal, desktop or IDE app, on your own plan.")),
    h("li", null, h("b", null, "Approve and send yourself"), h("div", { class: "note" }, "Claude hands back owner-only commands; you run them.")));
}
async function viewClaude(params) {
  const asked = ["post", "account", "campaign", "idea"].find(k => params.get(k));
  const pick = asked ? asked + ":" + params.get(asked) : composerState.about;
  const [att, accts, camps, ideas, owner] = await Promise.all([api("attention"), api("accounts"), api("campaigns"),
    api("ideas?status=open"), api("owner-actions")]);
  applyBadges(att);
  const choices = [["", "Nothing specific", null]];
  if (pick && pick.startsWith("post:") && !att.items.some(i => "post:" + i.id === pick)) choices.push([pick, "Post " + pick.slice(5), "This post"]);
  for (const i of att.items.slice(0, 15)) choices.push(["post:" + i.id, (KIND[i.kind] || [i.kind])[0] + " · " + (i.excerpt || i.id).slice(0, 60), "Posts that need you"]);
  for (const a of accts.accounts) choices.push(["account:" + a.id, a.id + " (" + a.platform + ")", "Accounts"]);
  for (const c of camps.campaigns.slice(0, 20)) choices.push(["campaign:" + c.campaign, c.campaign, "Campaigns"]);
  for (const i of ideas.ideas.slice(0, 20)) choices.push(["idea:" + i.id, i.excerpt.slice(0, 60), "Open ideas"]);
  if (pick && !choices.some(c => c[0] === pick)) choices.push([pick, pick.replace(":", " "), "Chosen"]);
  composerState.about = pick || "";
  const postId = pick && pick.startsWith("post:") ? pick.slice(5) : null;
  return [h("h1", null, "Work with Claude"),
    h("p", { class: "lede" }, "Keep Claude Code open in its own app on your subscription. Here you say what you want and copy a precise prompt; this page calls no model and runs nothing."),
    howSteps(),
    h("div", { class: "grid2" }, h("div", { class: "card askcard" }, h("h2", { class: "ct" }, icon("chat"), "Ask Claude"),
      composer({ page: "claude", choices, about: composerState.about })),
    h("div", { class: "card" }, h("h2", { class: "ct" }, "Ready-made prompts"),
      postId ? [h("p", { class: "note" }, "For post ", h("a", { href: postHref(postId) }, postId), ":"), pagePrompts("page=post&post=" + encodeURIComponent(postId))] : null,
      pagePrompts("page=claude"))),
    h("h2", null, "Only you can do these", owner.total ? h("span", { class: "count c-warn" }, owner.total) : null),
    h("p", { class: "muted" }, owner.note),
    ownerList(owner) || h("div", { class: "card" }, h("div", { class: "allclear" }, icon("check"), "Nothing needs you in the terminal right now.")),
    owner.truncated ? h("p", { class: "note" }, "Showing the first " + owner.posts.length + "; the Queue lists every post.") : null,
    h("h2", null, "Activity"), h("div", { class: "card" }, followHint(), later(() => activityNodes()))];
}
// ---- Ideas (the inbox from `reachweaver idea`; read-only here)
const IDEA_CHIP = { open: "info", promoted: "ok", dropped: "draft" };
function ideaRow(i) {
  const live = copiedLine(), box = promptBox("", 12);
  let prompt = null;
  const fetchPrompt = () => prompt ? Promise.resolve(prompt) : api("prompt?intent=idea&page=ideas&idea=" + encodeURIComponent(i.id)).then(d => { prompt = d.prompt; box.value = prompt; return prompt; });
  const btn = h("button", { type: "button", class: "primary over", "aria-label": "Copy prompt: promote idea " + i.id + " into a campaign" }, icon("copy"), "Promote with Claude");
  btn.addEventListener("click", () => fetchPrompt().then(p => copyPrompt(p, box, live)).catch(err => { live.textContent = "Could not build the prompt: " + err.message; }));
  const show = h("details", null, h("summary", null, "Show the prompt"), box);
  show.addEventListener("toggle", () => { if (show.open) fetchPrompt().catch(() => { /* the button reports errors */ }); });
  return row({ search: i.text + " " + i.id + " " + i.tags.join(" "), badge: h("span", { class: "chip " + (IDEA_CHIP[i.status] || "") }, i.status),
    text: i.excerpt, meta: [i.id, i.tags.map(t => h("span", { class: "tag" }, "#" + t)), i.source_host ? "source: " + i.source_host : null,
      i.promoted_campaign ? "campaign " + i.promoted_campaign : null],
    extra: [i.text.length > i.excerpt.length ? h("details", null, h("summary", null, "Full text"), h("pre", { class: "exact" }, i.text)) : null,
      // Write it: a link that opens the composer with this idea as the caption (studio), else the command that does.
      i.status === "open" ? (COMPOSER ? h("div", { class: "linkline" }, writeLink("Write a post from this idea", i.id)) : command("reachweaver share --web --open --idea " + i.id)) : null,
      i.status === "open" ? h("div", { class: "pact" }, btn, live) : null, i.status === "open" ? show : null],
    right: when(i.created_at) });
}
async function viewIdeas(params) {
  const status = params.get("status") || "";
  const d = await api("ideas" + (status ? "?status=" + encodeURIComponent(status) : ""));
  const all = Object.values(d.counts).reduce((a, b) => a + b, 0);
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Filter by state" }, [["", "all", all], ["open", "open", d.counts.open], ["promoted", "promoted", d.counts.promoted], ["dropped", "dropped", d.counts.dropped]].map(([k, label, n]) => {
    const b = h("button", { type: "button", class: "chipbtn", "aria-pressed": k === status ? "true" : "false" }, label, h("span", { class: "n" }, n));
    b.addEventListener("click", () => { location.hash = "#/ideas" + (k ? "?status=" + k : ""); }); return b; }));
  const list = d.ideas.map(ideaRow);
  queueRows = list; queueSel = -1;
  return [h("h1", null, "Ideas"), h("p", { class: "lede" }, "Your \"post about this later\" inbox (ideas.json next to the database). An idea is not a draft: it can never be approved or sent."),
    d.error ? h("div", { class: "error" }, "The ideas inbox could not be read: " + d.error) : null, chips,
    list.length ? [h("div", { class: "filters" }, listSearch(list, "Search these ideas ( / )", d.total, d.truncated ? " (newest " + d.ideas.length + " only)" : "")), rows(list)] :
      empty(status ? "No " + status + " ideas" : "No ideas yet", h("span", null, "Tell Claude \"post about this later\", or run ", h("code", null, "reachweaver idea add \"TEXT\" --tag TAG"), " in your terminal."), "idea"),
    h("p", { class: "note" }, "Only the host of a source link is shown. Promoting writes a NEW campaign scaffold folder that stays blocked until you finish and verify it; nothing is staged, approved or sent.")];
}

// ---- Write a new post: the browser composer is its own small local page. Under `reachweaver studio` it already runs
// beside this dashboard, so this is a link; on its own this read-only dashboard cannot start it, so the page gives the
// one command to copy (`reachweaver studio` next time opens both together), then says what happens next.
function viewWrite() {
  const open = COMPOSER ? [h("p", { class: "lead" }, "The composer is running next to this dashboard:"), h("div", { class: "linkline" }, writeLink("Open the composer")),
      h("p", { class: "note" }, "No account, password or social network is contacted, and this dashboard stays read-only.")] :
    [h("p", { class: "lead" }, "Copy this command and run it in your terminal:"), command("reachweaver share --web --open"),
      h("p", { class: "note" }, "It opens the composer in your browser. No account, password or social network is contacted, and this dashboard stays read-only. Next time, ",
        h("code", null, "reachweaver studio"), " opens this dashboard and the composer together, linked.")];
  return [h("h1", null, "Write a new post"),
    h("p", { class: "lede" }, "Write once and get a ready-to-paste version for each network. It runs only on your computer; you post it in each app yourself."),
    h("section", { class: "card nextstep", "aria-labelledby": "write-h" }, h("h2", { class: "ct", id: "write-h" }, "Open the composer"), open),
    h("ol", { class: "steps" }, h("li", null, h("b", null, "Write your post"), h("div", { class: "note" }, "Pick the networks; each version is checked against its limits.")),
      h("li", null, h("b", null, "Copy each version"), h("div", { class: "note" }, "Paste it into the network's own app and post it yourself.")),
      h("li", null, h("b", null, "Record that you posted it"), h("div", { class: "note" }, "Then it shows here under Posted and Results.")),
      h("li", null, h("b", null, "Add the numbers later"), h("div", { class: "note" }, "Likes and replies from the app; Results adds them up."))),
    h("p", { class: "note" }, "Prefer the terminal? ", h("code", null, "reachweaver share --guide"), " asks a few questions instead.")];
}

// ---- router (own-property lookup only: '#/constructor' must not reach Object.prototype)
const ROUTES = Object.assign(Object.create(null), { "": ["overview", viewOverview], queue: ["queue", viewQueue],
  schedule: ["schedule", viewSchedule], posted: ["posted", viewPosted], results: ["results", viewResults],
  campaigns: ["campaigns", viewCampaigns], campaign: ["campaigns", viewCampaign], search: ["search", viewSearch],
  accounts: ["accounts", viewAccounts], distribution: ["distribution", viewDistribution], ideas: ["ideas", viewIdeas],
  claude: ["claude", viewClaude], write: ["write", viewWrite] });
let rendering = 0, lastPath = null, lastFull = null, shownPath = null;
const scrolls = new Map();  // where each list was scrolled to, so coming back from a post lands on the same row
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [pathPart, query = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  let name = parts[0] || "", fn, arg = new URLSearchParams(query);
  if (name === "post" && parts[1]) {
    try { arg = decodeURIComponent(parts[1]); fn = viewPost; name = "queue"; } catch (e) { name = ""; fn = viewOverview; arg = new URLSearchParams(); }
  } else if (ROUTES[name]) { fn = ROUTES[name][1]; name = ROUTES[name][0]; } else { name = ""; fn = viewOverview; }
  return { name, fn, arg, path: "/" + pathPart, full: "/" + raw };
}
function skeleton() { return [h("div", { class: "sk h" }), h("div", { class: "sk l" }), h("div", { class: "sk b" }), h("div", { class: "sk b" })]; }
async function render(from) {
  const mine = ++rendering, route = parseHash(), moved = route.path !== shownPath;
  queueRows = []; queueSel = -1;  // j/k only move through rows of the page being shown
  for (const a of document.querySelectorAll("a[data-route]")) {
    if (a.dataset.route === (route.name || "overview")) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (moved) { main.replaceChildren(...skeleton()); main.setAttribute("aria-busy", "true"); }
  try {
    const nodes = await route.fn(route.arg);
    if (mine !== rendering) return;
    main.replaceChildren(...nodes.flat().filter(Boolean));
    document.getElementById("updated").textContent = "Updated " + new Date().toLocaleTimeString();
    if (moved) window.scrollTo(0, from && from.startsWith("/post/") && scrolls.has(route.full) ? scrolls.get(route.full) : 0);
    if (route.fn !== viewOverview && route.fn !== viewQueue) refreshBadges();
  } catch (err) {
    if (mine !== rendering) return;
    main.replaceChildren(h("div", { class: "error", role: "alert" }, "Could not load this page: " + err.message),
      h("p", { class: "muted" }, "The dashboard only reads your local database. If it is missing, run reachweaver init in your project folder."));
  } finally {
    if (mine === rendering) { main.removeAttribute("aria-busy"); shownPath = route.path; }
  }
}

// ---- preferences: theme and auto-refresh (browser storage is optional and always wrapped)
const THEMES = ["auto", "light", "dark"];
function store(key, value) { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; } return null; }
function applyTheme(name) {
  if (name === "light" || name === "dark") document.documentElement.setAttribute("data-theme", name); else document.documentElement.removeAttribute("data-theme");
  for (const b of document.querySelectorAll("[data-theme-choice]")) b.setAttribute("aria-pressed", b.dataset.themeChoice === name ? "true" : "false");
}
let theme = THEMES.includes(store("rw-theme")) ? store("rw-theme") : "auto";
applyTheme(theme);
function setTheme(name) { theme = name; store("rw-theme", theme); applyTheme(theme); toast("Theme: " + theme); }
function cycleTheme() { setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]); }
for (const b of document.querySelectorAll("[data-theme-choice]")) b.addEventListener("click", () => setTheme(b.dataset.themeChoice));

let timer = null;
const auto = document.getElementById("auto");
function typing() { // a text field, menu or text area has focus (a checkbox or button does not count)
  const el = document.activeElement;
  return !!el && (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || (el.tagName === "INPUT" && el.type !== "checkbox" && el.type !== "button"));
}
function popOpen() { try { return document.getElementById("settings").matches(":popover-open"); } catch (e) { return false; } }
function holdRefresh() { // do not wipe what the person is in the middle of using
  const search = document.getElementById("search");
  return typing() || !!(search && search.value) || !!document.querySelector("details[open]") || !!document.querySelector("dialog[open]") || popOpen();
}
function setAuto(on) {
  clearInterval(timer); timer = null;
  if (on) timer = setInterval(() => { if (document.visibilityState === "visible" && !holdRefresh()) render(); }, 20000);
}
auto.checked = store("rw-auto") === "1"; setAuto(auto.checked);
auto.addEventListener("change", () => { store("rw-auto", auto.checked ? "1" : "0"); setAuto(auto.checked); });

// ---- settings: a native popover where supported, a plain toggle otherwise
const settings = document.getElementById("settings"), settingsBtn = document.getElementById("settingsbtn");
function closeSettings() { try { if (popOpen()) settings.hidePopover(); } catch (e) { /* no popover */ } if (!settings.hasAttribute("popover")) settings.hidden = true; }
if (typeof settings.togglePopover !== "function") {
  settings.removeAttribute("popover"); settings.hidden = true;
  settingsBtn.addEventListener("click", () => { settings.hidden = !settings.hidden; });
}

// ---- header search: Enter opens the search page (no form, so nothing is submitted anywhere)
const gq = document.getElementById("gq");
gq.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); const q = gq.value.trim(); if (q) location.hash = "#/search?q=" + encodeURIComponent(q); } });

// ---- phone: the sidebar is a drawer opened from the menu or the More tab
const side = document.getElementById("side"), scrim = document.getElementById("scrim"), menuBtn = document.getElementById("menu"), moreBtn = document.getElementById("more");
const narrow = window.matchMedia ? window.matchMedia("(max-width: 860px)") : { matches: false };
function drawer(open) {
  if (open) closeSettings();
  side.classList.toggle("open", open); scrim.hidden = !open;
  for (const b of [menuBtn, moreBtn]) b.setAttribute("aria-expanded", String(open));
  side.inert = !!narrow.matches && !open;
  if (open) { const a = side.querySelector("a[aria-current]") || side.querySelector("a"); if (a) a.focus(); }
}
drawer(false);
if (narrow.addEventListener) narrow.addEventListener("change", () => drawer(false));
menuBtn.addEventListener("click", () => drawer(!side.classList.contains("open")));
moreBtn.addEventListener("click", () => drawer(true));
scrim.addEventListener("click", () => drawer(false));
side.addEventListener("click", e => { if (e.target.closest("a")) drawer(false); });

// ---- opt-in alerts: a browser notification with counts only (never post text), while this tab is open
const alerts = document.getElementById("alerts");
let alertTimer = null, alertLast = null;
async function checkAlerts() {
  let s;
  try { s = await api("overview"); } catch (e) { return; }
  const counts = s.global.status_counts || {}, now = { review: counts.needs_review || 0, blocked: s.reasons.filter(r => r.level === "blocked").length };
  setBadge(counts);
  if (alertLast && (now.review > alertLast.review || now.blocked > alertLast.blocked) && typeof Notification !== "undefined" && Notification.permission === "granted") {
    const body = (now.review ? now.review + " send(s) need your review. " : "") + (now.blocked ? now.blocked + " readiness block(s)." : "");
    try { new Notification("ReachWeaver needs you", { body: body.trim(), tag: "reachweaver" }); } catch (e) { /* notifications unavailable */ }
  }
  alertLast = now;
}
function setAlerts(on) {
  clearInterval(alertTimer); alertTimer = null; alertLast = null;
  if (on) { checkAlerts(); alertTimer = setInterval(checkAlerts, 60000); }
}
alerts.addEventListener("change", async () => {
  if (alerts.checked) {
    if (typeof Notification === "undefined") { alerts.checked = false; toast("This browser has no notifications"); return; }
    let permission = Notification.permission;
    if (permission === "default") { try { permission = await Notification.requestPermission(); } catch (e) { permission = "denied"; } }
    if (permission !== "granted") { alerts.checked = false; toast("Notifications are blocked for this page"); return; }
  }
  store("rw-alerts", alerts.checked ? "1" : "0"); setAlerts(alerts.checked); toast(alerts.checked ? "Alerts on" : "Alerts off");
});
alerts.checked = store("rw-alerts") === "1" && typeof Notification !== "undefined" && Notification.permission === "granted"; setAlerts(alerts.checked);

// ---- command palette (Ctrl K): jump to a page or a post by typing; it only navigates
const PAGES = [["Overview", "#/", "home"], ["Write a new post", "#/write", "pen"],["Queue", "#/queue", "queue"], ["Queue: needs review", "#/queue?status=needs_review", "alert"],
  ["Queue: drafts", "#/queue?status=draft", "queue"], ["Schedule", "#/schedule", "calendar"], ["Schedule: month", "#/schedule?view=month", "calendar"],
  ["Posted", "#/posted", "posted"], ["Results", "#/results", "results"], ["Campaigns", "#/campaigns", "campaigns"],
  ["Accounts", "#/accounts", "accounts"], ["Channels", "#/distribution", "channels"], ["Ideas", "#/ideas", "idea"],
  ["Work with Claude", "#/claude", "chat"]];
let palette = null, palInput = null, palList = null, palItems = [], palSel = 0, palPosts = null, palAt = 0, palOpener = null, palLeaving = false;
function palGo(target) { palLeaving = true; palette.close(); if (COMPOSER && target.startsWith(COMPOSER)) location.assign(withTheme(target)); else location.hash = target; }
// Esc or closing without a choice puts focus back where it was (the Ctrl K button or the field you were in).
function palReturn() {
  if (palLeaving) { palLeaving = false; return; }
  const back = palOpener && palOpener.isConnected && palOpener.getClientRects().length ? palOpener : null;
  const btn = document.getElementById("palettebtn"), box = document.getElementById("gq");
  const target = back || (btn && btn.getClientRects().length ? btn : box);
  if (target) target.focus();
}
function palMove(i) {
  palSel = Math.max(0, Math.min(palItems.length - 1, i));
  [...palList.children].forEach((li, n) => li.setAttribute("aria-selected", n === palSel ? "true" : "false"));
  const li = palList.children[palSel]; if (li) { li.scrollIntoView({ block: "nearest" }); palInput.setAttribute("aria-activedescendant", li.id); }
}
function palFill() {
  const q = palInput.value.trim().toLowerCase();
  const pages = PAGES.filter(([t]) => !q || t.toLowerCase().includes(q)).map(([t, target, ico]) => ({ t, target, ico, kind: "page" }));
  const posts = (palPosts || []).filter(p => q && (p.excerpt + " " + p.id + " " + p.account_id + " " + p.platform + " " + (p.campaign || "")).toLowerCase().includes(q))
    .slice(0, 8).map(p => ({ t: p.excerpt || "(empty)", target: postHref(p.id), ico: "queue", kind: STATUS_LABEL[p.status] + " · " + p.platform }));
  palItems = [...pages, ...posts];
  if (q) palItems.push({ t: "Search everything for “" + palInput.value.trim() + "”", target: "#/search?q=" + encodeURIComponent(palInput.value.trim()), ico: "search", kind: "search" });
  palList.replaceChildren(...palItems.map((it, n) => { const li = h("li", { role: "option", id: "pal-" + n }, icon(it.ico), h("span", { class: "pt" }, it.t), h("span", { class: "pk" }, it.kind));
    li.addEventListener("click", () => palGo(it.target)); li.addEventListener("mousemove", () => { if (palSel !== n) palMove(n); }); return li; }));
  if (!palItems.length) palList.replaceChildren(h("li", { class: "muted" }, palPosts === null ? "Loading posts…" : "Nothing matches"));
  palMove(0);
}
function openPalette() {
  if (!palette) {
    palInput = h("input", { type: "text", "aria-label": "Jump to a page or post", placeholder: "Jump to a page or post…", role: "combobox", "aria-controls": "pal-list", "aria-expanded": "true", autocomplete: "off" });
    palList = h("ul", { role: "listbox", id: "pal-list" });
    palette = h("dialog", { class: "palette", "aria-label": "Jump to" }, palInput, palList, h("p", { class: "phint" }, "↑ ↓ to move · Enter to open · Esc to close"));
    palInput.addEventListener("input", palFill);
    palInput.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); palMove(palSel + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); palMove(palSel - 1); }
      else if (e.key === "Enter") { e.preventDefault(); const it = palItems[palSel]; if (it) palGo(it.target); }
    });
    palette.addEventListener("close", palReturn);
    document.body.append(palette);
  }
  if (typeof palette.showModal !== "function" || palette.open) return;
  const was = document.activeElement;
  palOpener = was && was !== document.body && was !== main ? was : null;
  palette.showModal(); palInput.value = ""; palFill(); palInput.focus();
  if (palPosts === null || Date.now() - palAt > 30000) api("posts").then(d => { palPosts = d.posts; palAt = Date.now(); if (palette.open) palFill(); }).catch(() => { palPosts = []; });
}
document.getElementById("palettebtn").addEventListener("click", openPalette);

// ---- keyboard shortcuts
const SHORTCUTS = [["?", "Show this help"], ["Ctrl K", "Jump to a page or post"], ["g then o / q / s / p", "Go to Overview / Queue / Schedule / Posted"],
  ["g then r / m", "Go to Results / Campaigns"], ["g then a / c", "Go to Accounts / Channels"], ["g then i / w", "Go to Ideas / Work with Claude"], ["j / k", "Move down / up in the queue or the posted list"],
  ["Enter", "Open the selected post"], ["Ctrl Enter", "In the Claude composer: copy the prompt"], ["[ / ]", "Previous / next post"], ["/", "Search the queue or the posted list"], ["r", "Refresh"], ["t", "Cycle the theme"], ["Esc", "Close help, leave a field"]];
let helpDialog = null, pendingG = false, gTimer = null;
function showHelp() {
  if (!helpDialog) {
    const close = h("button", { type: "button" }, "Close");
    helpDialog = h("dialog", { "aria-label": "Keyboard shortcuts" }, h("h2", null, "Keyboard shortcuts"),
      h("table", null, h("tbody", null, SHORTCUTS.map(([k, d]) => h("tr", null, h("td", null, h("kbd", null, k)), h("td", null, d))))), h("p", null, close));
    close.addEventListener("click", () => helpDialog.close());
    document.body.append(helpDialog);
  }
  closeSettings();
  if (typeof helpDialog.showModal === "function" && !helpDialog.open) helpDialog.showModal();
}
const GO = Object.assign(Object.create(null), { o: "#/", q: "#/queue", s: "#/schedule", p: "#/posted", r: "#/results", m: "#/campaigns", a: "#/accounts", c: "#/distribution", i: "#/ideas", w: "#/claude" });
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if ((helpDialog && helpDialog.open) || (palette && palette.open)) return;
  const el = document.activeElement;
  if (e.key === "Escape") { if (side.classList.contains("open")) drawer(false); else if (typing()) el.blur(); return; }
  if (typing()) return;
  if (pendingG) { pendingG = false; clearTimeout(gTimer); if (GO[e.key]) { e.preventDefault(); location.hash = GO[e.key]; } return; }
  switch (e.key) {
    case "?": e.preventDefault(); showHelp(); break;
    case "g": pendingG = true; gTimer = setTimeout(() => { pendingG = false; }, 1200); break;
    case "j": select(1); break;
    case "k": select(-1); break;
    case "Enter": { const row = queueRows[queueSel]; if (row && !row.hidden && (el === document.body || el === main)) { const a = row.querySelector('a[href^="#/"]'); if (a) location.hash = a.getAttribute("href"); } break; }
    case "/": { const s = document.getElementById("search") || document.getElementById("gq"); if (s) { e.preventDefault(); s.focus(); } break; }
    case "r": render(); toast("Refreshed"); break;
    case "t": cycleTheme(); break;
    case "[": if (neighbours.prev && location.hash.startsWith("#/post/")) location.hash = postHref(neighbours.prev); break;
    case "]": if (neighbours.next && location.hash.startsWith("#/post/")) location.hash = postHref(neighbours.next); break;
    default: break;
  }
});
// Printing a post sheet shows every section, including the approval binding; closed ones close again after.
let printOpened = [];
window.addEventListener("beforeprint", () => { printOpened = [...document.querySelectorAll("details:not([open])")]; printOpened.forEach(d => { d.open = true; }); });
window.addEventListener("afterprint", () => { printOpened.forEach(d => { d.open = false; }); printOpened = []; });
document.getElementById("keys").addEventListener("click", showHelp);
document.getElementById("skip").addEventListener("click", e => { e.preventDefault(); main.focus(); });
for (const slot of document.querySelectorAll("[data-icon]")) slot.replaceWith(icon(slot.dataset.icon));
window.addEventListener("hashchange", () => { const { path, full } = parseHash(); closeSettings();
  if (!path.startsWith("/search")) gq.value = ""; scrolls.set(lastFull, window.scrollY); render(lastFull); if (path !== lastPath) main.focus({ preventScroll: true }); lastPath = path; lastFull = full; });
document.getElementById("refresh").addEventListener("click", () => { render(); toast("Refreshed"); });
lastPath = parseHash().path; lastFull = parseHash().full;
loadStudio().then(() => render());
"""
