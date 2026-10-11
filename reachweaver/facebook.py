"""Facebook Page text/link publisher (issue #21). Owner-approved 2026-10-09; disabled until a live receipt.

The capability registry keeps Facebook at ``reachweaver_adapter: "none"`` with text and link
``api_gated``, and ``policy.SUPPORTED_LIVE`` excludes it, so ``publishers.publish``,
``connections.check_connection`` and the CLI refuse Facebook before any credential read or
request. This module becomes reachable only after the owner's Meta App Review, a supervised
first live receipt and a reviewed registry change (docs/SECURITY-REVIEW-PROPOSAL-21-facebook-page.md).

Scope: one published text post (``message``, plus a ``link`` derived from the approved text)
on ONE configured Facebook Page. Never a personal profile or group; no media, scheduling,
unpublished/dark posts, targeting, place, tags, backdating or call-to-action fields are sent.

Facts read 2026-10-09 from official Meta developer docs (developers.facebook.com):

* Publish: "send a POST request to the /page_id/feed endpoint" with "message set to the text for
  your post", "link set to your URL if you want to post a link", "published set to true to
  publish the post immediately (default)"; response ``{"id": "page_post_id"}``; permissions
  pages_manage_engagement, pages_manage_posts, pages_read_engagement, pages_read_user_engagement
  and a Page access token; the app user must be able to perform the CREATE_CONTENT, MANAGE and
  MODERATE tasks; "The URL, or permalink, for a Page post is https://www.facebook.com/page_post_id";
  examples use ``graph.facebook.com/v25.0``. https://developers.facebook.com/docs/pages-api/posts
* Page feed reference: a Page access token, pages_manage_posts, pages_read_engagement and
  pages_show_list; "You must provide link or message"; the post "will not succeed" when the
  ``link`` URL cannot be seen by the viewer or app; no message length or rate limit stated.
  https://developers.facebook.com/docs/graph-api/reference/page/feed
* Page node ``GET /{page-id}?fields=id,name``: ``id`` "The ID representing a Facebook Page"
  (numeric string), ``name`` "The name of the Page".
  https://developers.facebook.com/docs/graph-api/reference/page
* ``/me`` "translates to the object ID of the person or Page whose access token is currently
  being used"; host ``graph.facebook.com``; unversioned calls use the oldest available version.
  https://developers.facebook.com/docs/graph-api/overview
* Post node: ``permalink_url`` "The permanent static URL to the post on www.facebook.com";
  ``from`` "The ID of the user, page, group, or event that published the post"; a Page token
  "can read all posts posted to or posted by that Page".
  https://developers.facebook.com/docs/graph-api/reference/post
* Versions: v25.0 released 2026-02-18, available until 2028-07-29 (v26.0, 2026-07-29, has no
  published end date yet, so v25.0 is pinned). https://developers.facebook.com/docs/graph-api/changelog
* Long-lived Page access tokens "do not have an expiration date" but can be invalidated.
  https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/
* Errors: body ``{"error": {"message", "type", "code", "error_subcode", "fbtrace_id"}}``; codes
  4, 17 (throttling), 10 and 200-299 (permission), 190 (expired token), 368 (policy block),
  506 (duplicate post) are documented WITHOUT HTTP statuses.
  https://developers.facebook.com/docs/graph-api/guides/error-handling
  Throttling codes 4, 17, 32, 613 and 80001 (Page token BUC limit, 4800 calls x engaged users per
  24 h), again without HTTP statuses. https://developers.facebook.com/docs/graph-api/overview/rate-limiting
* ``Authorization: Bearer`` on graph.facebook.com is documented in the WhatsApp Cloud API token
  guide ("include your token in an authorization request header, preceded by Bearer", example
  on ``graph.facebook.com/v25.0``), not on the Pages pages, whose examples use ``access_token``.
  https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens/

Not verified (fail closed or documented, never relied on): the HTTP status Meta uses for each
error code, so errors are mapped by HTTP status only and error bodies are never read; the
exact post id shape (``{page-id}_{post-id}`` is required here; anything else is ambiguous);
that the Bearer header is honoured by the Pages endpoints (if not, ``GET /me`` fails before
the claim and nothing is sent); any Page post rate or message length limit.

Credentials come only from ``{env_prefix}_PAGE_ACCESS_TOKEN`` via ``accounts.credential``, sent
only in the ``Authorization`` header, never in a URL or form body. No error raised here contains
the token, the URL, request headers or the remote response body.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import urlencode, urlsplit

from . import publishers
from .accounts import credential
from .policy import ValidationError

API_HOST = "https://graph.facebook.com"
GRAPH_API_VERSION = "v25.0"                 # pinned; examples on the Pages posts page use it
GRAPH_API_AVAILABLE_UNTIL = date(2028, 7, 29)  # official changelog, read 2026-10-09
VERSION_WARN_DAYS = 90
API_BASE = f"{API_HOST}/{GRAPH_API_VERSION}"
CREDENTIAL_SUFFIX = "_PAGE_ACCESS_TOKEN"
PAGE_ID_PATTERN = re.compile(r"^[0-9]{1,40}$")  # documented as a numeric string
POST_ID_PATTERN = re.compile(r"^([0-9]{1,40})_([0-9]{1,40})$")  # assumed {page-id}_{post-id}; unverified
PAGE_NAME_MAX = 200  # fail-closed local bound; Meta documents no Page name length
PERMALINK_HOSTS = frozenset({"www.facebook.com"})
_ALLOWED_KEYS = frozenset({"id", "platform", "env_prefix", "page_id", "page_name"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _utc_now() -> datetime:
    """Indirection so tests can pin the clock; never used for remote time."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- config

