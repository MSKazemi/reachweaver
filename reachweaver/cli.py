"""Local-first CLI with zero implicit external side effects."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .accounts import bluesky_service, credential_source, load_accounts, missing_credentials
from .attribution import (
    CAMPAIGN_TYPES, attribution_report, build_utm_link, campaign_destinations,
    load_analytics_csv, load_attribution_outcomes, load_github_traffic, load_seo_briefs,
)
from .attribution import ATTRIBUTION_SOURCES
from .web_analytics import combine_ga4, combine_gsc, load_ga4_csv, load_gsc_csv
from .connections import check_connection, mastodon_instance_limits
from .identity import IDENTITY_REQUIRED, current_fingerprint, refusal_text, verify_identity_online
from .send_options import ALWAYS_BOUND_PLATFORMS as SEND_OPTIONS_ALWAYS_BOUND
from .send_options import SEND_OPTIONS_UNAVAILABLE
from .send_options import describe as send_options_describe
from .send_options import fingerprint as send_options_fingerprint
from .credential_hygiene import audit_environment, render_audit
from .audience import DEFAULT_MAX_AGE_DAYS as AUDIENCE_MAX_AGE_DAYS
from .audience import DEFAULT_TOP_HOURS as AUDIENCE_TOP_HOURS
from .audience import derive_slots as derive_audience_slots
from .audience import load_audience_activity
from .calendar import apply_calendar, propose_calendar
from .caps import effective_caps, effective_rate_limits, load_caps, providers_for
from .capabilities import (
    DEFAULT_MAX_AGE_DAYS, FORMATS, load_registry as load_capabilities,
    require_capability, stale_entries, summarize,
)
from .campaigns import OBJECTIVES, read_json, stage_campaign, validate_campaign
from .claims import DEFAULT_DUPLICATE_THRESHOLD, check_claims
from . import confirmation
from .account_advisor import FORMATS as ADVISOR_FORMATS
from . import account_kit, persona_guard
from . import profile as profile_store
from .distribution import GOALS, build_distribution_plan
from .health import (build_health, dead_letter, exit_code as health_exit_code, render_dead_letter, render_health,
                     sanitize_error_code)
from .experiments import evaluate_experiment
from .growth import next_hypotheses, preregister, verify_preregistration, write_preregistration
from .growth_readout import load_results, render_next, render_readout, render_simulation, run_readout, simulate_readout
from .optout import describe_mentions, load_optout, optout_mentions, publish_block_reason
from .privacy import apply_purge, export_data, load_policy, plan_purge, privacy_status, write_private_file
from .media import CREATORS, media_check
from .metrics_fetch import DEFAULT_MAX_POSTS, MAX_POSTS_LIMIT, fetch_metrics
from .preview import build_preview, render_markdown as render_preview_markdown, render_text as render_preview_text
from .profile_audit import load_snapshot as load_profile_snapshot, audit_profiles, audit_posts, render_summary as render_profile_audit
from .profile_metrics_csv import merge_authorized_csv
from .suggest import build_suggestions, render_text as render_suggestions
from .manual_export import MANUAL_PLATFORMS, build_export, write_export
from .manual_package import build_package, generic_platforms, write_package
from .pilot import DEFAULT_MIN_WEEKS, load_pilot_log, pilot_report, render_report as render_pilot_report
from .timing_analysis import analyze_posting_times
from .worker_log import claim_refusal_reason, error_code as worker_error_code, open_worker_log
from .schema_export import EXPORT_TYPES, export_documents, render_jsonl
from .schemas import TYPES as SCHEMA_TYPES, validate as validate_schema
from .policy import (CONSERVATIVE_TEXT_LIMITS, LINKEDIN_ESCAPED_MAX, OWNER_TRIGGERED_ONLY, OWNER_TRIGGERED_REASON,
                     PLATFORMS, SUPPORTED_LIVE,
                     SUPPORTED_LIVE_VIDEO, ValidationError, live_opt_in, mastodon_text_limit,
                     require_live_gate, runtime_environment, validate_post)
from .attachments import MEDIA_PLATFORMS, UNSUPPORTED_PLATFORM
from .tiktok import COMMERCIAL_CHOICES as TIKTOK_COMMERCIAL
from .tiktok import MODES as TIKTOK_MODES
from .tiktok import PRIVACY_LEVELS as TIKTOK_PRIVACY_LEVELS
from .publishers import (MEDIA_ONLY_PLATFORMS, PublishError, close_http_error, publish, publish_video, reset_write_marker,
                         write_started)
from .video import mode as video_mode
from .ratelimit import (
    AMBIGUOUS, SESSIONS_PER_PUBLISH, classify_send_failure, cooldown_scopes, is_rate_limited,
    preflight_retries, preflight_with_backoff, record_rate_limit, recorded_session_usage,
    reserve_bluesky_sessions, session_budget_problem,
)
from .report_format import REPORT_FORMATS, render_report
from .richtext import bluesky_preview, deterministic_tid, mastodon_length
from .post_view import (build_post_view, repeat_findings as _repeat_findings, approval_view as _approval_view,
                        offline_binding_problem as _offline_binding_problem, media_view as _media_view,
                        platform_preview as _platform_preview, video_issues as _video_issues,
                        video_view as _video_view)
from . import disconnect as account_disconnect
from . import home, repeat_guard
from .journal import ALL_STATUSES as _JOURNAL_STATUSES
from .storage import (
    APPROVAL_KEY_CREDENTIAL, APPROVAL_KEY_UNAVAILABLE, BINDING_MEDIA_CHANGED, BINDING_VIDEO_CHANGED, METRIC_FIELDS, account_send_hold, active_provider_cooldown, hold_refusal,
    add_post, approval_binding_problem, identity_mode_required, latch_identity_mode, approval_key_mode, approval_payload_sha256, latch_keyed_mode, offline_key_mode,
    binding_refusal, check_approval_binding, claim_sending, connect, cooldown_refusal, finish_send, get_post,
    approval_max_age_hours, approval_rebind_required, is_paused, parse_utc, pending, post_media, post_video, set_approval_max_age_hours,
    provider_cooldowns, published_text, queue_health, reconcile, record_binding_failure, reply_report, reply_target_text, report, set_paused,
    set_post_media, set_post_video, store_metrics, sync_cooldown_scopes, transition, uncertain_send, utc_now,
    connect_readonly as storage_connect_readonly, database_path as storage_database_path, event as record_event,
    record_shared, repeat_refusal, repeat_send_problem,
)
from .inputs import read_json_file
from .cli_parser import make_parser  # noqa: F401  (re-exported: tests and tools use cli.make_parser)


def _accounts(args):
    try:
        return load_accounts(args.accounts)
    except ValidationError as exc:
        if not str(exc).startswith("Missing "):
            raise
        # Skill-only installs have no config/ folder; point at the bundled example.
        example = Path(__file__).resolve().parents[2] / "assets" / "accounts.example.json"
        if not Path("config/accounts.example.json").exists() and example.is_file():
            raise ValidationError(f"{exc} Skill install: copy {example} to config/accounts.json "
                                  "in your project (no secrets).") from None
        raise


def journal_statuses() -> tuple[str, ...]:
    return _JOURNAL_STATUSES


def _journal_bound(value: str | None, *, end: bool) -> str | None:
    if value is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value + ("T23:59:59+00:00" if end else "T00:00:00+00:00")
    return value


def _campaign_history(args, campaign: dict) -> dict | None:
    """Load the read-only posting history for campaign-check/preview, or None without --history-db."""
    tuning = {"history_days": args.history_days, "history_window_days": args.history_window_days,
              "history_max_posts": args.history_max_posts}
    if args.history_db is None:
        given = [f"--{name.replace('_', '-')}" for name, value in tuning.items() if value is not None]
        if given:
            raise ValidationError(f"{', '.join(given)} requires --history-db PATH")
        return None
    from .history import (
        DEFAULT_FREQUENCY_MAX_POSTS, DEFAULT_FREQUENCY_WINDOW_DAYS, DEFAULT_HISTORY_DAYS, load_history,
    )
    return load_history(args.history_db, campaign,
                        days=DEFAULT_HISTORY_DAYS if args.history_days is None else args.history_days,
                        window_days=DEFAULT_FREQUENCY_WINDOW_DAYS if args.history_window_days is None
                        else args.history_window_days,
                        max_posts=DEFAULT_FREQUENCY_MAX_POSTS if args.history_max_posts is None
                        else args.history_max_posts)


def _persona_gate(args, subjects: list[dict | None], accounts: dict, ids: list[str]) -> list[str]:
    """Wrong-account refusals for the persona a post is for (``--persona``, else the profile's default).

    Empty when no persona is asserted. An invalid profile file raises (fail closed). ``subjects`` is parallel to
    ``ids``; a None subject is an account that could not be read, which cannot be verified and is refused.
    """
    profile = profile_store.load_optional(args.profile)
    persona = getattr(args, "persona", None) or ((profile or {}).get("preferences") or {}).get("default_persona")
    if not persona:
        return []
    problems = []
    for account_id, account in zip(ids, subjects):
        if account is None:
            problems.append(f"cannot verify persona '{persona}': account {account_id} is not in the accounts file")
            continue
        refusal = persona_guard.check_account(account, persona, accounts, profile)
        if refusal:
            problems.append(refusal)
    return problems


def _optional_accounts_map(args) -> dict:
    try:
        return _accounts(args)
    except ValidationError as exc:
        if str(exc).startswith("Missing "):
            return {}
        raise


def _optional_account(args, account_id: str, platform: str) -> dict | None:
    """Offline review helper: account metadata if the local config loads, else None."""
    try:
        account = _accounts(args).get(account_id)
    except ValidationError:
        return None
    return account if account and account["platform"] == platform else None




def _media_issues(post, account: dict | None = None, *, require_image: bool = False,
                  check_files: bool = True) -> list[str]:
    """Blocking attachment problems, offline (#13, #20): malformed record, platform/source/count/alt
    rules and, with ``check_files``, a local file that no longer holds the recorded bytes. With
    ``require_image`` (live sends) also an Instagram post without its image; ``check`` leaves that
    out so a caption drafted for manual-export still checks OK. A hosted image is never downloaded here."""
    from .attachments import check_media_list, media_problem
    try:
        media = post_media(post)
        check_media_list(media, post["platform"], account)
    except ValidationError as exc:
        return [f"media: {exc}"]
    if require_image and post["platform"] in MEDIA_ONLY_PLATFORMS and not media:
        return ["media: instagram_image_required: attach one image with 'media-attach POST_ID --url URL --alt TEXT "
                "--online' (Instagram posts need media; manual-export is unaffected)"]
    problem = media_problem(media) if media and check_files else None
    return [binding_refusal(problem, post["id"])] if problem else []




def _private_link_refusal(args, texts=(), urls=()) -> str | None:
    """#9 on the phase 1-3 surfaces: with ``--sources CONFIG``, the first link (in ``texts``: post or
    reply text, alt texts, video title and tags) or URL (``urls``: a hosted image, the source URL)
    inside a ``visibility: private`` repository of that config, as a refusal message; else None.
    Offline; reads only the config file (a malformed one raises, failing closed)."""
    path = getattr(args, "sources", None)
    if not path:
        return None
    from .private_sources import links_in, private_repos_from_config, url_in_private_repo
    from .sources import load_config as load_sources_config
    repos = private_repos_from_config(load_sources_config(path)[0])
    if not repos:
        return None
    candidates = [link for text in texts for link in links_in(text)] + [url for url in urls if url]
    if any(url_in_private_repo(link, repos) for link in candidates):
        return ("private_source_link: links into a private source repository; private repository URLs "
                "are never public links (issue #9)")
    return None


def _post_private_link_refusal(args, post) -> str | None:
    """``_private_link_refusal`` over everything a post publishes or records as provenance."""
    try:
        hosted = [item["url"] for item in post_media(post) if item.get("source") == "url"]
    except ValidationError:
        hosted = [str(post["media_json"])]  # malformed: check the raw value rather than skip it
    return _private_link_refusal(args, [published_text(post), *hosted], [post["source_url"]])


def _check_findings(args, db, post) -> tuple[list[str], list[dict], list[dict]]:
    """``check``'s offline findings for one stored post: (blocking issues, repeat hits, blocking repeat hits).

    Reads only (``db`` may be a read-only connection); the review packet shows the same findings."""
    account = _optional_account(args, post["account_id"], post["platform"])
    issues = validate_post(post["platform"],post["content"], account)
    issues.extend(_persona_gate(args, [account], _optional_accounts_map(args), [post["account_id"]]))
    issues.extend(_media_issues(post, account))
    issues.extend(_video_issues(post))
    optout = load_optout(args.optout)
    hits = optout_mentions(published_text(post), optout, post["platform"])
    if hits:
        issues.append(describe_mentions(hits))
    issues.extend(_reply_problems(post, account, optout))
    private = _post_private_link_refusal(args, post)  # #9 over post text, alt texts, video title/tags
    if private:
        issues.append(private)
    repeats = _repeat_findings(db, post)
    blocking_repeats = repeat_guard.blocking(repeats)
    issues.extend(f"repeat: {repeat_guard.describe(hit)}" for hit in blocking_repeats[:3])
    return issues, repeats, blocking_repeats


def _check_report(args, db, post, found=None) -> dict:
    """The ``check --json`` document (also each review packet post's check section)."""
    issues, repeats, blocking_repeats = found if found is not None else _check_findings(args, db, post)
    return {"post_id": post["id"], "ok": not issues, "findings": issues,
            "warnings": [repeat_guard.describe(hit) for hit in repeats if hit not in blocking_repeats],
            "blocking_repeat": bool(blocking_repeats),
            "manual_only": post["platform"] not in SUPPORTED_LIVE}


def _draft_reply(db, args, account: dict, content: str, optout: list[dict]) -> int:
    """``draft --reply-to`` (issue #17): verify the parent read-only, then add an ordinary
    draft row carrying the canonical target. Never approves, schedules or sends."""
    from .replies import canonical_target, parse_reply_ref, verification_plan, verify_reply_target
    ref = parse_reply_ref(account, args.reply_to)
    problems = validate_post(account["platform"], content, account)
    if problems:
        raise ValidationError("; ".join(problems))
    if not args.online:
        plan = [{k: v for k, v in r.items() if k != "url"} for r in verification_plan(account, ref)]
        print(json.dumps({"mode": "plan_only", "account_id": account["id"], "platform": account["platform"],
                          "would_verify": plan}, indent=2))
        print("PLAN ONLY: no request sent, no draft created. Re-run with --online to verify the parent "
              "(unauthenticated read-only GETs) and create the reply draft.", file=sys.stderr)
        return 0
    target = verify_reply_target(db, account, ref, optout=optout, override=args.override_flagged)
    post_id = add_post(db, account["id"], account["platform"], content, args.campaign, args.source_url,
                       account=account, reply_target=canonical_target(target))
    if target["override"]:  # the human override is audited, not only stored in the bound target
        from .storage import event
        event(db, "reply_override_flagged", post_id, f"label={target['override']}")
    note = f"; triage override {target['override']} recorded" if target["override"] else ""
    print(f"Created reply draft {post_id} for {account['id']} under your post {target['root_post_id']}"
          f"{note}; not published. Review it with 'show {post_id}' before approving")
    print("Verified read-only and unauthenticated; nothing was sent, liked or followed.", file=sys.stderr)
    return 0


def _reply_problems(post, account: dict | None, optout: list[dict]) -> list[str]:
    """Offline reply-target problems for ``check``/``approve`` (empty for an ordinary post).
    An unloadable accounts config is a problem: a reply is never approved blind."""
    from .replies import row_target, target_problems
    try:
        target = row_target(post)
    except ValidationError as exc:
        return [str(exc)]
    if target is None:
        return []
    from .storage import REPLY_MEDIA_REFUSED
    return ([REPLY_MEDIA_REFUSED] if _reply_has_media(post) else []) + target_problems(target, account, optout)


def _reply_has_media(post) -> bool:
    """True when a row carries images or a video (a reply must not: it is text-only)."""
    return any(name in post.keys() and bool(post[name]) for name in ("media_json", "video_json"))


def _reply_gate(post, account: dict | None, optout: list[dict]) -> tuple[str | None, str | None, dict | None]:
    """Pre-claim reply checks (issue #17): (worker reason, message, target).

    Ordinary posts return (None, None, None). Every refusal leaves the post approved/scheduled.
    """
    from .capabilities import require_reply_capability
    from .replies import row_target, target_problems
    try:
        target = row_target(post)
    except ValidationError as exc:
        return "reply_refused", str(exc), None
    if target is None:
        return None, None, None
    if _reply_has_media(post):  # a reply is text-only (#17 integration with #13/#20/#22/#23)
        from .storage import REPLY_MEDIA_REFUSED
        return "reply_refused", REPLY_MEDIA_REFUSED, target
    problems = target_problems(target, account, optout)
    if "reply_parent_author_optout_listed" in problems:
        return "optout_blocked", "reply_parent_author_optout_listed: the parent author is on the opt-out list", target
    if problems:
        return "reply_refused", "; ".join(problems), target
    try:
        require_reply_capability(post["platform"])
    except ValidationError as exc:
        return "capability_refused", str(exc), target
    return None, None, target


BOOKKEEPING_ATTEMPTS = 3
BOOKKEEPING_DELAY_SECONDS = 0.5


def _bookkeep(call, *args, **kwargs):
    """Run a post-send bookkeeping write (``finish_send``/``uncertain_send``) with a short bounded retry.

    Only the local database write is retried, never the remote send. Returns ``(result, error)``:
    ``error`` is the last ``sqlite3.Error`` once every attempt failed, else ``None``. A retry that
    finds the post no longer ``sending`` (the earlier attempt did commit) yields a state_conflict
    event and a REVIEW line, which is safe: a human checks the native account.
    """
    error = None
    for attempt in range(BOOKKEEPING_ATTEMPTS):
        try:
            return call(*args, **kwargs), None
        except sqlite3.Error as exc:
            error = exc
            if attempt + 1 < BOOKKEEPING_ATTEMPTS:
                time.sleep(BOOKKEEPING_DELAY_SECONDS * (attempt + 1))
    return None, error


def _publish(db, args, wlog) -> int:
    """Body of ``publish``. ``wlog`` only records decisions; it never changes control flow."""
    due = pending(
        db, args.limit, post_id=args.post_id,
        scheduled_only=args.scheduled_only,
        scheduled_not_before=(
            (datetime.now(timezone.utc) - timedelta(minutes=args.max_lateness_minutes))
            .isoformat(timespec="seconds")
            if args.scheduled_only else None
        ),
    )
    wlog.due(len(due))
    if args.post_id and not due:
        wlog.skipped_id(args.post_id, "not_due")
        raise ValidationError("Selected post is not approved and due, or does not exist. No send attempted.")
    # Platform terms forbid automated posting (policy.OWNER_TRIGGERED_ONLY, e.g. LinkedIn API Terms 3.1): such a
    # post leaves only through the owner's own `publish --live --post-id ID`; scheduled and bulk runs hold it
    # here, before any claim, request or credential read. Tighten-only.
    if args.scheduled_only or not args.post_id:
        held = [post for post in due if post["platform"] in OWNER_TRIGGERED_ONLY]
        if held:
            held_ids = {post["id"] for post in held}
            due = [post for post in due if post["id"] not in held_ids]
            for post in held:
                wlog.skipped(post, "owner_triggered_only")
                print(f"  HELD {post['id']} {post['platform']}: {OWNER_TRIGGERED_REASON}")
    # Optional per-account caps file: tighten-only, min/max with the CLI values (caps.py).
    caps, caps_path = load_caps()
    # Opt-out list: loaded ONCE per run, before any claim; a malformed file aborts here.
    optout = load_optout(args.optout)
    if not args.live:
        print(f"DRY RUN: {len(due)} due approved posts; no requests sent")
        print(f"Live gate: REACHWEAVER_ALLOW_LIVE {'enabled' if live_opt_in() else 'disabled'}; "
              f"REACHWEAVER_ENV {runtime_environment()} (--live also requires REACHWEAVER_ENV=prod); "
              f"credential source {credential_source()}")
        print(f"Live account budget: max {args.max_per_24h} send attempts per rolling 24h; "
              f"minimum {args.min_gap_minutes} min between attempts.")
        if caps_path:
            print(f"Caps file {caps_path}: per-account values apply only where stricter (tighten-only).")
        if due:  # #39: what the sends are documented to cost, from the sourced registry (no money is metered here)
            from .capabilities import api_cost_note
            for platform_id in sorted({post["platform"] for post in due}):
                cost_note = api_cost_note(platform_id)
                print(f"API cost ({platform_id}): {cost_note['api_fees']} (registry verified {cost_note['verified_date']}); "
                      "ReachWeaver meters no money, limits are attempt caps and cooldowns.")
        if args.scheduled_only:
            print(f"Scheduled-only safe worker preview: exclude unscheduled approvals and "
                  f"slots older than {args.max_lateness_minutes} minutes.")
        # Provider cooldowns (#11): read-only; listed whether or not a due post is affected.
        for active in provider_cooldowns(db):
            print(f"Provider cooldown: account {active['account_id']} until {active['until']} "
                  f"(source {active['source']}"
                  + (f", shared scope {active['scope']}" if active.get("scope") else "")
                  + (", provider asked for more than 24 h: clamped" if active["ceiling_clamped"] else "")
                  + "); its unclaimed posts wait, nothing is resent")
        if confirmation.confirmation_mode(db):
            print("Human confirmation (#7 D6): ON; --live needs a terminal and the typed hash prefix per post.")
        key_mode = offline_key_mode(db)  # a dry run reads no credential, so keyed tags are not verified here
        if key_mode.required:
            print(f"Keyed approval binding (#7 D7): ON; tags are verified by --live, which needs "
                  f"{APPROVAL_KEY_CREDENTIAL}.")
        # A dry run never loads accounts (#5). Shared scopes and the Bluesky createSession
        # counter (#11) are read from the state database: the scope membership the last
        # live run recorded, and each account's recorded session-counter key.
        blocked = unbound = cooling = sessions = replies_held = repeats_held = 0
        planned_sessions: dict[str, int] = {}
        preview_paused = wlog.probe(lambda: is_paused(db), default=False)  # read only for the log line
        for post in due:
            print(f"  {post['id']} {post['platform']} {post['account_id']}: {post['content'][:90]!r}")
            reason = publish_block_reason(published_text(post), optout, post["platform"])
            if reason:
                blocked += 1
                print(f"    BLOCKED {post['id']}: {reason}; --live would skip it before any claim "
                      f"(details: reachweaver check {post['id']})")
            reply_reason = reply_message = reply_target = None
            if reply_target_text(post) is not None:  # ordinary posts never load the accounts config here
                reply_reason, reply_message, reply_target = _reply_gate(
                    post, _optional_account(args, post["account_id"], post["platform"]), optout)
            if reply_target is not None:
                print(f"    REPLY under your post {reply_target['root_post_id']} "
                      f"(triage {reply_target['triage_label']}; details: reachweaver show {post['id']})")
            if reply_reason:
                replies_held += 1
                print(f"    HELD {post['id']}: {reply_message}; --live would skip it before any claim")
            problem = _offline_binding_problem(post, key_mode)
            media_refused = None
            try:
                preview_media = post_media(post)
            except ValidationError:
                preview_media = []  # malformed record: its approval hash cannot match, so problem is set
            if preview_media:  # #13 offline file re-hash; #20 hosted URLs listed only. Nothing is uploaded
                from .attachments import media_problem
                problem = problem or media_problem(preview_media)
                hosted = any(entry["source"] == "url" for entry in preview_media)
                print(f"    media: {len(preview_media)} image(s) " + ", ".join(
                    f"{entry['mime']} sha256 {entry['sha256'][:12]}" for entry in preview_media)
                      + ("; --live re-downloads each URL and refuses changed bytes" if hosted else ""))
                try:
                    require_capability(post["platform"], "image", "publish")
                except ValidationError as exc:
                    media_refused = str(exc)
                    print(f"    SKIPPED {post['id']}: {exc}; --live would skip it before any claim")
            if problem:
                unbound += 1
                print(f"    BLOCKED {binding_refusal(problem, post['id'])}; --live would refuse it")
            since = account_disconnect.disconnected_since(db, post["account_id"])
            if since is not None:
                print(f"    SKIPPED {post['id']}: {account_disconnect.refusal(post['account_id'], since)}; "
                      "--live would skip it before any claim or request")
            cooldown = active_provider_cooldown(db, post["account_id"])  # + recorded scope membership
            if cooldown:
                cooling += 1
                print(f"    SKIPPED {post['id']}: {cooldown_refusal(cooldown)}; "
                      "--live would skip it before any claim or request")
            hold = account_send_hold(db, post["account_id"], post["id"])
            if hold:
                print(f"    SKIPPED {post['id']}: {hold_refusal(hold)}; --live would skip it before any claim or request")
            repeat = repeat_send_problem(db, post)  # sheet item 1: read-only, as the live run checks it
            if repeat:
                repeats_held += 1
                print(f"    SKIPPED {post['id']}: {repeat_refusal(post['id'], repeat)}; --live would skip it before "
                      "any claim or request")
            budget: str | None = None
            if post["platform"] == "bluesky" and not (reason or problem or cooldown or hold or repeat):
                key = post["account_id"]
                usage = recorded_session_usage(db, key)  # None: never metered, nothing to refuse
                if usage is not None:
                    budget = session_budget_problem(usage, planned_sessions.get(key, 0) + SESSIONS_PER_PUBLISH)
                if budget:
                    sessions += 1
                    print(f"    SKIPPED {post['id']}: {budget}; --live would skip it before any request")
                else:
                    planned_sessions[key] = planned_sessions.get(key, 0) + SESSIONS_PER_PUBLISH
            if "video_json" in post.keys() and post["video_json"]:  # #22/#23: offline re-hash only
                view = _video_view(post)
                if view is not None and "record" in view:
                    record = view["record"]
                    mode = video_mode(record)
                    print(f"    video: {record['platform']} {mode} sha256 {record['sha256'][:12]} {record['bytes']} "
                          f"bytes; file {'matches_recorded_sha256' if view['file_matches_record'] else 'CHANGED or unusable'}; "
                          f"live {mode}: {view['live'][mode]}")
                elif view is not None:
                    print(f"    video: {view['error']}")
            elif post["platform"] == "tiktok":
                print(f"    video: none attached; --live would skip {post['id']} (manual-export meanwhile)")
            # One decision per post, first reason in live-run order; nothing is ever sent here.
            wlog.skipped(post, "paused" if preview_paused else "optout_blocked" if reason
                         else reply_reason if reply_reason else "rebind_required" if problem else "cooldown"
                         if cooldown else "repeat_of_shared_material" if repeat else "session_budget" if budget
                         else "capability_refused" if media_refused else "dry_run")
        if replies_held:
            print(f"Replies: {replies_held} due reply post(s) would be HELD before any claim; they stay approved.")
        if cooling:
            print(f"Provider cooldown: {cooling} due post(s) would be SKIPPED; they stay approved/scheduled.")
        if repeats_held:
            print(f"Repeat guard: {repeats_held} due post(s) would be SKIPPED as exact repeats; they stay "
                  "approved/scheduled until cancelled or re-approved with --allow-repeat.")
        if sessions:
            print(f"Bluesky createSession budget: {sessions} due post(s) would be SKIPPED; they stay "
                  "approved/scheduled.")
        if blocked:
            print(f"Opt-out: {blocked} due post(s) would be BLOCKED; they stay approved until edited or cancelled.")
        if unbound:
            print(f"Approval binding: {unbound} due post(s) would be BLOCKED until re-approved.")
        if is_paused(db):
            print("Emergency pause is active")
        return 0
    # --live, REACHWEAVER_ALLOW_LIVE=1 and REACHWEAVER_ENV=prod (#5), before any account work.
    try:
        require_live_gate()
    except ValidationError:
        wlog.gate_refused("live_gate")
        raise
    if is_paused(db):
        wlog.gate_refused("emergency_pause")
        raise ValidationError("Emergency pause is active")
    if confirmation.confirmation_mode(db):  # #7 D6 (opt-in): refuse before any account work
        try:
            confirmation.require_interactive()
        except ValidationError:
            wlog.gate_refused("human_confirmation")
            raise
    accounts = _accounts(args)
    # #11: record the owner-declared shared cooldown scopes of the loaded accounts, so
    # claim_sending and a later dry run see the same membership as this run.
    sync_cooldown_scopes(db, {account_id: cooldown_scopes(account) for account_id, account in accounts.items()})
    for post in due:
        account = accounts.get(post["account_id"])
        if account is None or account["platform"] != post["platform"]:
            print(f"SKIPPED {post['id']}: missing/mismatched account configuration")
            wlog.skipped(post, "account_missing")
            if args.post_id:
                return 2
            continue
        # Reply target (issue #17): shape, account/server/visibility, opt-out on the parent
        # author and the registry reply gate; pre-claim and pre-network, before any backend path.
        reply_reason, reply_message, reply_target = _reply_gate(post, account, optout)
        if reply_reason:
            label = "BLOCKED" if reply_reason == "optout_blocked" else "SKIPPED"
            print(f"{label} {post['id']}: {reply_message}; not claimed, post stays {post['status']}, no post sent")
            wlog.skipped(post, reply_reason)
            if args.post_id:
                return 2
            continue
        providers = providers_for(account)
        rate = effective_rate_limits(caps, providers)  # caps.json v2, tighten-only (#11)
        # #13/#20/#22/#23: images and videos are only ever sent by a direct media adapter, never by
        # a backend (which would publish the text without the approved media), and a post carries
        # images or one video, never both.
        has_media = "media_json" in post.keys() and bool(post["media_json"])
        has_video = "video_json" in post.keys() and bool(post["video_json"])
        if has_media and has_video:
            from .video import BOTH_KINDS
            print(f"SKIPPED {post['id']}: {BOTH_KINDS}; not claimed, no request sent")
            wlog.skipped(post, "content_preflight_failed")
            if args.post_id:
                return 2
            continue
        if (has_media or has_video) and account.get("backend") is not None:
            print(f"SKIPPED {post['id']}: media_attachments_unsupported via a publishing backend; not claimed, "
                  "no request sent")
            wlog.skipped(post, "capability_refused")
            if args.post_id:
                return 2
            continue
        if account.get("backend") is not None:  # explicit owner opt-in backend (issue #30)
            from .postiz import run_live_send
            post_max, post_gap = effective_caps(caps, post["account_id"], args.max_per_24h,
                                                args.min_gap_minutes, providers=providers)
            code = run_live_send(db, post, account, max_per_24h=post_max, min_gap_minutes=post_gap,
                                 optout=optout, cooldown_floor_seconds=rate["cooldown_floor_seconds"],
                                 preflight_max_retries=rate["preflight_max_retries"],
                                 on_preflight_retry=wlog.retry_hook(post))
            wlog.backend_result(db, post, code, backend=account["backend"])
            if code and args.post_id:
                return code
            continue
        if post["platform"] == "tiktok" and post["platform"] in SUPPORTED_LIVE_VIDEO:  # #23: its own runner
            from .tiktok import run_live_send as run_video_send
            post_max, post_gap = effective_caps(caps, post["account_id"], args.max_per_24h,
                                                args.min_gap_minutes, providers=providers)
            code = run_video_send(db, post, account, max_per_24h=post_max, min_gap_minutes=post_gap,
                                  optout=optout, wlog=wlog, cooldown_floor_seconds=rate["cooldown_floor_seconds"],
                                  preflight_max_retries=rate["preflight_max_retries"])
            if code and args.post_id:
                return code
            continue
        try:
            video = post_video(post)  # #22/#23: a malformed record refuses before any claim
        except ValidationError as exc:
            print(f"BLOCKED {post['id']}: {exc}; not claimed, no request sent")
            wlog.skipped(post, "content_preflight_failed")
            if args.post_id:
                return 2
            continue
        # #22: a YouTube video goes through this loop (and publish_video) once the platform is in
        # SUPPORTED_LIVE_VIDEO; require_video_mode below still needs the registry switch.
        live_video = video is not None and post["platform"] in SUPPORTED_LIVE_VIDEO
        if post["platform"] not in SUPPORTED_LIVE and not live_video:
            print(f"SKIPPED {post['id']}: {post['platform']} requires manual publishing/approved integration")
            wlog.skipped(post, "manual_only")
            if args.post_id:
                return 2
            continue
        if has_video and not live_video:  # a text adapter would send the text without the approved video
            print(f"SKIPPED {post['id']}: video_unsupported: no ReachWeaver live video adapter for "
                  f"{post['platform']}; not claimed, no request sent")
            wlog.skipped(post, "capability_refused")
            if args.post_id:
                return 2
            continue
        # Opt-out list must not be mentioned: a pre-claim, pre-network gate (issue #26).
        reason = publish_block_reason(published_text(post), optout, post["platform"])
        if reason:
            print(f"BLOCKED {post['id']}: {reason}; not claimed, post stays approved, no post sent "
                  f"(details: reachweaver check {post['id']})")
            wlog.skipped(post, "optout_blocked")
            if args.post_id:
                return 2
            continue
        # Approval binding (#7): pre-claim, pre-network; claim_sending re-checks under lock.
        account_fingerprint = current_fingerprint(account)  # wave 9 item 2: offline; None fails closed if bound
        options_fingerprint = send_options_fingerprint(account)  # sheet item 5: offline; the account sent with below
        problem = check_approval_binding(db, post["id"], identity_fingerprint=account_fingerprint,
                                         send_options_fingerprint=options_fingerprint)
        if problem:
            print(f"BLOCKED {post['id']}: {binding_refusal(problem, post['id'])}; no social post sent")
            wlog.skipped(post, "rebind_required")
            if args.post_id:
                return 2
            continue
        if video is not None:  # #22/#23: the file must still hold the approved bytes (pre-claim, offline)
            from .video import file_problem as video_file_problem
            if video_file_problem(video):
                record_binding_failure(db, post["id"], BINDING_VIDEO_CHANGED)
                print(f"BLOCKED {post['id']}: {binding_refusal(BINDING_VIDEO_CHANGED, post['id'])}; no social post sent")
                wlog.skipped(post, "rebind_required")
                if args.post_id:
                    return 2
                continue
        # Human confirmation (#7 D6, opt-in): pre-claim, pre-network. A failure stops the run.
        confirmation.confirm_live_send(db, post)
        # Provider cooldown (#11): after a 429 on this account (or on its shared
        # scope) its other unclaimed posts wait. Pre-claim and pre-network; claim_sending
        # re-checks under its lock with the same scopes.
        scopes = cooldown_scopes(account)  # () for direct adapters; validated at load
        cooldown = active_provider_cooldown(db, post["account_id"], scopes=scopes)
        if cooldown:
            print(f"SKIPPED {post['id']}: {cooldown_refusal(cooldown)}; not claimed, post stays "
                  f"{post['status']}, no request sent")
            wlog.skipped(post, "cooldown")
            if args.post_id:
                return 2
            continue
        # Unresolved send (#41): another post of this account is needs_review or sending, so its real
        # state is unknown. Pre-claim and pre-network; claim_sending re-checks under its lock.
        hold = account_send_hold(db, post["account_id"], post["id"])
        if hold:
            print(f"SKIPPED {post['id']}: {hold_refusal(hold)}; not claimed, post stays {post['status']}, no request sent")
            wlog.skipped(post, "unresolved_send_hold")
            if args.post_id:
                return 2
            continue
        # Repeat guard at send time (sheet item 1): pre-claim and pre-network; claim_sending re-checks under its lock.
        repeat = repeat_send_problem(db, post)
        if repeat:
            print(f"SKIPPED {post['id']}: {repeat_refusal(post['id'], repeat)}; not claimed, no request sent")
            wlog.skipped(post, "repeat_of_shared_material")
            if args.post_id:
                return 2
            continue
        try:  # Capability registry must agree before any claim/network (fail closed).
            if video is not None:  # #22: video needs its registry switch, never the text adapter
                from .capabilities import require_video_mode
                require_video_mode(post["platform"], video_mode(video))
            elif post["platform"] in MEDIA_ONLY_PLATFORMS:  # #20: image, never text alone
                require_capability(post["platform"], "image", "publish")
            else:
                require_capability(post["platform"], "text", "publish")
                if has_media:  # #13: live images need the registry's live_media_formats too
                    require_capability(post["platform"], "image", "publish")
        except ValidationError as exc:
            print(f"SKIPPED {post['id']}: {exc}")
            wlog.skipped(post, "capability_refused")
            if args.post_id:
                return 2
            continue
        media: list[dict] = []
        media_issues = _media_issues(post, account, require_image=True, check_files=False)
        if media_issues:
            print(f"SKIPPED {post['id']}: failed media preflight check ({'; '.join(media_issues)})")
            wlog.skipped(post, "content_preflight_failed")
            if args.post_id:
                return 2
            continue
        if has_media:  # pre-claim: #13 local files re-hashed offline; #20 hosted images re-downloaded
            from .attachments import media_problem
            from .remote_media import ImageFetchError, fetch_approved_bytes
            media = post_media(post)
            changed = media_problem(media) is not None
            try:
                for item in media:
                    if not changed and item["source"] == "url":
                        fetch_approved_bytes(item)
            except ImageFetchError as exc:
                print(f"SKIPPED {post['id']}: image URL check failed ({exc}); not claimed, no social request sent")
                wlog.skipped(post, "content_preflight_failed")
                if args.post_id:
                    return 2
                continue
            except ValidationError:
                changed = True
            if changed:
                record_binding_failure(db, post["id"], BINDING_MEDIA_CHANGED)
                print(f"BLOCKED {post['id']}: {binding_refusal(BINDING_MEDIA_CHANGED, post['id'])}; "
                      "not claimed, no social post sent")
                wlog.skipped(post, "rebind_required")
                if args.post_id:
                    return 2
                continue
        since = account_disconnect.disconnected_since(db, post["account_id"])
        if since is not None:
            print(f"SKIPPED {post['id']}: {account_disconnect.refusal(post['account_id'], since)}; not claimed, "
                  f"post stays {post['status']}, no request sent")
            wlog.skipped(post, "account_disconnected")
            if args.post_id:
                return 2
            continue
        missing = missing_credentials(account)
        if missing:
            print(f"SKIPPED {post['id']}: configure environment variables: {', '.join(missing)}")
            wlog.skipped(post, "credentials_missing")
            if args.post_id:
                return 2
            continue
        problems = validate_post(post["platform"],post["content"],account)
        if problems:
            print(f"SKIPPED {post['id']}: failed preflight check")
            wlog.skipped(post, "content_preflight_failed")
            if args.post_id:
                return 2
            continue
        # Bluesky createSession budget (#11): reserve the preflight session and the
        # one inside publish_bluesky before any request; refusal leaves the post untouched.
        if post["platform"] == "bluesky":
            try:
                reserve_bluesky_sessions(db, account, needed=SESSIONS_PER_PUBLISH, stage="preflight")
            except ValidationError as exc:
                print(f"SKIPPED {post['id']}: {exc}; not claimed, post stays {post['status']}")
                wlog.skipped(post, "session_budget")
                if args.post_id:
                    return 2
                continue
        # An identity failure must leave an approved post eligible to be
        # retried after correcting configuration; no send was attempted. Read-only GET
        # checks get at most 2 jittered retries before the claim (#11, D13); the
        # Bluesky createSession check is never retried automatically.
        try:
            verified_connection = preflight_with_backoff(
                lambda: check_connection(account),
                max_retries=preflight_retries(post["platform"], rate["preflight_max_retries"]),
                paused=lambda: is_paused(db), retry_on=PublishError,
                on_retry=wlog.retry_hook(post))  # log only (security decision item 3); None without --log-json
        except (ValidationError, PublishError) as exc:
            note = ""
            if is_rate_limited(exc):
                recorded = record_rate_limit(db, account, exc, stage="preflight",
                                             floor_seconds=rate["cooldown_floor_seconds"])
                note = f"; {cooldown_refusal(recorded)} recorded"
            print(f"BLOCKED {post['id']}: account identity preflight failed ({exc}){note}; no post sent")
            wlog.skipped(post, "identity_preflight_failed", rate_limited=bool(note))
            if args.post_id:
                return 2
            continue
        for note in getattr(verified_connection, "notes", ()):  # advisory output only
            print(f"NOTE {post['id']}: {note}")
        post_max, post_gap = effective_caps(caps, post["account_id"], args.max_per_24h,
                                            args.min_gap_minutes, providers=providers)
        try:
            claimed = claim_sending(
                db, post["id"], max_per_24h=post_max,
                min_gap_minutes=post_gap,
                expected_sha256=approval_payload_sha256(post),  # the exact text sent below
                cooldown_scopes=scopes,
                identity_fingerprint=account_fingerprint,
                send_options_fingerprint=options_fingerprint,  # the same account dict publish() receives
            )
        except ValidationError as exc:
            print(f"BLOCKED {post['id']}: {exc}; no social post sent")
            wlog.skipped(post, claim_refusal_reason(exc))
            if args.post_id:
                return 2
            continue
        if not claimed:
            print(f"SKIPPED {post['id']}: no longer eligible")
            wlog.skipped(post, "not_eligible")
            if args.post_id:
                return 2
            continue
        wlog.claimed(post)
        reset_write_marker()
        try:
            receipt: tuple[str, str | None]
            if video is not None:  # #22; the pause is re-read between upload chunks
                receipt = publish_video(account, post["content"], post["id"], video,
                                        verified_connection=verified_connection, paused=lambda: is_paused(db))
            else:
                receipt = publish(account,post["content"],post["id"], verified_connection=verified_connection,
                                  created_at=post["created_at"], media=media or None,
                                  paused=lambda: is_paused(db), reply_target=reply_target, optout=optout)
            remote_id, remote_url = receipt
        except (PublishError, ValidationError) as exc:
            # Classification (#11, D11) labels guidance and the cooldown event only: the
            # claimed post goes to needs_review exactly as before and is never resent.
            send_class = classify_send_failure(post["platform"], None, exc, write_started=write_started())
            applied, bookkeeping_error = _bookkeep(uncertain_send, db, post["id"], str(exc), send_class=send_class)
            note = ""
            if is_rate_limited(exc):
                recorded = record_rate_limit(db, account, exc, stage="send", post_id=post["id"],
                                             send_class=send_class,
                                             floor_seconds=rate["cooldown_floor_seconds"])
                note = (f"; {cooldown_refusal(recorded)} recorded, other posts of this account wait; "
                        "this post is never resent automatically")
            limited = bool(note)
            if bookkeeping_error is not None:
                note += f"; local bookkeeping FAILED ({type(bookkeeping_error).__name__}), post may still read 'sending'"
            elif not applied:
                note += "; state_conflict: the post was no longer 'sending', see the event log"
            print(f"REVIEW {post['id']}: {exc} [{send_class}]{note}; verify remote account before reconciliation")
            wlog.needs_review(post, send_class=send_class,
                              code=("bookkeeping_failed" if bookkeeping_error is not None else
                                    "state_conflict" if not applied else worker_error_code(exc)),
                              rate_limited=limited)
            if args.post_id:
                return 3
        except Exception:
            # Unknown error may follow successful transmission. Do not repeat.
            applied, bookkeeping_error = _bookkeep(uncertain_send, db, post["id"], "unexpected_error_uncertain",
                                                   send_class=AMBIGUOUS)
            extra = (f"; local bookkeeping FAILED ({type(bookkeeping_error).__name__}), post may still read 'sending'"
                     if bookkeeping_error is not None else
                     "; state_conflict: the post was no longer 'sending', see the event log" if not applied else "")
            print(f"REVIEW {post['id']}: unexpected error; inspect manually; not retried{extra}")
            wlog.needs_review(post, send_class=AMBIGUOUS,
                              code=("bookkeeping_failed" if bookkeeping_error is not None else
                                    "state_conflict" if not applied else "unexpected_error_uncertain"))
            if args.post_id:
                return 3
        else:
            applied, bookkeeping_error = _bookkeep(finish_send, db, post["id"], remote_id, remote_url,
                                                   getattr(receipt, "extra", None))
            if bookkeeping_error is not None:
                # The remote post exists but the local record could not be written: surface the id so
                # a human can run 'reconcile --published'. Never retry the send; count it as review.
                print(f"REVIEW {post['id']}: remote send succeeded (remote_id={remote_id}) but local bookkeeping "
                      f"FAILED ({type(bookkeeping_error).__name__}); run 'reconcile {post['id']} --published "
                      f"--remote-id {remote_id}' after checking the native account; not retried", file=sys.stderr)
                print(f"REVIEW {post['id']}: bookkeeping failed; remote_id={remote_id}")
                wlog.needs_review(post, send_class=AMBIGUOUS, code="bookkeeping_failed")
                if args.post_id:
                    return 3
            elif not applied:
                print(f"REVIEW {post['id']}: remote send succeeded (remote_id={remote_id}) but the post was no "
                      "longer 'sending' (state_conflict); not marked published; verify the native account, then "
                      "reconcile", file=sys.stderr)
                print(f"REVIEW {post['id']}: state_conflict; remote_id={remote_id}")
                wlog.needs_review(post, send_class=AMBIGUOUS, code="state_conflict")
                if args.post_id:
                    return 3
            else:
                print(f"PUBLISHED {post['id']}: {remote_url or remote_id}")
                if post["platform"] == "youtube":
                    from .youtube import visibility_note
                    visibility = visibility_note(post["video_json"],
                                                 (getattr(receipt, "extra", None) or {}).get("upload_status"))
                    if visibility:
                        print(f"NOTE {post['id']}: {visibility}")
                wlog.published(post, remote_id, remote_url)
    return 0


def _run_publish(db, args) -> int:
    """Validate, open the optional structured log BEFORE any work, then run ``_publish``.

    A log destination that cannot be opened or written refuses the run here (exit 2): no
    due query, account load, claim or request has happened yet. Later write failures are
    counted and never raised (``worker_log``), so logging cannot change a send.
    """
    if not 1 <= args.limit <= 100:
        raise ValidationError("--limit must be 1..100")
    if not 1 <= args.max_per_24h <= 10 or not 15 <= args.min_gap_minutes <= 1440:
        raise ValidationError("Live account budget must be 1..10 attempts/24h and 15..1440 min gap")
    if not 5 <= args.max_lateness_minutes <= 10080:
        raise ValidationError("--max-lateness-minutes must be 5..10080")
    wlog = open_worker_log(args.log_json, args.log_file, dry_run=not args.live)
    try:
        wlog.start(live_requested=args.live, scheduled_only=args.scheduled_only, limit=args.limit,
                   post_id=args.post_id)
        try:
            code = _publish(db, args, wlog)
        except BaseException as exc:
            wlog.end(exit_code=130 if isinstance(exc, KeyboardInterrupt) else 2, error=exc)
            raise
        wlog.end(exit_code=code, paused=None if args.live else (lambda: is_paused(db)))
        return code
    finally:
        wlog.close()


MAX_DRAFT_ACCOUNTS = 20


def _draft_many(db, args, accounts) -> int:
    """One separate draft per named account, all or none. Approval stays per post: nothing here approves."""
    names = args.account
    if args.reply_to is not None:
        raise ValidationError("--reply-to takes exactly one --account: a reply belongs to one of your threads")
    if args.online or args.override_flagged is not None:
        raise ValidationError("--online and --override-flagged apply only with --reply-to")
    if len(names) > MAX_DRAFT_ACCOUNTS:
        raise ValidationError(f"At most {MAX_DRAFT_ACCOUNTS} --account values per draft command")
    if len(set(names)) != len(names):
        raise ValidationError("An --account is named twice; name each account once")
    unknown = [name for name in names if name not in accounts]
    if unknown:
        raise ValidationError(f"Unknown account(s) {', '.join(unknown)}. Run 'doctor' to list configured accounts")
    content = args.file.read_text(encoding="utf-8") if args.file else args.text
    platforms = [accounts[name]["platform"] for name in names]
    doubled = sorted({p for p in platforms if platforms.count(p) > 1})
    if doubled:
        raise ValidationError(f"Draft refused, nothing created: the same text on two {', '.join(doubled)} accounts "
                              "reads as duplicate posting. Adapt the text per account and draft them separately")
    private = _private_link_refusal(args, [content], [args.source_url])  # #9
    if private:
        raise ValidationError(f"Draft refused, nothing created: {private}")
    optout = load_optout(args.optout)
    problems = _persona_gate(args, [accounts[name] for name in names], accounts, names)
    for name in names:
        account = accounts[name]
        hits = optout_mentions(content, optout, account["platform"])
        if hits:
            problems.append(f"{name}: {describe_mentions(hits)}")
        issues = validate_post(account["platform"], content, account)
        if issues:
            problems.append(f"{name} ({account['platform']}): " + "; ".join(issues))
    if problems:
        raise ValidationError("Draft refused, nothing created:\n  " + "\n  ".join(problems))
    from .storage import _atomic
    with _atomic(db):  # one transaction: a locked database or Ctrl-C leaves no partial set behind
        created = [(add_post(db, name, accounts[name]["platform"], content, args.campaign, args.source_url,
                             account=accounts[name]), name) for name in names]
    if args.json:
        print(json.dumps(_drafts_document(created, accounts), indent=2))
        return 0
    print(f"Created {len(created)} drafts, one per account; none is approved or published:")
    for post_id, name in created:
        print(f"  {post_id}  {name} ({accounts[name]['platform']})")
    print("Review and approve each one on its own: reachweaver show POST_ID, then reachweaver approve POST_ID "
          "--approver YOUR_NAME")
    return 0


def _approval_mode_document(db) -> dict:
    """``approval-mode --json``: the same facts the text display prints; changes nothing."""
    mode = approval_key_mode(db)
    ttl = approval_max_age_hours(db)
    return {"identity_bound_approval": "required" if identity_mode_required(db) else "off",
            "confirmation": confirmation.confirmation_mode(db) or "off",
            "approval_max_age_hours": ttl, "approval_max_age_invalid": ttl == 0,
            "keyed_binding": {"required": bool(mode.required), "latched": bool(mode.latched),
                              "key": "available" if mode.key is not None else (mode.key_error or "not provided")},
            "rebind_required": len(approval_rebind_required(db)),
            "note": "Display only. Tightening a safeguard is the owner's decision, in their own terminal."}


def _drafts_document(created: list[tuple[str, str]], accounts: dict) -> dict:
    """``draft --json``: the created post ids; each still needs its own approval by the owner."""
    return {"created": [{"post_id": post_id, "account": name, "platform": accounts[name]["platform"],
                         "status": "draft"} for post_id, name in created],
            "note": "Drafts only: nothing is approved or published. The owner reviews (show) and approves each one."}


# Commands that only read the state database: opened with connect_readonly, so a missing
# database fails (exit 2) instead of being created, and an older schema is never migrated.
READ_ONLY_DB_COMMANDS = ("engagement-fetch", "ops-metrics", "youtube-analytics", "ops-summary", "calendar-gaps", "link-health",
                         "trace")
OPS_METRICS_TABLES = ("posts", "event_log", "settings")


def _execute_read_only(args) -> int:
    from .storage import connect_readonly, database_path, schema_gaps
    database = database_path(args.db)
    if args.command == "calendar-gaps":  # SELECT statements only, no network, nothing scheduled
        from .calendar_gaps import find_calendar_gaps, render_text as render_gaps_text
        timing_args = (args.timing_source, args.timing_metric, args.timing_denominator)
        if any(timing_args) and not all(timing_args):
            raise ValidationError("--timing-source, --timing-metric and --timing-denominator go together")
        account = _accounts(args).get(args.account)
        if account is None:
            raise ValidationError("Unknown calendar account; configure the owner account and run doctor")
        audience = None
        if args.audience_activity is not None:
            today = datetime.now(timezone.utc).date()
            audience = derive_audience_slots(
                load_audience_activity(args.audience_activity, today=today), args.account,
                target_timezone=args.timezone, today=today,
                top_hours=AUDIENCE_TOP_HOURS if args.audience_top_hours is None else args.audience_top_hours,
                max_age_days=AUDIENCE_MAX_AGE_DAYS if args.audience_max_age_days is None else args.audience_max_age_days,
            )
        elif args.audience_top_hours is not None or args.audience_max_age_days is not None:
            raise ValidationError("--audience-top-hours/--audience-max-age-days need --audience-activity FILE")
        with connect_readonly(database) as db:
            needed = ("posts", "metric_snapshots") if args.timing_source else ("posts",)
            gaps = schema_gaps(db, needed)
            if gaps:
                raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                      "calendar-gaps is read-only and never migrates. Run 'reachweaver init' "
                                      "once to upgrade it, then retry")
            timing = None
            if args.timing_source:
                timing = analyze_posting_times(
                    db, account_id=args.account, platform=account["platform"], audience_timezone=args.timezone,
                    source=args.timing_source, metric=args.timing_metric, denominator=args.timing_denominator)
            report = find_calendar_gaps(
                db, account_id=args.account, platform=account["platform"], audience_timezone=args.timezone,
                start=args.start, days=args.days, slots=args.slot, blackout=args.blackout,
                audience_activity=audience, timing=timing, tolerance_minutes=args.tolerance_minutes,
                max_per_day=args.max_per_day, min_spacing_hours=args.min_spacing_hours)
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_gaps_text(report))
        return 0
    if args.command == "trace":  # SELECT statements and the worker-log files named; no network
        from .trace import build_trace, render_text as render_trace
        with connect_readonly(database) as db:
            gaps = schema_gaps(db, ("posts", "event_log"))
            if gaps:
                raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                      "trace is read-only and never migrates. Run 'reachweaver init' "
                                      "once to upgrade it, then retry")
            trace = build_trace(db, args.post_id, args.log, stdin=sys.stdin)
        print(json.dumps(trace, indent=2, ensure_ascii=False) if args.json else render_trace(trace))
        return 0
    if args.command == "link-health":  # SELECT statements only; network only with --online
        from .link_health import DEFAULT_STATUSES, exit_code as link_exit_code, link_health, render_text as render_links
        with connect_readonly(database) as db:
            gaps = schema_gaps(db, ("posts",))
            if gaps:
                raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                      "link-health is read-only and never migrates. Run 'reachweaver init' "
                                      "once to upgrade it, then retry")
            report = link_health(db, statuses=tuple(args.status or DEFAULT_STATUSES), account_id=args.account,
                                 campaign=args.campaign, online=args.online, allow_origins=args.allow_origin,
                                 max_links=args.max_links)
        print(json.dumps({"generated_at": utc_now(), **report}, indent=2, ensure_ascii=False) if args.json
              else render_links(report))
        return link_exit_code(report)
    if args.command == "engagement-fetch":  # network only with --online
        from .engagement_fetch import fetch_engagement, write_export as write_engagement_export
        if args.online and args.out is None:
            raise ValidationError("engagement-fetch --online requires --out FILE (a new 0600 export file)")
        if args.out is not None and (args.out.exists() or args.out.is_symlink()):
            raise ValidationError(f"{args.out} already exists; engagement-fetch never overwrites a file")
        accounts = _accounts(args)
        with connect_readonly(database) as db:
            replies = fetch_engagement(db, accounts, account_id=args.account, post_id=args.post_id,
                                       max_posts=args.max_posts, depth=args.depth,
                                       max_replies=args.max_replies, online=args.online,
                                       quotes=args.quotes, max_quotes=args.max_quotes)
        export = replies.pop("export", None)
        if export is not None:
            count = len(export["items"])
            replies["export_items"] = count
            if count:
                write_engagement_export(args.out, export)
                replies["export_file"] = str(args.out)
        print(json.dumps(replies, indent=2, ensure_ascii=False))
        if export is not None:
            print(f"Wrote {count} items to {args.out} (mode 0600). Next: engagement-triage {args.out}"
                  if count else "No replies or quotes found; no export file written.", file=sys.stderr)
        print("Nothing was sent, posted, liked or followed; the database was not modified.", file=sys.stderr)
        return 1 if replies["stopped"] or replies["failed"] else 0
    if args.command == "youtube-analytics":  # SELECT statements only, no network, nothing fetched
        from .youtube_analytics import sampling_report
        with connect_readonly(database) as db:
            gaps = schema_gaps(db, ("posts", "metric_snapshots", "event_log"))
            if gaps:
                raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                      "youtube-analytics is read-only and never migrates. Run 'reachweaver init' "
                                      "once to upgrade it, then retry")
            result = sampling_report(db, account_id=args.account, post_id=args.post_id)
        print(json.dumps({"generated_at": utc_now(), **result}, indent=2, ensure_ascii=False))
        return 0
    if args.command == "ops-metrics":  # SELECT statements only, no network
        from .ops_metrics import collect, render_json, render_prometheus, write_textfile
        from .optout import optout_source
        optout = load_optout(args.optout)
        with connect_readonly(database) as db:
            gaps = schema_gaps(db, OPS_METRICS_TABLES)
            if gaps:
                raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                      "ops-metrics is read-only and never migrates. Run 'reachweaver init' once "
                                      "to upgrade it, then retry")
            metrics = collect(db, now=args.now, since=args.since, stale_minutes=args.stale_minutes,
                              optout=optout, optout_configured=optout_source(args.optout) is not None)
        text = render_prometheus(metrics) if args.format == "prometheus" else render_json(metrics)
        if args.out:
            write_textfile(args.out, text, database=database)
            print(f"Wrote {len(metrics['metrics'])} metric families to {args.out}; database unchanged.",
                  file=sys.stderr)
        else:
            print(text, end="")
        return 0
    if args.command == "ops-summary":  # SELECT statements only, no network, no credential values
        from .ops_summary import build_summary, exit_code as summary_exit_code, render_json as summary_json, render_text
        from .optout import optout_source
        optout = load_optout(args.optout)
        try:
            accounts, accounts_error = _accounts(args), None
        except ValidationError as exc:
            accounts, accounts_error = {}, str(exc)
        try:
            (caps, caps_path), caps_error = load_caps(), None
        except ValidationError as exc:
            (caps, caps_path), caps_error = (None, None), str(exc)
        with connect_readonly(database) as db:
            gaps = schema_gaps(db, OPS_METRICS_TABLES)
            if gaps:
                raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                      "ops-summary is read-only and never migrates. Run 'reachweaver init' once "
                                      "to upgrade it, then retry")
            summary = build_summary(db, accounts, accounts_error=accounts_error, now=args.now, since=args.since,
                                    stale_minutes=args.stale_minutes, caps=caps,
                                    caps_path=str(caps_path) if caps_path else None, caps_error=caps_error,
                                    optout=optout, optout_configured=optout_source(args.optout) is not None)
        print(summary_json(summary), end="") if args.json else print(render_text(summary))
        return summary_exit_code(summary)
    raise ValidationError(f"not a read-only command: {args.command}")


