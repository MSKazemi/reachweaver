"""Self-contained browser UI for the manual Share Composer.

No external CSS, JavaScript, fonts, images, analytics or network requests.
The browser only talks to the loopback ReachWeaver ShareServer. All user
captions, package names and account names are inserted with textContent,
never HTML markup. There is no Publish, approve or schedule control:
"Mark as posted" only records a post the owner already published by hand.

The look mirrors the read-only dashboard (ui_assets.APP_CSS): the same colour
tokens (accent #0b6e69, dark mode by prefers-color-scheme), system font stack,
12px radius and "RW" mark, copied here so the two pages read as one product.
"""
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="light dark">
<title>ReachWeaver — Write a post</title>
<style nonce="__CSP_NONCE__">
:root {
  color-scheme: light;
  --bg: #f5f6f8; --panel: #ffffff; --ink: #1b1f24; --muted: #5b6573; --line: #dfe3e9; --hover: #f2f5f8;
  --field-line: #8a94a3;
  --accent: #0b6e69; --accent-ink: #ffffff; --accent-soft: #e2f2f0; --accent-strong: #08544f;
  --ok: #166a2e; --warn: #7d5400; --bad: #b42318; --info: #1f5fbf;
  --ok-bg: #e6f4ea; --warn-bg: #fff4d6; --bad-bg: #fde8e6; --info-bg: #e5eefb; --code-bg: #eef1f5;
  --focus: #1f5fbf;
  --shadow: 0 1px 2px rgba(16, 24, 40, .06), 0 1px 3px rgba(16, 24, 40, .08);
  --radius: 12px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #0f1317; --panel: #171c22; --ink: #e8ecf1; --muted: #9aa5b3; --line: #2b333d; --hover: #1d242c;
    --field-line: #6b7686;
    --accent: #3cc2b8; --accent-ink: #06201d; --accent-soft: #123532; --accent-strong: #7fe0d8;
    --ok: #56d364; --warn: #e3b341; --bad: #ff7b72; --info: #79b8ff;
    --ok-bg: #12301b; --warn-bg: #3a2c08; --bad-bg: #3d1512; --info-bg: #12263f; --code-bg: #0c1014;
    --focus: #79b8ff;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0f1317; --panel: #171c22; --ink: #e8ecf1; --muted: #9aa5b3; --line: #2b333d; --hover: #1d242c;
  --field-line: #6b7686;
  --accent: #3cc2b8; --accent-ink: #06201d; --accent-soft: #123532; --accent-strong: #7fe0d8;
  --ok: #56d364; --warn: #e3b341; --bad: #ff7b72; --info: #79b8ff;
  --ok-bg: #12301b; --warn-bg: #3a2c08; --bad-bg: #3d1512; --info-bg: #12263f; --code-bg: #0c1014;
  --focus: #79b8ff;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4);
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.skip { position: absolute; left: -999px; top: 0; background: var(--accent); color: var(--accent-ink); padding: 10px 14px; z-index: 50; }
.skip:focus { left: 8px; top: 8px; }
.hidden { display: none !important; }
code, kbd { font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--code-bg); border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px; overflow-wrap: anywhere; }

/* ---- header: brand bar + hero (one line on phones) */
header { background: var(--panel); border-bottom: 1px solid var(--line); }
.topbar { max-width: 1160px; margin: auto; padding: 10px 24px; display: flex; align-items: center; gap: 10px 14px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 9px; font-weight: 700; font-size: 16px; }
.mark { display: inline-grid; place-items: center; width: 30px; height: 30px; border-radius: 9px; font-weight: 800;
  background: var(--accent); color: var(--accent-ink); font-size: 12px; letter-spacing: .5px; flex: none; }
h1 { font-size: 16px; font-weight: 600; margin: 0; color: var(--muted); }
h1::before { content: "— "; }
.results { margin-left: auto; display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); flex-wrap: wrap; }
.hero { max-width: 1160px; margin: auto; padding: 4px 24px 22px; }
.tagline { font-size: clamp(26px, 3.4vw, 38px); font-weight: 750; letter-spacing: -.03em; line-height: 1.1; margin: 8px 0 6px; }
.lede { max-width: 640px; margin: 0; color: var(--muted); }
.security { display: inline-block; margin-top: 12px; font-size: 12px; font-weight: 600; padding: 4px 11px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent-strong); }

/* ---- progress */
main { max-width: 1160px; margin: auto; padding: 18px 24px 64px; }
.progress { display: flex; gap: 6px; list-style: none; margin: 0; padding: 0; counter-reset: step; }
.progress li { flex: 1 1 0; min-width: 0; display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; color: var(--muted);
  padding: 7px 8px; border-radius: 9px; background: var(--panel); border: 1px solid var(--line); }
.progress .num { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; flex: none; font-size: 12px;
  border: 1px solid var(--field-line); }
.progress .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.progress li.done { color: var(--ink); }
.progress li.done .num { background: var(--accent-soft); color: var(--accent-strong); border-color: var(--accent); }
.progress li[aria-current="step"] { color: var(--accent-strong); border: 2px solid var(--accent); padding: 6px 7px; background: var(--accent-soft); }
.progress li[aria-current="step"] .num { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.next { margin: 8px 2px 16px; font-size: 14px; color: var(--ink); }
.next b { color: var(--accent-strong); }

/* ---- panels and fields */
.layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(315px, .92fr); align-items: start; gap: 18px; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); padding: 20px; min-width: 0; }
h2 { font-size: 19px; margin: 0 0 4px; letter-spacing: -.01em; }
h3 { font-size: 16px; margin: 0; overflow-wrap: anywhere; }
.explain { color: var(--muted); font-size: 14px; margin: 0 0 14px; }
label { display: block; font-weight: 650; font-size: 14px; margin: 14px 0 6px; }
textarea, input[type=text], input[type=search], input[type=url], input[type=datetime-local], select {
  display: block; width: 100%; min-height: 44px; padding: 11px 12px; border: 1px solid var(--field-line); border-radius: 10px;
  background: var(--panel); color: var(--ink); font: inherit; resize: vertical; }
input[type=file] { display: block; width: 100%; max-width: 100%; min-height: 44px; padding: 6px; font: inherit; font-size: 14px; color: var(--ink);
  border: 1px dashed var(--field-line); border-radius: 10px; background: var(--panel); }
input[type=file]::file-selector-button { font: inherit; font-weight: 650; min-height: 32px; margin-right: 10px; padding: 4px 12px; border-radius: 8px;
  border: 1px solid var(--field-line); background: var(--panel); color: var(--ink); cursor: pointer; }
textarea { min-height: 190px; line-height: 1.55; }
textarea[aria-invalid=true], input[aria-invalid=true] { border: 2px solid var(--bad); }
.hint { font-size: 13px; color: var(--muted); margin: 6px 0 0; overflow-wrap: anywhere; }
.field-error { font-size: 13px; color: var(--bad); font-weight: 650; margin: 6px 0 0; }
.field { margin-top: 14px; }
.docbar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; justify-content: space-between; margin: 0 0 12px; padding: 8px 10px;
  background: var(--code-bg); border-radius: 10px; font-size: 13px; }
