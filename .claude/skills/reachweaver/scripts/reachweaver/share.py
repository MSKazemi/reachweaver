"""Simple offline social-sharing handoff. No network, DB, login or send path.

Turn plain text into existing manual-export packages for up to ten deliberately
chosen platforms. Human-only communities get private notes, not prepared posts.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys

from .manual_export import MANUAL_PLATFORMS, build_export, write_export
from .manual_package import build_package, generic_platforms, write_package
from .policy import SUPPORTED_LIVE, ValidationError
from .privacy import write_private_file

SEND_PATH = None
MAX_DESTINATIONS = 10
MAX_TEXT_BYTES = 65536
ALIASES = {
    "facebook pages": "facebook", "facebook_pages": "facebook",
    "facebook-pages": "facebook", "youtube shorts": "youtube",
    "youtube-shorts": "youtube", "twitter": "x", "dev.to": "dev",
    "rednote": "xiaohongshu", "little red book": "xiaohongshu",
}
SUGGESTED = ("instagram", "threads", "tiktok", "youtube", "facebook",
             "pinterest", "x", "dev", "xiaohongshu", "sharechat")
IMAGE_SUFFIXES = frozenset((".jpg", ".jpeg", ".png", ".webp"))
VIDEO_SUFFIXES = frozenset((".mp4", ".mov", ".webm"))


def available() -> tuple[str, ...]:
    return tuple(sorted(set(MANUAL_PLATFORMS) | set(generic_platforms())))


def destinations(raw: list[str]) -> list[str]:
    allowed = set(available())
    chosen: list[str] = []
    for group in raw:
        for token in group.split(","):
            key = token.strip().casefold()
            if not key:
                continue
            key = ALIASES.get(key, key)
            if key not in allowed:
                if key in SUPPORTED_LIVE:
                    raise ValidationError(
                        key + " uses the separate draft, check, approve, publish flow. "
                        "Share creates offline manual handoffs only."
                    )
                raise ValidationError(
                    f"Unknown manual destination {key!r}; try 'reachweaver share --list' "
                    "or 'reachweaver delivery-options'."
                )
            if key in chosen:
                raise ValidationError(f"Duplicate destination {key!r}")
            chosen.append(key)
    if not chosen:
        raise ValidationError("Choose a network with --platform instagram,threads or --guide")
    if len(chosen) > MAX_DESTINATIONS:
        raise ValidationError(f"Choose at most {MAX_DESTINATIONS} networks per package")
    return chosen


def _human_only() -> set[str]:
    from .distribution import load_registry
    return {p["id"] for p in load_registry()["platforms"] if p["human_only"]}


def _ask(message: str) -> str:
    try:
        return input(message).strip()
    except EOFError as exc:
        raise ValidationError("Input ended. Nothing was written.") from exc


def _guided(args) -> list[str]:
    if not sys.stdin.isatty():
        raise ValidationError(
            "Share guide needs an interactive terminal. Example: "
            "reachweaver share --platform instagram --text 'Your caption'"
        )
    print("ReachWeaver Share: prepare a post without logging in or publishing.")
    print("Popular networks: " + ", ".join(x for x in SUGGESTED if x in available()))
    if not args.platform:
        args.platform = [_ask("Where to share? (names separated by commas): ")]
    selected = destinations(args.platform)
    human = _human_only()
    if any(p not in human for p in selected) and args.text is None and args.file is None:
        print("For several lines, save your caption in a .txt file and use --file instead.")
        args.text = _ask("Caption or post text: ")
    if any(p in human for p in selected) and not args.note:
        print("For communities, you write the final post yourself. Only short notes are prepared.")
        args.note = [_ask("One short reminder or idea: ")]
    if "youtube" in selected and not args.title:
        args.title = _ask("YouTube video title: ")
    return selected


def _caption(args) -> str | None:
    if args.file is not None:
        if args.file.stat().st_size > MAX_TEXT_BYTES:
            raise ValidationError("Text file exceeds 64 KiB")
        try:
            return args.file.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise ValidationError("The file must contain valid UTF-8 text") from exc
    if args.text is not None and len(args.text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValidationError("Caption exceeds 64 KiB")
    return args.text


def _attachments(paths: list[Path] | None) -> list[Path]:
    files = paths or []
    if len(files) > 10:
        raise ValidationError("At most ten files may be listed for manual attachment")
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"Attachment is missing, a directory or a symlink: {path}")
        if not path.stat().st_size:
            raise ValidationError(f"Attachment is empty: {path}")
        if path.suffix.casefold() not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
            raise ValidationError(f"Unsupported extension on {path.name}; use a photo or video file")
    return files


def _format(platform: str, chosen: str | None, files: list[Path]) -> str:
    if chosen is not None:
        return chosen
    if platform in ("youtube", "tiktok"):
        return "video"
    if any(p.suffix.casefold() in VIDEO_SUFFIXES for p in files):
        return "video"
    if platform == "instagram" or files:
        return "image"
    return "text"


def make_packages(
    platforms: list[str], text: str | None, notes: list[str],
    title: str | None, fmt: str | None, attachments: list[Path],
    media_manifest: Path | None,
) -> tuple[list[tuple[str, dict]], list[str]]:
    human = _human_only()
    errors: list[str] = []
    if title is not None and "youtube" not in platforms:
        errors.append("--title is only for YouTube")
    if notes and not any(p in human for p in platforms):
        errors.append("--note is only for human-only community destinations")
    if any(p in human for p in platforms) and not notes:
        errors.append("Human-only communities need --note, not pre-written post text")
    if any(p not in human for p in platforms) and text is None:
        errors.append("Add a caption with --text or --file")
    if all(p in human for p in platforms) and text is not None:
        errors.append("This selection is human-only. Remove --text/--file and supply --note")
    if errors:
        return [], errors
    packages = []
    for platform in platforms:
        if platform in human:
            variant = {"platform": platform, "talking_points": notes}
        else:
            variant = {"platform": platform, "text": text,
                       "format": _format(platform, fmt, attachments)}
            if platform == "facebook":
                variant["platform_options"] = {"target_type": "page"}
            if platform == "youtube":
                variant["platform_options"] = {"title": title}
        package = (build_export(platform, variant, media_manifest)
                   if platform in MANUAL_PLATFORMS else
                   build_package(platform, variant, media_manifest))
        packages.append((platform, package))
        errors.extend(f"{platform}: {e}" for e in package["preflight"]["errors"])
    return packages, errors


def _output_path(path: Path | None) -> Path:
    if path is not None:
        if os.path.lexists(path):
            raise ValidationError(f"Output already exists: {path}. Choose a fresh folder.")
        return path
    root = Path("state") / ("share-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    result = root
    index = 1
    while os.path.lexists(result):
        result = Path(str(root) + f"-{index}")
        index += 1
    return result


def _readme(packages: list[tuple[str, dict]], files: list[Path]) -> str:
    lines = [
        "# Your posts: what to do next", "",
        "NOT PUBLISHED. These private files are only drafts and handoff instructions.",
        "ReachWeaver has not logged in, connected, uploaded, scheduled or published.", "",
    ]
    for number, (platform, pkg) in enumerate(packages, 1):
        human = pkg.get("human_only") is True
        content_file = ("NOTES.md" if human else
                        "title.txt and description.txt" if platform == "youtube" else
                        "caption.txt" if platform in MANUAL_PLATFORMS else "post.txt")
        lines += [
            f"## {number}. {pkg['platform_label']}", "",
            f"1. Open {pkg['platform_label']} and choose the correct account yourself.",
            f"2. Open the folder {platform} and read {content_file}.",
            ("3. Write the final message in your own words. This is a human-only venue."
             if human else "3. Review and copy the text into the app's native composer."),
            "4. Review that platform's CHECKLIST.md (rights, labels, audience and rules).",
            "5. Upload your own media if needed, choose privacy and publishing time yourself.",
            "6. Publish manually if you decide to, then keep the native post URL.", "",
        ]
        for warning in pkg["preflight"]["warnings"]:
            lines.append("- Check: " + warning)
        if pkg["preflight"]["warnings"]:
            lines.append("")
    if files:
        lines += [
            "## Media files you selected", "",
            "References only: not copied, uploaded, checked for media specs or rights.", "",
        ]
        for path in files:
            lines.append("- " + str(path).replace("\n", " ").replace("\r", " "))
        lines.append("")
    lines += [
        "Keep each platform's community rules in mind; do not send bulk unsolicited posts.",
        "No password, session cookie or token is required for this handoff.",
        "For repeat-post tracking after a manual post, see 'reachweaver mark-shared'.", "",
    ]
    return "\n".join(lines)


def run(args) -> int:
    """Perform only local preflight and optional private file creation."""
    if args.list:
        print("Easy choices: " + ", ".join(x for x in SUGGESTED if x in available()))
        print("Use platform IDs from 'reachweaver delivery-options' for more networks.")
        print("Facebook means a managed Page, never a profile or group.")
        print("Live-capable networks use the separate approval-first draft workflow.")
        print("Share is offline: it prepares handoff files, never publishing or scheduling.")
        return 0
    guided = bool(args.guide or not args.platform)
    platforms = _guided(args) if guided else destinations(args.platform)
    text = _caption(args)
    files = _attachments(args.attach)
    packages, errors = make_packages(platforms, text, args.note or [], args.title,
                                     args.format, files, args.media)
    if errors:
        print("BLOCKED. No files were written:", file=sys.stderr)
        for error in errors:
            print(" - " + error, file=sys.stderr)
        return 2
    print("Review the handoff plan:")
    for platform, pkg in packages:
        mode = "YOU WRITE IT" if pkg.get("human_only") else "MANUAL COPY"
        print(f" - {pkg['platform_label']}: {mode}; format {pkg['content'].get('format')}")
        for warning in pkg["preflight"]["warnings"]:
            print("   Check: " + warning)
    if files:
        print(f"Media references: {len(files)} file(s), not uploaded or validated")
    if args.preview:
        print("PREVIEW ONLY: nothing saved, uploaded or published.")
        return 0
    if guided and _ask("Save the private handoff folder? [y/N]: ").casefold() not in ("y", "yes"):
        print("Cancelled: nothing written.")
        return 0
    out = _output_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.mkdir(out, 0o700)
    if os.name == "posix":
        os.chmod(out, 0o700)
    for platform, package in packages:
        if platform in MANUAL_PLATFORMS:
            write_export(package, out / platform)
        else:
            write_package(package, out / platform)
    write_private_file(out / "OPEN-FIRST.md", _readme(packages, files))
    print(f"Prepared {len(packages)} private manual packages in {out}")
    print("Start with: " + str(out / "OPEN-FIRST.md"))
    print("NOT PUBLISHED: open each social app and share manually after review.")
    return 0