def normalize_page_id(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not PAGE_ID_PATTERN.fullmatch(text):
        raise ValidationError("page_id is required: the numeric Page 'id' from GET /me with the Page token")
    return text


def normalize_page_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("page_name is required (the Page 'name' from GET /{page-id}?fields=id,name)")
    text = " ".join(value.split())
    if len(text) > PAGE_NAME_MAX or _CONTROL.search(text):
        raise ValidationError(f"page_name must be at most {PAGE_NAME_MAX} characters without control characters")
    return text


def _same_name(remote: object, expected: str) -> bool:
    return isinstance(remote, str) and " ".join(remote.split()).casefold() == expected.casefold()


def validate_account_config(raw: dict, account_id: str) -> None:
    """Called by ``accounts.load_accounts``.

    An account without ``page_id`` is manual-only metadata (calendar, manual-export) and loads
    as before. Once ``page_id`` is set the account is adapter-shaped: only documented,
    non-secret keys are allowed, and ``page_id``/``page_name`` are normalized. ``preflight``
    re-applies the strict rules before any request either way.
    """
    if "page_id" not in raw:
        return
    try:
        extra = sorted(set(raw) - _ALLOWED_KEYS)
        if extra:
            raise ValidationError(f"Facebook account has unsupported fields {', '.join(extra)}; "
                                  "only text/link posts on one Page are supported")
        raw["page_id"] = normalize_page_id(raw.get("page_id"))
        raw["page_name"] = normalize_page_name(raw.get("page_name"))
    except ValidationError as exc:
        raise ValidationError(f"{exc} (account {account_id})") from None


def version_status(today: date | None = None) -> tuple[str, str]:
    """('ok'|'warning'|'error', message) for the pinned Graph API version's documented end date."""
    current = today or _utc_now().date()
    left = (GRAPH_API_AVAILABLE_UNTIL - current).days
    if left <= 0:
        return "error", (f"facebook_graph_version_expired: {GRAPH_API_VERSION} was available until "
                         f"{GRAPH_API_AVAILABLE_UNTIL.isoformat()}; re-test and pin a current version")
    if left <= VERSION_WARN_DAYS:
        return "warning", (f"facebook_graph_version_ageing: {GRAPH_API_VERSION} ends "
                           f"{GRAPH_API_AVAILABLE_UNTIL.isoformat()} ({left} day(s)); plan the upgrade")
    return "ok", f"Graph API {GRAPH_API_VERSION} available until {GRAPH_API_AVAILABLE_UNTIL.isoformat()}"


def doctor_notes(account: dict, *, now: datetime | None = None) -> list[str]:
    """Offline notes for `doctor`; never reads the token value."""
    notes = ["INFO: Facebook Page adapter is owner-approved but blocked by the capability registry until a live receipt "
             "(docs/SECURITY-REVIEW-PROPOSAL-21-facebook-page.md); use manual-export meanwhile"]
    level, message = version_status((now or _utc_now()).date())
    if level != "ok":
        notes.append(f"{level.upper()}: {message}")
    if "page_id" not in account:
        notes.append("INFO: no page_id configured; this account is manual-export metadata only")
    return notes


def derived_link(content: str) -> str | None:
    """The first ``https://`` URL in the approved text, or None.

    The ``link`` field is a pure function of the approved bytes, so the #7 approval hash over
    ``content`` covers every byte sent; nothing comes from config or the network.
    """
    from .manual_export import extract_links
    for url in extract_links(content):
        if url.lower().startswith("https://"):
            return url
    return None


def feed_form(content: str) -> dict:
    """The exact form body of ``POST /{page-id}/feed``: ``message`` and an optional derived ``link``."""
    form = {"message": content}
    link = derived_link(content)
    if link:
        form["link"] = link
    return form


def preflight(account: dict, *, now: datetime | None = None) -> tuple[str, str]:
    """Offline, fail-closed checks before any Facebook request. Returns (page id, page name)."""
    if account.get("platform") != "facebook" or account.get("backend") is not None:
        raise ValidationError("facebook_preflight_wrong_platform")
    extra = sorted(set(account) - _ALLOWED_KEYS)
    if extra:
        raise ValidationError("facebook_unsupported_fields: " + ", ".join(extra))
    page_id = normalize_page_id(account.get("page_id"))
    page_name = normalize_page_name(account.get("page_name"))
    level, message = version_status((now or _utc_now()).date())
    if level == "error":
        raise ValidationError(message)
    return page_id, page_name


# --------------------------------------------------------------------------- network

def _token(account: dict) -> str:
    name = account["env_prefix"] + CREDENTIAL_SUFFIX
    token = (credential(name) or "").strip()
    if not token:
        raise ValidationError("missing_credentials: " + name)
    return token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_UNDOCUMENTED = "Meta documents its error codes without HTTP statuses"
_HINTS = {
    ("me", "http_400"): (f"facebook_http_400: request refused; the token may be invalid, expired, not a Page token, "
                         f"or throttled ({_UNDOCUMENTED}); nothing was sent"),
    ("me", "http_401"): "facebook_http_401: Page token invalid, revoked or expired; generate a new Page token",
    ("me", "http_403"): "facebook_http_403: token refused for this Page; check Page tasks and permissions",
    ("page", "http_400"): (f"facebook_http_400: Page read refused; confirm page_id and pages_read_engagement "
                           f"({_UNDOCUMENTED}); nothing was sent"),
    ("page", "http_403"): "facebook_http_403: Page read refused; check Page tasks and permissions; nothing was sent",
    ("feed", "http_400"): (f"facebook_feed_http_400_uncertain: post refused, link not visible, duplicate or throttled "
                           f"({_UNDOCUMENTED}); verify the Page before reconciling"),
    ("feed", "http_429"): "facebook_feed_http_429_uncertain: rate limited; do not retry automatically",
}


def _actionable(stage: str, code: str) -> str:
    """Map a sanitized helper error to an actionable one; never adds remote text."""
    hint = _HINTS.get((stage, code))
    if hint:
        return hint
    if stage == "feed":  # after mark_write_started(): always ambiguous, never retried
        return f"facebook_feed_{code}" + ("" if code.endswith("_uncertain") else "_uncertain")
    return f"facebook_{code}"


def _get(path: str, fields: str, token: str, stage: str) -> dict:
    url = f"{API_BASE}/{path}?" + urlencode({"fields": fields})
    try:
        return publishers.request_get_json(url, _headers(token))
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable(stage, str(exc))) from None


