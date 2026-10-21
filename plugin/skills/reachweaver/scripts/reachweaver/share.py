"""Simple offline social-sharing handoff. No network, DB, login or send path.

Turn plain text into existing manual-export packages for up to ten deliberately
chosen platforms. Human-only communities get private notes, not prepared posts.

Text networks that ReachWeaver can also send to through its API (Bluesky, Mastodon,
Telegram, LinkedIn) get the same kind of copy-only package (owner decision 2026-10-21),
checked with the rule ``draft`` and ``check`` use. Share still writes no database,
queue or post row: sending from ReachWeaver stays the separate draft, review and
approval flow. Each caption is fitted to its network deterministically
(``share_tailor``) and attached photos or videos get offline header checks
(``media.check_file_for_targets``).
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any

from . import home
from .manual_export import MANUAL_PLATFORMS, MEDIA_NOT_CHECKED, YOUTUBE_TITLE_MISSING, build_export, write_export
from .inputs import split_list_args
from .manual_package import build_package, generic_platforms, write_package
from .policy import SUPPORTED_LIVE, ValidationError, validate_post
from .privacy import write_private_file
from .share_tailor import RULES, fit, phrase_warnings

SEND_PATH = None
MAX_DESTINATIONS = 10
MAX_TEXT_BYTES = 65536
ALIASES = {
    "facebook pages": "facebook", "facebook_pages": "facebook",
    "facebook-pages": "facebook", "youtube shorts": "youtube",
    "youtube-shorts": "youtube", "twitter": "x", "dev.to": "dev",
    "rednote": "xiaohongshu", "little red book": "xiaohongshu",
    "bsky": "bluesky",
}
SUGGESTED = ("instagram", "threads", "tiktok", "youtube", "facebook", "linkedin", "bluesky",
             "mastodon", "telegram", "pinterest", "x", "dev", "xiaohongshu", "sharechat")
IMAGE_SUFFIXES = frozenset((".jpg", ".jpeg", ".png", ".webp"))
VIDEO_SUFFIXES = frozenset((".mp4", ".mov", ".webm"))
API_NOTE = ("Copy and post it yourself; to send it from ReachWeaver instead, connect the account and use "
            "draft → review.")
# Share id -> media.py constraint table. Every other network has no sourced media table yet.
MEDIA_TABLES = {"bluesky": "bluesky", "mastodon": "mastodon", "instagram": "instagram", "tiktok": "tiktok",
                "linkedin": "linkedin", "youtube": "youtube_shorts", "dev": "devto"}
MEDIA_TABLE_NOTES = {"youtube_shorts": " (YouTube Shorts rules: only if you post it as a Short)"}
# One short tip per network for OPEN-FIRST.md. Each restates a fact ReachWeaver already holds with a source
# (manual_export.FACTS, the capability and distribution registries, policy limits); none is a reach promise.
TIPS = {
    "instagram": "A caption holds up to 2200 characters and 30 hashtags; write it so it still makes sense if "
                 "the link in it cannot be tapped.",
    "threads": "Threads allows at most 5 links and 1 topic tag per post.",
    "tiktok": "Choose who can see the video yourself (ReachWeaver never pre-selects it), and do not count on a "
              "caption link being tappable (unverified).",
    "youtube": "Keep the title to 100 characters or fewer; links in the description are clickable only for "
               "channels that meet YouTube's requirements.",
    "facebook": "Post it on a Facebook Page you manage; personal profiles and groups are not covered here.",
    "bluesky": "A Bluesky post holds 300 graphemes; a link in the text becomes a link when you post it.",
    "mastodon": "Mastodon counts a link as 23 characters by default however long it is, so no link shortener is "
                "needed.",
    "linkedin": "ReachWeaver's LinkedIn sending covers personal profiles only; post on a company Page by hand.",
}
TIP_LABEL = "Tip (from ReachWeaver's sourced notes; a suggestion, not a promise of reach): "
YOUTUBE_TITLE_SHARE = 'YouTube needs a video title: add --title "…"'
MEDIA_CHECK_NOTE = ("Media header checks ran offline from --attach; alt text, captions, rights and consent "
                    "were not checked (a media manifest with media-check covers those)")


def api_text_platforms() -> tuple[str, ...]:
    """Text networks ReachWeaver can send to through its API, offered here as copy-only handoffs."""
    from .distribution import load_registry
    entries = {p["id"]: p for p in load_registry()["platforms"]}
    return tuple(sorted(p for p in SUPPORTED_LIVE if p in entries
                        and entries[p]["reachweaver_delivery"] == "mock_tested_text"
                        and entries[p]["human_only"] is not True))


def available() -> tuple[str, ...]:
    return tuple(sorted(set(MANUAL_PLATFORMS) | set(generic_platforms()) | set(api_text_platforms())))


def unknown_network(name: str) -> str:
    """``Unknown network 'blusky'. Did you mean bluesky?`` plus where to see every name."""
    import difflib
    names = list(available()) + list(ALIASES)
    close = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
    guess = f" Did you mean {ALIASES.get(close[0], close[0])}?" if close else ""
    return (f"Unknown network {name!r}.{guess} 'reachweaver share --list' shows the easy names; "
            "'reachweaver delivery-options' lists every id")


def destinations(raw: list[str]) -> list[str]:
    allowed = set(available())
    chosen: list[str] = []
    for token in split_list_args(raw):
        key = token.casefold()
        key = ALIASES.get(key, key)
        if key not in allowed:
            raise ValidationError(unknown_network(key))
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
    popular = ", ".join(x for x in SUGGESTED if x in available())
    print("Popular networks: " + popular)
    selected = destinations(args.platform) if args.platform else _ask_networks(popular)
    human = _human_only()
    if any(p not in human for p in selected) and args.text is None and args.file is None:
        print("For several lines, save your caption in a .txt file and use --file instead.")
        args.text = _ask("Caption or post text: ")
    if any(p not in human for p in selected) and not args.attach:
        attach = _ask_attachment()
        if attach is not None:
            args.attach = [attach]
    if any(p in human for p in selected) and not args.note:
        print("For communities, you write the final post yourself. Only short notes are prepared.")
        args.note = [_ask("One short reminder or idea: ")]
    if "youtube" in selected and not args.title:
        args.title = _ask("YouTube video title: ")
    return selected


GUIDE_ATTEMPTS = 5  # wrong answers in a row before the guide stops (nothing is written)


def _ask_networks(popular: str) -> list[str]:
    """Ask until the answer names known networks; an empty or unknown answer re-asks with the list."""
    for _ in range(GUIDE_ATTEMPTS):
        answer = _ask("Where to share? (names separated by commas): ")
        if not answer:
            print("Type at least one network, for example: " + popular)
            continue
        try:
            return destinations([answer])
        except ValidationError as exc:
            print(f"{exc}. Try again.")
    raise ValidationError("No network chosen. Nothing was written. 'reachweaver share --list' shows the names")


def _ask_attachment() -> Path | None:
    """A photo or video path for --attach, or None when the person presses Enter."""
    for _ in range(GUIDE_ATTEMPTS):
        answer = _ask("Attach a photo or video? (path, or Enter to skip): ")
        answer = answer.strip().strip("'\"")  # a dragged-in file often arrives quoted
        if not answer:
            return None
        path = Path(answer).expanduser()
        if path.is_file():
            return path
        print(f"File not found: {path} (looked in {path.absolute().parent}). Try again, or press Enter to skip.")
    raise ValidationError("No usable attachment given. Nothing was written")


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


def media_findings(files: list[Path], platforms: list[str]) -> dict[str, list[dict]]:
    """Per platform: one offline header check per attached file (``media.check_file_for_targets``)."""
    from .media import check_file_for_targets
    tables = sorted({MEDIA_TABLES[p] for p in platforms if p in MEDIA_TABLES})
    reports = [check_file_for_targets(path, tables) for path in files]
    findings: dict[str, list[dict]] = {}
    for platform in platforms:
        table = MEDIA_TABLES.get(platform)
        rows = []
        for report in reports:
            target = report["targets"].get(table) if table else None
            rows.append({"path": report["path"], "kind": report["kind"], "detected": report["detected"],
                         "table": table if target and target["table"] else None,
                         "problems": target["problems"] if target else [],
                         "advice": target["advice"] if target else [],
                         "not_checked": target["not_checked"] if target else [],
                         "notes": report["notes"]})
        findings[platform] = rows
    return findings


def _media_lines(rows: list[dict]) -> list[str]:
    """Plain warnings for the package: each file, what was found and what was not checked."""
    lines = []
    for row in rows:
        name = Path(row["path"]).name
        detected = row["detected"]
        if row["kind"] is None:
            lines.append(f"Media {name}: " + "; ".join(row["notes"]))
            continue
        shape = (f"{detected['width']}x{detected['height']}" if detected["width"] != "unknown" else "size unknown")
        if row["kind"] == "video":
            shape += (f", {detected['duration_seconds']}s" if detected["duration_seconds"] != "unknown"
                      else ", duration unknown")
        head = f"Media {name} ({detected['container']}, {shape})"
        for note in row["notes"]:
            lines.append(f"{head}: {note}")
        if row["table"] is None:
            lines.append(f"{head}: no sourced {row['kind']} limits for this network in ReachWeaver; check the "
                         "app's own requirements")
            continue
        suffix = MEDIA_TABLE_NOTES.get(row["table"], "")
        for problem in row["problems"]:
            lines.append(f"MEDIA PROBLEM{suffix}: {problem}")
        for advice in row["advice"]:
            lines.append(f"Media advice{suffix}: {advice}")
        if not row["problems"] and not row["advice"]:
            lines.append(f"{head}: no problem found against the sourced {row['table']} {row['kind']} limits{suffix}")
        if row["not_checked"]:
            lines.append(f"{head}: not checked (no sourced value or unreadable header): "
                         + ", ".join(row["not_checked"]))
    return lines


def _api_extras(platform: str, package: dict) -> None:
    """Copy-only package for a network ReachWeaver can also send to: the draft/check text rule and the note."""
    text = package["content"]["text"]
    errors = package["preflight"]["errors"]
    issues = validate_post(platform, text)  # the exact rule draft and check apply to this network
    errors.extend(issues if not errors else [i for i in issues if "limit" in i])
    package["ready_for_human_review"] = not errors
    package["limits"]["text"] = {"value": None, "display": RULES[platform][0],
                                 "note": "Same rule as draft and check (policy.validate_post)."}
    package["checklist"] = [f"Text limit: {RULES[platform][0]}." if step.startswith("Text limit:") else step
                            for step in package["checklist"]]
    if platform == "bluesky":
        from .richtext import bluesky_preview
        package["content"]["bluesky_preview"] = dict(
            bluesky_preview(text), note="Offline estimate of links and tags; the Bluesky app builds its own when "
                                        "you post")
    package["notice"] += " " + API_NOTE
    package["checklist"].insert(0, API_NOTE)


def make_packages(
    platforms: list[str], text: str | None, notes: list[str],
    title: str | None, fmt: str | None, attachments: list[Path],
    media_manifest: Path | None, *, shorten: bool = True,
) -> tuple[list[tuple[str, dict]], list[str]]:
    human = _human_only()
    api = set(api_text_platforms())
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
    checks = media_findings(attachments, platforms) if attachments and media_manifest is None else {}
    packages = []
    for platform in platforms:
        variant: dict[str, Any]
        tailoring = None
        if platform in human:
            variant = {"platform": platform, "talking_points": notes}
        else:
            tailoring = fit(platform, text or "", shorten=shorten)
            variant = {"platform": platform, "text": tailoring["text"],
                       "format": _format(platform, fmt, attachments)}
            if platform == "facebook":
                variant["platform_options"] = {"target_type": "page"}
            if platform == "youtube":
                variant["platform_options"] = {"title": title}
        package = (build_export(platform, variant, media_manifest)
                   if platform in MANUAL_PLATFORMS else
                   build_package(platform, variant, media_manifest, copy_handoff=platform in api))
        if platform in api:
            _api_extras(platform, package)
        _share_section(platform, package, tailoring, checks.get(platform), api)
        packages.append((platform, package))
        errors.extend(f"{platform}: {e}" for e in package["preflight"]["errors"])
    return packages, errors


def _share_section(platform: str, package: dict, tailoring: dict | None, media: list[dict] | None,
                   api: set[str]) -> None:
    """Record what share changed or checked, and show it as warnings in the package and the plan."""
    route = ("human_only_notes" if package.get("human_only") is True else
             "api_capable_copy" if platform in api else "manual_copy")
    package["share"] = {"route": route, "tailoring": tailoring, "media_checks": media or [],
                        "send_from_reachweaver": API_NOTE if platform in api else None}
    warnings = package["preflight"]["warnings"]
    if tailoring and tailoring["note"]:
        warnings.insert(0, tailoring["note"])
    if tailoring:
        warnings.extend(phrase_warnings(platform, tailoring["text"]))
    if media:
        warnings[:] = [w for w in warnings if MEDIA_NOT_CHECKED not in w]
        warnings.extend(_media_lines(media))
        warnings.append(MEDIA_CHECK_NOTE)


def write_one(platform: str, package: dict, destination: Path) -> None:
    if platform in MANUAL_PLATFORMS:
        write_export(package, destination)
    else:
        write_package(package, destination)


def _output_path(path: Path | None) -> Path:
    if path is not None:
        if os.path.lexists(path):
            raise ValidationError(f"Output already exists: {path}. Choose a fresh folder.")
        return path
    root = home.default_path(Path("state") / ("share-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
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
            "6. Post it yourself if you decide to, then copy and keep the link to your post.", "",
        ]
        if (pkg.get("share") or {}).get("send_from_reachweaver"):
            lines += [pkg["share"]["send_from_reachweaver"], ""]
        if platform in TIPS and not human:
            lines += [TIP_LABEL + TIPS[platform], ""]
        for warning in pkg["preflight"]["warnings"]:
            lines.append("- Check: " + warning)
        if pkg["preflight"]["warnings"]:
            lines.append("")
    if files:
        lines += [
            "## Media files you selected", "",
            "Not copied or uploaded. Their headers were checked offline against the sourced limits each network "
            "has in ReachWeaver (see the Check lines above); rights, consent, alt text and captions are yours "
            "to check.", "",
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
    if args.web:
        if (args.platform or args.guide or args.list or args.text is not None or
                args.file is not None or args.note or args.title is not None or
                args.format is not None or args.attach or args.media is not None or
                args.out is not None or args.preview or args.no_shorten):
            raise ValidationError(
                "The local browser composer takes its posts and network choices in the "
                "browser. Run 'reachweaver share --web [--open]' without caption flags."
            )
        from .share_browser import serve
        return serve(port=args.port, open_browser=args.open, db=getattr(args, "db", None))
    if args.open or args.port:
        raise ValidationError("--open and --port need --web (local browser composer).")
    if args.list:
        print("Easy choices: " + ", ".join(x for x in SUGGESTED if x in available()))
        print("Use platform IDs from 'reachweaver delivery-options' for more networks.")
        print("Facebook means a managed Page, never a profile or group.")
        print("Bluesky, LinkedIn, Mastodon and Telegram: copy and post yourself, or connect the account and use "
              "the separate draft, review and approval flow to send from ReachWeaver.")
        print("Share is offline: it prepares handoff files, never publishing or scheduling.")
        return 0
    guided = bool(args.guide or not args.platform)
    platforms = _guided(args) if guided else destinations(args.platform)
    text = _caption(args)
    files = _attachments(args.attach)
    packages, errors = make_packages(platforms, text, args.note or [], args.title,
                                     args.format, files, args.media, shorten=not args.no_shorten)
    # In this command the title is a flag, not a variant field: say which flag to add.
    errors = [f"youtube: {YOUTUBE_TITLE_SHARE}" if error == f"youtube: {YOUTUBE_TITLE_MISSING}" else error
              for error in errors]
    if errors:
        print("BLOCKED. No files were written:", file=sys.stderr)
        for error in errors:
            print(" - " + error, file=sys.stderr)
        return 2
    print("Review the handoff plan:")
    for platform, pkg in packages:
        mode = "YOU WRITE IT" if pkg.get("human_only") else "MANUAL COPY"
        tailoring = (pkg.get("share") or {}).get("tailoring") or {}
        fitted = (f"; SHORTENED {tailoring['original_chars']} -> {tailoring['chars']} characters"
                  if tailoring.get("shortened") else "")
        print(f" - {pkg['platform_label']}: {mode}; format {pkg['content'].get('format')}{fitted}")
        for warning in pkg["preflight"]["warnings"]:
            print("   Check: " + warning)
    if files:
        print(f"Media: {len(files)} file(s) checked offline from their headers; not copied or uploaded")
    if args.preview:
        print("PREVIEW ONLY: nothing saved, uploaded or published.")
        return 0
    # Saving only writes private local files (nothing is sent), so Enter means yes.
    if guided and _ask("Save these files? [Y/n]: ").casefold() not in ("", "y", "yes"):
        print("Cancelled: nothing written.")
        return 0
    out = _output_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.mkdir(out, 0o700)
    if os.name == "posix":
        os.chmod(out, 0o700)
    for platform, package in packages:
        write_one(platform, package, out / platform)
    write_private_file(out / "OPEN-FIRST.md", _readme(packages, files))
    print(f"Prepared {len(packages)} private manual packages in {out}")
    print("Start with: " + str(out / "OPEN-FIRST.md"))
    print("NOT PUBLISHED: open each social app and share manually after review.")
    return 0
