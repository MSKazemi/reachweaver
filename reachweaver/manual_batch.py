"""Offline batch of manual packages, preflighted as a whole (salvaged from draft PR #65).

``manual-export-batch`` prepares 1 to 25 manual packages in one run. Each item goes through the
existing single-package code: the sourced ``manual_export`` package for Threads, Instagram,
Facebook Pages, YouTube and TikTok, and the generic ``manual_package`` for every other
``manual_only`` destination, so every per-platform text and media check is the one
``manual-export`` already runs. The batch adds only three things:

- **Whole-batch preflight.** Every item is checked before anything is written; one blocking error
  anywhere writes nothing.
- **SHA-256 of the exact text.** The manifest records the hash of each text file, and the writer
  re-reads every written file and refuses to finish if its bytes differ from the reviewed text.
- **An advisory local time** per item (ISO 8601 with an explicit offset), recorded for the owner's
  own planning. Nothing is scheduled anywhere.

Human-only destinations (Reddit, Hacker News, community forums and the rest) are refused in a
batch: their notes form is prepared one at a time with ``manual-export``, and the owner writes the
post. Nothing here sends, uploads, schedules, opens a browser or reads a credential
(``SEND_PATH = None``); every package keeps ``status = "manual_export_not_published"``.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .campaigns import _sensitive_keys
from .distribution import load_registry
from .manual_export import MANUAL_PLATFORMS, build_export, write_export
from .manual_package import build_package, generic_platforms, write_package
from .policy import ValidationError
from .privacy import write_private_file

SEND_PATH = None  # Asserted in tests: this module can never send, upload or schedule.
BATCH_KIND = "reachweaver.manual-export-batch.v1"
BATCH_STATUS = "manual_export_batch_not_published"
MAX_ITEMS = 25
ITEM_FIELDS = frozenset({"platform", "variant", "account_meta", "at"})
MANIFEST = "batch-manifest.json"
NOTICE = ("MANUAL PACKAGES ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything. Each folder is "
          "one package for the account holder to review and, if they choose, post by hand in the platform's own "
          "app or website. A batch is not permission for bulk posting, and no package is evidence that a post exists.")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _advisory_time(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("must be an ISO 8601 string")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("needs an explicit timezone offset, for example 2026-11-02T19:00:00+08:00")
    return stamp.isoformat()


def text_files(package: dict) -> dict[str, str]:
    """The exact-text files the single-package writer creates for this package, as {name: text}."""
    content = package["content"]
    if package["kind"] == "reachweaver.manual-export.v1":
        if package["platform"] == "youtube":
            return {"title.txt": content.get("title") or "", "description.txt": content["text"]}
        return {"caption.txt": content["text"]}
    if package["human_only"]:
        return {}
    files = {"title.txt": content["title"]} if content["title"] is not None else {}
    files["post.txt"] = content["text"]
    return files


def _build_one(platform: str, variant: object, meta: object, generated_at: str | None,
               human_only: dict[str, bool]) -> dict:
    if not isinstance(variant, dict):
        raise ValidationError("variant must be a JSON object")
    if meta is not None and not isinstance(meta, dict):
        raise ValidationError("account_meta must be a JSON object of public account fields")
    if platform in MANUAL_PLATFORMS:
        return build_export(platform, variant, None, meta, generated_at=generated_at)
    if human_only.get(platform):
        raise ValidationError("is human-only: batches never include human-only destinations. Prepare its notes "
                              f"form on its own with `manual-export --platform {platform}` and write the post yourself")
    return build_package(platform, variant, None, meta, generated_at=generated_at)


def build_batch(data: object, *, generated_at: str | None = None) -> dict:
    """Preflight a whole batch. ``errors`` is non-empty when any item blocks; then nothing may be written."""
    if not isinstance(data, dict) or data.get("schema_version") != 1 or set(data) != {"schema_version", "items"}:
        raise ValidationError("Batch must be a JSON object with exactly schema_version (1) and items")
    items = data["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValidationError(f"Batch items must be a list of 1 to {MAX_ITEMS} entries")
    secrets = _sensitive_keys(data, "batch")
    if secrets:
        raise ValidationError("Batch contains secret-like fields; never put credentials in a batch: "
                              + "; ".join(secrets))
    allowed = set(MANUAL_PLATFORMS) | set(generic_platforms())
    human_only = {p["id"]: p["human_only"] is True for p in load_registry()["platforms"]}
    errors: list[str] = []
    warnings: list[str] = []
    packages: list[dict] = []
    summaries: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    for index, item in enumerate(items, start=1):
        where = f"item {index}"
        if not isinstance(item, dict) or not {"platform", "variant"} <= set(item) or set(item) - ITEM_FIELDS:
            errors.append(f"{where}: needs platform and variant, optionally account_meta and at, and nothing else")
            continue
        platform = item["platform"]
        if not isinstance(platform, str) or platform not in allowed:
            errors.append(f"{where}: platform {platform!r} is not a manual-export destination "
                          "(see `delivery-options` for each channel's route)")
            continue
        where = f"item {index} ({platform})"
        planned = None
        if "at" in item:
            try:
                planned = _advisory_time(item["at"])
            except (TypeError, ValueError) as exc:
                errors.append(f"{where}: at {exc}")
        try:
            package = _build_one(platform, item["variant"], item.get("account_meta"), generated_at, human_only)
        except ValidationError as exc:
            errors.append(f"{where}: {exc}")
            continue
        errors.extend(f"{where}: {e}" for e in package["preflight"]["errors"])
        warnings.extend(f"{where}: {w}" for w in package["preflight"]["warnings"])
        files = text_files(package)
        key = (platform, files.get("caption.txt") or files.get("post.txt") or files.get("description.txt") or "")
        if key in seen:
            warnings.append(f"{where}: same platform and text as item {seen[key]}; post each text once")
        seen.setdefault(key, index)
        packages.append(package)
        summaries.append({
            "directory": f"{index:02d}-{platform}",
            "platform": platform,
            "platform_label": package["platform_label"],
            "package_kind": package["kind"],
            "account": {k: v for k, v in package["account"].items() if v is not None},
            "files_sha256": {name: _sha256(text) for name, text in files.items()},
            "planned_local_time": planned,
            "status": package["status"],
            "published": False,
            "scheduled": False,
        })
    if any(s["planned_local_time"] for s in summaries):
        warnings.append("planned_local_time is advisory only: nothing was scheduled in ReachWeaver or on any platform")
    return {
        "schema_version": 1,
        "kind": BATCH_KIND,
        "status": BATCH_STATUS,
        "item_count": len(items),
        "ready_for_human_review": not errors,
        "errors": errors,
        "warnings": warnings,
        "items": summaries,
        "packages": packages,
        "published": False,
        "scheduled": False,
        "notice": NOTICE,
    }


def manifest(batch: dict) -> dict:
    """The batch summary written to batch-manifest.json (the full packages live in each folder)."""
    return {key: batch[key] for key in ("schema_version", "kind", "status", "item_count", "warnings", "items",
                                        "published", "scheduled", "notice")}


def write_batch(batch: dict, destination: str | Path) -> list[str]:
    """Write one NEW owner-only folder (0700) holding one package folder per item; never overwrite."""
    if batch.get("kind") != BATCH_KIND or batch.get("status") != BATCH_STATUS or batch.get("errors"):
        raise ValidationError("Refusing to write a batch with blocking preflight errors")
    if len(batch["packages"]) != batch["item_count"]:
        raise ValidationError("Batch is incomplete; refusing to write")
    folder = Path(destination)
    if os.path.lexists(folder):
        raise ValidationError("Output directory already exists; refusing to overwrite a prior export")
    folder.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(folder, 0o700)
    if os.name == "posix":
        os.chmod(folder, 0o700)
    written: list[str] = []
    for package, summary in zip(batch["packages"], batch["items"]):
        target = folder / summary["directory"]
        writer = write_export if package["kind"] == "reachweaver.manual-export.v1" else write_package
        written.extend(writer(package, target))
        for name, digest in summary["files_sha256"].items():
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
                raise ValidationError(f"{summary['directory']}/{name} does not match the reviewed text (SHA-256)")
    path = folder / MANIFEST
    write_private_file(path, json.dumps(manifest(batch), indent=2, ensure_ascii=False) + "\n")
    written.append(str(path))
    return written


def render_text(batch: dict) -> str:
    if batch["errors"]:
        lines = ["BLOCKED: nothing written"] + [f"  ERROR: {e}" for e in batch["errors"]]
    else:
        lines = [f"{batch['status']}: {batch['item_count']} manual package(s) for human review"]
        for item in batch["items"]:
            lines.append(f"  {item['directory']}: {item['platform_label']}"
                         + (f", planned {item['planned_local_time']} (advisory, not scheduled)"
                            if item["planned_local_time"] else ""))
    lines.extend(f"  WARNING: {w}" for w in batch["warnings"])
    return "\n".join(lines) + "\n"
