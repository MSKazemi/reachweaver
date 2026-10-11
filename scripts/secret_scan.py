"""Offline, stdlib-only secret scanner for git-tracked files.

Usage:
    python3 scripts/secret_scan.py [--root DIR] [--allowlist FILE] [--list-patterns]

Scans every file listed by ``git ls-files`` under ``--root`` (default: this
repository) for high-signal credential patterns. Findings are printed as
``path:line:pattern-id`` only. The matched value is NEVER printed, logged or
returned, so CI output cannot itself leak a credential.

Known-safe fixtures are suppressed by ``.secret-scan-allow`` entries of the form::

    <path-glob> <pattern-id>  # mandatory reason

Exit codes: 0 = clean, 1 = findings, 2 = usage/configuration error.
No network access is performed.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_NAME = ".secret-scan-allow"
MAX_BYTES = 5 * 1024 * 1024

# Values that are obviously placeholders, env-var references or templates.
_PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|changeme|change_me|redacted|dummy|your[_-]|<|>|\$\{|\{\{|\*{3}|x{6}|^\$)"
)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")
_SECRET_KEY = r"(?:password|passwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)"


def _assignment_ok(value: str) -> bool:
    return not (_PLACEHOLDER.search(value) or _ENV_NAME.match(value) or len(set(value)) <= 2)


def _mixed_alnum(value: str) -> bool:
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


@dataclass(frozen=True)
class Pattern:
    id: str
    description: str
    regex: re.Pattern
    group: int = 0  # capture group holding the secret value for post-filters
    accept: object = None  # optional callable(value) -> bool
    skip_example_paths: bool = False


PATTERNS = (
    Pattern("private-key", "PEM/OpenSSH/PGP private key block header",
            re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    Pattern("aws-access-key-id", "AWS access key id (AKIA/ASIA)",
            re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    Pattern("aws-secret-access-key", "AWS secret access key assignment",
            re.compile(r"(?i)aws_?secret_?access_?key[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"), 1),
    Pattern("github-token", "GitHub token (ghp_/gho_/ghu_/ghs_/ghr_)",
            re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,}(?![A-Za-z0-9_])")),
    Pattern("github-pat", "GitHub fine-grained personal access token",
            re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{22,}")),
    Pattern("slack-token", "Slack token (xoxa/xoxb/xoxp/xoxo/xoxr/xoxs)",
            re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}")),
    Pattern("telegram-bot-token", "Telegram Bot API token",
            re.compile(r"(?<![A-Za-z0-9_])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    Pattern("jwt", "JSON Web Token (header.payload.signature)",
            re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    Pattern("bluesky-app-password", "Bluesky app-password-shaped string (xxxx-xxxx-xxxx-xxxx)",
            re.compile(r"(?<![A-Za-z0-9-])([a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})(?![A-Za-z0-9-])"), 1,
            _mixed_alnum, skip_example_paths=True),
    Pattern("secret-assignment", "password/token/secret/api key assigned a quoted literal",
            re.compile(r"(?i)(?<![A-Za-z0-9])[A-Za-z0-9_-]*" + _SECRET_KEY
                       + r"[A-Za-z0-9_]*[\"']?\s*[:=]\s*([\"'])([^\"'\s]{8,})\1"), 2, _assignment_ok),
    Pattern("secret-env-assignment", "PASSWORD/TOKEN/SECRET/API_KEY env-style assignment of a literal",
            re.compile(r"(?m)^[ \t]*(?:export[ \t]+)?[A-Z0-9_]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API_KEY|ACCESS_KEY)"
                       r"[A-Z0-9_]*[ \t]*=[ \t]*([A-Za-z0-9_+/=-]{8,})[ \t]*$"), 1, _assignment_ok),
)
PATTERN_IDS = {p.id for p in PATTERNS}


@dataclass(frozen=True)
class AllowEntry:
    glob: str
    pattern_id: str
    line: int


def load_allowlist(path: Path) -> list[AllowEntry]:
    entries: list[AllowEntry] = []
    if not path.is_file():
        return entries
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        body, sep, reason = raw.partition("#")
        if not body.strip():
            continue
        fields = body.split()
        if len(fields) != 2 or not sep or not reason.strip():
            raise ValueError(f"{path.name}:{number}: expected '<path-glob> <pattern-id>  # reason'")
        if fields[1] not in PATTERN_IDS:
            raise ValueError(f"{path.name}:{number}: unknown pattern id {fields[1]!r}")
        entries.append(AllowEntry(fields[0], fields[1], number))
    return entries


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                            capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"git ls-files failed in {root}")
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def scan_text(rel: str, text: str) -> list[tuple[int, str]]:
    """Return (line, pattern-id) pairs. Never returns matched values."""
    hits: set[tuple[int, str]] = set()
    is_example = "example" in rel.lower()
    for pattern in PATTERNS:
        if pattern.skip_example_paths and is_example:
            continue
        for match in pattern.regex.finditer(text):
            value = match.group(pattern.group)
            if pattern.accept is not None and not pattern.accept(value):
                continue
            hits.add((text.count("\n", 0, match.start(pattern.group)) + 1, pattern.id))
    return sorted(hits)


def scan(root: Path, allowlist: list[AllowEntry]):
    findings: list[tuple[str, int, str]] = []
    used: set[int] = set()
    scanned = suppressed = 0
    for rel in tracked_files(root):
        path = root / rel
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        scanned += 1
        for line, pid in scan_text(rel, data.decode("utf-8", "replace")):
            allowed = [e for e in allowlist
                       if e.pattern_id == pid and fnmatch.fnmatchcase(rel, e.glob)]
            if allowed:
                suppressed += 1
                used.update(e.line for e in allowed)
                continue
            findings.append((rel, line, pid))
    unused = [e for e in allowlist if e.line not in used]
    return findings, scanned, suppressed, unused


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan git-tracked files for credential patterns; offline.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Git work tree to scan (default: this repo)")
    parser.add_argument("--allowlist", type=Path, help=f"Allowlist file (default: <root>/{ALLOWLIST_NAME})")
    parser.add_argument("--list-patterns", action="store_true", help="Print pattern ids and exit")
    args = parser.parse_args(argv)
    if args.list_patterns:
        for p in PATTERNS:
            print(f"{p.id}: {p.description}")
        return 0
    root = args.root.resolve()
    try:
        allowlist = load_allowlist(args.allowlist or root / ALLOWLIST_NAME)
        findings, scanned, suppressed, unused = scan(root, allowlist)
    except (ValueError, OSError) as exc:
        print(f"secret-scan: error: {exc}", file=sys.stderr)
        return 2
    for rel, line, pid in findings:
        print(f"{rel}:{line}:{pid}")
    for entry in unused:
        print(f"secret-scan: warning: allowlist line {entry.line} ({entry.glob} {entry.pattern_id}) "
              "matched nothing; remove it if the fixture is gone", file=sys.stderr)
    status = f"{len(findings)} finding(s)" if findings else "OK"
    print(f"secret-scan: {status}; {scanned} tracked text files scanned, {suppressed} allowlisted hit(s)",
          file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