def verify_identity(account: dict, *, now: datetime | None = None) -> tuple[str, str]:
    """Before the claim: the token must be a Page token for exactly the configured Page.

    ``GET /me?fields=id,name`` must return ``page_id`` (a user token returns the user's id), then
    ``GET /{page-id}?fields=id,name`` must return the same id and the configured ``page_name``.
    Neither read proves ``pages_manage_posts``; the write permission stays unverified.
    """
    page_id, page_name = preflight(account, now=now)
    token = _token(account)
    me = _get("me", "id,name", token, "me")
    if not isinstance(me.get("id"), str):
        raise publishers.PublishError("facebook_me_missing_identity")
    if me["id"] != page_id:
        raise ValidationError("identity_mismatch: the token is not a Page token for the configured page_id "
                              "(a user token or another Page's token)")
    page = _get(page_id, "id,name", token, "page")
    if page.get("id") != page_id:
        raise ValidationError("identity_mismatch: GET /{page-id} returned a different Page")
    if not _same_name(page.get("name"), page_name) or ("name" in me and not _same_name(me["name"], page_name)):
        raise ValidationError("identity_mismatch: Page name differs from page_name; update the config after checking")
    return page_id, page_name


def permalink_ok(url: object) -> bool:
    """An HTTPS ``www.facebook.com`` URL without credentials, port or fragment, of bounded length."""
    if not isinstance(url, str) or len(url) > 500 or _CONTROL.search(url):
        return False
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.hostname in PERMALINK_HOSTS and parts.port is None
            and not parts.username and not parts.password and not parts.fragment and len(parts.path) > 1)


