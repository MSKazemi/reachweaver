"""Explicit API adapters (text; Mastodon images behind the registry gate, #13). Never log credentials."""
from __future__ import annotations

import http.client
import io
import json
import re
import secrets
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Protocol, cast, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .netconnect import IPv4FirstHTTPSHandler

from .accounts import bluesky_service, require_credential, validate_mastodon_options
from .policy import SUPPORTED_LIVE, SUPPORTED_LIVE_VIDEO, ValidationError, require_https_public_server, validate_post
from .ratelimit import RetryHint, hint_from_http_error
from .richtext import bluesky_facets, deterministic_tid, link_facets


class PublishError(RuntimeError):
    """Sanitized error: safe to put in a local audit log.

    ``str(exc)`` is the sanitized code only. ``status`` (HTTP status, if any) and
    ``retry_hint`` (a provider delay as integer seconds plus a source label, issue #11 D15)
    are typed attributes for the cooldown logic; neither carries header or body text.
    """

    def __init__(self, code: str = "", *, status: int | None = None, retry_hint: RetryHint | None = None):
        super().__init__(code)
        self.status = status
        self.retry_hint = retry_hint

    def with_code(self, code: str) -> "PublishError":
        """Same status and delay hint under a more specific sanitized code."""
        return PublishError(code, status=getattr(self, "status", None),
                            retry_hint=getattr(self, "retry_hint", None))


# Issue #11 (D11): set by each adapter immediately before it hands the content-create request
# to the network. A post-claim failure while it is still False is certain not sent (C1).
_WRITE_STARTED: ContextVar[bool] = ContextVar("reachweaver_write_started", default=False)


def reset_write_marker() -> None:
    _WRITE_STARTED.set(False)


def mark_write_started() -> None:
    _WRITE_STARTED.set(True)


def write_started() -> bool:
    return _WRITE_STARTED.get()


def http_error(exc: HTTPError, *, telegram_body: bool = False) -> PublishError:
    """Sanitized ``http_<code>`` plus a bounded Retry-After / Telegram retry_after hint.

    Headers (and for Telegram at most 4 KB of the error body) are read only to compute an
    integer delay; the URL, request headers, response headers and body never reach the error.
    """
    return PublishError(f"http_{exc.code}", status=exc.code,
                        retry_hint=hint_from_http_error(exc, telegram_body=telegram_body))


def close_http_error(exc: HTTPError) -> None:
    """Release an ``HTTPError``'s response once its status, headers and any bounded body are handled.

    Never reads anything and never raises. Python 3.14 warns (``ResourceWarning``) when an unclosed
    ``HTTPError`` is garbage-collected, and the warning text is the error's repr, which quotes the
    provider's reason phrase; under ``-W``, ``-X dev`` or ``PYTHONWARNINGS`` that reached stderr.
    """
    try:
        exc.close()
    except Exception:  # noqa: BLE001 - closing is best effort; it never replaces the sanitized error
        pass


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization headers or a Telegram bot token path to a redirect.
        return None


def _send_http(req: Request):
    # IPv4 first, 4 s connect per address (netconnect): a broken IPv6 route no longer costs the full
    # timeout per AAAA address. Request, redirect refusal, headers and timeout are unchanged.
    return build_opener(_NoRedirects(), IPv4FirstHTTPSHandler()).open(req, timeout=25)


# Fixed code for a request urllib/http.client refused to build or send: a control character in
# a header value or URL (http.client raises ValueError / InvalidURL) or an unknown URL scheme.
# Their messages quote the header value or URL, which can hold a bearer token or the Telegram
# bot token, so the original exception is dropped ("from None"), never printed or logged.
# The write marker is untouched: a failure after mark_write_started() stays C3 (ambiguous).
INVALID_REQUEST = "invalid_request"


def _request(url: str, *, data: bytes | None = None, method: str, headers: dict) -> Request:
    try:
        return Request(url, data=data, method=method, headers=headers)
    except ValueError:  # "unknown url type: '<url>'"
        raise PublishError(INVALID_REQUEST) from None


def _open(req: Request):
    """``_send_http`` with request-construction errors sanitized (see ``INVALID_REQUEST``)."""
    try:
        return _send_http(req)
    except http.client.InvalidURL:  # "URL can't contain control characters. '<url>'"
        raise PublishError(INVALID_REQUEST) from None
    except (URLError, OSError):  # includes HTTPError; handled by the callers
        raise
    except ValueError:  # "Invalid header value b'Bearer <token>\\r'" and similar
        raise PublishError(INVALID_REQUEST) from None
    except http.client.HTTPException:  # protocol failure after the request was handed over
        raise PublishError("invalid_api_response_uncertain") from None


