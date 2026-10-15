"""Human-approved replies under your own published Bluesky/Mastodon posts (issue #17, owner-approved 2026-10-09;
disabled until a live receipt).

Owner-approved 2026-10-09 with every recommended decision; disabled until a supervised live reply
receipt (docs/SECURITY-REVIEW-PROPOSAL-17-reply-send.md).

A reply is an ordinary ``posts`` row with a ``reply_target``: the same draft -> approve
(payload hash) -> ``publish --live`` -> claim -> receipt / needs_review lifecycle as any post.
There is no second send pipeline. This module only builds and checks the target:

* ``verify_reply_target`` runs at draft time (``draft --reply-to ITEM --online``). It reads the
  parent with unauthenticated, redirect-free GETs (``publishers.request_get_json``, no token),
  re-runs the engagement triage rules on the parent's text, and refuses unless the parent sits
  in a thread whose root is a post that ReachWeaver itself published for this account (no cold
  replies into strangers' threads). The result is canonical JSON that the approval hash binds.
* ``target_problems`` re-checks a stored target before approve and before every claim
  (opt-out list on the parent author, server/visibility against the current account config).
* The send itself stays behind ``capabilities.require_reply_capability``; the bundled registry
  ships ``reachweaver_reply_adapter: none``, so no reply can be claimed until the owner changes it.

Bluesky (accessed 2026-10-09):
  A reply record carries ``reply: {root: strongRef, parent: strongRef}``; both are required and a
  strongRef is ``{uri: at-uri, cid: cid}``. When the parent is itself a reply, ``root`` is copied
  from the parent's own ``reply.root``.
  https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/post.json
  https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/com/atproto/repo/strongRef.json
  https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/posts.md
  ``app.bsky.feed.getPostThread`` (``uri``, ``depth``, ``parentHeight``) returns a
  ``threadViewPost`` with ``post`` (postView: uri, cid, author, record, optional threadgate and
  labels) and ``parent`` (threadViewPost | notFoundPost | blockedPost).
  https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/getPostThread.json
  https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/defs.json
Mastodon (accessed 2026-10-09):
  ``POST /api/v1/statuses`` takes ``in_reply_to_id`` ("ID of the status being replied to") and
  ``visibility`` (public, unlisted, private, direct). ``GET /api/v1/statuses/:id`` is "Public for
  public statuses"; ``GET /api/v1/statuses/:id/context`` returns ``ancestors`` ("Parents in the
  thread"), unauthenticated "limited to 40 ancestors and 60 descendants with a maximum depth of 20".
  https://docs.joinmastodon.org/methods/statuses/  https://docs.joinmastodon.org/entities/Status/
  https://docs.joinmastodon.org/entities/Context/
  The docs do not say that a reply's visibility must relate to its parent's. The official web
  client picks the more restrictive of the parent's visibility and the default
  (``privacyPreference`` in app/javascript/mastodon/reducers/compose.js,
  https://github.com/mastodon/mastodon, main branch, read 2026-10-09). ReachWeaver applies the
  same rule as its own policy, and never sends ``direct``. Whether the server notifies the parent
  author without an @-mention is not documented and not assumed; ReachWeaver adds no mention.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from . import publishers
from .engagement import SAFETY_LABELS, analyze, sanitize_text
from .optout import listed_entry
from .policy import MASTODON_VISIBILITIES, ValidationError, require_https_public_server
from .storage import reply_target_text, utc_now

REPLY_PLATFORMS = ("bluesky", "mastodon")
TARGET_VERSION = 1
BLUESKY_APPVIEW = "https://public.api.bsky.app"  # same public AppView as metrics_fetch
GET_POST_THREAD = "/xrpc/app.bsky.feed.getPostThread"
MAX_PARENT_HEIGHT = 20  # ancestors read to prove the thread root; deeper threads are refused
# Overridable at draft time with --override-flagged LABEL (the exact label): triage safety labels
# plus a Bluesky moderation label on the parent. Opt-out signals are NEVER overridable.
FLAGGED_LABELS = tuple(sorted(SAFETY_LABELS)) + ("moderation_labelled",)
VISIBILITY_ORDER = ("public", "unlisted", "private")  # widest first; "direct" is never sent
_POST_URI = re.compile(r"at://(did:[a-z]+:[A-Za-z0-9._:%-]+)/app\.bsky\.feed\.post/([A-Za-z0-9._:~-]{1,512})")
_CID = re.compile(r"[A-Za-z0-9]{8,256}")
_DID = re.compile(r"did:[a-z]+:[A-Za-z0-9._:%-]{1,2000}")
_MASTODON_ID = re.compile(r"[A-Za-z0-9._~-]{1,128}")
_HANDLE = re.compile(r"[A-Za-z0-9._:@-]{1,253}")
_LOCAL_POST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00")
_COMMON_KEYS = ("v", "platform", "root_post_id", "parent_author", "parent_url", "verified_at",
                "triage_label", "override")
TARGET_KEYS = {
    "bluesky": frozenset(_COMMON_KEYS + ("root", "parent", "parent_author_did")),
    "mastodon": frozenset(_COMMON_KEYS + ("server", "in_reply_to_id", "root_remote_id",
                                          "parent_visibility", "visibility")),
}
TRIAGE_LABELS = ("bug_report", "technical_question", "harassment", "actionable_feedback", "feature_request",
                 "collaboration_opportunity", "suspicious", "praise", "off_topic", "spam")


class ReplyRefused(ValidationError):
    """A reply target that ReachWeaver will not draft or send. ``code`` is a fixed reason code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# --- Target references -----------------------------------------------------------------------

