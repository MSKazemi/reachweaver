"""Static page, stylesheet and script of the local dashboard, kept as Python strings.

They live in a ``.py`` module so the skill bundle and plugin mirror (``scripts/sync_skill.py``
copies ``reachweaver/*.py``) ship them with no extra file type. The page is served under a
per-run token path with a strict CSP (no inline script or style), so all code is in ``APP_JS`` and
all styling in ``APP_CSS``. Data from the API (post text, errors, URLs) is always inserted with
``textContent``/``createTextNode``, never as HTML.
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
</head>
<body>
<a class="skip" id="skip" href="#main">Skip to content</a>
<header class="top">
  <div class="brand"><span class="mark" aria-hidden="true">RW</span><span>ReachWeaver</span></div>
  <nav aria-label="Sections" id="nav">
    <a href="#/" data-route="overview">Overview</a>
    <a href="#/queue" data-route="queue">Queue</a>
    <a href="#/schedule" data-route="schedule">Schedule</a>
    <a href="#/posted" data-route="posted">Posted</a>
    <a href="#/accounts" data-route="accounts">Accounts</a>
    <a href="#/distribution" data-route="distribution">Channels</a>
  </nav>
  <div class="tools">
    <span class="readonly" title="This dashboard cannot approve, schedule, pause or publish">Read-only</span>
    <label class="auto"><input type="checkbox" id="auto"> Auto-refresh</label>
    <button type="button" id="theme">Theme: auto</button>
    <button type="button" id="refresh">Refresh</button>
  </div>
</header>
<main id="main" tabindex="-1"><p class="muted">Loading&hellip;</p></main>
<footer>
  <span id="updated" class="muted"></span>
  <span class="muted">Local view of your own state. Nothing is sent from here; run commands in your terminal.</span>
  <button type="button" id="keys" class="linkbtn">Keyboard shortcuts (?)</button>
</footer>
<div id="toast" role="status" aria-live="polite"></div>
<script src="app.js"></script>
</body>
</html>
"""

