"""Ideas inbox: "post about this later" notes, kept apart from the post queue.

``reachweaver idea add|list|drop|promote`` keeps a small private JSON file, ``ideas.json``, next to the
database (``state/ideas.json`` in a workspace), the same way the approval ledger lives next to it.
The inbox is deliberately NOT a table in the posts database:

- An idea is not a draft. It has no account, platform, approval or send state, so it can never be
  picked up by ``approve``, ``schedule`` or ``publish``, and adding one never creates or migrates the
  database.
- ``promote`` writes a NEW campaign scaffold folder in the campaign contract format (the idea text as
  the brief, ``{{write: ...}}`` placeholders everywhere else). ``campaigns.validate_campaign`` reports
  it BLOCKED until a person writes and verifies it, exactly like a ``signal-brief`` scaffold. It never
  stages, approves, schedules or publishes anything and never opens the database.

Limits: text up to ``TEXT_MAX`` characters, at most ``TAGS_MAX`` tags, ``IDEAS_MAX`` ideas, a source
URL must be a public ``https`` URL (``campaigns.public_url``), and text that looks like a credential is
refused. No network, no model, no external service.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .policy import ValidationError

FILENAME = "ideas.json"
LOCK_SUFFIX = ".lock"
SCHEMA_VERSION = 1
TEXT_MAX = 2000
TAGS_MAX = 8
IDEAS_MAX = 2000
MAX_FILE_BYTES = 8_000_000
STATUSES = ("open", "promoted", "dropped")
TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
IDEA_ID = re.compile(r"^idea-[0-9a-f]{8}$")
# A URL rendered as <...> in brief.md must not break out of the Markdown (same character set as briefs.py).
MARKDOWN_SAFE_URL = re.compile(r"^https://[A-Za-z0-9\-._~:/?\[\]@!$&()*+,;=%]{1,2000}$")
CAMPAIGN_FILE = "campaign.reachweaver.json"
BRIEF_FILE = "brief.md"
SCAFFOLD_STATUS = "SCAFFOLD_BLOCKED_UNTIL_HUMAN_EDIT"
SEND_PATH = None  # Asserted in tests: the inbox can never stage, approve, schedule or send.


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inbox_path(db: str | Path | None = None) -> Path:
    """``ideas.json`` next to the database that ``--db`` / ``$REACHWEAVER_DB`` / the workspace names."""
    from .storage import database_path
    return database_path(db).parent / FILENAME


# --------------------------------------------------------------------- validation

def clean_text(text: object) -> str:
    if not isinstance(text, str):
        raise ValidationError("Idea text must be a string")
    text = unicodedata.normalize("NFC", text).strip()
    if not text:
        raise ValidationError("Idea text is empty")
    if len(text) > TEXT_MAX:
        raise ValidationError(f"Idea text is {len(text)} characters; the inbox keeps at most {TEXT_MAX}. "
                              "Keep the idea short and put the detail in the source it links to")
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in text):
        raise ValidationError("Idea text contains control characters")
    from .privacy import redact
    if redact(text) != text:
        raise ValidationError("Idea text looks like it contains a credential or secret; the inbox is not a secret "
                              "store. Remove it and try again")
    return text


def clean_source(url: object) -> str | None:
    if url is None:
        return None
    from .campaigns import public_url
    if not isinstance(url, str) or len(url) > 2000 or not public_url(url):
        raise ValidationError("--source must be a public https URL (no credentials, fragment, IP address or local host)")
    return url


def clean_tags(tags) -> list[str]:
    out: list[str] = []
    for raw in tags or []:
        tag = str(raw).strip().casefold()
        if not TAG.fullmatch(tag):
            raise ValidationError(f"Tag {str(raw)[:40]!r} must be 1..32 lowercase letters, digits or hyphens, "
                                  "starting with a letter or digit")
        if tag not in out:
            out.append(tag)
    if len(out) > TAGS_MAX:
        raise ValidationError(f"At most {TAGS_MAX} tags per idea")
    return out


def _check_record(item: object) -> dict:
    if not isinstance(item, dict):
        raise ValidationError("ideas.json is damaged: an entry is not an object")
    if not isinstance(item.get("id"), str) or not IDEA_ID.fullmatch(item["id"]):
        raise ValidationError("ideas.json is damaged: an entry has no valid id")
    if item.get("status") not in STATUSES:
        raise ValidationError(f"ideas.json is damaged: {item['id']} has an unknown status")
    if not isinstance(item.get("text"), str) or not isinstance(item.get("tags"), list):
        raise ValidationError(f"ideas.json is damaged: {item['id']} has no text or tags")
    return item


# --------------------------------------------------------------------- file access

def _check_private_dir(folder: Path) -> None:
    if os.name == "posix" and stat.S_IMODE(folder.stat().st_mode) & 0o077:
        raise ValidationError("State directory is not private; restrict it with chmod 700 before running")


def _check_private_file(path: Path) -> None:
    if path.is_symlink():
        raise ValidationError(f"{path.name} cannot be a symbolic link")
    if os.name == "posix" and path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValidationError(f"{path.name} is not private; restrict it with chmod 600 before running")


def load(path: Path) -> dict:
    """The inbox at ``path``; an empty inbox when the file does not exist. Never creates anything."""
    if path.is_symlink():
        raise ValidationError(f"{path.name} cannot be a symbolic link")
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "ideas": []}
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValidationError(f"{path.name} is larger than {MAX_FILE_BYTES} bytes")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path.name} is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("ideas"), list):
        raise ValidationError(f"{path.name} is not a ReachWeaver ideas inbox (schema_version {SCHEMA_VERSION})")
    for item in data["ideas"]:
        _check_record(item)
    return data


def _save(path: Path, data: dict) -> None:
    """Atomic replace through a NEW 0600 temporary file in the same private folder."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if fd != -1:
            os.close(fd)
        if tmp.exists():
            tmp.unlink()


