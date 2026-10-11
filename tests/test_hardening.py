"""Issue #5 hardening, owner-approved 2026-10-09. Every test here is offline.

1. ``load_accounts`` refuses identical or overlapping ``env_prefix`` values, over every
   adapter suffix and the Postiz backend suffix.
2. ``publish --live`` also needs ``REACHWEAVER_ENV=prod``. The gate runs before any
   account is loaded, any credential is read or any request is made, for native adapters
   and the Postiz backend alike. Unrecognized ``REACHWEAVER_ENV`` values are never echoed.
3. systemd ``LoadCredential=``: ``accounts.credential`` reads ``$CREDENTIALS_DIRECTORY/<NAME>``
   (safe directory, regular file, O_NOFOLLOW, at most 64 KiB, UTF-8, one trailing newline
   stripped) and never consults the environment while the directory is set. Every credential
   read in ``reachweaver/`` goes through that accessor, and file values are redacted exactly
   like secret environment variables.

Socket entry points are refused by the shared ``NetworkGuard``; HTTP is mocked at
``publishers._send_http``. No test passes ``--live`` with a real transport.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_no_network import NetworkGuard

from reachweaver import accounts as accounts_module
from reachweaver import linkedin, postiz, publishers
from reachweaver.accounts import (
    ALL_CREDENTIAL_SUFFIXES, BACKEND_CREDENTIAL_SUFFIX, MAX_CREDENTIAL_BYTES, CredentialError,
    CredentialsDirectoryError, credential, credential_source, env_prefix_conflicts, load_accounts,
    missing_credentials, require_credential,
)
from reachweaver.cli import main
from reachweaver.connections import VerifiedConnection, check_connection
from reachweaver.credential_hygiene import audit_environment, render_audit
from reachweaver.health import build_health, render_health
from reachweaver.policy import ValidationError, live_opt_in, require_live_gate, runtime_environment
from reachweaver.privacy import redact
from reachweaver.storage import add_post, connect, get_post, transition

ROOT = Path(__file__).resolve().parents[1]
POSIX = os.name == "posix" and hasattr(os, "mkfifo") and os.open in os.supports_dir_fd
# Built at runtime so the tracked-file secret scan never sees a credential-shaped literal. Never real.
FILE_PASSWORD = "-".join(("hardening", "file", "app", "pw", "51c0"))
FILE_TOKEN = "-".join(("hardening", "file", "token", "8e21"))
ENV_TOKEN = "-".join(("hardening", "env", "token", "0b9d"))
TG_TOKEN = ":".join(("123456", "AAhardeningfiletoken_x"))
LI_TOKEN = "-".join(("hardening", "li", "token", "77f3"))
PZ_KEY = "-".join(("hardening", "pz", "key", "c4a8"))
GARBAGE_ENV = "pr0d$(touch-x)"  # an unrecognized REACHWEAVER_ENV value that must never be echoed
DID = "did:plc:hardening123"
CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"


def prod_env(**extra):
    """The three live opt-ins' env half, plus extra variables. Written once, used by the positive controls."""
    return {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", **extra}


def write_accounts(path: Path, rows: list[dict]) -> Path:
    path.write_text(json.dumps({"accounts": rows}), encoding="utf-8")
    return path


def bsky(account_id, prefix, **extra):
    return {"id": account_id, "platform": "bluesky", "env_prefix": prefix, "handle": "owner.bsky.social", **extra}


def masto(account_id, prefix, **extra):
    return {"id": account_id, "platform": "mastodon", "env_prefix": prefix,
            "server": "https://mastodon.example.org", "handle": "owner", **extra}


def tg(account_id, prefix, **extra):
    return {"id": account_id, "platform": "telegram", "env_prefix": prefix, "chat_id": "@examplechannel",
            "channel_username": "examplechannel", "bot_username": "example_bot", **extra}


def li(account_id, prefix, **extra):
    return {"id": account_id, "platform": "linkedin", "env_prefix": prefix, "person_id": "782bbtaQ",
            "linkedin_version": "202609", "authorization_expires_at": "2026-12-01T00:00:00+00:00", **extra}


def pz(account_id, prefix, **extra):
    return {"id": account_id, "platform": "bluesky", "env_prefix": prefix, "handle": "owner.bsky.social",
            "backend": "postiz", "postiz_base_url": "https://api.postiz.com",
            "postiz_integration_id": "int-hb-1", **extra}


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        # File-read values are kept in-process for redaction; do not leak them into other tests.
        self.addCleanup(accounts_module._LOADED_FILE_CREDENTIALS.clear)


# --------------------------------------------------------------------------- 1. env_prefix overlap

class EnvPrefixOverlapTests(TempDirTest):
    def load(self, *rows):
        return load_accounts(write_accounts(self.base / "accounts.json", list(rows)))

    def test_identical_prefix_rejected_naming_accounts_only(self):
        with self.assertRaises(ValidationError) as caught:
            self.load(bsky("a-one", "REACHWEAVER_SAME"), masto("b-two", "REACHWEAVER_SAME"))
        message = str(caught.exception)
        self.assertIn("a-one", message)
        self.assertIn("b-two", message)
        self.assertIn("identical env_prefix", message)

    def test_suffix_collision_rejected(self):
        # Telegram prefix X reads X_BOT_TOKEN; Mastodon prefix X_BOT reads X_BOT + _TOKEN: the same variable.
        with self.assertRaises(ValidationError) as caught:
            self.load(tg("tg-x", "REACHWEAVER_X"), masto("masto-x", "REACHWEAVER_X_BOT"))
        self.assertIn("REACHWEAVER_X_BOT_TOKEN", str(caught.exception))
        [conflict] = env_prefix_conflicts([tg("tg-x", "REACHWEAVER_X"), masto("masto-x", "REACHWEAVER_X_BOT")])
        self.assertEqual(conflict["reason"], "overlapping_env_prefix")
        self.assertEqual(conflict["accounts"], ["masto-x", "tg-x"])
        self.assertIn("REACHWEAVER_X_BOT_TOKEN", conflict["variables"])

    def test_collision_counted_over_every_suffix_regardless_of_platform(self):
        # Two Bluesky accounts never read *_BOT_TOKEN today, but switching one to Telegram
        # would make them collide, so the rule uses every adapter suffix.
        with self.assertRaises(ValidationError):
            self.load(bsky("b-one", "REACHWEAVER_X"), bsky("b-two", "REACHWEAVER_X_BOT"))

    def test_string_prefix_of_another_variable_rejected(self):
        # REACHWEAVER_A_TOKEN (Mastodon, prefix A) is a string prefix of REACHWEAVER_A_TOKEN2_TOKEN.
        with self.assertRaises(ValidationError) as caught:
            self.load(masto("m-one", "REACHWEAVER_A"), masto("m-two", "REACHWEAVER_A_TOKEN2"))
        self.assertIn("REACHWEAVER_A_TOKEN", str(caught.exception))

    def test_postiz_backend_suffix_counted(self):
        self.assertEqual(BACKEND_CREDENTIAL_SUFFIX, postiz.CREDENTIAL_ENV_SUFFIX)
        self.assertIn(postiz.CREDENTIAL_ENV_SUFFIX, ALL_CREDENTIAL_SUFFIXES)
        self.assertEqual(set(ALL_CREDENTIAL_SUFFIXES) - {postiz.CREDENTIAL_ENV_SUFFIX},
                         {s for names in accounts_module.CREDENTIAL_SUFFIXES.values() for s in names})
        # Only the Postiz suffix makes these collide: REACHWEAVER_Q_POSTIZ_API_KEY begins
        # REACHWEAVER_Q_POSTIZ_API_KEY_TOKEN.
        rows = [pz("pz-q", "REACHWEAVER_Q"), masto("m-q", "REACHWEAVER_Q_POSTIZ_API_KEY")]
        [conflict] = env_prefix_conflicts(rows)
        self.assertIn("REACHWEAVER_Q_POSTIZ_API_KEY", conflict["variables"])
        with self.assertRaises(ValidationError):
            self.load(*rows)
        # Two Postiz accounts sharing one prefix (one workspace key) are refused too.
        with self.assertRaises(ValidationError):
            self.load(pz("pz-a", "REACHWEAVER_PZSHARED"),
                      pz("pz-b", "REACHWEAVER_PZSHARED", postiz_integration_id="int-hb-2"))

    def test_non_overlapping_prefixes_accepted(self):
        loaded = self.load(bsky("acme-bsky", "REACHWEAVER_ACME_BSKY"),
                           bsky("acme-bsky-staging", "REACHWEAVER_ACME_BSKY_STAGING"),
                           masto("acme-masto", "REACHWEAVER_ACME_MASTO"),
                           tg("acme-tg", "REACHWEAVER_ACME_TG"),
                           li("acme-li", "REACHWEAVER_ACME_LI"),
                           pz("acme-pz", "REACHWEAVER_ACME_PZ"))
        self.assertEqual(len(loaded), 6)
        self.assertEqual(env_prefix_conflicts(loaded.values()), [])

    def test_example_config_still_loads(self):
        self.assertTrue(load_accounts(ROOT / "config" / "accounts.example.json"))

    def test_reserved_non_account_credential_rejected(self):
        # Mastodon prefix REACHWEAVER_GITHUB would read REACHWEAVER_GITHUB + _TOKEN: the sources token.
        from reachweaver import sources
        self.assertIn(sources.TOKEN_ENV, accounts_module.NON_ACCOUNT_CREDENTIALS)
        with self.assertRaises(ValidationError) as caught:
            self.load(masto("m-gh", "REACHWEAVER_GITHUB"))
        message = str(caught.exception)
        self.assertIn("m-gh", message)
        self.assertIn("REACHWEAVER_GITHUB_TOKEN", message)
        # A prefix whose variable merely begins the reserved name is refused too.
        [conflict] = env_prefix_conflicts([tg("tg-gh", "REACHWEAVER_GITHUB_TOKEN_X")])
        self.assertEqual(conflict["reason"], "reserved_credential_variable")
        self.assertEqual(conflict["accounts"], ["tg-gh"])
        report = audit_environment([masto("m-gh", "REACHWEAVER_GITHUB")], {})
        self.assertIn("reserved_credential_variable", [f["code"] for f in report["findings"]])
        self.assertEqual(env_prefix_conflicts([masto("m-ghx", "REACHWEAVER_GITHUBX")]), [])

    def test_audit_reports_overlap_for_raw_lists(self):
        report = audit_environment([tg("tg-x", "REACHWEAVER_X"), masto("masto-x", "REACHWEAVER_X_BOT")], {})
        codes = [f["code"] for f in report["findings"]]
        self.assertIn("overlapping_env_prefix", codes)
        self.assertIn("shared_credential_variable", codes)


# --------------------------------------------------------------------------- 2. live gate

class RequireLiveGateUnitTests(unittest.TestCase):
    def test_all_combinations(self):
        self.assertIsNone(require_live_gate(prod_env()))
        refused = [
            {},
            {"REACHWEAVER_ENV": "prod"},                                   # no opt-in
            {"REACHWEAVER_ALLOW_LIVE": "true", "REACHWEAVER_ENV": "prod"},  # opt-in must be exactly 1
            {"REACHWEAVER_ALLOW_LIVE": "1"},                                # ENV unset
            {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": ""},
            {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "dev"},
            {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "staging"},
            {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "PROD"},
            {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod "},
            {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "production"},
            {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": GARBAGE_ENV},
        ]
        for env in refused:
            with self.subTest(env=env), self.assertRaises(ValidationError) as caught:
                require_live_gate(env)
            message = str(caught.exception)
            self.assertIn("REACHWEAVER_ENV=prod", message)
            self.assertNotIn(GARBAGE_ENV, message)

    def test_runtime_environment_never_echoes_unrecognized_values(self):
        self.assertEqual(runtime_environment({}), "unset")
        self.assertEqual(runtime_environment({"REACHWEAVER_ENV": ""}), "unset")
        for value in ("dev", "staging", "prod"):
            self.assertEqual(runtime_environment({"REACHWEAVER_ENV": value}), value)
        for value in ("PROD", "prod ", "production", GARBAGE_ENV, "\x1b[31mprod", "prod\n"):
            with self.subTest(value=value):
                self.assertEqual(runtime_environment({"REACHWEAVER_ENV": value}), "unrecognized")
        self.assertTrue(live_opt_in({"REACHWEAVER_ALLOW_LIVE": "1"}))
        self.assertFalse(live_opt_in({"REACHWEAVER_ALLOW_LIVE": "yes"}))


class LiveGateCliTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.db = str(self.base / "state" / "outbox.sqlite")
        self.config = write_accounts(self.base / "accounts.json", [
            bsky("hb-bluesky", "REACHWEAVER_HB_BSKY"), pz("hb-postiz", "REACHWEAVER_HB_PZ")])
        with connect(self.db) as db:
            self.native = add_post(db, "hb-bluesky", "bluesky", "Hardening gate note https://example.org/a")
            self.backend = add_post(db, "hb-postiz", "bluesky", "Hardening backend note https://example.org/b")
            transition(db, self.native, "approve")
            transition(db, self.backend, "approve")
        # Credentials would be readable if the gate let anything through.
        self.credentials = {"REACHWEAVER_HB_BSKY_IDENTIFIER": "owner.bsky.social",
                            "REACHWEAVER_HB_BSKY_APP_PASSWORD": FILE_PASSWORD,
                            "REACHWEAVER_HB_PZ_POSTIZ_API_KEY": PZ_KEY}

    def cli(self, *argv, env):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    @contextlib.contextmanager
    def nothing_may_happen(self):
        """Every account load, credential read, identity check, send and socket fails the test."""
        boom = AssertionError("live gate let work through")
        with NetworkGuard() as guard, \
                patch("reachweaver.cli.load_accounts", side_effect=boom) as loader, \
                patch("reachweaver.accounts.credential", side_effect=boom) as reader, \
                patch("reachweaver.linkedin.credential", side_effect=boom) as li_reader, \
                patch("reachweaver.cli.check_connection", side_effect=boom) as checker, \
                patch("reachweaver.cli.publish", side_effect=boom) as sender, \
                patch("reachweaver.postiz.run_live_send", side_effect=boom) as backend, \
                patch("reachweaver.publishers._send_http", side_effect=boom) as wire, \
                patch("reachweaver.publishers.request_json", side_effect=boom) as poster, \
                patch("reachweaver.publishers.request_get_json", side_effect=boom) as getter:
            yield
        for mock in (loader, reader, li_reader, checker, sender, backend, wire, poster, getter):
            mock.assert_not_called()
        self.assertEqual(guard.attempts, [])

    def assert_untouched(self):
        with connect(self.db) as db:
            for pid in (self.native, self.backend):
                self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 0)

    def test_without_live_flag_is_a_dry_run_even_with_both_env_markers(self):
        with self.nothing_may_happen():
            code, out, err = self.cli("publish", env=prod_env(**self.credentials))
        self.assertEqual(code, 0, err)
        self.assertIn("DRY RUN", out)
        self.assertIn("REACHWEAVER_ENV prod", out)
        self.assertNotIn(FILE_PASSWORD, out + err)
        self.assert_untouched()

    def test_live_without_opt_in_refused_before_any_account_or_credential(self):
        env = {"REACHWEAVER_ENV": "prod", **self.credentials}
        for argv in (("publish", "--live"), ("publish", "--live", "--post-id", self.native),
                     ("publish", "--live", "--post-id", self.backend), ("publish", "--live", "--scheduled-only")):
            with self.subTest(argv=argv), self.nothing_may_happen():
                code, out, err = self.cli(*argv, env=env)
                self.assertEqual(code, 2)
                self.assertIn("REACHWEAVER_ALLOW_LIVE=1", err)
                self.assertNotIn("PUBLISHED", out)
        self.assert_untouched()

    def test_live_without_prod_marker_refused_before_any_account_or_credential(self):
        for value in (None, "", "dev", "staging", "PROD", "production", GARBAGE_ENV):
            env = {"REACHWEAVER_ALLOW_LIVE": "1", **self.credentials}
            if value is not None:
                env["REACHWEAVER_ENV"] = value
            for post_id in (self.native, self.backend):  # native adapter and Postiz backend paths
                with self.subTest(value=value, post_id=post_id), self.nothing_may_happen():
                    code, out, err = self.cli("publish", "--live", "--post-id", post_id, env=env)
                    self.assertEqual(code, 2)
                    self.assertIn("REACHWEAVER_ENV=prod", err)
                    self.assertIn("no account was loaded and nothing was sent", err)
                    self.assertNotIn(GARBAGE_ENV, out + err)
                    self.assertNotIn(FILE_PASSWORD, out + err)
            with self.subTest(value=value, batch=True), self.nothing_may_happen():
                self.assertEqual(self.cli("publish", "--live", env=env)[0], 2)
        self.assert_untouched()

    def test_positive_control_all_three_reach_the_native_send(self):
        verified = VerifiedConnection("hb-bluesky", "bluesky", "verified", "unverified")
        with NetworkGuard() as guard, \
                patch("reachweaver.cli.check_connection", return_value=verified) as checker, \
                patch("reachweaver.cli.publish", return_value=("at://x", "https://bsky.app/x")) as sender:
            code, out, err = self.cli("publish", "--live", "--post-id", self.native, env=prod_env(**self.credentials))
        self.assertEqual(code, 0, out + err)
        self.assertIn("PUBLISHED " + self.native, out)
        checker.assert_called_once()
        sender.assert_called_once()
        self.assertEqual(guard.attempts, [])

    def test_positive_control_all_three_reach_the_postiz_backend(self):
        with NetworkGuard() as guard, patch("reachweaver.postiz.run_live_send", return_value=0) as backend:
            code, out, err = self.cli("publish", "--live", "--post-id", self.backend, env=prod_env(**self.credentials))
        self.assertEqual(code, 0, out + err)
        backend.assert_called_once()
        self.assertEqual(guard.attempts, [])

    def test_unrecognized_env_never_echoed_by_status_commands(self):
        env = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": GARBAGE_ENV}
        with NetworkGuard() as guard:
            for argv in (("doctor",), ("publish",), ("health",), ("health", "--json"), ("credentials-audit",),
                         ("credentials-audit", "--json"), ("privacy-status",)):
                with self.subTest(argv=argv):
                    _code, out, err = self.cli(*argv, env=env)
                    self.assertNotIn(GARBAGE_ENV, out + err)
                    self.assertIn("unrecognized", out)
        self.assertEqual(guard.attempts, [])
        _code, out, _ = self.cli("doctor", env={"REACHWEAVER_ENV": "staging"})
        self.assertIn("REACHWEAVER_ENV=staging", out)
        self.assertIn("Live opt-in: disabled", out)
        self.assertIn("Credential source: environment", out)


# --------------------------------------------------------------------------- 3. credentials directory

@unittest.skipUnless(POSIX, "systemd credentials directories are POSIX-only")
class CredentialsDirectoryTests(TempDirTest):
    NAME = "REACHWEAVER_HB_MASTO_TOKEN"

    def setUp(self):
        super().setUp()
        self.dir = self.base / "creds"
        self.dir.mkdir(mode=0o700)
        self.dir.chmod(0o700)
        self.addCleanup(self.dir.chmod, 0o700)  # so the temporary directory can always be removed
        self.env = {"CREDENTIALS_DIRECTORY": str(self.dir)}

    def put(self, name, data, mode=0o400):
        path = self.dir / name
        if path.exists() or path.is_symlink():
            path.unlink()
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        path.chmod(mode)
        return path

    def assert_safe_error(self, exc, *secrets):
        message = str(exc)
        self.assertNotIn(str(self.base), message)  # never a path
        for secret in secrets:
            self.assertNotIn(secret, message)       # never a value

    def test_reads_file_and_strips_exactly_one_trailing_newline(self):
        self.put(self.NAME, FILE_TOKEN + "\n")
        self.assertEqual(credential(self.NAME, self.env), FILE_TOKEN)
        self.put(self.NAME, FILE_TOKEN, mode=0o600)
        self.assertEqual(credential(self.NAME, self.env), FILE_TOKEN)
        self.put(self.NAME, FILE_TOKEN + "\r\n", mode=0o600)  # a file saved with Windows line endings
        self.assertEqual(credential(self.NAME, self.env), FILE_TOKEN)
        self.put(self.NAME, FILE_TOKEN + "\n\n", mode=0o600)  # only ONE newline is stripped: the rest is refused
        with self.assertRaisesRegex(CredentialError, "contains control characters"):
            credential(self.NAME, self.env)
        self.assertEqual(credential_source(self.env), "credentials_directory")
        self.assertEqual(credential_source({}), "environment")

    def test_environment_not_consulted_when_directory_set(self):
        env = {**self.env, self.NAME: ENV_TOKEN}
        self.assertIsNone(credential(self.NAME, env))  # no file: missing, even though the env var is set
        self.put(self.NAME, FILE_TOKEN)
        self.assertEqual(credential(self.NAME, env), FILE_TOKEN)
        self.assertEqual(credential(self.NAME, {self.NAME: ENV_TOKEN}), ENV_TOKEN)  # fallback without the directory

    def test_missing_file_is_none_and_missing_credentials(self):
        self.assertIsNone(credential(self.NAME, self.env))
        with self.assertRaises(ValidationError) as caught:
            require_credential(self.NAME, self.env)
        self.assertEqual(str(caught.exception), "missing_credentials: " + self.NAME)
        with patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(missing_credentials(masto("hb-masto", "REACHWEAVER_HB_MASTO")), [self.NAME])
            self.put(self.NAME, FILE_TOKEN)
            self.assertEqual(missing_credentials(masto("hb-masto", "REACHWEAVER_HB_MASTO")), [])

    def test_symlink_refused(self):
        target = self.base / "elsewhere"
        target.write_text(FILE_TOKEN, encoding="utf-8")
        os.symlink(target, self.dir / self.NAME)
        with self.assertRaises(CredentialError) as caught:
            credential(self.NAME, self.env)
        self.assertIn(self.NAME, str(caught.exception))
        self.assertIn("symlink", str(caught.exception))
        self.assert_safe_error(caught.exception, FILE_TOKEN)

    def test_fifo_refused_without_hanging(self):
        os.mkfifo(self.dir / self.NAME, 0o600)
        outcome: list[BaseException | object] = []

        def attempt():
            try:
                outcome.append(credential(self.NAME, self.env))
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion below
                outcome.append(exc)

        worker = threading.Thread(target=attempt, daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "reading a FIFO credential must not block")
        self.assertIsInstance(outcome[0], CredentialError)
        self.assertIn("not a regular file", str(outcome[0]))
        self.assert_safe_error(outcome[0])

    def test_directory_and_other_non_regular_entries_refused(self):
        (self.dir / self.NAME).mkdir()
        with self.assertRaises(CredentialError):
            credential(self.NAME, self.env)

    def test_size_limit(self):
        self.put(self.NAME, "a" * MAX_CREDENTIAL_BYTES)
        self.assertEqual(len(credential(self.NAME, self.env)), MAX_CREDENTIAL_BYTES)
        self.put(self.NAME, "a" * (MAX_CREDENTIAL_BYTES + 1), mode=0o600)
        with self.assertRaises(CredentialError) as caught:
            credential(self.NAME, self.env)
        self.assertIn("larger than", str(caught.exception))
        self.assertEqual(MAX_CREDENTIAL_BYTES, 64 * 1024)

    def test_non_utf8_refused(self):
        self.put(self.NAME, b"\xff\xfe" + FILE_TOKEN.encode())
        with self.assertRaises(CredentialError) as caught:
            credential(self.NAME, self.env)
        self.assertIn("UTF-8", str(caught.exception))
        self.assert_safe_error(caught.exception, FILE_TOKEN)

    def test_group_or_world_writable_directory_refused(self):
        self.put(self.NAME, FILE_TOKEN)
        for mode in (0o770, 0o707, 0o777, 0o720):
            self.dir.chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(CredentialsDirectoryError) as caught:
                credential(self.NAME, self.env)
            self.assertIn("writable", str(caught.exception))
            self.assert_safe_error(caught.exception, FILE_TOKEN)
        self.dir.chmod(0o750)  # group-readable is fine; only write access is refused
        self.assertEqual(credential(self.NAME, self.env), FILE_TOKEN)
        self.dir.chmod(0o500)  # systemd's own credentials directories are read-only
        self.assertEqual(credential(self.NAME, self.env), FILE_TOKEN)
        self.dir.chmod(0o700)

    def test_relative_or_missing_directory_refused(self):
        for value in ("creds", "./creds", str(self.base / "does-not-exist")):
            with self.subTest(value=value), self.assertRaises(CredentialsDirectoryError) as caught:
                credential(self.NAME, {"CREDENTIALS_DIRECTORY": value})
            self.assert_safe_error(caught.exception)
        (self.base / "plain-file").write_text("x", encoding="utf-8")
        with self.assertRaises(CredentialsDirectoryError):
            credential(self.NAME, {"CREDENTIALS_DIRECTORY": str(self.base / "plain-file")})

    def test_invalid_names_never_touch_the_filesystem(self):
        for name in ("../etc/passwd", "lower_case", "A/B", "", "REACHWEAVER_X.TOKEN"):
            with self.subTest(name=name), self.assertRaises(CredentialError):
                credential(name, self.env)

    def test_file_values_redacted_like_env_secrets(self):
        self.put(self.NAME, FILE_TOKEN + "\n")
        text = f"provider said Bearer-less {FILE_TOKEN} in an error"
        self.assertIn(FILE_TOKEN, redact(text, env={}))  # not read yet: nothing to strip verbatim
        self.assertEqual(credential(self.NAME, self.env), FILE_TOKEN)
        redacted = redact(text, env={})
        self.assertNotIn(FILE_TOKEN, redacted)
        self.assertIn(f"[REDACTED:env:{self.NAME}]", redacted)
        # Identical to the environment-variable path.
        self.assertEqual(redacted, redact(text, env={self.NAME: FILE_TOKEN}))

    def test_doctor_health_and_audit_use_the_directory(self):
        config = write_accounts(self.base / "accounts.json", [masto("hb-masto", "REACHWEAVER_HB_MASTO"),
                                                              tg("hb-tg", "REACHWEAVER_HB_TG")])
        self.put(self.NAME, FILE_TOKEN)
        self.put("REACHWEAVER_HB_TG_BOT_TOKEN", FILE_TOKEN)        # reused value
        self.put("REACHWEAVER_STALE_TOKEN", "-".join(("stale", "file", "tok")))  # orphan file
        os.symlink(self.base / "nowhere", self.dir / "REACHWEAVER_LINKED_TOKEN")  # unsafe file
        env = {**self.env, "REACHWEAVER_HB_MASTO_TOKEN": ENV_TOKEN}  # shadowed by the directory
        db = str(self.base / "state" / "outbox.sqlite")
        outputs = []
        with NetworkGuard() as guard, patch.dict(os.environ, env, clear=True):
            for argv in (("doctor",), ("credentials-audit", "--json"), ("credentials-audit",)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(["--db", db, "--accounts", str(config), *argv])
                outputs.append((code, out.getvalue(), err.getvalue()))
        self.assertEqual(guard.attempts, [])
        self.assertEqual([code for code, _, _ in outputs], [0, 1, 1])
        text = "".join(out + err for _, out, err in outputs)
        self.assertIn("Credential source: credentials_directory", outputs[0][1])
        self.assertIn("Credential source: credentials_directory", outputs[2][1])
        self.assertIn("hb-masto: mastodon; supported (text); env variables missing: none", outputs[0][1])
        for secret in (FILE_TOKEN, ENV_TOKEN, "stale-file-tok", str(self.base)):
            self.assertNotIn(secret, text)
        report = json.loads(outputs[1][1])
        self.assertEqual(report["credential_source"], "credentials_directory")
        codes = {f["code"]: f for f in report["findings"]}
        self.assertEqual(codes["env_credential_shadowed"]["variables"], ["REACHWEAVER_HB_MASTO_TOKEN"])
        self.assertEqual(codes["orphan_credential"]["variables"], ["REACHWEAVER_LINKED_TOKEN", "REACHWEAVER_STALE_TOKEN"])
        self.assertEqual(codes["credential_value_reused"]["variables"],
                         ["REACHWEAVER_HB_MASTO_TOKEN", "REACHWEAVER_HB_TG_BOT_TOKEN"])
        self.assertEqual(codes["unsafe_credential_file"]["variables"], ["REACHWEAVER_LINKED_TOKEN"])
        rendered = render_audit(audit_environment(load_accounts(config), env))
        self.assertIn("Credential source: credentials_directory", rendered)
        self.assertNotIn(FILE_TOKEN, rendered)

    def test_unsafe_directory_reported_not_raised_by_audit_and_health(self):
        config = write_accounts(self.base / "accounts.json", [masto("hb-masto", "REACHWEAVER_HB_MASTO")])
        self.put(self.NAME, FILE_TOKEN)
        self.dir.chmod(0o777)
        try:
            report = audit_environment(load_accounts(config), self.env)
            codes = [f["code"] for f in report["findings"]]
            self.assertIn("unsafe_credentials_directory", codes)
            self.assertEqual(report["accounts"][0]["missing"], [self.NAME])
            with connect(str(self.base / "state" / "outbox.sqlite")) as db:
                health = build_health(db, load_accounts(config), env=self.env)
            reasons = {r["code"]: r for r in health["reasons"]}
            self.assertIn("credential_unreadable", reasons)
            self.assertEqual(health["global"]["credential_source"], "credentials_directory")
            dumped = json.dumps(report) + json.dumps(health) + render_health(health) + render_audit(report)
            self.assertNotIn(FILE_TOKEN, dumped)
            self.assertNotIn(str(self.base), dumped)
        finally:
            self.dir.chmod(0o700)


# --------------------------------------------------------------------------- every adapter uses the accessor

class FakeResponse:
    def __init__(self, data=None, *, status=200, headers=None, raw=None):
        self.raw = raw if raw is not None else json.dumps(data).encode("utf-8")
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self.raw[:limit] if limit and limit > 0 else self.raw


class FakeWire:
    """Scripted provider replies keyed by URL fragment. Records every Request; opens no socket."""

    def __init__(self):
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        url = req.full_url
        if url.endswith("/xrpc/com.atproto.server.createSession"):
            return FakeResponse({"did": DID, "handle": "owner.bsky.social", "accessJwt": "hb-access"})
        if url.endswith("/xrpc/com.atproto.repo.createRecord"):
            return FakeResponse({"uri": f"at://{DID}/app.bsky.feed.post/3khardening", "cid": CID})
        if url.endswith("/api/v1/statuses"):
            return FakeResponse({"id": "1001", "url": "https://mastodon.example.org/@owner/1001",
                                 "account": {"username": "owner"}, "visibility": "public"})
        if url.endswith("/api/v1/accounts/verify_credentials"):
            return FakeResponse({"id": "7", "username": "owner"})
        if "api.telegram.org/bot" in url and url.endswith("/sendMessage"):
            return FakeResponse({"ok": True, "result": {"message_id": 42}})
        if url == linkedin.POSTS_URL:
            return FakeResponse(status=201, headers={"x-restli-id": "urn:li:share:6844785523593134080"}, raw=b"")
        if url.endswith("/integrations"):
            return FakeResponse([{"id": "int-hb-1", "identifier": "bluesky", "profile": "owner.bsky.social",
                                  "disabled": False}])
        if url.endswith("/posts") and req.get_method() == "POST":
            return FakeResponse([{"postId": "pz-hb-1", "integration": "int-hb-1"}])
        raise AssertionError("unexpected request")


@unittest.skipUnless(POSIX, "systemd credentials directories are POSIX-only")
class AdapterAccessorTests(TempDirTest):
    """Only $CREDENTIALS_DIRECTORY is set: every adapter must find its credential in a file."""

    def setUp(self):
        super().setUp()
        self.dir = self.base / "creds"
        self.dir.mkdir(mode=0o700)
        self.dir.chmod(0o700)
        for name, value in (("REACHWEAVER_HB_BSKY_IDENTIFIER", "owner.bsky.social"),
                            ("REACHWEAVER_HB_BSKY_APP_PASSWORD", FILE_PASSWORD),
                            ("REACHWEAVER_HB_MASTO_TOKEN", FILE_TOKEN),
                            ("REACHWEAVER_HB_TG_BOT_TOKEN", TG_TOKEN),
                            ("REACHWEAVER_HB_LI_ACCESS_TOKEN", LI_TOKEN),
                            ("REACHWEAVER_HB_PZ_POSTIZ_API_KEY", PZ_KEY)):
            path = self.dir / name
            path.write_text(value + "\n", encoding="utf-8")
            path.chmod(0o400)
        self.wire = FakeWire()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        # Only the directory: no credential exists in the environment at all.
        stack.enter_context(patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.dir)}, clear=True))
        self.guard = stack.enter_context(NetworkGuard())
        stack.enter_context(patch("reachweaver.publishers._send_http", side_effect=self.wire))

    def tearDown(self):
        self.assertEqual(self.guard.attempts, [])

    def test_bluesky(self):
        receipt = publishers.publish_bluesky(bsky("hb-bsky", "REACHWEAVER_HB_BSKY"), "Hardening note", "p1")
        self.assertTrue(receipt[0].startswith("at://"))
        session = json.loads(self.wire.requests[0].data)
        self.assertEqual(session, {"identifier": "owner.bsky.social", "password": FILE_PASSWORD})

    def test_mastodon_publish_and_identity_check(self):
        account = masto("hb-masto", "REACHWEAVER_HB_MASTO")
        publishers.publish_mastodon(account, "Hardening note", "p2")
        self.assertEqual(self.wire.requests[-1].get_header("Authorization"), "Bearer " + FILE_TOKEN)
        check_connection(account)
        self.assertEqual(self.wire.requests[-1].get_header("Authorization"), "Bearer " + FILE_TOKEN)

    def test_telegram(self):
        remote_id, _url = publishers.publish_telegram(tg("hb-tg", "REACHWEAVER_HB_TG"), "Hardening note", "p3")
        self.assertEqual(remote_id, "42")
        self.assertIn("/bot" + TG_TOKEN + "/sendMessage", self.wire.requests[-1].full_url)

    def test_linkedin(self):
        from datetime import datetime, timezone
        account = li("hb-li", "REACHWEAVER_HB_LI")
        remote_id, _url = linkedin.publish_member_post(account, "Hardening note", "p4",
                                                       now=datetime(2026, 10, 9, tzinfo=timezone.utc))
        self.assertEqual(remote_id, "urn:li:share:6844785523593134080")
        self.assertEqual(self.wire.requests[-1].get_header("Authorization"), "Bearer " + LI_TOKEN)

    def test_postiz(self):
        account = pz("hb-pz", "REACHWEAVER_HB_PZ", postiz_api_prefix="/public/v1")
        with self.assertRaises(postiz.BackendAccepted):
            postiz.publish(account, "Hardening note https://example.org/c", "p5")
        self.assertTrue(self.wire.requests)
        for req in self.wire.requests:
            self.assertEqual(req.get_header("Authorization"), PZ_KEY)

    def test_environment_values_are_ignored(self):
        os.environ["REACHWEAVER_HB_MASTO_TOKEN"] = ENV_TOKEN
        (self.dir / "REACHWEAVER_HB_MASTO_TOKEN").chmod(0o600)
        (self.dir / "REACHWEAVER_HB_MASTO_TOKEN").unlink()
        with self.assertRaises(ValidationError) as caught:
            publishers.publish_mastodon(masto("hb-masto", "REACHWEAVER_HB_MASTO"), "Hardening note", "p6")
        self.assertEqual(str(caught.exception), "missing_credentials: REACHWEAVER_HB_MASTO_TOKEN")
        self.assertEqual(self.wire.requests, [])


class SingleAccessorStaticTests(unittest.TestCase):
    """No module in reachweaver/ reads a credential from the environment around the accessor."""

    ALLOWED = {  # (module, first argument) pairs that are configuration, never credentials
        ("accounts.py", '"REACHWEAVER_ACCOUNTS"'), ("accounts.py", "CREDENTIALS_DIRECTORY_ENV"),
        ("accounts.py", "name"),  # inside credential() itself
        ("caps.py", "CAPS_ENV"), ("optout.py", "OPTOUT_ENV"), ("storage.py", '"REACHWEAVER_DB"'),
        ("privacy.py", "POLICY_ENV"), ("cli.py", '"USER"'), ("cli.py", '"LOGNAME"'),
        ("policy.py", "RUNTIME_ENV"), ("policy.py", "LIVE_OPT_IN_ENV"),
        ("credential_hygiene.py", "n"),  # names of shadowed env credentials: presence only, never the value
    }
    READ = re.compile(r"(?:os\.environ|\benv|getenv)\s*(?:\.get\(|\[|\()\s*([^,)\]]+)")

    def test_every_environment_read_is_configuration_or_the_accessor(self):
        found = set()
        for path in sorted((ROOT / "reachweaver").glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for match in self.READ.finditer(line):
                    found.add((path.name, match.group(1).strip(), number))
        unexpected = sorted(f"{name}:{number}: {arg}" for name, arg, number in found
                            if (name, arg) not in self.ALLOWED)
        self.assertEqual(unexpected, [], "route credential reads through accounts.credential")

    def test_no_adapter_imports_os_for_credentials(self):
        for name in ("publishers.py", "connections.py", "linkedin.py", "metrics_fetch.py", "postiz.py"):
            text = (ROOT / "reachweaver" / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^import os\b", name)


# --------------------------------------------------------------------------- control characters (review F1)

# A token whose header would be "Bearer <token>\r": http.client refused it with
# ValueError("Invalid header value b'Bearer <token>\\r'"), which reached "ERROR: ..." verbatim.
CR_TOKEN = "-".join(("hardening", "cr", "token", "5d1e"))


class ControlCharacterCredentialTests(TempDirTest):
    """A credential containing CR, LF, NUL or another C0 control is refused by name only."""

    NAME = "REACHWEAVER_HB_MASTO_TOKEN"

    def setUp(self):
        super().setUp()
        self.dir = self.base / "creds"
        self.dir.mkdir(mode=0o700)
        self.dir.chmod(0o700)
        self.env = {"CREDENTIALS_DIRECTORY": str(self.dir)}

    def put(self, data: bytes):
        path = self.dir / self.NAME
        if path.exists():
            path.chmod(0o600)
            path.unlink()
        path.write_bytes(data)
        path.chmod(0o400)

    def assert_refused(self, env):
        with self.assertRaises(CredentialError) as caught:
            credential(self.NAME, env)
        self.assertEqual(str(caught.exception), f"credential {self.NAME} contains control characters")
        self.assertNotIn(CR_TOKEN, str(caught.exception))
        self.assertNotIn(str(self.base), str(caught.exception))
        self.assertEqual(accounts_module.loaded_file_credentials(), [])  # never kept for later use

    @unittest.skipUnless(POSIX, "systemd credentials directories are POSIX-only")
    def test_file_values(self):
        self.put(CR_TOKEN.encode() + b"\r\n")
        self.assertEqual(credential(self.NAME, self.env), CR_TOKEN)  # exactly one CRLF is stripped
        accounts_module._LOADED_FILE_CREDENTIALS.clear()
        for data in (b"\r", b"\r\r\n", b"\n\n", b"\x00", b"\t", b"\x1b[0m", b"\x7f", b"\r\n\r\n"):
            with self.subTest(data=data):
                self.put(CR_TOKEN.encode() + data)
                self.assert_refused(self.env)
        self.put(CR_TOKEN[:6].encode() + b"\nInjected: header")  # an embedded newline, not trailing
        self.assert_refused(self.env)

    def test_environment_values(self):
        for suffix in ("\r", "\n", "\r\n", "\x00", "\x1f"):
            with self.subTest(suffix=suffix):
                self.assert_refused({self.NAME: CR_TOKEN + suffix})
        self.assert_refused({self.NAME: "Bearer\x00" + CR_TOKEN})
        self.assertEqual(credential(self.NAME, {self.NAME: CR_TOKEN}), CR_TOKEN)  # clean values unchanged
        self.assertEqual(credential(self.NAME, {self.NAME: "spaces are fine"}), "spaces are fine")

    def test_require_credential_and_missing_credentials_name_only(self):
        env = {self.NAME: CR_TOKEN + "\r"}
        with self.assertRaisesRegex(CredentialError, "contains control characters") as caught:
            require_credential(self.NAME, env)
        self.assertNotIn(CR_TOKEN, str(caught.exception))
        with patch.dict(os.environ, env, clear=True), self.assertRaises(CredentialError) as caught:
            missing_credentials(masto("hb-masto", "REACHWEAVER_HB_MASTO"))
        self.assertNotIn(CR_TOKEN, str(caught.exception))

    def test_health_and_audit_report_the_name_never_the_value(self):
        config = write_accounts(self.base / "accounts.json", [masto("hb-masto", "REACHWEAVER_HB_MASTO")])
        env = {self.NAME: CR_TOKEN + "\r"}
        report = audit_environment(load_accounts(config), env)
        with connect(str(self.base / "state" / "outbox.sqlite")) as db:
            health = build_health(db, load_accounts(config), env=env)
        dumped = json.dumps(report) + json.dumps(health) + render_health(health) + render_audit(report)
        self.assertNotIn(CR_TOKEN, dumped)
        self.assertIn("contains control characters", dumped)


class ControlCharacterCliTests(TempDirTest):
    """End to end: the token never reaches stdout or stderr; no socket is opened."""

    def setUp(self):
        super().setUp()
        self.db = str(self.base / "state" / "outbox.sqlite")
        self.config = write_accounts(self.base / "accounts.json", [
            masto("hb-masto", "REACHWEAVER_HB_MASTO"), bsky("hb-bsky", "REACHWEAVER_HB_BSKY"),
            tg("hb-tg", "REACHWEAVER_HB_TG")])

    def cli(self, *argv, env):
        out, err = io.StringIO(), io.StringIO()
        with NetworkGuard() as guard, patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        self.assertEqual(guard.attempts, [])
        return code, out.getvalue() + err.getvalue()

    def test_connect_check_with_crlf_file_or_cr_env_value(self):
        creds = self.base / "creds"
        creds.mkdir(mode=0o700)
        (creds / "REACHWEAVER_HB_MASTO_TOKEN").write_bytes(CR_TOKEN.encode() + b"\r\r\n")
        for env in ({"CREDENTIALS_DIRECTORY": str(creds)},
                    {"REACHWEAVER_HB_MASTO_TOKEN": CR_TOKEN + "\r"},
                    {"REACHWEAVER_HB_BSKY_IDENTIFIER": "owner.bsky.social",
                     "REACHWEAVER_HB_BSKY_APP_PASSWORD": FILE_PASSWORD + "\n"},
                    {"REACHWEAVER_HB_TG_BOT_TOKEN": TG_TOKEN + "\r\n"}):
            account = ("hb-masto" if any("MASTO" in k or "CREDENTIALS" in k for k in env) else
                       "hb-bsky" if any("BSKY" in k for k in env) else "hb-tg")
            with self.subTest(account=account, env=sorted(env)):
                code, text = self.cli("connect-check", "--online", "--account", account, env=env)
                self.assertEqual(code, 2, text)
                self.assertIn("contains control characters", text)
                for secret in (CR_TOKEN, FILE_PASSWORD, TG_TOKEN, "Invalid header", str(self.base)):
                    self.assertNotIn(secret, text)

    def test_metrics_fetch_bearer_retry_with_cr_env_token(self):
        with connect(self.db) as db:
            pid = add_post(db, "hb-masto", "mastodon", "A verified research note")
            db.execute("UPDATE posts SET status='published', remote_id='1001' WHERE id=?", (pid,))
        unauthenticated_404 = publishers.PublishError("http_404", status=404)
        with patch("reachweaver.publishers.request_get_json", side_effect=[unauthenticated_404]) as getter:
            code, text = self.cli("metrics-fetch", "--online", "--account", "hb-masto",
                                  env={"REACHWEAVER_HB_MASTO_TOKEN": CR_TOKEN + "\r"})
        self.assertEqual(code, 2, text)
        self.assertEqual(getter.call_count, 1)  # the bearer retry is never attempted
        self.assertIn("contains control characters", text)
        self.assertNotIn(CR_TOKEN, text)

    def test_top_level_error_printer_redacts(self):
        env = {"REACHWEAVER_HB_MASTO_TOKEN": CR_TOKEN}
        with patch("reachweaver.cli.execute", side_effect=ValueError(f"boom Bearer {CR_TOKEN} {CR_TOKEN}")):
            code, text = self.cli("doctor", env=env)
        self.assertEqual(code, 2)
        self.assertIn("ERROR: boom", text)
        self.assertNotIn(CR_TOKEN, text)


class RequestBuildErrorTests(TempDirTest):
    """Defence in depth: http.client/urllib errors raised while building or sending a request
    become a fixed sanitized PublishError; header and URL text never reach the message."""

    def setUp(self):
        super().setUp()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.guard = stack.enter_context(NetworkGuard())
        stack.enter_context(patch.dict(os.environ, {}, clear=True))

    def tearDown(self):
        self.assertEqual(self.guard.attempts, [])  # every case fails before any socket is opened

    def assert_sanitized(self, call):
        with self.assertRaises(publishers.PublishError) as caught:
            call()
        self.assertEqual(str(caught.exception), "invalid_request")
        self.assertTrue(caught.exception.__suppress_context__)  # raised "from None"
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(CR_TOKEN, repr(caught.exception))

    def test_control_character_in_header_value(self):
        headers = {"Authorization": f"Bearer {CR_TOKEN}\r"}
        self.assert_sanitized(lambda: publishers.request_get_json("https://mastodon.example.org/x", headers))
        self.assert_sanitized(lambda: publishers.request_json("https://mastodon.example.org/x", {}, headers))
        self.assert_sanitized(lambda: publishers.request_json_with_headers("https://api.linkedin.com/x", {}, headers))

    def test_control_character_or_bad_scheme_in_url(self):
        for url in (f"https://api.telegram.org/bot{CR_TOKEN}\r\n/getMe",  # http.client.InvalidURL
                    f"bot{CR_TOKEN}/getMe",                                # urllib "unknown url type"
                    f"https://api.telegram.org/bot{CR_TOKEN} x/getMe"):
            with self.subTest(url=url.replace(CR_TOKEN, "<token>")):
                self.assert_sanitized(lambda: publishers.request_get_json(url, telegram_body=True))
                self.assert_sanitized(lambda: publishers.request_json(url, {"text": "x"}))
                self.assert_sanitized(lambda: publishers.request_json_with_headers(url, {}))

    def test_error_after_write_start_is_still_ambiguous(self):
        """Classification is unchanged: a request failure after mark_write_started is C3."""
        from reachweaver.ratelimit import AMBIGUOUS, CERTAIN_NOT_SENT, classify_send_failure
        account = masto("hb-masto", "REACHWEAVER_HB_MASTO")
        publishers.reset_write_marker()
        with patch("reachweaver.publishers.require_credential", return_value=CR_TOKEN + "\r"):
            with self.assertRaises(publishers.PublishError) as caught:
                publishers.publish_mastodon(account, "Hardening note", "p-cr")
        self.assertEqual(str(caught.exception), "invalid_request")
        self.assertTrue(publishers.write_started())
        self.assertEqual(classify_send_failure("mastodon", None, caught.exception,
                                               write_started=publishers.write_started()), AMBIGUOUS)
        # The real accessor now refuses the value before the write marker: certain not sent (C1).
        publishers.reset_write_marker()
        with patch.dict(os.environ, {"REACHWEAVER_HB_MASTO_TOKEN": CR_TOKEN + "\r"}, clear=True), \
                self.assertRaises(CredentialError) as refused:
            publishers.publish_mastodon(account, "Hardening note", "p-cr")
        self.assertFalse(publishers.write_started())
        self.assertEqual(classify_send_failure("mastodon", None, refused.exception, write_started=False),
                         CERTAIN_NOT_SENT)
        publishers.reset_write_marker()


if __name__ == "__main__":
    unittest.main()
