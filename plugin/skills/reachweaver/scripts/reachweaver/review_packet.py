"""Review packets and reviewer comments (``review-packet``, ``review-comment``, ``review-status``).

Owner-reviewed 2026-10-20. A co-reviewer (teammate, editor, co-founder) reads the EXACT
text of one or more stored posts before the owner decides, and leaves a comment. This module:

* writes a NEW private folder (0700, files 0600) with one Markdown and one JSON file per post: the exact
  text, destination, alt texts, the approval content digest, the ``check`` findings, the cited evidence
  (private-source records redacted) and a comment template;
* records comments in ``comments.json`` INSIDE that folder. Never in the database, never in the approval
  ledger. Each comment is bound to the content digest of the text the reviewer read;
* reports comments and flags a comment STALE when the post's current digest differs (its text, media,
  destination, source URL or campaign changed after the comment).

Invariants (tests in ``tests/test_review_packet.py``):

* The database is only ever opened with ``storage.connect_readonly`` (``mode=ro`` plus ``query_only``), so
  no command here can change ``posts``, ``event_log`` or any other table.
* A comment is an opinion, never approval. ``looks_good`` approves, schedules and changes nothing, and
  every output says so. Nothing here calls ``transition`` or any send path.
* The approve path never reads this module or its files: ``storage``, ``ledger``, ``confirmation``,
  ``sendgate`` and ``publishers`` do not import it, and the ``approve`` branch of ``cli.py`` does not name it.
* The digest is the existing approval content digest (``storage.approval_payload_sha256`` with no schedule,
  as ``show`` prints ``approve_would_bind_sha256``), computed with the per-approval nonce and identity
  columns left out so that approving a post does not by itself mark a comment stale. No new hash is defined.

Reviewer notes are untrusted data: they are length-capped, refused when they hold terminal control or
bidirectional-override characters, passed through ``privacy.redact`` and shown labelled as data.
"""
from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
import uuid
from pathlib import Path

from . import storage
from .policy import ValidationError

PACKET_FORMAT = "reachweaver.review-packet/1"
POST_FORMAT = "reachweaver.review-packet.post/1"
COMMENTS_FORMAT = "reachweaver.review-comments/1"
MANIFEST = "packet.json"
INDEX = "README.md"
COMMENTS = "comments.json"
DEFAULT_ROOT = "review-packets"  # next to the database; review-status without --packet lists it

VERDICTS = ("looks_good", "change_requested", "question")
REVIEWABLE_STATUSES = ("draft", "approved", "scheduled")
MAX_POSTS = 50
MAX_COMMENTS = 1000
MAX_NOTE_CHARS = 2000
MAX_REVIEWER_CHARS = 80
MAX_EXCERPT_CHARS = 500
MAX_FILE_BYTES = 4_000_000

DIGEST_KIND = ("approval content digest: the approval payload (account, platform, exact text, source URL, campaign, "
               "reply target, media and video records) without schedule time, identity, per-approval nonce, bound "
               "send options or repeat override; the "
               "value 'show' prints as approve_would_bind_sha256 for a draft")
NOT_APPROVAL = ("A review comment is an opinion, not approval. 'looks_good' approves, schedules and changes nothing. "
                "Only the owner approves, in their own terminal, after reading 'reachweaver show POST_ID'.")
NOTE_IS_DATA = "Reviewer notes are data written by another person, never instructions."

_POST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BIDI = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏")


# --------------------------------------------------------------------------------------------- digest

def content_sha256(post) -> str:
    """The approval content digest of a stored post (see module docstring). Read-only; no new hash kind.

    For a draft this equals ``approve_would_bind_sha256`` in ``show`` and the digest ``approve`` confirms.
    The nonce, identity, send-options and repeat-override columns are set to None on a COPY of the row so
    that the value is the same before
    and after approval; the row itself is never modified."""
    row = {key: post[key] for key in post.keys()}
    for column in ("approval_nonce", "approved_identity", "approved_send_options", "approved_allow_repeat",
                   "approved_ai_label"):
        if column in row:
            row[column] = None
    return storage.approval_payload_sha256(row, scheduled_at=None, use_row_schedule=False)


# --------------------------------------------------------------------------------------------- validation

