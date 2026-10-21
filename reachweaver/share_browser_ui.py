"""Self-contained browser UI for the manual Share Composer.

No external CSS, JavaScript, fonts, images, analytics or network requests.
The browser only talks to the loopback ReachWeaver ShareServer. All user
captions are inserted with textContent, never HTML markup. There is no
Publish, approve or schedule control: "Mark as posted" only records a post
the owner already published by hand.
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
.panel { background:var(--white); border: 1px solid var(--line); border-radius:var(--radius); box-shadow:0 8px 28px #133b6010; padding:24px; min-width:0; }
h2 { font-size:21px; margin:0 0 5px; letter-spacing:-.025em; }
h3 { font-size:17px; margin:0; overflow-wrap:anywhere; }
.explain { color:var(--muted); font-size:14px; margin: 0 0 21px; }
label { display:block; font-weight:720; font-size:14px; margin: 16px 0 7px; }
textarea,input[type=text],input[type=search],input[type=url],input[type=datetime-local] { display:block; width:100%; padding:13px 14px; border:1px solid #becddd; border-radius: 12px; background:white; color:var(--ink); font:inherit; resize:vertical; }
input[type=file] { display:block; max-width:100%; font:inherit; font-size:13px; }
textarea { min-height: 215px; line-height: 1.55; }
input:focus-visible,textarea:focus-visible,button:focus-visible,a:focus-visible { outline:3px solid #4aa6fb; outline-offset: 2px; }
.hint { font-size:12px; color:var(--muted); margin-top:7px; overflow-wrap:anywhere; }
.field { margin-top:17px; }
.hidden { display: none !important; }
.docbar { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center; justify-content:space-between; margin:0 0 14px; padding:10px 12px; background:#f0f5fa; border-radius:12px; font-size:13px; }
.docbar #editing { font-weight:700; color:var(--deep); overflow-wrap:anywhere; min-width:0; }
.docbar .tools { display:flex; gap:8px; align-items:center; }
.saved { font-size:12px; font-weight:700; padding:3px 9px; border-radius:20px; background:#e5edf5; color:var(--muted); }
.saved.ok { background:#e3f6ee; color:#1d5549; }
.saved.bad { background:#fff1dc; color:#684c15; }
.small { font:inherit; font-size:12px; font-weight:700; padding:6px 11px; border-radius:10px; border:1px solid #bccfdf; background:white; color:var(--deep); cursor:pointer; }
.small:disabled { opacity:.45; cursor:not-allowed; }
.network-options { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:9px; max-height:330px; overflow:auto; padding:2px 4px 3px 2px; }
.network { text-align:left; display:flex; flex-direction:column; gap:2px; min-height:64px; border:1px solid #ccd8e6; background:#fff; border-radius:12px; padding:10px 11px; cursor:pointer; color:var(--ink); }
.network strong { font-size:14px; overflow-wrap:anywhere; }
.network span { font-size:11px; color:var(--muted); }
.network[aria-pressed=true] { background:#e8f3ff; border:2px solid var(--sky); padding:9px 10px; }
.network[aria-pressed=true] strong::before { content:"✓ "; color:var(--sky); }
.count { display:inline-block; color:var(--deep); background:var(--soft); border-radius:20px; font-size:12px; font-weight:750; padding:4px 10px; }
.filter-row { display:flex; gap:8px; margin-bottom:11px; align-items:center; flex-wrap:wrap; }
.filter-row button { font:inherit; padding:8px 12px; background:white; border:1px solid var(--line); border-radius:10px; color:var(--ink); cursor:pointer; font-size:12px; }
.filter-row button[aria-pressed=true] { color:white; background:var(--deep); }
.actions { display:flex; gap:12px; justify-content:flex-start; margin-top:25px; flex-wrap:wrap; }
button { font-family:inherit; }
.primary { background:var(--sky); color:white; border:1px solid var(--sky); border-radius:12px; padding:12px 21px; cursor:pointer; font-size:14px; font-weight:750; }
.primary:hover { background:#054a94; }
.primary:disabled,.secondary:disabled { opacity:.42; cursor:not-allowed; }
.secondary { background:white; color:var(--deep); border:1px solid #bccfdf; border-radius:12px; padding:11px 18px; cursor:pointer; font-size:14px; font-weight:720; }
.alert { border-radius:12px; border:1px solid #e0bf8b; background:#fff9ea; padding:12px 14px; margin-top:18px; color:#684c15; font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; }
.alert.ok { border-color:#a6dcca; background:#edf9f4; color:#1d5549; }
.wide { margin-top:22px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr)); gap:13px; margin-top:15px; }
.review { border:1px solid var(--line); border-radius:13px; padding:17px; background:#fcfdff; min-width:0; }
.review-head { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap;}
.save-buttons { display:flex; gap:10px; flex-wrap:wrap; }
.review pre { white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; background:#f0f5fa; color:#243b4c; padding:13px; border-radius:9px; font-family:inherit; font-size:13px; max-height: 230px; overflow:auto;}
.review ul { color:#6b5416; font-size:12px; padding-left:21px; overflow-wrap:anywhere; }
.review ul.media-checks { color:#243b4c; }
.review small { color:var(--muted); }
.chip { display:inline-block; font-size:11px; font-weight:760; padding:3px 9px; border-radius:20px; background:#e5edf5; color:var(--muted); white-space:nowrap; }
.chip.exported { background:#e8f3ff; color:var(--deep); }
.chip.posted { background:#e3f6ee; color:#1d5549; }
.confirm { margin-top:14px; padding:12px 14px; border:1px solid #e0bf8b; background:#fff9ea; border-radius:12px; font-size:13px; color:#684c15; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.confirm span { flex:1 1 220px; }
.media-list { list-style:none; padding:0; margin:10px 0 0; display:flex; flex-direction:column; gap:6px; }
.media-list li { display:flex; justify-content:space-between; gap:10px; align-items:center; background:#f0f5fa; border-radius:10px; padding:7px 10px; font-size:13px; min-width:0; }
.media-list li span { overflow-wrap:anywhere; min-width:0; }
.netlist { list-style:none; padding:0; margin:12px 0 0; display:flex; flex-direction:column; gap:10px; }
.netlist li { border-top:1px solid var(--line); padding-top:10px; color:var(--ink); font-size:13px; }
.netrow { display:flex; justify-content:space-between; gap:8px; align-items:center; flex-wrap:wrap; }
.netlist a { color:var(--sky); overflow-wrap:anywhere; }
.markform { margin-top:8px; }
.markform label { margin:10px 0 5px; font-size:13px; }
.markform input { padding:9px 11px; }
.foot { font-size:12px; color:var(--muted); margin:20px 0 0; }
.no-preview { padding:30px 0; color:var(--muted); text-align:center; font-size:14px; }
@media (max-width:820px) { main {padding:23px 14px 50px;} .head {padding:25px 18px 34px;} .layout {grid-template-columns:1fr;} .panel {padding:19px;} .network-options{ max-height:390px;}}
@media (max-width:400px) { .network-options {grid-template-columns:1fr;} .review { padding:13px; } }
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
 <li>01 · Write</li><li>02 · Choose</li><li>03 · Preview</li><li>04 · Save</li><li>05 · Share manually</li><li>06 · Mark as posted</li>
</ol>
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
 <p class="explain">What would you like to share today? Your draft is kept on this computer as you type.</p>
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
 <div class="field">
   <label for="media-input">Photos or videos (optional)</label>
   <input id="media-input" type="file" multiple accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.webm">
   <ul id="media-list" class="media-list" aria-label="Attached files"></ul>
   <p class="hint">Kept privately on this computer and copied into the package folder when you save; never uploaded to a network. Up to 10 files, 256 MiB each. Each file's header is checked offline in the preview.</p>
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
      <p class="explain">Tailor each caption for its network, then preview again to recheck all limits before saving.</p>
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
  <div id="results" class="cards"><p class="no-preview">Select networks and choose “Preview my posts” to get started.</p></div>
  <p class="foot">Saving creates private files on this computer, not online posts. You still choose the account, media, audience and publication time inside each platform.</p>
</section>
<section class="panel wide" aria-labelledby="packages-title">
  <div class="review-head">
    <div>
      <h2 id="packages-title">Saved packages</h2>
      <p class="explain">Open a package to edit it again, duplicate it, or record a post you published by hand.</p>
    </div>
    <button id="refresh" class="secondary" type="button">Refresh</button>
  </div>
  <div id="pkg-status" role="status" aria-live="polite"></div>
  <div id="packages" class="cards"><p class="no-preview">Loading your saved packages…</p></div>
  <p class="foot">“Mark as posted” records a post you already published yourself so you can measure it. It never posts, schedules or sends anything.</p>
</section>
</main>
<noscript><p>This local composer needs JavaScript to display previews. The command-line Share flow works without JavaScript.</p></noscript>
<script nonce="__CSP_NONCE__">
(function () {
 "use strict";
 const sessionToken = window.location.hash.slice(1);
 const el = id => document.getElementById(id);
 const MAX_MEDIA = 10;
 const MAX_MEDIA_BYTES = 256 * 1024 * 1024;
 const selected = new Set();
 const edits = new Map();
 let media = [];
 let current = {package:null, revision:null, state:null};
 let catalog = [];
 let filter = "popular";
 let previewValid = false;
 let previewData = [];
 let busy = false;
 let ready = false;
 let saveTimer = null;
 const undoStack = [];
 let stable = null;
 let lastChange = 0;
 let confirmOpen = null;
 let lastPackages = [];
 const showIn = function(id, message, isSuccess) {
   const target = el(id);
   target.textContent = message || "";
   target.className = message ? (isSuccess ? "alert ok" : "alert") : "";
 };
 const showMessage = function(message, isSuccess) { showIn("status", message, isSuccess); };
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
 function payload() {
   return {
     platforms:Array.from(selected),
     text:el("caption").value || null,
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
 function setSaved(label, kind) {
   const node = el("saved");
   node.textContent = label;
   node.className = "saved" + (kind ? " " + kind : "");
 }
 function saveDraft() {
   saveTimer = null;
   api("/api/draft/save", editorState()).then(function(){
     setSaved("Saved", "ok");
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
 }
 function counter() {
   el("counter").textContent = el("caption").value.length + " characters · No social upload";
 }
 function invalidate() {
   previewValid = false;
   el("save").disabled = true;
   el("update").disabled = true;
   el("confirm-update").classList.add("hidden");
   previewData = [];
   el("results").replaceChildren(text("p", "Your draft changed. Preview again before saving.", "no-preview"));
   showMessage("",false);
 }
 function conditionalFields() {
   el("video-title").classList.toggle("hidden",!selected.has("youtube"));
   el("community-notes").classList.toggle("hidden",!humanSelected());
   el("selection-count").textContent = selected.size + " selected";
 }
 function updateEditing() {
   const state = current.state;
   el("editing").textContent = current.package ?
     "Editing saved package " + ((state && state.created) || "") + " · " + current.package :
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
   if (!choices.length) {items.appendChild(text("p","No matching networks. Try the All filter.","hint"));return;}
   for(const p of choices) {
     const node = button("", "network");
     node.setAttribute("aria-pressed", selected.has(p.id) ? "true" : "false");
     node.appendChild(text("strong", p.label));
     node.appendChild(text("span",p.human_only ? "You write the final post · Notes only" :
       p.api_capable ? "Copy and post yourself · or connect it to send from ReachWeaver" : "Manual posting package"));
     node.addEventListener("click",function(){
       if(selected.has(p.id)) {selected.delete(p.id); edits.delete(p.id);}
       else if(selected.size < 10) selected.add(p.id);
       else {showMessage("You can select a maximum of ten networks at once.",false);return;}
       conditionalFields();
       invalidate();
       renderNetworks();
       changed();
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
       showMessage("Attached " + result.media.name + ". Preview to see its offline header checks.", true);
     } catch(e) {
       showMessage(e.message || "The file could not be attached.", false);
     }
   }
 }
 async function copy(value, node) {
   try {
     await navigator.clipboard.writeText(value);
     node.textContent = "Copied";
   } catch(e) {
     showMessage("Clipboard access was blocked. Select the text in the preview to copy it.",false);
   }
 }
 function networkState(id) {
   const state = current.state;
   const found = state ? state.networks.find(n => n.id === id) : null;
   if (!found) return ["Draft · not in a package yet", "chip"];
   return found.state === "posted" ? ["Posted by hand", "chip posted"] : ["Exported to package", "chip exported"];
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
     card.appendChild(text("small",item.human_only ? "Human-written" : "Ready for review"));
     if (item.human_only) {
       card.appendChild(text("p","Only personal notes are prepared for this community. You must write your own post in its app."));
     } else {
       if(item.title) {
         card.appendChild(text("strong","Title"));
         card.appendChild(text("pre",item.title));
       }
       const label = text("label","Customize the caption for " + item.label);
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
         el("save").disabled = true;
         el("update").disabled = true;
         showMessage("Edited " + item.label + ". Preview again to validate all network rules.",false);
         changed();
       });
       const copyButton = button("Copy this caption","secondary");
       copyButton.addEventListener("click",function(){copy(editor.value,copyButton);});
       card.appendChild(copyButton);
       if(item.shortened) card.appendChild(text("p","Shortened to fit this network: read the ending before you copy it.","hint"));
       if(item.note) card.appendChild(text("p",item.note,"hint"));
     }
     if(item.media_checks && item.media_checks.length) {
       card.appendChild(text("strong","Photo and video checks (offline, from the file header):"));
       const list = document.createElement("ul");
       list.className = "media-checks";
       for(const line of item.media_checks) list.appendChild(text("li", line));
       card.appendChild(list);
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
   el("update").disabled = state || !previewValid || !current.package;
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
 async function save(mode) {
   if (!previewValid || busy) return;
   el("confirm-update").classList.add("hidden");
   lockBusy(true);
   try {
     const extra = mode === "update" ?
       {mode:"update", package:current.package, revision:current.revision, confirm:true} : {mode:"new"};
     const result = await api("/api/prepare", Object.assign(payload(), extra));
     current = {package:result.package.name, revision:result.package.revision, state:result.package};
     updateEditing();
     renderPreview(previewData);
     previewValid = false;
     scheduleSave();
     showMessage((result.updated ? "Updated the package: " : "Saved a new package: ") + result.saved_to +
       "\nOpen OPEN-FIRST.md in that folder. Nothing was uploaded or published.",true);
     loadPackages();
   } catch(e) {
     showMessage(e.message || "Saving failed.",false);
   }
   lockBusy(false);
   el("save").disabled = true;
   el("update").disabled = true;
 }
 function hasContent() {
   return Boolean(el("caption").value || selected.size || media.length || el("notes").value);
 }
 async function openPackage(name, node) {
   if (hasContent() && confirmOpen !== name) {
     confirmOpen = name;
     node.textContent = "Click again to replace the editor";
     showIn("pkg-status", "Opening a package replaces what is in the editor now. Click again to open it.", false);
     return;
   }
   confirmOpen = null;
   try {
     const result = await api("/api/package/open", {package:name});
     current = {package:result.package.name, revision:result.package.revision, state:result.package};
     applyEditor(result.editor, result.media);
     updateEditing();
     setSaved("Saved", "ok");
     showIn("pkg-status", "", true);
     showMessage("Opened " + name + ". Preview again before you save it as new or update it.", true);
     el("caption").focus();
     renderPackages(lastPackages);
   } catch(e) {
     showIn("pkg-status", e.message || "The package could not be opened.", false);
   }
 }
 async function duplicate(name) {
   try {
     const result = await api("/api/package/duplicate", {package:name});
     showIn("pkg-status", "Duplicated into a new package: " + result.saved_to + ". Nothing was uploaded or published.", true);
     loadPackages();
   } catch(e) {
     showIn("pkg-status", e.message || "The package could not be duplicated.", false);
   }
 }
 async function markPosted(pkg, network, url, when, node) {
   node.disabled = true;
   try {
     const at = when ? new Date(when) : null;
     if (at && isNaN(at.getTime())) throw new Error("The posted time is not a valid date.");
     const result = await api("/api/mark-posted", {package:pkg.name, platform:network.id, url:url,
                                                   posted_at: at ? at.toISOString() : null});
     if (current.package === pkg.name) current.state = result.package;
     showIn("pkg-status", network.label + ": " + result.notice, true);
     if (previewData.length) renderPreview(previewData);
     loadPackages();
   } catch(e) {
     node.disabled = false;
     showIn("pkg-status", e.message || "Nothing was recorded.", false);
   }
 }
 function networkRow(pkg, network) {
   const row = document.createElement("li");
   const line = document.createElement("div");
   line.className = "netrow";
   line.appendChild(text("strong", network.label));
   line.appendChild(network.state === "posted" ? text("span", "Posted by hand", "chip posted") :
                                                  text("span", "Exported", "chip exported"));
   row.appendChild(line);
   if (network.posted) {
     const info = text("p", "Post " + (network.posted.post_id || "") + " · " + (network.posted.label || "posted by hand") + " · ", "hint");
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
     row.appendChild(text("p", "Human-only: write and post it yourself. 'reachweaver mark-shared' in the terminal can record it.", "hint"));
     return row;
   }
   if (!network.measurable) {
     row.appendChild(text("p", "Not measurable in ReachWeaver yet, so it cannot be marked as posted.", "hint"));
     return row;
   }
   const open = button("Mark as posted", "small");
   const form = document.createElement("div");
   form.className = "markform hidden";
   const base = "mark-" + pkg.name + "-" + network.id;
   const urlLabel = text("label", "Link to your post on " + network.label);
   const url = document.createElement("input");
   url.type = "url"; url.id = base + "-url"; url.maxLength = 2048; url.placeholder = "https://";
   url.autocomplete = "off";
   urlLabel.setAttribute("for", url.id);
   const whenLabel = text("label", "When you posted it (optional, your local time)");
   const when = document.createElement("input");
   when.type = "datetime-local"; when.id = base + "-at";
   whenLabel.setAttribute("for", when.id);
   const actions = document.createElement("div");
   actions.className = "actions";
   const record = button("Record as posted", "primary");
   const cancel = button("Cancel", "secondary");
   actions.appendChild(record); actions.appendChild(cancel);
   form.appendChild(urlLabel); form.appendChild(url); form.appendChild(whenLabel); form.appendChild(when);
   form.appendChild(text("p", "Records it in your history as posted by hand so you can measure it. Nothing is posted or sent.", "hint"));
   form.appendChild(actions);
   open.addEventListener("click", function(){ form.classList.remove("hidden"); open.classList.add("hidden"); url.focus(); });
   cancel.addEventListener("click", function(){ form.classList.add("hidden"); open.classList.remove("hidden"); });
   record.addEventListener("click", function(){ markPosted(pkg, network, url.value.trim(), when.value, record); });
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
     head.appendChild(text("h3", pkg.created || pkg.name));
     if (pkg.name === current.package) head.appendChild(text("span", "In the editor", "chip exported"));
     card.appendChild(head);
     card.appendChild(text("small", pkg.name + (pkg.media.length ? " · " + pkg.media.length + " media file(s)" : "")));
     const list2 = document.createElement("ul");
     list2.className = "netlist";
     for (const network of pkg.networks) list2.appendChild(networkRow(pkg, network));
     card.appendChild(list2);
     const actions = document.createElement("div");
     actions.className = "actions";
     const open = button("Open in editor", "secondary");
     open.addEventListener("click", function(){ openPackage(pkg.name, open); });
     const copyPkg = button("Duplicate as new", "secondary");
     copyPkg.addEventListener("click", function(){ duplicate(pkg.name); });
     actions.appendChild(open); actions.appendChild(copyPkg);
     card.appendChild(actions);
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
   el("confirm-text").textContent = "Replace the saved files of " + current.package +
     " with what is in the editor? A package with a network marked as posted cannot be changed.";
   el("confirm-update").classList.remove("hidden");
   el("confirm-yes").focus();
 });
 el("confirm-yes").addEventListener("click",function(){ save("update"); });
 el("confirm-no").addEventListener("click",function(){ el("confirm-update").classList.add("hidden"); });
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
   el("caption").value="";el("title").value="";el("notes").value="";
   counter(); conditionalFields(); invalidate(); renderNetworks(); renderMedia(); updateEditing();
   undoStack.length = 0; stable = snapshot(); el("undo").disabled = true;
   api("/api/draft/clear", {}).then(function(){ setSaved("Cleared", "ok"); }).catch(function(e){
     setSaved("Not saved", "bad"); showMessage(e.message || "The draft could not be cleared.", false);
   });
   renderPackages(lastPackages);
 });
 if(!/^[A-Za-z0-9_-]{30,}$/.test(sessionToken)) {
   showMessage("This local page needs a session link. Open the complete URL printed in your terminal.",false);
   el("preview").disabled=true;
   el("networks").replaceChildren(text("p","Missing local session link.","hint"));
   el("packages").replaceChildren(text("p","Missing local session link.","hint"));
   return;
 }
 api("/api/catalog").then(function(data){
   catalog = data.platforms;
   conditionalFields();
   renderNetworks();
   return api("/api/draft/load", {});
 }).then(function(data){
   if (data.draft) {
     current = {package:data.draft.package, revision:data.draft.revision, state:null};
     applyEditor(data.draft, data.media);
     setSaved("Saved", "ok");
   } else {
     stable = snapshot();
   }
   updateEditing();
   ready = true;
   showMessage(data.draft ? "Your saved draft is back. No social network is connected." :
               "Local composer ready. No social network is connected.",true);
   return api("/api/packages", {});
 }).then(function(data){
   if (current.package) current.state = data.packages.find(p => p.name === current.package) || null;
   if (current.package && !current.state) current = {package:null, revision:null, state:null};
   updateEditing();
   renderPackages(data.packages);
 }).catch(function(err){
   showMessage(err.message || "Unable to open the local composer.",false);
   el("preview").disabled=true;
 });
})();
</script>
</body>
</html>"""