def _receipt(post_id: str, content: str, page_id: str, token: str) -> publishers.Receipt:
    """The post id is the receipt. A permalink read failure keeps it (remote_url None); a
    response naming another post or Page is ambiguous (needs_review)."""
    try:
        post = _get(post_id, "id,permalink_url,from,message", token, "permalink")
    except publishers.PublishError:
        return publishers.Receipt(post_id, None, permalink="unavailable")
    if post.get("id") not in (None, post_id):
        raise publishers.PublishError("facebook_receipt_id_mismatch_uncertain")
    author = post.get("from")
    if isinstance(author, dict) and author.get("id") is not None and str(author.get("id")) != page_id:
        raise publishers.PublishError("facebook_receipt_page_mismatch_uncertain")
    message = post.get("message")
    text_match = "unknown" if not isinstance(message, str) else ("true" if message == content else "false")
    link = post.get("permalink_url")
    if not permalink_ok(link):
        return publishers.Receipt(post_id, None, permalink="unrecognized", text_match=text_match)
    return publishers.Receipt(post_id, link, text_match=text_match)


def publish_page_post(account: dict, content: str, post_id: str, *,
                      now: datetime | None = None) -> publishers.Receipt:
    """Publish one Page feed post. ``post_id`` is local only (no idempotency key is documented)."""
    page_id, _name = preflight(account, now=now)
    if not content or not content.strip():
        raise ValidationError("Post must contain substantive text")
    form = feed_form(content)
    token = _token(account)
    publishers.mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    try:
        result = publishers.request_json(f"{API_BASE}/{page_id}/feed", form, headers=_headers(token), form=True)
    except publishers.PublishError as exc:
        # Keeps the HTTP status and any delay hint (a 429 records a cooldown, issue #11).
        raise exc.with_code(_actionable("feed", str(exc))) from None
    remote = result.get("id")
    if isinstance(remote, bool) or not isinstance(remote, str):
        raise publishers.PublishError("facebook_feed_missing_post_id_uncertain")
    match = POST_ID_PATTERN.fullmatch(remote)
    if match is None:
        raise publishers.PublishError("facebook_feed_post_id_unrecognized_uncertain")
    if match.group(1) != page_id:
        raise publishers.PublishError("facebook_receipt_page_mismatch_uncertain")
    return _receipt(remote, content, page_id, token)

