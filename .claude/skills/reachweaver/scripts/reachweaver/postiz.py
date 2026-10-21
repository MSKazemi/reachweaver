"""Optional, owner-selected Postiz public-API publishing backend (issue #30).

Postiz is a *backend* (a scheduler that holds the owner's platform OAuth
connections), not a platform. ReachWeaver stays the approval authority:

* Only an account with an explicit ``"backend": "postiz"`` entry is routed here;
  there is no implicit or default backend.
* A send still needs the normal per-item approval, ``publish --live`` plus
  ``REACHWEAVER_ALLOW_LIVE=1`` and ``REACHWEAVER_ENV=prod`` (checked by the CLI before
  any account is loaded, issue #5), the emergency stop, the rolling budget and the
  durable ``claim_sending`` claim. The read-only integration identity preflight
  runs BEFORE that claim; failure leaves the approved post untouched.
* The approval payload hash (#7) is checked before the identity preflight and again
  inside the claim lock; an unbound (legacy) or changed post is BLOCKED and stays
  approved/scheduled until a human re-approves it.
* Exactly one integration, the exact approved text, Postiz type ``now`` only.
  Postiz ``draft``/``schedule`` and any Postiz-side approval feature are never
  used: a future-dated Postiz job would escape ReachWeaver's pause switch.
* A Postiz acceptance is NOT a platform receipt. The post goes to
  ``needs_review`` with ``remote_id='postiz:<id>'`` until a human confirms the
  native post and runs ``reconcile``. Any post-claim failure is also
  ``needs_review``; nothing is retried automatically.
* The API key comes only from ``{env_prefix}_POSTIZ_API_KEY``. Neither the key
  nor any response body is ever placed in errors, output or the audit log.
* Rate limits (#11): a 429 records a per-account ``provider_cooldown`` (default 15
  minutes; a standard ``Retry-After`` header is honoured, clamped to 60 s..24 h) that
  delays the account's other unclaimed posts. Since #11 (owner-approved 2026-10-09), accounts that
  declare the same ``postiz_workspace`` share that cooldown, because Postiz throttles
  ``POST /public/v1/posts`` per organization (see ``ratelimit.cooldown_scopes``).
  A post-claim Postiz 429 is ambiguous (C3): ``POST /posts`` documents no idempotency and no statement on job creation. The
  read-only ``GET /integrations`` preflight may be retried at most twice with jitter
  before the claim; ``POST /posts`` is never retried.

API facts (docs.postiz.com, verified 2026-10-08; see docs/POSTIZ.md): cloud base
``https://api.postiz.com/public/v1``, self-hosted ``https://<host>/api/public/v1``,
raw key in the ``Authorization`` header, ``GET /integrations``,
``POST /posts`` returning ``[{postId, integration}]`` and ``GET /posts`` with
``state``/``releaseURL``. No Postiz source code is copied (Postiz is AGPL-3.0).
"""
from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from . import publishers
from .policy import ValidationError, require_https_public_server, validate_post
from .publishers import PublishError

BACKEND = "postiz"
CLOUD_HOST = "api.postiz.com"
API_PREFIXES = ("/public/v1", "/api/public/v1")
CREDENTIAL_ENV_SUFFIX = "_POSTIZ_API_KEY"
ALLOWED_KEYS = frozenset({"postiz_base_url", "postiz_integration_id", "postiz_api_prefix", "postiz_workspace"})
REMOTE_PREFIX = "postiz:"
ACCEPTED_REASON = "postiz_accepted_platform_receipt_unverified"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_API_KEY = re.compile(r"^[\x21-\x7e]{8,512}$")  # printable, no whitespace/CR/LF header injection

# ReachWeaver platform -> Postiz provider identifiers accepted for that platform.
# Identifiers verified against GET /integrations "identifier" and the provider
# settings pages on 2026-10-08. Every other platform is refused (fail closed).
PROVIDERS: dict[str, tuple[str, ...]] = {
    "bluesky": ("bluesky",),
    "mastodon": ("mastodon",),
    "telegram": ("telegram",),
    "threads": ("threads",),
    "linkedin": ("linkedin", "linkedin-page"),
    "facebook": ("facebook",),
}
EXCLUDED: dict[str, str] = {
    "reddit": "Reddit is human-only and native scheduling is moderator-only; no backend may post",
    "x": "X needs the owner cost ceiling and spend ledger from issue #24 first",
    "instagram": "Instagram text-only posts are unavailable",
    "pinterest": "Pinterest text-only Pins are unavailable",
    "youtube": "YouTube text posting is unknown/unsupported (video-only)",
    "tiktok": "TikTok text posting is unknown/unsupported (video-only)",
    "devto": "DEV is manual-export only (human-authored articles; Postiz needs a title setting)",
    "hashnode": "Hashnode is manual-export only (Postiz needs title/tags settings)",
}
_BACKEND_TEXT_FORMATS = frozenset({"api_direct", "api_gated"})
_BACKEND_PLATFORM_STATUSES = frozenset({"supported", "requires-review"})


