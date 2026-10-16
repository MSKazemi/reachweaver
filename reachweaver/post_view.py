"""Offline, read-only views of one stored post (shared by ``show`` and the local dashboard).

Everything here describes state; nothing approves, schedules, publishes or writes. A platform
preview is what an adapter would send, computed without network or credentials. The functions
were moved unchanged from ``cli.py`` (issue #7 approval view, #13/#20 media, #22/#23 video).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import repeat_guard
from .capabilities import require_capability
from .policy import CONSERVATIVE_TEXT_LIMITS, LINKEDIN_ESCAPED_MAX, ValidationError, mastodon_text_limit
from .richtext import bluesky_preview, deterministic_tid, mastodon_length
from .accounts import bluesky_service
from .storage import (
    APPROVAL_KEY_UNAVAILABLE, approval_binding_problem, approval_payload_sha256, binding_refusal,
    offline_key_mode, parse_utc, post_media, post_video,
)
from .video import mode as video_mode


def offline_binding_problem(post, mode) -> str | None:
    """Binding check without the key (#7 D7): a keyed tag is verified only where the key is read."""
    problem = approval_binding_problem(post, mode)
    return None if problem == APPROVAL_KEY_UNAVAILABLE else problem


def approval_view(post, mode=None) -> dict:
    """Approval binding state for ``show`` (#7): hashes and attribution, never a decision."""
    view: dict = {"binding": "not_approved"}
    if all(name in post.keys() for name in ("approved_payload_sha256", "approved_by", "approval_channel")):
        view.update(payload_sha256=post["approved_payload_sha256"], approved_by=post["approved_by"],
                    approval_channel=post["approval_channel"])
    if "approved_payload_mac" in post.keys():  # #7 D7: whether a keyed tag exists, never the tag
        view["keyed_tag"] = "present" if post["approved_payload_mac"] else None
    if post["status"] in ("approved", "scheduled"):
        problem = offline_binding_problem(post, mode)
        view["binding"] = problem or "bound"
        if problem:
            view["reapprove"] = binding_refusal(problem, post["id"])
        ttl = getattr(mode, "max_age_hours", None)
        if ttl and post["approved_at"]:
            view["approval_expires_at"] = (datetime.fromisoformat(parse_utc(post["approved_at"]))
                                           + timedelta(hours=ttl)).isoformat(timespec="seconds")
    elif post["status"] == "draft":
        # What 'approve' would bind right now (approve always clears scheduled_at).
        view["approve_would_bind_sha256"] = approval_payload_sha256(post, scheduled_at=None, use_row_schedule=False)
        view["approve_adds"] = "a fresh per-approval nonce (approval/2) that this digest does not include"
    return view


def media_view(post) -> dict | None:
    """Offline review of a post's attachments (#13, #20): records, local file check, live capability.

    A hosted image (#20) is not downloaded; its view says how to verify it yourself."""
    from .attachments import describe
    try:
        media = post_media(post)
    except ValidationError as exc:
        return {"error": str(exc), "send": "refused until the attachments are re-created"}
    if not media:
        return None
    try:
        require_capability(post["platform"], "image", "publish")
        capability = "enabled in the capability registry"
    except ValidationError as exc:
        capability = f"refused: {exc}"
    attachments = describe(media)
    view: dict = {"count": len(media), "attachments": attachments, "live_image_publish": capability}
    local = [item for item in attachments if item["source"] == "local"]
    if local:
        view["all_files_match_record"] = all(item["file_matches_record"] for item in local)
    if len(local) == len(attachments):
        view["bound_by_approval"] = "each attachment's sha256, mime, alt and focus, in this order"
    else:
        view["bound_by_approval"] = "each attachment's sha256, mime, alt, focus and url, in this order"
        view["sent_as"] = "image_url (Meta downloads it from your host), caption, alt_text"
    if any(item.get("rights") for item in attachments):  # #48
        view["bound_by_approval"] += ", plus each rights record (license, attribution, provenance, alt translations; approval/media-2)"
        view["rights_not_sent"] = "rights and alt translations are stored and bound but never published"
    return view


def platform_preview(post, account: dict | None) -> dict | None:
    """Compact, offline description of what an adapter would send (no network)."""
    content = post["content"]
    if account is not None and account.get("backend") is not None:
        # Issue #30/#56: a backend account never reaches the native adapter, so show the backend's
        # own offline preview (no credential, no network) instead of a native record it would not send.
        from .publishers import backend_for
        try:
            preview = dict(backend_for(account).preview(account, content))
        except ValidationError as exc:
            return {"path": account["backend"], "refused": str(exc), "network": "none"}
        preview.pop("content", None)  # 'show' already prints the exact text once
        preview["handle"] = account.get("handle")
        preview["receipt"] = ("a backend acceptance is needs_review, never published, until a human checks the "
                              "native post and runs 'reconcile' with its id and URL")
        preview["manual_fallback"] = ("if the backend is unavailable the post stays approved; post this exact "
                                      "text natively yourself, then 'cancel' it here so it is never sent")
        return preview
    if post["platform"] == "bluesky":
        preview = bluesky_preview(content)
        try:
            preview["record_key"] = deterministic_tid(post["id"], post["created_at"])
        except ValueError:
            preview["record_key"] = None
        preview["handle"] = account.get("handle") if account else None
        preview["service"] = bluesky_service(account) if account else None
        return preview
    if post["platform"] == "mastodon":
        if account is None:
            return {"account_config": "not loaded; visibility and instance limit unknown"}
        return {"server": account["server"], "handle": account.get("handle"),
                "visibility": account.get("visibility", "public"),
                "language": account.get("language"), "spoiler_text": account.get("spoiler_text"),
                "counted_length": mastodon_length(content), "text_limit": mastodon_text_limit(account),
                "idempotency_key": post["id"], "idempotency_window": "1 hour (server-side)"}
    if post["platform"] == "linkedin":  # private #14: account config only, never the token
        from .linkedin import VISIBILITY, author_urn, escape_commentary
        member_urn = None
        if account is not None and account.get("person_id"):
            try:
                member_urn = author_urn(account["person_id"])
            except ValidationError:
                member_urn = None
        counted, escaped = len(content), len(escape_commentary(content))
        preview = {"account_id": post["account_id"], "handle": account.get("handle") if account else None,
                   "member_urn": member_urn, "visibility": VISIBILITY,
                   "counted_length": counted, "text_limit": CONSERVATIVE_TEXT_LIMITS["linkedin"],
                   "length_method": "Unicode code points",
                   "escaped_commentary_length": escaped, "escaped_commentary_limit": LINKEDIN_ESCAPED_MAX,
                   "within_limits": counted <= CONSERVATIVE_TEXT_LIMITS["linkedin"] and escaped <= LINKEDIN_ESCAPED_MAX,
                   "dry_run": "show sends nothing; publish without --live is a dry run with no request; a live "
                              "send needs approval, REACHWEAVER_ENV=prod, REACHWEAVER_ALLOW_LIVE=1 and --live"}
        if account is None:
            preview["account_config"] = "not loaded; member URN unknown"
        return preview
    return None


def video_issues(post) -> list[str]:
    """Blocking problems of a post's video (#22, #23), offline: malformed record, a record built for
    another platform, images and a video together, or a changed/unusable file."""
    from .video import BOTH_KINDS, file_issue
    try:
        record = post_video(post)
        if record is None:
            return []
        if "media_json" in post.keys() and post["media_json"]:
            return [f"video: {BOTH_KINDS}"]
        if record["platform"] != post["platform"]:
            return [f"video: record is for {record['platform']}, not this {post['platform']} post"]
        issue = file_issue(record)
    except ValidationError as exc:
        return [f"video: {exc}"]
    return [f"video: {issue}"] if issue else []


def video_view(post) -> dict | None:
    """Offline review of a post's video (#22, #23): record, file check and the registry switches."""
    from .capabilities import VIDEO_MODES, require_video_mode
    try:
        record = post_video(post)
    except ValidationError as exc:
        return {"error": str(exc), "send": "refused until the video is re-attached"}
    if record is None:
        return None
    issues = video_issues(post)
    live = {}
    for mode in VIDEO_MODES.get(record["platform"], {}):
        try:
            require_video_mode(record["platform"], mode)
            live[mode] = "enabled in the capability registry"
        except ValidationError as exc:
            live[mode] = f"refused: {exc}"
    view = {"record": record, "file_matches_record": not issues, "problems": issues, "live": live,
            "bound_by_approval": "the whole video record except its local path (file sha256, size, parsed "
                                 "headers, platform_options) and the text"}
    if record["platform"] == "youtube":  # #22 review fields: exactly what the upload sends
        from .video import file_issue
        options = record["platform_options"]
        view.update(title=options["title"], tags=options["tags"], category_id=options["category_id"],
                    privacy_status=options["privacy_status"], made_for_kids=options["self_declared_made_for_kids"],
                    file_status=file_issue(record) or "matches_recorded_sha256", live_upload=live.get("upload"))
    if record["platform"] == "tiktok":
        direct = video_mode(record) == "direct_post"
        view["caption_sent"] = ("yes, as post_info.title" if direct
                                else "no: in creator_inbox mode the creator writes the caption in the TikTok app")
        view["public_after_send"] = ("only for direct_post when TikTok returns a public post id; a creator_inbox "
                                     "upload is never recorded as published")
    return view



def repeat_findings(db, post, days: int = repeat_guard.DEFAULT_DAYS) -> list[dict]:
    """Repeat findings for a stored post against the platform's history (read-only; never carries earlier text)."""
    try:
        material = repeat_guard.material_of_post(post)
    except ValidationError:  # a malformed attachment is refused elsewhere; still compare the text
        material = {"text": post["content"], "images": [], "video": None, "links": []}
    return repeat_guard.find_repeats(db, platform=post["platform"], account_id=post["account_id"], material=material,
                                     exclude_post_id=post["id"], days=days)


SHOW_FIELDS = ("id", "account_id", "platform", "status", "content", "campaign", "source_url", "scheduled_at",
               "approved_at")


def build_post_view(db, post, account: dict | None) -> dict:
    """The review sheet ``show`` prints: exact fields plus approval, media, video, reply, preview, cost.

    ``db`` is only read (``offline_key_mode`` inspects settings); ``account`` is metadata or None."""
    shown = {key: post[key] for key in SHOW_FIELDS}
    shown["approval"] = approval_view(post, offline_key_mode(db))
    media = media_view(post)
    if media is not None:
        shown["media"] = media
    video = video_view(post)
    if video is not None:
        shown["video"] = video
    from .replies import reply_view, row_target
    try:
        target = row_target(post)
        if target is not None:
            shown["reply"] = reply_view(target)
    except ValidationError as exc:
        shown["reply"] = {"kind": "reply", "invalid": str(exc)}
    preview = platform_preview(post, account)
    if preview is not None:
        shown["platform_preview"] = preview
    try:
        shown["repeat_check"] = repeat_guard.summary(repeat_findings(db, post))
    except sqlite3.Error:  # a read-only open of a database that predates the repeat-history tables
        shown["repeat_check"] = {"blocking": 0, "warnings": 0, "findings": [],
                                 "unavailable": "history tables missing; run 'reachweaver init' to upgrade"}
    from .capabilities import api_cost_note
    shown["api_cost"] = api_cost_note(post["platform"])  # #39: estimated platform/API cost
    return shown