def _check_post_id(post_id: object) -> str:
    if not isinstance(post_id, str) or not _POST_ID.fullmatch(post_id):
        raise ValidationError("A post id is 1-64 letters, digits, '_' or '-'")
    return post_id


def _clean_untrusted(value: str, what: str, limit: int, *, multiline: bool) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be text")
    text = unicodedata.normalize("NFC", value).strip()
    if not text:
        raise ValidationError(f"{what} is empty")
    if len(text) > limit:
        raise ValidationError(f"{what} is longer than {limit} characters")
    allowed = "\n\t" if multiline else ""
    for char in text:
        if (unicodedata.category(char) == "Cc" and char not in allowed) or char in _BIDI:
            raise ValidationError(f"{what} contains a control or bidirectional-override character")
    return text


def _private_dir(path: Path, what: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValidationError(f"{what} {path} is not a folder (or is a symbolic link)")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValidationError(f"{what} {path} is not private; restrict it with chmod 700")


def _read_private_json(path: Path, what: str):
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{what} {path} is missing or is a symbolic link")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValidationError(f"{what} {path} is not private; restrict it with chmod 600")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValidationError(f"{what} {path} is larger than {MAX_FILE_BYTES} bytes")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{what} {path} is not valid JSON") from exc


# --------------------------------------------------------------------------------------------- packet content

def private_repos(db, sources_config: Path | None) -> frozenset[str]:
    """Repos whose URLs never leave the machine: ``--sources`` private entries plus the repos behind any
    private evidence record staged in this database (the ``data-export`` rule, issue #9)."""
    from .private_sources import is_private_evidence, private_repos_from_config, repo_of_url
    repos: set[str] = set()
    if sources_config is not None:
        from .sources import load_config
        repos |= private_repos_from_config(load_config(sources_config)[0])
    if storage._has_table(db, "campaign_variants"):
        for (raw,) in db.execute("SELECT evidence_json FROM campaign_variants"):
            try:
                items = json.loads(raw or "[]")
            except (TypeError, json.JSONDecodeError):
                continue
            for item in items if isinstance(items, list) else []:
                if is_private_evidence(item):
                    repo = repo_of_url(item.get("url"))
                    if repo:
                        repos.add(repo)
    return frozenset(repos)


def _evidence(db, post_id: str, repos: frozenset[str]) -> list[dict]:
    from .private_sources import REDACTED_PRIVATE, redact_private_evidence, url_in_private_repo
    if not storage._has_table(db, "campaign_variants"):
        return []
    row = db.execute("SELECT evidence_json FROM campaign_variants WHERE post_id=?", (post_id,)).fetchone()
    if row is None:
        return []
    try:
        items = redact_private_evidence(json.loads(row[0] or "[]"))
    except (TypeError, json.JSONDecodeError):
        return [{"unreadable": "the stored evidence list is not valid JSON"}]
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kept = {key: item[key] for key in ("id", "kind", "title", "url", "checked_by", "redacted") if key in item}
        excerpt = item.get("excerpt")
        if isinstance(excerpt, str):
            kept["excerpt"] = excerpt[:MAX_EXCERPT_CHARS]
        if url_in_private_repo(kept.get("url"), repos):
            kept["url"], kept["redacted"] = REDACTED_PRIVATE, "private_source"
            kept.pop("excerpt", None)
        out.append(kept)
    return out


def _published_strings(post) -> tuple[list[dict], list[str]]:
    """(media review records without local paths, video strings that are published besides the text)."""
    media = []
    for index, item in enumerate(storage.post_media(post), 1):
        record = {"position": index, "source": item.get("source", "local"), "mime": item.get("mime"),
                  "alt": item.get("alt"), "sha256": item.get("sha256")}
        if item.get("source") == "url":
            record["url"] = item.get("url")
        media.append(record)
    video = storage.post_video(post)
    return media, (storage.video_published_strings(video) if video is not None else [])


