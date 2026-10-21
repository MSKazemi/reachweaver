"""``reachweaver posted URL``: record a post you made by hand, and its numbers, in one command. Local; sends nothing.

It is ``mark-shared --url`` plus ``metrics`` for a person who just posted in an app:

* The **platform** comes from the link's host through the small explicit map ``HOSTS`` (and, for Mastodon, the
  ``server`` of a configured Mastodon account). An unknown host is refused with a list of platform ids to pass as
  ``--platform``; nothing is guessed from the page and nothing is fetched.
* The **account** is the one configured account of that platform whose handle the link names, else the only
  configured (non-placeholder) account of that platform. Several and no clue: ``--account`` is required (the ids
  are listed). None: the post is recorded without an account and the output says so.
* A new link goes through ``storage.record_manual_publish`` (the same validation and the same terminal
  ``published_manual`` record as ``mark-shared --url``: it can never be approved, scheduled or sent). A link that
  is already recorded (by hand or by a ReachWeaver send) is not recorded again: the numbers are ADDED to that post
  with ``storage.store_metrics`` (source ``entered-by-hand``, observed now), which is how you update them later.
* Metric values are checked before anything is written, so a bad value leaves the database unchanged.

It never writes the send queue, never approves and never contacts a network.
"""
from __future__ import annotations

import difflib
import shlex
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from .policy import PLATFORMS, ValidationError

# Host -> platform id (a host also matches its subdomains, e.g. www., m., mobile.). Explicit and tested; every value
# is a ``policy.PLATFORMS`` id, the set ``mark-shared --platform`` accepts. Shorteners other than youtu.be are left
# out on purpose: a short link cannot be matched to the same post's long link without a network lookup.
HOSTS: dict[str, str] = {
    "bsky.app": "bluesky",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube", "youtu.be": "youtube",
    "threads.net": "threads", "threads.com": "threads",
    "x.com": "x", "twitter.com": "x",
    "facebook.com": "facebook",
    "t.me": "telegram",
    "dev.to": "devto",
    "reddit.com": "reddit",
    "pinterest.com": "pinterest",
    "hashnode.com": "hashnode", "hashnode.dev": "hashnode",
}
METRICS = ("likes", "replies", "reposts", "views")
NEXT_STEP = "reachweaver report --week"
HANDLE_FIELDS = ("handle", "channel_username")


