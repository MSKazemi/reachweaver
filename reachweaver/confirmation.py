"""Opt-in human-presence confirmation for ``approve`` and ``publish --live`` (#7 D6, options A+B).

Owner-approved 2026-10-09 (docs/SECURITY-REVIEW-PROPOSAL-7-d6-d7.md). Off by default. When
the owner enables ``settings.approval_confirmation = tty_hash_prefix`` (``approval-mode
--confirmation tty_hash_prefix``):

* A. both stdin and stdout must be terminals (``os.isatty``), so a plain agent subprocess with
  pipes is refused before anything changes;
* B. the operator types the first 8 hex characters of the approval payload sha256 that was just
  printed. The answer is read from ``/dev/tty`` (the process's controlling terminal, tty(4),
  https://man7.org/linux/man-pages/man4/tty.4.html, accessed 2026-10-09), never from stdin, so
  piping cannot answer it. Compared with ``hmac.compare_digest`` on bytes (Python docs: str
  arguments must be ASCII, https://docs.python.org/3/library/hmac.html, accessed 2026-10-09).
  At most 3 attempts; nothing is approved or sent on failure.

This is friction, not a security boundary: any local process can allocate a pseudo-terminal
(``script``, ``expect``, Python ``pty``) and read the hash from its own output. A key the agent
cannot read (D7) or a hardware touch (option C) is the real boundary.
"""
from __future__ import annotations

import hmac
import os
import secrets
import sys
from typing import Callable, Protocol

from .policy import ValidationError
from .storage import event

SETTING = "approval_confirmation"
TTY_HASH_PREFIX = "tty_hash_prefix"
MODES = (TTY_HASH_PREFIX,)
PREFIX_HEX = 8
MAX_ATTEMPTS = 3
MAX_ANSWER_BYTES = 256
TTY_PATH = "/dev/tty"

REQUIRES_TTY = "human_confirmation_requires_tty"
TTY_UNAVAILABLE = "human_confirmation_tty_unavailable"
FAILED = "human_confirmation_failed"
ABORTED = "human_confirmation_aborted"


class ConfirmationError(ValidationError):
    """The human-presence check did not pass. Nothing was approved, claimed or sent."""


class TtyChannel(Protocol):
    def write(self, text: str) -> None: ...
    def readline(self) -> str | None: ...  # None on end of file
    def close(self) -> None: ...


class _DevTty:
    """Unbuffered read/write on the controlling terminal; never stdin/stdout."""

    def __init__(self, path: str = TTY_PATH):
        flags = os.O_RDWR | getattr(os, "O_NOCTTY", 0)
        self._fd = os.open(path, flags)

    def write(self, text: str) -> None:
        os.write(self._fd, text.encode("utf-8", "replace"))

    def readline(self) -> str | None:
        data = b""
        while not data.endswith(b"\n") and len(data) < MAX_ANSWER_BYTES:
            chunk = os.read(self._fd, MAX_ANSWER_BYTES - len(data))
            if not chunk:
                return None if not data else data.decode("utf-8", "replace")
            data += chunk
        return data.decode("utf-8", "replace")

    def close(self) -> None:
        os.close(self._fd)


def confirmation_mode(con) -> str | None:
    """``tty_hash_prefix`` when enabled, else None. Any stored value counts as enabled (fail closed)."""
    row = con.execute("SELECT value FROM settings WHERE name=?", (SETTING,)).fetchone()
    if row is None:
        return None
    return row["value"] if row["value"] in MODES else TTY_HASH_PREFIX