def _decode_response(req: Request, *, telegram_body: bool = False) -> dict:
    try:
        with _open(req) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise PublishError("response_too_large")
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise PublishError("unexpected_response_type")
            return data
    except HTTPError as exc:
        try:
            raise http_error(exc, telegram_body=telegram_body) from None
        finally:
            close_http_error(exc)
    except (URLError, TimeoutError, OSError):
        raise PublishError("network_or_timeout_uncertain") from None
    except (json.JSONDecodeError, UnicodeError, http.client.HTTPException):
        raise PublishError("invalid_api_response_uncertain") from None


def request_json(url: str, payload: dict, headers: dict | None = None, *, form=False,
                 telegram_body: bool = False) -> dict:
    """HTTP POST with bounded timeout, no redirects and sanitized errors."""
    if form:
        # doseq: a list value becomes repeated fields (Mastodon media_ids[]); str values are unchanged.
        body = urlencode(payload, doseq=True).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        content_type = "application/json"
    req = _request(url, data=body, method="POST",
                   headers={"Content-Type": content_type, "User-Agent": "ReachWeaver/0.3", **(headers or {})})
    return _decode_response(req, telegram_body=telegram_body)


def request_get_json(url: str, headers: dict | None = None, *, telegram_body: bool = False) -> dict:
    """HTTP GET used only for explicit authentication/identity preflight."""
    req = _request(url, method="GET", headers={"User-Agent": "ReachWeaver/0.3", **(headers or {})})
    return _decode_response(req, telegram_body=telegram_body)

