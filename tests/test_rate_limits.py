"""Issue #11 provider rate limits (owner-approved 2026-10-08, SECURITY-REVIEW-PROPOSAL-7-11.md part 2).

Every HTTP exchange is scripted at ``publishers._send_http``; nothing reaches any host, no
test sleeps, and no live publishing happens. The named tests follow proposal section 3.2.
The rule under test throughout (D16): a claimed post is never retried or resent; a 429 only
delays the account's OTHER, unclaimed posts.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from test_no_network import NetworkGuard

from hold_support import claim as claim_sending
from reachweaver import ratelimit
from reachweaver.caps import (
    effective_caps, effective_rate_limits, load_caps, providers_for, validate_caps,
)
from reachweaver.cli import main
from reachweaver.health import CLASS_GUIDANCE, build_health, dead_letter, exit_code, render_health
from reachweaver.policy import ValidationError
from reachweaver.privacy import apply_purge, plan_purge
from reachweaver.publishers import PublishError, http_error
from reachweaver.ratelimit import (
    AMBIGUOUS, CEILING_SECONDS, CERTAIN_NOT_SENT, DEFAULT_SECONDS, FLOOR_SECONDS, LIKELY_NOT_CREATED,
    RetryHint, classify_send_failure, cooldown_deadline, hint_from_http_error, parse_retry_after,
    parse_telegram_retry_after, parse_x_ratelimit_reset, preflight_retries, preflight_with_backoff,
)
from reachweaver.storage import (
    active_provider_cooldown, add_post, connect, event, get_post, provider_cooldowns,
    queue_health, record_provider_cooldown, set_paused, transition, uncertain_send,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
NOW_S = NOW.isoformat(timespec="seconds")
# Built at runtime so the tracked-file secret scan never sees a credential-shaped literal.
MARKER = "-".join(("leaky", "provider", "body", "marker"))
TG_TOKEN = ":".join(("123456", "AAtestonlytoken_x"))
PZ_KEY = "-".join(("pz", "test", "key", "0001"))
ENV = {
    "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
    "REACHWEAVER_RL_BSKY_IDENTIFIER": "owner.bsky.social",
    "REACHWEAVER_RL_BSKY_APP_PASSWORD": "-".join(("bsky", "app", "pw")),
    "REACHWEAVER_RL_BSKY2_IDENTIFIER": "owner.bsky.social",
    "REACHWEAVER_RL_BSKY2_APP_PASSWORD": "-".join(("bsky", "two", "pw")),
    "REACHWEAVER_RL_MASTO_TOKEN": "-".join(("masto", "tok")),
    "REACHWEAVER_RL_TG_BOT_TOKEN": TG_TOKEN,
    "REACHWEAVER_RL_LI_ACCESS_TOKEN": "-".join(("li", "tok")),
    "REACHWEAVER_RL_PZ_POSTIZ_API_KEY": PZ_KEY,
}
ACCOUNTS = [
    {"id": "rl-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_RL_BSKY",
     "handle": "owner.bsky.social"},
    # Own env_prefix: load_accounts refuses identical or overlapping prefixes (#5).
    {"id": "rl-bluesky-two", "platform": "bluesky", "env_prefix": "REACHWEAVER_RL_BSKY2",
     "handle": "owner.bsky.social"},
    {"id": "rl-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_RL_MASTO",
     "server": "https://mastodon.example.org", "handle": "owner"},
    {"id": "rl-telegram", "platform": "telegram", "env_prefix": "REACHWEAVER_RL_TG",
     "chat_id": "@examplechannel", "channel_username": "examplechannel", "bot_username": "example_bot"},
    {"id": "rl-linkedin", "platform": "linkedin", "env_prefix": "REACHWEAVER_RL_LI",
     "person_id": "782bbtaQ", "linkedin_version": "202609",
     "authorization_expires_at": "2026-12-01T00:00:00+00:00"},
    {"id": "rl-postiz", "platform": "mastodon", "env_prefix": "REACHWEAVER_RL_PZ", "handle": "owner",
     "backend": "postiz", "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-masto-1"},
]
DID = "did:plc:owner123"
CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"
SESSION = {"did": DID, "handle": "owner.bsky.social", "accessJwt": "mock-access"}


class Resp:
    def __init__(self, data=None, *, status=200, headers=None, raw=None):
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self._raw = raw if raw is not None else json.dumps(data).encode("utf-8")

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._raw if limit is None or limit < 0 else self._raw[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http(code, headers=None, body=b""):
    """A fresh HTTPError whose URL holds a fake secret that must never be surfaced."""
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return HTTPError(f"https://provider.invalid/{MARKER}", code, MARKER, message, io.BytesIO(body))


class Wire:
    """Scripted ``_send_http``: (METHOD, url-substring) -> list of replies consumed in order.

    A reply is a JSON-able object, a ``Resp``, an exception instance, or a zero-argument
    callable producing one of those (so each retry gets a fresh HTTPError body).
    """

    def __init__(self, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, req):
        method = req.get_method()
        for (want, part), replies in self.routes.items():
            if want == method and part in req.full_url:
                self.calls.append((method, part))
                if not replies:
                    raise AssertionError(f"no scripted reply left for {method} {part}")
                reply = replies.pop(0)
                if callable(reply) and not isinstance(reply, BaseException):
                    reply = reply()
                if isinstance(reply, BaseException):
                    raise reply
                return reply if isinstance(reply, Resp) else Resp(reply)
        raise AssertionError(f"unexpected request {method} {req.full_url.split('?')[0][-40:]}")

    def count(self, method, part):
        return sum(1 for call in self.calls if call == (method, part))


# Read-only identity preflight replies per adapter.
TG_PREFLIGHT = {
    ("GET", "/getMe"): [{"ok": True, "result": {"id": 1, "is_bot": True, "username": "example_bot"}}] * 4,
    ("GET", "/getChat?"): [{"ok": True, "result": {"id": -100, "type": "channel", "username": "examplechannel"}}] * 4,
    ("GET", "/getChatMember"): [{"ok": True, "result": {"status": "administrator", "can_post_messages": True}}] * 4,
}
MASTO_ME = {"id": "1", "username": "owner"}
LI_ME = Resp({"sub": "782bbtaQ"})
PZ_INTEGRATIONS = [{"id": "int-masto-1", "identifier": "mastodon", "profile": "owner", "disabled": False}]


def create_route(platform):
    return {"bluesky": ("POST", "repo.createRecord"), "mastodon": ("POST", "/api/v1/statuses"),
            "telegram": ("POST", "/sendMessage"), "linkedin": ("POST", "/rest/posts"),
            "postiz": ("POST", "/public/v1/posts")}[platform]


def ok_routes(platform, create_replies):
    """Preflight succeeds; the content create gets ``create_replies``."""
    if platform == "bluesky":
        return {("POST", "createSession"): [SESSION, SESSION], create_route(platform): create_replies}
    if platform == "mastodon":
        return {("GET", "verify_credentials"): [MASTO_ME], create_route(platform): create_replies}
    if platform == "telegram":
        return {**TG_PREFLIGHT, create_route(platform): create_replies}
    if platform == "linkedin":
        return {("GET", "/v2/userinfo"): [LI_ME], create_route(platform): create_replies}
    return {("GET", "/integrations"): [PZ_INTEGRATIONS], create_route(platform): create_replies}


ACCOUNT_OF = {"bluesky": "rl-bluesky", "mastodon": "rl-mastodon", "telegram": "rl-telegram",
              "linkedin": "rl-linkedin", "postiz": "rl-postiz"}
PLATFORM_OF = {"postiz": "mastodon"}


class RateLimitBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.base = base
        self.db = str(base / "state" / "rl.sqlite")
        self.config = base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": ACCOUNTS}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        clock = patch("reachweaver.linkedin._utc_now", return_value=datetime.now(timezone.utc).replace(
            year=2026, month=10, day=8))
        clock.start()
        self.addCleanup(clock.stop)
        self.sleeps: list[float] = []
        sleeper = patch("reachweaver.ratelimit._sleep", side_effect=self.sleeps.append)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def approved(self, account="rl-bluesky", text="Evidence-backed release note", schedule=None):
        platform = next(a["platform"] for a in ACCOUNTS if a["id"] == account)
        with connect(self.db) as db:
            pid = add_post(db, account, platform, text)
            transition(db, pid, "approve")
            if schedule:
                transition(db, pid, "schedule", schedule)
        return pid

    def cli(self, *argv, env=ENV):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def live(self, wire, *argv):
        with patch("reachweaver.publishers._send_http", wire):
            return self.cli("publish", "--live", *argv)

    def row(self, pid):
        with connect(self.db) as db:
            return get_post(db, pid)

    def events(self, action, post_id=None):
        with connect(self.db) as db:
            query, params = "SELECT post_id, detail FROM event_log WHERE action=?", [action]
            if post_id:
                query += " AND post_id=?"
                params.append(post_id)
            return [dict(r) for r in db.execute(query + " ORDER BY id", params)]

    def cooldowns(self):
        with connect(self.db) as db:
            return provider_cooldowns(db)


# --------------------------------------------------------------------------- pure parsing (D15)

class RetryAfterParsingTests(unittest.TestCase):
    def test_parse_retry_after_delay_seconds(self):
        self.assertEqual(parse_retry_after("120", NOW), 120)
        self.assertEqual(parse_retry_after(" 7 ", NOW), 7)
        self.assertEqual(parse_retry_after("0", NOW), 0)  # valid; the floor raises it later
        self.assertEqual(parse_retry_after("007", NOW), 7)

    def test_parse_retry_after_http_date(self):
        later = format_datetime(NOW + timedelta(minutes=5), usegmt=True)
        self.assertEqual(later, "Thu, 08 Oct 2026 12:05:00 GMT")
        self.assertEqual(parse_retry_after(later, NOW), 300)
        self.assertEqual(parse_retry_after("Thu, 08 Oct 2026 12:00:01 GMT", NOW), 1)

    def test_parse_retry_after_rejects_negative_garbage_and_past(self):
        past = format_datetime(NOW - timedelta(minutes=5), usegmt=True)
        for value in ("-5", "1.5", "soon", "", "   ", None, 120, b"120", "１２０", "12 0", past,
                      "Thu, 08 Oct 2026 12:05:00 -0000",  # unknown zone: not an HTTP-date
                      format_datetime(NOW, usegmt=True),  # exactly now: nothing to wait for
                      "x" * 65, "1" * 65, MARKER):
            with self.subTest(value=value):
                self.assertIsNone(parse_retry_after(value, NOW))

    def test_parse_retry_after_clamps_to_floor_and_ceiling(self):
        args = {"platform": "mastodon", "backend": None, "now": NOW}
        low = cooldown_deadline(RetryHint(5, "retry_after"), **args)
        self.assertEqual((low["seconds"], low["source"], low["ceiling_clamped"]), (FLOOR_SECONDS, "retry_after", False))
        self.assertEqual(low["until"], "2026-10-08T12:01:00+00:00")
        huge = parse_retry_after("9" * 64, NOW)  # the largest accepted value; clamped, never ignored
        self.assertGreater(huge, CEILING_SECONDS)
        top = cooldown_deadline(RetryHint(huge, "retry_after"), **args)
        self.assertEqual((top["seconds"], top["ceiling_clamped"]), (CEILING_SECONDS, True))
        self.assertEqual(top["until"], "2026-10-09T12:00:00+00:00")
        exact = cooldown_deadline(RetryHint(CEILING_SECONDS, "retry_after"), **args)
        self.assertFalse(exact["ceiling_clamped"])
        none = cooldown_deadline(None, **args)
        self.assertEqual((none["seconds"], none["source"]), (DEFAULT_SECONDS, "default"))
        floor = cooldown_deadline(RetryHint(120, "retry_after"), floor_seconds=3600, **args)
        self.assertEqual(floor["seconds"], 3600)
        for bad in (59, 86401, True, "60"):
            with self.subTest(floor=bad), self.assertRaises(ValidationError):
                cooldown_deadline(None, floor_seconds=bad, **args)
        unknown = cooldown_deadline(RetryHint(500, "made_up_source"), **args)
        self.assertEqual(unknown["source"], "default")

    def test_x_ratelimit_reset_and_telegram_body_parsers(self):
        self.assertEqual(parse_x_ratelimit_reset("2026-10-08T12:30:00.000Z", NOW), 1800)
        self.assertEqual(parse_x_ratelimit_reset("2026-10-08T14:30:00+02:00", NOW), 1800)
        for value in ("2026-10-08T11:00:00Z", "2026-10-08T12:30:00", "tomorrow", "", None, "2" * 65):
            with self.subTest(value=value):
                self.assertIsNone(parse_x_ratelimit_reset(value, NOW))
        self.assertEqual(parse_telegram_retry_after(b'{"ok":false,"parameters":{"retry_after":42}}'), 42)
        for body in (b'{"parameters":{"retry_after":-1}}', b'{"parameters":{"retry_after":true}}',
                     b'{"parameters":{"retry_after":"42"}}', b'{"parameters":[]}', b"[]", b"not json",
                     b"\xff\xfe", None, "text"):
            with self.subTest(body=body):
                self.assertIsNone(parse_telegram_retry_after(body))

    def test_hint_only_for_429_503_and_body_only_when_asked(self):
        telegram = b'{"parameters":{"retry_after":77}}'
        self.assertIsNone(hint_from_http_error(http(500, {"Retry-After": "30"}), now=NOW))
        self.assertEqual(hint_from_http_error(http(503, {"Retry-After": "30"}), now=NOW), RetryHint(30, "retry_after"))
        self.assertIsNone(hint_from_http_error(http(429, body=telegram), now=NOW))  # body not read by default
        self.assertEqual(hint_from_http_error(http(429, body=telegram), telegram_body=True, now=NOW),
                         RetryHint(77, "telegram_retry_after"))
        # Retry-After wins over the body and X-RateLimit-Reset.
        both = http(429, {"Retry-After": "90", "X-RateLimit-Reset": "2026-10-08T12:30:00Z"}, telegram)
        self.assertEqual(hint_from_http_error(both, telegram_body=True, now=NOW), RetryHint(90, "retry_after"))
        # Bluesky ratelimit-reset is deliberately not used (unit unverified): default applies.
        self.assertIsNone(hint_from_http_error(http(429, {"ratelimit-reset": "1759999999"}), now=NOW))
        # A broken error object never raises from the hint path.
        self.assertIsNone(hint_from_http_error(HTTPError("https://x.invalid", 429, "m", None, None), now=NOW))  # type: ignore[arg-type]
        self.assertIsNone(hint_from_http_error(object(), now=NOW))

    def test_mastodon_x_ratelimit_reset_used_when_no_retry_after(self):
        reset = http(429, {"X-RateLimit-Reset": "2026-10-08T12:30:00.000Z"})
        self.assertEqual(hint_from_http_error(reset, now=NOW), RetryHint(1800, "x_ratelimit_reset"))
        with_header = http(429, {"Retry-After": "120", "X-RateLimit-Reset": "2026-10-08T12:30:00.000Z"})
        self.assertEqual(hint_from_http_error(with_header, now=NOW), RetryHint(120, "retry_after"))

    def test_linkedin_429_defaults_to_next_midnight_utc(self):
        evening = datetime(2026, 10, 8, 21, 30, tzinfo=timezone.utc)
        deadline = cooldown_deadline(None, platform="linkedin", backend=None, now=evening)
        self.assertEqual((deadline["until"], deadline["source"]), ("2026-10-09T00:00:00+00:00", "daily_reset"))
        late = datetime(2026, 10, 8, 23, 59, 30, tzinfo=timezone.utc)
        self.assertEqual(cooldown_deadline(None, platform="linkedin", backend=None, now=late)["until"],
                         "2026-10-09T00:00:30+00:00")  # 30 s to midnight is raised to the 60 s floor
        via_postiz = cooldown_deadline(None, platform="linkedin", backend="postiz", now=evening)
        self.assertEqual((via_postiz["seconds"], via_postiz["source"]), (DEFAULT_SECONDS, "default"))

    def test_http_error_keeps_code_sanitized_with_typed_hint(self):
        exc = http_error(http(429, {"Retry-After": "120", "X-Secret": MARKER}, MARKER.encode()))
        self.assertEqual(str(exc), "http_429")
        self.assertEqual((exc.status, exc.retry_hint), (429, RetryHint(120, "retry_after")))
        self.assertNotIn(MARKER, repr(exc) + str(exc.args))
        garbage = http_error(http(429, {"Retry-After": MARKER}))
        self.assertIsNone(garbage.retry_hint)
        derived = exc.with_code("linkedin_http_429_uncertain: rate limited")
        self.assertEqual((derived.status, derived.retry_hint), (429, exc.retry_hint))


# --------------------------------------------------------------------------- classification (D11)

class ClassificationTests(unittest.TestCase):
    def test_table_as_implemented(self):
        rate = PublishError("http_429", status=429)
        cases = [
            # (platform, backend, exc, write_started, expected)
            ("bluesky", None, rate, False, CERTAIN_NOT_SENT),      # createSession inside publish_bluesky
            ("bluesky", None, rate, True, LIKELY_NOT_CREATED),     # createRecord 429
            ("mastodon", None, rate, True, LIKELY_NOT_CREATED),
            ("telegram", None, rate, True, LIKELY_NOT_CREATED),
            ("linkedin", None, PublishError("linkedin_http_429_uncertain: rate limited", status=429), True,
             AMBIGUOUS),
            ("linkedin", None, ValidationError("linkedin_version_unsupported"), False, CERTAIN_NOT_SENT),
            ("mastodon", "postiz", PublishError("postiz_http_429_rate_limited", status=429), True, AMBIGUOUS),
            ("bluesky", None, PublishError("http_429"), True, LIKELY_NOT_CREATED),  # status from the code
            ("bluesky", None, PublishError("network_or_timeout_uncertain"), True, AMBIGUOUS),
            ("mastodon", None, PublishError("http_503", status=503), True, AMBIGUOUS),
            ("telegram", None, PublishError("http_401", status=401), True, AMBIGUOUS),
            ("linkedin", None, PublishError("linkedin_http_426_uncertain", status=426), True, AMBIGUOUS),
            ("bluesky", None, PublishError("bluesky_missing_post_uri_uncertain"), True, AMBIGUOUS),
        ]
        for platform, backend, exc, started, expected in cases:
            with self.subTest(platform=platform, backend=backend, code=str(exc), started=started):
                self.assertEqual(classify_send_failure(platform, backend, exc, write_started=started), expected)


# --------------------------------------------------------------------------- cooldown storage (D12)

class CooldownStorageTests(RateLimitBase):
    def test_cooldown_blocks_only_same_account_unclaimed_posts(self):
        a1 = self.approved()
        a2 = self.approved(text="Second note, same account")
        scheduled = self.approved(text="Scheduled same account", schedule="2026-10-08T11:00:00+00:00")
        other = self.approved("rl-bluesky-two", text="Other account note")
        with connect(self.db) as db:
            self.assertTrue(claim_sending(db, a1, now="2026-10-08T11:30:00+00:00"))  # claimed BEFORE the 429
            record_provider_cooldown(db, "rl-bluesky", "2026-10-08T12:30:00+00:00", "retry_after",
                                     stage="send", post_id=a1, send_class=LIKELY_NOT_CREATED, now=NOW_S)
            for pid in (a2, scheduled):
                with self.subTest(pid=pid), self.assertRaisesRegex(ValidationError, "account_provider_cooldown_active"):
                    claim_sending(db, pid, now=NOW_S, max_per_24h=10)
            self.assertEqual(get_post(db, a2)["status"], "approved")
            self.assertEqual(get_post(db, scheduled)["status"], "scheduled")
            self.assertEqual(get_post(db, a1)["status"], "sending")  # the claimed post is untouched
            self.assertFalse(claim_sending(db, a1, now=NOW_S))       # and never re-claimed
            self.assertTrue(claim_sending(db, other, now=NOW_S, max_per_24h=10))
            claims = [r[0] for r in db.execute("SELECT post_id FROM event_log WHERE action='send_claimed'")]
        self.assertEqual(claims, [a1, other])

    def test_cooldown_never_shortens_and_expires(self):
        with connect(self.db) as db:
            record_provider_cooldown(db, "rl-bluesky", "2026-10-08T13:00:00+00:00", "retry_after",
                                     stage="send", now=NOW_S)
            record_provider_cooldown(db, "rl-bluesky", "2026-10-08T12:05:00+00:00", "default",
                                     stage="preflight", ceiling_clamped=True, now=NOW_S)
            active = active_provider_cooldown(db, "rl-bluesky", NOW_S)
            self.assertEqual(active["until"], "2026-10-08T13:00:00+00:00")
            self.assertTrue(active["ceiling_clamped"])  # any clamped event keeps the attention flag
            self.assertIsNotNone(active_provider_cooldown(db, "rl-bluesky", "2026-10-08T12:59:59+00:00"))
            self.assertIsNone(active_provider_cooldown(db, "rl-bluesky", "2026-10-08T13:00:00+00:00"))
            self.assertIsNone(active_provider_cooldown(db, "rl-bluesky-two", NOW_S))
            # A malformed row (direct SQL) can never extend a cooldown.
            event(db, "provider_cooldown", None, "account=rl-bluesky until=never source=default", at=NOW_S)
            self.assertEqual(active_provider_cooldown(db, "rl-bluesky", NOW_S)["until"], "2026-10-08T13:00:00+00:00")

    def test_cooldown_survives_restart_and_purge_keeps_recent_cooldowns(self):
        now = datetime.now(timezone.utc)
        recent_until = (now + timedelta(hours=1)).isoformat(timespec="seconds")
        with connect(self.db) as db:
            record_provider_cooldown(db, "rl-bluesky", recent_until, "retry_after", stage="send",
                                     now=(now - timedelta(minutes=5)).isoformat(timespec="seconds"))
            old = (now - timedelta(days=3)).isoformat(timespec="seconds")
            record_provider_cooldown(db, "rl-mastodon", (now - timedelta(days=3) + timedelta(hours=1))
                                     .isoformat(timespec="seconds"), "default", stage="send", now=old)
        with connect(self.db) as again:  # a restart: the deadline is durable
            self.assertEqual(active_provider_cooldown(again, "rl-bluesky")["until"], recent_until)
            plan = plan_purge(again, {"event_log_days": 1})
            self.assertEqual(plan["protected"]["recent_provider_cooldown_events"], 1)
            tampered = json.loads(json.dumps(plan))
            tampered["actions"]["event_log_delete"] = [r[0] for r in again.execute("SELECT id FROM event_log")]
            apply_purge(again, tampered)  # even a tampered plan cannot delete a recent cooldown
            left = [r[0] for r in again.execute("SELECT detail FROM event_log WHERE action='provider_cooldown'")]
            self.assertEqual(len(left), 1)
            self.assertIn("account=rl-bluesky", left[0])
            self.assertEqual(active_provider_cooldown(again, "rl-bluesky")["until"], recent_until)

    def test_pause_and_approval_hash_checked_before_cooldown(self):
        pid = self.approved()
        with connect(self.db) as db:
            record_provider_cooldown(db, "rl-bluesky", "2026-10-08T13:00:00+00:00", "default",
                                     stage="send", now=NOW_S)
            set_paused(db, True)
            with self.assertRaisesRegex(ValidationError, "emergency pause"):
                claim_sending(db, pid, now=NOW_S)
            set_paused(db, False)
            db.execute("UPDATE posts SET content='tampered' WHERE id=?", (pid,))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                claim_sending(db, pid, now=NOW_S)
            db.execute("UPDATE posts SET content='Evidence-backed release note', last_error=NULL WHERE id=?", (pid,))
            with self.assertRaisesRegex(ValidationError, "account_provider_cooldown_active until=2026-10-08T13:00"):
                claim_sending(db, pid, now=NOW_S)
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(db.execute("SELECT count(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 0)
        # CLI order: a tampered post is BLOCKED by the binding gate, not SKIPPED by the cooldown.
        with connect(self.db) as db:
            db.execute("UPDATE posts SET content='tampered again' WHERE id=?", (pid,))
            record_provider_cooldown(db, "rl-bluesky", (datetime.now(timezone.utc) + timedelta(hours=1))
                                     .isoformat(timespec="seconds"), "default", stage="send")
        wire = Wire({})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 2)
        self.assertIn("approval_hash_mismatch", out)
        self.assertNotIn("SKIPPED", out)
        self.assertEqual(wire.calls, [])

    def test_record_and_uncertain_send_validate_codes(self):
        with connect(self.db) as db:
            for kwargs in ({"account_id": "has space"}, {"source": "free text"}, {"source": "unknown_src"},
                           {"stage": "later"}, {"send_class": "maybe"}):
                args = {"account_id": "rl-bluesky", "until": NOW_S, "source": "default", "stage": "send", **kwargs}
                with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                    record_provider_cooldown(db, args.pop("account_id"), args.pop("until"), args.pop("source"),
                                             **args)
            pid = self.approved()
            claim_sending(db, pid)
            with self.assertRaises(ValidationError):
                uncertain_send(db, pid, "http_500", send_class="probably_fine")
            self.assertEqual(get_post(db, pid)["status"], "sending")


# --------------------------------------------------------------------------- CLI send paths (D12, D16)

class PostClaimRateLimitTests(RateLimitBase):
    def test_post_claim_429_records_cooldown_and_keeps_needs_review(self):
        first = self.approved(text="First note, will hit the limit")
        second = self.approved(text="Second note, same account")
        other = self.approved("rl-bluesky-two", text="Other account note")
        wire = Wire({("POST", "createSession"): [SESSION, SESSION, SESSION, SESSION],
                     ("POST", "repo.createRecord"): [lambda: http(429, {"Retry-After": "600"}),
                                                     {"uri": f"at://{DID}/app.bsky.feed.post/__RKEY__", "cid": CID}]})
        before = datetime.now(timezone.utc).replace(microsecond=0)
        code, out, _ = self.live(wire, "--post-id", first)
        self.assertEqual(code, 3, out)
        self.assertIn(f"REVIEW {first}: http_429 [likely_not_created]", out)
        self.assertIn("never resent automatically", out)
        row = self.row(first)
        self.assertEqual((row["status"], row["last_error"]), ("needs_review", "http_429"))
        self.assertTrue(self.events("needs_review", first)[0]["detail"].endswith("class=likely_not_created"))
        [cooldown] = self.cooldowns()
        self.assertEqual((cooldown["account_id"], cooldown["source"], cooldown["stage"], cooldown["post_id"]),
                         ("rl-bluesky", "retry_after", "send", first))
        until = datetime.fromisoformat(cooldown["until"])
        self.assertTrue(before + timedelta(seconds=599) <= until <= before + timedelta(seconds=605), until)
        self.assertEqual(wire.count("POST", "repo.createRecord"), 1)
        # The next run: the 429'd post is not due, the same-account post is SKIPPED with no request.
        calls = len(wire.calls)
        with NetworkGuard() as guard:
            code, out, _ = self.live(wire, "--post-id", second)
        self.assertEqual(code, 2)
        self.assertIn(f"SKIPPED {second}: account_provider_cooldown_active", out)
        self.assertIn("post stays approved", out)
        self.assertEqual((len(wire.calls), guard.attempts), (calls, []))
        self.assertEqual(self.row(second)["status"], "approved")
        code, _, _ = self.live(wire, "--post-id", first)
        self.assertEqual(code, 2)  # never auto-retried: not eligible
        self.assertEqual(wire.count("POST", "repo.createRecord"), 1)
        # Another account is unaffected (its own createRecord succeeds).
        rkey_reply = wire.routes[("POST", "repo.createRecord")]
        from reachweaver.richtext import deterministic_tid
        rkey = deterministic_tid(other, self.row(other)["created_at"])
        rkey_reply[0] = {"uri": f"at://{DID}/app.bsky.feed.post/{rkey}", "cid": CID}
        code, out, _ = self.live(wire, "--post-id", other)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.row(other)["status"], "published")
        self.assertEqual(self.sleeps, [])

    def test_bluesky_session_429_after_claim_is_certain_not_sent(self):
        pid = self.approved()
        wire = Wire({("POST", "createSession"): [SESSION, lambda: http(429)],
                     ("POST", "repo.createRecord"): []})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 3, out)
        self.assertIn("[certain_not_sent]", out)
        self.assertEqual(self.row(pid)["status"], "needs_review")  # state is unchanged by the label
        self.assertEqual(wire.count("POST", "repo.createRecord"), 0)
        [cooldown] = self.cooldowns()
        self.assertEqual((cooldown["source"], cooldown["send_class"]), ("default", CERTAIN_NOT_SENT))
        letters = dead_letter(self.connect_once())
        post = letters["groups"][0]["posts"][0]
        self.assertEqual(post["send_class"], CERTAIN_NOT_SENT)
        self.assertEqual(post["class_guidance"], CLASS_GUIDANCE[CERTAIN_NOT_SENT])

    def connect_once(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        return stack.enter_context(connect(self.db))

    def test_telegram_retry_after_body_bounded_and_not_logged(self):
        body = json.dumps({"ok": False, "error_code": 429, "description": f"Too Many Requests {MARKER}",
                           "parameters": {"retry_after": 1234}}).encode()
        first = self.approved("rl-telegram", text="Channel note one")
        wire = Wire({**TG_PREFLIGHT, ("POST", "/sendMessage"): [lambda: http(429, body=body)]})
        code, out, err = self.live(wire, "--post-id", first)
        self.assertEqual(code, 3, out)
        [cooldown] = self.cooldowns()
        self.assertEqual((cooldown["source"], cooldown["send_class"]), ("telegram_retry_after", LIKELY_NOT_CREATED))
        with connect(self.db) as db:
            log = " ".join(r[0] for r in db.execute("SELECT detail FROM event_log"))
            sent = (datetime.fromisoformat(cooldown["until"]) - datetime.fromisoformat(cooldown["recorded_at"]))
        self.assertEqual(sent, timedelta(seconds=1234))
        for text in (out, err, log, self.row(first)["last_error"]):
            self.assertNotIn(MARKER, text)
            self.assertNotIn(TG_TOKEN, text)
        # A body above 4 KB is ignored as a whole: the default applies.
        with connect(self.db) as db:
            db.execute("DELETE FROM event_log WHERE action IN ('provider_cooldown','send_claimed')")
            db.execute("UPDATE posts SET status='cancelled' WHERE id=?", (first,))  # #41: the unresolved first send holds the account
        second = self.approved("rl-telegram", text="Channel note two")
        big = json.dumps({"parameters": {"retry_after": 999}, "pad": "x" * 5000}).encode()
        wire = Wire({**TG_PREFLIGHT, ("POST", "/sendMessage"): [lambda: http(429, body=big)]})
        self.assertEqual(self.live(wire, "--post-id", second)[0], 3)
        self.assertEqual(self.cooldowns()[0]["source"], "default")

    def test_mastodon_post_claim_429_uses_x_ratelimit_reset(self):
        pid = self.approved("rl-mastodon", text="Fediverse note")
        reset = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="milliseconds")
        wire = Wire({("GET", "verify_credentials"): [MASTO_ME],
                     ("POST", "/api/v1/statuses"): [lambda: http(429, {"X-RateLimit-Reset": reset})]})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 3, out)
        [cooldown] = self.cooldowns()
        self.assertEqual((cooldown["source"], cooldown["send_class"]), ("x_ratelimit_reset", LIKELY_NOT_CREATED))
        self.assertEqual(wire.count("POST", "/api/v1/statuses"), 1)

    def test_linkedin_post_claim_429_is_ambiguous_with_midnight_cooldown(self):
        pid = self.approved("rl-linkedin", text="LinkedIn release note")
        nxt = self.approved("rl-linkedin", text="LinkedIn second note")
        wire = Wire({("GET", "/v2/userinfo"): [LI_ME, LI_ME], ("POST", "/rest/posts"): [lambda: http(429)]})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 3, out)
        self.assertIn("linkedin_http_429_uncertain", out)
        self.assertIn("[ambiguous]", out)
        self.assertEqual(self.row(pid)["status"], "needs_review")
        [cooldown] = self.cooldowns()
        midnight = ratelimit.next_utc_midnight(datetime.now(timezone.utc))
        self.assertEqual((cooldown["source"], cooldown["send_class"]), ("daily_reset", AMBIGUOUS))
        self.assertLessEqual(abs((datetime.fromisoformat(cooldown["until"]) - midnight).total_seconds()), 61)
        with NetworkGuard() as guard:
            code, out, _ = self.live(wire, "--post-id", nxt)
        self.assertEqual((code, guard.attempts), (2, []))
        self.assertIn("SKIPPED", out)
        self.assertEqual(wire.count("POST", "/rest/posts"), 1)
        self.assertEqual(wire.count("GET", "/v2/userinfo"), 1)

    def test_postiz_post_claim_429_cooldown_and_preflight_429(self):
        pid = self.approved("rl-postiz", text="Backend note")
        nxt = self.approved("rl-postiz", text="Backend second note")
        wire = Wire({("GET", "/integrations"): [PZ_INTEGRATIONS],
                     ("POST", "/public/v1/posts"): [lambda: http(429, {"Retry-After": "900"}, MARKER.encode())]})
        code, out, err = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 3, out)
        self.assertIn("[ambiguous]", out)
        row = self.row(pid)
        self.assertEqual((row["status"], row["last_error"]), ("needs_review", "postiz:postiz_http_429_rate_limited"))
        [cooldown] = self.cooldowns()
        self.assertEqual((cooldown["account_id"], cooldown["source"], cooldown["send_class"]),
                         ("rl-postiz", "retry_after", AMBIGUOUS))
        self.assertNotIn(MARKER, out + err)
        with NetworkGuard() as guard:
            code, out, _ = self.live(wire, "--post-id", nxt)
        self.assertEqual((code, guard.attempts), (2, []))
        self.assertIn(f"SKIPPED {nxt}: account_provider_cooldown_active", out)
        self.assertEqual(wire.count("POST", "/public/v1/posts"), 1)
        # Preflight 429 (GET /integrations): two jittered retries, then a cooldown and BLOCKED.
        with connect(self.db) as db:
            db.execute("DELETE FROM event_log WHERE action='provider_cooldown'")
        wire = Wire({("GET", "/integrations"): [lambda: http(429)] * 3, ("POST", "/public/v1/posts"): []})
        code, out, _ = self.live(wire, "--post-id", nxt)
        self.assertEqual(code, 2, out)
        self.assertIn("postiz_http_429_rate_limited", out)
        self.assertIn("account_provider_cooldown_active", out)
        self.assertEqual(wire.count("GET", "/integrations"), 3)
        self.assertEqual(len(self.sleeps), 2)
        self.assertEqual(self.row(nxt)["status"], "approved")
        self.assertEqual(self.cooldowns()[0]["stage"], "preflight")

    def test_ambiguous_codes_never_retried(self):
        """Timeout, 5xx, 401/403/426 on create, malformed receipt, unexpected exception: one create
        request, needs_review, no cooldown, and a second run sends nothing, for every adapter."""
        receipts = {"bluesky": {"uri": "at://wrong", "cid": CID}, "mastodon": {"id": None},
                    "telegram": {"ok": False}, "linkedin": Resp(status=201, headers={}, raw=b""),
                    "postiz": {"weird": 1}}
        failures = {"timeout": lambda: TimeoutError(), "http_500": lambda: http(500),
                    "http_502": lambda: http(502), "http_503": lambda: http(503, {"Retry-After": "5"}),
                    "http_401": lambda: http(401), "http_403": lambda: http(403), "http_426": lambda: http(426),
                    "unexpected": lambda: RuntimeError(MARKER)}
        for adapter, account in ACCOUNT_OF.items():
            for name, failure in [*failures.items(), ("malformed_receipt", receipts[adapter])]:
                with self.subTest(adapter=adapter, failure=name):
                    with connect(self.db) as db:
                        db.execute("DELETE FROM event_log WHERE action IN ('send_claimed','bluesky_create_session')")
                        # #41: the previous iteration left a needs_review post, which holds the account
                        db.execute("UPDATE posts SET status='cancelled' WHERE status='needs_review'")
                    pid = self.approved(account, text=f"Note for {adapter} {name}")
                    wire = Wire(ok_routes(adapter, [failure]))
                    code, out, err = self.live(wire, "--post-id", pid)
                    self.assertEqual(code, 3, out + err)
                    self.assertEqual(self.row(pid)["status"], "needs_review")
                    self.assertEqual(wire.count(*create_route(adapter)), 1)
                    detail = self.events("needs_review", pid)[-1]["detail"]
                    self.assertTrue(detail.endswith("class=ambiguous"), detail)
                    self.assertEqual(self.cooldowns(), [])
                    code, _, _ = self.live(wire, "--post-id", pid)  # never resent: not eligible
                    self.assertEqual(code, 2)
                    code, _, _ = self.live(wire)  # nor by a full worker sweep
                    self.assertEqual(wire.count(*create_route(adapter)), 1)
                    self.assertNotIn(MARKER, out + err)
        self.assertEqual(self.sleeps, [])  # nothing after a claim ever backs off and retries


# --------------------------------------------------------------------------- preflight backoff (D13)

class PreflightBackoffTests(RateLimitBase):
    def test_preflight_get_backoff_bounded_with_seeded_jitter(self):
        calls, slept = [], []

        def failing():
            calls.append(1)
            raise PublishError("http_503", status=503)
        with self.assertRaisesRegex(PublishError, "http_503"):
            preflight_with_backoff(failing, max_retries=2, paused=lambda: False, retry_on=PublishError,
                                   rng=random.Random(7), sleep=slept.append)
        expected = random.Random(7)
        self.assertEqual(slept, [expected.uniform(0, 2.0), expected.uniform(0, 4.0)])
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(0 <= s <= 30 for s in slept))
        self.assertEqual(ratelimit.backoff_delay(10, random.Random(1)) <= 30, True)  # capped at 30 s
        answers = iter([PublishError("network_or_timeout_uncertain"), "verified"])

        def flaky():
            value = next(answers)
            if isinstance(value, Exception):
                raise value
            return value
        self.assertEqual(preflight_with_backoff(flaky, max_retries=2, paused=lambda: False,
                                                retry_on=PublishError, sleep=slept.append), "verified")
        for code in ("http_401", "http_403", "http_404", "identity_mismatch"):
            once = []

            def permanent(code=code):
                once.append(1)
                raise PublishError(code)
            with self.subTest(code=code), self.assertRaises(PublishError):
                preflight_with_backoff(permanent, max_retries=2, paused=lambda: False, retry_on=PublishError,
                                       sleep=slept.append)
            self.assertEqual(once, [1])
        # A short server delay is respected as a minimum.
        waits = []
        hinted = iter([PublishError("http_429", status=429, retry_hint=RetryHint(10, "retry_after")), "ok"])

        def short():
            value = next(hinted)
            if isinstance(value, Exception):
                raise value
            return value
        preflight_with_backoff(short, max_retries=2, paused=lambda: False, retry_on=PublishError,
                               rng=random.Random(3), sleep=waits.append)
        self.assertGreaterEqual(waits[0], 10)
        for bad in (-1, 3, True):
            with self.subTest(retries=bad), self.assertRaises(ValidationError):
                preflight_with_backoff(lambda: None, max_retries=bad, paused=lambda: False, retry_on=PublishError)

    def test_preflight_backoff_rechecks_pause(self):
        state = {"paused": False}
        calls = []

        def failing():
            calls.append(1)
            raise PublishError("http_502", status=502)

        def operator_pauses_during_sleep(_seconds):
            state["paused"] = True
        with self.assertRaisesRegex(ValidationError, "pause"):
            preflight_with_backoff(failing, max_retries=2, paused=lambda: state["paused"], retry_on=PublishError,
                                   sleep=operator_pauses_during_sleep)
        self.assertEqual(calls, [1])
        slept = []
        with self.assertRaisesRegex(ValidationError, "pause"):  # already paused: no sleep at all
            preflight_with_backoff(failing, max_retries=2, paused=lambda: True, retry_on=PublishError,
                                   sleep=slept.append)
        self.assertEqual(slept, [])

    def test_cli_mastodon_preflight_retries_then_sends_once(self):
        pid = self.approved("rl-mastodon", text="Retry the read, never the write")
        wire = Wire({("GET", "verify_credentials"): [lambda: http(503), lambda: http(502), MASTO_ME],
                     ("POST", "/api/v1/statuses"): [{"id": "9", "url": "https://mastodon.example.org/@owner/9",
                                                     "account": {"username": "owner"}, "visibility": "public"}]})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 0, out)
        self.assertEqual(wire.count("GET", "verify_credentials"), 3)
        self.assertEqual(wire.count("POST", "/api/v1/statuses"), 1)
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(all(0 <= s <= 30 for s in self.sleeps))
        self.assertEqual(self.row(pid)["status"], "published")

    def test_cli_caps_v2_can_disable_preflight_retries(self):
        caps = self.base / "caps.json"
        caps.write_text(json.dumps({"version": 2, "providers": {"telegram": {"preflight_max_retries": 0}}}))
        pid = self.approved("rl-telegram", text="No retries for this provider")
        wire = Wire({("GET", "/getMe"): [lambda: http(503)], ("POST", "/sendMessage"): []})
        code, out, _ = self.live_with_caps(wire, caps, "--post-id", pid)
        self.assertEqual(code, 2, out)
        self.assertEqual((wire.count("GET", "/getMe"), self.sleeps), (1, []))
        self.assertEqual(self.row(pid)["status"], "approved")

    def live_with_caps(self, wire, caps, *argv):
        with patch("reachweaver.publishers._send_http", wire):
            return self.cli("publish", "--live", *argv, env={**ENV, "REACHWEAVER_CAPS": str(caps)})

    def test_preflight_long_retry_after_records_cooldown_without_sleeping(self):
        pid = self.approved("rl-mastodon", text="Long server delay")
        wire = Wire({("GET", "verify_credentials"): [lambda: http(429, {"Retry-After": "3600"})],
                     ("POST", "/api/v1/statuses"): []})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 2, out)
        self.assertIn("BLOCKED", out)
        self.assertIn("account_provider_cooldown_active", out)
        self.assertEqual((wire.count("GET", "verify_credentials"), self.sleeps), (1, []))
        [cooldown] = self.cooldowns()
        self.assertEqual((cooldown["source"], cooldown["stage"], cooldown["post_id"]), ("retry_after", "preflight", None))
        span = datetime.fromisoformat(cooldown["until"]) - datetime.fromisoformat(cooldown["recorded_at"])
        self.assertEqual(span, timedelta(seconds=3600))
        row = self.row(pid)
        self.assertEqual(row["status"], "approved")
        self.assertEqual(self.events("send_claimed"), [])

    def test_bluesky_createsession_not_auto_retried_by_default(self):
        self.assertEqual(preflight_retries("bluesky", 2), 0)
        self.assertEqual(preflight_retries("mastodon", 2), 2)
        self.assertEqual(preflight_retries("postiz", 5), 2)
        pid = self.approved(text="Bluesky session check")
        wire = Wire({("POST", "createSession"): [lambda: http(503)], ("POST", "repo.createRecord"): []})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 2, out)
        self.assertEqual((wire.count("POST", "createSession"), self.sleeps), (1, []))
        wire = Wire({("POST", "createSession"): [lambda: http(429)], ("POST", "repo.createRecord"): []})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 2, out)
        self.assertEqual((wire.count("POST", "createSession"), self.sleeps), (1, []))
        self.assertEqual(self.cooldowns()[0]["stage"], "preflight")
        self.assertEqual(self.row(pid)["status"], "approved")


# --------------------------------------------------------------------------- caps v2 (D14)

class CapsV2Tests(unittest.TestCase):
    def test_caps_v2_providers_tighten_only(self):
        caps = validate_caps({"version": 2, "defaults": {"max_per_24h": 2},
                              "providers": {"linkedin": {"max_per_24h": 1, "cooldown_floor_seconds": 3600},
                                            "telegram": {"preflight_max_retries": 0, "min_gap_minutes": 120},
                                            "postiz": {"cooldown_floor_seconds": 600, "preflight_max_retries": 1},
                                            "mastodon": {"max_per_24h": 10, "min_gap_minutes": 15,
                                                         "cooldown_floor_seconds": 60, "preflight_max_retries": 2}},
                              "accounts": {"li": {"min_gap_minutes": 240}}})
        self.assertEqual(effective_caps(caps, "li", 2, 60, providers=("linkedin",)), (1, 240))
        self.assertEqual(effective_caps(caps, "tg", 2, 60, providers=("telegram",)), (2, 120))
        self.assertEqual(effective_caps(caps, "m", 2, 60, providers=("mastodon",)), (2, 60))  # looser ignored
        self.assertEqual(effective_caps(caps, "li", 2, 60), (2, 240))  # no providers given: v1 behaviour
        self.assertEqual(effective_rate_limits(caps, ("linkedin",)),
                         {"cooldown_floor_seconds": 3600, "preflight_max_retries": 2})
        self.assertEqual(effective_rate_limits(caps, ("telegram",)),
                         {"cooldown_floor_seconds": 60, "preflight_max_retries": 0})
        self.assertEqual(effective_rate_limits(caps, ("mastodon",)),
                         {"cooldown_floor_seconds": 60, "preflight_max_retries": 2})
        self.assertEqual(effective_rate_limits(caps, ("postiz", "mastodon")),
                         {"cooldown_floor_seconds": 600, "preflight_max_retries": 1})
        self.assertEqual(effective_rate_limits(None, ("linkedin",)),
                         {"cooldown_floor_seconds": 60, "preflight_max_retries": 2})
        self.assertEqual(providers_for({"platform": "mastodon", "backend": "postiz"}), ("postiz", "mastodon"))
        self.assertEqual(providers_for({"platform": "linkedin"}), ("linkedin",))
        self.assertEqual(providers_for({"platform": "x"}), ())

    def test_caps_v2_malformed_fails_closed(self):
        for raw in ({"version": 3}, {"version": "2"}, {"version": True},
                    {"version": 1, "providers": {}},  # providers needs version 2
                    {"version": 2, "providers": []}, {"version": 2, "providers": {"x": {}}},
                    {"version": 2, "providers": {"bluesky": {"burst": 1}}},
                    {"version": 2, "providers": {"bluesky": {"cooldown_floor_seconds": 59}}},
                    {"version": 2, "providers": {"bluesky": {"cooldown_floor_seconds": 86401}}},
                    {"version": 2, "providers": {"bluesky": {"preflight_max_retries": 3}}},
                    {"version": 2, "providers": {"bluesky": {"preflight_max_retries": -1}}},
                    {"version": 2, "providers": {"bluesky": {"preflight_max_retries": True}}},
                    {"version": 2, "providers": {"bluesky": {"max_per_24h": 0}}},
                    {"version": 2, "accounts": {"a": {"cooldown_floor_seconds": 600}}},  # provider-only key
                    {"version": 2, "defaults": {"preflight_max_retries": 0}},
                    {"version": 2, "extra": {}}):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                validate_caps(raw)

    def test_caps_v1_still_valid(self):
        caps = validate_caps({"version": 1, "defaults": {"max_per_24h": 1},
                              "accounts": {"a": {"min_gap_minutes": 240}}})
        self.assertEqual((caps["version"], caps["providers"]), (1, {}))
        self.assertEqual(effective_caps(caps, "a", 2, 60), (1, 240))
        self.assertEqual(effective_caps(caps, "a", 2, 60, providers=("bluesky",)), (1, 240))
        self.assertEqual(effective_rate_limits(caps, ("bluesky",)),
                         {"cooldown_floor_seconds": 60, "preflight_max_retries": 2})
        example, _ = load_caps(Path(__file__).resolve().parents[1] / "config/caps.example.json")
        self.assertEqual(example["version"], 2)
        self.assertIn("linkedin", example["providers"])


class CapsV2CliTests(RateLimitBase):
    def test_cooldown_floor_from_caps_lengthens_post_claim_cooldown(self):
        caps = self.base / "caps.json"
        caps.write_text(json.dumps({"version": 2, "providers": {"bluesky": {"cooldown_floor_seconds": 7200}}}))
        pid = self.approved()
        wire = Wire({("POST", "createSession"): [SESSION, SESSION],
                     ("POST", "repo.createRecord"): [lambda: http(429, {"Retry-After": "30"})]})
        with patch("reachweaver.publishers._send_http", wire):
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env={**ENV, "REACHWEAVER_CAPS": str(caps)})
        self.assertEqual(code, 3, out)
        [cooldown] = self.cooldowns()
        span = datetime.fromisoformat(cooldown["until"]) - datetime.fromisoformat(cooldown["recorded_at"])
        self.assertEqual(span, timedelta(seconds=7200))


# --------------------------------------------------------------------------- visibility (no network)

class CooldownVisibilityTests(RateLimitBase):
    def seed(self, clamped=False):
        until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
        with connect(self.db) as db:
            record_provider_cooldown(db, "rl-mastodon", until, "retry_after", stage="send",
                                     send_class=LIKELY_NOT_CREATED, ceiling_clamped=clamped)
        return until

    def test_health_shows_cooldowns(self):
        until = self.seed(clamped=True)
        self.approved("rl-mastodon", text="Waiting post")
        from reachweaver.accounts import load_accounts
        with NetworkGuard() as guard, connect(self.db) as db:
            report = build_health(db, load_accounts(self.config), env=ENV)
            text = render_health(report)
        self.assertEqual(guard.attempts, [])
        self.assertEqual(report["overall"], "attention")
        self.assertEqual(exit_code(report), 0)
        row = next(r for r in report["accounts"] if r["account_id"] == "rl-mastodon")
        self.assertEqual(row["provider_cooldown"]["until"], until)
        codes = {r["code"] for r in row["reasons"]}
        self.assertTrue({"provider_cooldown_active", "provider_cooldown_ceiling_clamped"} <= codes)
        self.assertEqual(row["rate_limits"]["cooldown_floor_seconds"], 60)
        self.assertEqual([c["account_id"] for c in report["provider_cooldowns"]], ["rl-mastodon"])
        self.assertIn(f"Provider cooldown: rl-mastodon until {until}", text)
        other = next(r for r in report["accounts"] if r["account_id"] == "rl-bluesky")
        self.assertIsNone(other["provider_cooldown"])
        self.assertIn("never retried", other["rate_limits"]["preflight_retries_note"])

    def test_cli_health_queue_health_and_dry_run_show_cooldowns_offline(self):
        until = self.seed()
        pid = self.approved("rl-mastodon", text="Waiting post")
        offline = {k: v for k, v in ENV.items()
                   if k not in ("REACHWEAVER_ALLOW_LIVE", "REACHWEAVER_ENV")}  # names present, no opt-in
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("health", env=offline)
            self.assertEqual(code, 0, out)  # a cooldown is attention, never blocked
            self.assertIn("provider_cooldown_active", out)
            code, out, _ = self.cli("queue-health", env=offline)
            data = json.loads(out)
            self.assertTrue(data["attention_required"])
            self.assertEqual(data["provider_cooldowns"][0]["until"], until)
            code, out, _ = self.cli("publish", env=offline)
            self.assertEqual(code, 0)
            self.assertIn(f"Provider cooldown: account rl-mastodon until {until}", out)
            self.assertIn(f"SKIPPED {pid}: account_provider_cooldown_active", out)
            code, out, _ = self.cli("health", "--json", env=offline)
            self.assertEqual(json.loads(out)["provider_cooldowns"][0]["account_id"], "rl-mastodon")
        self.assertEqual(guard.attempts, [])
        network.assert_not_called()
        self.assertEqual(self.row(pid)["status"], "approved")

    def test_cooldown_skip_is_pre_claim_and_network_free(self):
        self.seed()
        pid = self.approved("rl-mastodon", text="Waiting post", schedule="2026-01-01T00:00:00+00:00")
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http") as network, \
                patch("reachweaver.cli.check_connection") as identity:
            code, out, _ = self.cli("publish", "--live")
            self.assertEqual(code, 0)
            code_one, _, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code_one, 2)
        self.assertIn(f"SKIPPED {pid}: account_provider_cooldown_active", out)
        self.assertIn("post stays scheduled", out)
        self.assertEqual(guard.attempts, [])
        network.assert_not_called()
        identity.assert_not_called()
        self.assertEqual(self.row(pid)["status"], "scheduled")
        self.assertEqual(self.events("send_claimed"), [])

    def test_dead_letter_reports_classes_and_unknown_for_legacy(self):
        with connect(self.db) as db:
            legacy = self.approved(text="Legacy failure")
            claim_sending(db, legacy)
            uncertain_send(db, legacy, "http_429")  # no class: recorded before #11
            fresh = self.approved("rl-bluesky-two", text="Classified failure")
            claim_sending(db, fresh)
            uncertain_send(db, fresh, "http_429", send_class=LIKELY_NOT_CREATED)
            with NetworkGuard() as guard:
                letters = dead_letter(db)
                queue = queue_health(db)
        self.assertEqual(guard.attempts, [])
        [group] = letters["groups"]
        self.assertEqual(group["send_classes"], {"unknown": 1, LIKELY_NOT_CREATED: 1})
        by_id = {p["post_id"]: p["send_class"] for p in group["posts"]}
        self.assertEqual(by_id, {legacy: "unknown", fresh: LIKELY_NOT_CREATED})
        self.assertIn("cooldown", group["suggested_action"])
        self.assertNotIn("probably not created", group["suggested_action"])
        self.assertIn("not an API guarantee", letters["send_class_meaning"][LIKELY_NOT_CREATED])
        self.assertEqual(queue["provider_cooldowns"], [])
        code, out, _ = self.cli("dead-letter", env={})
        self.assertIn("[likely_not_created]", out)
        self.assertIn("Send classification", out)


if __name__ == "__main__":
    unittest.main()
