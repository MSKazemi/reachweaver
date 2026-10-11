"""LinkedIn member (personal profile) text publisher, issue #14.

Scope is deliberately narrow: one text-only post per call, authored by the
authenticated member as ``urn:li:person:{id}``. Organization pages are
unsupported: ``w_organization_social`` is a separate permission restricted to
page roles, and member posting rights never imply page rights.

Facts verified 2026-10-08 against official Microsoft Learn LinkedIn docs:

* Share on LinkedIn is an open, self-serve product granting ``w_member_social``
  https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access
* Posts API ``POST https://api.linkedin.com/rest/posts`` needs ``Linkedin-Version:
  YYYYMM`` and ``X-Restli-Protocol-Version: 2.0.0``; 201 with the post URN in
  the ``x-restli-id`` response header
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
* Versions are supported for a minimum of one year; deprecated versions are
  refused with HTTP 426
  https://learn.microsoft.com/en-us/linkedin/marketing/versioning
  https://learn.microsoft.com/en-us/linkedin/marketing/error-responses
* ``commentary`` uses the ``little`` text format; reserved characters must be
  backslash-escaped
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format
* ``GET https://api.linkedin.com/v2/userinfo`` (OpenID ``openid profile``)
  returns the member identifier ``sub``
  https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2

Optional write-scope check (issue #14, owner-approved 2026-10-09), verified
2026-10-09 against https://learn.microsoft.com/en-us/linkedin/shared/authentication/token-introspection :

* ``POST https://www.linkedin.com/oauth/v2/introspectToken``, form-encoded
  ``client_id``, ``client_secret`` and ``token`` (all required). A 200 returns
  ``active`` (boolean, required) and optional ``status`` (``active``/``expired``/
  ``revoked``), ``scope`` (comma-separated, only for 3-legged tokens),
  ``client_id``, ``expires_at``/``authorized_at``/``created_at`` (epoch seconds)
  and ``auth_type`` (``3L`` = member token). 400 = invalid client id or token,
  401 = invalid client secret. Valid app credentials that do not match the
  token's app give 200 with ``active: false``.
* The client secret must never go in a URL or query string
  (https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow,
  accessed 2026-10-09). It is sent only in the form body here.
* No rate limit is documented for ``introspectToken``. LinkedIn states that all
  API calls are rate limited per application and per member per UTC day and that
  standard limits are not published
  (https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts/rate-limits,
  accessed 2026-10-09). Whether introspection counts toward the member quota is
  NOT documented (unverified), so calls are bounded locally: at most
  ``MAX_INTROSPECTIONS_PER_ACCOUNT`` per account per process, with the result
  cached in memory only.

Credentials come only from ``{env_prefix}_ACCESS_TOKEN`` and, only with
``"scope_check": "introspect"``, ``{env_prefix}_CLIENT_ID``/``_CLIENT_SECRET``.
No error raised here ever contains a credential, the request headers or the
remote response body.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

from . import publishers
from .accounts import (OPT_IN_CREDENTIAL_SUFFIXES, SCOPE_CHECK_FIELD, SCOPE_CHECK_INTROSPECT, credential,
                       require_credential, scope_check_enabled)
from .policy import ValidationError

POSTS_URL = "https://api.linkedin.com/rest/posts"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
RESTLI_PROTOCOL_VERSION = "2.0.0"
CREDENTIAL_SUFFIX = "_ACCESS_TOKEN"
INTROSPECT_URL = "https://www.linkedin.com/oauth/v2/introspectToken"
# ("_CLIENT_ID", "_CLIENT_SECRET"): one table, so the env_prefix overlap rule covers them.
CLIENT_ID_SUFFIX, CLIENT_SECRET_SUFFIX = OPT_IN_CREDENTIAL_SUFFIXES["linkedin"]
REQUIRED_WRITE_SCOPE = "w_member_social"
MEMBER_AUTH_TYPE = "3L"
# Local policy, not LinkedIn's: refuse a token that expires within this margin so a send never
# races the expiry, and flag an accounts.json expiry more than a day off LinkedIn's value.
SCOPE_EXPIRY_MARGIN = timedelta(hours=1)
EXPIRY_MISMATCH_TOLERANCE = timedelta(days=1)
# Introspection results live in process memory only, for at most this long, and an account
# gets at most this many requests per process (one check plus the preflight's 2 retries).
SCOPE_CACHE_SECONDS = 15 * 60
MAX_INTROSPECTIONS_PER_ACCOUNT = 3
_MAX_EPOCH = 32503680000  # 3000-01-01: anything later is not a plausible expiry

# First versioned Marketing API release was June 2022 (versioning docs).
FIRST_VERSION = (2022, 6)
# LinkedIn guarantees a minimum of 12 months of support per monthly version.
# Warn two months ahead; treat the version as unsupported from month 12 so a
# live send fails closed before the provider's sunset date.
VERSION_WARN_MONTHS = 10
VERSION_UNSUPPORTED_MONTHS = 12
# Access tokens are currently issued with a 60-day lifespan (authorization-code-flow docs).
TOKEN_WARN_DAYS = 7

VERSION_PATTERN = re.compile(r"^(20[0-9]{2})(0[1-9]|1[0-2])$")
# Documented examples: 8675309, 782bbtaQ, 5abc_dEfgH. '-' is also accepted as a
# URL-safe identifier character; anything with ':' or spaces is refused.
PERSON_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
PERSON_URN_PREFIX = "urn:li:person:"
POST_URN_PATTERN = re.compile(r"^urn:li:(?:share|ugcPost):[0-9]{1,40}$")
# little text format reserved characters, from the documented Text grammar.
RESERVED_CHARACTERS = frozenset("\\|{}@[]()<>#*_~")
_ORG_KEYS = re.compile(r"(organi[sz]ation|company|page|^author(?:_?(?:urn|id))?$)", re.I)


def _utc_now() -> datetime:
    """Indirection so tests can pin the clock; never used for remote time."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- config