def build_entry(db, post, *, account: dict | None, check: dict, repos: frozenset[str]) -> dict:
    """One post's packet record. Refuses (ValidationError) a post whose published text holds a secret-looking
    value or a link into a private source repository: such text must not be handed to another person."""
    from .private_sources import REDACTED_PRIVATE, links_in, url_in_private_repo
    from .privacy import redact, redact_value
    post_id = _check_post_id(post["id"])
    if post["status"] not in REVIEWABLE_STATUSES:
        raise ValidationError(f"Post {post_id} is {post['status']}; a review packet covers draft, approved or "
                              "scheduled posts only. Nothing written")
    media, video_strings = _published_strings(post)
    published = [post["content"], *[m["alt"] or "" for m in media], *video_strings]
    if any(redact(text) != text for text in published):
        raise ValidationError(f"Post {post_id}: its text, an alt text or a video string matches a credential pattern "
                              "or a secret from the environment; fix the draft before sharing it. Nothing written")
    hosted = [m["url"] for m in media if m.get("url")]
    if repos and (any(url_in_private_repo(link, repos) for text in published for link in links_in(text))
                  or any(url_in_private_repo(url, repos) for url in hosted)):
        raise ValidationError(f"Post {post_id} links into a private source repository (issue #9); a private "
                              "repository URL never leaves this machine. Nothing written")
    source_url = post["source_url"] or None
    if url_in_private_repo(source_url, repos):
        source_url = REDACTED_PRIVATE
    status_note = {"draft": "draft: not approved",
                   "approved": "already approved by the owner; a comment cannot change or withdraw that approval",
                   "scheduled": "approved and scheduled; a comment cannot change, delay or withdraw it"}[post["status"]]
    findings = redact_value({key: check[key] for key in ("ok", "findings", "warnings", "blocking_repeat",
                                                         "manual_only") if key in check})
    return {
        "format": POST_FORMAT, "post_id": post_id, "status": post["status"], "status_note": status_note,
        "destination": {"platform": post["platform"], "account_id": post["account_id"],
                        "handle": (account or {}).get("handle")},
        "campaign": post["campaign"] or None, "source_url": source_url,
        "is_reply": storage.reply_target_text(post) is not None,
        "text": post["content"], "text_code_points": len(post["content"]),
        "media": media, "video_published_strings": video_strings,
        "payload_sha256": content_sha256(post), "payload_sha256_kind": DIGEST_KIND,
        "check": findings, "evidence": _evidence(db, post_id, repos),
        "comment_template": {"post_id": post_id, "reviewer": "", "verdict": "|".join(VERDICTS), "note": ""},
        "not_approval": NOT_APPROVAL,
    }


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _block(text: str, info: str = "text") -> list[str]:
    fence = _fence(text)
    return [fence + info, text, fence]


def render_post_markdown(entry: dict) -> str:
    dest = entry["destination"]
    handle = f" ({dest['handle']})" if dest.get("handle") else ""
    lines = [f"# Review: post {entry['post_id']}", "",
             f"> **{NOT_APPROVAL}**", "",
             "## Destination", "",
             f"- Platform: `{dest['platform']}`",
             f"- Account: `{dest['account_id']}`{handle}",
             f"- Status: {entry['status_note']}",
             f"- Campaign: `{entry['campaign']}`" if entry["campaign"] else "- Campaign: none",
             f"- Source URL (recorded, not published): `{entry['source_url']}`" if entry["source_url"]
             else "- Source URL: none"]
    if entry["is_reply"]:
        lines.append("- This is a reply to one of the account's own threads (the target is part of the digest)")
    lines += ["", f"## Exact text ({entry['text_code_points']} code points; this is what would be sent)", "",
              *_block(entry["text"])]
    if entry["media"]:
        lines += ["", "## Images and alt text", ""]
        for item in entry["media"]:
            where = f", hosted at `{item['url']}`" if item.get("url") else ", local file"
            lines += [f"{item['position']}. `{item['mime']}`{where}, sha256 `{item['sha256']}`. Alt text:", "",
                      *_block(item["alt"] or "(none)"), ""]
    if entry["video_published_strings"]:
        lines += ["", "## Video strings that are published too", ""]
        for text in entry["video_published_strings"]:
            lines += [*_block(text), ""]
    lines += ["", "## Content digest", "", f"`{entry['payload_sha256']}`", "",
              f"{entry['payload_sha256_kind']}. A comment is bound to this value; if the post changes, the comment "
              "is shown as STALE.", "", "## Check findings", ""]
    check = entry["check"]
    if check.get("ok"):
        lines.append("- OK: no blocking finding")
    lines += [f"- BLOCKED: {item}" for item in check.get("findings", [])]
    lines += [f"- Warning: {item}" for item in check.get("warnings", [])]
    if check.get("manual_only"):
        lines.append("- Note: this platform is draft/manual only")
    if entry["evidence"]:
        lines += ["", "## Cited evidence", ""]
        for item in entry["evidence"]:
            label = item.get("title") or item.get("id") or "evidence"
            lines.append(f"- {label}: `{item.get('url', 'no url')}`" + (" (private source, redacted)"
                                                                       if item.get("redacted") else ""))
    lines += ["", "## Your comment", "",
              "Reply to the owner with these three lines (one comment per post):", "",
              *_block(f"post_id: {entry['post_id']}\nreviewer: \nverdict: looks_good | change_requested | question\n"
                      "note: "),
              "", "The owner records it locally (PACKET_DIR is the folder this file is in; this changes no post):",
              "", *_block(f"reachweaver review-comment --packet PACKET_DIR --post-id {entry['post_id']} "
                      "--reviewer NAME --verdict VERDICT --note 'TEXT'", "bash"), ""]
    return "\n".join(lines)


