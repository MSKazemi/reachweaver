"""`reachweaver studio`: dashboard + composer in one process, linked, each with its own token and guards. Offline.

Only test servers bound to 127.0.0.1 are used, with synthetic captions, ideas and a temporary database. No provider,
credential, cookie, account or network call is made, and nothing may approve, schedule or send.
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import re
import socket
import tempfile
import threading
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from reachweaver import home, ideas, share_browser, share_composer_store as store, studio, ui_assets, ui_server
from reachweaver.cli_parser import make_parser
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, connect

TOKEN_PATH = re.compile(r"^http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]{20,})/#/results$")
COMPOSER = re.compile(r"^http://127\.0\.0\.1:(\d+)/#([A-Za-z0-9_-]{30,})$")


def http_call(port, method, path, *, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    sent = {"Host": f"127.0.0.1:{port}"}
    sent.update(headers or {})
    try:
        conn.request(method, path, body=body, headers=sent)
        res = conn.getresponse()
        return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
    finally:
        conn.close()


class StudioBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "state").mkdir(mode=0o700)
        self.db = str(self.root / "state" / "studio.sqlite")
        old = home.root()
        home.set_root(self.root)
        self.addCleanup(lambda: home.set_root(old))
        with connect(self.db) as con:
            add_post(con, "a-bsky", "bluesky", "Synthetic draft for the studio test")

    def start(self, *, linked=True):
        dashboard = ui_server.make_server(self.db)
        composer = share_browser.ShareServer(0, db=self.db)
        if linked:
            studio.link(dashboard, composer)
        stop = threading.Event()
        runner = threading.Thread(target=studio.serve_both, args=(dashboard, composer),
                                  kwargs={"wait": lambda threads: stop.wait(20)}, daemon=True)
        runner.start()
        self.addCleanup(runner.join, 10)
        self.addCleanup(stop.set)
        return dashboard, composer

    def dash(self, dashboard, rest, method="GET", **kw):
        return http_call(dashboard.server_address[1], method, f"/{dashboard.token}/{rest}", **kw)

    def comp(self, composer, path, payload=None, *, token=None, origin=None, method="POST"):
        headers = {"Content-Type": "application/json"}
        headers["X-ReachWeaver-Token"] = composer.token if token is None else token
        headers["Origin"] = composer.origin if origin is None else origin
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        return http_call(composer.server_port, method, path, body=body, headers=headers)


class LinkTests(StudioBase):
    def test_both_servers_start_and_each_links_to_the_other_with_its_own_token(self):
        dashboard, composer = self.start()
        status, _, body = self.dash(dashboard, "api/studio")
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertTrue(document["studio"])
        match = COMPOSER.fullmatch(document["composer"])
        self.assertIsNotNone(match)
        self.assertEqual((int(match.group(1)), match.group(2)), (composer.server_port, composer.token))
        status, _, body = http_call(composer.server_port, "GET", "/api/catalog",
                                    headers={"X-ReachWeaver-Token": composer.token})
        self.assertEqual(status, 200)
        link = TOKEN_PATH.fullmatch(json.loads(body)["dashboard"])
        self.assertIsNotNone(link)
        self.assertEqual((int(link.group(1)), link.group(2)), (dashboard.server_address[1], dashboard.token))
        self.assertNotEqual(dashboard.token, composer.token)  # two tokens, never one shared
        # The composer page itself (no token) carries neither URL: links only come from token-gated JSON.
        status, _, page = http_call(composer.server_port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertNotIn(dashboard.token.encode(), page)
        self.assertNotIn(composer.token.encode(), page)
        status, _, index = self.dash(dashboard, "")
        self.assertNotIn(composer.token.encode(), index)

    def test_alone_neither_server_has_a_cross_link(self):
        dashboard, composer = self.start(linked=False)
        self.assertEqual(json.loads(self.dash(dashboard, "api/studio")[2]), {"studio": False, "composer": None})
        catalog = json.loads(http_call(composer.server_port, "GET", "/api/catalog",
                                       headers={"X-ReachWeaver-Token": composer.token})[2])
        self.assertIsNone(catalog["dashboard"])

    def test_studio_route_needs_the_dashboard_token(self):
        dashboard, composer = self.start()
        status, _, body = http_call(dashboard.server_address[1], "GET", "/" + "x" * 32 + "/api/studio")
        self.assertEqual(status, 403)
        self.assertNotIn(composer.token.encode(), body)
        status, _, body = http_call(composer.server_port, "GET", "/api/catalog")  # no token header
        self.assertEqual(status, 403)
        self.assertNotIn(dashboard.token.encode(), body)

    def test_referrer_policy_and_csp_on_both_servers(self):
        dashboard, composer = self.start()
        for status, headers, _ in (self.dash(dashboard, ""), self.dash(dashboard, "api/studio"),
                                   self.dash(dashboard, "app.js"),
                                   http_call(composer.server_port, "GET", "/"),
                                   self.comp(composer, "/api/draft/load", {})):
            self.assertEqual(status, 200)
            self.assertEqual(headers["referrer-policy"], "no-referrer")
            self.assertEqual(headers["cache-control"], "no-store")
            csp = headers["content-security-policy"]
            self.assertIn("default-src 'none'", csp)
            self.assertIn("connect-src 'self'", csp)
            self.assertNotIn("http", csp)  # no third-party (or cross-port) origin is allowed to load or connect

    def test_the_dashboard_stays_read_only_in_studio(self):
        dashboard, _ = self.start()
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                status, headers, _ = self.dash(dashboard, "api/studio", method=method, body=b"{}",
                                               headers={"Content-Type": "application/json"})
                self.assertEqual(status, 405)
                self.assertEqual(headers["allow"], "GET, HEAD")

    def test_the_composer_keeps_its_token_and_origin_checks_in_studio(self):
        dashboard, composer = self.start()
        dash_origin = f"http://127.0.0.1:{dashboard.server_address[1]}"
        for token, origin in ((dashboard.token, None), ("wrong-" + "a" * 40, None), (None, dash_origin),
                              (None, "https://attacker.example"), (None, "null")):
            with self.subTest(token=bool(token), origin=origin):
                status, _, _ = self.comp(composer, "/api/draft/save", {"text": "x"}, token=token, origin=origin)
                self.assertEqual(status, 403)
        self.assertIsNone(store.load_draft())  # nothing refused was written
        self.assertEqual(self.comp(composer, "/api/draft/save", {"text": "ok"})[0], 200)

    def test_shutdown_closes_both_servers(self):
        dashboard = ui_server.make_server(self.db)
        composer = share_browser.ShareServer(0, db=self.db)
        studio.link(dashboard, composer)
        ports = (dashboard.server_address[1], composer.server_port)
        seen = []

        def ctrl_c(threads):
            seen.extend(t.is_alive() for t in threads)
            self.assertEqual(self.dash(dashboard, "api/studio")[0], 200)  # both answer while running
            self.assertEqual(http_call(composer.server_port, "GET", "/")[0], 200)
            raise KeyboardInterrupt

        self.assertEqual(studio.serve_both(dashboard, composer, wait=ctrl_c), 0)
        self.assertEqual(seen, [True, True])
        for port in ports:
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=2).close()


class RunTests(StudioBase):
    def args(self, **kw):
        values = dict(db=self.db, accounts=None, optout=None, port=0, composer_port=0, open=True, home=None)
        values.update(kw)
        return Namespace(**values)

    def run_once(self, args):
        out = io.StringIO()
        with patch.object(studio, "_wait_forever", side_effect=KeyboardInterrupt), \
                patch.object(studio.webbrowser, "open") as opened, contextlib.redirect_stdout(out):
            self.assertEqual(studio.run(args), 0)
        return out.getvalue(), opened

    def test_opens_the_dashboard_when_there_are_posts(self):
        out, opened = self.run_once(self.args())
        url = opened.call_args.args[0]
        self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{20,}/$")
        self.assertIn("Dashboard (read-only): " + url, out)
        self.assertRegex(out, r"Composer \(write a post, copy it into each app yourself\): http://127\.0\.0\.1:\d+/#")
        self.assertIn("Press Ctrl+C to stop both.", out)

    def test_a_brand_new_workspace_gets_an_empty_database_and_opens_the_composer(self):
        fresh = str(self.root / "state" / "new.sqlite")
        out, opened = self.run_once(self.args(db=fresh))
        self.assertTrue(Path(fresh).is_file())
        self.assertIn("Created an empty local database", out)
        self.assertRegex(opened.call_args.args[0], r"^http://127\.0\.0\.1:\d+/#[A-Za-z0-9_-]{30,}$")

    def test_parser_and_catalog_know_the_command(self):
        args = make_parser().parse_args(["studio", "--open", "--port", "0", "--composer-port", "0"])
        self.assertEqual(args.command, "studio")
        from reachweaver import catalog, cli_help, mcp_server
        self.assertIn("interactive", catalog.COMMANDS["studio"]["tags"])
        self.assertIn("studio", mcp_server.NEVER_EXPOSED)
        self.assertEqual(sum("studio" in names for _, names in cli_help.GROUPS), 1)
        self.assertIsNone(studio.SEND_PATH)


class IdeaAndSeedTests(StudioBase):
    def setUp(self):
        super().setUp()
        self.inbox = ideas.inbox_path(self.db)
        self.idea = ideas.add(self.inbox, "Synthetic idea: one caption, a version per network")

    def test_idea_route_reads_one_open_idea_and_changes_nothing(self):
        _, composer = self.start(linked=False)
        before = self.inbox.read_bytes()
        status, _, body = self.comp(composer, "/api/idea", {"idea": self.idea["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["idea"], {"id": self.idea["id"], "text": self.idea["text"]})
        self.assertEqual(self.inbox.read_bytes(), before)
        for bad in ("idea-zzzzzzzz", "../ideas.json", 7, "idea-00000000"):
            with self.subTest(bad=bad):
                self.assertEqual(self.comp(composer, "/api/idea", {"idea": bad})[0], 422)
        self.assertEqual(self.comp(composer, "/api/idea", {"idea": self.idea["id"], "x": 1})[0], 422)
        ideas.drop(self.inbox, self.idea["id"])
        self.assertEqual(self.comp(composer, "/api/idea", {"idea": self.idea["id"]})[0], 422)  # open ideas only
        self.assertEqual(self.comp(composer, "/api/idea", {"idea": self.idea["id"]}, token="w" * 40)[0], 403)

    def test_seed_starts_an_empty_draft_but_never_replaces_an_unsaved_one(self):
        result = share_browser.seed_draft(*share_browser.seed_from(None, self.idea["id"], self.db))
        self.assertTrue(result["seeded"])
        self.assertEqual(store.load_draft()["text"], self.idea["text"])
        store.save_draft({"text": "My own unsaved words", "platforms": ["threads"]})
        result = share_browser.seed_draft("Other text", "your --text")
        self.assertFalse(result["seeded"])
        self.assertIn("kept", result["message"])
        self.assertEqual(store.load_draft()["text"], "My own unsaved words")
        self.assertEqual(result["offer"], {"text": "Other text", "label": "your --text"})

    def test_an_offer_is_delivered_once_with_the_draft(self):
        store.save_draft({"text": "Unsaved"})
        _, composer = self.start(linked=False)
        with contextlib.redirect_stdout(io.StringIO()) as said:
            share_browser.apply_seed(composer, ("Seeded text", "your --text"))
        self.assertIn("was kept", said.getvalue())
        first = json.loads(self.comp(composer, "/api/draft/load", {})[2])
        self.assertEqual(first["offer"]["text"], "Seeded text")
        self.assertEqual(first["draft"]["text"], "Unsaved")
        self.assertIsNone(json.loads(self.comp(composer, "/api/draft/load", {})[2])["offer"])

    def test_seed_refusals(self):
        with self.assertRaises(ValidationError):
            share_browser.seed_from("text", self.idea["id"], self.db)
        with self.assertRaises(ValidationError):
            share_browser.seed_from(None, "idea-00000000", self.db)
        with self.assertRaises(ValidationError):
            share_browser.seed_draft("   ", "x")
        with self.assertRaises(ValidationError):
            share_browser.seed_draft("x" * (share_browser.SEED_TEXT_BYTES + 1), "x")
        self.assertIsNone(store.load_draft())

    def test_share_web_accepts_idea_and_text_but_idea_needs_web(self):
        parser = make_parser()
        args = parser.parse_args(["share", "--web", "--idea", self.idea["id"]])
        self.assertEqual(args.idea, self.idea["id"])
        from reachweaver import share
        with self.assertRaises(ValidationError):
            share.run(parser.parse_args(["share", "--idea", self.idea["id"], "--platform", "threads"]))
        with patch.object(share_browser, "serve", return_value=0) as serve:
            self.assertEqual(share.run(parser.parse_args(["share", "--web", "--text", "Hello"])), 0)
        self.assertEqual(serve.call_args.kwargs["text"], "Hello")


class PageWiringTests(unittest.TestCase):
    """Static checks of the shipped pages: the browser checks run in the real-browser verification."""

    def test_dashboard_links_open_the_composer_only_in_studio_and_without_a_referrer(self):
        js = ui_assets.APP_JS
        self.assertIn('api("studio")', js)
        self.assertIn('rel: COMPOSER ? "noopener noreferrer" : null', js)
        self.assertIn('cta.rel = "noopener noreferrer"', js)
        self.assertIn('command("reachweaver share --web --open")', js)  # the dashboard alone keeps the command
        self.assertIn("COMPOSER_URL.test(d.composer)", js)  # only a loopback composer URL becomes a link

    def test_composer_links_to_results_without_a_referrer_and_preselects_a_single_account(self):
        html = share_browser.HTML
        self.assertIn('link.rel = "noopener noreferrer"', html)
        self.assertIn("DASHBOARD_URL.test(data.dashboard)", html)
        self.assertIn("if (mine.length === 1) select.value = mine[0].id;", html)
        self.assertIn('select.appendChild(text("option", "Not sure / skip"))', html)  # still changeable
        self.assertIn('<div class="picked hidden" id="picked"', html)
        self.assertIn("previewedKey !== null", html)  # "Your post changed" only against the last preview


if __name__ == "__main__":
    unittest.main()
