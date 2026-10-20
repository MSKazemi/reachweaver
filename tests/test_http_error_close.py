"""Every ReachWeaver ``HTTPError`` handler closes the error response once it is handled (audit B5).

Python 3.14 emits a ``ResourceWarning`` when an ``HTTPError`` is garbage-collected unclosed, and the warning
text is the error's repr, which quotes the reason phrase. With ``-W``, ``-X dev`` or ``PYTHONWARNINGS`` that
text reached stderr; the fixtures here put a fake bearer value in the reason phrase and a fake marker in the
body, as the platform fixtures do. Checked on every supported version: the handler still returns the same
sanitized error (and still reads the bounded body where it did before), the error response is closed, no
``ResourceWarning`` is emitted, and a ``-X dev -W error::ResourceWarning`` process prints neither value.
No network: the opener is patched.
"""
from __future__ import annotations

import email.message
import gc
import io
import json
import os
import subprocess
import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from reachweaver import postiz, publishers, remote_media, sources, updater, youtube

ROOT = Path(__file__).resolve().parents[1]
FAKE_TOKEN = "-".join(("fake", "httperror", "close", "token", "0000"))  # not a real credential
BODY_MARKER = "fake-httperror-close-body-0000"


def make_error(code: int = 401, body: bytes | None = None, headers: dict | None = None) -> HTTPError:
    message = email.message.Message()
    for name, value in (headers or {}).items():
        message[name] = value
    payload = body if body is not None else json.dumps({"detail": BODY_MARKER}).encode()
    return HTTPError("https://api.example.invalid/v1/me", code, "Bearer " + FAKE_TOKEN, message,
                     io.BytesIO(payload))