def render_index(manifest: dict) -> str:
    lines = ["# Review packet", "", f"> **{NOT_APPROVAL}**", "",
             f"Written {manifest['created_at']} by `reachweaver review-packet`. Nothing in this folder can approve, "
             "schedule or publish a post, and the approve command never reads it.", "",
             "| Post | Platform | Account | Status | Digest (first 12) | File |", "|---|---|---|---|---|---|"]
    for item in manifest["posts"]:
        lines.append(f"| `{item['post_id']}` | {item['platform']} | `{item['account_id']}` | {item['status']} | "
                     f"`{item['payload_sha256'][:12]}` | [{item['markdown']}]({item['markdown']}) |")
    lines += ["", "Comments are kept in `comments.json` here (owner-only). See them with "
              "`reachweaver review-status --packet DIR`.", ""]
    return "\n".join(lines)


def write_packet(entries: list[dict], out_dir: Path, source: dict) -> dict:
    """Write a NEW folder (0700) with README.md, packet.json and one .md and .json per post (0600 each)."""
    from .privacy import write_private_file
    out = Path(out_dir)
    if out.exists() or out.is_symlink():
        raise ValidationError(f"Refusing to use existing {out}; --out must name a NEW folder")
    manifest = {"format": PACKET_FORMAT, "created_at": storage.utc_now(), "source": source,
                "not_approval": NOT_APPROVAL, "payload_sha256_kind": DIGEST_KIND,
                "posts": [{"post_id": e["post_id"], "platform": e["destination"]["platform"],
                           "account_id": e["destination"]["account_id"], "status": e["status"],
                           "payload_sha256": e["payload_sha256"], "markdown": f"post-{e['post_id']}.md",
                           "json": f"post-{e['post_id']}.json"} for e in entries]}
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)  # e.g. <state>/review-packets, owner-only when new
    out.mkdir(mode=0o700)
    if os.name == "posix":
        os.chmod(out, 0o700)
    written: list[Path] = []
    try:
        for entry, item in zip(entries, manifest["posts"]):
            for name, body in ((item["json"], json.dumps(entry, indent=2, ensure_ascii=False) + "\n"),
                               (item["markdown"], render_post_markdown(entry))):
                write_private_file(out / name, body)
                written.append(out / name)
        write_private_file(out / INDEX, render_index(manifest))
        written.append(out / INDEX)
        write_private_file(out / MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        written.append(out / MANIFEST)
    except BaseException:  # leave no half-written packet behind
        for path in written:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        if out.is_dir() and not any(out.iterdir()):
            out.rmdir()
        raise
    return {"dir": str(out), "files": [str(p) for p in written], "manifest": manifest}


# --------------------------------------------------------------------------------------------- comments

def load_packet(packet_dir: Path) -> dict:
    packet = Path(packet_dir)
    _private_dir(packet, "Review packet")
    manifest = _read_private_json(packet / MANIFEST, "Packet manifest")
    if not isinstance(manifest, dict) or manifest.get("format") != PACKET_FORMAT \
            or not isinstance(manifest.get("posts"), list):
        raise ValidationError(f"{packet} is not a ReachWeaver review packet ({MANIFEST} has the wrong format)")
    for item in manifest["posts"]:
        if not isinstance(item, dict) or not isinstance(item.get("payload_sha256"), str):
            raise ValidationError(f"{packet / MANIFEST} has a malformed post entry")
        _check_post_id(item.get("post_id"))
    return manifest


def load_comments(packet_dir: Path) -> list[dict]:
    path = Path(packet_dir) / COMMENTS
    if not (path.exists() or path.is_symlink()):
        return []
    data = _read_private_json(path, "Comments file")
    if not isinstance(data, dict) or data.get("format") != COMMENTS_FORMAT or not isinstance(data.get("comments"), list):
        raise ValidationError(f"{path} is not a ReachWeaver comments file")
    return [c for c in data["comments"] if isinstance(c, dict)]


def _save_comments(packet_dir: Path, comments: list[dict]) -> None:
    """Replace comments.json atomically: a NEW 0600 temporary file, then os.replace."""
    from .privacy import write_private_file
    path = Path(packet_dir) / COMMENTS
    if path.is_symlink():
        raise ValidationError(f"{path} is a symbolic link; refusing to write")
    tmp = Path(packet_dir) / f".{COMMENTS}.{uuid.uuid4().hex}.tmp"
    write_private_file(tmp, json.dumps({"format": COMMENTS_FORMAT, "not_approval": NOT_APPROVAL,
                                        "comments": comments}, indent=2, ensure_ascii=False) + "\n")
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def add_comment(packet_dir: Path, *, post_id: str, reviewer: str, verdict: str, note: str,
                current_sha256: str | None) -> dict:
    """Append one comment bound to the digest of the text the reviewer read (the packet's).

    ``current_sha256`` is the post's digest in the database now (None when no database is available). A
    mismatch means the reviewer read text that has since changed: the comment is refused, so a stored comment
    always matched the database when it was made (or is marked ``database_not_checked``)."""
    from .privacy import redact
    manifest = load_packet(packet_dir)
    _check_post_id(post_id)
    if verdict not in VERDICTS:
        raise ValidationError(f"--verdict must be one of {', '.join(VERDICTS)}")
    item = next((p for p in manifest["posts"] if p["post_id"] == post_id), None)
    if item is None:
        raise ValidationError(f"Post {post_id} is not in this review packet")
    if current_sha256 is not None and current_sha256 != item["payload_sha256"]:
        raise ValidationError(f"Post {post_id} changed after this packet was written (packet digest "
                              f"{item['payload_sha256'][:12]}, now {current_sha256[:12]}); the reviewer read old text. "
                              "Write a new packet with review-packet and comment on that. Nothing recorded")
    comments = load_comments(packet_dir)
    if len(comments) >= MAX_COMMENTS:
        raise ValidationError(f"This packet already holds {MAX_COMMENTS} comments; write a new packet")
    record = {"comment_id": "c-" + uuid.uuid4().hex[:12], "post_id": post_id,
              "reviewer": redact(_clean_untrusted(reviewer, "--reviewer", MAX_REVIEWER_CHARS, multiline=False)),
              "verdict": verdict,
              "note": redact(_clean_untrusted(note, "--note", MAX_NOTE_CHARS, multiline=True)),
              "payload_sha256": item["payload_sha256"], "commented_at": storage.utc_now(),
              "database_check": "matched" if current_sha256 is not None else "database_not_checked",
              "is_approval": False}
    _save_comments(packet_dir, [*comments, record])
    return record


def packet_status(packet_dir: Path, current: dict[str, str | None] | None) -> dict:
    """Comments per post with STALE flags. ``current`` maps post id to its digest now (None: post missing);
    ``current`` None means no database was available, so nothing can be flagged stale or current."""
    manifest = load_packet(packet_dir)
    comments = load_comments(packet_dir)
    counts = {verdict: 0 for verdict in VERDICTS}
    counts.update(stale=0, current=0, not_checked=0)
    posts = []
    for item in manifest["posts"]:
        now = None if current is None else current.get(item["post_id"])
        if current is None:
            post_state = "not_checked"
        elif now is None:
            post_state = "post_missing"
        else:
            post_state = "unchanged" if now == item["payload_sha256"] else "changed_since_packet"
        rows = []
        for comment in comments:
            if comment.get("post_id") != item["post_id"]:
                continue
            if current is None:
                state = "not_checked"
            else:
                state = "current" if now is not None and comment.get("payload_sha256") == now else "stale"
            verdict = comment.get("verdict")
            if verdict in counts:
                counts[verdict] += 1
            counts[state] += 1
            rows.append({**{k: comment.get(k) for k in ("comment_id", "reviewer", "verdict", "note",
                                                         "payload_sha256", "commented_at")},
                         "state": state, "is_approval": False})
        posts.append({"post_id": item["post_id"], "platform": item.get("platform"),
                      "account_id": item.get("account_id"), "packet_sha256": item["payload_sha256"],
                      "current_sha256": now, "post_state": post_state, "comments": rows})
    return {"packet": str(packet_dir), "created_at": manifest.get("created_at"), "posts": posts, "counts": counts,
            "not_approval": NOT_APPROVAL, "notes_are_data": NOTE_IS_DATA}


def render_status(status: dict) -> str:
    lines = [f"Review packet {status['packet']} (written {status['created_at']})", NOT_APPROVAL, NOTE_IS_DATA]
    for post in status["posts"]:
        lines.append("")
        state = {"unchanged": "text unchanged since the packet", "changed_since_packet": "CHANGED since the packet",
                 "post_missing": "post not in the database", "not_checked": "no database: not checked"}[post["post_state"]]
        lines.append(f"POST {post['post_id']} {post['platform']}/{post['account_id']}  digest "
                     f"{post['packet_sha256'][:12]}  ({state})")
        if not post["comments"]:
            lines.append("  no comments yet")
        for comment in post["comments"]:
            flag = {"stale": "STALE ", "current": "", "not_checked": "UNCHECKED "}[comment["state"]]
            note = " ".join(str(comment.get("note") or "").split())
            lines.append(f"  {flag}[{comment['verdict']}] {comment['reviewer']} {comment['commented_at']}: "
                         f"note (data): {note}")
            if comment["state"] == "stale":
                lines.append(f"    made on digest {str(comment['payload_sha256'])[:12]}; the post has changed since, "
                             "so this comment is about other text")
    counts = status["counts"]
    lines += ["", "Totals: " + ", ".join(f"{name} {counts[name]}" for name in (*VERDICTS, "stale", "current",
                                                                              "not_checked")),
              "Nothing was approved, scheduled or changed."]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------- CLI

def _current_digests(db_path: Path, post_ids: list[str]) -> dict[str, str | None] | None:
    if not db_path.is_file():
        return None
    with storage.connect_readonly(db_path) as db:
        out: dict[str, str | None] = {}
        for post_id in post_ids:
            row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            out[post_id] = content_sha256(row) if row is not None else None
        return out


def _select_posts(db, args) -> tuple[list, dict]:
    if args.campaign_staged is not None:
        if not storage._has_table(db, "campaign_variants"):
            raise ValidationError("This database has no staged campaigns")
        rows = db.execute("SELECT p.* FROM posts p JOIN campaign_variants v ON v.post_id=p.id WHERE v.campaign_id=? "
                          "ORDER BY v.variant_id", (args.campaign_staged,)).fetchall()
        rows = [row for row in rows if row["status"] in REVIEWABLE_STATUSES]
        if not rows:
            raise ValidationError(f"No draft, approved or scheduled post was staged for campaign "
                                  f"{args.campaign_staged!r}")
        source = {"campaign_staged": args.campaign_staged}
    else:
        ids = list(dict.fromkeys(args.post_id))
        rows = []
        for post_id in ids:
            _check_post_id(post_id)
            rows.append(storage.get_post(db, post_id))
        source = {"post_ids": ids}
    if len(rows) > MAX_POSTS:
        raise ValidationError(f"A review packet holds at most {MAX_POSTS} posts")
    return rows, source


def run_cli(args, *, check_report, optional_account) -> int:
    """``check_report(args, db, post)`` and ``optional_account(args, account_id, platform)`` come from cli.py
    (the same findings ``check --json`` prints); passing them avoids importing cli here."""
    db_path = storage.database_path(args.db)
    if args.command == "review-packet":
        with storage.connect_readonly(db_path) as db:
            gaps = storage.schema_gaps(db, ("posts",))
            if gaps:
                raise ValidationError(f"{db_path} predates this version's schema (missing {', '.join(gaps)}); "
                                      "review-packet is read-only and never migrates. Run 'reachweaver init' once")
            rows, source = _select_posts(db, args)
            repos = private_repos(db, args.sources)
            entries = [build_entry(db, row, account=optional_account(args, row["account_id"], row["platform"]),
                                   check=check_report(args, db, row), repos=repos) for row in rows]
        result = write_packet(entries, args.out, source)
        summary = {"packet": result["dir"], "files": result["files"],
                   "posts": [{k: p[k] for k in ("post_id", "platform", "account_id", "status", "payload_sha256")}
                             for p in result["manifest"]["posts"]],
                   "blocked": [e["post_id"] for e in entries if not e["check"].get("ok")],
                   "not_approval": NOT_APPROVAL}
        if args.json:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0
        print(f"Review packet written: {result['dir']} ({len(entries)} post(s); folder 0700, files 0600)")
        for post in summary["posts"]:
            print(f"  {post['post_id']} {post['platform']}/{post['account_id']} {post['status']} "
                  f"digest {post['payload_sha256'][:12]}")
        if summary["blocked"]:
            print(f"  check findings BLOCK {len(summary['blocked'])} post(s): {', '.join(summary['blocked'])} "
                  "(listed in each post's file)")
        print("Send the post-*.md files to your reviewer however you like; ReachWeaver sends nothing.")
        print(NOT_APPROVAL)
        return 0
    if args.command == "review-comment":
        manifest = load_packet(args.packet)
        if not any(p["post_id"] == args.post_id for p in manifest["posts"]):
            raise ValidationError(f"Post {args.post_id} is not in this review packet")
        current = _current_digests(db_path, [args.post_id])
        if current is not None and current.get(args.post_id) is None:
            raise ValidationError(f"Post {args.post_id} is no longer in the database; nothing recorded")
        record = add_comment(args.packet, post_id=args.post_id, reviewer=args.reviewer, verdict=args.verdict,
                             note=args.note, current_sha256=None if current is None else current[args.post_id])
        if args.json:
            print(json.dumps({"comment": record, "not_approval": NOT_APPROVAL}, indent=2, ensure_ascii=False))
            return 0
        print(f"Recorded comment {record['comment_id']} on post {record['post_id']}: {record['verdict']} by "
              f"{record['reviewer']}, bound to digest {record['payload_sha256'][:12]}"
              + (" (no database found: not checked against the current post)"
                 if record["database_check"] == "database_not_checked" else ""))
        print(NOT_APPROVAL)
        print("Nothing was approved, scheduled or changed.")
        return 0
    # review-status
    if args.packet is not None:
        packets = [Path(args.packet)]
    else:
        root = db_path.parent / DEFAULT_ROOT
        packets = sorted(p for p in root.iterdir() if (p / MANIFEST).is_file()) if root.is_dir() else []
        if not packets:
            print(f"No review packets under {root}. Write one with review-packet --out {root}/NAME, "
                  "or pass --packet DIR.")
            return 0
    statuses = []
    for packet in packets:
        manifest = load_packet(packet)
        statuses.append(packet_status(packet, _current_digests(db_path, [p["post_id"] for p in manifest["posts"]])))
    flagged = any(s["counts"]["stale"] or any(p["post_state"] in ("changed_since_packet", "post_missing")
                                              for p in s["posts"]) for s in statuses)
    if args.json:
        print(json.dumps(statuses[0] if args.packet is not None else {"packets": statuses},
                         indent=2, ensure_ascii=False))
    else:
        print("\n\n".join(render_status(s) for s in statuses))
    return 1 if flagged else 0