def parse_reply_ref(account: dict, ref: str) -> dict:
    """``--reply-to`` value -> ``{"platform", ...native id}``. Offline; no request.

    Accepted: an engagement item id exactly as engagement-fetch writes it
    (``bluesky:at://DID/app.bsky.feed.post/RKEY`` or ``mastodon:SERVER_HOST:STATUS_ID``) or, for
    Bluesky, a bare AT-URI. A Mastodon id must belong to the account's own server, because a
    status id is local to the server that issued it.
    """
    platform = account["platform"]
    if platform not in REPLY_PLATFORMS:
        raise ReplyRefused("reply_platform_unsupported", f"replies are drafted for {', '.join(REPLY_PLATFORMS)} only")
    if account.get("backend") is not None:
        raise ReplyRefused("reply_backend_unsupported", "replies are never sent through a backend such as Postiz")
    value = ref.strip() if isinstance(ref, str) else ""
    if ":sha256:" in value:
        raise ReplyRefused("reply_ref_hashed", "this item id is a hash of an over-long id; it cannot name a target")
    if platform == "bluesky":
        uri = value[len("bluesky:"):] if value.startswith("bluesky:") else value
        if not _POST_URI.fullmatch(uri):
            raise ReplyRefused("reply_ref_invalid", "expected bluesky:at://DID/app.bsky.feed.post/RKEY")
        return {"platform": "bluesky", "uri": uri}
    server = require_https_public_server(str(account.get("server", "")))
    host = urlparse(server).hostname or ""
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "mastodon" or not _MASTODON_ID.fullmatch(parts[2]):
        raise ReplyRefused("reply_ref_invalid", "expected mastodon:SERVER_HOST:STATUS_ID")
    if parts[1].casefold() != host.casefold():
        raise ReplyRefused("reply_ref_other_server", "the status id is not from this account's server")
    return {"platform": "mastodon", "id": parts[2], "server": server}


def verification_plan(account: dict, ref: dict) -> list[dict]:
    """The unauthenticated GETs ``verify_reply_target`` would send (never any credential)."""
    if ref["platform"] == "bluesky":
        params = {"uri": ref["uri"], "depth": 0, "parentHeight": MAX_PARENT_HEIGHT}
        return [{"method": "GET", "endpoint": BLUESKY_APPVIEW + GET_POST_THREAD, "params": params,
                 "url": BLUESKY_APPVIEW + GET_POST_THREAD + "?" + urlencode(params), "auth": "none"}]
    status = f"{ref['server']}/api/v1/statuses/{quote(ref['id'], safe='')}"
    return [{"method": "GET", "endpoint": status, "url": status, "auth": "none"},
            {"method": "GET", "endpoint": status + "/context", "url": status + "/context", "auth": "none"}]


