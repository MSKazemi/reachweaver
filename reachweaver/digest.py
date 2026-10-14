"""Weekly digest: ``suggest`` plus the local ``report`` as one Markdown file (issue #45 W7).

Pure rendering over results other modules already produce. It has NO network access, reads no
credential, opens no database itself and has no send, e-mail, post or schedule path. The only
side effect is ``write_digest``, which creates one NEW owner-only (0600) file and refuses to
overwrite or follow a symlink. Delivering the file anywhere is a separate human step.

The digest repeats the evidence labels and limits of its inputs. It adds no ranking, forecast or
causal claim of its own.
"""
from __future__ import annotations

import re
from pathlib import Path

from .policy import ValidationError
from .privacy import write_private_file
from .report_format import markdown_cell, render_markdown

DIGEST_SCHEMA = "reachweaver.weekly-digest.v1"
_IDENT_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")
LIMITS = (
    "Local state and files you supplied only: nothing was fetched, posted, replied to or sent, and this is the only file written.",
    "Ranked tasks are rule-based triage for human review, not a platform algorithm or a reach prediction.",
    "Missing data is not negative evidence. A metric without an exact value is shown as unknown, never 0.",
    "Counts are latest-per-post snapshots, not unique people or comparable cross-platform views.",
    "This file may contain account ids and local metrics. Keep it private; sharing it is your decision.",
)


def _ident(value: object) -> str:
    """A curated identifier (priority, kind, signal, source) as a code span; unexpected characters are dropped."""
    return "`" + _IDENT_UNSAFE.sub("", str(value)) + "`"


def render_digest(suggestions: dict, report_envelope: dict | None = None, *, generated_at: str) -> str:
    """Markdown for one digest. ``suggestions`` is ``suggest.build_suggestions`` output."""
    if suggestions.get("schema_version") != "reachweaver.suggestions.v1":
        raise ValidationError("weekly-digest needs a reachweaver.suggestions.v1 result")
    esc = markdown_cell
    lines = [
        "# ReachWeaver weekly digest",
        "",
        f"Generated {esc(generated_at)}. Draft for your review; never sent by ReachWeaver.",
        "",
        f"## Next actions ({suggestions['shown']} of {suggestions['total_actionable_signals']} signals)",
        "",
    ]
    for action in suggestions["actions"]:
        where = " / ".join(esc(x) for x in (action["platform"], action["account_id"]) if x)
        lines.append(f"{action['rank']}. **{_ident(action['priority'])} {_ident(action['kind'])}**"
                     + (f" [{where}]" if where else "") + f": {esc(action['title'])}")
        lines.append(f"   - Evidence: {_ident(action['evidence']['source'])} ({_ident(action['confidence'])}); "
                     f"signal {_ident(action['signal'])}")
        lines.append(f"   - Next: {esc(action['next_step'])}")
    if not suggestions["actions"]:
        lines.append("No actionable findings from the supplied inputs. This does not prove accounts are optimized.")
    lines += ["", "## Local report", ""]
    if report_envelope is None:
        lines.append("Not included. Pass `--include-db` to add the local published-post report.")
    elif not report_envelope.get("platforms"):
        lines.append("The local database has no published posts yet.")
    else:
        # Same Markdown the `report` command prints, one heading level below ours.
        lines.append(render_markdown(report_envelope, level=3, title="Published-post totals").strip())
    lines += ["", "## What this digest is not", ""]
    lines += [f"- {item}" for item in LIMITS]
    lines += [f"- {esc(note)}" for note in suggestions.get("limitations", [])]
    return "\n".join(lines) + "\n"


def write_digest(path: Path, text: str) -> None:
    """Create the digest as a NEW owner-only file next to an existing directory."""
    if path.is_symlink() or path.exists():
        raise ValidationError(f"weekly-digest refuses to overwrite {path.name}; choose a new file name")
    if not path.parent.is_dir():
        raise ValidationError("weekly-digest --out must be in an existing directory")
    write_private_file(path, text)