def normalize_person_id(value: object) -> str:
    """Return the bare member id; refuse organization or other URN types."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("LinkedIn person_id is required (the 'sub' from GET /v2/userinfo)")
    text = value.strip()
    if text.lower().startswith("urn:li:organization"):
        raise ValidationError("linkedin_organization_unsupported: organization pages are not supported; "
                              "configure the member person_id only")
    if text.startswith(PERSON_URN_PREFIX):
        text = text[len(PERSON_URN_PREFIX):]
    elif text.lower().startswith("urn:"):
        raise ValidationError("linkedin_author_unsupported: only urn:li:person member authors are supported")
    if not PERSON_ID_PATTERN.fullmatch(text):
        raise ValidationError("LinkedIn person_id must be the member id (letters, digits, '_' or '-'), "
                              "e.g. the 'sub' returned by /v2/userinfo")
    return text


def parse_version(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ValidationError("linkedin_version must be a YYYYMM string such as '202609'")
    year, month = int(value[:4]), int(value[4:])
    if (year, month) < FIRST_VERSION:
        raise ValidationError("linkedin_version predates LinkedIn's first versioned API release (202206)")
    return year, month


def version_status(version: object, today: date | None = None) -> tuple[str, str]:
    """Classify a pinned ``Linkedin-Version``: ('ok'|'warning'|'error', message)."""
    try:
        year, month = parse_version(version)
    except ValidationError as exc:
        return "error", f"linkedin_version_invalid: {exc}"
    now = today or _utc_now().date()
    age = (now.year * 12 + now.month) - (year * 12 + month)
    if age < 0:
        return "error", f"linkedin_version_invalid: {version} is in the future"
    if age >= VERSION_UNSUPPORTED_MONTHS:
        return "error", (f"linkedin_version_unsupported: {version} is {age} months old; LinkedIn supports each "
                         "version for a minimum of one year. Re-test and pin a current version")
    if age >= VERSION_WARN_MONTHS:
        return "warning", (f"linkedin_version_ageing: {version} is {age} months old; plan the upgrade before "
                           f"month {VERSION_UNSUPPORTED_MONTHS}")
    return "ok", f"linkedin_version {version} is {age} months old"


def parse_expiry(value: object) -> datetime:
    """``authorization_expires_at`` is non-secret metadata: an ISO-8601 timestamp with offset only."""
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        raise ValidationError("authorization_expires_at must be an ISO-8601 timestamp with a timezone offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("authorization_expires_at must be an ISO-8601 timestamp with a timezone offset") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("authorization_expires_at needs an explicit timezone offset")
    return parsed


def token_status(account: dict, now: datetime | None = None) -> tuple[str, str]:
    """('ok'|'warning'|'expired'|'unknown', message) from optional account metadata."""
    raw = account.get("authorization_expires_at")
    if raw is None:
        return "unknown", ("authorization_expires_at not recorded; LinkedIn access tokens currently last 60 days, "
                           "record the expiry shown at consent time")
    try:
        expires = parse_expiry(raw)
    except ValidationError as exc:
        return "expired", f"linkedin_token_expiry_invalid: {exc}"
    current = now or _utc_now()
    if expires <= current:
        return "expired", ("linkedin_token_expired: re-run the LinkedIn OAuth consent, export the new token "
                           "and update authorization_expires_at")
    remaining = expires - current
    if remaining <= timedelta(days=TOKEN_WARN_DAYS):
        return "warning", f"linkedin_token_expiring: about {max(remaining.days, 0)} day(s) left; re-authorize soon"
    return "ok", f"linkedin token valid for about {remaining.days} more day(s)"


def validate_account_config(raw: dict, account_id: str) -> None:
    """Called by ``accounts.load_accounts``; mutates ``person_id`` to the bare id."""
    for key in raw:
        if key not in ("id", "platform", "env_prefix") and _ORG_KEYS.search(key):
            raise ValidationError(f"linkedin_organization_unsupported for account {account_id}: field '{key}' "
                                  "is not allowed; only member (personal profile) posting is supported")
    if SCOPE_CHECK_FIELD in raw and raw[SCOPE_CHECK_FIELD] != SCOPE_CHECK_INTROSPECT:
        raise ValidationError(f"{SCOPE_CHECK_FIELD} must be \"{SCOPE_CHECK_INTROSPECT}\" or omitted "
                              f"(account {account_id})")
    try:
        raw["person_id"] = normalize_person_id(raw.get("person_id"))
        parse_version(raw.get("linkedin_version"))
        if "authorization_expires_at" in raw:
            parse_expiry(raw["authorization_expires_at"])
    except ValidationError as exc:
        raise ValidationError(f"{exc} (account {account_id})") from None


def doctor_notes(account: dict, *, now: datetime | None = None) -> list[str]:
    """Offline warnings for `doctor`; never reads the token value."""
    current = now or _utc_now()
    notes = []
    level, message = version_status(account.get("linkedin_version"), current.date())
    if level != "ok":
        notes.append(f"{level.upper()}: {message}")
    level, message = token_status(account, current)
    if level != "ok":
        notes.append(f"{'ERROR' if level == 'expired' else level.upper()}: {message}")
    if not scope_check_enabled(account):
        prefix = account.get("env_prefix", "<env_prefix>")
        notes.append(f"INFO: {SCOPE_UNVERIFIED_NOTE} Opt in with \"{SCOPE_CHECK_FIELD}\": "
                     f"\"{SCOPE_CHECK_INTROSPECT}\" (reads {prefix}{CLIENT_ID_SUFFIX} and "
                     f"{prefix}{CLIENT_SECRET_SUFFIX}; see docs/LINKEDIN.md)")
    return notes


SCOPE_UNVERIFIED_NOTE = ("linkedin_write_scope_unverified: w_member_social is not checked before a send "
                         "(GET /v2/userinfo proves identity only); a token without it fails with HTTP 403 "
                         "after the claim and the post goes to needs_review.")


# --------------------------------------------------------------------------- payload

def escape_commentary(text: str, *, keep_hashtags: bool = True) -> str:
    """Escape every little-format reserved character so text renders literally.

    Mentions are never produced: '@', '[', ']', '(' and ')' are always escaped,
    so approved text cannot tag a member or organization. When ``keep_hashtags``
    is true a '#' at a word start followed by a letter/digit is left as a
    documented HashtagElement; every other '#' is escaped.
    """
    out = []
    for index, char in enumerate(text):
        if char in RESERVED_CHARACTERS:
            is_hashtag = (keep_hashtags and char == "#" and (index == 0 or text[index - 1].isspace())
                          and index + 1 < len(text) and text[index + 1].isalnum())
            if not is_hashtag:
                out.append("\\")
        out.append(char)
    return "".join(out)


def author_urn(person_id: str) -> str:
    """Always a member URN; organization URNs are refused by normalize_person_id."""
    return PERSON_URN_PREFIX + normalize_person_id(person_id)


def build_post_payload(person_id: str, content: str) -> dict:
    """Text-only member post body exactly as the Posts API documents it."""
    return {
        "author": author_urn(person_id),
        "commentary": escape_commentary(content),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def api_headers(token: str, version: str) -> dict:
    parse_version(version)
    return {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": version,
        "X-Restli-Protocol-Version": RESTLI_PROTOCOL_VERSION,
    }


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def parse_post_receipt(status: int, headers: dict) -> tuple[str, str]:
    """Return (post URN, post URL) from a create response, or raise an *uncertain* error.

    Any failure here happens after LinkedIn may have created the post, so every
    error ends in ``_uncertain`` and must be reconciled by a human, not retried.
    """
    if status != 201:
        raise publishers.PublishError(f"linkedin_unexpected_status_{int(status)}_uncertain")
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    raw = lowered.get("x-restli-id")
    if not isinstance(raw, str) or not raw.strip():
        raise publishers.PublishError("linkedin_missing_x_restli_id_uncertain")
    urn = unquote(raw.strip())
    if not POST_URN_PATTERN.fullmatch(urn):
        raise publishers.PublishError("linkedin_unrecognized_post_urn_uncertain")
    return urn, post_url(urn)


# --------------------------------------------------------------------------- network

def _token(account: dict) -> str:
    token = credential(account["env_prefix"] + CREDENTIAL_SUFFIX) or ""
    if not token.strip():
        raise ValidationError("missing_credentials: " + account["env_prefix"] + CREDENTIAL_SUFFIX)
    return token.strip()


_HINTS = {
    ("userinfo", "http_401"): "linkedin_http_401: access token invalid, revoked or expired; re-run OAuth consent",
    ("userinfo", "http_403"): ("linkedin_http_403: token lacks the 'openid profile' scopes (Sign In with LinkedIn "
                               "using OpenID Connect product); identity cannot be checked"),
    ("posts", "http_401"): "linkedin_http_401_uncertain: token rejected; verify the profile before reconciling",
    ("posts", "http_403"): ("linkedin_http_403_uncertain: ACCESS_DENIED; confirm the token carries w_member_social "
                            "(Share on LinkedIn product) and verify the profile before reconciling"),
    ("posts", "http_426"): ("linkedin_http_426_uncertain: Linkedin-Version is not active; pin a current version "
                            "and verify the profile before reconciling"),
    ("posts", "http_429"): "linkedin_http_429_uncertain: rate limited; do not retry automatically",
    ("introspect", "http_400"): ("linkedin_introspect_http_400: LinkedIn rejected the client id or the token "
                                 "(token-introspection docs); check the _CLIENT_ID variable and the access token"),
    ("introspect", "http_401"): ("linkedin_introspect_http_401: LinkedIn rejected the client secret; "
                                 "check the _CLIENT_SECRET variable"),
}


def _actionable(stage: str, code: str) -> str:
    """Map a sanitized helper error to an actionable one; never adds remote text."""
    hint = _HINTS.get((stage, code))
    if hint:
        return hint
    if stage == "introspect":  # pre-claim, read-only: the post was never claimed
        return f"linkedin_introspect_{code}"
    if stage == "posts":  # after the durable claim: always ambiguous, never retried
        return f"linkedin_{code}" + ("" if code.endswith("_uncertain") else "_uncertain")
    return f"linkedin_{code}"


def preflight(account: dict, *, now: datetime | None = None) -> str:
    """Offline, fail-closed checks before any LinkedIn request. Returns the person id."""
    if account.get("platform") != "linkedin":
        raise ValidationError("linkedin_preflight_wrong_platform")
    for key in account:
        if key not in ("id", "platform", "env_prefix") and _ORG_KEYS.search(key):
            raise ValidationError("linkedin_organization_unsupported")
    person_id = normalize_person_id(account.get("person_id"))
    current = now or _utc_now()
    level, message = version_status(account.get("linkedin_version"), current.date())
    if level == "error":
        raise ValidationError(message)
    level, message = token_status(account, current)
    if level == "expired":
        raise ValidationError(message)
    return person_id


def verify_identity(account: dict, *, now: datetime | None = None) -> str:
    """GET /v2/userinfo and require ``sub`` to equal the configured person_id exactly."""
    person_id = preflight(account, now=now)
    token = _token(account)
    try:
        info = publishers.request_get_json(USERINFO_URL, {"Authorization": f"Bearer {token}"})
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable("userinfo", str(exc))) from None
    sub = info.get("sub")
    if not isinstance(sub, str) or not sub:
        raise publishers.PublishError("linkedin_userinfo_missing_sub")
    if sub != person_id:
        raise ValidationError("identity_mismatch: LinkedIn token belongs to a different member than person_id")
    return person_id


# --------------------------------------------------------------------------- write scope (opt-in)

@dataclass(frozen=True)
class ScopeCheck:
    """Outcome of one token introspection. Holds no credential and no raw response."""
    ok: bool
    code: str                      # "ok" or a fixed refusal code; never remote text
    expires_at: datetime | None
    notes: tuple[str, ...] = ()


# (account id, env_prefix) -> (checked_at, result). Process memory only; never persisted.
_SCOPE_CACHE: dict[tuple[str, str], tuple[datetime, ScopeCheck]] = {}
_INTROSPECTION_CALLS: dict[tuple[str, str], int] = {}

_REFUSAL_HINTS = {
    "linkedin_scope_missing_w_member_social": ("token lacks w_member_social; add the Share on LinkedIn product "
                                               "and re-run OAuth consent requesting that scope"),
    "linkedin_token_client_mismatch": "token was issued to a different app than the configured _CLIENT_ID",
    "linkedin_token_not_member_token": "token is not a 3-legged member token",
    "linkedin_introspection_missing_expiry": "LinkedIn returned no usable expires_at; refusing (fail closed)",
    "linkedin_token_expires_within_margin": "token expires within the safety margin; re-authorize first",
}


def reset_scope_cache() -> None:
    """Forget every cached introspection result and call count (tests, or a new run)."""
    _SCOPE_CACHE.clear()
    _INTROSPECTION_CALLS.clear()


def _scope_key(account: dict) -> tuple[str, str]:
    return str(account.get("id")), str(account.get("env_prefix"))


def _epoch(value: object) -> datetime | None:
    if type(value) is not int or not 0 < value < _MAX_EPOCH:
        return None
    return datetime.fromtimestamp(value, timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _scope_names(raw: object) -> frozenset[str]:
    """Introspection documents a comma-separated list; the token endpoint uses spaces. Accept both."""
    if not isinstance(raw, str) or len(raw) > 4096:
        return frozenset()
    return frozenset(part for part in re.split(r"[,\s]+", raw) if part)


def evaluate_introspection(data: dict, account: dict, client_id: str, current: datetime) -> ScopeCheck:
    """Classify an introspection body into a fixed code, an expiry and non-secret notes.

    Fails closed: anything other than an active 3-legged member token, issued to the
    configured app, carrying ``w_member_social`` and expiring after the safety margin,
    is a refusal. Only fixed fields are read; no remote text is returned.
    """
    status = data.get("status", "active")
    if data.get("active") is not True or status != "active":
        detail = status if status in ("expired", "revoked") else "inactive"
        return ScopeCheck(False, f"linkedin_token_{detail}", None)
    reported_client = data.get("client_id")
    if reported_client is not None and reported_client != client_id:
        return ScopeCheck(False, "linkedin_token_client_mismatch", None)
    auth_type = data.get("auth_type")
    if auth_type is not None and auth_type != MEMBER_AUTH_TYPE:
        return ScopeCheck(False, "linkedin_token_not_member_token", None)
    if REQUIRED_WRITE_SCOPE not in _scope_names(data.get("scope")):
        return ScopeCheck(False, "linkedin_scope_missing_w_member_social", None)
    expires = _epoch(data.get("expires_at"))
    if expires is None:
        return ScopeCheck(False, "linkedin_introspection_missing_expiry", None)
    if expires <= current:
        return ScopeCheck(False, "linkedin_token_expired", expires)
    if expires - current <= SCOPE_EXPIRY_MARGIN:
        return ScopeCheck(False, "linkedin_token_expires_within_margin", expires)
    notes: list[str] = []
    configured = account.get("authorization_expires_at")
    if configured is None:
        notes.append(f"INFO: linkedin_expiry_unrecorded: LinkedIn reports the token expires at {_iso(expires)}; "
                     "record it as authorization_expires_at")
    else:
        try:
            recorded: datetime | None = parse_expiry(configured)
        except ValidationError:
            recorded = None
        if recorded is None or abs(recorded - expires) > EXPIRY_MISMATCH_TOLERANCE:
            notes.append(f"WARNING: linkedin_expiry_mismatch: authorization_expires_at differs from the expiry "
                         f"LinkedIn reports ({_iso(expires)}); update accounts.json")
    return ScopeCheck(True, "ok", expires, tuple(notes))


def _refusal(check: ScopeCheck) -> ValidationError:
    hint = _REFUSAL_HINTS.get(check.code, "re-run the LinkedIn OAuth consent and export the new token")
    return ValidationError(f"{check.code}: {hint}")


def _cached_scope(account: dict, current: datetime) -> ScopeCheck | None:
    entry = _SCOPE_CACHE.get(_scope_key(account))
    if entry is None:
        return None
    checked_at, check = entry
    if not timedelta(0) <= current - checked_at <= timedelta(seconds=SCOPE_CACHE_SECONDS):
        return None
    if check.ok and (check.expires_at is None or check.expires_at - current <= SCOPE_EXPIRY_MARGIN):
        return None
    return check


def verify_write_scope(account: dict, *, now: datetime | None = None) -> ScopeCheck:
    """Opt-in, pre-claim check that the token is active and carries ``w_member_social``.

    One ``introspectToken`` request per account per process (result cached in memory
    for ``SCOPE_CACHE_SECONDS``), and never more than ``MAX_INTROSPECTIONS_PER_ACCOUNT``
    requests per account per process, even across transient failures and retries.
    Raises ``ValidationError`` for a definitive refusal and a sanitized ``PublishError``
    for HTTP or network errors. Nothing is logged, persisted or returned beyond
    a fixed code, the expiry and non-secret notes.
    """
    if not scope_check_enabled(account):
        raise ValidationError("linkedin_scope_check_not_enabled")
    current = now or _utc_now()
    cached = _cached_scope(account, current)
    if cached is not None:
        if not cached.ok:
            raise _refusal(cached)
        return cached
    key = _scope_key(account)
    if _INTROSPECTION_CALLS.get(key, 0) >= MAX_INTROSPECTIONS_PER_ACCOUNT:
        raise ValidationError("linkedin_scope_check_call_budget_exhausted: at most "
                              f"{MAX_INTROSPECTIONS_PER_ACCOUNT} introspection requests per account per run")
    prefix = account["env_prefix"]
    client_id = require_credential(prefix + CLIENT_ID_SUFFIX).strip()
    if not client_id:
        raise ValidationError("missing_credentials: " + prefix + CLIENT_ID_SUFFIX)
    client_secret = require_credential(prefix + CLIENT_SECRET_SUFFIX).strip()
    if not client_secret:
        raise ValidationError("missing_credentials: " + prefix + CLIENT_SECRET_SUFFIX)
    token = _token(account)
    _INTROSPECTION_CALLS[key] = _INTROSPECTION_CALLS.get(key, 0) + 1
    try:
        data = publishers.request_json(INTROSPECT_URL, {"client_id": client_id, "client_secret": client_secret,
                                                        "token": token}, form=True)
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable("introspect", str(exc))) from None
    check = evaluate_introspection(data, account, client_id, current)
    del data  # the raw response is never kept, logged or returned
    _SCOPE_CACHE[key] = (current, check)
    if not check.ok:
        raise _refusal(check)
    return check


def require_verified_write_scope(account: dict, *, now: datetime | None = None) -> None:
    """Offline defense in depth before the write: an opted-in account needs a fresh passing check."""
    if not scope_check_enabled(account):
        return
    cached = _cached_scope(account, now or _utc_now())
    if cached is None or not cached.ok:
        raise ValidationError("linkedin_write_scope_not_verified: run the identity preflight "
                              "(connect-check --online or publish --live) before sending")


def publish_member_post(account: dict, content: str, post_id: str, *,
                        now: datetime | None = None) -> tuple[str, str]:
    """Create one text-only member post. ``post_id`` is local only (no documented idempotency key)."""
    person_id = preflight(account, now=now)
    require_verified_write_scope(account, now=now)  # no network; opted-in accounts only
    payload = build_post_payload(person_id, content)
    headers = api_headers(_token(account), account["linkedin_version"])
    publishers.mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    try:
        status, response_headers, _body = publishers.request_json_with_headers(POSTS_URL, payload, headers)
    except publishers.PublishError as exc:
        # Keeps the HTTP status and any delay hint (a 429 records a cooldown, issue #11).
        raise exc.with_code(_actionable("posts", str(exc))) from None
    return parse_post_receipt(status, response_headers)