APP_CSS = r""":root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #1b1f24; --muted: #5b6573; --line: #d9dee5;
  --accent: #0b6e69; --accent-ink: #ffffff; --ok: #166a2e; --warn: #7d5400; --bad: #b42318; --info: #1f5fbf;
  --ok-bg: #e6f4ea; --warn-bg: #fff4d6; --bad-bg: #fde8e6; --info-bg: #e5eefb; --code-bg: #eef1f5;
  --radius: 10px; --gutter: 16px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1317; --panel: #171c22; --ink: #e8ecf1; --muted: #9aa5b3; --line: #2b333d;
    --accent: #3cc2b8; --accent-ink: #06201d; --ok: #56d364; --warn: #e3b341; --bad: #ff7b72; --info: #79b8ff;
    --ok-bg: #12301b; --warn-bg: #3a2c08; --bad-bg: #3d1512; --info-bg: #12263f; --code-bg: #0c1014;
  }
}
:root[data-theme="dark"] {
  --bg: #0f1317; --panel: #171c22; --ink: #e8ecf1; --muted: #9aa5b3; --line: #2b333d;
  --accent: #3cc2b8; --accent-ink: #06201d; --ok: #56d364; --warn: #e3b341; --bad: #ff7b72; --info: #79b8ff;
  --ok-bg: #12301b; --warn-bg: #3a2c08; --bad-bg: #3d1512; --info-bg: #12263f; --code-bg: #0c1014;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
.skip { position: absolute; left: -999px; top: 0; background: var(--accent); color: var(--accent-ink); padding: 8px 12px; }
.skip:focus { left: 8px; top: 8px; z-index: 10; }
a { color: var(--accent); }
:focus-visible { outline: 3px solid var(--info); outline-offset: 2px; border-radius: 4px; }
.top { display: flex; flex-wrap: wrap; align-items: center; gap: 12px var(--gutter); padding: 10px var(--gutter);
  background: var(--panel); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
.brand { display: flex; align-items: center; gap: 8px; font-weight: 700; }
.mark { display: inline-grid; place-items: center; width: 28px; height: 28px; border-radius: 8px;
  background: var(--accent); color: var(--accent-ink); font-size: 12px; letter-spacing: .5px; }
nav { display: flex; flex-wrap: wrap; gap: 4px; }
nav a { text-decoration: none; color: var(--ink); padding: 6px 12px; border-radius: 999px; }
nav a[aria-current="page"] { background: var(--accent); color: var(--accent-ink); }
nav a:hover:not([aria-current="page"]) { background: var(--code-bg); }
.tools { margin-left: auto; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.readonly { font-size: 12px; padding: 3px 10px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); }
button, select, input { font: inherit; color: inherit; }
button { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; cursor: pointer; }
button:hover { border-color: var(--accent); }
button.linkbtn { border: 0; background: none; color: var(--accent); padding: 0; text-decoration: underline; }
select, input[type="text"] { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; min-width: 0; }
label.auto { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
main { max-width: 1100px; margin: 0 auto; padding: 20px var(--gutter) 40px; }
main:focus { outline: none; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 24px 0 8px; }
.muted { color: var(--muted); }
.lede { margin: 0 0 16px; color: var(--muted); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 16px; margin: 0 0 14px; }
.banner { border-radius: var(--radius); padding: 12px 16px; margin: 0 0 16px; border: 1px solid var(--line); font-weight: 600; }
.banner.ok { background: var(--ok-bg); color: var(--ok); }
.banner.attention { background: var(--warn-bg); color: var(--warn); }
.banner.blocked { background: var(--bad-bg); color: var(--bad); }
.banner small { display: block; font-weight: 400; color: var(--ink); margin-top: 2px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 0 0 8px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 10px 12px; text-decoration: none; color: var(--ink); display: block; }
a.tile:hover { border-color: var(--accent); }
.tile b { display: block; font-size: 24px; line-height: 1.1; }
.tile span { color: var(--muted); font-size: 13px; }
.chip { display: inline-block; font-size: 12px; padding: 2px 9px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.chip.draft { color: var(--muted); } .chip.approved, .chip.scheduled { color: var(--info); }
.chip.sending { color: var(--warn); } .chip.needs_review { color: var(--bad); font-weight: 700; }
.chip.published { color: var(--ok); } .chip.cancelled { color: var(--muted); text-decoration: line-through; }
.chip.ok { color: var(--ok); } .chip.attention { color: var(--warn); } .chip.blocked { color: var(--bad); }
.tablewrap { overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
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
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
button.chipbtn { border-radius: 999px; padding: 3px 11px; font-size: 13px; }
button.chipbtn[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.empty { text-align: center; padding: 28px 16px; color: var(--muted); }
.empty b { display: block; color: var(--ink); font-size: 16px; margin-bottom: 4px; }
.exact { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--code-bg); border: 1px solid var(--line);
  border-radius: 8px; padding: 12px; margin: 0; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
.meter { display: flex; align-items: center; gap: 10px; margin: 8px 0 0; font-size: 13px; color: var(--muted); }
.meter .track { flex: 1; max-width: 260px; height: 8px; border-radius: 999px; background: var(--code-bg); border: 1px solid var(--line); overflow: hidden; }
.meter .bar { height: 100%; background: var(--ok); }
.meter.tight .bar { background: var(--warn); } .meter.over .bar { background: var(--bad); }
.meter.over { color: var(--bad); font-weight: 700; }
dl.kv { display: grid; grid-template-columns: minmax(120px, 220px) 1fr; gap: 4px 14px; margin: 0; }
dl.kv dt { color: var(--muted); } dl.kv dd { margin: 0; overflow-wrap: anywhere; min-width: 0; }
code, pre.json { font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
pre.json { background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px; padding: 10px; overflow-x: auto; margin: 6px 0 0; max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
.cmd { display: flex; gap: 8px; align-items: center; background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px 6px 12px; margin: 6px 0; }
.cmd code { flex: 1; overflow-wrap: anywhere; }
.next { border-left: 4px solid var(--accent); }
.next ol { margin: 0; padding-left: 20px; } .next li { margin: 0 0 10px; } .next li:last-child { margin: 0; }
.note { color: var(--muted); font-size: 13px; }
.status-card { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: flex-start; border-left: 4px solid var(--muted); }
.status-card.needs_review { border-left-color: var(--bad); background: var(--bad-bg); }
.status-card.approved, .status-card.scheduled { border-left-color: var(--info); }
.status-card.published { border-left-color: var(--ok); }
.status-card .what { flex: 1 1 320px; min-width: 0; } .status-card .what strong { display: block; font-size: 16px; }
.pager { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin: 0 0 12px; }
.reason { padding: 8px 0; border-bottom: 1px solid var(--line); } .reason:last-child { border: 0; }
.reason .lvl { font-weight: 700; margin-right: 6px; }
.reason .lvl.blocked { color: var(--bad); } .reason .lvl.attention { color: var(--warn); }
details { margin: 8px 0; } summary { cursor: pointer; color: var(--muted); }
.error { background: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad); border-radius: var(--radius); padding: 12px 16px; margin: 0 0 12px; }
.warn { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn); border-radius: var(--radius); padding: 12px 16px; margin: 0 0 12px; }
footer { max-width: 1100px; margin: 0 auto; padding: 0 var(--gutter) 24px; display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 13px; }
#toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); background: var(--ink); color: var(--bg);
  padding: 8px 16px; border-radius: 999px; font-size: 14px; opacity: 0; pointer-events: none; z-index: 20; }
#toast.show { opacity: 1; }
dialog { background: var(--panel); color: var(--ink); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 22px; max-width: 460px; }
dialog::backdrop { background: rgba(0, 0, 0, .45); }
dialog h2 { margin-top: 0; }
dialog table td { border: 0; padding: 3px 10px 3px 0; }
kbd { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--code-bg); border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; }
.agenda table { table-layout: fixed; }  /* the same columns line up from one day to the next */
.agenda th:nth-child(1) { width: 140px; } .agenda th:nth-child(2) { width: 120px; } .agenda th:nth-child(4) { width: 180px; }
.day { margin: 18px 0 6px; font-size: 15px; } .day small { color: var(--muted); font-weight: 400; margin-left: 6px; }
.flag { display: block; font-size: 13px; color: var(--warn); } .flag.bad { color: var(--bad); font-weight: 600; }
.src { display: inline-block; font-size: 12px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
.suggest ol { margin: 6px 0 0; padding-left: 20px; } .suggest li { margin: 0 0 10px; }
.late > strong { display: block; margin-bottom: 4px; }
@media (max-width: 640px) {
  main { padding-top: 14px; }
  .top { position: static; }  /* a three-row sticky header would cover a third of a phone screen */
  dl.kv { grid-template-columns: 1fr; } dl.kv dt { margin-top: 6px; }
  .tools { margin-left: 0; width: 100%; }
  .tablewrap { border: 0; background: none; overflow: visible; }
  table, tbody, tr, td { display: block; width: 100%; }
  thead { position: absolute; left: -9999px; }
  tr { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); margin: 0 0 10px; padding: 6px 0; }
  td { border: 0; padding: 4px 14px; }
  td[data-label]::before { content: attr(data-label); display: block; font-size: 11px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  td.num { text-align: left; }
}
@media print {
  /* A post sheet printed or saved as PDF: the exact text and the approval binding, no controls. */
  :root, :root[data-theme="dark"] { --bg: #fff; --panel: #fff; --ink: #000; --muted: #444; --line: #bbb; --code-bg: #fff; }
  .top, footer, .pager, #toast, .skip, button { display: none !important; }
  main { max-width: none; padding: 0; }
  .card, .exact, pre.json, .tablewrap { break-inside: avoid; }
  a[href^="http"]::after { content: " (" attr(href) ")"; font-size: 11px; }
}
@media (prefers-reduced-motion: no-preference) { .tile, button, nav a { transition: border-color .12s, background .12s; } #toast { transition: opacity .18s; } }
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
  accounts_config_unloadable: "The accounts file could not be loaded. Copy config/accounts.example.json to config/accounts.json.",
  no_accounts_configured: "No accounts are configured yet. Drafts can still be exported for manual posting.",
  manual_platform_has_queued_posts: "Posts are queued for a platform that is manual-only; post them yourself from the export.",
  registry_entry_stale: "The platform capability research is old. Re-check the platform before relying on it.",
  approval_key_unavailable: "Keyed approvals are on but the key is not readable in this process.",
  caps_file_invalid: "The send-limits file is invalid.",
  capability_registry_invalid: "The capability registry failed validation."
};
const STATUS_LABEL = { draft: "draft", approved: "approved", scheduled: "scheduled", sending: "sending",
  needs_review: "needs review", published: "published", cancelled: "cancelled" };
const STATUS_ORDER = ["needs_review", "sending", "approved", "scheduled", "draft", "published", "cancelled"];
const STATUS_MEANING = {
  draft: ["Draft", "Not approved yet. Nothing can be sent until you approve this exact text."],
  approved: ["Approved", "Approved and waiting. A worker you run decides when it is sent; you can preview it with a dry run."],
  scheduled: ["Scheduled", "Approved with a due time. It is only sent by a worker you run."],
  sending: ["Sending", "A send is in progress or was interrupted. If it stays here, treat it as uncertain."],
  needs_review: ["Needs your review", "A send ended in an unknown state. Check the native platform first; never resend blindly."],
  published: ["Published", "Sent. Open the remote post to confirm it looks right."],
  cancelled: ["Cancelled", "This post will never be sent."]
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
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function safeLink(url) { // only http(s) from stored data becomes a link
  try { const u = new URL(url); return (u.protocol === "https:" || u.protocol === "http:") ? u.href : null; } catch (e) { return null; }
}
function chip(status) { return h("span", { class: "chip " + status }, STATUS_LABEL[status] || status); }
function unknown(v) { return v == null || v === "" ? h("span", { class: "muted" }, "unknown") : String(v); }
function empty(title, hint) { return h("div", { class: "card empty" }, h("b", null, title), hint); }
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
    dl.append(h("dd", null, v !== null && typeof v === "object" ? h("pre", { class: "json" }, JSON.stringify(v, null, 2)) : unknown(v)));
  }
  return dl;
}

// ---- relative times: "2 hours ago", with the exact timestamp on hover
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
  return h("time", { datetime: iso, title: iso }, ago(iso) || iso);
}

// ---- feedback
let toastTimer = null;
function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message; el.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 1800);
}
async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); toast("Copied " + what); } catch (e) { toast("Could not copy; select the text and copy it"); }
}
function command(text) {
  const btn = h("button", { type: "button", "aria-label": "Copy command: " + text }, "Copy");
  btn.addEventListener("click", () => copy(text, "command"));
  return h("div", { class: "cmd" }, h("code", null, text), btn);
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

// ---- Overview
function nextCard(guide) {
  const items = guide.steps.map(s => h("li", null, h("div", null, s.label), s.command ? command(s.command) : null,
    s.note ? h("div", { class: "note" }, s.note) : null));
  return h("div", { class: "card next" }, h("strong", null, "What to do next"), h("p", { class: "muted" }, guide.summary), h("ol", null, items));
}
async function viewOverview() {
  const [s, guide] = await Promise.all([api("overview"), api("next")]);
  const g = s.global, counts = g.status_counts || {};
  setBadge(counts);
  const out = [h("h1", null, "Overview"), h("p", { class: "lede" }, "What needs a human right now. Read-only; observed ", when(s.observed_at), ".")];
  const paused = g.emergency_paused;
  out.push(h("div", { class: "banner " + (paused ? "blocked" : s.overall) },
    paused ? "Emergency stop is ON: no new sends." : "Readiness: " + s.overall,
    h("small", null, "Runtime environment: " + g.runtime_environment + ". Live-send opt-in variable: " + (g.live_opt_in_env ? "set" : "not set") + "."),
    !paused && s.overall !== "ok" && s.reasons.length && s.reasons.every(r => r.code === "credentials_missing")
      ? h("small", null, "Only credentials are missing: drafting, review and dry runs still work. They are needed for live sends only.") : null));
  out.push(nextCard(guide));
  out.push(h("div", { class: "tiles" }, STATUS_ORDER.map(k =>
    h("a", { class: "tile", href: k === "scheduled" ? "#/schedule" : "#/queue?status=" + k }, h("b", null, counts[k] || 0), h("span", null, STATUS_LABEL[k])))));
  out.push(h("h2", null, "Why this status"));
  const groups = new Map();  // one row per (level, code); the accounts it affects are listed together
  for (const r of s.reasons) { const key = r.level + "/" + r.code; if (!groups.has(key)) groups.set(key, { r, accounts: [] }); if (r.account_id) groups.get(key).accounts.push(r.account_id); }
  out.push(groups.size ? h("div", { class: "card" }, [...groups.values()].map(({ r, accounts }) => h("div", { class: "reason" },
    h("span", { class: "lvl " + r.level }, r.level === "blocked" ? "Blocked" : "Attention"),
    h("code", null, r.code), accounts.length ? " (" + accounts.join(", ") + ")" : "",
    h("div", { class: "muted" }, REASON_HELP[r.code] || "See docs/TROUBLESHOOTING.md for this code.")))) :
    h("div", { class: "card muted" }, "Nothing needs attention."));
  out.push(lateCard("Files and approvals", safetyNodes), lateCard("Suggestions", suggestionNodes, "suggest"));
  out.push(h("h2", null, "Accounts"));
  out.push(s.accounts.length ? table(["Account", "Platform", "Adapter", "Status", "Sent / 24h", "Needs review", "Missing credentials"],
    s.accounts.map(a => h("tr", null,
      h("td", null, a.account_id), h("td", null, a.platform), h("td", null, a.adapter),
      h("td", null, h("span", { class: "chip " + a.status }, a.status)),
      h("td", { class: "num" }, a.budget.attempts_rolling_24h + " / " + a.budget.max_per_24h),
      h("td", { class: "num" }, a.needs_review),
      h("td", null, a.credentials_missing.length ? a.credentials_missing.join(", ") : "none")))) :
    empty("No accounts configured", "Copy config/accounts.example.json to config/accounts.json. Drafts can still be exported for manual posting."));
  out.push(h("h2", null, "Dead letters"));
  out.push(h("div", { class: "card" }, s.dead_letter.total ? kv(s.dead_letter.by_code) : "None."));
  out.push(h("p", { class: "muted" }, s.note));
  return out;
}

// Cards filled after the page is shown, so a slow or failing check never blocks the Overview.
function lateCard(title, loader, extra) {
  const box = h("div", { class: "card late" + (extra ? " " + extra : "") }, h("strong", null, title), h("p", { class: "muted" }, "Checking…"));
  loader().then(nodes => { const kids = nodes.flat().filter(Boolean); if (!kids.length) { box.hidden = true; return; } box.replaceChildren(h("strong", null, title), ...kids); })
    .catch(err => { box.replaceChildren(h("strong", null, title), h("p", { class: "muted" }, "Could not load this check: " + err.message)); });
  return box;
}
const LEDGER_CLASS = { ok: "ok", absent: "ok", not_checked: "attention", broken: "blocked", unavailable: "blocked" };
const CHECK_LEVEL = { blocking: ["blocked", "Problem"], warning: ["attention", "Attention"], info: ["", "Note"] };
async function safetyNodes() {
  const d = await api("safety");
  const out = [h("div", { class: "reason" }, h("span", { class: "chip " + (LEDGER_CLASS[d.ledger.status] || "attention") }, "approval ledger: " + d.ledger.status.replace(/_/g, " ")), " ", d.ledger.sentence)];
  for (const p of d.ledger.problems) out.push(h("div", { class: "reason" },
    p.post_id ? h("a", { href: "#/post/" + encodeURIComponent(p.post_id) }, "Post " + p.post_id) : "The ledger", ": ", p.sentence));
  out.push(h("div", { class: "reason" }, h("span", { class: "chip " + (d.integrity === "ok" ? "ok" : "blocked") }, "database: " + (d.integrity || "unknown")),
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
function dayTitle(iso) {
  const key = localDay(iso), today = localDay(new Date().toISOString()), tomorrow = localDay(new Date(Date.now() + 86400000).toISOString());
  let label = key;
  try { label = new Date(iso).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }); } catch (e) { /* keep the ISO date */ }
  const near = key === today ? "Today" : key === tomorrow ? "Tomorrow" : null;
  return h("h3", { class: "day" }, near || label, near ? h("small", null, label) : null);
}
function clock(iso) {
  let text = iso;
  try { text = new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }); } catch (e) { /* keep the ISO time */ }
  return h("time", { datetime: iso, title: iso + " (UTC)" }, text);
}
function agendaRows(items, timeCell) {
  return items.map(p => h("tr", null, h("td", null, timeCell(p)), h("td", null, chip(p.status)),
    h("td", null, h("a", { class: "row-link", href: "#/post/" + encodeURIComponent(p.id) }, p.excerpt || "(empty)"),
      p.looks_like_secret ? h("span", { class: "warnbadge" }, "looks like a secret") : null,
      p.approval_expired ? h("span", { class: "flag bad" }, "Approval expired: approve it again before it can be sent.") :
        p.expires_before_due ? h("span", { class: "flag" }, "Approval expires before this time (", when(p.approval_expires_at), ").") : null,
      p.last_error ? h("div", { class: "muted" }, "Last error: " + p.last_error) : null),
    h("td", null, p.account_id, h("div", { class: "muted" }, p.platform))));
}
async function viewSchedule() {
  const d = await api("schedule");
  const head = ["When", "Status", "Text", "Account"];
  const out = [h("h1", null, "Schedule"),
    h("p", { class: "lede" }, "Approved posts by due time, in your local time (UTC on hover). A post is only sent by a worker you run; ", h("code", null, "reachweaver pause"), " stops every send.")];
  if (d.overdue.length) out.push(h("h2", null, "Past their time (" + d.overdue.length + ")"),
    h("div", { class: "warn" }, "These were due and have not been sent. Check whether each still makes sense before a worker runs."),
    table(head, agendaRows(d.overdue, p => when(p.scheduled_at))));
  if (d.sending.length) out.push(h("h2", null, "Sending now (" + d.sending.length + ")"),
    h("p", { class: "muted" }, "If one stays here, treat it as uncertain: check the platform before anything else."),
    table(head, agendaRows(d.sending, () => h("span", { class: "muted" }, "in progress"))));
  out.push(h("h2", null, "Next " + d.days + " days"));
  if (d.upcoming.length) {
    const days = new Map();
    for (const p of d.upcoming) { const k = localDay(p.scheduled_at); if (!days.has(k)) days.set(k, []); days.get(k).push(p); }
    for (const items of days.values()) out.push(dayTitle(items[0].scheduled_at), table(head, agendaRows(items, p => clock(p.scheduled_at))));
  } else out.push(empty("Nothing scheduled in the next " + d.days + " days", h("span", null, "Give an approved post a time in your terminal: ", h("code", null, "reachweaver schedule POST_ID --at 2026-10-20T09:00:00+02:00"))));
  if (d.later) out.push(h("p", { class: "muted" }, d.later + " more scheduled after that. The Queue lists them all."));
  out.push(h("h2", null, "Approved, no time set (" + d.unscheduled.length + ")"));
  out.push(d.unscheduled.length ? table(["Approved", "Status", "Text", "Account"], agendaRows(d.unscheduled, p => when(p.approved_at)))
    : h("p", { class: "muted" }, "None."));
  if (d.approval_max_age_hours != null) out.push(h("p", { class: "note" }, "Approvals expire after " + d.approval_max_age_hours + " hours (approval-mode)."));
  return [h("div", { class: "agenda" }, out.flat().filter(Boolean))];
}

// ---- Posted (the journal: what was sent, and what you recorded as shared by hand)
async function viewPosted(params) {
  const account = params.get("account") || "";
  const [d, accounts] = await Promise.all([api("journal" + (account ? "?account=" + encodeURIComponent(account) : "")), api("accounts")]);
  const ids = accounts.accounts.map(a => a.id);
  if (account && !ids.includes(account)) ids.push(account);
  const acc = h("select", { "aria-label": "Account filter" }, h("option", { value: "" }, "All accounts"), ids.map(id => h("option", { value: id, selected: id === account }, id)));
  acc.addEventListener("change", () => { location.hash = "#/posted" + (acc.value ? "?account=" + encodeURIComponent(acc.value) : ""); });
  const search = h("input", { type: "text", id: "search", placeholder: "Search what was posted ( / )", "aria-label": "Search the listed posts" });
  const rows = d.entries.map(e => { const link = safeLink(e.remote_url), files = Object.entries(e.files || {}), byHand = e.source === "by_hand";
    return h("tr", { "data-text": (e.excerpt + " " + e.id + " " + (e.account || "") + " " + e.platform + " " + (e.campaign || "") + " " + (e.note || "")).toLowerCase() },
      h("td", null, when(e.when)),
      h("td", null, byHand ? [h("span", { class: "src" }, "shared by hand"), h("div", null, e.excerpt || h("span", { class: "muted" }, "(no text recorded)"))]
        : [e.status !== "published" ? h("div", null, chip(e.status), " ", h("span", { class: "note" }, "possibly posted: check the platform")) : null,
          h("a", { class: "row-link", href: "#/post/" + encodeURIComponent(e.id) }, e.excerpt || "(empty)")],
        e.looks_like_secret ? h("span", { class: "warnbadge" }, "looks like a secret") : null,
        e.note ? h("div", { class: "muted" }, "Note: " + e.note) : null,
        files.length ? h("div", { class: "note" }, files.map(([k, n]) => n + " " + k).join(", ")) : null),
      h("td", null, e.account || h("span", { class: "muted" }, "unknown"), h("div", { class: "muted" }, e.platform)),
      h("td", null, byHand ? h("span", { class: "muted" }, "by you") : unknown(e.approved_by)),
      h("td", null, link ? h("a", { href: link, rel: "noopener noreferrer", target: "_blank" }, "Open") : h("span", { class: "muted" }, "no link"))); });
  queueRows = rows; queueSel = -1;
  const shown = h("span", { class: "muted", role: "status", "aria-live": "polite" });
  const count = () => { shown.textContent = rows.filter(r => !r.hidden).length + " of " + d.shown + (d.truncated ? " (newest " + d.shown + " only)" : "") + " shown"; };
  search.addEventListener("input", () => { const t = search.value.trim().toLowerCase(); rows.forEach(r => { r.hidden = t !== "" && !r.dataset.text.includes(t); r.classList.remove("sel"); }); queueSel = -1; count(); });
  count();
  return [h("h1", null, "Posted"), h("p", { class: "lede" }, "What ReachWeaver sent, plus what you recorded as shared by hand, newest first."),
    h("div", { class: "filters" }, acc, search, shown),
    rows.length ? table(["When", "Text", "Account", "Approved by", "Remote"], rows) :
      empty(account ? "Nothing posted from this account yet" : "Nothing posted yet", h("span", null, "Posted it yourself? Record it with ",
        h("code", null, "reachweaver mark-shared --platform PLATFORM --url URL"), ". Export the record with ", h("code", null, "reachweaver journal --format markdown"), ".")),
    h("p", { class: "note" }, d.note)];
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
  const [data, accounts] = await Promise.all([api("posts" + (q.toString() ? "?" + q : "")), api("accounts")]);
  const go = (nextStatus, nextAccount) => { const p = new URLSearchParams(); if (nextStatus) p.set("status", nextStatus); if (nextAccount) p.set("account", nextAccount);
    location.hash = "#/queue" + (p.toString() ? "?" + p : ""); };
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Filter by status" },
    [["", "all"], ...STATUS_ORDER.map(k => [k, STATUS_LABEL[k]])].map(([k, label]) => {
      const b = h("button", { type: "button", class: "chipbtn", "aria-pressed": k === status ? "true" : "false" }, label);
      b.addEventListener("click", () => go(k, account)); return b; }));
  const ids = accounts.accounts.map(a => a.id);
  if (account && !ids.includes(account)) ids.push(account);  // keep the menu truthful for an account not in the config
  const acc = h("select", { "aria-label": "Account filter" }, h("option", { value: "" }, "All accounts"),
    ids.map(id => h("option", { value: id, selected: id === account }, id)));
  acc.addEventListener("change", () => go(status, acc.value));
  const search = h("input", { type: "text", id: "search", placeholder: "Search these posts ( / )", "aria-label": "Search the listed posts" });
  const rows = data.posts.map(p => h("tr", { "data-text": (p.excerpt + " " + p.id + " " + p.account_id + " " + p.platform + " " + (p.campaign || "")).toLowerCase() },
    h("td", null, chip(p.status)),
    h("td", null, h("a", { class: "row-link", href: "#/post/" + encodeURIComponent(p.id) }, p.excerpt || "(empty)"),
      p.looks_like_secret ? h("span", { class: "warnbadge" }, "looks like a secret") : null,
      p.last_error ? h("div", { class: "muted" }, "Last error: " + p.last_error) : null),
    h("td", null, p.account_id, h("div", { class: "muted" }, p.platform)),
    h("td", null, when(p.scheduled_at, "not scheduled")), h("td", null, when(p.created_at))));
  queueRows = rows; queueSel = -1;
  const shown = h("span", { class: "muted", role: "status", "aria-live": "polite" });
  const count = () => { const n = rows.filter(r => !r.hidden).length; shown.textContent = n + " of " + data.total + (data.truncated ? " (list capped at " + data.shown + ")" : "") + " shown"; };
  search.addEventListener("input", () => { const t = search.value.trim().toLowerCase(); rows.forEach(r => { r.hidden = t !== "" && !r.dataset.text.includes(t); r.classList.remove("sel"); }); queueSel = -1; count(); });
  count();
  const body = data.posts.length ? table(["Status", "Text", "Account", "Scheduled", "Created"], rows) :
    (status || account ? empty("No posts match these filters", "Clear a filter above to see more.") :
      empty("No posts yet", "Create one in your terminal: reachweaver draft --account ACCOUNT --text 'Your text'"));
  return [h("h1", null, "Queue"),
    h("p", { class: "lede" }, "Posts needing a human come first. Use j and k to move, Enter to open."),
    chips, h("div", { class: "filters" }, acc, search, shown), body];
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
let neighbours = { prev: null, next: null };
async function viewPost(id) {
  const [p, list] = await Promise.all([api("posts/" + encodeURIComponent(id)), api("posts")]);
  const ids = list.posts.map(x => x.id), at = ids.indexOf(id);
  neighbours = { prev: at > 0 ? ids[at - 1] : null, next: at >= 0 && at < ids.length - 1 ? ids[at + 1] : null };
  const link = safeLink(p.remote_url), meaning = STATUS_MEANING[p.status] || [p.status, ""];
  const first = p.next_steps[0], rest = p.next_steps.slice(1);
  const pager = h("div", { class: "pager" }, h("a", { href: "#/queue" }, "← Queue"), h("span", null,
    neighbours.prev ? h("a", { href: "#/post/" + encodeURIComponent(neighbours.prev) }, "[ Previous") : null,
    neighbours.prev && neighbours.next ? "  ·  " : null,
    neighbours.next ? h("a", { href: "#/post/" + encodeURIComponent(neighbours.next) }, "Next ]") : null));
  const out = [pager, h("h1", null, "Post ", h("code", null, p.id), " ", chip(p.status)),
    h("p", { class: "lede" }, p.account_id + " on " + p.platform + (p.campaign ? " · campaign " + p.campaign : ""))];
  out.push(h("div", { class: "card status-card " + p.status },
    h("div", { class: "what" }, h("strong", null, meaning[0]), h("span", null, meaning[1]), (p.status === "approved" || p.status === "scheduled") ? h("div", { class: "note" }, p.approval_summary) : null),
    first ? h("div", { class: "what" }, h("div", null, "Next: " + first.label), command(first.command), first.note ? h("div", { class: "note" }, first.note) : null) : null));
  if (p.content_looks_like_secret) out.push(h("div", { class: "warn" }, "This text contains something that looks like a password, token or key. Check it before approving or sending."));
  if (p.length && p.length.state === "over") out.push(h("div", { class: "error" }, "This text is over the platform limit, so it would be refused when sent."));
  if (p.last_error) out.push(h("div", { class: "error" }, "Last error: " + p.last_error));
  if (p.repeat_check && p.repeat_check.findings && p.repeat_check.findings.length) {
    out.push(h("div", { class: p.repeat_check.blocking ? "error" : "warn" }, h("strong", null, "Repeat check: "),
      p.repeat_check.blocking + " blocking, " + p.repeat_check.warnings + " warning(s). Approving a blocking repeat needs --allow-repeat in your terminal.",
      p.repeat_check.findings.slice(0, 3).map(f => h("div", null, f.message))));
  }
  const copyBtn = h("button", { type: "button" }, "Copy text");
  copyBtn.addEventListener("click", () => copy(p.content, "text"));
  out.push(h("h2", null, "Exact text"), h("pre", { class: "exact" }, p.content), lengthMeter(p.length), h("p", null, copyBtn));
  out.push(h("details", null, h("summary", null, "Approval details"), h("div", { class: "card" }, kv(p.approval))));
  if (p.platform_preview) out.push(h("details", null, h("summary", null, "Platform preview (offline)"), h("div", { class: "card" }, kv(p.platform_preview))));
  out.push(h("details", null, h("summary", null, "Source, schedule and API cost"), h("div", { class: "card" },
    kv({ source_url: p.source_url, scheduled_at: p.scheduled_at || "not scheduled", approved_at: p.approved_at || "not approved", api_cost: p.api_cost }),
    link ? h("p", null, h("a", { href: link, rel: "noopener noreferrer", target: "_blank" }, "Open remote post")) : null)));
  for (const [t, v] of [["Media", p.media], ["Video", p.video], ["Reply", p.reply]]) { const s = reviewSection(t, v); if (s) out.push(s); }
  if (rest.length) out.push(h("h2", null, "More commands for your terminal"), rest.map(s => h("div", null, h("div", null, s.label), command(s.command))));
  out.push(h("p", { class: "muted" }, "This page cannot approve, schedule, pause or publish; run commands in your terminal."));
  out.push(h("h2", null, "Timeline"), p.events.length ? table(["When", "Action", "Detail"],
    p.events.map(e => h("tr", null, h("td", null, when(e.at)), h("td", null, e.action), h("td", null, e.detail)))) : h("p", { class: "muted" }, "No events."));
  return out.flat();
}

// ---- Accounts and channels
async function viewAccounts() {
  const d = await api("accounts");
  return [h("h1", null, "Accounts"), h("p", { class: "lede" }, "Configured accounts. Credential values are never shown."),
    d.error ? h("div", { class: "error" }, d.error) : null,
    h("p", { class: "muted" }, "Credentials are needed only for live sending. Drafting, review and dry runs work without them."),
    d.accounts.length ? table(["Account", "Platform", "Handle", "Adapter", "Credentials"],
      d.accounts.map(a => h("tr", null, h("td", null, a.id), h("td", null, a.platform), h("td", null, a.handle || h("span", { class: "muted" }, "not set")),
        h("td", null, a.adapter + (a.backend ? " (" + a.backend + ")" : "")),
        h("td", null, a.credentials_missing.length
          ? [h("span", { class: "chip attention" }, a.credentials_missing.length + " missing"), h("div", { class: "note" }, "Not set: " + a.credentials_missing.join(", "))]
          : h("span", { class: "chip ok" }, "all set"))))) :
      empty("No accounts configured", "Copy config/accounts.example.json to config/accounts.json.")];
}
async function viewDistribution(params) {
  const meta = await api("distribution/markets");
  const region = params.get("region") || "global", goal = params.get("goal") || "awareness";
  const rsel = h("select", { "aria-label": "Market" }, meta.markets.map(m => h("option", { value: m.id, selected: m.id === region }, m.label)));
  const gsel = h("select", { "aria-label": "Goal" }, meta.goals.map(x => h("option", { value: x, selected: x === goal }, x)));
  const go = () => { location.hash = "#/distribution?region=" + encodeURIComponent(rsel.value) + "&goal=" + encodeURIComponent(gsel.value); };
  rsel.addEventListener("change", go); gsel.addEventListener("change", go);
  const plan = await api("distribution?region=" + encodeURIComponent(region) + "&goal=" + encodeURIComponent(goal) + "&limit=10");
  const rows = (plan.channels || []).map(c => { const src = safeLink(c.official_source); return h("tr", null,
    h("td", { class: "num" }, c.priority), h("td", null, c.platform, h("div", { class: "muted" }, c.content_category)),
    h("td", null, c.reachweaver_delivery === "manual_only" ? "manual only" : c.reachweaver_delivery,
      c.requires_human_only ? h("div", null, h("span", { class: "chip attention" }, "human-only")) : null),
    h("td", null, c.native_schedule, h("div", { class: "muted" }, c.native_schedule_scope || "")),
    h("td", null, src ? h("a", { href: src, rel: "noopener noreferrer", target: "_blank" }, "official source") : h("span", { class: "muted" }, "none"))); });
  return [h("h1", null, "Channels"), h("p", { class: "lede" }, "Offline research hypotheses, not predictions, account connections or publishing permission."),
    h("div", { class: "filters" }, rsel, gsel),
    h("div", { class: "card" }, kv({ status: plan.status, market: plan.market, goal: plan.goal, confidence: plan.confidence,
      reviewed_on: plan.reviewed_on, audience_timezone: plan.audience_timezone, account_connections: "unverified (this view never contacts a platform)" }),
      plan.localization_warning ? h("p", { class: "muted" }, plan.localization_warning) : null),
    rows.length ? table(["#", "Channel", "ReachWeaver delivery", "Native scheduling", "Source"], rows) : h("div", { class: "card muted" }, "No channels."),
    plan.guardrails ? h("p", { class: "muted" }, plan.guardrails) : null];
}

// ---- router (own-property lookup only: '#/constructor' must not reach Object.prototype)
const ROUTES = Object.assign(Object.create(null), { "": ["overview", viewOverview], queue: ["queue", viewQueue],
  schedule: ["schedule", viewSchedule], posted: ["posted", viewPosted],
  accounts: ["accounts", viewAccounts], distribution: ["distribution", viewDistribution] });
let rendering = 0, lastPath = null;
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [pathPart, query = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  let name = parts[0] || "", fn, arg = new URLSearchParams(query);
  if (name === "post" && parts[1]) {
    try { arg = decodeURIComponent(parts[1]); fn = viewPost; name = "queue"; } catch (e) { name = ""; fn = viewOverview; arg = new URLSearchParams(); }
  } else if (ROUTES[name]) fn = ROUTES[name][1]; else { name = ""; fn = viewOverview; }
  return { name, fn, arg, path: "/" + pathPart };
}
async function render() {
  const mine = ++rendering, route = parseHash();
  queueRows = []; queueSel = -1;  // j/k only move through rows of the page being shown
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.dataset.route === (route.name || "overview")) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  try {
    const nodes = await route.fn(route.arg);
    if (mine !== rendering) return;
    main.replaceChildren(...nodes.flat().filter(Boolean));
    document.getElementById("updated").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (err) {
    if (mine !== rendering) return;
    main.replaceChildren(h("div", { class: "error", role: "alert" }, "Could not load this page: " + err.message),
      h("p", { class: "muted" }, "The dashboard only reads your local database. If it is missing, run reachweaver init in your project folder."));
  }
}

// ---- preferences: theme and auto-refresh (browser storage is optional and always wrapped)
const THEMES = ["auto", "light", "dark"];
function store(key, value) { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; } return null; }
function applyTheme(name) {
  if (name === "light" || name === "dark") document.documentElement.setAttribute("data-theme", name); else document.documentElement.removeAttribute("data-theme");
  document.getElementById("theme").textContent = "Theme: " + name;
}
let theme = THEMES.includes(store("rw-theme")) ? store("rw-theme") : "auto";
applyTheme(theme);
function cycleTheme() { theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]; store("rw-theme", theme); applyTheme(theme); toast("Theme: " + theme); }
document.getElementById("theme").addEventListener("click", cycleTheme);

let timer = null;
const auto = document.getElementById("auto");
function typing() { // a text field, menu or text area has focus (a checkbox or button does not count)
  const el = document.activeElement;
  return !!el && (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || (el.tagName === "INPUT" && el.type !== "checkbox" && el.type !== "button"));
}
function holdRefresh() { // do not wipe what the person is in the middle of using
  const search = document.getElementById("search");
  return typing() || !!(search && search.value) || !!document.querySelector("details[open]") || !!document.querySelector("dialog[open]");
}
function setAuto(on) {
  clearInterval(timer); timer = null;
  if (on) timer = setInterval(() => { if (document.visibilityState === "visible" && !holdRefresh()) render(); }, 20000);
}
auto.checked = store("rw-auto") === "1"; setAuto(auto.checked);
auto.addEventListener("change", () => { store("rw-auto", auto.checked ? "1" : "0"); setAuto(auto.checked); });

// ---- keyboard shortcuts
const SHORTCUTS = [["?", "Show this help"], ["g then o / q / s / p", "Go to Overview / Queue / Schedule / Posted"],
  ["g then a / c", "Go to Accounts / Channels"], ["j / k", "Move down / up in the queue or the posted list"],
  ["Enter", "Open the selected post"], ["[ / ]", "Previous / next post"], ["/", "Search the queue or the posted list"], ["r", "Refresh"], ["t", "Cycle the theme"], ["Esc", "Close help, leave a field"]];
let helpDialog = null, pendingG = false, gTimer = null;
function showHelp() {
  if (!helpDialog) {
    const close = h("button", { type: "button" }, "Close");
    helpDialog = h("dialog", { "aria-label": "Keyboard shortcuts" }, h("h2", null, "Keyboard shortcuts"),
      h("table", null, h("tbody", null, SHORTCUTS.map(([k, d]) => h("tr", null, h("td", null, h("kbd", null, k)), h("td", null, d))))), h("p", null, close));
    close.addEventListener("click", () => helpDialog.close());
    document.body.append(helpDialog);
  }
  if (typeof helpDialog.showModal === "function" && !helpDialog.open) helpDialog.showModal();
}
const GO = Object.assign(Object.create(null), { o: "#/", q: "#/queue", s: "#/schedule", p: "#/posted", a: "#/accounts", c: "#/distribution" });
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (helpDialog && helpDialog.open) return;
  const el = document.activeElement;
  if (e.key === "Escape") { if (typing()) el.blur(); return; }
  if (typing()) return;
  if (pendingG) { pendingG = false; clearTimeout(gTimer); if (GO[e.key]) { e.preventDefault(); location.hash = GO[e.key]; } return; }
  switch (e.key) {
    case "?": e.preventDefault(); showHelp(); break;
    case "g": pendingG = true; gTimer = setTimeout(() => { pendingG = false; }, 1200); break;
    case "j": select(1); break;
    case "k": select(-1); break;
    case "Enter": { const row = queueRows[queueSel]; if (row && !row.hidden && (el === document.body || el === main)) { const a = row.querySelector('a[href^="#/"]'); if (a) location.hash = a.getAttribute("href"); } break; }
    case "/": { const s = document.getElementById("search"); if (s) { e.preventDefault(); s.focus(); } break; }
    case "r": render(); toast("Refreshed"); break;
    case "t": cycleTheme(); break;
    case "[": if (neighbours.prev && location.hash.startsWith("#/post/")) location.hash = "#/post/" + encodeURIComponent(neighbours.prev); break;
    case "]": if (neighbours.next && location.hash.startsWith("#/post/")) location.hash = "#/post/" + encodeURIComponent(neighbours.next); break;
    default: break;
  }
});
// Printing a post sheet shows every section, including the approval binding; closed ones close again after.
let printOpened = [];
window.addEventListener("beforeprint", () => { printOpened = [...document.querySelectorAll("details:not([open])")]; printOpened.forEach(d => { d.open = true; }); });
window.addEventListener("afterprint", () => { printOpened.forEach(d => { d.open = false; }); printOpened = []; });
document.getElementById("keys").addEventListener("click", showHelp);
document.getElementById("skip").addEventListener("click", e => { e.preventDefault(); main.focus(); });
window.addEventListener("hashchange", () => { const path = parseHash().path; render(); if (path !== lastPath) main.focus(); lastPath = path; });
document.getElementById("refresh").addEventListener("click", () => { render(); toast("Refreshed"); });
lastPath = parseHash().path;
render();
"""