# --- Local evidence: posts ReachWeaver itself published ---------------------------------------

def _owner_published(con: sqlite3.Connection, account_id: str) -> dict[str, dict]:
    """remote_id -> {post_id, cid} of this account's published ORDINARY posts (thread roots)."""
    out: dict[str, dict] = {}
    for row in con.execute("SELECT * FROM posts WHERE account_id=? AND status='published' AND remote_id IS NOT NULL",
                           (account_id,)).fetchall():
        if reply_target_text(row) is not None:
            continue  # a reply is never a thread root
        detail = con.execute("SELECT detail FROM event_log WHERE post_id=? AND action='published' "
                             "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
        tokens = dict(t.split("=", 1) for t in str(detail["detail"] if detail else "").split() if "=" in t)
        cid = tokens.get("cid")
        out[str(row["remote_id"])] = {"post_id": row["id"], "cid": cid if cid and _CID.fullmatch(cid) else None}
    return out


def _bsky_url(uri: str) -> str:
    match = _POST_URI.fullmatch(uri)
    assert match is not None
    return f"https://bsky.app/profile/{match.group(1)}/post/{match.group(2)}"


def _strong_ref(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    uri, cid = value.get("uri"), value.get("cid")
    if isinstance(uri, str) and _POST_URI.fullmatch(uri) and isinstance(cid, str) and _CID.fullmatch(cid):
        return {"uri": uri, "cid": cid}
    return None


def stricter_visibility(*values: str) -> str:
    """The most restrictive of public < unlisted < private (Mastodon web-client rule, cited above)."""
    ranks = [VISIBILITY_ORDER.index(v) for v in values if v in VISIBILITY_ORDER]
    if len(ranks) != len(values):
        raise ReplyRefused("reply_visibility_invalid", "visibility must be public, unlisted or private")
    return VISIBILITY_ORDER[max(ranks)]


# --- Triage of the parent text ------------------------------------------------------------------

def _triage(text: str, moderation_labels: bool, override: str | None) -> tuple[str, str | None]:
    """(triage label, accepted override). Refuses flagged parents unless ``override`` names the flag."""
    clean, flags = sanitize_text(text)
    analysis = analyze(clean, flags)
    if analysis["opt_out"]:
        raise ReplyRefused("reply_parent_asked_not_to_be_contacted",
                           "the parent asks not to be contacted; this is never overridable")
    label = analysis["label"]
    flag = label if label in SAFETY_LABELS else "moderation_labelled" if moderation_labels else None
    if override is not None and override not in FLAGGED_LABELS:
        raise ReplyRefused("reply_override_invalid", f"--override-flagged takes one of {', '.join(FLAGGED_LABELS)}")
    if flag is None:
        if override is not None:
            raise ReplyRefused("reply_override_unused", f"the parent is not flagged {override}; drop --override-flagged")
        return label, None
    if override != flag:
        raise ReplyRefused(f"reply_target_flagged_{flag}",
                           f"triage flagged the parent as {flag}; read it yourself on the platform and re-run "
                           f"with --override-flagged {flag} only if a reply is still right")
    return label, override


def _check_optout(optout: list[dict], platform: str, *handles: str | None) -> None:
    for handle in handles:
        if handle and listed_entry(handle, optout, platform) is not None:
            raise ReplyRefused("reply_parent_author_optout_listed",
                               "the parent author is on the local opt-out list; never overridable")


# --- Online verification -------------------------------------------------------------------------

def _get(url: str) -> dict:
    return publishers.request_get_json(url)  # no headers: never a credential; no redirects


def _verify_bluesky(con, account: dict, ref: dict, optout: list[dict], override: str | None) -> dict:
    data = _get(verification_plan(account, ref)[0]["url"])
    thread = data.get("thread")
    if not isinstance(thread, dict) or thread.get("notFound") is True or thread.get("blocked") is True:
        raise ReplyRefused("reply_parent_not_found_or_blocked")
    post = thread.get("post")
    if not isinstance(post, dict) or post.get("uri") != ref["uri"]:
        raise ReplyRefused("reply_parent_response_mismatch")
    parent_ref = _strong_ref(post)
    author = post.get("author")
    record = post.get("record")
    did = _POST_URI.fullmatch(ref["uri"]).group(1)  # type: ignore[union-attr]
    if parent_ref is None or not isinstance(author, dict) or author.get("did") != did or not isinstance(record, dict):
        raise ReplyRefused("reply_parent_malformed")
    reply = record.get("reply")
    root_ref = _strong_ref(reply.get("root")) if isinstance(reply, dict) else None
    if root_ref is None:
        raise ReplyRefused("reply_target_outside_own_threads", "the parent is a top-level post, not a reply in your thread")
    owned = _owner_published(con, account["id"])
    root = owned.get(root_ref["uri"])
    if root is None:
        raise ReplyRefused("reply_target_outside_own_threads",
                           "the thread root is not a post ReachWeaver published for this account")
    if did == _POST_URI.fullmatch(root_ref["uri"]).group(1):  # type: ignore[union-attr]
        raise ReplyRefused("reply_parent_is_owner_post", "the parent is your own post")
    if root["cid"] is not None and root["cid"] != root_ref["cid"]:
        raise ReplyRefused("reply_root_cid_mismatch", "the root CID differs from the recorded publish receipt")
    # Walk the ancestors the AppView returned: they must end at exactly that root.
    node, top = thread.get("parent"), None
    for _ in range(MAX_PARENT_HEIGHT + 1):
        if node is None:
            break
        if not isinstance(node, dict) or node.get("notFound") is True or node.get("blocked") is True:
            raise ReplyRefused("reply_thread_unverifiable", "an ancestor is missing or blocked")
        top = node.get("post")
        node = node.get("parent")
    if not isinstance(top, dict) or _strong_ref(top) != root_ref:
        raise ReplyRefused("reply_thread_unverifiable",
                           f"the returned ancestors do not end at your root post (at most {MAX_PARENT_HEIGHT} levels)")
    if top.get("threadgate") is not None:
        raise ReplyRefused("reply_root_has_threadgate", "a threadgate on the root may hide the reply")
    handle = author.get("handle") if isinstance(author.get("handle"), str) and author["handle"].strip() else did
    _check_optout(optout, "bluesky", handle, did)
    labels = post.get("labels")
    raw_text = record.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    label, accepted = _triage(text, bool(labels) and isinstance(labels, list), override)
    return {"v": TARGET_VERSION, "platform": "bluesky", "root": root_ref, "parent": parent_ref,
            "root_post_id": root["post_id"], "parent_author": str(handle)[:253], "parent_author_did": did,
            "parent_url": _bsky_url(ref["uri"]), "verified_at": utc_now(), "triage_label": label,
            "override": accepted}


def _mastodon_owner(account: dict) -> tuple[str, str]:
    from .metrics_fetch import _mastodon_local_handle
    return _mastodon_local_handle(account), (urlparse(account["server"]).hostname or "").casefold()


def _is_owner(status: dict, local: str, host: str) -> bool:
    author = status.get("account")
    if not isinstance(author, dict):
        return False
    acct = str(author.get("acct", "")).casefold()
    return str(author.get("username", "")).casefold() == local and acct in (local, f"{local}@{host}")


def _verify_mastodon(con, account: dict, ref: dict, optout: list[dict], override: str | None) -> dict:
    from .engagement_fetch import html_to_text
    plan = verification_plan(account, ref)
    status = _get(plan[0]["url"])
    if str(status.get("id", "")) != ref["id"]:
        raise ReplyRefused("reply_parent_response_mismatch")
    visibility = status.get("visibility")
    if visibility not in ("public", "unlisted"):
        raise ReplyRefused("reply_parent_visibility_not_public", "only public or unlisted parents are replied to")
    local, host = _mastodon_owner(account)
    if _is_owner(status, local, host):
        raise ReplyRefused("reply_parent_is_owner_post", "the parent is your own status")
    author = status.get("account")
    acct = str(author.get("acct", "")).strip().lstrip("@") if isinstance(author, dict) else ""
    if not acct or not _HANDLE.fullmatch(acct):
        raise ReplyRefused("reply_parent_malformed")
    handle = acct if "@" in acct else f"{acct}@{host}"
    parent_of = status.get("in_reply_to_id")
    if not isinstance(parent_of, str) or not parent_of:
        raise ReplyRefused("reply_target_outside_own_threads", "the parent is a top-level status, not a reply in your thread")
    context = _get(plan[1]["url"])
    ancestors = context.get("ancestors")
    if not isinstance(ancestors, list):
        raise ReplyRefused("reply_parent_malformed")
    ids = [str(a.get("id")) for a in ancestors if isinstance(a, dict)]
    if parent_of not in ids:
        raise ReplyRefused("reply_thread_unverifiable", "the parent's in_reply_to_id is not among its ancestors")
    owned = _owner_published(con, account["id"])
    root = next((a for a in ancestors if isinstance(a, dict) and str(a.get("id")) in owned
                 and _is_owner(a, local, host)), None)
    if root is None:
        raise ReplyRefused("reply_target_outside_own_threads",
                           "no ancestor is a status ReachWeaver published for this account")
    url = status.get("url") if isinstance(status.get("url"), str) else status.get("uri")
    if not isinstance(url, str) or urlparse(url).scheme != "https" or len(url) > 2048:
        raise ReplyRefused("reply_parent_malformed")
    _check_optout(optout, "mastodon", handle)
    text = html_to_text(status.get("content"))
    spoiler = status.get("spoiler_text")
    if isinstance(spoiler, str) and spoiler.strip():
        text = f"[CW: {spoiler.strip()}]\n{text}"
    label, accepted = _triage(text, False, override)
    own = account.get("visibility", "public")
    return {"v": TARGET_VERSION, "platform": "mastodon", "server": ref["server"], "in_reply_to_id": ref["id"],
            "root_post_id": owned[str(root["id"])]["post_id"], "root_remote_id": str(root["id"]),
            "parent_author": handle, "parent_url": url, "parent_visibility": visibility,
            "visibility": stricter_visibility(visibility, own), "verified_at": utc_now(),
            "triage_label": label, "override": accepted}


def verify_reply_target(con: sqlite3.Connection, account: dict, ref: dict, *, optout: list[dict],
                        override: str | None = None) -> dict:
    """Read-only, unauthenticated verification of one parent. Returns the target dict.

    Raises ``ReplyRefused`` (a reason code first) for every refusal, ``publishers.PublishError``
    for a sanitized network failure. Nothing is written anywhere.
    """
    if ref["platform"] != account["platform"]:
        raise ReplyRefused("reply_ref_platform_mismatch")
    if ref["platform"] == "bluesky":
        target = _verify_bluesky(con, account, ref, optout, override)
    else:
        target = _verify_mastodon(con, account, ref, optout, override)
    load_target(canonical_target(target))  # the stored form must pass the same strict check
    return target


# --- Stored form -------------------------------------------------------------------------------

def canonical_target(target: dict) -> str:
    """Sorted, compact JSON: the exact bytes stored in ``posts.reply_target`` and bound by approval."""
    return json.dumps(target, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def load_target(text: str | None) -> dict | None:
    """Strictly validate a stored target; None for an ordinary post. Any defect fails closed."""
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ReplyRefused("reply_target_invalid", "not JSON") from None
    platform = data.get("platform") if isinstance(data, dict) else None
    if platform not in TARGET_KEYS or set(data) != TARGET_KEYS[platform] or data["v"] != TARGET_VERSION:
        raise ReplyRefused("reply_target_invalid", "unknown version, platform or fields")
    ok = (isinstance(data["root_post_id"], str) and _LOCAL_POST_ID.fullmatch(data["root_post_id"])
          and isinstance(data["parent_author"], str) and _HANDLE.fullmatch(data["parent_author"])
          and isinstance(data["parent_url"], str) and urlparse(data["parent_url"]).scheme == "https"
          and isinstance(data["verified_at"], str) and _TIME.fullmatch(data["verified_at"])
          and data["triage_label"] in TRIAGE_LABELS
          and (data["override"] is None or data["override"] in FLAGGED_LABELS))
    if platform == "bluesky":
        ok = ok and _strong_ref(data["root"]) == data["root"] and _strong_ref(data["parent"]) == data["parent"] \
            and isinstance(data["parent_author_did"], str) and bool(_DID.fullmatch(data["parent_author_did"])) \
            and data["parent"]["uri"] != data["root"]["uri"]
    else:
        ok = ok and isinstance(data["server"], str) and isinstance(data["in_reply_to_id"], str) \
            and bool(_MASTODON_ID.fullmatch(data["in_reply_to_id"])) and isinstance(data["root_remote_id"], str) \
            and data["parent_visibility"] in ("public", "unlisted") and data["visibility"] in VISIBILITY_ORDER \
            and VISIBILITY_ORDER.index(data["visibility"]) >= VISIBILITY_ORDER.index(data["parent_visibility"])
        if ok:
            try:
                ok = require_https_public_server(data["server"]) == data["server"]
            except ValidationError:
                ok = False
    if not ok:
        raise ReplyRefused("reply_target_invalid", "a field has the wrong shape")
    return data


def row_target(row) -> dict | None:
    """``load_target`` of a posts row's ``reply_target`` column."""
    return load_target(reply_target_text(row))


def target_problems(target: dict, account: dict | None, optout: list[dict]) -> list[str]:
    """Reason codes that refuse approving or sending this reply now (empty = OK). Offline."""
    problems: list[str] = []
    if account is None or account.get("platform") != target["platform"]:
        return ["reply_account_mismatch"]
    if account.get("backend") is not None:
        problems.append("reply_backend_unsupported")
    if target["platform"] == "mastodon":
        try:
            server = require_https_public_server(str(account.get("server", "")))
        except ValidationError:
            server = ""
        if server != target["server"]:
            problems.append("reply_server_mismatch")
        own = account.get("visibility", "public")
        if own not in MASTODON_VISIBILITIES or VISIBILITY_ORDER.index(target["visibility"]) < VISIBILITY_ORDER.index(own):
            problems.append("reply_visibility_wider_than_account")  # owner tightened it since the draft
    handles = [target["parent_author"]] + ([target["parent_author_did"]] if target["platform"] == "bluesky" else [])
    if any(listed_entry(h, optout, target["platform"]) is not None for h in handles):
        problems.append("reply_parent_author_optout_listed")
    return problems


def bluesky_reply_ref(target: dict) -> dict:
    """The record's ``reply`` field (app.bsky.feed.post#replyRef)."""
    return {"root": dict(target["root"]), "parent": dict(target["parent"])}


def reply_view(target: dict) -> dict[str, Any]:
    """What ``show`` displays for human review: where the reply lands and why it was allowed."""
    view: dict[str, Any] = {"kind": "reply", "platform": target["platform"], "parent_url": target["parent_url"],
                            "parent_author": target["parent_author"], "root_post_id": target["root_post_id"],
                            "triage_label": target["triage_label"], "override_flagged": target["override"],
                            "verified_at": target["verified_at"]}
    if target["platform"] == "bluesky":
        view["reply_ref"] = bluesky_reply_ref(target)
    else:
        view.update(server=target["server"], in_reply_to_id=target["in_reply_to_id"],
                    parent_visibility=target["parent_visibility"], visibility=target["visibility"])
    return view