def _run_ui(args) -> int:
    """``ui``: serve the read-only dashboard until interrupted (Ctrl-C returns 0)."""
    from .storage import database_path
    from .ui_server import make_server, url_for
    prefix = ["reachweaver"]  # prompts for Claude Code repeat the global options this dashboard was started with
    for flag, value in (("--home", getattr(args, "home", None)), ("--db", args.db), ("--accounts", args.accounts)):
        if value:
            prefix += [flag, str(value)]
    server = make_server(database_path(args.db), accounts_path=args.accounts,
                         optout_path=str(args.optout) if args.optout else None, port=args.port,
                         command_prefix=tuple(prefix))
    url = url_for(server)
    print(f"ReachWeaver dashboard (read-only, loopback only): {url}", flush=True)
    print("The address contains a per-run token; keep it private. Press Ctrl-C to stop.", flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _profile_check_report(args) -> tuple[list[str], list[str]]:
    """(findings, ok lines) for ``profile --check``: the file, its permissions and its agreement with accounts.json."""
    path = profile_store.profile_path(args.profile)
    findings: list[str] = []
    if not path.is_file():
        raise ValidationError(f"Missing {path}. Create it with `reachweaver profile --init`")
    try:
        data = read_json_file(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError(f"Profile is unreadable or not JSON: {path}") from None
    errors = profile_store.validate_profile(data)
    findings += errors
    if errors:
        return findings, []
    permissions = profile_store.permissions_finding(args.profile)
    if permissions:
        findings.append(permissions)
    accounts = _optional_accounts_map(args)
    personas = set(data.get("personas", {}))
    for account in accounts.values():
        if account.get("persona") and account["persona"] not in personas:
            findings.append(f"account {account['id']} has persona '{account['persona']}', which is not in the profile")
    for handle in data.get("handles", []):
        if handle["status"] != "claimed":
            continue
        route = persona_guard.route(handle["platform"], handle["persona"], accounts, data)
        if route["status"] == "conflict":
            findings.append(route["advice"])
    ok = [f"OK profile valid: {len(personas)} persona(s), {len(data.get('handles', []))} username(s)"]
    return findings, ok


def _run_identity_command(args) -> int:
    """profile, account-route and account-kit: offline, local files only, no database."""
    if args.command == "profile":
        modes = [args.init, args.show, args.check, bool(args.set or args.handle)]
        if sum(bool(m) for m in modes) != 1:
            raise ValidationError("Choose one: --init, --show, --check, or --set / --handle")
        if args.persona and not args.init:
            raise ValidationError("--persona is only for --init; create personas by setting personas.ID.FIELD")
        path = profile_store.profile_path(args.profile)
        if args.init:
            if path.exists():
                raise ValidationError(f"{path} already exists; nothing changed. Edit it with --set / --handle")
            profile_store.save_profile(profile_store.template(args.persona or ["work"]), path)
            print(f"Created {path} (private, mode 600, ignored by git). Next: reachweaver profile --set "
                  "person.given_name=... --set personas.work.display_name=...")
            return 0
        if args.show:
            print(json.dumps(profile_store.masked_view(profile_store.load_profile(args.profile)), indent=2,
                             ensure_ascii=False))
            return 0
        if args.check:
            findings, ok = _profile_check_report(args)
            print("\n".join(ok + [f"FINDING {item}" for item in findings]))
            return 1 if findings else 0
        data = profile_store.load_profile(args.profile)
        for item in args.set:
            key, sep, value = item.partition("=")
            if not sep:
                raise ValidationError("--set takes PATH=VALUE (an empty VALUE clears the field)")
            profile_store.set_value(data, key.strip(), value)
        for spec in args.handle:
            profile_store.set_handle(data, *profile_store.parse_handle_spec(spec))
        profile_store.save_profile(data, args.profile)
        print(f"Saved {len(args.set) + len(args.handle)} change(s) to {path}. Values are not echoed.")
        return 0
    profile = profile_store.load_optional(args.profile)
    persona = args.persona or ((profile or {}).get("preferences") or {}).get("default_persona")
    if args.command == "account-route":
        if not persona:
            raise ValidationError("Name the persona with --persona (or set preferences.default_persona in the profile)")
        route = persona_guard.route(args.platform, persona, _optional_accounts_map(args), profile)
        if args.json:
            print(json.dumps(route, indent=2, ensure_ascii=False))
        else:
            print(f"{route['persona']} on {route['platform']}: {route['status']}. {route['advice']}")
        return 0 if route["status"] == "ok" else 1
    # account-kit
    if profile is None:
        raise ValidationError(f"Missing {profile_store.profile_path(args.profile)}. Create it with "
                              "`reachweaver profile --init`, then answer the questions it lists")
    if not persona:
        raise ValidationError("Name the persona with --persona (or set preferences.default_persona in the profile)")
    if not args.platform:
        raise ValidationError("Name at least one network with --platform (`reachweaver account-advisor` lists them)")
    if args.approve_plan and args.out is None:
        raise ValidationError("--approve-plan only goes with --out NEW_DIR")
    plan = account_kit.plan(profile, persona, args.platform)
    if args.out is not None:
        if not args.approve_plan:
            raise ValidationError("--out needs --approve-plan SHA256: review the plan first (run without --out), "
                                  "then pass the first 12+ characters of its sha256")
        files = account_kit.write_plan(plan, args.out, args.approve_plan)
        print(f"Wrote {len(files)} private file(s) to {args.out}. ReachWeaver created no account and sent nothing: "
              "follow each SIGNUP.md yourself, then record the username with reachweaver profile --handle.")
        return 0
    if args.questions:
        rows = plan["questions"]
        if args.json:
            print(json.dumps({"persona": persona, "questions": rows, "optional_questions": plan["optional_questions"]},
                             indent=2, ensure_ascii=False))
        else:
            print("\n".join(f"{q['key']}: {q['question']} [{', '.join(q['platforms'])}]" for q in rows)
                  or "Nothing required is missing.")
        return 0
    print(json.dumps(account_kit.masked_plan(plan), indent=2, ensure_ascii=False) if args.json
          else account_kit.render_plan(plan), end="")
    return 0 if plan["ready"] else 1


def execute(args) -> int:
    from . import term
    term.set_plain(getattr(args, "plain", False))
    if args.command == "attribution-report" and not (args.analytics or args.ga4_csv or args.gsc_csv):
        raise ValidationError("attribution-report needs at least one of --analytics, --ga4-csv or --gsc-csv")
    if args.command == "schema-validate":
        try:
            document = read_json_file(args.file)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid JSON: {exc.msg}") from exc
        errors = validate_schema(args.schema_type, document)
        print(f"OK: {args.schema_type} {document['schema_version']}" if not errors else f"INVALID: {args.schema_type}")
        for item in errors:
            print("  ERROR:", item)
        return 1 if errors else 0
    if args.command == "experiment-preregister":
        record = preregister(read_json(args.plan))
        write_preregistration(record, args.out)
        print(json.dumps({"experiment_id": record["experiment_id"], "plan_hash": record["plan_hash"],
                          "registered_at": record["registered_at"], "out": str(args.out),
                          "warnings": record["warnings"], "status": record["status"]}, indent=2))
        print("Registered locally only. Not approval to publish; no account, queue or network was used.",
              file=sys.stderr)
        return 0
    if args.command == "experiment-verify":
        result = verify_preregistration(read_json(args.plan), args.expected_hash)
        print(json.dumps(result, indent=2))
        return 0 if result["match"] else 1
    if args.command == "experiment-next":
        result = next_hypotheses(load_results(args.results))
        print(json.dumps(result, indent=2) if args.json else render_next(result))
        return 0
    if args.command == "experiment-simulate":
        result = simulate_readout(args.seed, args.pairs, args.lift, args.noise, trials=args.trials)
        print(json.dumps(result, indent=2) if args.json else render_simulation(result))
        return 0
    if args.command == "baseline-eval":  # read-only DB access; no network, no writes
        from .baseline_eval import evaluate_against_baseline, load_readouts, render_text as render_baseline
        account = _accounts(args).get(args.account)
        if account is None:
            raise ValidationError("Unknown account. Configure your own account metadata first")
        readouts = load_readouts(args.readout or [])
        attribution = load_attribution_outcomes(args.attribution, args.attribution_source) if args.attribution is not None else None
        with connect(args.db) as db:
            result = evaluate_against_baseline(
                db, account_id=args.account, platform=account["platform"], audience_timezone=args.timezone,
                source=args.source, metric=args.metric, denominator=args.denominator, since=args.since,
                until=args.until, slots=args.slot, campaign_arms=args.campaign_arm, readouts=readouts,
                age_hours=args.age_hours, tolerance_hours=args.tolerance_hours,
                slot_tolerance_minutes=args.slot_tolerance_minutes, min_posts=args.min_posts,
                resamples=args.resamples, confidence=args.confidence, seed=args.seed,
                attribution=attribution, outcome=args.outcome)
        print(json.dumps(result, indent=2) if args.json else render_baseline(result))
        return 0
    if args.command == "pilot-report":
        result = pilot_report(load_pilot_log(args.log), min_weeks=args.min_weeks)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_pilot_report(result))
        return 1 if result["malformed_rows"] else 0
    if args.command == "engagement-triage":
        from .engagement import triage_files  # imported here: the triage rules are not needed at CLI startup
        triage, written = triage_files(args.input, args.docs_map, args.out, args.issues_dir,
                                       optout_path=args.optout)
        print(json.dumps(triage, indent=2, ensure_ascii=False) if args.json else
              f"{triage['status']}: {len(triage['items'])} items, {len(triage['drafts'])} reply drafts, "
              f"{len(triage['issue_drafts'])} issue drafts; " + ", ".join(f"{k}={v}" for k, v in triage["counts"].items() if v))
        for path in written:
            print(f"Wrote draft file {path}", file=sys.stderr)
        print("Nothing was sent, posted, followed, liked, messaged or filed.", file=sys.stderr)
        return 0
    if args.command == "engagement-queue":
        from .engagement_queue import queue_files, render_text
        queue = queue_files(args.input, docs_map=args.docs_map, optout_path=args.optout, now=args.now, sla_pairs=args.sla)
        print(json.dumps(queue, indent=2, ensure_ascii=False) if args.json else render_text(queue))
        print("Nothing was sent, posted, followed, liked, messaged or filed.", file=sys.stderr)
        return 0
    if args.command == "capabilities":
        registry = load_capabilities()
        stale = stale_entries(registry, max_age_days=args.max_age_days)
        rows = summarize(registry, args.platform)
        if args.json:
            chosen = [e for e in registry["platforms"] if e["id"] in {r["platform"] for r in rows}]
            print(json.dumps({"valid": True, "reviewed_on": registry["reviewed_on"], "stale": stale,
                              "platforms": sorted(chosen, key=lambda e: e["id"])}, indent=2, ensure_ascii=False))
        else:
            print("platform | status | adapter | " + " | ".join(FORMATS) + " | verified")
            for row in rows:
                print(" | ".join(str(row[k]) for k in ("platform", "status", "adapter", *FORMATS, "verified_date")))
            for key in stale:
                print(f"WARNING: {key} verified_date older than {args.max_age_days} days; re-verify official sources")
            print("Registry is research data, not permission; unknown/gated/manual formats fail closed.")
        return 0
    if args.command in ("suggest", "weekly-digest"):
        from .storage import connect_readonly, database_path
        def _bounded(path: Path) -> Path:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
                raise ValidationError(f"{args.command} input must be an existing regular file <=2 MB (no symlinks)")
            return path
        snapshot = load_profile_snapshot(_bounded(args.snapshot)) if args.snapshot else None
        if args.owner_authorized and not args.metrics_csv:
            raise ValidationError("--owner-authorized applies only with --metrics-csv")
        csv_info = None
        if args.metrics_csv:
            if snapshot is None:
                raise ValidationError("--metrics-csv requires --snapshot to bind metrics to existing authorized accounts")
            snapshot, csv_info = merge_authorized_csv(snapshot, _bounded(args.metrics_csv),
                                                      owner_authorized=args.owner_authorized)
        engagement = read_json_file(_bounded(args.engagement_export)) if args.engagement_export else None
        seo = load_seo_briefs(_bounded(args.seo_briefs)) if args.seo_briefs else None
        experiments = load_results(_bounded(args.experiments)) if args.experiments else None
        digest_report = None
        if args.include_db:
            with connect_readonly(database_path(args.db)) as db:
                result = build_suggestions(snapshot=snapshot, engagement=engagement, seo_briefs=seo,
                                           experiments=experiments, con=db, limit=args.limit)
                if args.command == "weekly-digest":
                    digest_report = {"generated_at": utc_now(), "platforms": report(db),
                                     "note": "Coverage varies. Counts are latest-per-post snapshots, "
                                             "not unique people or comparable cross-platform views."}
        else:
            result = build_suggestions(snapshot=snapshot, engagement=engagement, seo_briefs=seo,
                                       experiments=experiments, limit=args.limit)
        if csv_info is not None:
            result["source_coverage"]["normalized_csv"] = csv_info
            result["sources_used"].append("owner_authorized_local_csv_readonly")
        if args.command == "weekly-digest":
            from .digest import render_digest, write_digest
            write_digest(args.out, render_digest(result, digest_report, generated_at=utc_now()))
            print(f"Weekly digest written to {args.out} (owner-only file). Nothing was sent or published; "
                  "share it yourself if you choose.")
            return 0
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_suggestions(result))
        return 0
    if args.command in ("profile-audit", "post-audit"):
        snapshot = load_profile_snapshot(args.file)
        csv_info = None
        if args.command == "post-audit":
            if args.owner_authorized and not args.metrics_csv:
                raise ValidationError("--owner-authorized applies only with --metrics-csv")
            if args.metrics_csv:
                snapshot, csv_info = merge_authorized_csv(snapshot, args.metrics_csv,
                                                          owner_authorized=args.owner_authorized)
        result = (
            audit_profiles(snapshot, platform=args.platform)
            if args.command == "profile-audit"
            else audit_posts(snapshot, platform=args.platform, age_hours=args.age_hours,
                             tolerance_hours=args.tolerance_hours, min_posts=args.min_posts)
        )
        if csv_info is not None:
            result["normalized_csv"] = csv_info
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_profile_audit(result))
        return 0
    if args.command == "peer-benchmark":
        from . import peer_benchmark
        peers = peer_benchmark.load_peers(args.file, include_individuals=args.include_individuals)
        reference = peer_benchmark._when(args.now) if args.now else None
        result = peer_benchmark.benchmark(peers, platform=args.platform, now=reference)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else peer_benchmark.render_summary(result))
        return 0
    if args.command == "distribution-plan":
        plan = build_distribution_plan(
            args.region, args.goal, limit=args.limit,
            local_date=args.date, audience_timezone=args.timezone,
        )
        if args.json:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        else:
            print(f"{plan['status']}: {plan['market']} / {plan['goal']}")
            print(f"Confidence: {plan['confidence']}; account connections: UNVERIFIED")
            for channel in plan["channels"]:
                print(f"{channel['priority']}. {channel['platform']} | "
                      f"native={channel['native_schedule']} | "
                      f"ReachWeaver={channel['reachweaver_delivery']} | "
                      f"human-only={channel['requires_human_only']}")
            if plan["illustrative_time_tests"]:
                print("Illustrative time hypotheses ONLY; no items scheduled:")
                for slot in plan["illustrative_time_tests"]:
                    print(f"  {slot['label']}: {slot['local']} = {slot['utc']} UTC")
            print("No network, accounts, drafts, schedules or publishing operations performed.")
        return 0
    if args.command == "utm-link":
        from .private_sources import private_repos_from_config
        from .sources import load_config as load_sources_config
        private_repos = private_repos_from_config(load_sources_config(args.sources)[0]) if args.sources else ()
        print(build_utm_link(args.url, platform=args.platform, campaign=args.campaign,
                             content=args.content, term=args.term, allow_origins=args.allow_origin,
                             private_repos=private_repos))
        return 0
    if args.command == "seo-destinations":
        result = campaign_destinations(load_seo_briefs(args.briefs), args.type, args.project,
                                       platform=args.platform, campaign=args.campaign)
        if args.suggest_for:
            from .seo_platforms import platform_suggestions
            result["platform_suggestions"] = platform_suggestions(result["destination"], args.suggest_for, args.campaign)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.command in ("creator-match", "creator-kit", "creator-coverage"):
        from .creator_amplification import (
            coverage_report, creator_kit, creator_rank, load_input, render_coverage, render_rank, write_kit,
        )
        creator_input = load_input(args.file)
        if args.command == "creator-match":
            outcome = creator_rank(creator_input)
            print(json.dumps(outcome, indent=2, ensure_ascii=False) if args.json else render_rank(outcome))
        elif args.command == "creator-kit":
            kit = creator_kit(creator_input)
            files = write_kit(kit, args.out)
            outcome = {"status": kit["status"], "files": files, "campaign_id": kit["campaign_id"],
                       "note": "Only a local owner-review kit was written; no creator was contacted."}
            print(json.dumps(outcome, indent=2, ensure_ascii=False) if args.json else
                  "\n".join([outcome["status"]] + ["  wrote " + path for path in files] + [outcome["note"]]))
        else:
            outcome = coverage_report(creator_input)
            print(json.dumps(outcome, indent=2, ensure_ascii=False) if args.json else render_coverage(outcome))
        return 0
    if args.command == "creative-preflight":
        from .creative_preflight import check_file as preflight_check_file, render_text as preflight_render
        preflight = preflight_check_file(args.file, voice_profile=args.voice_profile)
        print(json.dumps(preflight, indent=2, ensure_ascii=False) if args.json else preflight_render(preflight))
        return 0
    if args.command in ("voice-profile", "voice-check"):  # database only through connect_readonly; never written
        from .voice import run_command as run_voice
        return run_voice(args)
    if args.command == "account-advisor":
        from .account_advisor import advise as advise_accounts, render_text as advisor_render
        plan = advise_accounts(region=args.region, goal=args.goal, content_format=args.format,
                               weekly_hours=args.weekly_hours, limit=args.limit, existing=tuple(args.existing),
                               brand=args.brand, audience=args.audience, language=args.language, setup=args.setup)
        print(json.dumps(plan, indent=2, ensure_ascii=False) if args.json else advisor_render(plan))
        return 0
    if args.command in ("profile", "account-route", "account-kit"):
        return _run_identity_command(args)
    if args.command == "discovery-plan":
        from .discovery import check_file as discovery_check_file, render_text as discovery_render
        plan = discovery_check_file(args.file)
        print(json.dumps(plan, indent=2, ensure_ascii=False) if args.json else discovery_render(plan))
        return 0
    if args.command == "seo-content-check":
        from .seo_content import check_file, render_text as seo_content_text
        seo_report = check_file(args.file, names=args.name, online=args.online, allow_origins=args.allow_origin)
        print(json.dumps(seo_report, indent=2, ensure_ascii=False) if args.json else seo_content_text(seo_report))
        return 2 if seo_report["counts"]["error"] else 0
    if args.command == "media-check":
        result = media_check(args.manifest)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("VALID" if result["valid"] else "BLOCKED")
            for item in result["errors"]:
                print("  ERROR:", item)
            for item in result["warnings"]:
                print("  WARNING:", item)
            print(result["note"])
        return 2 if result["errors"] else 0
    if args.command == "media-cutlist":  # reads only the plan file; no database, no media file, no network
        from .cutlist import build_cutlist, load_plan, render_text as render_cutlist
        cut_result = build_cutlist(load_plan(args.plan))
        print(json.dumps(cut_result, indent=2, ensure_ascii=False) if args.json else render_cutlist(cut_result))
        return 0
    if args.command == "media-storyboard":
        from .storyboard import run_command as run_storyboard
        return run_storyboard(args)
    if args.command == "delivery-options":
        from .delivery_options import list_delivery_routes, render_text as render_routes
        routes = list_delivery_routes(args.platform)
        if args.json:
            print(json.dumps(routes, indent=2, ensure_ascii=False))
        else:
            print(render_routes(routes), end="")
        return 0
    if args.command == "share":
        from .share import run as run_share
        return run_share(args)
    if args.command == "manual-export-batch":
        from .manual_batch import build_batch, manifest as batch_manifest, render_text as render_batch, write_batch
        batch = build_batch(read_json(args.file))
        if batch["errors"]:
            if args.json:
                print(json.dumps({"status": "BLOCKED", "errors": batch["errors"], "warnings": batch["warnings"],
                                  "written": False}, indent=2, ensure_ascii=False))
            else:
                print(render_batch(batch), end="")
            return 2
        files = write_batch(batch, args.out)
        if args.json:
            print(json.dumps(batch_manifest(batch), indent=2, ensure_ascii=False))
        else:
            print(render_batch(batch), end="")
        for path in files:
            print(f"Wrote {path}", file=sys.stderr)
        print("Nothing was sent, uploaded, scheduled or shared.", file=sys.stderr)
        return 0
    if args.command == "manual-export" and args.platform not in MANUAL_PLATFORMS:
        variant = read_json(args.variant)
        meta = read_json(args.account_meta) if args.account_meta else None
        package = build_package(args.platform, variant, args.media, meta)
        if package["preflight"]["errors"]:
            print("BLOCKED: nothing written")
            for item in package["preflight"]["errors"]:
                print("  ERROR:", item)
            for item in package["preflight"]["warnings"]:
                print("  WARNING:", item)
            return 2
        files = write_package(package, args.out)
        if args.json:
            print(json.dumps(package, indent=2, ensure_ascii=False))
        else:
            kind = "China-aware" if "china" in package else "generic"
            print(f"{package['status']}: {package['platform_label']} {kind} manual package for human review")
            for item in package["preflight"]["warnings"]:
                print("  WARNING:", item)
            print(f"  TEXT LIMIT: {package['limits']['text']['display']}")
            for decision in package.get("china", {}).get("human_decisions", []):
                print(f"  DECIDE YOURSELF: {decision['id']} (nothing pre-selected)")
            if package["human_only"]:
                print(f"  WRITE IT YOURSELF: {package['platform_label']} is human-only; no post text was packaged "
                      "(NOTES.md is a notes form)")
        for path in files:
            print(f"Wrote {path}", file=sys.stderr)
        print("Nothing was sent, uploaded, scheduled or shared.", file=sys.stderr)
        return 0
    if args.command == "manual-export":
        variant = read_json(args.variant)
        meta = read_json(args.account_meta) if args.account_meta else None
        package = build_export(args.platform, variant, args.media, meta)
        if package["preflight"]["errors"]:
            print("BLOCKED: nothing written")
            for item in package["preflight"]["errors"]:
                print("  ERROR:", item)
            for item in package["preflight"]["warnings"]:
                print("  WARNING:", item)
            return 2
        files = write_export(package, args.out)
        if args.json:
            print(json.dumps(package, indent=2, ensure_ascii=False))
        else:
            print(f"{package['status']}: {package['platform_label']} package for human review")
            for item in package["preflight"]["warnings"]:
                print("  WARNING:", item)
            for decision in package["human_decisions"]:
                print(f"  DECIDE YOURSELF: {decision['id']} (nothing pre-selected)")
        for path in files:
            print(f"Wrote {path}", file=sys.stderr)
        print("Nothing was sent, uploaded, scheduled or shared.", file=sys.stderr)
        return 0
    if args.command == "campaign-check":
        campaign = read_json(args.file)
        brand = read_json(args.brand) if args.brand else None
        history = _campaign_history(args, campaign)
        errors, warnings = validate_campaign(campaign, brand=brand, optout=load_optout(args.optout),
                                             history=history)
        if args.json:
            checked = {"valid": not errors, "errors": errors, "warnings": warnings}
            if history is not None:
                from .history import history_summary
                checked["history"] = history_summary(history)
            print(json.dumps(checked, indent=2))
        else:
            print("VALID" if not errors else "BLOCKED")
            for item in errors:
                print("  ERROR:",item)
            for item in warnings:
                print("  WARNING:",item)
            if history is not None:
                print(f"History: {len(history['posts'])} local post(s) of {len(history['accounts'])} account(s) "
                      f"read-only from {args.history_db}; database unchanged.")
            print("External evidence still requires human verification; no accounts were accessed.")
        return 2 if errors else 0
    if args.command == "campaign-preview":
        accounts_path = args.preview_accounts or args.accounts
        campaign = read_json(args.file)
        preview = build_preview(campaign, read_json(args.brand),
                                load_accounts(accounts_path) if accounts_path else None, utm=args.utm,
                                optout=load_optout(args.optout), history=_campaign_history(args, campaign))
        if args.json:
            print(json.dumps(preview, indent=2, ensure_ascii=False))
        elif args.markdown:
            print(render_preview_markdown(preview), end="")
        else:
            print(render_preview_text(preview))
        return 2 if preview["summary"]["low"] or preview["campaign_errors"] else 0
    if args.command == "claims-check":
        grounding = check_claims(read_json(args.campaign), read_json(args.brand),
                                 duplicate_threshold=args.duplicate_threshold, optout=load_optout(args.optout))
        if args.json:
            print(json.dumps(grounding, indent=2, ensure_ascii=False))
        else:
            print("GROUNDED (lexical)" if grounding["valid"] else "BLOCKED", "|", json.dumps(grounding["summary"]))
            for variant in grounding["variants"]:
                for f in variant["findings"]:
                    print(f"  {f['severity'].upper()} {variant['variant_id']}: {f['code']}: {f['message']}")
            print(grounding["limitations"])
        return 0 if grounding["valid"] else 2
    if args.command == "campaign-export":
        campaign, brand = read_json(args.file),read_json(args.brand)
        optout = load_optout(args.optout)
        errors, warnings = validate_campaign(campaign, brand=brand, optout=optout)
        if errors:
            raise ValidationError("; ".join(errors))
        # The directory must be entirely new; never overwrite a prior reviewed package.
        # Validation above already ran, so a bad pack never creates --out. The directory is
        # private (0700) and removed again if the export itself fails part-way.
        import shutil
        from .campaigns import export_campaign
        args.out.mkdir(parents=True, mode=0o700, exist_ok=False)
        try:
            files = export_campaign(campaign, brand, args.out, optout=optout)
        except BaseException:
            shutil.rmtree(args.out, ignore_errors=True)
            raise
        print(json.dumps({"exported":files,"warnings":warnings,"note":"DRAFTS ONLY. No approval or publication."},indent=2))
        return 0
    if args.command == "connect-check":
        if not args.online:
            raise ValidationError("connect-check requires explicit --online. Offline 'doctor' does not use network.")
        accounts = _accounts(args)
        account = accounts.get(args.account)
        if account is None:
            raise ValidationError("Unknown account. Run 'doctor' for account IDs")
        if account["platform"] == "bluesky" and account.get("backend") is None:
            # #11: createSession has its own per-account limit; reserve 1 call first.
            # Refusal (exit 2) happens before any request and before credentials are read.
            with connect(args.db) as db:
                reserve_bluesky_sessions(db, account, needed=1, stage="connect_check")
        connection = check_connection(account)
        output: dict = {"account_id":connection.account_id,"platform":connection.platform,
                        "identity":connection.identity,"write_permission":connection.write_permission}
        if connection.notes:  # non-secret advisories only (e.g. LinkedIn expiry consistency)
            output["notes"] = list(connection.notes)
        if account["platform"] == "bluesky":
            output["service"] = bluesky_service(account)
        instance = None
        if account["platform"] == "mastodon":  # public GET /api/v2/instance, no token sent
            instance = mastodon_instance_limits(account)
            output["instance"] = instance
        with connect(args.db) as db:
            since = account_disconnect.disconnected_since(db, args.account)
        output["local_state"] = "disconnected" if since is not None else "connected"
        if since is not None:
            output["disconnected_since"] = since
        output["note"] = "No content posted. Human-approved live pilot still required."
        print(json.dumps(output,indent=2))
        return 2 if instance is not None and not instance["ok"] else 0
    if args.command == "workspace" and args.init is not None:
        from . import workspace as ws_report
        made = ws_report.init_workspace(args.init.expanduser().resolve())
        if args.json:
            print(json.dumps(made, indent=2))
        else:
            print(f"Workspace folder: {made['root']}")
            for name in made["created"]:
                print(f"  created {name}")
            for name in made["kept"]:
                print(f"  kept    {name} (already there, not touched)")
            print("Everything that is yours now lives in this one folder. To use it, `cd` into it (or pass --home). "
                  "To move to another computer, copy the whole folder, then run `reachweaver workspace --repair --apply` there. "
                  "Credentials are never stored here; see secrets.env.example for the names.")
        return 0
    if args.command == "workspace" and (args.gather or args.repair):
        from . import workspace as ws_report
        if home.root() is None:
            raise ValidationError("Not inside a ReachWeaver workspace. Run `reachweaver workspace --init DIR` first, "
                                  "then `cd` into it or pass --home DIR")
        db_file = storage_database_path(args.db)
        if not db_file.is_file():
            raise ValidationError(f"Database {db_file} does not exist; nothing to gather or repair")
        workspace_root = home.root()
        assert workspace_root is not None
        moves_writer = connect(args.db) if args.apply else storage_connect_readonly(db_file)
        with moves_writer as mdb:
            moves = (ws_report.plan_gather(mdb, workspace_root) if args.gather else ws_report.plan_repair(mdb, workspace_root))
            if args.apply and args.gather:
                (workspace_root / "media").mkdir(mode=0o700, exist_ok=True)
                for move in moves:
                    if move["state"] == "ok" and not ws_report.copy_verified(move["from"], move["to"], move["sha256"]):
                        move["state"] = "missing"
            applied = ws_report.apply_relink(mdb, moves) if args.apply else {"relinked": [], "skipped": []}
        if args.json:
            print(json.dumps({"applied": args.apply, "attachments": moves, **applied}, indent=2))
        else:
            if not moves:
                print("Nothing to do: every attachment of an unsent post is already in media/" if args.gather
                      else "Nothing to repair: every attachment of an unsent post is where its record says.")
            for move in moves:
                print(f"{move['state']:<8} {move['post_id']} ({move['status']}) {move['kind']}: {move['from']}"
                      + (f" -> {move['to']}" if move["to"] else " (not found in media/)"))
            if args.apply:
                print(f"Updated {len(applied['relinked'])} post(s); left {len(applied['skipped'])} untouched.")
            elif moves:
                print("Dry run: nothing was changed. Add --apply to write. Only 'ok' rows are written, files are copied "
                      "or found by SHA-256, and the approval hash is checked to be unchanged.")
        return 1 if any(move["state"] != "ok" for move in moves) else 0
    if args.command == "workspace":
        from . import workspace as ws_report
        from .accounts import accounts_path as resolve_accounts_path
        db_file = storage_database_path(args.db)
        accounts_file = resolve_accounts_path(args.accounts)
        try:
            known = load_accounts(accounts_file)
        except ValidationError:
            known = None
        if not db_file.is_file():
            workspace_report: dict = {"workspace": str(home.root()) if home.root() else None,
                                "paths": [ws_report._entry("accounts", accounts_file), ws_report._entry("database", db_file),
                                          ws_report._entry("ideas inbox", db_file.parent / ws_report.IDEAS_FILENAME,
                                                           must_be_private=True)],
                      "database": {"counts": {}}, "ledger": "absent", "files": [],
                      "credentials_missing": {n: missing_credentials(a) for n, a in (known or {}).items() if missing_credentials(a)},
                      "not_in_the_folder": list(ws_report.NOT_IN_THE_FOLDER),
                      "problems": [{"code": "no_database_yet", "level": "info", "detail": f"{db_file} does not exist",
                                    "advice": "Nothing has been recorded here yet (`init` creates it), or you are in the wrong folder."}],
                      "ready_to_copy": True}
        else:
            with storage_connect_readonly(db_file) as ro:
                workspace_report = ws_report.inspect(ro, db_file, known, accounts_file, home.root() or Path.cwd(), home.root())
        print(json.dumps(workspace_report, indent=2) if args.json else ws_report.render(workspace_report))
        return 0 if workspace_report["ready_to_copy"] else 1
    if args.command == "media-relink":
        from . import workspace as ws_report
        db_file = storage_database_path(args.db)
        if not db_file.is_file():
            raise ValidationError(f"Database {db_file} does not exist; nothing to relink")
        if args.apply:
            with connect(args.db) as db:
                relink_plan = ws_report.plan_relink(db, args.from_prefix, args.to_prefix)
                relink_result = ws_report.apply_relink(db, relink_plan)
        else:
            with storage_connect_readonly(db_file) as ro:
                relink_plan = ws_report.plan_relink(ro, args.from_prefix, args.to_prefix)
            relink_result = {"relinked": [], "skipped": []}
        summary = {"applied": args.apply, "attachments": relink_plan, **relink_result}
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            if not relink_plan:
                print("No attachment of an unsent post is recorded under that folder.")
            for change in relink_plan:
                print(f"{change['state']:<8} {change['post_id']} ({change['status']}) {change['kind']}: {change['from']} -> {change['to']}")
            if args.apply:
                print(f"Relinked {len(relink_result['relinked'])} post(s); left {len(relink_result['skipped'])} untouched "
                      "(a file is missing or no longer matches its recorded SHA-256).")
            elif relink_plan:
                print("Dry run: nothing was changed. Add --apply to write these paths. Only 'ok' rows are written, and "
                      "the approval hash is checked to be unchanged.")
        return 1 if any(change["state"] != "ok" for change in relink_plan) else 0
    if args.command in ("journal", "archive"):
        from . import journal as posting_journal
        if isinstance(args.limit, bool) or not 1 <= args.limit <= 5000:
            raise ValidationError("--limit must be an integer in 1..5000")
        statuses = tuple(args.status) if args.status else (
            tuple(s for s in posting_journal.ALL_STATUSES if s != "cancelled") if args.all_statuses
            else posting_journal.POSTED_STATUSES)
        if not storage_database_path(args.db).is_file():
            print("No posts recorded yet: there is no database, so there is nothing to show or archive.")
            return 0
        with storage_connect_readonly(storage_database_path(args.db)) as ro:
            entries = posting_journal.build_entries(
                ro, statuses=statuses, platform=args.platform, account=args.account,
                since=_journal_bound(args.since, end=False), until=_journal_bound(args.until, end=True),
                post_id=args.post_id, limit=args.limit, by_hand=not args.no_by_hand,
                verify_files=getattr(args, "verify_files", False))
        if args.post_id is not None and not entries:
            raise ValidationError(f"Unknown post {args.post_id!r}")
        if args.command == "archive":
            out_dir = args.out or storage_database_path(args.db).parent / "archive"
            result = posting_journal.write_archive(entries, out_dir, copy_files=args.copy_files)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Archive {'created' if result['created'] else 'updated'}: {result['dir']}")
                print(f"  {len(result['archived'])} post(s) added, {len(result['skipped_existing'])} already there, "
                      f"{result['files_copied']} file(s) copied, {result['files_unavailable']} file(s) not copied "
                      "(missing or changed since they were attached)")
                if not args.copy_files:
                    print("  Files were not copied: add --copy-files to keep copies next to each post.")
                print("  Open index.md for the overview. The folder is private (0700) and nothing was sent.")
            return 0
        if args.format == "json":
            body = posting_journal.render_json(entries)
        elif args.format == "markdown":
            body = posting_journal.render_markdown(entries)
        elif args.format == "csv":
            body = posting_journal.render_csv(entries)
        else:
            headers, table_body = posting_journal.table_rows(entries)
            from . import term
            if not table_body:
                body = "No matching posts."
            elif term.enabled(sys.stdout):
                body = term.table(headers, table_body, sys.stdout, fixed=(4,))
            else:
                body = "\n".join(" | ".join(row) for row in [headers, *table_body])
        if args.out is not None:
            write_private_file(args.out, body + ("" if body.endswith("\n") else "\n"))
            print(f"Wrote {len(entries)} entries to {args.out} (owner-only file).")
        else:
            print(body, end="" if body.endswith("\n") else "\n")
        return 0
    if args.command in ("repeat-check", "mark-shared"):
        text = args.text
        if args.file is not None:
            if text is not None:
                raise ValidationError("Give --text or --file, not both")
            text = args.file.read_text(encoding="utf-8")
        images = [repeat_guard.sha256_file(path) for path in args.image]
        video_sha = repeat_guard.sha256_file(args.video) if args.video is not None else None
        links = [repeat_guard.canonical_link(url) for url in args.link]
        if any(link is None for link in links):
            raise ValidationError("--link must be an http(s) URL")
        clean_links = [link for link in links if link is not None]
        if args.command == "mark-shared":
            if args.platform not in PLATFORMS:
                raise ValidationError(f"Unknown platform {args.platform!r}; known: {', '.join(sorted(PLATFORMS))}")
            if args.account is not None:
                known = _accounts(args).get(args.account)
                if known is None or known["platform"] != args.platform:
                    raise ValidationError("Unknown account for that platform. Run 'doctor' for account IDs")
            if not (text or images or video_sha or clean_links):
                raise ValidationError("Give at least one of --text/--file, --image, --video or --link")
            with connect(args.db) as db:
                entry_id = record_shared(db, platform=args.platform, account_id=args.account, content=text or "",
                                         images=images, video=video_sha, links=clean_links, posted_at=args.at,
                                         url=args.url, note=args.note)
            print(f"Recorded {entry_id}: later drafts for {args.platform} are checked against this material. "
                  "Nothing was posted or sent.")
            return 0
        notes: list[str] = []
        if args.post_id is not None:
            if any((args.platform, args.account, text, images, video_sha, clean_links)):
                raise ValidationError("--post-id cannot be combined with --platform, --account, --text, --file, "
                                      "--image, --video or --link")
        elif not args.platform:
            raise ValidationError("Give --post-id, or --platform with the material to check")
        findings: list[dict] = []
        if not storage_database_path(args.db).is_file():
            notes.append("No history database yet, so there is nothing to compare with.")
        else:
            with storage_connect_readonly(storage_database_path(args.db)) as ro:
                if args.post_id is not None:
                    stored = get_post(ro, args.post_id)
                    findings = _repeat_findings(ro, stored, args.days)
                else:
                    findings = repeat_guard.find_repeats(
                        ro, platform=args.platform, account_id=args.account, days=args.days,
                        material={"text": text or "", "images": images, "video": video_sha, "links": clean_links})
        result = repeat_guard.summary(findings)
        result["notes"] = notes
        result["note"] = ("Read-only. Exact file and wording matches only; a re-encoded video or a reworded post is "
                          "not an exact match. Nothing was posted.")
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            for note in notes:
                print(note)
            if not findings and not notes:
                print("No repeat found: nothing you have shared or queued on this platform matches.")
            for finding in findings:
                tag = "BLOCKING" if finding in repeat_guard.blocking(findings) else "warning"
                print(f"{tag}: {repeat_guard.describe(finding)}")
            if result["blocking"]:
                print("`approve` refuses a blocking repeat unless you pass --allow-repeat.")
        return 1 if result["blocking"] else 0
    if args.command == "connect":
        from . import connect_guide
        accounts = _accounts(args)
        if args.account is not None and args.account not in accounts:
            raise ValidationError("Unknown account. Run 'doctor' for account IDs")
        if args.guide and args.json:
            raise ValidationError("connect --json prints the status table; it cannot be combined with --guide")
        registry = load_capabilities()
        gone: list[str] = []
        if storage_database_path(args.db).is_file():  # read-only: the status table never creates or changes the database
            try:
                with storage_connect_readonly(storage_database_path(args.db)) as ro:
                    gone = account_disconnect.disconnected_accounts(ro)
            except (ValidationError, sqlite3.Error):
                gone = []
        if not args.guide:
            rows = connect_guide.account_rows(accounts, registry, disconnected=gone)
            if args.account is not None:
                rows = [r for r in rows if r["account"] == args.account]
            if args.json:
                print(json.dumps({"accounts": rows, "note": "Offline. Credential variable NAMES only; no request was made."}, indent=2))
            else:
                print(connect_guide.render_rows(rows))
                print("\nOffline status only. `reachweaver connect --guide` walks through the accounts one at a time with you present.")
            return 0
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise ValidationError("connect --guide needs an interactive terminal with you present (sign-ins and any "
                                  "verification are yours to complete). Run it yourself in a terminal; "
                                  "`reachweaver connect` prints the status table without prompts.")

        def ask(prompt: str) -> str:
            try:
                return input(prompt)
            except EOFError:
                return "q"

        def guided_check(account: dict) -> str | None:
            try:
                if account["platform"] == "bluesky" and account.get("backend") is None:
                    with connect(args.db) as db:
                        reserve_bluesky_sessions(db, account, needed=1, stage="connect_check")
                check_connection(account)
            except (ValidationError, PublishError, HTTPError, URLError, OSError) as exc:
                if isinstance(exc, HTTPError):
                    close_http_error(exc)
                return sanitize_error_code(str(exc))
            return None

        print("ReachWeaver connection guide. You stay in control: nothing is posted, no secret is asked for, and "
              "every online check needs your 'y'.")
        results = connect_guide.run_guided(accounts, registry, only=args.account, ask=ask, say=print,
                                           check=guided_check, disconnected=gone)
        print(connect_guide.render_summary(results))
        return 1 if any(r["state"] == connect_guide.FAILED for r in results) else 0
    if args.command == "disconnect":
        account = _accounts(args).get(args.account)
        if account is None:
            raise ValidationError("Unknown account. Run 'doctor' for account IDs")
        with connect(args.db) as db:
            first = account_disconnect.disconnect(db, args.account)
        print(("Disconnected " if first else "Already disconnected ") + f"{args.account}: nothing will be claimed or sent for it "
              "on this installation. This revoked nothing at the provider.")
        for step in account_disconnect.revocation_steps(account):
            print(step)
        return 0
    if args.command == "review":  # #11: owner-only loop; each answer runs the real approve or cancel command
        if args.verify_identity and not args.online:
            raise ValidationError("--verify-identity requires explicit --online (one read-only identity request per "
                                  "approval). Nothing shown or approved")
        from .review_loop import run as run_review_loop
        return run_review_loop(args, execute=execute, make_parser=make_parser)
    if args.command == "reconnect":
        with connect(args.db) as db:
            lifted = account_disconnect.reconnect(db, args.account)
        print(f"Reconnected {args.account}. Run connect-check --online before the next live send." if lifted
              else f"{args.account} is not disconnected; nothing changed.")
        return 0
    if args.command == "postiz-check":
        if not args.online:
            raise ValidationError("postiz-check requires explicit --online. Offline 'doctor' does not use network.")
        from .postiz import list_integrations, lookup_backend_post, match_integration
        account = _accounts(args).get(args.account)
        if account is None or account.get("backend") != "postiz":
            raise ValidationError("Unknown account or account has no \"backend\": \"postiz\". Run 'doctor'")
        rows = list_integrations(account)
        try:
            match = match_integration(account, rows)
            identity = {"identity": match.identity, "provider": match.provider,
                        "write_permission": match.write_permission}
        except ValidationError as exc:
            identity = {"identity": "mismatch", "reason": str(exc)}
        result = {"account_id": account["id"], "platform": account["platform"], "backend": "postiz",
                  "configured_integration_id": account["postiz_integration_id"], **identity,
                  "integrations": rows,
                  "note": "Read-only. No content posted. Postiz acceptance is never a platform receipt."}
        if args.post_id:
            with connect(args.db) as db:
                post = get_post(db, args.post_id)
                if post["account_id"] != account["id"]:
                    raise ValidationError("Post belongs to a different account")
                result["receipt_lookup"] = {
                    **lookup_backend_post(account, post["remote_id"] or "",
                                          post["send_started_at"] or post["created_at"]),
                    "local_status": post["status"],
                    "note": "Backend-reported only: open the native post, compare the exact text, then run 'reconcile'."}
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "idea":  # ideas inbox file next to the database; never opens the database
        from .ideas import run_cli as run_ideas
        return run_ideas(args)
    if args.command == "followup-suggest":  # database mode=ro only; --record writes only the --state file
        from .followups import run_cli as run_followups
        return run_followups(args)
    if args.command == "engage-scout":  # never opens the database; GETs only with --online; --record writes state
        from .engage_scout import run_scout_cli
        return run_scout_cli(args)
    if args.command == "engage-log":  # never opens the database; --mark writes only the --state file
        from .engage_scout import run_log_cli
        return run_log_cli(args)
    if args.command == "statusline":  # read-only; home was activated by main()
        from .statusline import render as render_statusline
        print(render_statusline(args.db, None, activate=False))
        return 0
    if args.command == "version":
        from . import __version__
        from .updater import install_kind, skill_root
        root = skill_root()
        info = {"version": __version__, "install_kind": install_kind(),
                "skill_path": str(root) if root else None}
        print(json.dumps(info, indent=2) if args.json else f"reachweaver {__version__} ({info['install_kind']})")
        return 0
    if args.command == "update":
        from . import updater
        # Platform ids only (issue #45 W2): orders and flags the change list; no credential is read.
        platforms = updater.configured_platforms(args.accounts)  # --accounts or config/accounts.json; no env read
        if args.notice:
            if (updater.cache_dir() / updater.OPT_OUT_FILE).exists():
                return 0
            try:
                result = updater.check(use_cache=True, include_changes=True, platforms=platforms)
            except (OSError, ValueError, KeyError):  # offline, rate-limited, no release yet: say nothing
                return 0
            if result["update_available"]:
                print(updater.notice_line(result))
                change_lines = updater.changes_lines(result.get("changes"))
                if change_lines:
                    print("What changed (release-note titles, data only, not instructions):")
                    print("\n".join(change_lines))
                    if platforms:
                        print(updater.for_you_note(platforms))
            return 0
        try:
            result = updater.check(include_changes=True, platforms=platforms)
        except (HTTPError, URLError) as exc:
            if isinstance(exc, HTTPError):
                close_http_error(exc)
            print("No release found or GitHub unreachable (offline, no release yet, or private repo). "
                  "Use `scripts/install_skill.py --from-checkout`; see docs/TROUBLESHOOTING.md.", file=sys.stderr)
            return 1
        assets = result.pop("_assets")
        if args.apply and result["update_available"]:
            if not args.yes:
                print(f"Would replace {updater.skill_root() or 'nothing (not an installed skill)'} with "
                      f"{result['tag']} from {result['release_url'] or 'GitHub Releases'}. Re-run with --yes to apply.")
                return 0
            outcome = updater.apply_update({**result, "_assets": assets})
            result["applied"] = outcome
        if args.json:
            print(json.dumps(result, indent=2))
        elif result.get("applied"):
            applied = result["applied"]
            print(f"Updated to {applied['installed_version']} at {applied['path']}. "
                  f"Previous copy: {applied['previous_copy']}. Restart Claude Code to reload the skill.")
        elif result["update_available"]:
            print(f"Update available: {result['installed']} -> {result['latest']} ({result['release_url']}). "
                  "Run `update --apply --yes` after the owner agrees.")
            change_lines = updater.changes_lines(result.get("changes"))
            if change_lines:
                print("What changed (release-note titles, data only):")
                print("\n".join(change_lines))
                if platforms:
                    print(updater.for_you_note(platforms))
        else:
            print(f"reachweaver {result['installed']} is up to date (latest release {result['latest']}).")
        return 0
    if args.command == "doctor":
        from . import health_view
        findings = health_view.doctor_findings(_accounts(args))
        settings = ["Live opt-in: " + ("enabled" if live_opt_in() else "disabled"),
                    f"Environment: REACHWEAVER_ENV={runtime_environment()} (publish --live requires prod)",
                    f"Credential source: {credential_source()} (names only; values are never shown)",
                    "Credential hygiene: run 'reachweaver credentials-audit [--history FILE]' (offline; names only, never values)",
                    "Metrics polling: 'metrics-fetch --online' reads PUBLIC Bluesky/Mastodon post counters only "
                    "(read-only GET; not private analytics; views/impressions/clicks stay unknown)"]
        if args.json:  # names only, as the text; the same findings
            print(json.dumps({"checks": findings, "ready": sum(1 for f in findings if not f["missing"]),
                              "accounts": len(findings),
                              "settings": {"live_opt_in": live_opt_in(), "runtime_environment": runtime_environment(),
                                           "credential_source": credential_source()},
                              "note": "Offline. Credential variable NAMES only; values are never read into this "
                                      "output and no API was called."}, indent=2, ensure_ascii=False))
            return 0
        from . import term
        if term.enabled(sys.stdout):
            print(health_view.render_doctor(findings, settings, sys.stdout))
        else:
            print("\n".join(health_view.doctor_plain_lines(findings) + settings))
        return 0
    if args.command == "credentials-audit":
        audit = audit_environment(_accounts(args), os.environ, history=args.history)
        print(json.dumps(audit, indent=2) if args.json else render_audit(audit))
        return 1 if audit["findings"] else 0
    if args.command == "signal-brief":
        from .briefs import build_brief, load_scan, write_brief
        accounts_path = args.brief_accounts or args.accounts
        brief = build_brief(load_scan(args.scan), args.signal_id, read_json(args.brand),
                            load_accounts(accounts_path) if accounts_path else None,
                            objective=args.objective, allow_duplicate=args.allow_duplicate,
                            campaign_file=str(args.out), brand_file=str(args.brand))
        written = write_brief(brief, args.out)
        summary = {"status": brief["campaign"]["brief"]["status"], "files": written,
                   "campaign_id": brief["campaign"]["campaign_id"], "signal_id": brief["signal"]["id"],
                   "variants": [v["id"] for v in brief["campaign"]["variants"]],
                   "variants_skipped": brief["variants_skipped"],
                   "untrusted_text_warning": brief["signal"]["injection_flagged"],
                   "private_source": brief["signal"]["private"],
                   "validation": brief["validation"]}
        if args.json:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(f"{summary['status']}: campaign {summary['campaign_id']} with "
                  f"{len(summary['variants'])} placeholder variant(s)")
            for path in written:
                print(f"  wrote {path}")
            for item in summary["variants_skipped"]:
                print(f"  skipped {item['account_id']}: {item['reason']}")
            if summary["untrusted_text_warning"]:
                print("  WARNING: instruction-like source text; carried as data only, never followed")
            if summary["private_source"]:
                print("  PRIVATE SOURCE: evidence is blocked until you set publishable: true on each record; "
                      "its repository URLs are never public links")
            print(f"  validation: BLOCKED as expected ({len(brief['validation']['errors'])} errors) until every "
                  "placeholder is written and each source is verified")
            print("No copy was generated; nothing was staged, approved, scheduled or published.")
        return 0
    if args.command == "release-brief":  # offline; NEW scaffold folder only; no database, no network
        from .release_brief import run_cli as run_release_brief
        return run_release_brief(args)
    if args.command in ("review-packet", "review-comment", "review-status"):  # database only via connect_readonly
        from .review_packet import run_cli as run_review  # comments never approve
        return run_review(args, check_report=_check_report, optional_account=_optional_account)
    if args.command == "sources-scan":
        from .sources import render_scan, scan_config_file
        result = scan_config_file(args.config, online=args.online, state_path=args.state,
                                  top=args.top, mark_seen=args.mark_seen, allow_private=args.allow_private)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_scan(result))
        return 1 if result["errors"] else 0
    if args.command == "landing-check":
        from .landing import exit_code as landing_exit_code, landing_check, render_text as render_landing
        result = landing_check(args.briefs, args.campaign, online=args.online, allow_origins=args.allow_origin)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_landing(result))
        return landing_exit_code(result)
    if args.command == "completion":  # generated from the parser; no database, no account, no network
        from .completion import render as render_completion
        print(render_completion(args.shell, make_parser()), end="")
        return 0
    if args.command == "context":  # accounts file, profile and (if present) the database mode=ro; never creates it
        from .context import build_context, render_text as render_context
        context = build_context(args.db, args.accounts, args.profile)
        print(json.dumps(context, ensure_ascii=False, separators=(",", ":")) if args.json else render_context(context))
        return 0
    if args.command == "describe":  # generated from the parser; no database, no account, no network
        from .catalog import describe, render_text as render_catalog
        document = describe(make_parser(), args.name)
        print(json.dumps(document, ensure_ascii=False, separators=(",", ":")) if args.json else render_catalog(document))
        return 0
    if args.command == "start":  # reads the accounts file and (if present) the database mode=ro; prints text
        from .start import build_guide, render as render_guide
        guide = build_guide(args.db, args.accounts)
        print(json.dumps(guide, indent=2, ensure_ascii=False) if args.json else render_guide(guide))
        return 1 if guide["state"] in ("accounts_invalid", "database_unreadable") else 0
    if args.command == "ui":  # read-only server on loopback; opens the database mode=ro, never creates it
        return _run_ui(args)
    if args.command == "mcp-serve":  # draft: stdio MCP server, allowlisted read-only tools (docs/MCP-SERVER.md)
        from .mcp_server import serve as serve_mcp
        return serve_mcp(args)
    if args.command in READ_ONLY_DB_COMMANDS:
        return _execute_read_only(args)
    with connect(args.db) as db:
        if args.command == "init":
            print("Initialized local database. No accounts were accessed.")
        elif args.command == "draft":
            if args.json and args.reply_to is not None:
                raise ValidationError("draft --json is not available with --reply-to; nothing created")
            accounts = _accounts(args)
            if len(args.account) > 1:
                return _draft_many(db, args, accounts)
            args.account = args.account[0]
            account = accounts.get(args.account)
            if not account:
                raise ValidationError("Unknown account. Run 'doctor' to list configured accounts")
            wrong = _persona_gate(args, [account], accounts, [args.account])
            if wrong:
                raise ValidationError(f"Draft refused, nothing created: {wrong[0]}")
            content = args.file.read_text(encoding="utf-8") if args.file else args.text
            optout = load_optout(args.optout)
            hits = optout_mentions(content, optout, account["platform"])
            if hits:
                raise ValidationError(f"Draft refused, nothing created: {describe_mentions(hits)}")
            private = _private_link_refusal(args, [content], [args.source_url])  # #9, reply drafts too
            if private:
                raise ValidationError(f"Draft refused, nothing created: {private}")
            if args.reply_to is None:
                if args.online or args.override_flagged is not None:
                    raise ValidationError("--online and --override-flagged apply only with --reply-to")
                post_id = add_post(db,args.account,account["platform"],content,args.campaign,args.source_url,account=account)
                if args.json:
                    print(json.dumps(_drafts_document([(post_id, args.account)], accounts), indent=2))
                else:
                    print(f"Created draft {post_id} for {args.account}; not published")
            else:
                return _draft_reply(db, args, account, content, optout)
        elif args.command == "media-attach":
            post = get_post(db, args.post_id)
            account = _optional_account(args, post["account_id"], post["platform"])
            source = MEDIA_PLATFORMS.get(post["platform"])
            if source is None:
                raise ValidationError(UNSUPPORTED_PLATFORM.format(platform=post["platform"]))
            if post["status"] != "draft":
                raise ValidationError(f"Attachments can only change on a draft (post {args.post_id} is "
                                      f"{post['status']}); run 'reachweaver unapprove {args.post_id}' first")
            alt_text = args.alt_file.read_text(encoding="utf-8") if args.alt_file else args.alt
            if source == "local":  # #13 Mastodon: a local file, offline
                if args.file is None:
                    raise ValidationError("Mastodon attachments are local files: use --file PATH (--url is for Instagram)")
                if args.online:
                    raise ValidationError("--online is only for --url (Instagram); a local file is read offline")
                from .attachments import build_attachment
                attachment = build_attachment(args.file, alt_text, args.focus, account=account)
                from .attachments import gps_warning
                gps_note = gps_warning(args.file, account)  # advisory only; never blocks or alters the record
            else:  # #20 Instagram: an owner-hosted URL, one read-only GET with explicit --online
                if args.url is None:
                    raise ValidationError("Instagram images are owner-hosted URLs: use --url URL --online "
                                          "(--file is for Mastodon)")
                if args.focus:
                    raise ValidationError("--focus is Mastodon only; Instagram has no focal point")
                if not args.online:
                    raise ValidationError("media-attach requires explicit --online: it downloads the image URL "
                                          "read-only to validate it and record its SHA-256. Nothing is uploaded or posted")
                from .remote_media import build_remote_attachment
                attachment = build_remote_attachment(args.url, alt_text)
            rights_note: list[str] = []
            if any((args.license_id, args.created_by, args.rights_confirmed, args.tool, args.attribution,
                    args.rights_source_url, args.alt_translation)):  # #48: an optional rights record
                from .attachments import build_rights
                translations: dict[str, str] = {}
                for item in args.alt_translation:
                    lang, sep, text = item.partition("=")
                    if not sep or lang in translations:
                        raise ValidationError("--alt-translation needs LANG=TEXT, once per language")
                    translations[lang] = text
                rights, rights_note = build_rights(
                    license_id=args.license_id, created_by=args.created_by, rights_confirmed=True if args.rights_confirmed else None,
                    attribution=args.attribution, tool=args.tool, source_url=args.rights_source_url,
                    alt_translations=translations)
                attachment["rights"] = rights
            texts = [attachment["alt"], *(attachment.get("rights", {}).get("alt_translations", {}).values()),
                     *([attachment["rights"]["attribution"]] if attachment.get("rights", {}).get("attribution") else [])]
            for text in texts:
                hits = optout_mentions(text, load_optout(args.optout), post["platform"])
                if hits:
                    raise ValidationError(f"Attachment refused, nothing changed: {describe_mentions(hits)}")
            private = _private_link_refusal(args, texts, [attachment.get("url"),
                                                          attachment.get("rights", {}).get("provenance", {}).get("source_url")])  # #9
            if private:
                raise ValidationError(f"Attachment refused, nothing changed: {private}")
            media = set_post_media(db, args.post_id, [*post_media(post), attachment], account=account)
            print(f"Attached image {len(media)} to draft {args.post_id}: {attachment['mime']} {attachment['width']}x"
                  f"{attachment['height']} {attachment['bytes']} bytes sha256 {attachment['sha256'][:12]}; "
                  f"alt {len(attachment['alt'])} chars"
                  + (f"; focus {attachment['focus']}" if attachment["focus"] else "")
                  + (f"; url {attachment['url']}. Not uploaded; review with 'show', then approve binds the bytes and URL. "
                     "Keep the image at that URL unchanged until it is published" if source == "url"
                     else ". Not uploaded; review with 'show', then approve binds it"))
            if attachment.get("rights"):
                print(f"Rights recorded (#48): license {attachment['rights']['license']}, made by "
                      f"{attachment['rights']['provenance']['created_by']}; bound at approval (approval/media-2)")
                for note in rights_note:
                    print(f"NOTE: {note}")
            if source == "local" and gps_note:
                print(f"WARNING: {gps_note}")
            if account is None and source == "local":
                print("Note: account config not loaded; checked against the conservative default limits")
        elif args.command == "media-detach":
            try:
                removed: int | str = len(post_media(get_post(db, args.post_id)))
            except ValidationError:
                removed = "malformed"  # detaching is the repair for an invalid stored record
            set_post_media(db, args.post_id, [])
            print(f"Removed {removed} attachment(s) from draft {args.post_id}; local files and hosted images untouched")
        elif args.command == "campaign-stage":
            campaign, brand = read_json(args.file),read_json(args.brand)
            accounts = _accounts(args)
            staged = stage_campaign(db,campaign,brand,accounts,optout=load_optout(args.optout))
            print(json.dumps({"staged":staged,"status":"draft_only","note":"Manual human source-check + approval remain required."},indent=2))
        elif args.command == "timing-evaluate":
            accounts = _accounts(args)
            account = accounts.get(args.account)
            if account is None:
                raise ValidationError("Unknown account. Configure your own account metadata first")
            result = analyze_posting_times(
                db, account_id=args.account, platform=account["platform"],
                audience_timezone=args.timezone, source=args.source,
                metric=args.metric, denominator=args.denominator,
                age_hours=args.age_hours, tolerance_hours=args.tolerance_hours,
                min_posts_per_slot=args.min_posts_per_slot, campaign=args.campaign,
                since=args.since, until=args.until, stratify=args.stratify,
            )
            print(json.dumps(result, indent=2))
        elif args.command == "attribution-report":
            ga4 = combine_ga4([load_ga4_csv(f) for f in args.ga4_csv or []])
            gsc = combine_gsc([load_gsc_csv(f) for f in args.gsc_csv or []])
            result = attribution_report(db, load_analytics_csv(args.analytics) if args.analytics else None,
                                        [load_github_traffic(f) for f in args.github_traffic or []],
                                        ga4=ga4 or None, gsc=gsc or None)
            result["generated_at"] = utc_now()
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                for item in result["attributed"]["posts"]:
                    print(f"ATTRIBUTED post {item['post_id']} {item['platform']}: "
                          f"sessions={item['sessions']} conversions={item['conversions']}")
                for item in result["attributed"]["campaigns"]:
                    print(f"ATTRIBUTED campaign {item['campaign']} (no post id): sessions={item['sessions']}")
                for item in result["referrer_only"]["analytics"]:
                    print(f"REFERRER-ONLY {item['platform']} (domain level): sessions={item['sessions']}")
                for item in result["referrer_only"]["github"]:
                    print(f"REFERRER-ONLY GitHub {item['referrer']}: views={item['count']}")
                print(f"UNATTRIBUTED rows={result['unattributed']['rows']} "
                      f"by_reason={result['unattributed']['by_reason']}")
                for gap in result["coverage_gaps"]:
                    print("GAP:", gap["gap"])
                section = result["ga4"]
                if section:
                    window = " to ".join(section["date_range"]) if section["date_range"] else "unknown dates"
                    print(f"GA4 export (owner-supplied, {window}): rows={section['rows']}")
                    for item in section["attributed"]["posts"]:
                        print(f"GA4 ATTRIBUTED post {item['post_id']} {item['platform']}: sessions={item['sessions']} "
                              f"engaged_sessions={item['engaged_sessions']} key_events={item['key_events']}")
                    for item in section["attributed"]["campaigns"]:
                        print(f"GA4 ATTRIBUTED campaign {item['campaign']} (no post id): sessions={item['sessions']}")
                    for item in section["referrer_only"]:
                        print(f"GA4 REFERRER-ONLY {item['platform']} (domain level): sessions={item['sessions']}")
                    print(f"GA4 UNATTRIBUTED rows={section['unattributed']['rows']} "
                          f"by_reason={section['unattributed']['by_reason']}")
                    for gap in section["coverage_gaps"]:
                        print("GA4 GAP:", gap["gap"])
                section = result["search_console"]
                if section:
                    window = " to ".join(section["date_range"]) if section["date_range"] else (
                        section["date_filter_label"] or "unknown dates")
                    print(f"SEARCH CONSOLE export (owner-supplied, {window}): search-side context, never credited "
                          "to a post")
                    for item in section["pages_linked_from_posts"]:
                        print(f"SEARCH page {item['page']} linked from {', '.join(item['linked_from_post_ids'])}: "
                              f"clicks={item['clicks']} impressions={item['impressions']}")
                    for gap in section["coverage_gaps"]:
                        print("SEARCH CONSOLE GAP:", gap["gap"])
                print("None = unknown, not zero. Tagging join only; no causal claim.")
        elif args.command == "experiment-evaluate":
            result = evaluate_experiment(db,read_json(args.file))
            print(json.dumps(result,indent=2))
        elif args.command == "experiment-readout":
            attribution = load_attribution_outcomes(args.attribution, args.attribution_source) if args.attribution is not None else None
            result = run_readout(db, read_json(args.prereg), read_json(args.posts), expected_hash=args.expected_hash,
                                 attribution=attribution)
            print(json.dumps(result, indent=2) if args.json else render_readout(result))
        elif args.command == "list":
            query = "SELECT id,account_id,platform,status,scheduled_at,remote_url FROM posts"
            params: tuple = ()
            if args.status:
                query += " WHERE status=?"
                params = (args.status,)
            rows = db.execute(query + " ORDER BY created_at DESC",params).fetchall()
            from . import term
            if args.json:
                print(json.dumps([dict(row) for row in rows], indent=2, ensure_ascii=False))
            elif term.enabled(sys.stdout):  # a terminal gets an aligned, coloured table; pipes keep the plain format
                if rows:
                    print(term.table(["ID", "ACCOUNT", "PLATFORM", "STATUS", "SCHEDULED", "REMOTE URL"],
                                     [[str(row[k] or "") for k in row.keys()] for row in rows], sys.stdout,
                                     paint=lambda col, text: term.STATUS_COLOUR.get(text) if col == 3 else None,
                                     fixed=(0,)))
                    print(f"\n{len(rows)} post(s). Review one: reachweaver show POST_ID")
                elif args.status:
                    print(f"No {args.status} posts. See them all: reachweaver list")
                else:
                    print("No posts yet. Create a draft: reachweaver draft --account ACCOUNT --text 'Your text'")
            else:
                for row in rows:
                    print(" | ".join(str(row[k] or "") for k in row.keys()))
                if not rows:  # stdout stays empty for parsers; the note goes to stderr
                    print("No posts" + (f" with status {args.status}" if args.status else ""), file=sys.stderr)
        elif args.command == "show":
            post = get_post(db,args.post_id)
            shown = build_post_view(db, post, _optional_account(args, post["account_id"], post["platform"]))
            from . import term
            if term.enabled(sys.stdout):  # a person at a terminal gets the review sheet; pipes and --plain get JSON
                from .show_view import render_sheet
                from .ui_data import next_steps
                print(render_sheet(shown, next_steps(post), sys.stdout))
            else:
                print(json.dumps(shown,indent=2,ensure_ascii=False))
        elif args.command == "check":
            post = get_post(db,args.post_id)
            issues, repeats, blocking_repeats = _check_findings(args, db, post)
            if args.json:  # the same findings and exit code as the text
                print(json.dumps(_check_report(args, db, post, (issues, repeats, blocking_repeats)),
                                 indent=2, ensure_ascii=False))
                return 1 if issues else 0
            from . import term
            print(term.style("OK", "green", sys.stdout) if not issues
                  else term.style("BLOCKED", "red", sys.stdout) + ": " + "; ".join(issues))
            for hit in repeats:
                if hit not in blocking_repeats:
                    print(f"Warning: {repeat_guard.describe(hit)}")
            if blocking_repeats:
                print("Pass --allow-repeat to approve if sharing it again is intended.")
            if post["platform"] not in SUPPORTED_LIVE:
                print("Note: this platform is draft/manual only")
            return 1 if issues else 0
        elif args.command == "approve":
            post = get_post(db,args.post_id)
            wrong = _persona_gate(args, [_optional_account(args, post["account_id"], post["platform"])],
                                  _optional_accounts_map(args), [post["account_id"]])
            if wrong:
                raise ValidationError(f"Refusing to approve {args.post_id}: {wrong[0]} Nothing changed")
            optout = load_optout(args.optout)
            hits = optout_mentions(published_text(post), optout, post["platform"])
            if hits:
                raise ValidationError(f"Refusing to approve {args.post_id}: {describe_mentions(hits)}. "
                                      "Create a corrected draft and cancel this one (reachweaver cancel POST_ID)")
            private = _post_private_link_refusal(args, post)  # #9: never approve a private repository link
            if private:
                raise ValidationError(f"Refusing to approve {args.post_id}: {private}. Nothing changed")
            media_issues = _media_issues(post, _optional_account(args, post["account_id"], post["platform"]))
            if media_issues:
                raise ValidationError(f"Refusing to approve {args.post_id}: {'; '.join(media_issues)}")
            video_issues = _video_issues(post)  # #22/#23: the bytes on disk must be the ones being approved
            if video_issues:
                raise ValidationError(f"Refusing to approve {args.post_id}: {'; '.join(video_issues)}")
            repeats = _repeat_findings(db, post)
            blocking_repeats = repeat_guard.blocking(repeats)
            if blocking_repeats and not args.allow_repeat:
                raise ValidationError(f"Refusing to approve {args.post_id}: {repeat_guard.describe(blocking_repeats[0])}. "
                                      "Check `repeat-check --post-id` for the full list; pass --allow-repeat only if "
                                      "sharing it again is intended. Nothing changed")
            for hit in repeats:
                print(f"Warning: {repeat_guard.describe(hit)}")
            queued_exact = [hit for hit in repeats if hit["kind"] in repeat_guard.BLOCKING_KINDS
                            and hit["strength"] == "exact" and hit["state"] == "also_queued" and hit["same_account"]]
            if queued_exact and not args.allow_repeat:  # sheet item 1: say now what the send-time check will do
                print("Note: once one of these copies is claimed for sending, the send-time repeat check refuses the "
                      "others (repeat_of_shared_material); approve with --allow-repeat only if every copy should go out")
            # Sheet items 5/9: the account's send-shaping settings are bound with the text. A Mastodon post
            # always has them, so it is not approved without its account entry.
            send_account = _optional_account(args, post["account_id"], post["platform"])
            options_fp = send_options_fingerprint(send_account)
            if options_fp is None and post["platform"] in SEND_OPTIONS_ALWAYS_BOUND:
                raise ValidationError(f"Refusing to approve {args.post_id}: {SEND_OPTIONS_UNAVAILABLE}: the "
                                      f"{post['platform']} account entry {post['account_id']} could not be loaded, so "
                                      "its send options (visibility, language, spoiler_text) cannot be bound; fix "
                                      "the account configuration ('doctor'). Nothing approved")
            reply_problems = _reply_problems(post, send_account, optout)
            if reply_problems:
                raise ValidationError(f"Refusing to approve reply {args.post_id}: {'; '.join(reply_problems)}. "
                                      "Nothing changed")
            bound_identity: str | None = None
            if identity_mode_required(db) and not args.verify_identity:
                raise ValidationError(refusal_text(IDENTITY_REQUIRED, args.post_id))
            if args.verify_identity:
                # Wave 9 item 2: one read-only identity check (the connect-check call class), before
                # anything is written. A failure raises here, so the post stays a draft.
                if not args.online:
                    raise ValidationError("--verify-identity requires explicit --online (one read-only identity "
                                          "request). Nothing approved")
                if post["status"] != "draft":
                    raise ValidationError(f"Cannot approve post in state {post['status']}. Nothing approved")
                id_account = _accounts(args).get(post["account_id"])
                if id_account is None or id_account["platform"] != post["platform"]:
                    raise ValidationError("Unknown account for this post; run 'doctor'. Nothing approved")
                if id_account["platform"] == "bluesky" and id_account.get("backend") is None:
                    reserve_bluesky_sessions(db, id_account, needed=1, stage="connect_check")  # #11, as connect-check
                bound_identity = verify_identity_online(id_account)
            approver, channel = args.approver, "cli_flag"
            if approver is None:
                approver = os.environ.get("USER") or os.environ.get("LOGNAME") or None
                channel = "cli_env_user" if approver else "cli_unattributed"
            confirmed, expected = None, None
            if confirmation.confirmation_mode(db) and post["status"] == "draft":
                # #7 D6 (opt-in): show what would be bound, then the TTY + typed-prefix check.
                expected = approval_payload_sha256(post, scheduled_at=None, use_row_schedule=False, identity=bound_identity)
                confirmation.show_payload(post, expected, heading="CONFIRM approval of",
                                          send_options=send_options_describe(send_account),
                                          allow_repeat=bool(args.allow_repeat))
                confirmed = confirmation.confirm_post(db, args.post_id, expected, action="approve")
            from .storage import _atomic
            with _atomic(db):  # an --allow-repeat override is recorded only with the approval it belongs to
                target = transition(db, args.post_id, "approve", approver=approver, approval_channel=channel,
                                    expected_sha256=expected, confirmation=confirmed, identity=bound_identity,
                                    send_options=options_fp, allow_repeat=bool(args.allow_repeat))
                if blocking_repeats:  # --allow-repeat: an audit trace tied to this approval. Never read as
                    # permission: the send-time check reads the bound approved_allow_repeat.
                    approved_hash = get_post(db, args.post_id)["approved_payload_sha256"] or ""
                    record_event(db, "repeat_override", args.post_id,
                                 "kinds=" + ",".join(sorted({hit["kind"] for hit in blocking_repeats}))
                                 + f" approval_sha256={approved_hash[:12]}")
            post = get_post(db, args.post_id)
            bound = post["approved_payload_sha256"]
            print(f"Post {args.post_id} -> {target} (payload sha256 {bound[:12]}, "
                  f"approver {approver or 'unattributed'} via {channel}"
                  + (", keyed hmac-sha256/1" if post["approved_payload_mac"] else "")
                  + (", identity bound (approval/id-1)" if bound_identity else "")
                  + (", send options bound (approval/options-1)" if options_fp else "")
                  + (", --allow-repeat bound (approval/repeat-1)" if args.allow_repeat else "")
                  + (f", confirmed {confirmed}" if confirmed else "") + ")")
            if options_fp:
                print(f"Bound send options: {send_options_describe(send_account)}; changing them in the account "
                      "file refuses the send until re-approved")
            print(f"Bound: account={post['account_id']} platform={post['platform']} "
                  f"campaign={post['campaign'] or '-'} source_url={post['source_url'] or '-'} "
                  f"content={len(post['content'])} chars"
                  + (f" media={len(post_media(post))} (sha256, mime, alt, focus"
                     + (", url" if any(item["source"] == "url" for item in post_media(post)) else "") + " each)"
                     if post_media(post) else "")
                  + (f" video={video['sha256'][:12]} ({video_mode(video)}; whole record except its path)"
                     if (video := post_video(post)) else "")
                  + "; any later change refuses the send until re-approved")
            from .replies import row_target
            bound_reply = row_target(post)
            if bound_reply is not None:
                where = (f"in_reply_to_id={bound_reply['in_reply_to_id']} server={bound_reply['server']} "
                         f"visibility={bound_reply['visibility']}" if bound_reply["platform"] == "mastodon" else
                         f"parent={bound_reply['parent']['uri']} root={bound_reply['root']['uri']} (URI+CID)")
                print(f"Bound reply target: {where}; a changed target refuses the send until re-approved. "
                      "Sending stays blocked until the capability registry enables replies")
        elif args.command == "video-attach":
            from .tiktok import build_video_record
            post = get_post(db, args.post_id)
            if post["platform"] != "tiktok":
                raise ValidationError(f"video-attach supports TikTok drafts only, not {post['platform']}")
            record = build_video_record(args.file, mode=args.mode, privacy_level=args.privacy_level,
                                        allow_comment=args.allow_comment, allow_duet=args.allow_duet,
                                        allow_stitch=args.allow_stitch, commercial=args.commercial, aigc=args.aigc)
            set_post_video(db, args.post_id, record)
            options = record["platform_options"]
            print(f"Attached video to draft {args.post_id}: {record['width']}x{record['height']} "
                  f"{record['duration_seconds']}s {record['video_codec']} {record['bytes']} bytes "
                  f"sha256 {record['sha256'][:12]}; mode {options['mode']}"
                  + (f" privacy {options['privacy_level']}" if options["mode"] == "direct_post" else
                     " (the creator finishes, captions and posts it in the TikTok app; nothing becomes public)")
                  + ". Not uploaded; review with 'show', then approve binds it")
        elif args.command == "video-detach":
            set_post_video(db, args.post_id, None)
            print(f"Removed the video from draft {args.post_id}; the local file is untouched")
        elif args.command == "youtube-attach":
            post = get_post(db, args.post_id)
            if post["platform"] != "youtube":
                raise ValidationError("youtube-attach only applies to YouTube posts")
            from .youtube import build_video_spec, shorts_hint
            private = _private_link_refusal(args, [args.title, *args.tag])  # #9: title and tags are published
            if private:
                raise ValidationError(f"Video refused, nothing changed: {private}")
            record, info = build_video_spec(args.file, title=args.title, tags=args.tag,
                                            privacy_status=args.privacy, made_for_kids=args.made_for_kids == "yes",
                                            category_id=args.category_id)
            set_post_video(db, args.post_id, record)
            print(json.dumps({"post_id": args.post_id, "video": _video_view(get_post(db, args.post_id)),
                              "duration_seconds": info.get("duration_seconds"), "width": info.get("width"),
                              "height": info.get("height"), "shorts": shorts_hint(info),
                              "note": "Draft only. Review with 'show', then approve; the approval binds these "
                                      "bytes and fields. Nothing was uploaded."}, indent=2, ensure_ascii=False))
        elif args.command == "youtube-detach":
            set_post_video(db, args.post_id, None)
            print(f"Post {args.post_id}: video removed (draft)")
        elif args.command in ("unapprove","cancel"):
            new_status = transition(db,args.post_id,args.command)
            print(json.dumps({"post_id": args.post_id, "status": new_status}) if args.json
                  else f"Post {args.post_id} -> {new_status}")
            # Security decision item 12: the withdrawal could not be written to the approval ledger.
            from .storage import LEDGER_UNRECORDED
            last = db.execute("SELECT detail FROM event_log WHERE post_id=? AND action=? ORDER BY id DESC LIMIT 1",
                              (args.post_id, args.command)).fetchone()
            if last is not None and LEDGER_UNRECORDED in str(last[0] or "").split():
                print(f"WARNING: the withdrawal of post {args.post_id} is not recorded in the approval ledger "
                      "(approval_ledger_unavailable). The post is withdrawn in this database, but a copy restored "
                      "from an older backup could still carry the old approval. Restore approval-ledger.jsonl "
                      "next to the database (reachweaver health shows its state)", file=sys.stderr)
        elif args.command == "schedule":
            when = parse_utc(args.at)
            platform = get_post(db, args.post_id)["platform"]
            if platform in OWNER_TRIGGERED_ONLY:
                raise ValidationError(f"{platform} cannot be scheduled: {OWNER_TRIGGERED_REASON}. Nothing changed")
            new_status = transition(db,args.post_id,'schedule',when)
            print(json.dumps({"post_id": args.post_id, "status": new_status, "scheduled_at": when}) if args.json
                  else f"Post {args.post_id} -> {new_status} for {when}")
        elif args.command == "approval-mode":
            if args.json:  # display only: a setting change keeps its text-only path, untouched
                if args.confirmation or args.require_key or args.require_identity or args.max_age_hours is not None:
                    raise ValidationError("approval-mode --json only displays the current mode; run a change "
                                          "without --json. Nothing changed")
                print(json.dumps(_approval_mode_document(db), indent=2))
                return 0
            if args.confirmation == confirmation.TTY_HASH_PREFIX:
                confirmation.enable(db)
            elif args.confirmation == "off":
                confirmation.disable(db)
            if args.require_key:
                mode = approval_key_mode(db)
                if mode.key is None:
                    raise ValidationError(f"--require-key needs a usable {APPROVAL_KEY_CREDENTIAL} in this process"
                                          + (f" ({mode.key_error})" if mode.key_error else "") + "; nothing changed")
                latch_keyed_mode(db)
            if args.require_identity:
                latch_identity_mode(db)
            if args.max_age_hours is not None:
                set_approval_max_age_hours(db, args.max_age_hours)
            mode = approval_key_mode(db)
            print(f"Identity-bound approval (wave 9 item 2): {'required' if identity_mode_required(db) else 'off'}")
            print(f"Human confirmation (#7 D6): {confirmation.confirmation_mode(db) or 'off'}")
            ttl = approval_max_age_hours(db)
            print("Approval time-to-live (#39): " + ("off" if ttl is None else
                  "invalid stored value, every approval counts as expired" if ttl == 0 else f"{ttl} hours"))
            print(f"Keyed approval binding (#7 D7): {'required' if mode.required else 'off'}; "
                  f"latched={'yes' if mode.latched else 'no'}; {APPROVAL_KEY_CREDENTIAL} "
                  f"{'available' if mode.key is not None else (mode.key_error or 'not provided')}")
            rebind = approval_rebind_required(db)
            if rebind:
                print(f"{len(rebind)} approved/scheduled post(s) need attention before any send "
                      "(see: reachweaver health)")
        elif args.command == "pause":
            set_paused(db,True)
            print("Emergency pause ON. All live publishing is blocked")
        elif args.command == "resume":
            set_paused(db,False)
            print("Emergency pause OFF. Approved due posts may be sent with --live")
        elif args.command == "calendar-plan":
            accounts = _accounts(args)
            account = accounts.get(args.account)
            if account is None:
                raise ValidationError("Unknown calendar account; configure the owner account and run doctor")
            if args.apply and args.include_drafts:
                raise ValidationError("--apply cannot be combined with --include-drafts; approve exact content first")
            audience = None
            if args.audience_activity is not None:
                today = datetime.now(timezone.utc).date()
                audience = derive_audience_slots(
                    load_audience_activity(args.audience_activity, today=today), args.account,
                    target_timezone=args.timezone, today=today,
                    top_hours=AUDIENCE_TOP_HOURS if args.audience_top_hours is None else args.audience_top_hours,
                    max_age_days=AUDIENCE_MAX_AGE_DAYS if args.audience_max_age_days is None else args.audience_max_age_days,
                )
            elif args.audience_top_hours is not None or args.audience_max_age_days is not None:
                raise ValidationError("--audience-top-hours/--audience-max-age-days need --audience-activity FILE")
            proposal = propose_calendar(
                db, account_id=args.account, platform=account["platform"],
                audience_timezone=args.timezone, start=args.start, days=args.days,
                spacing_hours=args.spacing_hours, post_ids=args.post_id,
                include_drafts=args.include_drafts, slots=args.slot,
                blackout=args.blackout, limit=args.limit, audience_activity=audience,
                explore_share=args.explore_share, explore_slots=args.explore_slot,
            )
            if args.apply:
                proposal = apply_calendar(db, proposal)
            print(json.dumps(proposal, indent=2))
        elif args.command == "publish":
            if args.json:  # dry run only; the live path is never wrapped or changed
                if args.live:
                    raise ValidationError("publish --json is for the dry run only; --live keeps its text output. "
                                          "Nothing was sent")
                from .publish_preview import capture_dry_run
                document = capture_dry_run(lambda: _run_publish(db, args))
                print(json.dumps(document, indent=2, ensure_ascii=False))
                return document["exit_code"]
            return _run_publish(db, args)
        elif args.command == "reconcile":
            reconcile(db,args.post_id,args.published,args.remote_id,args.remote_url,force=args.force)
            print("Reconciled on user's attestation; check the external platform carefully")
        elif args.command == "metrics":
            observed_at = parse_utc(args.observed_at)
            values = {key:getattr(args,key) for key in METRIC_FIELDS}
            store_metrics(db,args.post_id,observed_at,args.source,
                          window_start=args.window_start,window_end=args.window_end,**values)
            print("Metrics snapshot recorded. Missing values remain unknown, not zero")
        elif args.command == "import-metrics":
            with args.csv.open(newline="",encoding="utf-8-sig") as file:
                reader = csv.DictReader(file)
                required = {"post_id","observed_at","source"}
                if not required.issubset(set(reader.fieldnames or [])):
                    raise ValidationError("CSV must contain post_id, observed_at, source columns")
                count = 0
                # All rows or none (#15): a bad cell must not leave a half-imported export behind.
                db.execute("BEGIN IMMEDIATE")
                try:
                    for row in reader:
                        values = {k: row.get(k) for k in METRIC_FIELDS}  # tokens; never coerced to 0
                        try:
                            store_metrics(db,row["post_id"],parse_utc(row["observed_at"]),row["source"],
                                          window_start=row.get("window_start") or None,
                                          window_end=row.get("window_end") or None,**values)
                        except ValidationError as exc:
                            raise ValidationError(f"CSV line {reader.line_num}: {exc}. Nothing imported") from None
                        count += 1
                    db.execute("COMMIT")
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
            if args.json:
                print(json.dumps({"imported": count, "note": "Platform data was not fetched. Non-numeric statuses "
                                                              "are kept as statuses, never as 0"}))
            else:
                print(f"Imported {count} snapshots; platform data not fetched automatically. "
                      "Non-numeric statuses are kept as statuses, never as 0")
        elif args.command == "metrics-fetch":
            if not args.online and not args.dry_run:
                raise ValidationError("metrics-fetch requires explicit --online (or --dry-run for a no-network plan)")
            fetched = fetch_metrics(db, _accounts(args), account_id=args.account, post_id=args.post_id,
                                    max_posts=args.max_posts, dry_run=args.dry_run)
            print(json.dumps(fetched, indent=2, ensure_ascii=False))
            return 1 if fetched["stopped"] or fetched["failed"] else 0
        elif args.command == "queue-health":
            print(json.dumps(queue_health(db, stale_minutes=args.stale_minutes), indent=2))
        elif args.command == "health":
            try:
                accounts, accounts_error = _accounts(args), None
            except ValidationError as exc:
                accounts, accounts_error = {}, str(exc)
            try:
                (caps, caps_path), caps_error = load_caps(), None
            except ValidationError as exc:
                (caps, caps_path), caps_error = (None, None), str(exc)
            health_report = build_health(db, accounts, accounts_error=accounts_error,
                                         stale_minutes=args.stale_minutes, caps=caps,
                                         caps_path=str(caps_path) if caps_path else None,
                                         caps_error=caps_error)
            from . import health_view, term
            if args.json:
                print(json.dumps(health_report, indent=2))
            elif term.enabled(sys.stdout):
                print(health_view.render_health_sheet(health_report, sys.stdout))
            else:
                print(render_health(health_report))
            return health_exit_code(health_report)
        elif args.command == "dead-letter":
            letters = dead_letter(db)
            print(json.dumps(letters, indent=2) if args.json else render_dead_letter(letters))
        elif args.command == "data-export":
            text = json.dumps(export_data(db), indent=2, ensure_ascii=False) + "\n"
            if args.out:
                write_private_file(args.out, text)
                print(f"Exported local data to {args.out} (mode 0600). Keep it private; no network used.")
            else:
                print(text, end="")
        elif args.command == "schema-export":
            if args.out is not None and (args.out.exists() or args.out.is_symlink()):
                raise ValidationError(f"{args.out} already exists; schema-export never overwrites a file")
            try:
                export_accounts, accounts_error = _accounts(args), None
            except ValidationError as exc:
                export_accounts, accounts_error = None, f"accounts config not loaded: {exc}"
            exported = export_documents(db, export_accounts, accounts_error=accounts_error,
                                        types=[args.export_type] if args.export_type else None)
            export_summary = json.dumps({key: exported[key] for key in ("schema_version", "counts", "unmapped", "note")},
                                        indent=2, ensure_ascii=False)
            if args.out is not None:
                write_private_file(args.out, render_jsonl(exported["documents"]))
                print(export_summary)
                print(f"Wrote {len(exported['documents'])} documents to {args.out} (mode 0600). "
                      "Database unchanged; nothing was sent.", file=sys.stderr)
            else:
                print(render_jsonl(exported["documents"]), end="")
                print(export_summary, file=sys.stderr)
        elif args.command == "data-purge":
            plan = plan_purge(db, load_policy(args.policy)[0])
            print(json.dumps(apply_purge(db, plan) if args.apply else plan, indent=2))
        elif args.command == "privacy-status":
            privacy_report = privacy_status(db, policy_path=args.policy)
            privacy_report["live_safety"] = {"emergency_paused": is_paused(db),
                                             "live_opt_in_env": live_opt_in(),
                                             "runtime_environment": runtime_environment(),
                                             "publish_default_caps": {"max_per_24h": 2, "min_gap_minutes": 60}}
            print(json.dumps(privacy_report, indent=2))
        elif args.command == "report":
            data = report(db)
            envelope = {"generated_at":utc_now(),"platforms":data,
                        "note":"Coverage varies. Counts are latest-per-post snapshots, not unique people or comparable cross-platform views."}
            replies = reply_report(db)
            if replies:  # issue #17: replies are counted apart from the post totals; key only when present
                envelope["replies"] = replies
            print(render_report(envelope, args.report_format), end="")
        return 0


