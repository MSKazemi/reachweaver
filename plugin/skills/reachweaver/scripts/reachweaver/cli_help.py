"""Grouped command index for ``reachweaver --help``.

argparse prints one flat list of ~80 subcommands. This module groups them by the job a person is
doing, so the top-level help reads as a map. Per-command help (``reachweaver COMMAND --help``) is
unchanged. ``tests/test_cli_help.py`` fails if a subcommand is missing from every group, so the
index cannot drift; an ungrouped command still appears under "Other" rather than disappearing.
"""
from __future__ import annotations

import argparse
import re
import textwrap

# (title, command names in the order a person meets them)
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Get started and look around", ("start", "connect", "version", "init", "doctor", "health", "workspace", "ui", "list", "show", "journal", "capabilities", "completion")),
    ("Draft and approve (nothing is sent)", (
        "draft", "check", "repeat-check", "mark-shared", "approve", "unapprove", "cancel", "schedule", "media-attach", "media-detach",
        "video-attach", "video-detach", "youtube-attach", "youtube-detach", "campaign-check", "campaign-preview",
        "campaign-stage", "campaign-export", "claims-check")),
    ("Publish and recover (owner only; live sends are gated)", (
        "publish", "pause", "resume", "reconcile", "approval-mode", "calendar-plan", "calendar-gaps",
        "connect-check", "postiz-check", "disconnect", "reconnect")),
    ("Post by hand", ("share", "manual-export", "manual-export-batch", "delivery-options")),
    ("Monitor", ("queue-health", "dead-letter", "ops-metrics", "ops-summary", "credentials-audit", "link-health", "trace")),
    ("Measure and learn", (
        "metrics", "import-metrics", "metrics-fetch", "report", "youtube-analytics", "timing-evaluate",
        "baseline-eval", "attribution-report", "pilot-report", "peer-benchmark", "suggest", "weekly-digest")),
    ("Experiments", (
        "experiment-preregister", "experiment-verify", "experiment-evaluate", "experiment-readout", "experiment-next",
        "experiment-simulate")),
    ("Audit profiles and content", ("profile-audit", "post-audit", "seo-destinations", "seo-content-check",
                                    "landing-check", "utm-link", "media-check", "media-storyboard", "media-cutlist")),
    ("Research channels and sources", ("distribution-plan", "discovery-plan", "sources-scan", "signal-brief")),
    ("Engagement", ("engagement-fetch", "engagement-triage", "engagement-queue")),
    ("Data, privacy and schemas", ("archive", "media-relink", "data-export", "data-purge", "privacy-status", "schema-validate", "schema-export")),
    ("Updates", ("update",)),
)
SUMMARY_WIDTH = 66


def summary(text: str | None) -> str:
    """One short line from a subcommand's help: drop issue references and parentheticals, cut at the
    first sentence break."""
    if not text:
        return ""
    text = re.sub(r"\s*\((?:[^()]*#\d+[^()]*|issue #\d+[^()]*)\)", "", text)  # "(#22 ...)" / "(issue #23 ...)"
    text = re.sub(r"^#\d+(?: \([^)]*\))?:\s*", "", text)  # a leading "#22: "
    text = re.split(r"(?<=[a-z0-9)])[.;](?:\s|$)|;\s", " ".join(text.split()), maxsplit=1)[0].rstrip(".;")
    text = text[:1].upper() + text[1:]
    return textwrap.shorten(text, width=SUMMARY_WIDTH, placeholder="…")


def grouped_index(parser: argparse.ArgumentParser) -> str:
    """The grouped command map, built from the parser's own subcommands and their help."""
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {choice.dest: choice.help for choice in action._choices_actions}
    known = set(action.choices)
    width = max(len(name) for name in known) + 2
    lines = ["Commands by task (reachweaver COMMAND --help for details):"]
    groups = [(title, [n for n in names if n in known]) for title, names in GROUPS]
    other = sorted(known - {n for _, names in GROUPS for n in names})
    if other:
        groups.append(("Other", other))
    for title, names in groups:
        if not names:
            continue
        lines += ["", title + ":"]
        for name in names:
            lines.append(f"  {name.ljust(width)}{summary(helps.get(name))}".rstrip())
    lines += ["", "Nothing is sent without your explicit approval and the live-send gates; run publish without --live",
              "for a dry run. See docs/QUICKSTART.md to try it with no account and no network."]
    return "\n".join(lines)


_HINTS = (
    (re.compile(r"^Missing \S*accounts"), "Next: copy config/accounts.example.json to config/accounts.json (no "
                                           "secrets) or pass --accounts FILE. To try it with no account at all, "
                                           "follow docs/QUICKSTART.md."),
    (re.compile(r"^Unknown post id"), "Next: 'reachweaver list' shows the post ids."),
    (re.compile(r"^Unknown account"), "Next: 'reachweaver doctor' lists the configured accounts."),
    (re.compile(r"does not exist; .*never create"), "Next: 'reachweaver init' creates the local database."),
    (re.compile(r"State directory is not private"), "Next: restrict the state folder with chmod 700."),
)


def error_hint(message: str) -> str | None:
    """A one-line next step for a common, harmless failure, or None. Shown only to a person at a terminal."""
    for pattern, hint in _HINTS:
        if pattern.search(message):
            return hint
    return None