class BackendAccepted(PublishError):
    """Postiz accepted the job. NOT a platform receipt: callers must use needs_review.

    Subclassing PublishError means any generic caller of publishers.publish that
    maps PublishError to ``uncertain_send`` is automatically safe.
    """

    def __init__(self, backend_ref: str):
        super().__init__(f"{ACCEPTED_REASON} backend_ref={backend_ref}")
        self.backend_ref = backend_ref


@dataclass(frozen=True)
class VerifiedIntegration:
    account_id: str
    platform: str
    integration_id: str
    provider: str
    identity: str = "verified"
    write_permission: str = "unverified"  # Postiz exposes no platform scopes


def is_backend_account(account: dict) -> bool:
    return account.get("backend") is not None


def validate_backend_account(raw: dict) -> None:
    """Validate (and normalize in place) one accounts.json entry with a backend."""
    account_id = raw.get("id")
    if raw.get("backend") != BACKEND:
        raise ValidationError(f"Unknown publishing backend for account {account_id}; only 'postiz' is supported")
    unknown = sorted(k for k in raw if k.startswith("postiz_") and k not in ALLOWED_KEYS)
    if unknown:
        raise ValidationError(f"Unknown Postiz setting(s) for account {account_id}: {', '.join(unknown)}")
    platform = raw.get("platform")
    if platform not in PROVIDERS:
        reason = EXCLUDED.get(cast(str, platform), "no verified Postiz text provider mapping")
        raise ValidationError(f"Postiz backend refused for {platform} account {account_id}: {reason}")
    raw["postiz_base_url"] = require_https_public_server(str(raw.get("postiz_base_url", "")))
    host = raw["postiz_base_url"][len("https://"):]
    prefix = raw.get("postiz_api_prefix", "/public/v1" if host == CLOUD_HOST else "/api/public/v1")
    if prefix not in API_PREFIXES:
        raise ValidationError(f"postiz_api_prefix for account {account_id} must be one of {', '.join(API_PREFIXES)}")
    raw["postiz_api_prefix"] = prefix
    integration = raw.get("postiz_integration_id")
    if not isinstance(integration, str) or not _SAFE_ID.fullmatch(integration):
        raise ValidationError(f"postiz_integration_id for account {account_id} must be 1-64 chars of A-Z a-z 0-9 _ -")
    handle = raw.get("handle")
    if not isinstance(handle, str) or not handle.strip().lstrip("@"):
        raise ValidationError(f"Postiz account {account_id} needs 'handle' = the integration profile shown by postiz-check")
    if "postiz_workspace" in raw:  # #11: owner-declared shared cooldown group
        from .ratelimit import POSTIZ_WORKSPACE
        workspace = raw["postiz_workspace"]
        if not isinstance(workspace, str) or not POSTIZ_WORKSPACE.fullmatch(workspace):
            raise ValidationError(f"postiz_workspace for account {account_id} must be a lowercase slug "
                                  "(a-z 0-9 -, 1-63 chars, starting with a letter or digit)")


