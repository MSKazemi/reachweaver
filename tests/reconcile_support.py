"""Test-only helper: a post in ``needs_review`` is closed by ``reconcile`` and never sent again.

Shared by the flipped-registry CLI suites of the disabled Meta adapters (Threads, Instagram,
Facebook Page). ``storage.reconcile`` is platform-agnostic; this checks each adapter's real
``needs_review`` outcome through the CLI, then both human answers:

* ``reconcile POST --published --remote-id ID --remote-url URL``: the post becomes ``published``
  with exactly that receipt;
* ``reconcile POST --not-published``: the post returns to ``draft`` and needs a new approval;

and that afterwards neither ``publish --live --post-id POST`` nor a full ``publish --live`` sweep
makes a single request.
"""
from __future__ import annotations

from reachweaver.storage import connect, get_post


def assert_reconcile_closes_needs_review(case, needs_review_post, live, *, remote_id, remote_url):
    """``needs_review_post(label)`` returns the id of a post the adapter just left in needs_review
    (it may point ``case.db`` at a fresh queue). ``live(*argv)`` runs ``publish --live *argv`` against
    a fresh mocked provider and returns ``(exit status, provider requests made)``."""
    for outcome in ("published", "not-published"):
        with case.subTest(reconcile=outcome):
            pid = needs_review_post(outcome)
            with connect(str(case.db)) as con:
                case.assertEqual(get_post(con, pid)["status"], "needs_review")
            argv = ["reconcile", pid, "--" + outcome]
            if outcome == "published":
                argv += ["--remote-id", remote_id, "--remote-url", remote_url]
            status, out, err = case.cli(*argv)
            case.assertEqual(status, 0, out + err)
            with connect(str(case.db)) as con:
                row = get_post(con, pid)
                events = [r[0] for r in con.execute("SELECT action FROM event_log WHERE post_id=? ORDER BY id",
                                                    (pid,))]
            if outcome == "published":
                case.assertEqual((row["status"], row["remote_id"], row["remote_url"]),
                                 ("published", remote_id, remote_url))
                case.assertEqual(events[-1], "reconciled_published")
            else:
                case.assertEqual((row["status"], row["remote_id"], row["approved_at"]), ("draft", None, None))
                case.assertEqual(events[-1], "reconciled_not_published")
            case.assertEqual(events.count("send_claimed"), 1)
            status, requests = live("--post-id", pid)
            case.assertEqual((status, requests), (2, []))  # not due: never re-sent
            status, requests = live()
            case.assertEqual((status, requests), (0, []))  # nor by a full worker sweep
            with connect(str(case.db)) as con:
                case.assertEqual(get_post(con, pid)["status"], row["status"])