def _json_error(parsed, exc: BaseException | None, message: str) -> None:
    """With --json, also print {"error": {"code", "message"}} on stdout; stderr keeps its ERROR: line."""
    from .error_codes import error_code, json_error, wants_json
    if wants_json(parsed):
        print(json_error(error_code(exc) if exc is not None else "invalid_input", message))


def main(argv=None) -> int:
    argv_list = sys.argv[1:] if argv is None else list(argv)
    if argv_list[:1] == ["statusline"]:  # status bar fast path: skip building ~100 subparsers
        from .statusline import main as statusline_main
        return statusline_main(argv_list[1:])
    parsed = None
    try:
        parsed = make_parser().parse_args(argv)
        home.activate(parsed.home if parsed.command != "workspace" or parsed.init is None else None, Path.cwd())
        if parsed.command is None:  # bare `reachweaver`: where you are and what to type next, not a usage error
            from .start import build_guide, render as render_guide
            guide = build_guide(parsed.db, parsed.accounts)
            print(render_guide(guide))
            print("\nEvery command, grouped by task: reachweaver --help")
            return 1 if guide["state"] in ("accounts_invalid", "database_unreadable") else 0
        status = execute(parsed)
        sys.stdout.flush()  # a reader that left early (`| head`) surfaces here, inside the handlers below
        return status
    except sqlite3.Error as exc:
        # Last resort only: the send loop handles its own post-send database errors (_bookkeep) first.
        detail = str(exc).casefold()
        if "locked" in detail or "busy" in detail:
            print("ERROR: the state database is locked (busy). Check for another running reachweaver process "
                  "(a publish worker or the dashboard), then try again", file=sys.stderr)
        else:
            print(f"ERROR: the state database is unreadable or corrupt ({type(exc).__name__}). Check the --db path "
                  "and for another running reachweaver process; restore from a backup if the file is damaged",
                  file=sys.stderr)
        if write_started():
            print("A live send was in progress: reconcile manually before trying again.", file=sys.stderr)
        return 2
    except RecursionError:
        print("ERROR: an input file is nested too deeply to read; nothing was changed", file=sys.stderr)
        return 2
    except (ValidationError, PublishError, OSError, ValueError, OverflowError, csv.Error) as exc:
        if isinstance(exc, BrokenPipeError) and _stdout_closed():  # `reachweaver ... | head`: the reader left
            return 141
        from .privacy import redact  # last line of defence: a library message may quote a secret
        print(f"ERROR: {redact(str(exc))}",file=sys.stderr)
        _json_error(parsed, exc, redact(str(exc)))
        from . import term
        if term.interactive(sys.stderr):  # a person at a terminal gets the next step; scripts see only ERROR:
            from .cli_help import error_hint
            hint = error_hint(str(exc))
            if hint:
                print(hint, file=sys.stderr)
        return 2
    except (TypeError, KeyError, AttributeError):
        # Safety net: user-supplied JSON of an unexpected shape (e.g. a list where an object
        # is expected). Never echo the exception text, which may quote input values.
        print("ERROR: invalid input structure",file=sys.stderr)
        _json_error(parsed, None, "invalid input structure")
        return 2
    except KeyboardInterrupt:
        print("Interrupted. If a live send was in progress, reconcile manually before trying again.",file=sys.stderr)
        return 130


def _stdout_closed() -> bool:
    """True when stdout's reader has gone away; stdout then points at devnull so the exit flush stays quiet.

    The caller exits 141 (128 + SIGPIPE), what a shell reports for a tool stopped by a closed pipe: not 0
    (the output was cut short) and not 1 (this CLI's "findings"). See "Note on SIGPIPE" in the Python
    ``signal`` docs. A BrokenPipeError from anything else, such as a network socket, stays an ordinary error.
    """
    try:
        sys.stdout.flush()
        fd = sys.stdout.fileno()
    except BrokenPipeError:
        fd = None
    except (OSError, ValueError, AttributeError):  # flushed fine, but no file descriptor: not a pipe
        return False
    if fd is not None:
        import select
        if not hasattr(select, "poll"):  # Windows: no way to tell, keep the ordinary error
            return False
        poller = select.poll()
        poller.register(fd, select.POLLOUT)
        if not any(events & (select.POLLERR | select.POLLHUP) for _, events in poller.poll(0)):
            return False  # stdout is fine: the broken pipe was something else
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):  # a replaced stdout without a real file descriptor
        pass
    return True


if __name__ == "__main__":
    sys.exit(main())
