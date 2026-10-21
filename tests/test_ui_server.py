"""Dashboard server: loopback only, tokenised, GET only, read-only, strict headers. Offline."""
from __future__ import annotations

import contextlib
import socket
import hashlib
import http.client
import io
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import ui_assets, ui_server
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, claim_sending, connect, transition, uncertain_send
from test_ui_data import FORBIDDEN_NAMES, HOSTILE, NOW, SECRET, referenced_names

BSKY = {"id": "u-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_U_BSKY", "handle": "o.bsky.social"}


class ServerBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = self.base / "state" / "u.sqlite"
        self.config = self.base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [BSKY]}), encoding="utf-8")
        env = patch.dict(os.environ, {"REACHWEAVER_U_BSKY_APP_PASSWORD": SECRET})
        env.start()
        self.addCleanup(env.stop)
        with connect(str(self.db)) as con:
            self.draft = add_post(con, "u-bluesky", "bluesky", "Draft " + HOSTILE)
            self.review = add_post(con, "u-bluesky", "bluesky", "Ambiguous")
            transition(con, self.review, "approve", approver="owner")
            claim_sending(con, self.review, now=NOW)
            uncertain_send(con, self.review, "network error " + SECRET)
        self.server = ui_server.make_server(self.db, accounts_path=str(self.config), optout_path=None)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.token = self.server.token

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def get(self, rest, **kw):
        return self.request("GET", f"/{self.token}/{rest}", **kw)

    def api(self, rest):
        status, headers, body = self.get("api/" + rest)
        return status, json.loads(body)


class BindingTests(unittest.TestCase):
    def test_non_loopback_hosts_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "x.sqlite"
            db.write_bytes(b"")
            for host in ("0.0.0.0", "::", "192.168.1.5", "example.com", ""):
                with self.assertRaises(ValidationError, msg=host):
                    ui_server.make_server(db, host=host)

    def test_missing_database_is_refused_and_never_created(self):
        missing = Path(tempfile.gettempdir()) / "rw-ui-missing" / "nope.sqlite"
        with self.assertRaises(ValidationError):
            ui_server.make_server(missing)
        self.assertFalse(missing.parent.exists())

    def test_binds_loopback_on_a_free_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "u.sqlite"
            with connect(str(db)):
                pass
            server = ui_server.make_server(db)
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                self.assertGreater(server.server_address[1], 0)
                self.assertGreaterEqual(len(server.token), 24)
                self.assertTrue(ui_server.url_for(server).startswith("http://127.0.0.1:"))
            finally:
                server.server_close()