.docbar #editing { font-weight: 650; overflow-wrap: anywhere; min-width: 0; }
.docbar .tools { display: flex; gap: 8px; align-items: center; }
.saved { font-size: 12px; font-weight: 650; padding: 3px 9px; border-radius: 999px; background: var(--panel); color: var(--muted); border: 1px solid var(--line); }
.saved.ok { background: var(--ok-bg); color: var(--ok); border-color: transparent; }
.saved.bad { background: var(--warn-bg); color: var(--warn); border-color: transparent; }
button { font-family: inherit; }
.small { font: inherit; font-size: 13px; font-weight: 650; padding: 5px 11px; min-height: 32px; border-radius: 8px; border: 1px solid var(--field-line);
  background: var(--panel); color: var(--ink); cursor: pointer; }
.small:hover, .secondary:hover { border-color: var(--accent); }
.small:disabled { opacity: .5; cursor: not-allowed; }
.network-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; max-height: 330px; overflow: auto; padding: 3px; }
.network { text-align: left; display: flex; flex-direction: column; gap: 2px; min-height: 58px; border: 1px solid var(--line); background: var(--panel);
  border-radius: 10px; padding: 9px 11px; cursor: pointer; color: var(--ink); font: inherit; }
.network:hover { border-color: var(--accent); }
.network strong { font-size: 14px; overflow-wrap: anywhere; }
.network span { font-size: 12px; color: var(--muted); }
.network[aria-pressed=true] { background: var(--accent-soft); border: 2px solid var(--accent); padding: 8px 10px; }
.network[aria-pressed=true] strong::before { content: "✓ "; color: var(--accent-strong); }
.picked { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
.pick { font: inherit; font-size: 13px; font-weight: 650; min-height: 36px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--accent); background: var(--accent-soft); color: var(--accent-strong); }
.pick:hover { background: var(--accent); color: var(--accent-ink); }
.results-link { display: inline-flex; align-items: center; min-height: 44px; padding: 8px 14px; border-radius: 10px; font-weight: 700;
  border: 1px solid var(--accent); color: var(--accent-strong); background: var(--accent-soft); text-decoration: none; }
.results-link:hover { background: var(--accent); color: var(--accent-ink); }
.alert .results-link { margin: 8px 8px 0 0; }
.count { display: inline-block; color: var(--accent-strong); background: var(--accent-soft); border-radius: 999px; font-size: 12px; font-weight: 700; padding: 4px 10px; }
.filter-row { display: flex; gap: 8px; margin: 12px 0 10px; align-items: center; flex-wrap: wrap; }
.filter-row button { font: inherit; font-size: 13px; padding: 6px 12px; min-height: 34px; background: var(--panel); border: 1px solid var(--field-line); border-radius: 9px; color: var(--ink); cursor: pointer; }
.filter-row button[aria-pressed=true] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.actions { display: flex; gap: 10px; justify-content: flex-start; margin-top: 18px; flex-wrap: wrap; align-items: center; }
.go { margin: 18px 0 0; padding: 14px 16px; }
.go .actions { margin-top: 0; }
.primary { background: var(--accent); color: var(--accent-ink); border: 1px solid var(--accent); border-radius: 10px; padding: 11px 20px; min-height: 44px;
  cursor: pointer; font-size: 15px; font-weight: 700; }
.primary:hover { background: var(--accent-strong); border-color: var(--accent-strong); }
.primary:disabled, .secondary:disabled { opacity: .45; cursor: not-allowed; }
.secondary { background: var(--panel); color: var(--ink); border: 1px solid var(--field-line); border-radius: 10px; padding: 10px 16px; min-height: 44px;
  cursor: pointer; font-size: 14px; font-weight: 650; }
.alert { border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-bg); padding: 11px 14px; margin-top: 14px; color: var(--ink);
  font-size: 14px; white-space: pre-wrap; overflow-wrap: anywhere; }
.alert.ok { border-color: var(--ok); background: var(--ok-bg); }
.alert .small { margin: 8px 8px 0 0; }
.wide { margin-top: 18px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(280px, 100%), 1fr)); gap: 12px; margin-top: 14px; }
.review { border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; background: var(--panel); min-width: 0; }
.review-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.save-buttons { display: flex; gap: 10px; flex-wrap: wrap; }
.review pre { white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; background: var(--code-bg); color: var(--ink); padding: 12px;
  border-radius: 9px; font-family: inherit; font-size: 14px; max-height: 230px; overflow: auto; }