def enable(con, mode: str = TTY_HASH_PREFIX) -> None:
    if mode not in MODES:
        raise ValidationError(f"Unknown confirmation mode; choose one of {', '.join(MODES)}")
    con.execute("INSERT INTO settings(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (SETTING, mode))
    event(con, "approval_confirmation_enabled", None, f"mode={mode}")


def disable(con, **io) -> None:
    """Turn the check off. While it is on, turning it off needs the same ritual (random challenge)."""
    if confirmation_mode(con) is None:
        return
    challenge = secrets.token_hex(32)  # fresh each time, so no earlier output can answer it
    print(f"Disabling human confirmation. Challenge: {challenge}")
    confirm_prefix(challenge, prompt="disable human confirmation", **io)
    con.execute("DELETE FROM settings WHERE name=?", (SETTING,))
    event(con, "approval_confirmation_disabled", None, "confirmed=tty")


def require_interactive(*, stdin=None, stdout=None) -> None:
    """Option A: refuse unless stdin and stdout are terminals."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        interactive = bool(stdin.isatty() and stdout.isatty())
    except (AttributeError, ValueError, OSError):
        interactive = False
    if not interactive:
        raise ConfirmationError(f"{REQUIRES_TTY}: human confirmation is enabled and needs an interactive "
                                "terminal on stdin and stdout; nothing was approved or sent")


def confirm_prefix(value_hex: str, *, prompt: str, stdin=None, stdout=None,
                   open_tty: Callable[[], TtyChannel] | None = None) -> None:
    """Options A+B: TTY check, then the first ``PREFIX_HEX`` characters of ``value_hex`` typed on /dev/tty.

    Raises ``ConfirmationError`` (requires_tty, tty_unavailable, aborted, failed). The typed text
    is never echoed back or logged.
    """
    require_interactive(stdin=stdin, stdout=stdout)
    expected = value_hex[:PREFIX_HEX].lower().encode("ascii")
    try:
        tty = (open_tty or _DevTty)()
    except OSError:
        raise ConfirmationError(f"{TTY_UNAVAILABLE}: cannot open {TTY_PATH} (no controlling terminal); "
                                "nothing was approved or sent") from None
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            tty.write(f"Type the first {PREFIX_HEX} characters of the value shown above to {prompt} "
                      f"(attempt {attempt}/{MAX_ATTEMPTS}): ")
            line = tty.readline()
            if line is None:
                raise ConfirmationError(f"{ABORTED}: end of input on {TTY_PATH}; nothing was approved or sent")
            typed = line.strip().lower().encode("utf-8", "replace")
            if hmac.compare_digest(typed, expected):
                return
            tty.write("Does not match.\n")
    finally:
        tty.close()
    raise ConfirmationError(f"{FAILED}: {MAX_ATTEMPTS} attempts did not match; nothing was approved or sent")


def confirm_post(con, post_id: str, payload_sha256: str, *, action: str, **io) -> str | None:
    """Run the check for one post when enabled. Returns the mode label that passed, or None when off.

    Records ``human_confirmation_passed``/``human_confirmation_failed`` audit events (action and
    reason code only, never typed text). Raises ``ConfirmationError`` on failure.
    """
    mode = confirmation_mode(con)
    if mode is None:
        return None
    try:
        confirm_prefix(payload_sha256, prompt=f"{action.replace('_', ' ')} post {post_id}", **io)
    except ConfirmationError as exc:
        event(con, "human_confirmation_failed", post_id, f"action={action} reason={str(exc).split(':', 1)[0]}")
        raise
    event(con, "human_confirmation_passed", post_id, f"action={action} mode={mode} sha256={payload_sha256[:12]}")
    return mode


def show_payload(post, digest: str, *, heading: str, send_options: str | None = None,
                 allow_repeat: bool = False) -> None:
    """Print exactly what the confirmation binds: destination, time, text and its payload sha256.

    ``send_options`` / ``allow_repeat`` (approve only; sheet items 5/9 and 1): also bound by the approval,
    like the nonce, but outside this content digest, so they are shown as their own lines."""
    print(f"{heading} post {post['id']}: account={post['account_id']} platform={post['platform']} "
          f"campaign={post['campaign'] or '-'} source_url={post['source_url'] or '-'} "
          f"scheduled_at={post['scheduled_at'] or '-'}")
    print("---- exact text ----")
    print(post["content"])
    print("--------------------")
    _show_attachments(post)
    _show_reply_target(post)
    if send_options:
        print(f"send options (also bound): {send_options}")
    if allow_repeat:
        print("repeat override (also bound): --allow-repeat")
    print(f"payload sha256 {digest}")


def _show_reply_target(post) -> None:
    """Where a reply (#17) lands; the digest binds this target byte for byte. Ids, not handles."""
    from .storage import reply_target_text
    if reply_target_text(post) is None:
        return
    from .replies import row_target
    try:
        target = row_target(post)
    except ValidationError as exc:
        print(f"reply target: refused ({str(exc).split(':', 1)[0]})")
        return
    if target is None:
        return
    if target["platform"] == "mastodon":
        print(f"reply to: {target['parent_url']} (server {target['server']} in_reply_to_id "
              f"{target['in_reply_to_id']} visibility {target['visibility']})")
    else:
        print(f"reply to: {target['parent_url']} (parent {target['parent']['uri']} root {target['root']['uri']})")


def _show_attachments(post) -> None:
    """The images or video the digest also binds (#13, #20, #22, #23), so the confirmed prefix
    is visibly the media-aware hash. SHA-256 prefixes, not paths."""
    from .storage import post_media, post_video
    from .video import mode as video_mode
    try:
        media, video = post_media(post), post_video(post)
    except ValidationError as exc:
        print(f"attachments: refused ({str(exc).split(':', 1)[0]})")
        return
    for index, item in enumerate(media, 1):
        print(f"image {index}: {item['mime']} sha256 {item['sha256'][:12]} alt {len(item['alt'])} chars"
              + (f" url {item['url']}" if item["source"] == "url" else ""))
        rights = item.get("rights")
        if rights:  # #48: what the digest also binds
            print(f"  rights: license {rights['license']}, made by {rights['provenance']['created_by']}"
                  + (f", attribution {rights['attribution']!r}" if rights.get("attribution") else "")
                  + (f", {len(rights['alt_translations'])} alt translation(s)" if rights.get("alt_translations") else ""))
    if video is not None:
        print(f"video: {video['platform']} {video_mode(video)} {video['mime']} sha256 {video['sha256'][:12]} "
              f"{video['bytes']} bytes")


def confirm_live_send(con, post, **io) -> None:
    """Pre-claim, pre-network: show the exact payload and require confirmation (no-op when off).

    The digest is ``approval_payload_sha256(post)``, the same value the sender passes to
    ``claim_sending(expected_sha256=...)``, so what the human confirmed is what is claimed.
    """
    if confirmation_mode(con) is None:
        return
    from .storage import approval_payload_sha256
    digest = approval_payload_sha256(post)
    show_payload(post, digest, heading="CONFIRM live send of")
    confirm_post(con, post["id"], digest, action="publish_live", **io)
