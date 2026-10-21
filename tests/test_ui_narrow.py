"""Dashboard on a phone-sized screen: no page is wider than the screen, and no script error. Offline.

It drives a local headless Chrome over its DevTools pipe (``--remote-debugging-pipe``, standard library only) against
a dashboard on 127.0.0.1 with a synthetic database, and is skipped when no Chrome or Chromium is installed. The
browser is started with its background networking, sync and component updates off; the only page it opens is the
loopback dashboard.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from reachweaver import ui_server
from reachweaver.storage import add_post, claim_sending, connect, record_manual_publish, transition, uncertain_send

BROWSER = next((shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
                if shutil.which(name)), None)
LONG = "x" * 120  # one unbroken word: the kind of text that used to push a phone page sideways
ROUTES = ("#/", "#/queue", "#/schedule", "#/ideas", "#/claude", "#/posted", "#/results", "#/results?since=all",
          "#/campaigns", "#/accounts", "#/distribution", "#/search?q=mugs", "#/write")
# Visit each page, wait until it has rendered, and measure: the document must not be wider than the screen.
MEASURE = """(async (routes, extra) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = [];
  for (const r of routes.concat(extra)) {
    location.hash = r;
    for (let i = 0; i < 40; i++) { await sleep(100); if (!document.querySelector("main[aria-busy], main .sk")) break; }
    await sleep(400);  // late cards (Coming up, Files and approvals) fill in after the page
    const doc = document.documentElement;
    const wide = [...document.querySelectorAll("main *")].filter(e => e.getBoundingClientRect().right > doc.clientWidth + 1)
      .filter(e => ![...e.children].some(c => c.getBoundingClientRect().right > doc.clientWidth + 1)).slice(0, 3)
      .map(e => e.tagName.toLowerCase() + (e.className && typeof e.className === "string" ? "." + e.className.split(" ").join(".") : ""));
    out.push({ route: r, scroll: doc.scrollWidth, client: doc.clientWidth, wide });
  }
  return out;
})"""


class Pipe:
    """Chrome DevTools Protocol over the pipe Chrome opens on its file descriptors 3 (in) and 4 (out)."""

    def __init__(self, write_fd: int, read_fd: int):
        self.out = os.fdopen(write_fd, "wb", buffering=0)
        self.inp = os.fdopen(read_fd, "rb", buffering=0)
        self.buffer = b""
        self.next_id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 60) -> dict:
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.out.write(json.dumps(message).encode() + b"\0")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            reply = self._read()
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise AssertionError(f"{method}: {reply['error']}")
                return reply["result"]
            self.events.append(reply)
        raise AssertionError(f"{method}: no reply in {timeout} s")

    def _read(self) -> dict:
        while b"\0" not in self.buffer:
            chunk = self.inp.read(65536)
            if not chunk:
                raise AssertionError("the browser closed its DevTools pipe")
            self.buffer += chunk
        raw, self.buffer = self.buffer.split(b"\0", 1)
        return json.loads(raw)


@unittest.skipUnless(BROWSER and sys.platform.startswith("linux"), "needs Chrome or Chromium on Linux")
class NarrowScreenTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        db = self.base / "state" / "u.sqlite"
        config = self.base / "accounts.json"
        config.write_text(json.dumps({"accounts": [
            {"id": "an-account-with-a-rather-long-name", "platform": "bluesky", "env_prefix": "REACHWEAVER_N_BSKY",
             "handle": "MixedCase.example.social"}]}), encoding="utf-8")
        with connect(str(db)) as con:
            account = "an-account-with-a-rather-long-name"
            add_post(con, account, "bluesky", "Speckled mugs draft " + LONG, "a-campaign-name-" + LONG[:40])
            approved = add_post(con, account, "bluesky", "Approved and waiting " + LONG)
            transition(con, approved, "approve", approver="owner")
            review = add_post(con, account, "bluesky", "Ended in an unknown state " + LONG)
            transition(con, review, "approve", approver="owner")
            claim_sending(con, review, now="2026-10-08T10:00:00+00:00")
            uncertain_send(con, review, "network error " + LONG)
            self.manual = record_manual_publish(con, platform="bluesky", url="https://bsky.app/profile/o/post/1",
                                                account_id=account, content="New speckled mugs " + LONG,
                                                posted_at="2026-10-08T09:00:00+00:00", note="kiln " + LONG)["post_id"]
            self.draft_id = approved
        self.server = ui_server.make_server(db, accounts_path=str(config), optout_path=None)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def browse(self, width: int) -> tuple[list[dict], list[dict]]:
        to_chrome_r, to_chrome_w = os.pipe()
        from_chrome_r, from_chrome_w = os.pipe()

        def wire():  # Chrome reads commands on fd 3 and writes replies on fd 4 (moved high first: no clash)
            high_in, high_out = fcntl.fcntl(to_chrome_r, fcntl.F_DUPFD, 10), fcntl.fcntl(from_chrome_w, fcntl.F_DUPFD, 10)
            os.dup2(high_in, 3)
            os.dup2(high_out, 4)
        profile = self.base / f"profile-{width}"
        proc = subprocess.Popen(  # noqa: S603 - a fixed local browser binary, loopback page only
            [BROWSER, "--headless=new", "--remote-debugging-pipe", f"--user-data-dir={profile}", "--no-first-run",
             "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
             "--disable-component-update", "--disable-extensions", "--disable-gpu", "--mute-audio", "about:blank"],
            pass_fds=(3, 4, to_chrome_r, from_chrome_w), preexec_fn=wire, stdout=subprocess.DEVNULL,  # noqa: PLW1509
            stderr=subprocess.DEVNULL)
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        pipe = Pipe(to_chrome_w, from_chrome_r)
        try:
            try:
                target = pipe.call("Target.createTarget", {"url": "about:blank"}, timeout=30)["targetId"]
            except (AssertionError, OSError) as exc:  # e.g. a sandbox the machine does not allow
                self.skipTest(f"the browser did not start: {exc}")
            session = pipe.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            pipe.call("Runtime.enable", session=session)
            pipe.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 812, "deviceScaleFactor": 1,
                                                             "mobile": width < 700}, session=session)
            pipe.call("Page.navigate", {"url": ui_server.url_for(self.server)}, session=session)
            time.sleep(1.5)
            extra = [f"#/post/{self.manual}", f"#/post/{self.draft_id}"]
            found = pipe.call("Runtime.evaluate", {"expression": f"{MEASURE}({json.dumps(list(ROUTES))}, {json.dumps(extra)})",
                                                    "awaitPromise": True, "returnByValue": True}, session=session)
            errors = [e for e in pipe.events if e.get("method") == "Runtime.exceptionThrown"
                      or (e.get("method") == "Runtime.consoleAPICalled" and e["params"].get("type") == "error")]
            return found["result"]["value"], errors
        finally:
            try:
                pipe.call("Browser.close", timeout=10)
            except (AssertionError, OSError):
                pass
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            pipe.out.close()
            pipe.inp.close()

    def test_no_page_is_wider_than_a_phone_or_throws(self):
        for width in (375, 390):
            pages, errors = self.browse(width)
            self.assertEqual(len(pages), len(ROUTES) + 2)
            wide = [p for p in pages if p["scroll"] > p["client"]]
            self.assertEqual(wide, [], f"pages wider than {width} px")
            self.assertEqual(errors, [], "script errors")


if __name__ == "__main__":
    unittest.main()