.review ul { color: var(--warn); font-size: 13px; padding-left: 20px; overflow-wrap: anywhere; }
.review ul.media-checks { color: var(--ink); }
.review small { color: var(--muted); }
.chip { display: inline-block; font-size: 12px; font-weight: 700; padding: 3px 9px; border-radius: 999px; background: var(--code-bg); color: var(--muted); white-space: nowrap; }
.chip.ready { background: var(--info-bg); color: var(--info); }
.chip.posted { background: var(--ok-bg); color: var(--ok); }
.chip.here { background: var(--accent-soft); color: var(--accent-strong); }
.confirm { margin-top: 14px; padding: 12px 14px; border: 1px solid var(--warn); background: var(--warn-bg); border-radius: 10px; font-size: 14px;
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.confirm span { flex: 1 1 220px; }
.media-list { list-style: none; padding: 0; margin: 10px 0 0; display: flex; flex-direction: column; gap: 6px; }
.media-list li { display: flex; justify-content: space-between; gap: 10px; align-items: center; background: var(--code-bg); border-radius: 9px; padding: 6px 10px;
  font-size: 13px; min-width: 0; }
.media-list li span { overflow-wrap: anywhere; min-width: 0; }
.netlist { list-style: none; padding: 0; margin: 12px 0 0; display: flex; flex-direction: column; gap: 10px; }
.netlist li { border-top: 1px solid var(--line); padding-top: 10px; font-size: 14px; }
.netrow { display: flex; justify-content: space-between; gap: 8px; align-items: center; flex-wrap: wrap; }
.netrow .who { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.netlist a { overflow-wrap: anywhere; }
.markform { margin-top: 6px; }
.markform label { margin: 10px 0 5px; font-size: 13px; }
.cmdline { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px; }
.foot { font-size: 13px; color: var(--muted); margin: 18px 0 0; }
.page-foot { max-width: 1160px; margin: 0 auto; padding: 0 24px 40px; font-size: 14px; color: var(--muted); }
.page-foot .cmdline { margin-top: 0; }
.no-preview { padding: 24px 0; color: var(--muted); text-align: center; font-size: 14px; }
@media (max-width: 820px) {
  .topbar { padding: 8px 14px; }
  .results { display: none; }
  .hero { padding: 0 14px 8px; display: flex; gap: 8px; align-items: baseline; white-space: nowrap; overflow: hidden; }
  .tagline { font-size: 14px; font-weight: 700; letter-spacing: 0; margin: 0; flex: none; }
  .lede, .security { display: none; }
  .hero::after { content: "Private and local · nothing is posted for you"; font-size: 13px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; }
  main { padding: 12px 14px 48px; }
  .layout { grid-template-columns: 1fr; gap: 14px; }
  .panel { padding: 16px; }
  .explain { margin-bottom: 8px; }
  .network-options { max-height: none; overflow: visible; }  /* the list flows in the page; the chips show the choice */
  .page-foot { padding: 0 14px 32px; }
}
@media (max-width: 640px) {
  .progress { gap: 4px; }
  .progress li { flex-direction: column; gap: 2px; padding: 5px 2px; font-size: 12px; text-align: center; }
  .progress li[aria-current="step"] { padding: 4px 1px; }
  .small, .filter-row button, .network, .pick, input[type=file]::file-selector-button { min-height: 44px; }
  .small { padding: 8px 12px; }
  textarea { min-height: 150px; }
  .write-explain { display: none; }
}
@media (max-width: 400px) { .network-options { grid-template-columns: 1fr; } .review { padding: 13px; } }
@media (prefers-reduced-motion: no-preference) { button { transition: background-color .15s, border-color .15s; } }
</style>
</head>
<body>
<a class="skip" href="#caption">Skip to the caption</a>
<header>
<div class="topbar">
  <span class="brand"><span class="mark" aria-hidden="true">RW</span><span>ReachWeaver</span></span>
  <h1>Write a post</h1>
  <span class="results" id="results-top"></span>
</div>
<div class="hero">
  <p class="tagline">One story. Many places.</p>
  <p class="lede">Write once, pick your networks, check each version, then copy it into each app yourself.</p>
  <span class="security">Private and local · No login · No automatic posting</span>
</div>
</header>
<main>
<nav aria-label="Progress">
<ol class="progress" id="progress">
 <li data-step="write"><span class="num" aria-hidden="true">1</span><span class="lbl">Write</span><span class="sr-only state"></span></li>
 <li data-step="choose"><span class="num" aria-hidden="true">2</span><span class="lbl">Choose</span><span class="sr-only state"></span></li>
 <li data-step="preview"><span class="num" aria-hidden="true">3</span><span class="lbl">Preview</span><span class="sr-only state"></span></li>
 <li data-step="save"><span class="num" aria-hidden="true">4</span><span class="lbl">Save</span><span class="sr-only state"></span></li>
 <li data-step="post"><span class="num" aria-hidden="true">5</span><span class="lbl">Post</span><span class="sr-only state"></span></li>
</ol>
</nav>
<p class="next" id="next-hint"><b>Next:</b> write your caption.</p>
<div id="announce" class="sr-only" role="status" aria-live="polite"></div>
<div class="layout">
<section class="panel" aria-labelledby="write-title">
 <div class="docbar">
   <span id="editing">New post</span>
   <span class="tools">
     <span id="saved" class="saved" role="status" aria-live="polite">Not saved yet</span>
     <button id="undo" class="small" type="button" disabled>Undo</button>
   </span>
 </div>
 <h2 id="write-title">Your post</h2>
 <p class="explain write-explain">What would you like to share? Your draft is kept on this computer as you type.</p>
 <label for="caption">Caption or description</label>
 <textarea id="caption" placeholder="Tell people what you made, why it matters, and what they can do next…" maxlength="64000" spellcheck="true"></textarea>
 <p class="hint" id="counter">0 characters · Kept on this computer, never uploaded</p>
 <div id="video-title" class="field hidden">
   <label for="title">YouTube video title</label>
   <input id="title" type="text" maxlength="200" placeholder="A clear and descriptive title">
   <p class="hint">Needed for YouTube. You upload the video in YouTube Studio yourself.</p>
 </div>
 <div id="community-notes" class="field hidden">
   <label for="notes">Your personal community note</label>
   <input id="notes" type="text" maxlength="200" placeholder="E.g. Read the group rules and add a useful personal answer">
   <p class="hint">Community posts are written by you. ReachWeaver keeps your note, never a prepared promotional post.</p>
 </div>
 <div class="field">
   <label for="media-input">Photos or videos (optional)</label>
   <input id="media-input" type="file" multiple accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.webm" aria-describedby="media-hint">
   <ul id="media-list" class="media-list" aria-label="Attached files"></ul>
   <p class="hint" id="media-hint">Kept on this computer and copied into the package when you save; never uploaded to a network. Up to 10 files, 256 MiB each.</p>
 </div>
</section>
<section class="panel" aria-labelledby="networks-title">
 <h2 id="networks-title">Where to share?</h2>
 <p class="explain">Pick up to 10. Each one gets a version to copy and post yourself.</p>
 <label for="search">Find a network</label>
 <input id="search" type="search" placeholder="Search Instagram, Misskey, YouTube…" autocomplete="off">
 <div class="filter-row" role="group" aria-label="Network filter">
   <button id="popular" type="button" aria-pressed="true">Popular</button>
   <button id="all" type="button" aria-pressed="false">All networks</button>
   <span class="count" id="selection-count">0 selected</span>
 </div>
 <div class="picked hidden" id="picked" role="group" aria-label="Selected networks"></div>
 <div class="network-options" id="networks" aria-label="Available networks">
   <p class="hint">Loading your offline network list…</p>
 </div>
 <p class="hint">No passwords or tokens needed. Each post stays your decision.</p>
</section>
</div>
<section class="panel go" aria-label="Check your posts">
 <div class="actions">
   <button id="preview" class="primary" type="button">Preview my posts</button>
   <button id="reset" class="secondary" type="button">Clear form</button>
   <span class="hint">Checks every network's limits on this computer. Nothing is saved or sent.</span>
 </div>
</section>
<section class="panel wide" aria-labelledby="review-title">
  <div class="review-head">
    <div>
      <h2 id="review-title">Your previews</h2>
      <p class="explain">Adjust each caption for its network, then preview again before saving.</p>
    </div>
    <div class="save-buttons">
      <button id="save" class="primary" type="button" disabled>Save as new package</button>
      <button id="update" class="secondary hidden" type="button" disabled>Update this package</button>
    </div>
  </div>
  <div id="confirm-update" class="confirm hidden" role="alertdialog" aria-labelledby="confirm-text">
    <span id="confirm-text">Replace the saved files of this package with what is in the editor?</span>
    <button id="confirm-yes" class="primary" type="button">Yes, update it</button>
    <button id="confirm-no" class="secondary" type="button">Cancel</button>
  </div>
  <div id="status" role="status" aria-live="polite"></div>
  <div id="results" class="cards"><p class="no-preview">Pick networks and choose “Preview my posts” to see each version.</p></div>
  <p class="foot">Saving creates private files on this computer, not online posts. You still choose the account, media, audience and time inside each app.</p>
</section>
<section class="panel wide" aria-labelledby="packages-title">
  <div class="review-head">
    <div>
      <h2 id="packages-title">Saved packages</h2>
      <p class="explain">Open one to edit it again, duplicate it, or mark a network as posted after you posted it yourself.</p>
    </div>
    <button id="refresh" class="secondary" type="button">Refresh</button>
  </div>
  <div id="pkg-status" role="status" aria-live="polite"></div>
  <div id="packages" class="cards"><p class="no-preview">Loading your saved packages…</p></div>
  <p class="foot">“Mark as posted” records a post you already published yourself so you can track how it did. It never posts, schedules or sends anything.</p>
</section>
</main>
<footer class="page-foot"><div class="cmdline" id="results-foot"></div></footer>
<noscript><p>This local composer needs JavaScript to show previews. The command-line Share flow works without JavaScript.</p></noscript>
<script nonce="__CSP_NONCE__">
(function () {
 "use strict";
 // "#TOKEN" or "#TOKEN&idea=idea-xxxxxxxx" (the dashboard's "Write a post from this idea" under `studio`). The
 // fragment is never sent to any server; the idea part is dropped from the address at once, so a reload never
 // offers it again.
 const hashParts = window.location.hash.slice(1).split("&");
 const sessionToken = hashParts[0];
 const ideaWanted = (function(){
   const value = new URLSearchParams(hashParts.slice(1).join("&")).get("idea") || "";
   return /^idea-[0-9a-f]{8}$/.test(value) ? value : null;
 })();
 if (hashParts.length > 1) { try { history.replaceState(null, "", "#" + sessionToken); } catch(e) { /* keep going */ } }
 // Under `reachweaver studio`: the dashboard's Results page (its own token is in that URL). null when alone.
 let dashboardUrl = null;
 const DASHBOARD_URL = /^http:\/\/127\.0\.0\.1:\d{1,5}\/[A-Za-z0-9_-]{20,}\/#\/results$/;
 const el = id => document.getElementById(id);
 const MAX_MEDIA = 10;
 const MAX_MEDIA_BYTES = 256 * 1024 * 1024;
 const RESULTS_CMD = "reachweaver ui";
 const LINK_HELP = "Paste the full link to your post, starting with https://";
 const selected = new Set();
 const edits = new Map();
 let media = [];
 let current = {package:null, revision:null, state:null};
 let catalog = [];
 let accounts = [];
 let ledgerIds = {};
 let filter = "popular";
 let previewValid = false;
 let savedClean = false;
 let previewData = [];
 let busy = false;
 let ready = false;
 let saveTimer = null;
 const undoStack = [];
 let stable = null;
 let lastChange = 0;
 let confirmOpen = null;
 let lastPackages = [];
 const text = function(tag, value, className) {
   const node = document.createElement(tag);
   node.textContent = value;
   if (className) node.className = className;
   return node;
 };
 const button = function(label, className) {
   const node = text("button", label, className);
   node.type = "button";
   return node;
 };
 function announce(message) {
   const node = el("announce");
   node.textContent = "";
   window.setTimeout(function(){ node.textContent = message; }, 60);
 }
 async function copy(value, node) {
   const label = node.textContent;
   let done = false;
   try {
     await navigator.clipboard.writeText(value);
     done = true;
   } catch(e) {
     const area = document.createElement("textarea");
     area.value = value; area.setAttribute("readonly", ""); area.className = "sr-only";
     document.body.appendChild(area); area.select();
     try { done = document.execCommand("copy"); } catch(e2) { done = false; }
     area.remove();
     node.focus();
   }
   if (done) {
     node.textContent = "Copied";
     announce("Copied to the clipboard.");
     window.setTimeout(function(){ node.textContent = label; }, 2000);
   } else {
     announce("Copying was blocked. Select the text and copy it yourself.");
     showMessage("Copying was blocked by the browser. Select the text and copy it yourself.", false);
   }
 }
 function copyButton(label, value, accessible) {
   const node = button(label, "small");
   if (accessible) node.setAttribute("aria-label", accessible);
   node.addEventListener("click", function(){ copy(value, node); });
   return node;
 }
 // A message box: plain text, then optional copy buttons [{label, value, aria}].
 const showIn = function(id, message, isSuccess, copies) {
   const target = el(id);
   target.replaceChildren();
   target.className = message ? (isSuccess ? "alert ok" : "alert") : "";
   if (!message) return;
   target.appendChild(document.createTextNode(message));
   if (copies && copies.length) {
     const row = document.createElement("div");
     for (const c of copies) row.appendChild(copyButton(c.label, c.value, c.aria));
     target.appendChild(row);
   }
 };
 const showMessage = function(message, isSuccess, copies) { showIn("status", message, isSuccess, copies); };
 function resultsLink(label) {
   const link = text("a", label, "results-link");
   link.href = dashboardUrl;
   link.rel = "noopener noreferrer";
   return link;
 }
 function resultsLine(id) {
   const box = el(id);
   if (dashboardUrl) { box.replaceChildren(resultsLink("See how your posts did")); return; }
   box.replaceChildren(text("span", "See how your posts did: run "), text("code", RESULTS_CMD));
   box.appendChild(copyButton("Copy", RESULTS_CMD, "Copy the command " + RESULTS_CMD));
 }
 async function send(path, body, contentType, extra) {
   const headers = Object.assign({"X-ReachWeaver-Token":sessionToken}, extra || {});
   if (body !== undefined) headers["Content-Type"] = contentType;
   const res = await fetch(path, {
     method: body === undefined ? "GET" : "POST", headers:headers, body:body,
     credentials:"omit", cache:"no-store", redirect:"error"
   });
   const data = await res.json();
   if (!res.ok) throw new Error((data.errors || ["Request failed."]).join("\n"));
   return data;
 }
 function api(path, payload) {
   return send(path, payload === undefined ? undefined : JSON.stringify(payload), "application/json");
 }
 function upload(file) {
   return send("/api/media", file, "application/octet-stream",
               {"X-ReachWeaver-Filename": encodeURIComponent(file.name)});
 }
 function humanSelected() { return catalog.some(p => selected.has(p.id) && p.human_only); }
 function textSelected() { return catalog.some(p => selected.has(p.id) && !p.human_only); }
 function payload() {
   return {
     platforms:Array.from(selected),
     // A human-only network never receives the caption; with only those selected, the note is all that is sent.
     text:(textSelected() || !humanSelected()) ? (el("caption").value || null) : null,
     title:selected.has("youtube") ? el("title").value : null,
     note:humanSelected() ? el("notes").value : "",
     overrides:Object.fromEntries(Array.from(edits).filter(entry => selected.has(entry[0]))),
     media:media.map(m => m.id)
   };
 }
 function editorState() {
   return {
     platforms:Array.from(selected), text:el("caption").value || null, title:el("title").value || null,
     note:el("notes").value, overrides:Object.fromEntries(edits), media:media.map(m => m.id),
     package:current.package, revision:current.revision
   };
 }
 function snapshot() {
   return {selected:Array.from(selected), edits:Array.from(edits), caption:el("caption").value,
           title:el("title").value, notes:el("notes").value, media:media.slice()};
 }
 // ---- progress: Write -> Choose -> Preview -> Save -> Post, from the real editor state
 const STEPS = [
   ["write", "write your caption, or attach a photo or video."],
   ["choose", "pick where to share it (up to 10 networks)."],
   ["preview", "choose “Preview my posts” to check every network."],
   ["save", "choose “Save as new package” to keep these posts ready."],
   ["post", "copy each caption into its app and post it, then use “Mark as posted” below."]
 ];
 function progress() {
   const wrote = Boolean(el("caption").value.trim() || media.length || el("notes").value.trim());
   const chose = selected.size > 0;
   const saved = savedClean && Boolean(current.package);
   const previewed = previewValid || saved;
   const posted = saved && Boolean(current.state && current.state.networks.some(n => n.state === "posted"));
   const done = [wrote, chose, previewed, saved, posted];
   const at = done.indexOf(false);
   const items = el("progress").querySelectorAll("li");
   items.forEach(function(item, index) {
     item.classList.toggle("done", done[index] && (at === -1 || index < at));
     const isCurrent = index === at;
     if (isCurrent) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
     item.querySelector(".num").textContent = item.classList.contains("done") ? "✓" : String(index + 1);
     item.querySelector(".state").textContent = item.classList.contains("done") ? " (done)" : isCurrent ? " (current step)" : "";
   });
   const hint = el("next-hint");
   hint.replaceChildren(text("b", at === -1 ? "Done: " : "Next: "));
   hint.appendChild(document.createTextNode(at === -1 ?
     "posted and recorded. When you have numbers, record them to see how it did." : STEPS[at][1]));
 }
 function setSaved(label, kind) {
   const node = el("saved");
   node.textContent = label;
   node.className = "saved" + (kind ? " " + kind : "");
 }
 function saveDraft() {
   saveTimer = null;
   api("/api/draft/save", editorState()).then(function(){
     setSaved("Draft saved", "ok");
   }).catch(function(e){
     setSaved("Not saved", "bad");
     showMessage("Your draft could not be kept: " + (e.message || "error"), false);
   });
 }
 function scheduleSave() {
   if (!ready) return;
   setSaved("Saving…", "");
   if (saveTimer) clearTimeout(saveTimer);
   saveTimer = setTimeout(saveDraft, 700);
 }
 function changed() {
   const now = Date.now();
   if (stable && now - lastChange > 1000) {
     undoStack.push(stable);
     if (undoStack.length > 50) undoStack.shift();
   }
   lastChange = now;
   stable = snapshot();
   el("undo").disabled = !undoStack.length;
   scheduleSave();
   progress();
 }
 function counter() {
   el("counter").textContent = el("caption").value.length + " characters · Kept on this computer, never uploaded";
 }
 // What the last preview or save checked. "Your post changed" is only true against that; after a reload, an opened
 // package or Undo there is no preview on the page yet, which is not a change.
 let previewedKey = null;
 function invalidate() {
   previewValid = false;
   savedClean = false;
   el("save").disabled = true;
   el("update").disabled = true;
   el("confirm-update").classList.add("hidden");
   previewData = [];
   const differs = previewedKey !== null && JSON.stringify(payload()) !== previewedKey;
   el("results").replaceChildren(text("p", differs ? "Your post changed. Preview again before saving." :
     selected.size ? "Choose “Preview my posts” to see each version." :
     "Pick networks and choose “Preview my posts” to see each version.", "no-preview"));
   showMessage("",false);
   progress();
 }
 function toggleNetwork(p) {
   if (selected.has(p.id)) { selected.delete(p.id); edits.delete(p.id); }
   else if (selected.size < 10) selected.add(p.id);
   else { showMessage("You can pick at most ten networks at once.", false); return false; }
   conditionalFields();
   invalidate();
   renderNetworks();
   changed();
   return true;
 }
 // The chosen networks as chips above the list, so the choice stays in view however long the list is.
 function renderPicked() {
   const box = el("picked");
   box.replaceChildren();
   for (const p of catalog.filter(c => selected.has(c.id))) {
     const chip = button(p.label + " ✕", "pick");
     chip.setAttribute("aria-label", "Remove " + p.label);
     chip.addEventListener("click", function(){
       if (!toggleNetwork(p)) return;
       const next = el("picked").querySelector("button") || el("search");
       next.focus();
     });
     box.appendChild(chip);
   }
   box.classList.toggle("hidden", !selected.size);
 }
 function conditionalFields() {
   el("video-title").classList.toggle("hidden",!selected.has("youtube"));
   el("community-notes").classList.toggle("hidden",!humanSelected());
   el("selection-count").textContent = selected.size + " selected";
   renderPicked();
 }
 function updateEditing() {
   const state = current.state;
   el("editing").textContent = current.package ?
     "Editing: " + ((state && state.display_name) || "a saved package") :
     "New post (not saved as a package yet)";
   el("update").classList.toggle("hidden", !current.package);
 }
 function restore(snap) {
   selected.clear();
   const known = new Set(catalog.map(p => p.id));
   for (const id of snap.selected) if (known.has(id)) selected.add(id);
   edits.clear();
   for (const entry of snap.edits) edits.set(entry[0], entry[1]);
   el("caption").value = snap.caption || "";
   el("title").value = snap.title || "";
   el("notes").value = snap.notes || "";
   media = snap.media.slice();
   counter(); conditionalFields(); renderNetworks(); renderMedia(); invalidate();
 }
 function applyEditor(editor, infos) {
   previewedKey = null;  // a reloaded draft or an opened package has not been previewed on this page yet
   restore({selected:editor.platforms || [], edits:Object.entries(editor.overrides || {}),
            caption:editor.text || "", title:editor.title || "", notes:editor.note || "", media:infos || []});
   undoStack.length = 0;
   stable = snapshot();
   el("undo").disabled = true;
 }
 function undo() {
   if (!undoStack.length) return;
   restore(undoStack.pop());
   stable = snapshot();
   lastChange = 0;
   el("undo").disabled = !undoStack.length;
   scheduleSave();
   showMessage("Undid the last change.", true);
 }
 function renderNetworks() {
   const q = el("search").value.trim().toLowerCase();
   const choices = catalog.filter(p =>
      (filter === "all" || p.featured || selected.has(p.id)) &&
      (p.label.toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
   );
   const items = el("networks");
   items.replaceChildren();
   if (!choices.length) {items.appendChild(text("p","No matching networks. Try “All networks”.","hint"));return;}
   for(const p of choices) {
     const node = button("", "network");
     node.setAttribute("aria-pressed", selected.has(p.id) ? "true" : "false");
     node.appendChild(text("strong", p.label));
     node.appendChild(text("span",p.human_only ? "You write the post · notes only" :
       p.api_capable ? "Copy and post yourself (can also be connected)" : "Copy and post yourself"));
     node.addEventListener("click",function(){
       if (!toggleNetwork(p)) return;
       const again = Array.from(el("networks").querySelectorAll(".network")).find(n => n.firstChild.textContent === p.label);
       if (again) again.focus();
     });
     items.appendChild(node);
   }
 }
 function sizeLabel(bytes) {
   return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + " MiB" : Math.max(1, Math.round(bytes / 1024)) + " KiB";
 }
 function renderMedia() {
   const list = el("media-list");
   list.replaceChildren();
   for (const item of media) {
     const row = document.createElement("li");
     row.appendChild(text("span", item.name + " · " + (item.kind || "file") + " · " + sizeLabel(item.size)));
     const remove = button("Remove", "small");
     remove.setAttribute("aria-label", "Remove " + item.name);
     remove.addEventListener("click", function(){
       media = media.filter(m => m.id !== item.id);
       renderMedia(); invalidate(); changed();
       el("media-input").focus();
     });
     row.appendChild(remove);
     list.appendChild(row);
   }
 }
 async function attach(files) {
   for (const file of files) {
     if (media.length >= MAX_MEDIA) { showMessage("At most ten photos or videos per post.", false); return; }
     if (file.size > MAX_MEDIA_BYTES) { showMessage(file.name + " is larger than 256 MiB.", false); continue; }
     showMessage("Keeping " + file.name + " on this computer…", true);
     try {
       const result = await upload(file);
       media.push(result.media);
       renderMedia(); invalidate(); changed();
       showMessage("Attached " + result.media.name + ". Preview to see its checks.", true);
     } catch(e) {
       showMessage(e.message || "The file could not be attached.", false);
     }
   }
 }
 function networkState(id) {
   const state = current.state;
   const found = state ? state.networks.find(n => n.id === id) : null;
   if (!found) return ["Not saved yet", "chip"];
   return found.state === "posted" ? ["Posted by you", "chip posted"] : ["Ready to post", "chip ready"];
 }
 function renderPreview(packages) {
   const box = el("results");
   box.replaceChildren();
   for(const item of packages) {
     const card = document.createElement("article");
     card.className = "review";
     const header = document.createElement("div");
     header.className = "review-head";
     header.appendChild(text("h3",item.label));
     const state = networkState(item.id);
     header.appendChild(text("span", state[0], state[1]));
     card.appendChild(header);
     if (item.human_only) {
       card.appendChild(text("p","You write this one yourself in the community. Only your private note is kept."));
     } else {
       if(item.title) {
         card.appendChild(text("strong","Title"));
         card.appendChild(text("pre",item.title));
       }
       const label = text("label","Caption for " + item.label);
       const editor = document.createElement("textarea");
       editor.id = "edit-" + item.id;
       editor.rows = 5;
       editor.value = item.text || "";
       label.setAttribute("for", editor.id);
       card.appendChild(label);
       card.appendChild(editor);
       editor.addEventListener("input",function(){
         edits.set(item.id, editor.value);
         previewValid = false;
         savedClean = false;
         el("save").disabled = true;
         el("update").disabled = true;
         showMessage("Edited " + item.label + ". Preview again to recheck every network.",false);
         changed();
       });
       const actions = document.createElement("div");
       actions.className = "actions";
       const copyNode = button("Copy caption","secondary");
       copyNode.setAttribute("aria-label", "Copy the caption for " + item.label);
       copyNode.addEventListener("click",function(){copy(editor.value,copyNode);});
       actions.appendChild(copyNode);
       card.appendChild(actions);
       if(item.shortened) card.appendChild(text("p","Shortened to fit " + item.label + ": read the ending before you copy it.","hint"));
       if(item.note) card.appendChild(text("p",item.note,"hint"));
     }
     if(item.media_checks && item.media_checks.length) {
       card.appendChild(text("strong","Photo and video checks:"));
       const list = document.createElement("ul");
       list.className = "media-checks";
       for(const line of item.media_checks) list.appendChild(text("li", line));
       card.appendChild(list);
     }
     if(item.warnings.length) {
       card.appendChild(text("strong","Before you post, check:"));
       const list = document.createElement("ul");
       for(const warning of item.warnings) list.appendChild(text("li", warning));
       card.appendChild(list);
     }
     box.appendChild(card);
   }
 }
 function lockBusy(state) {
   busy = state;
   el("preview").disabled = state;
   el("save").disabled = state || !previewValid;
   el("update").disabled = state || !previewValid || !current.package;
 }
 async function preview() {
   if (busy) return;
   lockBusy(true);
   showMessage("Checking your posts on this computer…",true);
   try {
     const result = await api("/api/preview",payload());
     previewData = result.platforms;
     previewValid = true;
     previewedKey = JSON.stringify(payload());
     renderPreview(previewData);
     showMessage("Ready to review. Nothing has been saved or sent.",true);
   } catch(e) {
     previewValid = false;
     el("results").replaceChildren(text("p","Fix the problem above and preview again.","no-preview"));
     showMessage(e.message || "Preview failed.",false);
   }
   lockBusy(false);
   progress();
   if (previewValid) el("review-title").scrollIntoView({block:"start", behavior:"smooth"});
 }
 async function save(mode) {
   if (!previewValid || busy) return;
   el("confirm-update").classList.add("hidden");
   lockBusy(true);
   try {
     const extra = mode === "update" ?
       {mode:"update", package:current.package, revision:current.revision, confirm:true} : {mode:"new"};
     const result = await api("/api/prepare", Object.assign(payload(), extra));
     current = {package:result.package.name, revision:result.package.revision, state:result.package};
     previewedKey = JSON.stringify(payload());
     updateEditing();
     renderPreview(previewData);
     previewValid = false;
     savedClean = true;
     scheduleSave();
     showMessage((result.updated ? "Updated: " : "Saved: ") + result.display_name + " (in " + result.folder + ")" +
       "\nNext: copy each caption above into its app and post it. Nothing was uploaded or published.", true,
       [{label:"Copy folder path", value:result.saved_to, aria:"Copy the folder path of " + result.display_name}]);
     announce((result.updated ? "Updated " : "Saved ") + result.display_name + ".");
     loadPackages();
   } catch(e) {
     showMessage(e.message || "Saving failed.",false);
   }
   lockBusy(false);
   el("save").disabled = true;
   el("update").disabled = true;
   progress();
 }
 function hasContent() {
   return Boolean(el("caption").value || selected.size || media.length || el("notes").value);
 }
 async function openPackage(pkg, node) {
   if (hasContent() && confirmOpen !== pkg.name) {
     confirmOpen = pkg.name;
     node.textContent = "Click again to replace the editor";
     showIn("pkg-status", "Opening a package replaces what is in the editor now. Click again to open it.", false);
     return;
   }
   confirmOpen = null;
   try {
     const result = await api("/api/package/open", {package:pkg.name});
     current = {package:result.package.name, revision:result.package.revision, state:result.package};
     applyEditor(result.editor, result.media);
     savedClean = true;
     updateEditing();
     setSaved("Draft saved", "ok");
     showIn("pkg-status", "", true);
     showMessage("Opened " + result.package.display_name + ". Preview again before you save it.", true);
     el("caption").focus();
     renderPackages(lastPackages);
     progress();
   } catch(e) {
     showIn("pkg-status", e.message || "The package could not be opened.", false);
   }
 }
 async function duplicate(pkg) {
   try {
     const result = await api("/api/package/duplicate", {package:pkg.name});
     showIn("pkg-status", "Duplicated as: " + result.package.display_name + " (in " + result.package.folder +
       "). Nothing was uploaded or published.", true);
     announce("Duplicated as " + result.package.display_name + ".");
     loadPackages();
   } catch(e) {
     showIn("pkg-status", e.message || "The package could not be duplicated.", false);
   }
 }
 // The link as the server will check it, or null: one http(s) link with a host, no spaces, at most 2048
 // characters. A bare "www.example.com/…" gets "https://" in front. The server checks it again.
 function normalizeLink(raw) {
   let value = (raw || "").trim();
   if (!value) return null;
   if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) value = "https://" + value.replace(/^\/+/, "");
   if (value.length > 2048 || /\s/.test(value)) return null;
   let parsed;
   try { parsed = new URL(value); } catch(e) { return null; }
   if ((parsed.protocol !== "https:" && parsed.protocol !== "http:") || parsed.hostname.indexOf(".") < 1) return null;
   return value;
 }
 function accountsFor(network) {
   const ledger = network.ledger || ledgerIds[network.id] || network.id;
   return accounts.filter(a => a.platform === ledger);
 }
 async function markPosted(pkg, network, fields) {
   const link = normalizeLink(fields.url.value);
   if (!link) {
     fields.url.setAttribute("aria-invalid", "true");
     fields.error.textContent = LINK_HELP;
     fields.error.classList.remove("hidden");
     fields.url.focus();
     return;
   }
   fields.url.value = link;
   fields.url.removeAttribute("aria-invalid");
   fields.error.classList.add("hidden");
   fields.record.disabled = true;
   try {
     const at = fields.when.value ? new Date(fields.when.value) : null;
     if (at && isNaN(at.getTime())) throw new Error("The posted time is not a valid date.");
     const body = {package:pkg.name, platform:network.id, url:link, posted_at: at ? at.toISOString() : null};
     if (fields.account && fields.account.value) body.account_id = fields.account.value;
     const result = await api("/api/mark-posted", body);
     if (current.package === pkg.name) current.state = result.package;
     const who = result.posted.account_id ? " (" + result.posted.account_id + ")" : "";
     showIn("pkg-status", "Recorded as posted by you on " + network.label + who + ".\nNext: when you have numbers, run " +
       result.next + "\n(replace N with the numbers the app shows; the time defaults to now)", true,
       [{label:"Copy command", value:result.next, aria:"Copy the metrics command for " + network.label}]);
     if (dashboardUrl) el("pkg-status").appendChild(resultsLink("See it in your results"));
     announce("Recorded as posted on " + network.label + ".");
     if (previewData.length) renderPreview(previewData);
     loadPackages();
     progress();
   } catch(e) {
     fields.record.disabled = false;
     fields.error.textContent = e.message || "Nothing was recorded.";
     fields.error.classList.remove("hidden");
   }
 }
 function networkRow(pkg, network) {
   const row = document.createElement("li");
   const line = document.createElement("div");
   line.className = "netrow";
   const who = document.createElement("span");
   who.className = "who";
   who.appendChild(text("strong", network.label));
   who.appendChild(network.state === "posted" ? text("span", "Posted by you", "chip posted") :
                                                 text("span", "Ready to post", "chip ready"));
   line.appendChild(who);
   row.appendChild(line);
   if (network.posted) {
     const info = text("p", "Post " + (network.posted.post_id || "") +
       (network.posted.account_id ? " · " + network.posted.account_id : "") + " · ", "hint");
     if (/^https?:\/\//i.test(network.posted.url || "")) {
       const link = text("a", network.posted.url);
       link.href = network.posted.url;
       link.target = "_blank";
       link.rel = "noopener noreferrer";
       info.appendChild(link);
     }
     row.appendChild(info);
     return row;
   }
   if (network.human_only) {
     row.appendChild(text("p", "You write and post this one yourself. To keep it in your history, run reachweaver posted LINK in the terminal after you post it.", "hint"));
     return row;
   }
   if (!network.measurable) {
     row.appendChild(text("p", "ReachWeaver cannot track results for this network yet, so there is nothing to mark.", "hint"));
     return row;
   }
   const open = button("Mark as posted", "small");
   open.setAttribute("aria-label", "Mark " + network.label + " as posted");
   const form = document.createElement("div");
   form.className = "markform hidden";
   const base = "mark-" + pkg.name + "-" + network.id;
   const urlLabel = text("label", "Link to your post on " + network.label);
   const url = document.createElement("input");
   url.type = "url"; url.id = base + "-url"; url.maxLength = 2048; url.placeholder = "https://";
   url.autocomplete = "off"; url.inputMode = "url";
   urlLabel.setAttribute("for", url.id);
   const error = text("p", "", "field-error hidden");
   error.id = base + "-error";
   url.setAttribute("aria-describedby", error.id);
   form.appendChild(urlLabel); form.appendChild(url); form.appendChild(error);
   const fields = {url:url, error:error, account:null, when:null, record:null};
   const mine = accountsFor(network);
   if (mine.length) {
     const accountLabel = text("label", "Which account? (optional)");
     const select = document.createElement("select");
     select.id = base + "-account";
     accountLabel.setAttribute("for", select.id);
     select.appendChild(text("option", "Not sure / skip"));
     select.firstChild.value = "";
     for (const a of mine) {
       const option = text("option", a.name ? a.id + " · " + a.name : a.id);
       option.value = a.id;
       select.appendChild(option);
     }
     // Exactly one of your accounts on this network: it is chosen for you (you can still change it or skip).
     if (mine.length === 1) select.value = mine[0].id;
     form.appendChild(accountLabel); form.appendChild(select);
     fields.account = select;
   }
   const whenLabel = text("label", "When you posted it (optional, your local time)");
   const when = document.createElement("input");
   when.type = "datetime-local"; when.id = base + "-at";
   whenLabel.setAttribute("for", when.id);
   form.appendChild(whenLabel); form.appendChild(when);
   fields.when = when;
   const actions = document.createElement("div");
   actions.className = "actions";
   const record = button("Record as posted", "primary");
   const cancel = button("Cancel", "secondary");
   fields.record = record;
   actions.appendChild(record); actions.appendChild(cancel);
   form.appendChild(text("p", "Adds it to your history as posted by you, so you can track it. Nothing is posted or sent.", "hint"));
   form.appendChild(actions);
   url.addEventListener("blur", function(){
     const fixed = normalizeLink(url.value);
     if (fixed && fixed !== url.value.trim()) url.value = fixed;
   });
   url.addEventListener("input", function(){
     if (url.getAttribute("aria-invalid")) { url.removeAttribute("aria-invalid"); error.classList.add("hidden"); }
   });
   url.addEventListener("keydown", function(event){ if (event.key === "Enter") markPosted(pkg, network, fields); });
   open.addEventListener("click", function(){ form.classList.remove("hidden"); open.classList.add("hidden"); url.focus(); });
   cancel.addEventListener("click", function(){ form.classList.add("hidden"); open.classList.remove("hidden"); open.focus(); });
   record.addEventListener("click", function(){ markPosted(pkg, network, fields); });
   line.appendChild(open);
   row.appendChild(form);
   return row;
 }
 function renderPackages(list) {
   lastPackages = list;
   const box = el("packages");
   box.replaceChildren();
   if (!list.length) {
     box.appendChild(text("p", "No saved packages yet. Save one above and it appears here.", "no-preview"));
     return;
   }
   for (const pkg of list) {
     const card = document.createElement("article");
     card.className = "review";
     const head = document.createElement("div");
     head.className = "review-head";
     head.appendChild(text("h3", pkg.display_name || pkg.created || pkg.name));
     if (pkg.name === current.package) head.appendChild(text("span", "In the editor", "chip here"));
     card.appendChild(head);
     const count = pkg.networks.length;
     card.appendChild(text("small", count + (count === 1 ? " network" : " networks") +
       (pkg.media.length ? " · " + pkg.media.length + (pkg.media.length === 1 ? " photo or video" : " photos or videos") : "")));
     const list2 = document.createElement("ul");
     list2.className = "netlist";
     for (const network of pkg.networks) list2.appendChild(networkRow(pkg, network));
     card.appendChild(list2);
     const actions = document.createElement("div");
     actions.className = "actions";
     const open = button("Open in editor", "secondary");
     open.addEventListener("click", function(){ openPackage(pkg, open); });
     const copyPkg = button("Duplicate as new", "secondary");
     copyPkg.addEventListener("click", function(){ duplicate(pkg); });
     actions.appendChild(open); actions.appendChild(copyPkg);
     card.appendChild(actions);
     card.appendChild(text("p", "Folder: " + (pkg.folder || pkg.name), "hint"));
     box.appendChild(card);
   }
 }
 async function loadPackages() {
   try {
     const data = await api("/api/packages", {});
     renderPackages(data.packages);
   } catch(e) {
     el("packages").replaceChildren(text("p", e.message || "Saved packages could not be listed.", "hint"));
   }
 }
 el("caption").addEventListener("input",function(){
   edits.clear();
   counter();
   invalidate();
   changed();
 });
 el("title").addEventListener("input",function(){ invalidate(); changed(); });
 el("notes").addEventListener("input",function(){ invalidate(); changed(); });
 el("search").addEventListener("input",renderNetworks);
 el("popular").addEventListener("click",function(){
   filter="popular";el("popular").setAttribute("aria-pressed","true");
   el("all").setAttribute("aria-pressed","false");renderNetworks();
 });
 el("all").addEventListener("click",function(){
   filter="all";el("all").setAttribute("aria-pressed","true");
   el("popular").setAttribute("aria-pressed","false");renderNetworks();
 });
 el("preview").addEventListener("click",preview);
 el("save").addEventListener("click",function(){ save("new"); });
 el("update").addEventListener("click",function(){
   if (!previewValid || !current.package) return;
   el("confirm-text").textContent = "Replace the saved files of “" +
     ((current.state && current.state.display_name) || current.package) +
     "” with what is in the editor? A package with a network marked as posted cannot be changed.";
   el("confirm-update").classList.remove("hidden");
   el("confirm-yes").focus();
 });
 el("confirm-yes").addEventListener("click",function(){ save("update"); });
 el("confirm-no").addEventListener("click",function(){ el("confirm-update").classList.add("hidden"); el("update").focus(); });
 el("undo").addEventListener("click",undo);
 el("refresh").addEventListener("click",loadPackages);
 el("media-input").addEventListener("change",function(){
   const files = Array.from(el("media-input").files || []);
   el("media-input").value = "";
   attach(files);
 });
 el("reset").addEventListener("click",function(){
   selected.clear();
   edits.clear();
   media = [];
   current = {package:null, revision:null, state:null};
   previewedKey = null;
   el("caption").value="";el("title").value="";el("notes").value="";
   counter(); conditionalFields(); invalidate(); renderNetworks(); renderMedia(); updateEditing();
   undoStack.length = 0; stable = snapshot(); el("undo").disabled = true;
   api("/api/draft/clear", {}).then(function(){ setSaved("Cleared", "ok"); }).catch(function(e){
     setSaved("Not saved", "bad"); showMessage(e.message || "The draft could not be cleared.", false);
   });
   renderPackages(lastPackages);
   progress();
 });
 resultsLine("results-top");
 resultsLine("results-foot");
 progress();
 if(!/^[A-Za-z0-9_-]{30,}$/.test(sessionToken)) {
   showMessage("This local page needs a session link. Open the complete URL printed in your terminal.",false);
   el("preview").disabled=true;
   el("networks").replaceChildren(text("p","Missing local session link.","hint"));
   el("packages").replaceChildren(text("p","Missing local session link.","hint"));
   return;
 }
 let pendingOffer = null;
 function setCaption(value) {
   el("caption").value = value;
   edits.clear();
   counter(); invalidate(); changed();
 }
 // Text from an idea (studio's "Write a post from this idea") or from `share --web --text/--idea`. An empty editor
 // takes it at once; an unsaved draft is never replaced without your click, and Undo brings it back.
 function useText(value, label) {
   if (!hasContent() && !current.package) {
     setCaption(value);
     showMessage("Started from " + label + ". Pick your networks next. Nothing is posted for you.", true);
     el("caption").focus();
     return;
   }
   const box = el("status");
   box.replaceChildren(document.createTextNode("The editor already holds your post, so nothing was replaced. Use " + label +
     " as the caption instead?"));
   box.className = "alert";
   const row = document.createElement("div");
   const yes = button("Use it as my caption", "small");
   const no = button("Keep my draft", "small");
   yes.addEventListener("click", function(){
     setCaption(value);
     showMessage("Now using " + label + " as your caption. Undo brings the previous caption back.", true);
     el("caption").focus();
   });
   no.addEventListener("click", function(){ showMessage("Kept your draft.", true); el("caption").focus(); });
   row.appendChild(yes); row.appendChild(no);
   box.appendChild(row);
 }
 function startFromRequest() {
   if (pendingOffer) { useText(pendingOffer.text, pendingOffer.label); return; }
   if (!ideaWanted) return;
   api("/api/idea", {idea:ideaWanted}).then(function(data){
     useText(data.idea.text, "idea " + data.idea.id);
   }).catch(function(e){ showMessage(e.message || "The idea could not be read.", false); });
 }
 api("/api/catalog").then(function(data){
   catalog = data.platforms;
   if (typeof data.dashboard === "string" && DASHBOARD_URL.test(data.dashboard)) {
     dashboardUrl = data.dashboard;
     resultsLine("results-top");
     resultsLine("results-foot");
   }
   conditionalFields();
   renderNetworks();
   return api("/api/draft/load", {});
 }).then(function(data){
   if (data.draft) {
     current = {package:data.draft.package, revision:data.draft.revision, state:null};
     applyEditor(data.draft, data.media);
     setSaved("Draft saved", "ok");
   } else {
     stable = snapshot();
   }
   updateEditing();
   ready = true;
   showMessage(data.draft ? "Your draft is back. No social network is connected." :
               "Ready. No social network is connected; nothing is posted for you.",true);
   if (data.offer && typeof data.offer.text === "string") pendingOffer = {text:data.offer.text, label:String(data.offer.label || "your text")};
   return api("/api/accounts", {}).catch(function(){ return {accounts:[], ledger_ids:{}}; });
 }).then(function(data){
   accounts = data.accounts || [];
   ledgerIds = data.ledger_ids || {};
   return api("/api/packages", {});
 }).then(function(data){
   if (current.package) current.state = data.packages.find(p => p.name === current.package) || null;
   if (current.package && !current.state) current = {package:null, revision:null, state:null};
   updateEditing();
   renderPackages(data.packages);
   progress();
   startFromRequest();
 }).catch(function(err){
   showMessage(err.message || "Unable to open the local composer.",false);
   el("preview").disabled=true;
 });
})();
</script>
</body>
</html>"""