def host_of(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").casefold()


def _mastodon_servers(accounts: dict) -> dict[str, str]:
    """Configured Mastodon server host -> "mastodon"."""
    out = {}
    for account in accounts.values():
        if account.get("platform") == "mastodon" and account.get("server"):
            server = host_of(str(account["server"]))
            if server:
                out[server] = "mastodon"
    return out


def platform_from_url(url: str, accounts: dict | None = None) -> str | None:
    """The platform id for ``url``'s host, or None when the host is not in ``HOSTS`` or a configured server."""
    host = host_of(url)
    if not host:
        return None
    known = {**HOSTS, **_mastodon_servers(accounts or {})}
    for name, platform in known.items():
        if host == name or host.endswith("." + name):
            return platform
    return None


def unknown_host_message(url: str) -> str:
    host = host_of(url) or "this link"
    label = host.removeprefix("www.").split(".")[0]
    close = difflib.get_close_matches(label, sorted(PLATFORMS), n=3, cutoff=0.5)
    guess = f" Did you mean --platform {close[0]}?" if close else ""
    return (f"Which network is {host}? ReachWeaver does not know it from the link.{guess} Add --platform with one "
            f"of: {', '.join(sorted(PLATFORMS))} (a Mastodon server: --platform mastodon). Nothing recorded")


def handle_in_url(platform: str, url: str) -> str | None:
    """The account handle a post link names, when its shape says so (casefolded, no '@'); None otherwise.

    Bluesky ``/profile/HANDLE/post/ID`` (a ``did:`` profile is not a handle), Mastodon/Threads/TikTok ``/@USER/...``,
    X ``/USER/status/ID``, Telegram ``t.me/CHANNEL/ID``, Instagram ``/USER/p/ID``, DEV ``/USER/SLUG``."""
    segments = [part for part in urlsplit(url.strip()).path.split("/") if part]
    value = None
    if platform == "bluesky" and len(segments) >= 2 and segments[0] == "profile" and not segments[1].startswith("did:"):
        value = segments[1]
    elif platform in ("mastodon", "threads", "tiktok", "youtube") and segments and segments[0].startswith("@"):
        value = segments[0]
    elif platform == "x" and len(segments) >= 3 and segments[1] == "status":
        value = segments[0]
    elif platform == "telegram" and len(segments) >= 2 and segments[0] not in ("s", "c", "joinchat"):
        value = segments[0]
    elif platform == "instagram" and len(segments) >= 3 and segments[1] in ("p", "reel", "tv"):
        value = segments[0]
    elif platform == "devto" and len(segments) >= 2:
        value = segments[0]
    return _norm(value) if value else None


def _norm(value: object) -> str:
    return str(value or "").strip().lstrip("@").split("@")[0].casefold()


def account_handles(account: dict) -> set[str]:
    return {_norm(account.get(field)) for field in HANDLE_FIELDS if account.get(field)}


def pick_account(platform: str, url: str, accounts: dict, chosen: str | None,
                 placeholders: list[str] | tuple[str, ...] = ()) -> tuple[str | None, str]:
    """``(account_id or None, plain note)``. Raises when several accounts fit and nothing says which one."""
    from .results import platform_name
    label = platform_name(platform)
    if chosen is not None:
        known = accounts.get(chosen)
        if known is None or known.get("platform") != platform:
            ids = [i for i, a in accounts.items() if a.get("platform") == platform]
            raise ValidationError(f"{chosen!r} is not a configured {platform} account"
                                  + (f"; yours: {', '.join(ids)}" if ids else "; none is configured")
                                  + ". Nothing recorded")
        return chosen, ""
    host = host_of(url)
    candidates = [i for i, a in accounts.items() if a.get("platform") == platform and i not in placeholders
                  and (platform != "mastodon" or host_of(str(a.get("server") or "")) == host)]
    named = handle_in_url(platform, url)
    if named:
        matched = [i for i in candidates if named in account_handles(accounts[i])]
        if len(matched) == 1:
            return matched[0], ""
        if candidates and not matched:
            return None, (f"The link names @{named}, which is none of your {label} accounts "
                          f"({', '.join(candidates)}), so it is recorded as posted by you without an account. "
                          "If it was one of them, run this again with --account ID.")
    if len(candidates) == 1:
        return candidates[0], ""
    if len(candidates) > 1:
        raise ValidationError(f"Which of your {label} accounts posted it? Add --account with one of: "
                              f"{', '.join(candidates)}. Nothing recorded")
    return None, f"No {label} account is set up here, so it counts as posted by you (no account)."


def metric_values(args) -> dict[str, object]:
    """The numbers given on the command line, validated before anything is written."""
    from .storage import parse_metric_value
    values = {key: getattr(args, key) for key in METRICS if getattr(args, key, None) is not None}
    for key, value in values.items():
        try:
            parse_metric_value(value)
        except ValidationError as exc:
            raise ValidationError(f"--{key}: {exc}. Nothing recorded") from None
    return values


def _label(platform_name: str, content: str, url: str) -> str:
    from .results import first_words
    return f"\"{first_words(content)}\" on {platform_name}" if content.strip() else f"your {platform_name} post {url}"


def _numbers_line(values: dict, sent: datetime | None, observed: datetime) -> str:
    from . import results
    shown = ", ".join(f"{value} {key}" for key, value in values.items())
    line = f"Numbers: {shown} (as of now, entered by hand)."
    if sent is not None:
        age = results.hours_between(sent, observed)
        if age < results.DUE_AFTER_HOURS:
            again = results.local_date(sent + timedelta(hours=results.MATCH_AGE_HOURS))
            line += f" For the 7-day comparison, update them around {again}."
    return line


def run(args, accounts: dict, placeholders: list[str]) -> int:
    """The ``posted`` command. Prints 2-4 plain lines and the next step."""
    from . import results
    from .storage import (_atomic, connect, find_manual_duplicate, get_post, manual_url_problem, parse_utc,
                          record_manual_publish, store_metrics, utc_now)
    if not args.url:
        raise ValidationError("Give the link to your post, for example: reachweaver posted "
                              "https://bsky.app/profile/you.bsky.social/post/3k... --likes 12 --replies 3")
    problem = manual_url_problem(args.url)
    if problem:
        raise ValidationError(problem.replace("--url", "The link") + ". Nothing recorded")
    if results.has_control(args.url):  # never echo a direction override or control character to the terminal
        raise ValidationError("The link contains control or text-direction characters; copy it again from the "
                              "app's share menu. Nothing recorded")
    url = args.url.strip()
    if args.platform is not None:
        if args.platform not in PLATFORMS:
            close = difflib.get_close_matches(args.platform, sorted(PLATFORMS), n=1)
            raise ValidationError(f"Unknown platform {args.platform!r}." + (f" Did you mean {close[0]}?" if close else "")
                                  + f" Known: {', '.join(sorted(PLATFORMS))}. Nothing recorded")
        platform = args.platform
    else:
        found = platform_from_url(url, accounts)
        if found is None:
            raise ValidationError(unknown_host_message(url))
        platform = found
    values = metric_values(args)
    name = results.platform_name(platform)
    again = f"reachweaver posted {shlex.quote(url)} --likes N"
    observed = parse_utc(utc_now())
    with connect(args.db) as db:
        existing = find_manual_duplicate(db, platform, url)
        if existing is not None:
            post = get_post(db, existing)
            label = _label(name, post["content"] or "", url)
            if not values:
                print(f"Already recorded: {label} (post {existing}). Nothing changed.")
                print(f"Add its numbers: {again} --replies N --reposts N --views N")
                return 0
            with _atomic(db):
                store_metrics(db, existing, observed, results.HAND_ENTERED_SOURCE, **values)
            print(f"Updated numbers for {label}.")
            print(_numbers_line(values, results.to_time(post["send_started_at"]),
                                datetime.fromisoformat(observed)))
            if args.text is not None or args.at is not None or args.account is not None:
                print("The post itself was already recorded, so --text, --at and --account were not changed.")
            print(f"Next: {NEXT_STEP}")
            return 0
    account_id, account_note = pick_account(platform, url, accounts, args.account, placeholders)
    posted_at = None
    if args.at is not None:
        from .when import parse_when
        posted_at = parse_when(args.at, what="--at")
    from .metrics_fetch import platform_id_from_url
    remote_id = platform_id_from_url(platform, url, accounts.get(account_id) if account_id else None)
    content = args.text or ""
    with connect(args.db) as db:
        made = record_manual_publish(db, platform=platform, url=url, account_id=account_id, content=content,
                                     posted_at=posted_at, remote_id=remote_id, link_is_enough=True)
        if values:
            with _atomic(db):
                store_metrics(db, made["post_id"], observed, results.HAND_ENTERED_SOURCE, **values)
        sent = results.to_time(get_post(db, made["post_id"])["send_started_at"])
    when = results.local_date(sent) if sent else "now"
    who = f" as {account_id}" if account_id else ""
    print(f"Recorded {_label(name, content, url)}, posted {when}{who} (post {made['post_id']}). "
          "Only noted on this computer; nothing was posted or sent.")
    if account_note:
        print(account_note)
    if values:
        print(_numbers_line(values, sent, datetime.fromisoformat(observed)))
    else:
        print(f"No numbers yet. When you have them: {again} --replies N --reposts N --views N")
    print(f"Next: {NEXT_STEP}")
    return 0