@contextmanager
def _locked(path: Path):
    """Create the private folder if needed and hold an exclusive lock for one read-modify-write."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _check_private_dir(path.parent)
    _check_private_file(path)
    lock = path.with_name("." + path.name + LOCK_SUFFIX)
    if lock.is_symlink():
        raise ValidationError(f"{lock.name} cannot be a symbolic link")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            import fcntl
        except ImportError:  # not POSIX: one writer at a time is the user's responsibility
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _find(data: dict, idea_id: str) -> dict:
    wanted = idea_id.strip().casefold() if isinstance(idea_id, str) else ""
    for item in data["ideas"]:
        if item["id"] == wanted:
            return item
    raise ValidationError(f"No idea with id {wanted[:20]!r}; 'reachweaver idea list --status open' shows them")


# --------------------------------------------------------------------- operations

def add(path: Path, text: str, *, source: str | None = None, tags=None) -> dict:
    record = {"id": "", "text": clean_text(text), "source_url": clean_source(source), "tags": clean_tags(tags),
              "status": "open", "created_at": _now(), "updated_at": None, "promoted": None}
    with _locked(path):
        data = load(path)
        if len(data["ideas"]) >= IDEAS_MAX:
            raise ValidationError(f"The inbox already holds {IDEAS_MAX} ideas; drop or promote some first")
        taken = {item["id"] for item in data["ideas"]}
        while not record["id"] or record["id"] in taken:
            record["id"] = "idea-" + secrets.token_hex(4)
        data["ideas"].append(record)
        _save(path, data)
    return record


def listing(path: Path, *, status: str | None = None, tag: str | None = None) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise ValidationError(f"--status must be one of {', '.join(STATUSES)}")
    wanted_tag = clean_tags([tag])[0] if tag else None
    return [item for item in load(path)["ideas"]
            if (status is None or item["status"] == status) and (wanted_tag is None or wanted_tag in item["tags"])]


def counts(path: Path) -> dict[str, int]:
    out = {name: 0 for name in STATUSES}
    for item in load(path)["ideas"]:
        out[item["status"]] += 1
    return out


def drop(path: Path, idea_id: str) -> dict:
    with _locked(path):
        data = load(path)
        item = _find(data, idea_id)
        if item["status"] != "open":
            raise ValidationError(f"Idea {item['id']} is {item['status']}; only an open idea can be dropped")
        item["status"], item["updated_at"] = "dropped", _now()
        _save(path, data)
    return item


def remove_closed(path: Path, idea_ids) -> int:
    """Delete the named ideas that are dropped or promoted (``data-purge --apply``); an open idea is never
    removed, whatever the list says. Returns how many were removed; a missing inbox removes nothing."""
    wanted = {i for i in idea_ids if isinstance(i, str)}
    if not wanted or path.is_symlink() or not path.is_file():
        return 0
    with _locked(path):
        data = load(path)
        keep = [item for item in data["ideas"] if item["status"] == "open" or item["id"] not in wanted]
        removed = len(data["ideas"]) - len(keep)
        if removed:
            data["ideas"] = keep
            _save(path, data)
    return removed


def _placeholder(text: str) -> str:
    return "{{write: " + text.replace("{", "(").replace("}", ")") + "}}"


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def build_scaffold(item: dict, campaign_id: str, *, brand: dict | None = None, accounts: dict | None = None,
                   objective: str = "awareness") -> dict:
    """``{campaign, markdown, validation}`` for one idea; writes nothing and opens no database."""
    from .campaigns import OBJECTIVES, SLUG, validate_brand, validate_campaign
    if not isinstance(campaign_id, str) or not SLUG.fullmatch(campaign_id):
        raise ValidationError("--campaign must be a lowercase slug: a letter, then 1..63 letters, digits or hyphens")
    if objective not in OBJECTIVES:
        raise ValidationError(f"--objective must be one of {', '.join(sorted(OBJECTIVES))}")
    if brand is not None:
        issues = validate_brand(brand)
        if issues:
            raise ValidationError("Brand file is invalid: " + "; ".join(issues))
    evidence = []
    if item.get("source_url"):
        evidence.append({"id": "source-1", "kind": "other", "url": item["source_url"],
                         "claim": _placeholder("the specific claim this source supports, after you open it"),
                         "checked_by": "unverified"})
    variants = []
    if brand is not None and accounts:
        for account_id in brand.get("approved_accounts", []):
            account = accounts.get(account_id)
            if not isinstance(account, dict) or not isinstance(account.get("platform"), str):
                continue
            platform = account["platform"]
            variants.append({
                "id": account_id, "account_id": account_id, "platform": platform, "format": "text",
                "angle": _placeholder(f"one angle native to {platform} readers"),
                "cta": _placeholder("the one action a reader should take"),
                "evidence_ids": [e["id"] for e in evidence],
                "text": _placeholder(f"{platform} post for {account_id}; ground every claim in a cited source"),
            })
    campaign = {
        "schema_version": 1, "campaign_id": campaign_id,
        "brand_id": brand["brand_id"] if brand is not None else "replace-with-your-brand-id",
        "audience": _placeholder("the exact reader group this campaign serves"),
        "objective": objective,
        "brief": {"generated_by": "reachweaver idea promote", "generated_at": _now(), "status": SCAFFOLD_STATUS,
                  "idea_id": item["id"], "idea_text": item["text"], "idea_tags": list(item["tags"]),
                  "note": "SCAFFOLD ONLY. The idea text is a brief, not post copy and not evidence. Nothing was "
                          "staged, approved, scheduled or published."},
        "evidence": evidence, "variants": variants,
    }
    errors, warnings = validate_campaign(campaign, brand=brand, accounts=accounts)
    if not errors:  # a scaffold must never be stageable
        raise ValidationError("Internal check failed: the idea scaffold validated; refusing to write it")
    validation = {"valid": False, "expected": "BLOCKED", "errors": errors, "warnings": warnings}
    fence = _fence(item["text"])
    lines = [f"# Campaign brief from idea `{item['id']}`", "",
             f"Campaign `{campaign_id}`, objective `{objective}`. **Status: BLOCKED until a person edits "
             f"`{CAMPAIGN_FILE}`.** No copy was generated and nothing was staged, approved, scheduled or published.", "",
             "## The idea (your note: a brief, not evidence)", "", fence + "text", item["text"], fence, ""]
    if item.get("source_url"):
        url = item["source_url"]
        shown = f"<{url}>" if MARKDOWN_SAFE_URL.fullmatch(url) else "(see `evidence[0].url` in the campaign file)"
        lines += [f"Source you noted: {shown} (not opened or checked by ReachWeaver).", ""]
    if item["tags"]:
        lines += ["Tags: " + ", ".join(f"`{t}`" for t in item["tags"]), ""]
    lines += ["## Before this campaign can be staged", "",
              "1. Set `brand_id` to your brand and add one variant per approved account "
              "(`id`, `account_id`, `platform`, `format: text`, `angle`, `text`, `evidence_ids`, `cta`).",
              "2. Add evidence: a public https `url`, the specific `claim` it supports, `verification` "
              "(`primary_source_checked` or `owner_attested`) and `checked_at` with a timezone, after you open the source.",
              "3. Replace every `{{write: ...}}` placeholder. Ground every number, release word and feature in cited evidence.",
              "4. Run `reachweaver campaign-check --file " + CAMPAIGN_FILE + " --brand BRAND.json`, then "
              "`claims-check` and `campaign-preview`. Approval stays a separate human step after `campaign-stage`.",
              "", "## Validation at creation (expected: BLOCKED)", ""]
    lines += [f"- ERROR: {e}" for e in errors] + [f"- WARNING: {w}" for w in warnings]
    return {"campaign": campaign, "markdown": "\n".join(lines) + "\n", "validation": validation}


def promote(path: Path, idea_id: str, campaign_id: str, out_dir: str | Path, *, brand: dict | None = None,
            accounts: dict | None = None, objective: str = "awareness") -> dict:
    """Write ``OUT_DIR/campaign.reachweaver.json`` and ``OUT_DIR/brief.md`` (NEW folder 0700, files 0600)
    and mark the idea promoted. Never touches the database or the post queue."""
    from .privacy import write_private_file
    out = Path(out_dir)
    if out.exists() or out.is_symlink():
        raise ValidationError(f"Refusing to use existing {out}; --out must name a NEW folder")
    with _locked(path):
        data = load(path)
        item = _find(data, idea_id)
        if item["status"] != "open":
            raise ValidationError(f"Idea {item['id']} is {item['status']}; only an open idea can be promoted")
        scaffold = build_scaffold(item, campaign_id, brand=brand, accounts=accounts, objective=objective)
        out.mkdir(parents=True, mode=0o700)
        files = [out / CAMPAIGN_FILE, out / BRIEF_FILE]
        try:
            write_private_file(files[0], json.dumps(scaffold["campaign"], indent=2, ensure_ascii=False) + "\n")
            write_private_file(files[1], scaffold["markdown"])
            item["status"], item["updated_at"] = "promoted", _now()
            item["promoted"] = {"campaign_id": campaign_id, "out": str(out)}
            _save(path, data)
        except BaseException:  # leave no half-written scaffold behind; the idea stays open
            for name in files:
                if name.is_file() and not name.is_symlink():
                    name.unlink()
            if out.is_dir() and not any(out.iterdir()):
                out.rmdir()
            raise
    return {"idea": item, "files": [str(f) for f in files], "campaign_id": campaign_id,
            "status": SCAFFOLD_STATUS, "validation": scaffold["validation"]}


# --------------------------------------------------------------------- CLI

def render_list(items: list[dict]) -> str:
    if not items:
        return "No ideas. Capture one with: reachweaver idea add \"TEXT\" [--source URL] [--tag T]"
    lines = []
    for item in items:
        first = " ".join(item["text"].split())
        first = first if len(first) <= 90 else first[:89] + "…"
        tags = (" [" + ", ".join(item["tags"]) + "]") if item["tags"] else ""
        lines.append(f"{item['id']}  {item['status']:<8}  {item['created_at'][:10]}  {first}{tags}")
        if item.get("source_url"):
            lines.append(f"{'':>13}  source: {item['source_url']}")
    return "\n".join(lines)


def run_cli(args) -> int:
    path = inbox_path(args.db)
    action = args.idea_action
    if action == "add":
        record = add(path, args.text, source=args.source, tags=args.tag)
        if args.json:
            print(json.dumps(record, indent=2, ensure_ascii=False))
        else:
            print(f"Added {record['id']} to the ideas inbox ({path}). Not a draft: nothing was queued or sent.")
        return 0
    if action == "list":
        items = listing(path, status=args.status, tag=args.tag)
        print(json.dumps({"inbox": str(path), "ideas": items}, indent=2, ensure_ascii=False) if args.json
              else render_list(items))
        return 0
    if action == "drop":
        item = drop(path, args.id)
        print(json.dumps(item, indent=2, ensure_ascii=False) if args.json else f"Dropped {item['id']}.")
        return 0
    if action == "promote":
        from .accounts import load_accounts
        from .campaigns import read_json
        brand = read_json(args.brand) if args.brand else None
        accounts_path = args.idea_accounts
        accounts = load_accounts(accounts_path) if accounts_path else None
        result = promote(path, args.id, args.campaign, args.out, brand=brand, accounts=accounts,
                         objective=args.objective)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"{result['status']}: campaign {result['campaign_id']} from {result['idea']['id']}")
            for name in result["files"]:
                print(f"  wrote {name}")
            print(f"  validation: BLOCKED as expected ({len(result['validation']['errors'])} errors) until you "
                  "write every placeholder and verify each source")
            print("Nothing was staged, approved, scheduled or published; the post queue is unchanged.")
        return 0
    raise ValidationError("idea needs one of: add, list, drop, promote")