class AccessControlTests(ServerBase):
    def test_token_is_required(self):
        for path in ("/", "/app.js", "/api/overview", "/wrong-token/", f"/{self.token}x/", f"/{self.token[:-1]}/"):
            status, _, body = self.request("GET", path)
            self.assertEqual(status, 403, path)
            self.assertNotIn(b"ReachWeaver Dashboard", body)

    def test_page_and_assets_are_served_with_strict_headers(self):
        for rest, kind in (("", "text/html"), ("app.css", "text/css"), ("app.js", "text/javascript")):
            status, headers, body = self.get(rest)
            self.assertEqual(status, 200, rest)
            self.assertTrue(headers["Content-Type"].startswith(kind), rest)
            self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
            self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            self.assertTrue(body)

    def test_unexpected_host_header_is_refused(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", ""):
            status, _, _ = self.get("api/overview", headers={"Host": host})
            self.assertEqual(status, 403, host)

    def test_localhost_name_is_allowed_as_host(self):
        status, _, _ = self.get("api/accounts", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_cross_origin_requests_are_refused(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            status, _, _ = self.get("api/overview", headers=headers)
            self.assertEqual(status, 403, headers)
        status, _, _ = self.get("api/overview", headers={"Origin": f"http://127.0.0.1:{self.port}",
                                                         "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)

    def test_cross_site_navigation_is_allowed_but_still_needs_the_token(self):
        nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        self.assertEqual(self.get("", headers=nav)[0], 200)
        self.assertEqual(self.request("GET", "/wrong/", headers=nav)[0], 403)
        for mode in ("cors", "no-cors", "same-origin"):
            status, _, _ = self.get("api/overview", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": mode})
            self.assertEqual(status, 403, mode)

    def test_only_get_and_head_are_accepted(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            status, headers, _ = self.request(method, f"/{self.token}/api/overview", body=b"{}")
            self.assertEqual(status, 405, method)
            self.assertEqual(headers["Allow"], "GET, HEAD")
        status, headers, body = self.request("HEAD", f"/{self.token}/api/overview")
        self.assertEqual((status, body), (200, b""))

    def test_no_route_can_change_state(self):
        for rest in ("api/approve", "api/publish", "api/pause", "api/posts/x/approve", "api/schedule"):
            status, _ = self.api(rest)
            self.assertIn(status, (400, 404), rest)


class ReviewRegressionTests(ServerBase):
    """Findings from the independent review of the first version."""

    def raw(self, request: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as conn:
            conn.sendall(request)
            conn.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = conn.recv(4096)
                if not data:
                    return b"".join(chunks)
                chunks.append(data)

    def test_token_without_trailing_slash_redirects_to_the_slashed_url(self):
        status, headers, _ = self.request("GET", f"/{self.token}")
        self.assertEqual(status, 301)
        self.assertEqual(headers["Location"], f"/{self.token}/")
        self.assertEqual(self.request("GET", "/wrong")[0], 403)

    def test_too_many_query_fields_and_unknown_region_are_client_errors_not_500(self):
        many = "&".join(f"a{i}=1" for i in range(30))
        self.assertEqual(self.api("posts?" + many)[0], 400)
        status, body = self.api("distribution?region=atlantis&goal=awareness")
        self.assertEqual(status, 400)
        self.assertIn("Unknown region", body["error"])

    def test_malformed_request_line_gets_a_400_without_a_traceback(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            reply = self.raw(f"GET http://[/x HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
        self.assertIn(b"400", reply.split(b"\r\n")[0])
        self.assertEqual(err.getvalue(), "")

    def test_one_request_per_connection_and_idle_connections_time_out(self):
        self.assertEqual(ui_server.Handler.protocol_version, "HTTP/1.0")
        self.assertLessEqual(ui_server.Handler.timeout, 30)
        reply = self.raw(f"GET /{self.token}/api/accounts HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n"
                         f"GET /{self.token}/api/overview HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
        self.assertEqual(reply.count(b"HTTP/1."), 1, "a second request must not ride the same connection")

    def test_corrupt_database_is_refused_before_a_url_is_printed(self):
        junk = self.base / "junk.sqlite"
        junk.write_bytes(b"this is not a database" * 100)
        with self.assertRaises(ValidationError):
            ui_server.make_server(junk)


class ApiTests(ServerBase):
    def test_overview_posts_detail_accounts(self):
        status, overview = self.api("overview")
        self.assertEqual(status, 200)
        self.assertEqual(overview["report"], "reachweaver_ops_summary")
        status, posts = self.api("posts")
        self.assertEqual([p["status"] for p in posts["posts"]], ["needs_review", "draft"])
        status, detail = self.api("posts/" + self.draft)
        self.assertEqual(status, 200)
        self.assertIn(HOSTILE, detail["content"])
        status, accounts = self.api("accounts")
        self.assertEqual(accounts["accounts"][0]["id"], "u-bluesky")

    def test_next_steps_endpoint_matches_the_start_guide(self):
        status, guide = self.api("next")
        self.assertEqual(status, 200)
        self.assertIn(guide["state"], {"needs_review", "drafts", "queued", "paused", "empty", "idle"})
        self.assertTrue(guide["steps"])
        self.assertNotIn("reachweaver ui", [step["command"] for step in guide["steps"]])
        text = json.dumps(guide)
        self.assertNotRegex(text, r"reachweaver approve|--live")
        self.assertNotIn(SECRET, text)

    def test_filters_and_validation(self):
        self.assertEqual(self.api("posts?status=draft")[1]["total"], 1)
        self.assertEqual(self.api("posts?status=draft&account=nobody")[1]["total"], 0)
        self.assertEqual(self.api("posts?status=" + "%3Cscript%3E")[0], 400)
        self.assertEqual(self.api("posts/nope")[0], 404)
        self.assertEqual(self.api("posts/..%2f..%2fetc%2fpasswd")[0], 404)
        self.assertEqual(self.api("posts/" + "a" * 200)[0], 404)

    def test_distribution_is_offline_research(self):
        status, meta = self.api("distribution/markets")
        self.assertEqual(status, 200)
        self.assertIn("awareness", meta["goals"])
        status, plan = self.api("distribution?region=india&goal=awareness&limit=3")
        self.assertEqual(status, 200)
        self.assertEqual(plan["goal"], "awareness")
        self.assertEqual(self.api("distribution?region=india&goal=nonsense")[0], 400)
        self.assertEqual(self.api("distribution?region=india&goal=awareness&limit=x")[0], 400)

    def test_missing_accounts_file_still_serves(self):
        self.server.accounts_path = str(self.base / "absent.json")
        status, accounts = self.api("accounts")
        self.assertEqual(status, 200)
        self.assertTrue(accounts["error"])
        self.assertEqual(self.api("overview")[0], 200)

    def test_no_secret_in_any_response(self):
        blobs = []
        for rest in ("api/overview", "api/posts", "api/accounts", f"api/posts/{self.draft}",
                     f"api/posts/{self.review}", "", "app.js"):
            blobs.append(self.get(rest)[2].decode("utf-8", "replace"))
        self.assertNotIn(SECRET, "".join(blobs))

    def test_serving_every_endpoint_leaves_the_database_unchanged(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        names = sorted(p.name for p in self.db.parent.iterdir())
        for rest in ("api/overview", "api/posts", "api/accounts", f"api/posts/{self.draft}",
                     f"api/posts/{self.review}", "api/distribution/markets"):
            self.assertEqual(self.get(rest)[0], 200, rest)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir()), names)

    def test_search_and_schedule_days(self):
        status, found = self.api("search?q=draft")
        self.assertEqual(status, 200)
        self.assertEqual([p["id"] for p in found["posts"]], [self.draft])
        self.assertIn(HOSTILE, found["posts"][0]["excerpt"])  # data; the page inserts it as text
        self.assertEqual(self.api("search?q=")[0], 400)
        self.assertEqual(self.api("search?q=%01")[0], 400)
        self.assertEqual(self.api("search?q=" + "x" * 101)[0], 400)
        self.assertEqual(self.api("schedule?days=42")[1]["days"], 42)
        for bad in ("0", "63", "x"):
            self.assertEqual(self.api("schedule?days=" + bad)[0], 400, bad)
        self.assertNotIn(SECRET, json.dumps(self.api("search?q=ambiguous")[1]))

    def test_results_and_campaign_endpoints(self):
        status, results = self.api("results")
        self.assertEqual(status, 200)
        self.assertEqual(results["posts"], [])  # nothing published in this fixture
        self.assertIn("never zero", results["note"])
        self.assertEqual(self.api("results?platform=%3Cx%3E")[0], 400)
        status, campaigns = self.api("campaigns")
        self.assertEqual(status, 200)
        self.assertEqual(campaigns["campaigns"], [])
        self.assertEqual(self.api("campaign?name=nope")[0], 404)
        self.assertEqual(self.api("campaign?name=%01bad")[0], 400)
        self.assertEqual(self.api("campaign")[0], 400)
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for rest in ("api/results", "api/campaigns", "api/campaign?name=nope"):
            self.get(rest)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_schedule_posted_safety_and_suggest_endpoints(self):
        status, schedule = self.api("schedule")
        self.assertEqual(status, 200)
        self.assertLessEqual({"overdue", "upcoming", "unscheduled", "sending", "later"}, set(schedule))
        status, journal = self.api("journal")
        self.assertEqual(status, 200)
        self.assertEqual([e["id"] for e in journal["entries"]], [self.review])  # possibly posted, so it is listed
        self.assertEqual(self.api("journal?account=nobody")[1]["entries"], [])
        self.assertEqual(self.api("journal?platform=%3Cx%3E")[0], 400)
        status, safety = self.api("safety")
        self.assertEqual(status, 200)
        self.assertIn(safety["ledger"]["status"], ("ok", "absent", "not_checked", "broken", "unavailable"))
        self.assertTrue(safety["checked_at"])
        status, suggest = self.api("suggest")
        self.assertEqual(status, 200)
        self.assertIn("actions", suggest)

    def test_new_routes_are_get_only(self):
        for rest in ("api/schedule", "api/journal", "api/safety", "api/suggest", "api/attention"):
            status, headers, _ = self.request("POST", f"/{self.token}/{rest}", body=b"x")
            self.assertEqual(status, 405, rest)

    def test_the_folder_check_is_reused_for_a_minute_then_rechecked(self):
        with patch("reachweaver.ui_server.ui_data.safety_view", wraps=ui_server.ui_data.safety_view) as view:
            self.api("safety")
            self.api("safety")
            self.assertEqual(view.call_count, 1)
            self.server.safety_cache = (self.server.safety_cache[0] - ui_server.SAFETY_TTL - 1, self.server.safety_cache[1])
            self.api("safety")
            self.assertEqual(view.call_count, 2)

    def test_new_endpoints_carry_no_secret_and_leave_the_database_unchanged(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        names = sorted(p.name for p in self.db.parent.iterdir())
        blobs = []
        for rest in ("api/schedule", "api/journal", "api/safety", "api/suggest", "api/attention"):
            status, _, body = self.get(rest)
            self.assertEqual(status, 200, rest)
            blobs.append(body.decode("utf-8", "replace"))
        self.assertNotIn(SECRET, "".join(blobs))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir()), names)

    def test_database_removed_while_running_is_an_error_not_a_creation(self):
        self.db.unlink()
        for suffix in ("-wal", "-shm"):
            self.db.with_name(self.db.name + suffix).unlink(missing_ok=True)
        status, body = self.api("overview")
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        self.assertFalse(self.db.exists())

    def test_requests_are_not_logged(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.get("api/overview")
        self.assertEqual(err.getvalue(), "")


class AssetTests(unittest.TestCase):
    def test_page_has_no_inline_code_or_remote_resources(self):
        html = ui_assets.INDEX_HTML
        self.assertEqual(re.findall(r"<script(?![^>]*\ssrc=)", html), [])
        self.assertNotIn("<style", html)
        self.assertNotIn(" style=", html)
        self.assertNotRegex(html, r"\son[a-z]+=")
        self.assertEqual(re.findall(r"https?://", html + ui_assets.APP_CSS), [])

    def test_script_never_builds_html_from_data(self):
        js = ui_assets.APP_JS
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                       "setTimeout(\"", "javascript:"):
            self.assertNotIn(banned, js, banned)
        self.assertNotRegex(js, r"""\.href\s*=\s*[^;]*\+""")  # no concatenated navigation targets

    def test_browser_storage_is_wrapped_and_holds_only_preferences(self):
        js = ui_assets.APP_JS
        for use in re.finditer(r"localStorage", js):
            window = js[max(0, use.start() - 80):use.start()]
            self.assertIn("try {", window)
        # three preferences and the unsent text of the Claude composer (never sent anywhere but the local server)
        self.assertEqual(sorted(set(re.findall(r'"(rw-[a-z]+)"', js))), ["rw-alerts", "rw-auto", "rw-draft", "rw-theme"])

    def test_router_cannot_reach_object_prototype_or_throw_on_bad_escapes(self):
        js = ui_assets.APP_JS
        self.assertIn("Object.create(null)", js)
        self.assertRegex(js, r"try \{ arg = decodeURIComponent")

    def test_skip_link_does_not_navigate(self):
        self.assertIn('id="skip"', ui_assets.INDEX_HTML)
        self.assertRegex(ui_assets.APP_JS, r'getElementById\("skip"\)\.addEventListener\("click", e => \{ e\.preventDefault\(\)')

    def test_checkbox_focus_does_not_block_auto_refresh_and_open_work_is_not_wiped(self):
        js = ui_assets.APP_JS
        self.assertIn('el.type !== "checkbox"', js)
        for guard in ('"details[open]"', "search.value", "dialog[open]"):
            self.assertIn(guard, js)

    def test_accessibility_basics(self):
        html, js, css = ui_assets.INDEX_HTML, ui_assets.APP_JS, ui_assets.APP_CSS
        self.assertNotIn('aria-label="Colour theme"', html)  # the visible text is the name
        self.assertNotIn('id="updated" class="muted" role="status"', html)  # do not announce every refresh
        self.assertIn("showModal", js)  # native dialog: focus trap and Esc for free
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus-visible", css)
        self.assertIn('"aria-label": "Copy command: "', js)

    def test_narrow_screens_get_stacked_cards_with_labels(self):
        self.assertIn("max-width: 640px", ui_assets.APP_CSS)
        self.assertIn("td[data-label]::before", ui_assets.APP_CSS)
        self.assertIn('setAttribute("data-label"', ui_assets.APP_JS)

    @unittest.skipUnless(__import__("shutil").which("node"), "node not installed")
    def test_script_parses(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.js"
            path.write_text(ui_assets.APP_JS, encoding="utf-8")
            subprocess.run(["node", "--check", str(path)], check=True, timeout=30)

    def test_post_page_shows_the_repeat_check_and_names_the_terminal_flag(self):
        js = ui_assets.APP_JS
        self.assertIn("p.repeat_check", js)
        self.assertIn("--allow-repeat", js)

    def test_an_unset_schedule_is_not_called_unknown(self):
        js = ui_assets.APP_JS
        self.assertIn('when(p.scheduled_at, "not scheduled")', js)
        self.assertIn('p.approved_at || "not approved"', js)

    def test_accounts_page_says_ready_or_missing_and_that_credentials_are_for_live_sends(self):
        js = ui_assets.APP_JS
        self.assertIn("all set", js)
        self.assertIn(" missing", js)
        self.assertIn("Credentials are needed only for live sending", js)

    def test_blocked_banner_explains_a_credentials_only_block(self):
        js = ui_assets.APP_JS
        self.assertIn('r.code === "credentials_missing"', js)
        self.assertIn("drafting, review and dry runs still work", js)

    def test_tab_title_badge_is_not_overwritten_by_render(self):
        self.assertEqual(ui_assets.APP_JS.count("document.title ="), 1)

    def test_auto_refresh_is_opt_in_and_skips_while_typing(self):
        js = ui_assets.APP_JS
        self.assertIn('store("rw-auto") === "1"', js)
        self.assertIn("typing()", js)
        self.assertIn('visibilityState === "visible"', js)

    def test_schedule_and_posted_pages_are_routed_and_reachable_by_keyboard(self):
        html, js = ui_assets.INDEX_HTML, ui_assets.APP_JS
        for route in ("schedule", "posted"):
            self.assertIn(f'href="#/{route}" data-route="{route}"', html)
        self.assertIn('schedule: ["schedule", viewSchedule], posted: ["posted", viewPosted]', js)
        self.assertIn('s: "#/schedule", p: "#/posted"', js)
        self.assertIn("g then o / q / s / p", js)

    def test_alerts_are_opt_in_and_carry_counts_only(self):
        js = ui_assets.APP_JS
        self.assertIn('id="alerts"', ui_assets.INDEX_HTML)
        self.assertIn("Notification.requestPermission()", js)
        call = js[js.index("new Notification("):js.index("new Notification(") + 120]
        self.assertNotRegex(call, r"excerpt|content|text|account")  # nothing private on a lock screen
        self.assertIn('store("rw-alerts") === "1" && typeof Notification !== "undefined" && Notification.permission === "granted"', js)

    def test_month_calendar_and_global_search_are_wired(self):
        js = ui_assets.APP_JS
        self.assertIn('href: "#/schedule?view=month"', js)
        self.assertIn("function calendar(d, picked)", js)
        self.assertIn('search: ["search", viewSearch]', js)
        self.assertIn('id="gq"', ui_assets.INDEX_HTML)
        self.assertNotIn("<form", ui_assets.INDEX_HTML)  # the CSP forbids form submission; Enter is handled in script
        self.assertIn('document.getElementById("search") || document.getElementById("gq")', js)

    def test_results_and_campaigns_pages_are_routed(self):
        html, js = ui_assets.INDEX_HTML, ui_assets.APP_JS
        for route in ("results", "campaigns"):
            self.assertIn(f'href="#/{route}" data-route="{route}"', html)
        self.assertIn('campaign: ["campaigns", viewCampaign]', js)  # the detail page highlights Campaigns
        self.assertIn('r: "#/results", m: "#/campaigns"', js)
        self.assertIn('"not recorded"', js)
        self.assertIn('(platform estimate)', js)  # estimates are labelled, never added to exact values

    def test_enter_only_follows_in_page_links_and_rows_reset_per_page(self):
        js = ui_assets.APP_JS
        self.assertIn("""row.querySelector('a[href^="#/"]')""", js)  # a hand-shared row's first link may be remote
        self.assertIn("queueRows = []; queueSel = -1;", js)

    def test_overview_checks_load_late_and_cannot_break_the_page(self):
        js = ui_assets.APP_JS
        self.assertIn('lateCard("Files and approvals", safetyNodes)', js)
        self.assertIn("Could not load this check", js)
        self.assertIn("not a reach prediction", js)

    def test_a_sent_post_does_not_read_as_never_approved(self):
        js = ui_assets.APP_JS
        self.assertIn('const SENT_STATUSES = ["sending", "published", "needs_review"];', js)
        self.assertIn('approval.binding = "not re-checked after the send started"', js)
        self.assertIn("kv(approval)", js)  # a copy: the API document (the `show` contract) is unchanged

    def test_an_unreadable_approval_limit_is_not_shown_as_zero_hours(self):
        js = ui_assets.APP_JS
        self.assertIn("d.approval_max_age_hours === 0", js)
        self.assertIn("every approval counts as expired (fail-closed)", js)
        for code in ("instagram_authorization_expiry_invalid", "threads_authorization_expiry_invalid"):
            self.assertIn(code + ":", js)

    def test_print_stylesheet_hides_controls(self):
        css = ui_assets.APP_CSS
        self.assertIn("@media print", css)
        self.assertRegex(css, r"@media print \{[^@]*\.top, footer, \.pager")
        js = ui_assets.APP_JS  # closed <details> never print, so the approval binding is opened for printing
        self.assertIn('addEventListener("beforeprint"', js)
        self.assertIn('addEventListener("afterprint"', js)

    def test_app_shell_has_counts_drawer_and_bottom_tabs(self):
        html, js = ui_assets.INDEX_HTML, ui_assets.APP_JS
        for name in ("overview", "queue", "schedule"):
            self.assertIn(f'data-count="{name}"', html)
        self.assertIn('class="tabs"', html)
        self.assertIn('id="more"', html)
        self.assertIn('api("attention")', js)
        self.assertIn("side.inert", js)  # the closed drawer is out of the tab order on a phone

    def test_settings_popover_has_a_fallback_and_pauses_auto_refresh(self):
        self.assertIn('id="settings" class="pop" popover', ui_assets.INDEX_HTML)
        js = ui_assets.APP_JS
        self.assertIn('typeof settings.togglePopover !== "function"', js)
        self.assertIn("popOpen()", js[js.index("function holdRefresh"):js.index("function setAuto")])

    def test_command_palette_only_navigates(self):
        js = ui_assets.APP_JS
        palette = js[js.index("// ---- command palette"):js.index("// ---- keyboard shortcuts")]
        self.assertIn('api("posts")', palette)
        self.assertIn("location.hash = target", palette)
        self.assertNotRegex(palette, r"fetch\(|method")
        self.assertIn('e.key.toLowerCase() === "k"', js)

    def test_preview_and_sparkline_use_text_nodes_and_exact_values_only(self):
        js = ui_assets.APP_JS
        self.assertIn("function previewText(text)", js)
        self.assertIn("The platform's own rendering", js)  # the look-alike says it is not the platform
        spark = js[js.index("function spark(results)"):js.index("const EVENT_TONE")]
        self.assertIn('r.metrics[k].state === "exact"', spark)
        self.assertIn(">= 2", spark)  # no line from a single point

    def test_results_hides_metrics_nobody_recorded_but_names_them(self):
        js = ui_assets.APP_JS
        self.assertIn("They stay unknown, not zero.", js)

    def test_tab_icon_is_static_svg_without_script(self):
        svg = ui_assets.ICON_SVG
        self.assertTrue(svg.startswith("<svg"))
        self.assertNotRegex(svg.lower(), r"<script|\son[a-z]+=|href=")
        self.assertIn('href="icon.svg"', ui_assets.INDEX_HTML)
        self.assertEqual(ui_server.STATIC["icon.svg"][0], "image/svg+xml")

    def test_icons_are_drawn_here(self):
        js = ui_assets.APP_JS
        self.assertIn("const ICONS = {", js)
        self.assertNotIn("<svg", ui_assets.INDEX_HTML)  # icons are built with createElementNS, never parsed

    def test_script_has_no_write_requests(self):
        js = ui_assets.APP_JS
        self.assertNotRegex(js, r"""method\s*:\s*["'](POST|PUT|DELETE|PATCH)""")
        self.assertEqual(re.findall(r"XMLHttpRequest|sendBeacon|WebSocket|EventSource", js), [])


class StaticSafetyTests(unittest.TestCase):
    def test_server_and_assets_name_no_write_send_or_credential_api(self):
        # urllib.parse only parses URLs; urlopen stays forbidden
        for module in (ui_server, ui_assets):
            self.assertEqual(referenced_names(module.__file__) & (FORBIDDEN_NAMES - {"os", "urllib"}), set(), module.__name__)


class CommandTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_ui_refuses_a_missing_database_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "none.sqlite"
            code, out, err = self.run_main("--db", str(db), "ui")
            self.assertEqual(code, 2)
            self.assertIn("does not exist", err)
            self.assertFalse(db.parent.exists())

    def test_ui_prints_a_tokenised_loopback_url_and_stops_on_ctrl_c(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "u.sqlite"
            with connect(str(db)):
                pass
            with patch.object(ui_server.DashboardServer, "serve_forever", side_effect=KeyboardInterrupt):
                code, out, err = self.run_main("--db", str(db), "ui")
            self.assertEqual(code, 0, err)
            self.assertRegex(out, r"http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{24,}/")

    def test_ui_has_no_host_option(self):
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            self.run_main("ui", "--host", "0.0.0.0")
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