def require_backend_capability(platform: str, *, registry: dict | None = None) -> dict:
    """Fail closed unless the registry and the Postiz mapping both allow text.

    Stricter than "text is not unavailable": unknown/manual_export text formats
    and blocked/manual-export platforms are refused too. Reddit is refused
    unconditionally (human-only, moderator-only scheduling).
    """
    from .capabilities import load_registry, validate_registry
    if platform == "reddit":
        raise ValidationError("reddit is human-only/moderator-only; Postiz backend refused (fail closed)")
    if platform not in PROVIDERS:
        raise ValidationError(f"Postiz backend refused for {platform}: {EXCLUDED.get(platform, 'no verified provider mapping')}")
    data = registry if registry is not None else load_registry()
    if registry is not None:
        errors = validate_registry(data)
        if errors:
            raise ValidationError("Invalid capability registry: " + "; ".join(errors))
    entry = next((e for e in data["platforms"] if e["id"] == platform), None)
    if entry is None:
        raise ValidationError(f"Unknown platform capability: {platform}")
    text = entry["formats"]["text"]
    if text not in _BACKEND_TEXT_FORMATS:
        raise ValidationError(f"{platform} text is {text}; Postiz backend refused (fail closed)")
    if entry["status"] not in _BACKEND_PLATFORM_STATUSES:
        raise ValidationError(f"{platform} status is {entry['status']}; Postiz backend refused (fail closed)")
    return entry


def missing_credentials(account: dict) -> list[str]:
    from .accounts import credential
    name = account["env_prefix"] + CREDENTIAL_ENV_SUFFIX
    return [] if credential(name) else [name]  # environment or $CREDENTIALS_DIRECTORY; names only


def _api_key(account: dict) -> str:
    from .accounts import require_credential
    missing = missing_credentials(account)
    if missing:
        raise ValidationError("missing_credentials: " + ", ".join(missing))
    key = require_credential(account["env_prefix"] + CREDENTIAL_ENV_SUFFIX)
    if not _API_KEY.fullmatch(key):
        raise ValidationError("postiz_api_key_format_invalid")  # never echo the value
    return key


def api_base(account: dict) -> str:
    return account["postiz_base_url"] + account["postiz_api_prefix"]


