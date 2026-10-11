"""Offline credential hygiene audit: variable NAMES only, never values.

This module reads credentials through the same accessor the adapters use
(``accounts.credential``): the process environment (or a supplied mapping) or, when
``$CREDENTIALS_DIRECTORY`` is set (systemd ``LoadCredential=``), the files in that
directory. Only when explicitly given does it read a shell history file. It never
prints, logs, persists or returns a credential value or a digest of one, and it never
contacts a network.

Findings are hygiene problems (placeholders, orphans, reuse, colliding variable
names, unsafe credential files, environment secrets shadowed by a credentials
directory, literal secrets in shell history). A missing credential is *status*, not
a finding: offline dry runs without credentials are a supported mode.

``load_accounts`` refuses overlapping or identical ``env_prefix`` values (issue #5,
2026-10-09), so the ``shared_credential_variable`` and ``overlapping_env_prefix``
findings cannot fire for a config loaded through it. They stay for audits of raw
account lists, using the same rule (``accounts.env_prefix_conflicts``).
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Iterable, Mapping

from .accounts import (BACKEND_CREDENTIAL_SUFFIX, CREDENTIAL_SUFFIXES, CREDENTIALS_DIRECTORY_ENV, NON_ACCOUNT_CREDENTIALS,
                       CredentialError, CredentialsDirectoryError, account_credential_suffixes, credential,
                       credential_names_available, credential_source, env_prefix_conflicts)
from .policy import ValidationError, live_opt_in, runtime_environment
from .privacy import SECRET_ENV_NAME

__all__ = ["CREDENTIAL_SUFFIXES", "audit_environment", "expected_variables", "is_credential_name",
           "looks_like_placeholder", "render_audit", "scan_shell_history"]
# Non-account credentials read elsewhere (sources.py --online, the approval key). Reported, never orphans.
KNOWN_NON_ACCOUNT = frozenset(NON_ACCOUNT_CREDENTIALS)
_IDENTIFIER_NAME = re.compile(r"^REACHWEAVER_[A-Z0-9_]*_IDENTIFIER$")
_PLACEHOLDER_WORDS = frozenset({
    "changeme", "change_me", "change-me", "xxx", "todo", "tbd", "placeholder", "example",
    "dummy", "fake", "test", "secret", "password", "token", "none", "null", "undefined",
    "replace_me", "replace-me", "replaceme", "your_token", "your-token", "your_password",
})
_PLACEHOLDER_SHAPES = re.compile(
    r"^(?:x+|\*+|\.+|-+|0+|<[^>]*>|\$\{[^}]*\}|\{\{[^}]*\}\}|your[_-].*|.*[_-]here)$", re.I)
# A shell-history line that assigns a literal to a REACHWEAVER_*TOKEN/PASSWORD-style
# variable: `export X=lit`, `X=lit cmd`, `env X=lit`, `docker run -e X=lit`. Values that
# start with `$` (variable or command substitution) or are empty are not literals.
_HISTORY_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_])REACHWEAVER_[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY)"
    r"=(?![\"']?\$)(?![\"']{2})(?=[^\s;&|])")
NOTE = ("Offline. Variable names only; values and hashes of values are never shown. "
        "A clean audit does not prove a credential is valid, unexpired or least-privilege.")


def expected_variables(account: Mapping) -> tuple[str, ...]:
    """Credential variable names an account's adapter reads (empty for manual-only platforms).

    A Postiz-backed account reads only its backend key, like ``accounts.missing_credentials``.
    A LinkedIn account reads its app ``_CLIENT_ID``/``_CLIENT_SECRET`` only when it opted in
    with ``"scope_check": "introspect"`` (``accounts.account_credential_suffixes``).
    """
    if account.get("backend") is not None:
        return (account["env_prefix"] + BACKEND_CREDENTIAL_SUFFIX,)
    return tuple(account["env_prefix"] + suffix for suffix in account_credential_suffixes(account))


def is_credential_name(name: str) -> bool:
    return bool(SECRET_ENV_NAME.fullmatch(name) or _IDENTIFIER_NAME.fullmatch(name))


def looks_like_placeholder(value: str) -> bool:
    """True for empty/whitespace values and common template/placeholder strings."""
    stripped = value.strip()
    if not stripped:
        return True
    lowered = stripped.strip("\"'").lower()
    return lowered in _PLACEHOLDER_WORDS or bool(_PLACEHOLDER_SHAPES.fullmatch(lowered)) or len(set(lowered)) == 1


def scan_shell_history(path: str | Path) -> list[int]:
    """1-based line numbers that assign a literal REACHWEAVER_* secret. Never returns text."""
    history = Path(path).expanduser()
    if not history.is_file():
        raise ValidationError(f"History file not found: {history}")
    try:
        data = history.read_bytes()
    except OSError as exc:
        raise ValidationError(f"History file is unreadable: {history}") from exc
    text = data.decode("utf-8", errors="replace")
    return [number for number, line in enumerate(text.splitlines(), 1) if _HISTORY_ASSIGNMENT.search(line)]


def _accounts_list(accounts) -> list[Mapping]:
    items = accounts.values() if isinstance(accounts, Mapping) else accounts
    return sorted(items, key=lambda a: a["id"])


class _Reader:
    """Reads each credential at most once through ``accounts.credential``; never exposes a value.

    A ``CredentialError`` (unsafe directory or file) is remembered by name and reported as a
    finding. Values stay in this object only for the duration of one audit.
    """

    def __init__(self, env: Mapping[str, str]):
        self.env = env
        self.values: dict[str, str | None] = {}
        self.unsafe: dict[str, str] = {}
        self.directory_error: str | None = None

    def get(self, name: str) -> str | None:
        if name not in self.values:
            try:
                self.values[name] = credential(name, self.env)
            except CredentialError as exc:
                self.values[name] = None
                if isinstance(exc, CredentialsDirectoryError):
                    self.directory_error = str(exc)  # directory-level: same for every name
                else:
                    self.unsafe[name] = str(exc)
        return self.values[name]

    def clear(self) -> None:
        self.values.clear()


def audit_environment(accounts, env: Mapping[str, str] | None = None, *,
                      history: Iterable[str | Path] = ()) -> dict:
    """Return a names-only hygiene report for configured accounts and a credential source.

    ``accounts`` is the mapping from ``load_accounts`` (or an iterable of account dicts).
    ``env`` defaults to ``os.environ``. Every credential is read through
    ``accounts.credential``, so with ``$CREDENTIALS_DIRECTORY`` set the files in that
    directory are audited and the environment is not consulted for values; credential
    variables that are also exported are reported as ``env_credential_shadowed``.
    ``history`` lists shell history files to scan; none are read unless given explicitly.
    """
    env = os.environ if env is None else env
    accounts = _accounts_list(accounts)
    source = credential_source(env)
    reader = _Reader(env)
    findings: list[dict] = []
    rows: list[dict] = []
    owners: dict[str, list[str]] = {}
    for account in accounts:
        names = expected_variables(account)
        for name in names:
            owners.setdefault(name, []).append(account["id"])
        values = {n: reader.get(n) for n in names}
        rows.append({
            "account_id": account["id"],
            "platform": account["platform"],
            "set": [n for n in names if values[n]],
            # Same rule as accounts.missing_credentials: empty or unreadable counts as missing.
            "missing": [n for n in names if not values[n]],
            "manual_only": not names,
        })
    for name, ids in sorted(owners.items()):
        if len(ids) > 1:
            findings.append({"code": "shared_credential_variable", "variables": [name], "accounts": sorted(ids),
                             "message": "Several accounts read the same variable; give each account its own env_prefix"})
    for conflict in env_prefix_conflicts(accounts):  # the load_accounts rule, for raw account lists
        if conflict["reason"] == "overlapping_env_prefix":
            findings.append({"code": "overlapping_env_prefix", "variables": conflict["variables"],
                             "accounts": conflict["accounts"],
                             "message": "One account's credential variable name begins another's; "
                                        "load_accounts refuses this config. Use non-overlapping env_prefix values"})
        elif conflict["reason"] == "reserved_credential_variable":
            findings.append({"code": "reserved_credential_variable", "variables": conflict["variables"],
                             "accounts": conflict["accounts"],
                             "message": "This account's credential variables collide with a credential no account "
                                        "owns; load_accounts refuses this config. Choose another env_prefix"})
    for name in sorted(owners):
        value = reader.get(name)
        if value is not None and looks_like_placeholder(value):
            findings.append({"code": "placeholder_value", "variables": [name], "accounts": sorted(owners[name]),
                             "message": "Value is empty or looks like a placeholder"})
    try:
        available = credential_names_available(env)
    except CredentialError as exc:
        available = []
        reader.directory_error = str(exc)
    credential_names = sorted(n for n in available if is_credential_name(n))
    orphans = [n for n in credential_names if n not in owners and n not in KNOWN_NON_ACCOUNT]
    if orphans:
        findings.append({"code": "orphan_credential", "variables": orphans, "accounts": [],
                         "message": "Credential variables are set for no configured account; unset or revoke them"})
    # Salted per call so that even in-memory digests cannot be matched across runs.
    salt = secrets.token_bytes(16)
    by_digest: dict[bytes, list[str]] = {}
    for name in credential_names:
        value = reader.get(name)
        if value is None or name.endswith("_IDENTIFIER") or looks_like_placeholder(value):
            continue  # identifiers are handles, not secrets; placeholders are reported above
        by_digest.setdefault(hashlib.sha256(salt + value.encode("utf-8", "surrogatepass")).digest(), []).append(name)
    for digest_names in by_digest.values():
        if len(digest_names) > 1:
            ids = sorted({i for n in digest_names for i in owners.get(n, [])})
            findings.append({"code": "credential_value_reused", "variables": sorted(digest_names), "accounts": ids,
                             "message": "The same secret value is set in several variables; use one credential per account"})
    by_digest.clear()
    non_account = sorted(n for n in KNOWN_NON_ACCOUNT if reader.get(n))
    if reader.directory_error:
        findings.append({"code": "unsafe_credentials_directory", "variables": [CREDENTIALS_DIRECTORY_ENV],
                         "accounts": [], "message": reader.directory_error + "; no credential was read from it"})
    for name, problem in sorted(reader.unsafe.items()):
        findings.append({"code": "unsafe_credential_file", "variables": [name],
                         "accounts": sorted(owners.get(name, [])),
                         "message": problem + "; treated as missing, nothing was sent with it"})
    reader.clear()
    if source == "credentials_directory":
        shadowed = sorted(n for n in env if is_credential_name(n) and env.get(n))
        if shadowed:
            findings.append({"code": "env_credential_shadowed", "variables": shadowed, "accounts": [],
                             "message": "Also exported as environment variables, which are ignored while "
                                        "$CREDENTIALS_DIRECTORY is set; unset them so the secret lives in one place"})
    scanned: list[dict] = []
    for path in history:
        lines = scan_shell_history(path)
        entry = {"path": str(path), "lines": lines}
        if os.name == "posix" and stat.S_IMODE(Path(path).expanduser().stat().st_mode) & 0o077:
            entry["permissions"] = "group_or_world_readable"
            findings.append({"code": "history_file_not_private", "variables": [], "accounts": [],
                             "path": str(path), "lines": [],
                             "message": "Shell history is readable by other users; chmod 600 it"})
        scanned.append(entry)
        if lines:
            findings.append({"code": "shell_history_literal", "variables": [], "accounts": [],
                             "path": str(path), "lines": lines,
                             "message": "History lines assign a literal secret; revoke/rotate it, then delete those lines"})
    return {
        "accounts": rows,
        "credential_source": source,
        "non_account_credentials_set": non_account,
        "history_scanned": scanned,
        "live_opt_in": live_opt_in(env),
        "runtime_environment": runtime_environment(env),
        "findings": findings,
        "note": NOTE,
    }


def render_audit(report: dict) -> str:
    out = ["credentials-audit (offline; values are never printed)",
           f"Credential source: {report['credential_source']}"]
    for row in report["accounts"]:
        if row["manual_only"]:
            out.append(f"{row['account_id']}: {row['platform']}; manual-only, no credential variables")
            continue
        out.append(f"{row['account_id']}: {row['platform']}; set: {', '.join(row['set']) or 'none'}; "
                   f"missing: {', '.join(row['missing']) or 'none'}")
    for name in report["non_account_credentials_set"]:
        out.append(f"non-account credential set: {name}")
    for item in report["findings"]:
        where = ""
        if item.get("path"):
            where = f" {item['path']}" + (f" lines {', '.join(map(str, item['lines']))}" if item["lines"] else "")
        names = f" {', '.join(item['variables'])}" if item["variables"] else ""
        accounts = f" (accounts: {', '.join(item['accounts'])})" if item["accounts"] else ""
        out.append(f"FINDING {item['code']}:{names}{where}{accounts}: {item['message']}")
    out.append("Live opt-in: " + ("enabled" if report["live_opt_in"] else "disabled")
               + f"; REACHWEAVER_ENV {report['runtime_environment']} (publish --live requires prod)")
    out.append(f"{len(report['findings'])} finding(s)." if report["findings"] else "No findings.")
    out.append(report["note"])
    return "\n".join(out)
