"""Self-contained browser UI for the manual Share Composer.

No external CSS, JavaScript, fonts, images, analytics or network requests.
The browser only talks to the loopback ReachWeaver ShareServer. All user
captions are inserted with textContent, never HTML markup.
"""
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Share Composer · ReachWeaver</title>
<style nonce="__CSP_NONCE__">
:root {
  color-scheme: light;
  --ink: #12273a; --muted: #586c7b; --sky: #005cb9;
  --deep: #123d65; --line: #d9e4ef; --page: #f5f8fc;
  --white: #ffffff; --soft: #eaf3ff; --focus: #1277ce;
  --radius: 18px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); line-height: 1.5; }
main { max-width: 1160px; margin: auto; padding: 30px 24px 80px; }
header { background: linear-gradient(120deg,#0d355e,#125a7a); color: white; }
.head { max-width:1160px; margin:auto; padding: 30px 24px 38px; }
.wordmark { font-size: 15px; font-weight: 760; letter-spacing: .1px; display:flex; gap:9px; align-items:center; }
.mark { background: #b8f7de; color:#174957; width:28px; height:28px; border-radius:9px; display:grid; place-items:center; font-weight:900; }
.eyebrow { font-size:12px; letter-spacing: 2px; font-weight:750; text-transform:uppercase; opacity: .82; margin-top:30px; }
h1 { margin:7px 0 9px; font-size: clamp(30px,4vw,48px); letter-spacing: -.035em; line-height: 1.08; }
.head p { max-width: 620px; font-size: 16px; opacity:.9; margin-bottom:0; }
.security { font-size:12px; margin-top:22px; display:inline-block; padding:8px 12px; background: #ffffff1a; border: 1px solid #ffffff44; border-radius: 20px;}
.steps { display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin: 0 0 26px; padding:0; list-style:none;}
.steps li { font-size:12px; font-weight:720; color:var(--muted); background:#e5edf5; padding:7px 12px; border-radius: 999px;}
.steps li:first-child { background:var(--soft); color:var(--deep); }
.layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(315px,.92fr); align-items:start; gap:22px; }
.panel { background:var(--white); border: 1px solid var(--line); border-radius:var(--radius); box-shadow:0 8px 28px #133b6010; padding:24px; }
h2 { font-size:21px; margin:0 0 5px; letter-spacing:-.025em; }
h3 { font-size:17px; margin:0; }
.explain { color:var(--muted); font-size:14px; margin: 0 0 21px; }
label { display:block; font-weight:720; font-size:14px; margin: 16px 0 7px; }
textarea,input[type=text],input[type=search] { display:block; width:100%; padding:13px 14px; border:1px solid #becddd; border-radius: 12px; background:white; color:var(--ink); font:inherit; resize:vertical; }
textarea { min-height: 215px; line-height: 1.55; }
input:focus-visible,textarea:focus-visible,button:focus-visible { outline:3px solid #4aa6fb; outline-offset: 2px; }
.hint { font-size:12px; color:var(--muted); margin-top:7px; }
.field { margin-top:17px; }
.hidden { display: none !important; }
.network-options { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:9px; max-height:330px; overflow:auto; padding:2px 4px 3px 2px; }
.network { text-align:left; display:flex; flex-direction:column; gap:2px; min-height:64px; border:1px solid #ccd8e6; background:#fff; border-radius:12px; padding:10px 11px; cursor:pointer; color:var(--ink); }
.network strong { font-size:14px; overflow-wrap:anywhere; }
.network span { font-size:11px; color:var(--muted); }
.network[aria-pressed=true] { background:#e8f3ff; border:2px solid var(--sky); padding:9px 10px; }
.network[aria-pressed=true] strong::before { content:"✓ "; color:var(--sky); }
.count { display:inline-block; color:var(--deep); background:var(--soft); border-radius:20px; font-size:12px; font-weight:750; padding:4px 10px; }
.filter-row { display:flex; gap:8px; margin-bottom:11px; align-items:center;}
.filter-row button { font:inherit; padding:8px 12px; background:white; border:1px solid var(--line); border-radius:10px; color:var(--ink); cursor:pointer; font-size:12px; }
.filter-row button[aria-pressed=true] { color:white; background:var(--deep); }
.actions { display:flex; gap:12px; justify-content:flex-start; margin-top:25px; flex-wrap:wrap; }
button { font-family:inherit; }
.primary { background:var(--sky); color:white; border:1px solid var(--sky); border-radius:12px; padding:12px 21px; cursor:pointer; font-size:14px; font-weight:750; }
.primary:hover { background:#054a94; }
.primary:disabled { opacity:.42; cursor:not-allowed; }
.secondary { background:white; color:var(--deep); border:1px solid #bccfdf; border-radius:12px; padding:11px 18px; cursor:pointer; font-size:14px; font-weight:720; }
.alert { border-radius:12px; border:1px solid #e0bf8b; background:#fff9ea; padding:12px 14px; margin-top:18px; color:#684c15; font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; }
.alert.ok { border-color:#a6dcca; background:#edf9f4; color:#1d5549; }
.wide { margin-top:22px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:13px; margin-top:15px; }
.review { border:1px solid var(--line); border-radius:13px; padding:17px; background:#fcfdff; min-width:0; }
.review-head { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap;}
.review pre { white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; background:#f0f5fa; color:#243b4c; padding:13px; border-radius:9px; font-family:inherit; font-size:13px; max-height: 230px; overflow:auto;}
.review ul { color:#6b5416; font-size:12px; padding-left:21px; }
.review small { color:var(--muted); }
.foot { font-size:12px; color:var(--muted); margin:20px 0 0; }
.no-preview { padding:30px 0; color:var(--muted); text-align:center; font-size:14px; }
.site-status { display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:12px; font-weight:600; color:var(--muted);}
.dot { width:9px; height:9px; border-radius:100%; background:#1aa279; }
@media (max-width:820px) { main {padding:23px 14px 50px;} .head {padding:25px 18px 34px;} .layout {grid-template-columns:1fr;} .panel {padding:19px;} .network-options{ max-height:390px;}}
@media (max-width:400px) { .network-options {grid-template-columns:1fr;} }
@media (prefers-reduced-motion: no-preference) { button {transition: background-color .15s, border-color .15s;} }
</style>
</head>
<body>
<header>
<div class="head">
  <div class="wordmark"><span class="mark" aria-hidden="true">✦</span> ReachWeaver</div>
  <div class="eyebrow">Your local sharing workspace</div>
  <h1>One story.<br>Many places.</h1>
  <p>Write once, select the networks you care about, then review and save the right posting files. You publish from each social app yourself.</p>
  <span class="security">Private &amp; local · No login · No automatic posting</span>
</div>
</header>
<main>
<ol class="steps" aria-label="Steps">
 <li>01 · Write</li><li>02 · Choose</li><li>03 · Preview</li><li>04 · Share manually</li>
</ol>
<div class="layout">
<section class="panel" aria-labelledby="write-title">
 <h2 id="write-title">Your post</h2>
 <p class="explain">What would you like to share today? You can edit each caption later in its saved folder.</p>
 <label for="caption">Caption or description</label>
 <textarea id="caption" placeholder="Tell people what you created, why it matters, and what they can do next..." maxlength="64000" spellcheck="true"></textarea>
 <p class="hint" id="counter">0 characters · Private text, never uploaded to a social network</p>
 <div id="video-title" class="field hidden">
   <label for="title">YouTube video title</label>
   <input id="title" type="text" maxlength="200" placeholder="A clear and descriptive title">
   <p class="hint">Required for YouTube. You'll upload the video in YouTube Studio.</p>
 </div>
 <div id="community-notes" class="field hidden">
   <label for="notes">Your personal community note</label>
   <input id="notes" type="text" maxlength="200" placeholder="E.g. Read the group rules and add a useful personal answer">
   <p class="hint">Community posts are human-written. ReachWeaver saves notes, not a prepared promotional post.</p>
 </div>
 <div class="actions">
   <button id="preview" class="primary" type="button">Preview my posts</button>
   <button id="reset" class="secondary" type="button">Clear form</button>
 </div>
</section>
<section class="panel" aria-labelledby="networks-title">
 <h2 id="networks-title">Where to share?</h2>
 <p class="explain">Select up to 10 networks. The composer shows manual destinations only, never unapproved auto-posting connections.</p>
 <label for="search">Find a platform</label>
 <input id="search" type="search" placeholder="Search Instagram, Misskey, YouTube..." autocomplete="off">
 <div class="filter-row" role="group" aria-label="Platform filter">
   <button id="popular" type="button" aria-pressed="true">Popular</button>
   <button id="all" type="button" aria-pressed="false">All manual networks</button>
   <span class="count" id="selection-count">0 selected</span>
 </div>
 <div class="network-options" id="networks" aria-label="Available networks">
   <p class="hint">Loading your offline network catalog…</p>
 </div>
 <p class="hint">No passwords or tokens are needed. Each post remains your decision.</p>
</section>
</div>
<section class="panel wide" aria-labelledby="review-title">
  <div class="review-head">
    <div>
      <h2 id="review-title">Your previews</h2>
      <p class="explain">Every network receives its own checklist. Warnings are shown before files are saved.</p>
    </div>
    <button id="save" class="primary" type="button" disabled>Save posting files</button>
  </div>
  <div id="status" role="status" aria-live="polite"></div>
  <div id="results" class="cards"><p class="no-preview">Select networks and choose “Preview my posts” to get started.</p></div>
  <p class="foot">Saving creates private files on this computer, not online posts. You still choose the account, media, audience and publication time inside each platform.</p>
</section>
</main>
<noscript><p>This local composer needs JavaScript to display previews. The command-line Share flow works without JavaScript.</p></noscript>
<script nonce="__CSP_NONCE__">
(function () {
 "use strict";
 const sessionToken = window.location.hash.slice(1);
 const el = id => document.getElementById(id);
 const selected = new Set();
 let catalog = [];
 let filter = "popular";
 let previewValid = false;
 let previewData = [];
 let busy = false;
 const showMessage = function(message, isSuccess) {
   const target = el("status");
   target.textContent = message || "";
   target.className = message ? (isSuccess ? "alert ok" : "alert") : "";
 };
 const text = function(tag, value, className) {
   const node = document.createElement(tag);
   node.textContent = value;
   if (className) node.className = className;
   return node;
 };
 async function api(path, payload) {
   const headers = {"X-ReachWeaver-Token":sessionToken};
   if (payload !== undefined) headers["Content-Type"] = "application/json";
   const res = await fetch(path, {
     method: payload === undefined ? "GET" : "POST",
     headers:headers,
     body:payload === undefined ? undefined : JSON.stringify(payload),
     credentials:"omit", cache:"no-store", redirect:"error"
   });
   const data = await res.json();
   if (!res.ok) throw new Error((data.errors || ["Request failed."]).join("\n"));
   return data;
 }
 function payload() {
   return {
     platforms:Array.from(selected),
     text:el("caption").value || null,
     title:selected.has("youtube") ? el("title").value : null,
     note:catalog.some(p => selected.has(p.id) && p.human_only) ? el("notes").value : ""
   };
 }
 function invalidate() {
   previewValid = false;
   el("save").disabled = true;
   previewData = [];
   el("results").replaceChildren(text("p", "Your draft changed. Preview again before saving.", "no-preview"));
   showMessage("",false);
 }
 function conditionalFields() {
   const human = catalog.some(p => selected.has(p.id) && p.human_only);
   el("video-title").classList.toggle("hidden",!selected.has("youtube"));
   el("community-notes").classList.toggle("hidden",!human);
   el("selection-count").textContent = selected.size + " selected";
 }
 function renderNetworks() {
   const q = el("search").value.trim().toLowerCase();
   const choices = catalog.filter(p =>
      (filter === "all" || p.featured || selected.has(p.id)) &&
      (p.label.toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
   );
   const items = el("networks");
   items.replaceChildren();
   if (!choices.length) {items.appendChild(text("p","No matching networks. Try the All filter.","hint"));return;}
   for(const p of choices) {
     const button = document.createElement("button");
     button.type = "button";
     button.className = "network";
     button.setAttribute("aria-pressed", selected.has(p.id) ? "true" : "false");
     button.appendChild(text("strong", p.label));
     button.appendChild(text("span",p.human_only ? "You write the final post · Notes only" : "Manual posting package"));
     button.addEventListener("click",function(){
       if(selected.has(p.id)) selected.delete(p.id);
       else if(selected.size < 10) selected.add(p.id);
       else {showMessage("You can select a maximum of ten networks at once.",false);return;}
       conditionalFields();
       invalidate();
       renderNetworks();
     });
     items.appendChild(button);
   }
 }
 async function copy(value, button) {
   try {
     await navigator.clipboard.writeText(value);
     button.textContent = "Copied";
   } catch(e) {
     showMessage("Clipboard access was blocked. Select the text in the preview to copy it.",false);
   }
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
     header.appendChild(text("small",item.human_only ? "Human-written" : "Ready for review"));
     card.appendChild(header);
     if (item.human_only) {
       card.appendChild(text("p","Only personal notes are prepared for this community. You must write your own post in its app."));
     } else {
       if(item.title) {
         card.appendChild(text("strong","Title"));
         card.appendChild(text("pre",item.title));
       }
       card.appendChild(text("pre",item.text || ""));
       const button = text("button","Copy this caption","secondary");
       button.type = "button";
       button.addEventListener("click",function(){copy(item.text || "",button);});
       card.appendChild(button);
     }
     if(item.warnings.length) {
       card.appendChild(text("strong","Before sharing, check:"));
       const list = document.createElement("ul");
       for(const warning of item.warnings) list.appendChild(text("li", warning));
       card.appendChild(list);
     }
     card.appendChild(text("p","Not published. Open the platform, confirm all options and publish there yourself.","hint"));
     box.appendChild(card);
   }
 }
 function lockBusy(state) {
   busy = state;
   el("preview").disabled = state;
   el("save").disabled = state || !previewValid;
 }
 async function preview() {
   if (busy) return;
   lockBusy(true);
   showMessage("Checking your posts locally...",true);
   try {
     const result = await api("/api/preview",payload());
     previewData = result.platforms;
     previewValid = true;
     renderPreview(previewData);
     showMessage("Ready for your review. No files have been saved or sent.",true);
   } catch(e) {
     previewValid = false;
     el("results").replaceChildren(text("p","Fix the issue above and preview again.","no-preview"));
     showMessage(e.message || "Preview failed.",false);
   }
   lockBusy(false);
 }
 async function save() {
   if (!previewValid || busy) return;
   lockBusy(true);
   try {
     const result = await api("/api/prepare",payload());
     previewValid = false;
     showMessage("Saved locally: " + result.saved_to +
       "\nOpen OPEN-FIRST.md in that folder. Nothing was uploaded or published.",true);
   } catch(e) {
     showMessage(e.message || "Saving failed.",false);
   }
   lockBusy(false);
   el("save").disabled = true;
 }
 el("caption").addEventListener("input",function(){
   el("counter").textContent = el("caption").value.length + " characters · No social upload";
   invalidate();
 });
 el("title").addEventListener("input",invalidate);
 el("notes").addEventListener("input",invalidate);
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
 el("save").addEventListener("click",save);
 el("reset").addEventListener("click",function(){
   selected.clear();
   el("caption").value="";el("title").value="";el("notes").value="";
   el("counter").textContent="0 characters · No social upload";
   conditionalFields();invalidate();renderNetworks();
 });
 if(!/^[A-Za-z0-9_-]{30,}$/.test(sessionToken)) {
   showMessage("This local page needs a session link. Open the complete URL printed in your terminal.",false);
   el("preview").disabled=true;
   el("networks").replaceChildren(text("p","Missing local session link.","hint"));
   return;
 }
 api("/api/catalog").then(function(data){
   catalog = data.platforms;
   conditionalFields();
   renderNetworks();
   showMessage("Local composer ready. No social network is connected.",true);
 }).catch(function(err){
   showMessage(err.message || "Unable to open the local composer.",false);
   el("preview").disabled=true;
 });
})();
</script>
</body>
</html>"""
