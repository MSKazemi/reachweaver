"""Explicit online account-identity preflight; no social content writes.

Only an owner who has provisioned environment credentials should run this.
Bluesky's session endpoint authenticates (POST) but creates no social post.
LinkedIn reads GET /v2/userinfo, which cannot prove w_member_social; an account
that opts in with "scope_check": "introspect" is also checked with LinkedIn's
read-only token introspection (issue #14, owner-approved 2026-10-09).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse, urlencode

import weakref

from . import publishers
from .accounts import bluesky_service, missing_credentials, require_credential, scope_check_enabled
from .policy import (SUPPORTED_LIVE, SUPPORTED_LIVE_VIDEO, ValidationError, mastodon_text_limit,
                     require_https_public_server)


@dataclass(frozen=True)
class VerifiedConnection:
    account_id: str
    platform: str
    identity: str
    write_permission: str  # verified, or unverified (read-only test cannot prove scope)
    notes: tuple[str, ...] = ()  # non-secret advisories (e.g. LinkedIn expiry consistency)


# Objects returned by check_connection in this process, by identity (wave 9 item 3). A hand-built
# VerifiedConnection compares equal field by field but is not in here, so publish() re-checks it.
_ISSUED: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def was_issued_by_check(value: object) -> bool:
    return isinstance(value, VerifiedConnection) and _ISSUED.get(id(value)) is value


def _required_identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Expected {field} missing from accounts metadata. Update the local ignored config first.")
    return value.strip().lstrip("@").lower()


def _bluesky(account: dict) -> VerifiedConnection:
    prefix = account["env_prefix"]
    expected = _required_identity(account.get("handle"), "Bluesky handle")
    service = bluesky_service(account)  # PDS/entryway origin; never taken from the didDoc
    data = publishers.request_json(
        service + "/xrpc/com.atproto.server.createSession",
        {"identifier": require_credential(prefix + "_IDENTIFIER"), "password": require_credential(prefix + "_APP_PASSWORD")},
    )
    actual = data.get("handle")
    if not isinstance(actual, str) or actual.casefold() != expected.casefold():
        raise ValidationError("identity_mismatch: Bluesky session handle does not match the configured account")
    if not isinstance(data.get("did"), str) or not data["did"].startswith("did:") or not data.get("accessJwt"):
        raise publishers.PublishError("bluesky_session_missing_identity")
    if data.get("active") is False:
        raise ValidationError("bluesky_account_inactive")
    return VerifiedConnection(account["id"], "bluesky", "verified", "unverified")


def _mastodon(account: dict) -> VerifiedConnection:
    expected = _required_identity(account.get("handle"), "Mastodon username or username@instance")
    instance = urlparse(account["server"]).hostname or ""
    parts = expected.split("@")
    if len(parts) > 2 or not parts[0] or (len(parts) == 2 and parts[1] != instance):
        raise ValidationError("mastodon_expected_handle_must_be_local_to_configured_server")
    actual = publishers.request_get_json(
        account["server"] + "/api/v1/accounts/verify_credentials",
        {"Authorization": f"Bearer {require_credential(account['env_prefix'] + '_TOKEN')}"},
    )
    username = actual.get("username")
    if not isinstance(username, str) or username.casefold() != parts[0].casefold() or not actual.get("id"):
        raise ValidationError("identity_mismatch: Mastodon token belongs to a different account")
    return VerifiedConnection(account["id"], "mastodon", "verified", "unverified")


def _telegram(account: dict) -> VerifiedConnection:
    expected_bot = _required_identity(account.get("bot_username"), "Telegram bot_username")
    prefix = account["env_prefix"]
    token = require_credential(prefix + "_BOT_TOKEN")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValidationError("telegram_bot_token_format_invalid")
    base = "https://api.telegram.org/bot" + token
    bot = publishers.request_get_json(base + "/getMe", telegram_body=True)
    if bot.get("ok") is not True or not isinstance(bot.get("result"), dict):
        raise publishers.PublishError("telegram_getme_invalid")
    user = bot["result"]
    if not user.get("is_bot") or str(user.get("username", "")).casefold() != expected_bot.casefold():
        raise ValidationError("identity_mismatch: Telegram bot username does not match")
    bot_id = user.get("id")
    if type(bot_id) is not int:
        raise publishers.PublishError("telegram_getme_missing_id")
    chat_id = str(account["chat_id"]).strip()
    if not re.fullmatch(r"(?:@[A-Za-z0-9_]{5,32}|-?[0-9]+)", chat_id):
        raise ValidationError("telegram_chat_id_invalid")
    chat = publishers.request_get_json(base + "/getChat?" + urlencode({"chat_id": chat_id}), telegram_body=True)
    if chat.get("ok") is not True or not isinstance(chat.get("result"), dict):
        raise publishers.PublishError("telegram_getchat_invalid")
    destination = chat["result"]
    if destination.get("type") != "channel":
        raise ValidationError("telegram_only_channels_supported_for_automated_publishing")
    username = destination.get("username")
    expected_channel = account.get("channel_username")
    if chat_id.startswith("@") and (not isinstance(username, str) or username.casefold() != chat_id[1:].casefold()):
        raise ValidationError("identity_mismatch: Telegram chat_id username does not match")
    if expected_channel and (not isinstance(username, str) or username.casefold() != str(expected_channel).lstrip("@").casefold()):
        raise ValidationError("identity_mismatch: Telegram configured channel_username does not match")
    if not chat_id.startswith("@") and str(destination.get("id")) != chat_id:
        raise ValidationError("identity_mismatch: Telegram numeric destination ID does not match")
    member = publishers.request_get_json(base + "/getChatMember?" + urlencode({"chat_id": chat_id, "user_id": bot_id}),
                                           telegram_body=True)
    if member.get("ok") is not True or not isinstance(member.get("result"), dict):
        raise publishers.PublishError("telegram_getchatmember_invalid")
    rights = member["result"]
    if rights.get("status") != "creator" and not (rights.get("status") == "administrator" and rights.get("can_post_messages") is True):
        raise ValidationError("telegram_bot_cannot_post_to_channel")
    return VerifiedConnection(account["id"], "telegram", "verified", "verified")


def _linkedin(account: dict) -> VerifiedConnection:
    # Offline version/expiry/organization gates, then GET /v2/userinfo 'sub' == person_id.
    # Only with "scope_check": "introspect" (issue #14): token introspection must
    # also show an active member token with w_member_social; otherwise scope stays unverified.
    from .linkedin import verify_identity, verify_write_scope
    verify_identity(account)
    if not scope_check_enabled(account):
        return VerifiedConnection(account["id"], "linkedin", "verified", "unverified")
    scope = verify_write_scope(account)
    return VerifiedConnection(account["id"], "linkedin", "verified", "verified", notes=scope.notes)


def _instagram(account: dict) -> VerifiedConnection:
    # Issue #20 (owner-approved 2026-10-09; disabled until a live receipt), unreachable while instagram is outside SUPPORTED_LIVE. GET /me user_id,
    # username and a professional account_type must match, then the read-only quota preflight,
    # whose documented permissions include instagram_business_content_publish.
    from .instagram import verify_identity
    verify_identity(account)
    return VerifiedConnection(account["id"], "instagram", "verified", "verified")


def _tiktok(account: dict) -> VerifiedConnection:
    # Issue #23 (owner-approved 2026-10-09; disabled until a live receipt), unreachable while tiktok
    # is outside SUPPORTED_LIVE_VIDEO: the registry gate runs first, then GET /v2/user/info/ open_id
    # and display_name must equal the config. No read-only probe exists for video.upload, so the
    # write permission stays unverified.
    from .tiktok import check_connection as tiktok_check
    tiktok_check(account)
    return VerifiedConnection(account["id"], "tiktok", "verified", "unverified")


def _youtube(account: dict) -> VerifiedConnection:
    # Issue #22 (owner-approved 2026-10-09; disabled until a live receipt), unreachable while youtube
    # is outside SUPPORTED_LIVE_VIDEO. channels.list mine=true must return exactly the configured
    # channel id; it cannot prove youtube.upload.
    from .youtube import verify_identity
    verify_identity(account)
    return VerifiedConnection(account["id"], "youtube", "verified", "unverified")


def _threads(account: dict) -> VerifiedConnection:
    # Issue #19 (owner-approved 2026-10-09; disabled until a live receipt), unreachable while
    # threads is outside SUPPORTED_LIVE. GET /me id and
    # username must equal the config, then the read-only quota preflight; the quota endpoint
    # requires threads_content_publish, so a successful read verifies the write permission.
    from .threads import verify_identity
    verify_identity(account)
    return VerifiedConnection(account["id"], "threads", "verified", "verified")


def _facebook(account: dict) -> VerifiedConnection:
    # Issue #21 (owner-approved 2026-10-09; disabled until a live receipt), unreachable while
    # facebook is outside SUPPORTED_LIVE. GET /me must be the configured Page (proves a Page
    # token), then GET /{page-id} id and name must match. No documented read proves
    # pages_manage_posts, so the write permission stays unverified.
    from .facebook import verify_identity
    verify_identity(account)
    return VerifiedConnection(account["id"], "facebook", "verified", "unverified")


_CHECKS = {"bluesky": _bluesky, "mastodon": _mastodon, "telegram": _telegram, "linkedin": _linkedin,
           "instagram": _instagram, "tiktok": _tiktok, "youtube": _youtube, "threads": _threads,
           "facebook": _facebook}


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def mastodon_instance_limits(account: dict) -> dict:
    """Public, unauthenticated GET /api/v2/instance (Mastodon >= 4.0); no token sent.

    Reports the instance's own status and media limits and compares them with the
    owner-pinned ``max_characters``. Nothing is persisted; the owner updates the
    local config by hand. ``ok`` is False when the pinned value exceeds the instance.
    """
    if account.get("platform") != "mastodon":
        raise ValidationError("instance limits are only defined for Mastodon accounts")
    server = require_https_public_server(str(account.get("server", "")))
    data = publishers.request_get_json(server + "/api/v2/instance")
    config = data.get("configuration")
    if not isinstance(config, dict):
        raise publishers.PublishError("mastodon_instance_missing_configuration")
    statuses = cast(dict, config.get("statuses")) if isinstance(config.get("statuses"), dict) else {}
    media = cast(dict, config.get("media_attachments")) if isinstance(config.get("media_attachments"), dict) else {}
    instance_max = _positive_int(statuses.get("max_characters"))
    mimes = media.get("supported_mime_types")
    domain = data.get("domain")
    configured = account.get("max_characters")
    effective = mastodon_text_limit(account)
    problems, suggestions = [], []
    if instance_max is None:
        problems.append("instance did not report configuration.statuses.max_characters")
    else:
        if type(configured) is int and configured > instance_max:
            problems.append("configured max_characters exceeds the instance value; lower it")
        if effective > instance_max:
            problems.append("effective ReachWeaver limit exceeds the instance value; pin max_characters")
        if configured is None:
            suggestions.append(f"optionally pin \"max_characters\": {instance_max} in the local account config")
    return {
        "domain": domain if isinstance(domain, str) and len(domain) <= 253 else None,
        "statuses": {k: _positive_int(statuses.get(k)) for k in
                     ("max_characters", "max_media_attachments", "characters_reserved_per_url")},
        "media_attachments": {
            **{k: _positive_int(media.get(k)) for k in
               ("image_size_limit", "image_matrix_limit", "video_size_limit",
                "video_frame_rate_limit", "video_matrix_limit", "description_limit")},
            "supported_mime_types": len(mimes) if isinstance(mimes, list) else None,
        },
        "configured_max_characters": configured,
        "effective_text_limit": effective,
        "ok": not problems,
        "problems": problems,
        "suggestions": suggestions,
    }


def check_connection(account: dict) -> VerifiedConnection:
    """Check one configured identity; do not persist tokens or remote API responses.

    Must only be invoked by explicit connect-check --online or during an approved
    live send. A successful check does not authorize the content of any post.
    """
    if account.get("backend") is not None:
        raise ValidationError("backend_account: use 'postiz-check --account ID --online' for Postiz-backed accounts")
    # SUPPORTED_LIVE_VIDEO (#22 YouTube, #23 TikTok) is empty, so every video platform is refused here today.
    if account["platform"] not in SUPPORTED_LIVE and account["platform"] not in SUPPORTED_LIVE_VIDEO:
        raise ValidationError("platform_has_no_authorized_live_connector")
    missing = missing_credentials(account)
    if missing:
        raise ValidationError("missing_credentials: " + ", ".join(missing))
    issued = _CHECKS[account["platform"]](account)
    _ISSUED[id(issued)] = issued
    return issued
