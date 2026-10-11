#!/usr/bin/env python3
"""Review/apply ReachWeaver's GitHub About metadata; NEVER changes visibility.

Offline preview is the default. --apply uses an already-authenticated local
GitHub CLI on the repository owner's machine. No credentials are read here.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / ".github" / "repository-about.json"
TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ALLOWED = {
    "schema_version", "repository", "product_name", "description",
    "homepage", "topics", "metadata_scope", "notes",
}


class MetadataError(ValueError):
    """Invalid local manifest or unsafe update request."""


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError("Unable to read JSON repository About manifest") from exc
    if not isinstance(data, dict) or set(data) - ALLOWED:
        raise MetadataError("Unknown repository metadata field; visibility or other settings cannot be changed")
    if data.get("schema_version") != 1:
        raise MetadataError("Expected schema_version: 1")
    name = data.get("repository")
    if not isinstance(name, str) or not REPO.fullmatch(name):
        raise MetadataError("Expected explicit OWNER/REPO")
    if data.get("product_name") != "ReachWeaver":
        raise MetadataError("Keep product name ReachWeaver; rebranding requires a separate reviewed proposal")
    description = data.get("description")
    if not isinstance(description, str) or not 35 <= len(description) <= 160:
        raise MetadataError("GitHub About description must be 35–160 characters")
    if any(ord(char) < 32 for char in description):
        raise MetadataError("Description cannot contain control characters")
    topics = data.get("topics")
    if not isinstance(topics, list) or not 1 <= len(topics) <= 20:
        raise MetadataError("GitHub supports 1–20 topics in this manifest")
    if any(not isinstance(t, str) or not TOPIC.fullmatch(t) for t in topics) or len(topics) != len(set(topics)):
        raise MetadataError("Topics must be unique lowercase letters, digits, hyphens, <=50 characters")
    home = data.get("homepage")
    if home is not None:
        if not isinstance(home, str):
            raise MetadataError("Homepage must be null or a real https URL")
        parsed = urlsplit(home)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise MetadataError("Homepage must be a verified HTTPS URL without credentials")
    if data.get("metadata_scope") != "about_only_no_visibility_changes":
        raise MetadataError("Only About description/topics/homepage may be managed; visibility is never changed")
    return data


def merge_topics(current: list[str], proposed: list[str]) -> list[str]:
    """Retain existing repository topics rather than unexpectedly deleting them."""
    if not isinstance(current, list) or any(not isinstance(t, str) or not TOPIC.fullmatch(t) for t in current):
        raise MetadataError("GitHub returned invalid existing topics")
    merged = list(dict.fromkeys([*current, *proposed]))
    if len(merged) > 20:
        raise MetadataError("Existing+proposed topics exceed GitHub's 20-topic limit. Review manually; nothing changed")
    return merged


def _gh_api(method: str, endpoint: str, payload: dict | None = None) -> dict:
    """Only called after --apply with explicit name confirmation."""
    args = [
        "gh", "api", "-X", method, endpoint,
        "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2022-11-28",
    ]
    if payload is not None:
        args += ["--input", "-"]
    try:
        reply = subprocess.run(
            args, input=json.dumps(payload) if payload is not None else None,
            text=True, capture_output=True, check=False, timeout=45,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MetadataError("gh CLI is missing or timed out. No metadata was changed by this request") from exc
    if reply.returncode:
        # Never print shell environment or remote error payloads that could include secrets.
        raise MetadataError("GitHub API request failed. Check 'gh auth status' and repository Administration:write permission")
    try:
        return json.loads(reply.stdout)
    except json.JSONDecodeError as exc:
        raise MetadataError("Unexpected non-JSON response from GitHub API") from exc


def apply_metadata(data: dict, *, confirmed: str) -> dict:
    """Update only description/topics (and a verified homepage if explicitly provided)."""
    repo = data["repository"]
    if confirmed != repo:
        raise MetadataError(f"Explicit --confirm {repo} required before any network access")
    remote = _gh_api("GET", f"repos/{repo}")
    if remote.get("full_name", "").casefold() != repo.casefold():
        raise MetadataError("Authenticated repository identity did not match; no changes made")
    existing = _gh_api("GET", f"repos/{repo}/topics")
    final_topics = merge_topics(existing.get("names"), data["topics"])
    patch = {}
    if remote.get("description") != data["description"]:
        patch["description"] = data["description"]
    # An unverified or absent homepage is never written/cleared by this script.
    if data["homepage"] is not None and remote.get("homepage") != data["homepage"]:
        patch["homepage"] = data["homepage"]
    if patch:
        _gh_api("PATCH", f"repos/{repo}", patch)
    if existing["names"] != final_topics:
        _gh_api("PUT", f"repos/{repo}/topics", {"names": final_topics})
    return {
        "repository": repo, "description_updated": "description" in patch,
        "homepage_updated": "homepage" in patch,
        "topics_updated": existing["names"] != final_topics,
        "topic_count": len(final_topics),
        "visibility_changed": False,
        "note": "About-only update; repository visibility was never modified.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview or explicitly apply GitHub About metadata; no visibility changes")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--apply", action="store_true",
                        help="Opt-in GitHub API update using locally authenticated gh CLI")
    parser.add_argument("--confirm", help="Required exact OWNER/REPO to allow an --apply mutation")
    args = parser.parse_args(argv)
    try:
        data = load_manifest(args.manifest)
        if not args.apply:
            print(json.dumps({
                "status": "offline_preview_only",
                "repository": data["repository"], "product": data["product_name"],
                "description": data["description"], "topics": data["topics"],
                "homepage": data["homepage"],
                "note": "No API calls. Use --apply --confirm OWNER/REPO on your trusted machine after owner review; visibility never changes.",
            }, indent=2))
            return 0
        result = apply_metadata(data, confirmed=args.confirm or "")
        print(json.dumps(result, indent=2))
        return 0
    except MetadataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