class HttpErrorIsClosedTests(unittest.TestCase):
    def run_case(self, error: HTTPError, call, expected: type[BaseException], *,
                 sanitized: bool = True) -> BaseException:
        """Run ``call`` with the opener raising ``error``; return the exception it raised."""
        with patch.object(publishers, "_send_http", side_effect=error), \
                patch.object(updater, "urlopen", side_effect=error):
            with self.assertRaises(expected) as caught:
                call()
        self.assertTrue(error.fp.closed, "the HTTPError response was left open")
        if not sanitized:  # updater.http_get re-raises the HTTPError; its callers print a fixed message
            return caught.exception
        for text in (str(caught.exception), repr(caught.exception)):
            self.assertNotIn(FAKE_TOKEN, text)
            self.assertNotIn(BODY_MARKER, text)
        return caught.exception

    def assert_no_resource_warning(self, case) -> None:
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always", ResourceWarning)
            case()
            gc.collect()
        leaked = [str(w.message) for w in seen if issubclass(w.category, ResourceWarning)
                  and "HTTPError" in str(w.message)]
        self.assertEqual(leaked, [])

    def test_publishers_helpers_close_the_error_and_keep_their_sanitized_codes(self):
        cases = (
            lambda: publishers.request_get_json("https://api.example.invalid/me"),
            lambda: publishers.request_json("https://api.example.invalid/post", {"text": "x"}),
            lambda: publishers.request_json_with_headers("https://api.example.invalid/post", {"text": "x"}),
            lambda: publishers.request_put_bytes("https://upload.example.invalid/chunk", b"x", {}),
            lambda: publishers._status_and_json(publishers._request("https://api.example.invalid/media",
                                                                     method="GET", headers={})),
        )
        for index, call in enumerate(cases):
            with self.subTest(case=index):
                def case(call=call):
                    exc = self.run_case(make_error(403), call, publishers.PublishError)
                    self.assertEqual(str(exc), "http_403")
                    self.assertEqual(exc.status, 403)
                self.assert_no_resource_warning(case)

    def test_the_bounded_body_is_still_read_for_the_telegram_retry_hint_before_closing(self):
        def case():
            error = make_error(429, json.dumps({"ok": False, "parameters": {"retry_after": 7},
                                                "description": BODY_MARKER}).encode())
            exc = self.run_case(error, lambda: publishers.request_json(
                "https://api.telegram.example.invalid/sendMessage", {"text": "x"}, telegram_body=True),
                publishers.PublishError)
            self.assertEqual(str(exc), "http_429")
            self.assertEqual((exc.retry_hint.delay_seconds, exc.retry_hint.source), (7, "telegram_retry_after"))
        self.assert_no_resource_warning(case)

    def test_retry_after_header_hint_is_unchanged(self):
        def case():
            exc = self.run_case(make_error(503, headers={"Retry-After": "12"}),
                                lambda: publishers.request_get_json("https://api.example.invalid/me"),
                                publishers.PublishError)
            self.assertEqual((str(exc), exc.retry_hint.delay_seconds, exc.retry_hint.source), ("http_503", 12, "retry_after"))
        self.assert_no_resource_warning(case)

    def test_youtube_still_reads_the_allowlisted_reason_before_closing(self):
        def case():
            body = json.dumps({"error": {"errors": [{"reason": "quotaExceeded", "message": BODY_MARKER}]}}).encode()
            req = publishers._request("https://www.example.invalid/upload/youtube/v3/videos", method="POST",
                                      headers={})
            exc = self.run_case(make_error(403, body), lambda: youtube._exchange(req, "init"),
                                publishers.PublishError)
            self.assertEqual(str(exc), "youtube_init_http_403_quotaExceeded")
        self.assert_no_resource_warning(case)

    def test_postiz_sources_remote_media_and_updater_close_the_error(self):
        account = {"postiz_base_url": "https://postiz.example.invalid", "postiz_api_prefix": "/public/v1"}
        with patch.object(postiz, "_api_key", return_value="fake-postiz-key"), \
                patch.object(remote_media, "_require_public_addresses", return_value=None):
            cases = (
                (lambda: postiz._call(account, "GET", "/integrations"), publishers.PublishError,
                 "postiz_http_401_unauthorized"),
                (lambda: sources._fetch_response("https://api.github.example.invalid/repos/o/r", max_bytes=1000),
                 sources.SourceError, "http_401"),
                (lambda: remote_media.download_image("https://images.example.com/a/photo.jpg"),
                 remote_media.ImageFetchError, "image_url_http_401"),
                (lambda: updater.http_get("https://api.github.com/repos/o/r/releases/latest"), HTTPError, None),
            )
            for call, expected, code in cases:
                with self.subTest(expected=expected.__name__, code=code):
                    def case(call=call, expected=expected, code=code):
                        exc = self.run_case(make_error(401), call, expected, sanitized=code is not None)
                        if code is not None:
                            self.assertEqual(str(exc), code)
                        else:  # the updater re-raises the HTTPError itself for its callers' fixed message
                            self.assertEqual(exc.code, 401)
                    self.assert_no_resource_warning(case)


class DevModeStderrTests(unittest.TestCase):
    def test_dev_mode_with_resource_warnings_as_errors_prints_no_token_or_body(self):
        script = (
            "import email.message, gc, io\n"
            "from unittest import mock\n"
            "from urllib.error import HTTPError\n"
            "from reachweaver import publishers\n"
            f"err = HTTPError('https://api.example.invalid/me', 401, 'Bearer {FAKE_TOKEN}',\n"
            f"                email.message.Message(), io.BytesIO(b'{BODY_MARKER}'))\n"
            "with mock.patch.object(publishers, '_send_http', side_effect=err):\n"
            "    try:\n"
            f"        publishers.request_get_json('https://api.example.invalid/me', {{'Authorization': 'Bearer {FAKE_TOKEN}'}})\n"
            "    except publishers.PublishError as exc:\n"
            "        print(exc)\n"
            "del err\n"
            "gc.collect()\n"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run([sys.executable, "-X", "dev", "-W", "error::ResourceWarning", "-c", script],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "http_401\n")
        for leak in (FAKE_TOKEN, BODY_MARKER, "ResourceWarning", "Exception ignored"):
            self.assertNotIn(leak, proc.stderr)


if __name__ == "__main__":
    unittest.main()