def request_json_with_headers(url: str, payload: dict, headers: dict | None = None) -> tuple[int, dict, dict]:
    """JSON POST returning (status, lower-cased response headers, JSON object or {}).

    Same guarantees as request_json: bounded timeout, no redirects, bounded body
    and sanitized errors that never echo the URL, request headers or credentials.
    An empty body is allowed because some APIs (LinkedIn Posts) return the new
    resource ID only in a response header.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = _request(url, data=body, method="POST",
                   headers={"Content-Type": "application/json", "User-Agent": "ReachWeaver/0.3", **(headers or {})})
    try:
        with _open(req) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            names = {str(k).lower(): str(v) for k, v in response.headers.items()}
            raw = response.read(1_000_001)
    except HTTPError as exc:
        try:
            raise http_error(exc) from None
        finally:
            close_http_error(exc)
    except (URLError, TimeoutError, OSError):
        raise PublishError("network_or_timeout_uncertain") from None
    except http.client.HTTPException:  # e.g. IncompleteRead after the request was sent
        raise PublishError("invalid_api_response_uncertain") from None
    if len(raw) > 1_000_000:
        raise PublishError("response_too_large")
    if not raw.strip():
        return status, names, {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise PublishError("invalid_api_response_uncertain") from None
    if not isinstance(data, dict):
        raise PublishError("unexpected_response_type")
    return status, names, data


def _status_and_json(req: Request) -> tuple[int, dict]:
    """(HTTP status, JSON object) with the same sanitizing as ``_decode_response``.

    Needed where a 2xx status carries meaning (Mastodon media: 200/202 on upload, 206 while
    processing). An empty body is an error here: every caller expects a JSON entity.
    """
    try:
        with _open(req) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            raw = response.read(1_000_001)
    except HTTPError as exc:
        try:
            raise http_error(exc) from None
        finally:
            close_http_error(exc)
    except (URLError, TimeoutError, OSError):
        raise PublishError("network_or_timeout_uncertain") from None
    except http.client.HTTPException:
        raise PublishError("invalid_api_response_uncertain") from None
    if len(raw) > 1_000_000:
        raise PublishError("response_too_large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise PublishError("invalid_api_response_uncertain") from None
    if not isinstance(data, dict):
        raise PublishError("unexpected_response_type")
    return status, data


def multipart_body(fields: list[tuple[str, str]], file_field: str, filename: str, mime: str,
                   data: bytes) -> tuple[bytes, str]:
    """RFC 7578 multipart/form-data with text fields and one file part; stdlib only.

    Names, the filename and the MIME type are fixed ASCII tokens chosen by ReachWeaver (never
    a local path). Text values are UTF-8. The boundary is random and re-drawn if it occurs
    in any part, so no value can terminate a part early.
    """
    for token in (file_field, filename, mime, *(name for name, _ in fields)):
        if not re.fullmatch(r"[A-Za-z0-9._/+\[\]-]{1,64}", token):
            raise PublishError(INVALID_REQUEST)
    values = [(name, value.encode("utf-8")) for name, value in fields]
    while True:
        boundary = "ReachWeaver-" + secrets.token_hex(16)
        marker = boundary.encode("ascii")
        if marker not in data and not any(marker in value for _, value in values):
            break
    out = io.BytesIO()
    for name, value in values:
        out.write(b"--" + marker + b"\r\n")
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        out.write(value + b"\r\n")
    out.write(b"--" + marker + b"\r\n")
    out.write(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("ascii"))
    out.write(f"Content-Type: {mime}\r\n\r\n".encode("ascii"))
    out.write(data + b"\r\n--" + marker + b"--\r\n")
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"
def request_put_bytes(url: str, data: bytes, headers: dict) -> int:
    """HTTP PUT of raw bytes (issue #23 chunk upload); returns the HTTP status.

    Same guarantees as request_json: bounded timeout, no redirects, sanitized errors that never
    echo the URL (which may carry an upload token), the headers or the body. At most 1 MB of the
    response is read and discarded.
    """
    req = _request(url, data=data, method="PUT", headers={"User-Agent": "ReachWeaver/0.3", **headers})
    try:
        with _open(req) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            response.read(1_000_001)
    except HTTPError as exc:
        try:
            raise http_error(exc) from None
        finally:
            close_http_error(exc)
    except (URLError, TimeoutError, OSError):
        raise PublishError("network_or_timeout_uncertain") from None
    except http.client.HTTPException:
        raise PublishError("invalid_api_response_uncertain") from None
    return status


class Receipt(tuple):
    """(remote_id, remote_url) plus sanitized provider receipt fields in ``extra``.

    Unpacks exactly like the historical two-tuple, so callers stay compatible.
    """
    extra: dict[str, str]

    def __new__(cls, remote_id: str, remote_url: str | None, **extra: str):
        obj = super().__new__(cls, (remote_id, remote_url))
        obj.extra = extra
        return obj


def url_facets(text: str) -> list[dict]:
    """Bluesky link annotations at UTF-8 byte boundaries (not character offsets)."""
    return link_facets(text)


_DID = re.compile(r"did:[a-z]+:[A-Za-z0-9._:%-]{1,2000}")
_POST_URI = re.compile(r"at://(did:[a-z]+:[A-Za-z0-9._:%-]+)/app\.bsky\.feed\.post/([A-Za-z0-9._:~-]{1,512})")
_CID = re.compile(r"[A-Za-z0-9]{8,256}")


def bluesky_record(content: str, *, created_at: str | None = None, reply: dict | None = None) -> dict:
    """Minimal app.bsky.feed.post record: text, createdAt and link/hashtag facets.

    ``reply`` (issue #17) is ``{"root": {uri, cid}, "parent": {uri, cid}}``, the
    lexicon's ``#replyRef`` of two ``com.atproto.repo.strongRef`` (both required).
    """
    record: dict = {"$type": "app.bsky.feed.post", "text": content,
              "createdAt": created_at or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    facets = bluesky_facets(content)
    if facets:
        record["facets"] = facets
    if reply is not None:
        record["reply"] = {"root": {"uri": reply["root"]["uri"], "cid": reply["root"]["cid"]},
                           "parent": {"uri": reply["parent"]["uri"], "cid": reply["parent"]["cid"]}}
    return record


def publish_bluesky(account: dict, content: str, post_id: str, *, created_at: str | None = None,
                    reply: dict | None = None) -> Receipt:
    """Create one post on the account's configured PDS/entryway (default bsky.social).

    ``created_at`` (the local draft creation time) enables a deterministic TID
    record key, so a re-send of the same post collides on the PDS instead of
    duplicating. Without it the PDS assigns the key and no such protection exists.
    ``reply`` adds the approved reply reference (issue #17; see ``bluesky_record``).
    """
    service = bluesky_service(account)  # validated before any credential is read
    try:
        rkey = deterministic_tid(post_id, created_at) if created_at else None
    except ValueError:
        raise ValidationError("bluesky_record_key_unavailable: invalid created_at") from None
    prefix = account["env_prefix"]
    identifier = require_credential(prefix + "_IDENTIFIER")
    app_password = require_credential(prefix + "_APP_PASSWORD")
    session = request_json(service + "/xrpc/com.atproto.server.createSession",
                           {"identifier": identifier, "password": app_password})
    if str(session.get("handle", "")).casefold() != str(account["handle"]).casefold():
        raise ValidationError("identity_mismatch: Bluesky session handle does not match configured account")
    did, access = session.get("did"), session.get("accessJwt")
    if not isinstance(did, str) or not _DID.fullmatch(did) or not access:
        raise PublishError("bluesky_session_missing_fields")
    body = {"repo": did, "collection": "app.bsky.feed.post", "record": bluesky_record(content, reply=reply)}
    if rkey:
        body["rkey"] = rkey
    # Everything after this request is ambiguous: any failure is "uncertain" and
    # the caller moves the post to needs_review (never retried automatically).
    headers = {"Authorization": f"Bearer {access}"}
    mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    result = request_json(service + "/xrpc/com.atproto.repo.createRecord", body, headers=headers)
    uri, cid = result.get("uri"), result.get("cid")
    match = _POST_URI.fullmatch(uri) if isinstance(uri, str) else None
    if match is None:
        raise PublishError("bluesky_missing_post_uri_uncertain")
    if match.group(1) != did:
        raise PublishError("bluesky_receipt_repo_mismatch_uncertain")
    if rkey and match.group(2) != rkey:
        raise PublishError("bluesky_receipt_rkey_mismatch_uncertain")
    if not isinstance(cid, str) or not _CID.fullmatch(cid):
        raise PublishError("bluesky_receipt_missing_cid_uncertain")
    extra = {"cid": cid, **({"kind": "reply"} if reply is not None else {})}
    return Receipt(cast(str, uri), f"https://bsky.app/profile/{did}/post/{match.group(2)}", **extra)


_VISIBILITY_RANK = {"public": 0, "unlisted": 1, "private": 2}


def mastodon_status_payload(account: dict, content: str, reply: dict | None = None) -> dict:
    """Minimal form body: text, visibility and optional owner-pinned language/spoiler.

    ``reply`` (issue #17) adds ``in_reply_to_id`` and uses the target's bound
    visibility, which is never wider than the parent's or the account's own setting.
    """
    validate_mastodon_options(account)  # defense in depth for library callers: no DMs
    payload = {"status": content, "visibility": account.get("visibility", "public")}
    for field in ("language", "spoiler_text"):
        if account.get(field) is not None:
            payload[field] = account[field]
    if reply is not None:
        visibility, parent = reply.get("visibility"), reply.get("parent_visibility")
        if (visibility not in _VISIBILITY_RANK or parent not in _VISIBILITY_RANK
                or _VISIBILITY_RANK[visibility] < max(_VISIBILITY_RANK[parent], _VISIBILITY_RANK[payload["visibility"]])):
            raise ValidationError("reply_visibility_wider_than_parent_or_account")
        payload["visibility"] = visibility
        payload["in_reply_to_id"] = str(reply["in_reply_to_id"])
    return payload


# Mastodon media (issue #13, owner-approved 2026-10-09; disabled until a live receipt). Sources, accessed 2026-10-09,
# https://docs.joinmastodon.org/methods/media/ : POST /api/v2/media (multipart: file,
# description, focus) returns 200 when processed synchronously or 202 while the full-size
# file is still processing ("url will still be null"); GET /api/v1/media/:id returns 206
# while processing and 200 once a url is available. Version history: since 4.0.0 images are
# processed synchronously (200). https://docs.joinmastodon.org/entities/MediaAttachment/ :
# id is a String, type "image" for a static image. Both endpoints need scope write:media.
# https://docs.joinmastodon.org/methods/statuses/ : media_ids[] attaches uploaded media.
MEDIA_POLL_ATTEMPTS = 8
MEDIA_POLL_DELAYS = (1, 2, 4, 8, 8, 8, 8, 8)  # seconds; at most 47 s of waiting per attachment
_MEDIA_ID = re.compile(r"[A-Za-z0-9._~-]{1,128}")
_sleep = time.sleep  # replaced in tests


def _mastodon_media_ready(data: dict, expected_id: str | None = None) -> tuple[str, bool]:
    """(media id, has a processed https url) from a MediaAttachment entity, or raise."""
    media_id = data.get("id")
    if isinstance(media_id, bool) or not isinstance(media_id, (str, int)) or not _MEDIA_ID.fullmatch(str(media_id)):
        raise PublishError("mastodon_media_missing_id_uncertain")
    if expected_id is not None and str(media_id) != expected_id:
        raise PublishError("mastodon_media_id_mismatch_uncertain")
    if data.get("type") not in (None, "image"):
        raise PublishError("mastodon_media_type_mismatch_uncertain")
    url = data.get("url")
    ready = isinstance(url, str) and urlsplit(url).scheme == "https" and bool(urlsplit(url).hostname)
    return str(media_id), ready


def _staged(stage: str, exc: PublishError) -> PublishError:
    """``mastodon_media_<stage>_<code>``: the dead-letter code says no status was created yet."""
    return exc.with_code(f"mastodon_media_{stage}_{exc}")


def upload_mastodon_image(server: str, token: str, item: dict, data: bytes, position: int, *,
                          paused: Callable[[], bool] | None = None) -> str:
    """Upload one approved image and wait (bounded) until Mastodon has processed it.

    The caller has already called ``mark_write_started``: an uploaded but unattached media
    item is not a public post, but every failure from here on is still treated as
    ambiguous (needs_review). Returns the media id. Never retries an upload.
    """
    fields = [("description", item["alt"])]
    if item["focus"] is not None:
        fields.append(("focus", item["focus"]))
    from .attachments import EXTENSIONS
    body, content_type = multipart_body(fields, "file", f"attachment-{position}.{EXTENSIONS[item['mime']]}",
                                        item["mime"], data)
    auth = {"Authorization": f"Bearer {token}", "User-Agent": "ReachWeaver/0.3"}
    req = _request(server + "/api/v2/media", data=body, method="POST",
                   headers={**auth, "Content-Type": content_type})
    try:
        status, entity = _status_and_json(req)
        if status not in (200, 202):
            raise PublishError("unexpected_status_uncertain")
        media_id, ready = _mastodon_media_ready(entity)
    except PublishError as exc:
        raise _staged("upload", exc) from None
    for delay in MEDIA_POLL_DELAYS[:MEDIA_POLL_ATTEMPTS]:
        if ready:
            return media_id
        if paused is not None and paused():
            raise PublishError("mastodon_media_processing_paused_uncertain")
        _sleep(delay)
        try:
            status, entity = _status_and_json(_request(server + f"/api/v1/media/{media_id}", method="GET",
                                                       headers=auth))
            if status not in (200, 206):
                raise PublishError("unexpected_status_uncertain")
            _, ready = _mastodon_media_ready(entity, media_id)
            if status == 206:
                ready = False
        except PublishError as exc:
            raise _staged("poll", exc) from None
    if ready:
        return media_id
    raise PublishError("mastodon_media_processing_timeout_uncertain")


def publish_mastodon(account: dict, content: str, post_id: str, *, media: list[dict] | None = None,
                     paused: Callable[[], bool] | None = None, reply: dict | None = None) -> Receipt:
    """POST /api/v1/statuses with Idempotency-Key = local post id.

    Mastodon stores idempotency keys for up to 1 hour only; ReachWeaver's
    needs_review rule remains the duplicate guard beyond that window.

    ``media`` (#13): approved attachment records in send order. Every file is read and
    checked against its approved SHA-256 BEFORE the write marker (a changed file is certain
    not sent); the write marker is set before the FIRST upload, each image is uploaded and
    polled, then the status is created with ``media_ids[]``. ``paused`` is consulted before
    every poll; a pause mid-send stops waiting (ambiguous, needs_review), it never resends.

    ``reply`` (issue #17): ``in_reply_to_id`` on the account's own server only. A reply is
    text-only: a reply with media is refused before any credential read.
    """
    prefix = account["env_prefix"]
    server = require_https_public_server(str(account.get("server", "")))
    if reply is not None and reply.get("server") != server:
        raise ValidationError("reply_server_mismatch: the status id belongs to another server")
    if reply is not None and media:
        from .storage import REPLY_MEDIA_REFUSED
        raise ValidationError(REPLY_MEDIA_REFUSED)
    payload: dict = mastodon_status_payload(account, content, reply)
    media = list(media or [])
    blobs: list[bytes] = []
    if media:
        from .attachments import check_media_list, read_approved_bytes
        from .capabilities import require_capability
        require_capability("mastodon", "image", "publish")  # also for direct library callers
        check_media_list(media, "mastodon", account)
        blobs = [read_approved_bytes(item) for item in media]  # exactly these bytes are uploaded
    token = require_credential(prefix + "_TOKEN")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": post_id}
    mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    media_ids = [upload_mastodon_image(server, token, item, data, position, paused=paused)
                 for position, (item, data) in enumerate(zip(media, blobs), 1)]
    if media_ids:
        if paused is not None and paused():
            raise PublishError("mastodon_media_uploaded_paused_before_status_uncertain")
        payload["media_ids[]"] = media_ids
    result = request_json(server + "/api/v1/statuses", payload, headers=headers, form=True)
    remote_id = result.get("id")
    if isinstance(remote_id, bool) or not isinstance(remote_id, (str, int)) or not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", str(remote_id)):
        raise PublishError("mastodon_missing_post_id_uncertain")
    links = [result.get(k) for k in ("url", "uri")]
    links = [v for v in links if isinstance(v, str) and urlsplit(v).scheme == "https" and urlsplit(v).hostname]
    if not links:
        raise PublishError("mastodon_missing_post_url_uncertain")
    author = result.get("account")
    if isinstance(author, dict) and account.get("handle"):
        expected = str(account["handle"]).strip().lstrip("@").split("@")[0].casefold()
        if str(author.get("username", "")).casefold() != expected:
            raise PublishError("mastodon_receipt_account_mismatch_uncertain")
    if "visibility" in result and result["visibility"] != payload["visibility"]:
        raise PublishError("mastodon_receipt_visibility_mismatch_uncertain")
    if media_ids:
        attached = result.get("media_attachments")
        returned = [str(m.get("id")) for m in attached if isinstance(m, dict)] if isinstance(attached, list) else None
        if returned != media_ids:
            raise PublishError("mastodon_receipt_media_mismatch_uncertain")
        return Receipt(str(remote_id), links[0], visibility=payload["visibility"], media=str(len(media_ids)))
    if reply is not None:
        if str(result.get("in_reply_to_id") or "") != payload["in_reply_to_id"]:
            raise PublishError("mastodon_receipt_reply_target_mismatch_uncertain")
        return Receipt(str(remote_id), links[0], visibility=payload["visibility"],
                       in_reply_to_id=payload["in_reply_to_id"])
    return Receipt(str(remote_id), links[0], visibility=payload["visibility"])


def publish_telegram(account: dict, content: str, post_id: str) -> tuple[str,str | None]:
    prefix = account["env_prefix"]
    token = require_credential(prefix + "_BOT_TOKEN")
    # API path includes a token. request_json never surfaces the request URL in error messages.
    message = {"chat_id": account["chat_id"], "text": content, "disable_notification": False}
    mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    # telegram_body: a 429 body is read (at most 4 KB) for parameters.retry_after only.
    result = request_json(f"https://api.telegram.org/bot{token}/sendMessage", message, telegram_body=True)
    if result.get("ok") is not True or not isinstance(result.get("result"), dict):
        raise PublishError("telegram_api_not_ok_uncertain")
    message_id = result["result"].get("message_id")
    if message_id is None:
        raise PublishError("telegram_missing_message_id_uncertain")
    channel_username = account.get("channel_username")
    remote_url = f"https://t.me/{channel_username}/{message_id}" if channel_username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", channel_username) else None
    return str(message_id), remote_url


def publish_linkedin(account: dict, content: str, post_id: str, *, created_at: str | None = None) -> tuple[str,str]:
    # Member (personal profile) text only; logic and checks live in linkedin.py.
    # created_at is accepted for dispatch compatibility; LinkedIn sets its own time.
    from .linkedin import publish_member_post
    return publish_member_post(account, content, post_id)


def publish_instagram(account: dict, content: str, post_id: str, *, media: list[dict] | None = None,
                      paused=None) -> Receipt:
    # Issue #20 (owner-approved 2026-10-09; disabled until a live receipt): unreachable while
    # instagram is outside SUPPORTED_LIVE and the registry adapter is "none"; publish() refuses
    # it before this entry is looked up.
    from .instagram import publish_image_post
    return publish_image_post(account, content, post_id, media or [], paused=paused)


def publish_threads(account: dict, content: str, post_id: str, *, created_at: str | None = None,
                    paused: Callable[[], bool] | None = None) -> Receipt:
    # Issue #19 (owner-approved 2026-10-09; disabled until a live receipt): unreachable while
    # threads is outside SUPPORTED_LIVE and the registry adapter is "none"; publish() refuses it
    # before this entry is looked up. ``paused`` is re-read during the container wait (D19-2).
    from .threads import publish_text_post
    return publish_text_post(account, content, post_id, paused=paused)


def publish_facebook(account: dict, content: str, post_id: str, *, created_at: str | None = None) -> Receipt:
    # Issue #21 (owner-approved 2026-10-09; disabled until a live receipt): unreachable while
    # facebook is outside SUPPORTED_LIVE and the registry adapter is "none"; publish() refuses it
    # before this entry is looked up. One write request, so there is no wait to pause.
    from .facebook import publish_page_post
    return publish_page_post(account, content, post_id)


PUBLISHERS: dict[str, Callable[..., tuple[str, str | None]]] = {
    "bluesky": publish_bluesky,
    "mastodon": publish_mastodon,
    "telegram": publish_telegram,
    "linkedin": publish_linkedin,
    "instagram": publish_instagram,
    "threads": publish_threads,
    "facebook": publish_facebook,
}
# Platforms whose posts are media with a caption, never text alone (issue #20).
MEDIA_ONLY_PLATFORMS = frozenset({"instagram"})


def publish(account: dict, content: str, post_id: str, *, verified_connection=None,
            created_at: str | None = None, media: list[dict] | None = None,
            paused: Callable[[], bool] | None = None, reply_target: dict | None = None,
            optout: list | None = None) -> tuple[str,str | None]:
    """Dispatch one approved post. ``media``: approved image attachment records (#13 Mastodon local
    files, #20 Instagram hosted URLs); ``paused``: optional callable re-checked during a multi-step send.

    ``reply_target`` (issue #17, a ``replies.load_target`` dict) makes it a reply: refused before
    any request unless the capability registry enables replies for the platform
    (``reachweaver_reply_adapter: live_reply``), the reply carries no media, the target matches
    the account (no backend, same Mastodon server, visibility never wider) and, when ``optout``
    is given, the parent author is not on the opt-out list.

    Send gate (wave 9 item 3): the first step, before any capability, credential or network use,
    is ``sendgate.consume``. It refuses (fixed ``send_*`` codes) unless ``storage.claim_sending``
    committed a claim of exactly this post, account, content and media in this process under live
    caps, and the live opt-in env gate holds. A caller-built ``VerifiedConnection`` is honoured only
    if ``connections.check_connection`` returned it in this process; otherwise the check is rerun."""
    from . import sendgate
    sendgate.consume(post_id, account, content, media)
    if reply_target is not None:
        if media:
            from .storage import REPLY_MEDIA_REFUSED
            raise ValidationError(REPLY_MEDIA_REFUSED)
        from .capabilities import require_reply_capability
        from .replies import REPLY_PLATFORMS, load_target, canonical_target, target_problems
        load_target(canonical_target(reply_target))  # shape check, fail closed
        if account.get("platform") not in REPLY_PLATFORMS or reply_target["platform"] != account.get("platform"):
            raise ValidationError("reply_platform_unsupported")
        problems = target_problems(reply_target, account, list(optout or []))
        if problems:
            raise ValidationError("; ".join(problems))
        require_reply_capability(account["platform"])
    if media and account.get("backend") is not None:
        # Never publish the text without its approved images (#13/#20).
        raise ValidationError("media_attachments_unsupported via a publishing backend")
    if account.get("backend") is not None:
        # Explicit owner opt-in backend (issue #30). Postiz acceptance raises
        # postiz.BackendAccepted (a PublishError): never a platform receipt.
        from .postiz import publish as publish_via_backend
        return publish_via_backend(account, content, post_id, verified_connection=verified_connection)
    platform = account["platform"]
    if platform not in SUPPORTED_LIVE:
        raise ValidationError(f"Live publishing not yet implemented for {platform}. Use a manual workflow or approved external integration.")
    # Strengthening guard only (issue #6): the capability registry must also
    # positively allow live text, otherwise fail closed before any network use.
    # A media-only platform needs the image format instead (#20), and images on a text platform
    # need it too (#13); both come from the registry's live_media_formats (fail closed).
    from .capabilities import LIVE_MEDIA_FORMATS, require_capability
    if platform in MEDIA_ONLY_PLATFORMS:
        if not media:
            raise ValidationError(f"{platform}_image_required: an approved image attachment is needed")
        require_capability(platform, "image", "publish")
    else:
        require_capability(platform, "text", "publish")
        if media:
            if "image" not in LIVE_MEDIA_FORMATS.get(platform, ()):
                raise ValidationError(f"media_attachments_unsupported: No ReachWeaver live media adapter for "
                                      f"{platform}; use manual export")
            require_capability(platform, "image", "publish")
    problems = validate_post(platform, content, account)
    if problems:
        raise ValidationError("; ".join(problems))
    if platform == "bluesky" and created_at:
        try:
            deterministic_tid(post_id, created_at)  # reject a bad timestamp before any network use
        except ValueError:
            raise ValidationError("bluesky_record_key_unavailable: invalid created_at") from None
    # A library caller must pass an immediately-obtained identity check or one
    # is performed here. The CLI verifies BEFORE claiming the durable send.
    from .connections import VerifiedConnection, check_connection, was_issued_by_check
    if not (was_issued_by_check(verified_connection) and isinstance(verified_connection, VerifiedConnection) and
            verified_connection.account_id == account["id"] and
            verified_connection.platform == platform and verified_connection.identity == "verified"):
        check_connection(account)
    if reply_target is not None:
        if platform == "bluesky":
            from .replies import bluesky_reply_ref
            return publish_bluesky(account, content, post_id, created_at=created_at,
                                   reply=bluesky_reply_ref(reply_target))
        return publish_mastodon(account, content, post_id, reply=reply_target)
    if platform == "bluesky":
        return publish_bluesky(account, content, post_id, created_at=created_at)
    if platform == "mastodon":
        return publish_mastodon(account, content, post_id, media=media, paused=paused)
    if platform == "instagram":
        return publish_instagram(account, content, post_id, media=media, paused=paused)
    if platform == "threads":
        return publish_threads(account, content, post_id, paused=paused)
    return PUBLISHERS[platform](account,content,post_id)


def publish_video(account: dict, content: str, post_id: str, video: dict, *, verified_connection=None,
                  paused=None) -> Receipt:
    """Upload one approved YouTube video (issue #22; owner-approved 2026-10-09, disabled until a
    live receipt). ``content`` is the description; ``video`` the shared video record (``video.py``).

    Unreachable while ``policy.SUPPORTED_LIVE_VIDEO`` is empty and the registry's
    ``reachweaver_video_upload`` switch is off: ``capabilities.require_video_mode`` checks both
    here before any credential read or request. ``paused`` (a callable) is re-checked between
    upload chunks; a pause mid-upload stops it (needs_review), it never resumes or resends.
    TikTok video goes through ``tiktok.run_live_send``, never here.
    """
    from . import sendgate
    sendgate.consume(post_id, account, content)  # wave 9 item 3: first step, before any other check
    if account.get("backend") is not None:
        raise ValidationError("Video uploads are not supported through a publishing backend")
    platform = account["platform"]
    if platform not in SUPPORTED_LIVE_VIDEO:
        raise ValidationError(f"Live video upload not enabled for {platform}. Use a manual workflow.")
    if platform != "youtube" or not isinstance(video, dict) or video.get("platform") != platform:
        raise ValidationError(f"video_unsupported: publish_video uploads YouTube records only, not {platform}")
    from .capabilities import require_video_mode
    require_video_mode(platform, "upload")
    problems = validate_post(platform, content, account)
    if problems:
        raise ValidationError("; ".join(problems))
    from .connections import VerifiedConnection, check_connection, was_issued_by_check
    if not (was_issued_by_check(verified_connection) and isinstance(verified_connection, VerifiedConnection) and
            verified_connection.account_id == account["id"] and
            verified_connection.platform == platform and verified_connection.identity == "verified"):
        check_connection(account)
    from .youtube import upload_video
    return upload_video(account, content, post_id, video, paused=paused)


@runtime_checkable
class PublishingBackend(Protocol):
    """The publishing-path contract (issue #30): what the native adapters and an opt-in backend
    (today only Postiz, ``postiz.POSTIZ_BACKEND``) each provide. ``backend_for`` names the path an
    account takes. Declarative only: ``publish`` above dispatches exactly as before, and every gate
    (approval binding, durable claim, send gate, pause, budget, live opt-in) stays with the callers.
    ``tests/test_postiz_contract.py`` runs the same conformance checks against both paths.

    * ``preview(account, content)``: an offline description of the create request. No network, no
      credential, no database. Refuses text that fails ``validate_post``.
    * ``create(account, content, post_id, *, verified_connection=None)``: the one content-create
      request, only after a durable ``storage.claim_sending`` claim (``sendgate.consume`` refuses
      otherwise). It returns a first-party receipt or raises ``PublishError``/``ValidationError``
      and never retries.
    * ``lookup_receipt(account, remote_ref, around)``: read-only and never reconciles. ``None``
      when the path has nothing to look up because ``create`` returned a first-party receipt.
    * Reconciliation hand-off: ``acceptance_status`` is the state a successful ``create`` leaves
      the claimed post in: ``published`` when ``receipt_is_first_party``, otherwise
      ``needs_review``. Every other post-claim outcome is ``needs_review``, and only a human
      ``reconcile`` moves a post from there to ``published``.
    """

    name: str
    acceptance_status: str
    receipt_is_first_party: bool

    def preview(self, account: dict, content: str) -> dict: ...

    def create(self, account: dict, content: str, post_id: str, *,
               verified_connection=None) -> tuple[str, str | None]: ...

    def lookup_receipt(self, account: dict, remote_ref: str, around: str) -> dict | None: ...


class NativePath:
    """The direct adapters above, described by the ``PublishingBackend`` contract."""

    name = "native"
    acceptance_status = "published"
    receipt_is_first_party = True

    def preview(self, account: dict, content: str) -> dict:
        if account.get("backend") is not None:
            raise ValidationError("native_path_refuses_backend_account")
        platform = account["platform"]
        problems = validate_post(platform, content, account)
        if problems:
            raise ValidationError("; ".join(problems))
        return {"path": self.name, "account_id": account["id"], "platform": platform, "content": content,
                "live_text_adapter": platform in SUPPORTED_LIVE, "network": "none"}

    def create(self, account: dict, content: str, post_id: str, *,
               verified_connection=None) -> tuple[str, str | None]:
        if account.get("backend") is not None:
            raise ValidationError("native_path_refuses_backend_account")
        return publish(account, content, post_id, verified_connection=verified_connection)

    def lookup_receipt(self, account: dict, remote_ref: str, around: str) -> dict | None:
        return None  # create() already returned the platform's own receipt


NATIVE_PATH = NativePath()


def backend_for(account: dict) -> PublishingBackend:
    """The publishing path an account entry selects: no ``backend`` key means native, there is no
    default backend, and an unknown value is refused (``accounts.load_accounts`` refuses it too)."""
    backend = account.get("backend")
    if backend is None:
        return NATIVE_PATH
    from .postiz import BACKEND, POSTIZ_BACKEND
    if backend == BACKEND:
        return POSTIZ_BACKEND
    raise ValidationError("Unknown publishing backend; only 'postiz' is supported")