def _call(account: dict, method: str, path: str, payload: dict | None = None):
    """One bounded, no-redirect request. Errors are sanitized status codes only."""
    headers = {"Authorization": _api_key(account), "Accept": "application/json",
               "User-Agent": "ReachWeaver/0.3"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        req = Request(api_base(account) + path, data=body, method=method, headers=headers)
        with publishers._send_http(req) as response:
            raw = response.read(1_000_001)
    except HTTPError as exc:
        label = {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(exc.code)
        # Status and a bounded Retry-After hint only (issue #11, D15); the body is never read.
        try:
            raise publishers.http_error(exc).with_code(
                f"postiz_http_{exc.code}" + (f"_{label}" if label else "")) from None
        finally:
            publishers.close_http_error(exc)
    except (URLError, TimeoutError, OSError, http.client.HTTPException):
        raise PublishError("postiz_network_or_timeout_uncertain") from None
    except ValueError:
        # InvalidURL is an HTTPException above; a ValueError here (e.g. an invalid header value) can quote
        # the header, so only a fixed code is raised. Uncertain: the send may or may not have started.
        raise PublishError("postiz_request_invalid_uncertain") from None
    if len(raw) > 1_000_000:
        raise PublishError("postiz_response_too_large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise PublishError("postiz_invalid_response_uncertain") from None


def list_integrations(account: dict) -> list[dict]:
    """Read-only GET /integrations, reduced to non-secret routing fields."""
    data = _call(account, "GET", "/integrations")
    if not isinstance(data, list):
        raise PublishError("postiz_integrations_unexpected_response")
    rows = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        rows.append({"id": item["id"], "identifier": item.get("identifier") if isinstance(item.get("identifier"), str) else None,
                     "profile": item.get("profile") if isinstance(item.get("profile"), str) else None,
                     "disabled": item.get("disabled") is True})
    return rows


def match_integration(account: dict, rows: list[dict]) -> VerifiedIntegration:
    """Pure identity check: configured id exists, maps to platform+handle, enabled."""
    wanted = account["postiz_integration_id"]
    row = next((r for r in rows if r["id"] == wanted), None)
    if row is None:
        raise ValidationError("identity_mismatch: Postiz integration id not found for this API key's organization")
    allowed = PROVIDERS.get(account["platform"], ())
    if row["identifier"] not in allowed:
        raise ValidationError(f"identity_mismatch: Postiz integration provider is not {account['platform']}")
    expected = str(account.get("handle", "")).strip().lstrip("@").casefold()
    actual = (row["profile"] or "").strip().lstrip("@").casefold()
    if not expected or actual != expected:
        raise ValidationError("identity_mismatch: Postiz integration profile does not match configured handle")
    if row["disabled"]:
        raise ValidationError("postiz_integration_disabled")
    return VerifiedIntegration(account["id"], account["platform"], wanted, row["identifier"])


def verify_integration(account: dict) -> VerifiedIntegration:
    """Backend-capability gate + read-only identity preflight. Call BEFORE claiming."""
    if account.get("backend") != BACKEND:
        raise ValidationError("Account is not configured with backend 'postiz'")
    require_backend_capability(account["platform"])
    _api_key(account)
    return match_integration(account, list_integrations(account))


def build_payload(integration: VerifiedIntegration, content: str, now: datetime | None = None) -> dict:
    """Exactly one integration, exact approved text, immediate type, no short links."""
    return {
        "type": "now",  # never "draft"/"schedule": ReachWeaver's outbox owns timing and pause
        "date": _iso_ms(now or datetime.now(timezone.utc)),
        "shortLink": False,  # keep the approved URLs byte-identical
        "tags": [],
        "posts": [{
            "integration": {"id": integration.integration_id},
            "value": [{"content": content, "image": []}],
            "settings": {"__type": integration.provider},
        }],
    }


def publish(account: dict, content: str, post_id: str, *, verified_connection=None):
    """Create one Postiz ``now`` job. Never returns normally: success raises BackendAccepted."""
    if account.get("backend") != BACKEND:
        raise ValidationError("Unknown publishing backend; only 'postiz' is supported")
    problems = validate_post(account["platform"], content)
    if problems:
        raise ValidationError("; ".join(problems))
    require_backend_capability(account["platform"])
    integration = verified_connection
    if not (isinstance(integration, VerifiedIntegration) and integration.account_id == account["id"]
            and integration.platform == account["platform"]
            and integration.integration_id == account["postiz_integration_id"]):
        integration = verify_integration(account)
    payload = build_payload(integration, content)
    publishers.mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    result = _call(account, "POST", "/posts", payload)
    item = result[0] if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict) else None
    remote = item.get("postId") if item else None
    if not isinstance(remote, str) or not _SAFE_ID.fullmatch(remote) or cast(dict, item).get("integration") != integration.integration_id:
        raise PublishError("postiz_unexpected_create_response_uncertain")
    raise BackendAccepted(REMOTE_PREFIX + remote)


def record_backend_acceptance(con, post_id: str, backend_ref: str) -> None:
    """sending -> needs_review, keeping the backend job id. No schema/state-machine change."""
    from .storage import event
    con.execute("UPDATE posts SET status='needs_review',remote_id=?,last_error=? WHERE id=? AND status='sending'",
                (backend_ref, ACCEPTED_REASON, post_id))
    event(con, "needs_review", post_id, f"reason={ACCEPTED_REASON} backend_ref={backend_ref}")


def run_live_send(con, post, account: dict, *, max_per_24h: int, min_gap_minutes: int,
                  optout: list | None = None, cooldown_floor_seconds: int | None = None,
                  preflight_max_retries: int | None = None,
                  on_preflight_retry: Callable[[int, float, str], None] | None = None) -> int:
    """One guarded live send for the CLI loop. Returns 0, 2 (blocked/skipped) or 3 (review).

    ``optout``: entries the CLI loaded once for the run; ``None`` loads them here from
    the default sources, so a direct caller cannot skip the opt-out gate.
    ``cooldown_floor_seconds`` / ``preflight_max_retries``: the tighten-only caps.json v2
    values (issue #11); ``None`` uses the built-in 60 s floor and at most 2 retries.
    ``on_preflight_retry``: the worker log's retry observer (security decision item 3, log only),
    passed to ``preflight_with_backoff`` as ``on_retry``; it cannot change the outcome.
    """
    from .optout import load_optout, publish_block_reason
    from .ratelimit import (AMBIGUOUS, FLOOR_SECONDS, PREFLIGHT_MAX_RETRIES, classify_send_failure,
                            cooldown_scopes, is_rate_limited, preflight_retries, preflight_with_backoff,
                            record_rate_limit)
    from .send_options import fingerprint as send_options_fingerprint
    from .storage import (active_provider_cooldown, approval_payload_sha256, binding_refusal,
                          check_approval_binding, claim_sending, cooldown_refusal, is_paused, repeat_refusal,
                          repeat_send_problem, uncertain_send)
    floor = FLOOR_SECONDS if cooldown_floor_seconds is None else cooldown_floor_seconds
    retries = preflight_retries(BACKEND, PREFLIGHT_MAX_RETRIES if preflight_max_retries is None
                                else preflight_max_retries)
    pid = post["id"]
    if is_paused(con):
        print(f"BLOCKED {pid}: Global emergency pause is active; no post sent")
        return 2
    if any(name in post.keys() and post[name] for name in ("media_json", "video_json")):
        # #13/#20/#22/#23: never send the text without its images or video
        print(f"SKIPPED {pid}: media_attachments_unsupported via a publishing backend; not claimed, no request sent")
        return 2
    # Opt-out gate: before the read-only identity preflight and before any claim (issue #26).
    reason = publish_block_reason(post["content"], load_optout() if optout is None else optout, post["platform"])
    if reason:
        print(f"BLOCKED {pid}: {reason}; not claimed, post stays approved, no post sent "
              f"(details: reachweaver check {pid})")
        return 2
    # Approval binding (#7): before the identity preflight; claim_sending re-checks under lock.
    # Sheet item 9: the bound Postiz server and integration must be the ones this account names now.
    options_fingerprint = send_options_fingerprint(account)
    problem = check_approval_binding(con, pid, send_options_fingerprint=options_fingerprint)
    if problem:
        print(f"BLOCKED {pid}: {binding_refusal(problem, pid)}; no social post sent")
        return 2
    # Human confirmation (#7 D6, opt-in): pre-claim, pre-network; a failure raises and stops the run.
    from .confirmation import confirm_live_send
    confirm_live_send(con, post)
    # Provider cooldown (#11): after a 429 on this account, or on any account of its
    # declared Postiz workspace, other unclaimed posts wait. No network.
    try:
        scopes = cooldown_scopes(account)
    except ValidationError as exc:
        print(f"BLOCKED {pid}: {exc}; not claimed, no request sent")
        return 2
    cooldown = active_provider_cooldown(con, account["id"], scopes=scopes)
    if cooldown:
        print(f"SKIPPED {pid}: {cooldown_refusal(cooldown)}; not claimed, post stays {post['status']}, "
              "no request sent")
        return 2
    # Repeat guard at send time (sheet item 1): no network; claim_sending re-checks under its lock.
    repeat = repeat_send_problem(con, post)
    if repeat:
        print(f"SKIPPED {pid}: {repeat_refusal(pid, repeat)}; not claimed, no request sent")
        return 2
    try:  # Everything here is read-only or local: the approved post stays eligible.
        problems = validate_post(post["platform"], post["content"])
        if problems:
            raise ValidationError("failed preflight check")
        # GET /integrations only; bounded jittered retries before the claim (#11, D13).
        integration = preflight_with_backoff(lambda: verify_integration(account), max_retries=retries,
                                             paused=lambda: is_paused(con), retry_on=PublishError,
                                             on_retry=on_preflight_retry)
    except (ValidationError, PublishError) as exc:
        note = ""
        if is_rate_limited(exc):
            recorded = record_rate_limit(con, account, exc, stage="preflight", floor_seconds=floor)
            note = f"; {cooldown_refusal(recorded)} recorded"
        print(f"BLOCKED {pid}: Postiz backend preflight failed ({exc}){note}; no post sent")
        return 2
    try:
        claimed = claim_sending(con, pid, max_per_24h=max_per_24h, min_gap_minutes=min_gap_minutes,
                                expected_sha256=approval_payload_sha256(post),  # the exact text sent below
                                cooldown_scopes=scopes, send_options_fingerprint=options_fingerprint)
    except ValidationError as exc:
        print(f"BLOCKED {pid}: {exc}; no social post sent")
        return 2
    if not claimed:
        print(f"SKIPPED {pid}: no longer eligible")
        return 2
    publishers.reset_write_marker()
    try:
        publishers.publish(account, post["content"], pid, verified_connection=integration)
    except BackendAccepted as accepted:
        record_backend_acceptance(con, pid, accepted.backend_ref)
        print(f"REVIEW {pid}: accepted by Postiz backend ({accepted.backend_ref}); platform receipt unverified. "
              "Verify the native post, then reconcile with the native id/URL")
    except (PublishError, ValidationError) as exc:
        send_class = classify_send_failure(account["platform"], BACKEND, exc,
                                           write_started=publishers.write_started())
        uncertain_send(con, pid, f"postiz:{exc}", send_class=send_class)
        note = ""
        if is_rate_limited(exc):  # the claimed post stays needs_review; only OTHER posts wait
            cooldown = record_rate_limit(con, account, exc, stage="send", post_id=pid,
                                         send_class=send_class, floor_seconds=floor)
            note = f"; {cooldown_refusal(cooldown)} recorded, this post is never resent automatically"
        print(f"REVIEW {pid}: Postiz {exc} [{send_class}]{note}; verify the Postiz calendar and native account "
              "before reconciliation")
    except Exception:
        uncertain_send(con, pid, "postiz_unexpected_error_uncertain", send_class=AMBIGUOUS)
        print(f"REVIEW {pid}: unexpected error; inspect manually; not retried")
    else:  # publish() never returns for Postiz; treat any return as unknown.
        uncertain_send(con, pid, "postiz_unexpected_return_uncertain", send_class=AMBIGUOUS)
        print(f"REVIEW {pid}: unexpected backend return; inspect manually; not retried")
    return 3


def _iso_ms(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _public_https_url(value: object) -> str | None:
    """Keep a backend-reported URL only if it is a plain public HTTPS URL."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        return None
    try:
        require_https_public_server("https://" + parts.hostname)
    except ValidationError:
        return None
    return value


def lookup_backend_post(account: dict, backend_ref: str, around: str) -> dict:
    """Read-only receipt lookup of one Postiz job via GET /posts. Never reconciles.

    ``releaseURL`` is backend-reported, not first-party platform evidence.
    """
    ok = isinstance(backend_ref, str) and backend_ref.startswith(REMOTE_PREFIX)
    wanted = backend_ref[len(REMOTE_PREFIX):] if ok else ""
    if not _SAFE_ID.fullmatch(wanted):
        raise ValidationError("Post has no Postiz backend reference (remote_id postiz:<id>)")
    start = datetime.fromisoformat(around.replace("Z", "+00:00"))
    query = urlencode({"startDate": _iso_ms(start - timedelta(days=1)), "endDate": _iso_ms(start + timedelta(days=7))})
    data = _call(account, "GET", "/posts?" + query)
    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list):
        raise PublishError("postiz_posts_unexpected_response")
    item = next((p for p in posts if isinstance(p, dict) and p.get("id") == wanted), None)
    if item is None:
        return {"backend_ref": backend_ref, "found": False, "state": None, "release_url": None}
    state = item.get("state") if item.get("state") in ("QUEUE", "PUBLISHED", "ERROR", "DRAFT") else "unknown"
    return {"backend_ref": backend_ref, "found": True, "state": state,
            "release_url": _public_https_url(item.get("releaseURL"))}


class PostizBackend:
    """This module described by ``publishers.PublishingBackend`` (issue #30). Each method delegates
    to the existing function; ``run_live_send`` and ``publishers.publish`` are unchanged."""

    name = BACKEND
    acceptance_status = "needs_review"  # a Postiz acceptance is never a platform receipt
    receipt_is_first_party = False

    def preview(self, account: dict, content: str) -> dict:
        """Offline: the configured target and the fixed create shape. The provider ``__type`` is
        whatever the read-only ``GET /integrations`` preflight observes at send time."""
        if account.get("backend") != BACKEND:
            raise ValidationError("Account is not configured with backend 'postiz'")
        problems = validate_post(account["platform"], content)
        if problems:
            raise ValidationError("; ".join(problems))
        require_backend_capability(account["platform"])  # the bundled registry file; no network
        return {"path": BACKEND, "account_id": account["id"], "platform": account["platform"], "content": content,
                "request": {"method": "POST", "url": api_base(account) + "/posts", "type": "now",
                            "shortLink": False, "integration_id": account["postiz_integration_id"]},
                "network": "none"}

    def create(self, account: dict, content: str, post_id: str, *,
               verified_connection=None) -> tuple[str, str | None]:
        """Through ``publishers.publish``, so the send gate runs first; success raises BackendAccepted."""
        if account.get("backend") != BACKEND:
            raise ValidationError("Account is not configured with backend 'postiz'")
        return publishers.publish(account, content, post_id, verified_connection=verified_connection)

    def lookup_receipt(self, account: dict, remote_ref: str, around: str) -> dict | None:
        return lookup_backend_post(account, remote_ref, around)


POSTIZ_BACKEND = PostizBackend()
