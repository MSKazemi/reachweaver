"""Local-first CLI with zero implicit external side effects."""
from __future__ import annotations

import argparse
import csv
import json
import os
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
from .connections import check_connection, mastodon_instance_limits
from .identity import IDENTITY_REQUIRED, current_fingerprint, refusal_text, verify_identity_online
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
from .briefs import build_brief, load_scan, write_brief
from .campaigns import OBJECTIVES, read_json, stage_campaign, validate_campaign
from .claims import DEFAULT_DUPLICATE_THRESHOLD, check_claims
from . import confirmation
from .distribution import GOALS, build_distribution_plan
from .engagement import triage_files
from .health import build_health, dead_letter, exit_code as health_exit_code, render_dead_letter, render_health
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
from .pilot import DEFAULT_MIN_WEEKS, load_pilot_log, pilot_report, render_report as render_pilot_report
from .timing_analysis import analyze_posting_times
from .worker_log import claim_refusal_reason, error_code as worker_error_code, open_worker_log
from .schema_export import EXPORT_TYPES, export_documents, render_jsonl
from .schemas import TYPES as SCHEMA_TYPES, validate as validate_schema
from .sources import load_config as load_sources_config, render_scan, scan_config_file
from .policy import (CONSERVATIVE_TEXT_LIMITS, LINKEDIN_ESCAPED_MAX, PLATFORMS, SUPPORTED_LIVE,
                     SUPPORTED_LIVE_VIDEO, ValidationError, live_opt_in, mastodon_text_limit,
                     require_live_gate, runtime_environment, validate_post)
from .attachments import MEDIA_PLATFORMS, UNSUPPORTED_PLATFORM
from .tiktok import COMMERCIAL_CHOICES as TIKTOK_COMMERCIAL
from .tiktok import MODES as TIKTOK_MODES
from .tiktok import PRIVACY_LEVELS as TIKTOK_PRIVACY_LEVELS
from .publishers import MEDIA_ONLY_PLATFORMS, PublishError, publish, publish_video, reset_write_marker, write_started
from .video import mode as video_mode
from .ratelimit import (
    AMBIGUOUS, SESSIONS_PER_PUBLISH, classify_send_failure, cooldown_scopes, is_rate_limited,
    preflight_retries, preflight_with_backoff, record_rate_limit, recorded_session_usage,
    reserve_bluesky_sessions, session_budget_problem,
)
from .report_format import REPORT_FORMATS, render_report
from .richtext import bluesky_preview, deterministic_tid, mastodon_length
from . import disconnect as account_disconnect
from .storage import (
    APPROVAL_KEY_CREDENTIAL, APPROVAL_KEY_UNAVAILABLE, BINDING_MEDIA_CHANGED, BINDING_VIDEO_CHANGED, METRIC_FIELDS, account_send_hold, active_provider_cooldown, hold_refusal,
    add_post, approval_binding_problem, identity_mode_required, latch_identity_mode, approval_key_mode, approval_payload_sha256, latch_keyed_mode, offline_key_mode,
    binding_refusal, check_approval_binding, claim_sending, connect, cooldown_refusal, finish_send, get_post,
    approval_max_age_hours, approval_rebind_required, is_paused, parse_utc, pending, post_media, post_video, set_approval_max_age_hours,
    provider_cooldowns, published_text, queue_health, reconcile, record_binding_failure, reply_report, reply_target_text, report, set_paused,
    set_post_media, set_post_video, store_metrics, sync_cooldown_scopes, transition, uncertain_send, utc_now,
)


def _add_history_args(p: argparse.ArgumentParser) -> None:
    """Opt-in, read-only repetition checks against a local DB (history.py, issue #10 S6)."""
    p.add_argument("--history-db", type=Path, metavar="PATH",
                   help="Also compare variants with this EXISTING local database's posts of the same account "
                        "(read-only; never created or modified). Warnings only. The global --db is not used here")
    p.add_argument("--history-days", type=int, metavar="N",
                   help="History look-back for history_near_duplicate, 1..365 (default 30)")
    p.add_argument("--history-window-days", type=int, metavar="W",
                   help="Rolling window for the history_frequency heuristic, 1..365 (default 7)")
    p.add_argument("--history-max-posts", type=int, metavar="K",
                   help="history_frequency warns at K existing posts per window, 1..1000 (default 7; "
                        "a ReachWeaver heuristic, not a platform limit)")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reachweaver", description="Approval-first social publishing; dry-run by default")
    parser.add_argument("--db", help="Local persistent SQLite database path (defaults to state/reachweaver.sqlite)")
    parser.add_argument("--accounts", help="Accounts config path (defaults to config/accounts.json)")
    sub = parser.add_subparsers(dest="command", required=True)
    optout_help = "Opt-out list JSON (default $REACHWEAVER_OPTOUT or config/optout.json if present)"
    sub.add_parser("init", help="Initialize local database (not an online account)")
    p = sub.add_parser("version", help="Print the installed ReachWeaver version and install kind; offline")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("update", help="Check GitHub Releases for a newer ReachWeaver skill bundle (--check, default), "
                       "print a one-line cached --notice, or --apply it; HTTPS GET to github.com only")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Ask GitHub for the latest release now (default)")
    mode.add_argument("--notice", action="store_true",
                      help="At most one request per 24 h; silent on failure or when up to date; for session start")
    mode.add_argument("--apply", action="store_true",
                      help="Download, verify SHA-256 and replace this installed skill; needs --yes")
    p.add_argument("--yes", action="store_true", help="Confirm --apply (the user must have agreed to the update)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    sub.add_parser("doctor", help="Inspect account configuration and missing credentials without calling APIs")
    p = sub.add_parser("connect-check", help="Explicit online, non-publishing account identity check")
    p.add_argument("--account", required=True, help="One account id from local ignored config")
    p.add_argument("--online", action="store_true", help="Required: allow authentication/read-only remote API requests")
    p = sub.add_parser("disconnect", help="Stop publishing to one account on this installation (#38): tighten-only, local; "
                       "lists the credential variable names and the steps to revoke at the provider; revokes nothing itself")
    p.add_argument("--account", required=True, help="One account id from local ignored config")
    p = sub.add_parser("reconnect", help="Lift a local disconnect (#38). Human-only: needs an interactive terminal and a "
                       "fresh challenge typed on /dev/tty")
    p.add_argument("--account", required=True, help="The disconnected account id")
    p = sub.add_parser("postiz-check", help="Explicit online, READ-ONLY Postiz integration listing/receipt lookup; never posts")
    p.add_argument("--account", required=True, help="One account with \"backend\": \"postiz\" in local ignored config")
    p.add_argument("--online", action="store_true", help="Required: allow read-only GET requests to the Postiz API")
    p.add_argument("--post-id", help="Also look up this local post's Postiz job state (backend-reported; never reconciles)")
    p = sub.add_parser("credentials-audit", help="Offline credential hygiene: env variable NAMES only, never values; exit 1 on findings")
    p.add_argument("--history", action="append", default=[], type=Path, metavar="FILE",
                   help="Also scan this shell history file for literal REACHWEAVER_* secrets (line numbers only); repeatable")
    p.add_argument("--json", action="store_true", help="Machine-readable names-only report")
    sub.add_parser("list", help="List posts and statuses").add_argument("--status", help="Filter by status")
    sub.add_parser("show", help="Show exact post text and account metadata for human review; offline").add_argument("post_id")
    p = sub.add_parser("draft", help="Queue a platform-native text draft for an account")
    p.add_argument("--account", required=True)
    body = p.add_mutually_exclusive_group(required=True)
    body.add_argument("--text")
    body.add_argument("--file", type=Path)
    p.add_argument("--campaign", default="")
    p.add_argument("--source-url", default="")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention refuses the draft (and, with "
                                                             "--reply-to, a listed parent author)")
    p.add_argument("--reply-to", metavar="ITEM",
                   help="Issue #17 (owner-approved 2026-10-09; disabled until a live receipt): make this a reply to an engagement item id "
                        "(bluesky:at://... or mastodon:HOST:ID from engagement-fetch/triage) under one of "
                        "your own published posts; needs --online to verify the parent; sending stays "
                        "blocked by the capability registry")
    p.add_argument("--online", action="store_true",
                   help="With --reply-to: allow the unauthenticated read-only GETs that verify the parent "
                        "(without it the planned requests are printed and nothing is created)")
    p.add_argument("--override-flagged", metavar="LABEL",
                   help="With --reply-to: explicit human override for a parent that triage flags as "
                        "suspicious, harassment, spam or moderation_labelled (must name that exact label); "
                        "opt-out signals are never overridable")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when the text (a reply's too) or --source-url links into any visibility:private repository of that config (private repository URLs are never public links)")
    p = sub.add_parser("check", help="Validate a draft before approval")
    p.add_argument("post_id")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention is BLOCKED")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when any published text (post, alt texts, video title/tags), a hosted image URL or the source URL links into any visibility:private repository of that config (private repository URLs are never public links)")
    p = sub.add_parser("approve", help="Approve the exact current text; binds an immutable payload hash (#7)")
    p.add_argument("post_id")
    p.add_argument("--approver", help="Who approved (attribution, not authentication); defaults to $USER/$LOGNAME")
    p.add_argument("--verify-identity", action="store_true",
                   help="Wave 9 item 2: run the read-only connect-check identity check once and bind a "
                        "fingerprint of the verified account (no token or response stored); needs --online. "
                        "A failed check approves nothing. Binds WHICH account, not WHO approved")
    p.add_argument("--online", action="store_true", help="Required with --verify-identity: allow that one read-only identity request")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention refuses approval")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when any published text (post, alt texts, video title/tags), a hosted image URL or the source URL links into any visibility:private repository of that config (private repository URLs are never public links)")
    p = sub.add_parser("youtube-attach", help="#22 (owner-approved; disabled until a live receipt): attach one local MP4 + upload metadata to a YouTube "
                                              "draft; offline, hashes the file; live upload stays registry-blocked")
    p.add_argument("post_id")
    p.add_argument("--file", type=Path, required=True, help="Local MP4 file (symlinks refused); its SHA-256 is bound")
    p.add_argument("--title", required=True, help="Video title, at most 100 characters, no '<' or '>'")
    p.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private",
                   help="privacyStatus to request (default private; the API default would be public)")
    p.add_argument("--made-for-kids", choices=("yes", "no"), required=True,
                   help="Required human answer for selfDeclaredMadeForKids (YouTube requires every creator to set it)")
    p.add_argument("--category-id", required=True, help="Numeric YouTube category id (videoCategories.list)")
    p.add_argument("--tag", action="append", default=[], help="One tag; repeat (500 characters in total)")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when the title or a tag links into any visibility:private repository of that config (private repository URLs are never public links)")
    sub.add_parser("youtube-detach", help="#22: remove the video from a YouTube draft; offline").add_argument("post_id")
    for command in ("unapprove", "cancel"):
        sub.add_parser(command).add_argument("post_id")
    p = sub.add_parser("schedule")
    p.add_argument("post_id")
    p.add_argument("--at", required=True, help="Explicit ISO 8601 timezone offset, e.g. 2026-10-15T09:00:00+02:00")
    p = sub.add_parser("media-attach", help="Add one image with required alt text to a DRAFT: a local PNG/JPEG/WebP "
                                            "file for Mastodon (--file, offline) or an owner-hosted HTTPS JPEG URL for "
                                            "Instagram (--url with --online: one read-only GET to hash it). Its SHA-256 "
                                            "(and the URL) are bound at approval (#13, #20); never uploads")
    p.add_argument("post_id")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Mastodon: local image file (not a symlink); checked from its header")
    source.add_argument("--url", help="Instagram: public HTTPS image URL on a host you control (no query string); "
                                      "Meta fetches this URL itself when the post is sent")
    alt = p.add_mutually_exclusive_group(required=True)
    alt.add_argument("--alt", help="Alt text describing the image (required; Mastodon description or Instagram alt_text)")
    alt.add_argument("--alt-file", type=Path, help="Read the alt text from this UTF-8 file")
    p.add_argument("--focus", help="Mastodon only: optional focal point x,y, each -1.0..1.0 (0,0 = centre)")
    p.add_argument("--online", action="store_true", help="Required with --url: allow one read-only GET of the image URL")
    p.add_argument("--rights-confirmed", action="store_true",
                   help="#48: you confirm you hold the rights, consent and likeness permission for this image; "
                        "required with any other rights option")
    p.add_argument("--license", dest="license_id", metavar="ID",
                   help="#48: SPDX id (for example CC-BY-4.0), 'owner' or 'licensed'; stored with the image and bound at approval")
    p.add_argument("--created-by", choices=sorted(CREATORS), help="#48: who made the image (provenance)")
    p.add_argument("--tool", help="#48: with --created-by tool: the generation or editing tool")
    p.add_argument("--attribution", help="#48: attribution text (required for third_party and CC-BY images)")
    p.add_argument("--rights-source-url", metavar="URL", help="#48: public HTTPS page the image came from (provenance)")
    p.add_argument("--alt-translation", action="append", default=[], metavar="LANG=TEXT",
                   help="#48: your own alt text in another language (BCP-47 tag), stored and bound but not sent; repeatable")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention in the alt text refuses the attachment")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when the alt text or the image URL links into any visibility:private repository of that config (private repository URLs are never public links)")
    sub.add_parser("media-detach", help="Offline: remove every image attachment from a DRAFT").add_argument("post_id")
    p = sub.add_parser("video-attach", help="Offline: bind one local MP4 to a TikTok DRAFT (issue #23; owner-approved, "
                                            "disabled until a live receipt); approval binds its SHA-256 and the "
                                            "delivery options; a post has images or one video, never both; never uploads")
    p.add_argument("post_id")
    p.add_argument("--file", required=True, type=Path, help="Local MP4 (not a symlink), at most 256 MiB")
    p.add_argument("--mode", choices=TIKTOK_MODES, default="creator_inbox",
                   help="creator_inbox (default): a draft the creator finishes in the TikTok app, never public by "
                        "itself; direct_post: publishes (second registry switch, off)")
    p.add_argument("--privacy-level", choices=TIKTOK_PRIVACY_LEVELS,
                   help="direct_post only, required, no default; must be offered by the creator's creator_info")
    p.add_argument("--commercial", choices=TIKTOK_COMMERCIAL, help="direct_post only, required: commercial disclosure")
    for name in ("comment", "duet", "stitch"):
        p.add_argument(f"--allow-{name}", action="store_true", help=f"direct_post only: turn {name}s on (default off)")
    p.add_argument("--aigc", action="store_true", help="direct_post only: label the video as AI-generated")
    sub.add_parser("video-detach", help="Offline: remove the video from a TikTok DRAFT; the file is untouched"
                   ).add_argument("post_id")
    p = sub.add_parser("calendar-plan", help="Propose audience-local times; optional --apply queues approved posts locally only")
    p.add_argument("--account", required=True, help="One configured social account ID")
    p.add_argument("--timezone", required=True, help="Audience IANA zone, e.g. Europe/Rome; not necessarily operator zone")
    p.add_argument("--start", required=True, help="First eligible local day YYYY-MM-DD")
    p.add_argument("--days", type=int, default=14, help="Window length 1..60 days (default 14)")
    p.add_argument("--spacing-hours", type=int, default=20, help="Minimum gap per account 1..168 hours")
    p.add_argument("--post-id", action="append", help="Select a post explicitly; repeat; default is all approved posts")
    p.add_argument("--limit", type=int, default=30, help="Maximum selected posts, at most 100")
    p.add_argument("--slot", action="append", help="Audience-local preferred slot Wed@16:00; repeat (required for some platforms)")
    p.add_argument("--blackout", action="append", help="Exclude day YYYY-MM-DD; repeat")
    p.add_argument("--include-drafts", action="store_true", help="Preview draft and approved posts; cannot be applied")
    p.add_argument("--apply", action="store_true", help="Explicitly queue already approved items in local SQLite; never publish")
    p.add_argument("--audience-activity", type=Path, metavar="FILE",
                   help="Owner-exported hour-of-week activity CSV (ReachWeaver interchange format, "
                        "examples/audience-activity.example.csv); top hours become slots with provenance")
    p.add_argument("--audience-top-hours", type=int, metavar="N", help="With --audience-activity: top activity hours used, 1..24 (default 5)")
    p.add_argument("--audience-max-age-days", type=int, metavar="N",
                   help="With --audience-activity: warn when as_of is older than N days (default 90)")
    p.add_argument("--explore-share", type=float, default=0.0, metavar="FRACTION",
                   help="Send this share of the posts (above 0, at most 0.5; default 0 = off) to --explore-slot "
                        "times, labelled role=explore; the rest stay on the exploit slots. Not randomized")
    p.add_argument("--explore-slot", action="append", metavar="Wed@11:00",
                   help="With --explore-share: an audience-local time to explore, distinct from the exploit slots; repeat")
    p = sub.add_parser("calendar-gaps", help="Read-only: empty and overloaded slots per account from the local queue; "
                       "never approves, schedules or publishes")
    p.add_argument("--account", required=True, help="One configured social account ID")
    p.add_argument("--timezone", required=True, help="Audience IANA zone, e.g. Europe/Rome")
    p.add_argument("--start", help="First local day YYYY-MM-DD (default: today in the audience zone)")
    p.add_argument("--days", type=int, default=14, help="Window length 1..60 days (default 14)")
    p.add_argument("--slot", action="append", help="Audience-local preferred slot Wed@16:00; repeat (as calendar-plan)")
    p.add_argument("--blackout", action="append", help="Exclude day YYYY-MM-DD; repeat")
    p.add_argument("--audience-activity", type=Path, metavar="FILE", help="Owner-exported activity CSV; as calendar-plan")
    p.add_argument("--audience-top-hours", type=int, metavar="N", help="With --audience-activity: top hours used, 1..24")
    p.add_argument("--audience-max-age-days", type=int, metavar="N", help="With --audience-activity: stale after N days")
    p.add_argument("--tolerance-minutes", type=int, default=90, help="A queued post within this many minutes fills a slot (1..720, default 90)")
    p.add_argument("--max-per-day", type=int, default=2, help="More queued posts than this on one local day is overloaded (1..20, default 2)")
    p.add_argument("--min-spacing-hours", type=int, default=20, help="Queued posts closer than this are flagged (1..168, default 20)")
    p.add_argument("--timing-source", help="Annotate empty slots with timing-evaluate evidence from this exact imported source")
    p.add_argument("--timing-metric", choices=METRIC_FIELDS, help="With --timing-source: goal numerator, e.g. clicks")
    p.add_argument("--timing-denominator", choices=("views", "impressions"), help="With --timing-source")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("link-health", help="Read-only: broken, redirected or preview-less links in stored posts; "
                       "offline unless --online (robots.txt first, one GET per page, never posts)")
    p.add_argument("--status", action="append", metavar="STATUS",
                   help="Post status to scan; repeat (default: approved, scheduled, published)")
    p.add_argument("--account", help="Only posts of this account id")
    p.add_argument("--campaign", help="Only posts of this campaign")
    p.add_argument("--online", action="store_true", help="Allow one GET per distinct landing page (robots.txt first)")
    p.add_argument("--allow-origin", action="append", metavar="ORIGIN",
                   help="With --online: also follow redirects to this https origin; repeat")
    p.add_argument("--max-links", type=int, default=25, help="Distinct pages fetched per run, 1..50 (default 25)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("publish", help="Preview due work; --live + opt-in env flag required to send")
    p.add_argument("--live", action="store_true", help="Opt in to actual network publishing")
    p.add_argument("--limit", type=int, default=10, help="Maximum eligible posts (default 10, max 100)")
    p.add_argument("--post-id", help="Send or preview only this explicitly approved, due post ID (recommended for pilots)")
    p.add_argument("--max-per-24h", type=int, default=2,
                   help="Live per-account rolling 24h send-attempt cap 1..10 (default 2)")
    p.add_argument("--min-gap-minutes", type=int, default=60,
                   help="Live per-account minimum minutes between attempts 15..1440 (default 60)")
    p.add_argument("--scheduled-only", action="store_true",
                   help="Worker-safe mode: skip approved unscheduled posts; only send due scheduled items")
    p.add_argument("--max-lateness-minutes", type=int, default=180,
                   help="With --scheduled-only, skip posts whose due time is >N min late (5..10080, default 180)")
    p.add_argument("--optout", type=Path, help=optout_help + "; loaded once per run, a mention is BLOCKED "
                                                             "before any claim and the post stays approved")
    p.add_argument("--log-json", action="store_true",
                   help="Also emit one redacted JSON line per worker decision to stderr (dry run and live); "
                        "observational only, never post text, handles, secrets or URL query strings")
    p.add_argument("--log-file", type=Path, metavar="PATH",
                   help="With --log-json: append the JSON lines to PATH instead (created 0600, owner-only); "
                        "an unwritable PATH refuses the run before any account or claim")
    p = sub.add_parser("reconcile", help="Human-confirm outcome of an uncertain send; do not blindly retry")
    p.add_argument("post_id")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--published", action="store_true")
    group.add_argument("--not-published", action="store_true")
    p.add_argument("--remote-id", default="")
    p.add_argument("--remote-url", default="")
    p.add_argument("--force", action="store_true",
                   help="Reconcile a post still in 'sending' that was claimed less than 15 minutes ago "
                        "(a send may be in flight). Only after checking the native account yourself")
    sub.add_parser("pause", help="Emergency global stop for all live publishing")
    p = sub.add_parser("approval-mode",
                       help="Show or tighten approval safeguards: human confirmation (#7 D6) and keyed binding (D7); both opt-in")
    p.add_argument("--confirmation", choices=(confirmation.TTY_HASH_PREFIX, "off"),
                   help="tty_hash_prefix: approve and publish --live need a terminal and the typed hash prefix; "
                        "off needs the same ritual while it is on")
    p.add_argument("--require-key", action="store_true",
                   help="Latch keyed approval mode (needs a usable REACHWEAVER_APPROVAL_SECRET here); "
                        "no command removes the latch")
    p.add_argument("--require-identity", action="store_true",
                   help="Latch identity-bound approval (wave 9 item 2): approve needs --verify-identity --online and "
                        "every send needs a stored identity; tighten-only, no command removes the latch")
    p.add_argument("--max-age-hours", type=int, metavar="N",
                   help="Approval time-to-live (#39): an approval older than N hours (1..8760) is expired and the post "
                        "must be re-approved before any send; tighten-only, only a lower value is accepted later")
    sub.add_parser("resume", help="Explicitly lift the emergency stop")
    p = sub.add_parser("metrics", help="Record one snapshot from a native platform export")
    p.add_argument("post_id")
    p.add_argument("--observed-at", required=True, help="ISO 8601 timestamp WITH timezone")
    p.add_argument("--source", required=True)
    for key in METRIC_FIELDS:
        p.add_argument("--" + key, metavar="VALUE",
                       help="Count, or unknown / suppressed / not_available / delayed / estimated:N")
    p.add_argument("--window-start", help="Optional start of the period the counts cover (ISO 8601 with timezone)")
    p.add_argument("--window-end", help="Optional end of that period; give both or neither (neither = unknown)")
    p = sub.add_parser("import-metrics", help="Import native-analytics snapshots normalized as CSV (all rows or none)")
    p.add_argument("--csv", required=True, type=Path,
                   help="Columns post_id, observed_at, source, metric columns; optional window_start, window_end. "
                        "A metric cell is a count, empty/unknown, suppressed, not_available, delayed or estimated:N")
    p = sub.add_parser("metrics-fetch", help="Explicit online, READ-ONLY poll of public Bluesky/Mastodon post "
                       "counters into metric snapshots; not private analytics, never posts")
    p.add_argument("--online", action="store_true", help="Required (unless --dry-run): allow read-only GET requests")
    p.add_argument("--account", help="Only this bluesky/mastodon account id")
    p.add_argument("--post-id", help="Only this local published post")
    p.add_argument("--max-posts", type=int, default=DEFAULT_MAX_POSTS,
                   help=f"Posts fetched per run (1..{MAX_POSTS_LIMIT}, default {DEFAULT_MAX_POSTS})")
    p.add_argument("--dry-run", action="store_true", help="Print the planned GET requests; no network, no DB write")
    p = sub.add_parser("engagement-fetch", help="READ-ONLY fetch of public replies (and, with --quotes, Bluesky quote "
                       "posts) to your published Bluesky/Mastodon posts into an engagement-triage export; plan "
                       "only unless --online; never replies")
    p.add_argument("--online", action="store_true", help="Allow unauthenticated read-only GET requests (needs --out)")
    p.add_argument("--out", type=Path, help="NEW owner-only (0600) engagement export JSON; never overwritten")
    p.add_argument("--account", help="Only this bluesky/mastodon account id")
    p.add_argument("--post-id", help="Only this local published post")
    p.add_argument("--max-posts", type=int, default=DEFAULT_MAX_POSTS,
                   help=f"Posts fetched per run (1..{MAX_POSTS_LIMIT}, default {DEFAULT_MAX_POSTS})")
    p.add_argument("--depth", type=int, default=6, help="Reply levels below each post (1..20, default 6)")
    p.add_argument("--max-replies", type=int, default=100, help="Replies kept per post (1..500, default 100)")
    p.add_argument("--quotes", action="store_true",
                   help="Also read Bluesky quote posts of each post (app.bsky.feed.getQuotes, public AppView, "
                        "unauthenticated, cursor pages); kind quote_post; network only with --online")
    p.add_argument("--max-quotes", type=int, default=100,
                   help="Quote posts kept per Bluesky post with --quotes (1..500, default 100, at most 5 pages)")
    p = sub.add_parser("report", help="Coverage-aware local analytics; no fabricated reach or results")
    p.add_argument("--format", choices=REPORT_FORMATS, default="json", dest="report_format",
                   help="json (default, unchanged envelope), markdown, or csv (one row per platform and metric; "
                        "spreadsheet-formula cells neutralised)")
    p = sub.add_parser("youtube-analytics", help="#22: offline sampling report over stored metric snapshots of "
                       "published YouTube videos (exact/sampled/unknown labels); read-only, fetches nothing")
    p.add_argument("--account", help="Only this YouTube account id")
    p.add_argument("--post-id", help="Only this local published YouTube post")
    p = sub.add_parser("queue-health", help="Offline status, attempts, overdue and uncertain sends, provider "
                                            "cooldowns; never auto-retry")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p = sub.add_parser("health", help="Offline readiness per configured account: adapters, credentials (names), "
                                      "budgets, provider cooldowns, queue, pause; exit 1 when blocked; never "
                                      "contacts providers")
    p.add_argument("--json", action="store_true", help="Full machine-readable report, including the dead-letter view")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p = sub.add_parser("dead-letter", help="Read-only needs_review posts grouped by sanitized error code + reconcile steps; never retries")
    p.add_argument("--json", action="store_true", help="Machine-readable grouping")
    p = sub.add_parser("ops-metrics", help="Read-only operational counts (claims, failures by send class, cooldowns, "
                                           "pause, dead letters, stale sending, due age) as JSON or Prometheus text")
    p.add_argument("--format", choices=("json", "prometheus"), default="json", help="Output format (default json)")
    p.add_argument("--since", help="Count audit events from this ISO 8601 time or a relative 30m/24h/7d "
                                   "(default: all time, as Prometheus counters)")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p.add_argument("--optout", type=Path, help=optout_help + "; queued posts mentioning it are counted, never named")
    p.add_argument("--out", type=Path, help="Atomically write this file (mode 0644), e.g. a node_exporter "
                                            "textfile-collector *.prom path; an existing file is replaced only if "
                                            "named *.prom or *.json, never the database or a symlink")
    p.add_argument("--now", help="Evaluate at this ISO 8601 time instead of the clock (reproducible output)")
    p = sub.add_parser("ops-summary", help="One redacted read-only report: overall readiness, per-account adapter, "
                                           "budget and queue, dead letters and ops-metrics totals; exit 1 when "
                                           "blocked; never contacts providers")
    p.add_argument("--json", action="store_true", help="Machine-readable report (default: short text)")
    p.add_argument("--since", help="Count audit events from this ISO 8601 time or a relative 30m/24h/7d")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p.add_argument("--optout", type=Path, help=optout_help + "; queued posts mentioning it are counted, never named")
    p.add_argument("--now", help="Evaluate at this ISO 8601 time instead of the clock (reproducible output)")
    p = sub.add_parser("campaign-check", help="Offline evidence/brand/variant checks; does not assert source truth")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--brand", type=Path, help="Brand file; omitting skips account/voice checking")
    p.add_argument("--json", action="store_true", help="Machine-readable diagnostics")
    p.add_argument("--optout", type=Path, help=optout_help)
    _add_history_args(p)
    p = sub.add_parser("campaign-preview", help="Offline per-channel previews: exact text, platform length, facets, "
                       "capability, evidence, rule-based confidence; never stages or publishes")
    p.add_argument("--file", type=Path, required=True, help="Campaign JSON (read only; never modified)")
    p.add_argument("--brand", type=Path, required=True, help="Brand JSON, including optional per-account rules")
    p.add_argument("--accounts", dest="preview_accounts", type=Path,
                   help="Optional PUBLIC accounts config (e.g. Mastodon max_characters); same as global --accounts")
    p.add_argument("--utm", action="store_true", help="Show each link with utm_source/medium/campaign and utm_content=variant id")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention is an error")
    _add_history_args(p)
    shape = p.add_mutually_exclusive_group()
    shape.add_argument("--json", action="store_true", help="Machine-readable preview")
    shape.add_argument("--markdown", action="store_true", help="Editable Markdown review sheet")
    p = sub.add_parser("campaign-stage", help="Idempotently stage verified text variants as drafts; never publishes")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--brand", type=Path, required=True, help="Required explicit brand-account allowlist")
    p.add_argument("--optout", type=Path, help=optout_help)
    p = sub.add_parser("campaign-export", help="Export validated draft pack for manual publishing; no network")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--brand", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True, help="New output folder; refuses existing output")
    p.add_argument("--optout", type=Path, help=optout_help)
    p = sub.add_parser("suggest", help="Read-only ranked human review of profile, posts, SEO, engagement, experiments and local queue")
    p.add_argument("--snapshot", type=Path, help="Owner-supplied profile/post snapshot (local JSON, no remote access)")
    p.add_argument("--metrics-csv", type=Path, help="Optional normalized owner-authorized native post CSV, merged only in memory")
    p.add_argument("--owner-authorized", action="store_true", help="Explicitly confirm the metrics CSV was lawfully exported from your own account")
    p.add_argument("--engagement-export", type=Path, help="Existing owner-exported engagement JSON; only aggregate labels are returned")
    p.add_argument("--seo-briefs", type=Path, help="Local SEO export already used by seo-destinations")
    p.add_argument("--experiments", type=Path, help="Local classified experiment results JSON list")
    p.add_argument("--include-db", action="store_true", help="Explicitly read an EXISTING local SQLite DB in query-only mode")
    p.add_argument("--limit", type=int, default=10, help="Show 1..50 ranked review tasks (default 10)")
    p.add_argument("--json", action="store_true", help="Machine-readable curated findings; no raw comments or profile text")
    p = sub.add_parser("weekly-digest", help="Write suggest (and with --include-db the local report) as one private Markdown file; "
                       "local only, never sent")
    p.add_argument("--out", type=Path, required=True, help="NEW Markdown file (mode 0600); refuses an existing file or symlink")
    p.add_argument("--snapshot", type=Path, help="Owner-supplied profile/post snapshot (local JSON, no remote access)")
    p.add_argument("--metrics-csv", type=Path, help="Optional normalized owner-authorized native post CSV, merged only in memory")
    p.add_argument("--owner-authorized", action="store_true", help="Confirm the metrics CSV was lawfully exported from your own account")
    p.add_argument("--engagement-export", type=Path, help="Existing owner-exported engagement JSON; only aggregate labels are used")
    p.add_argument("--seo-briefs", type=Path, help="Local SEO export already used by seo-destinations")
    p.add_argument("--experiments", type=Path, help="Local classified experiment results JSON list")
    p.add_argument("--include-db", action="store_true", help="Also read an EXISTING local SQLite DB in query-only mode (queue health and report)")
    p.add_argument("--limit", type=int, default=10, help="Show 1..50 ranked review tasks (default 10)")
    p = sub.add_parser("profile-audit", help="Offline owner-snapshot, per-platform profile readiness and fix list; no network")
    p.add_argument("--file", type=Path, required=True, help="Local JSON snapshot, or a profile .csv (platform,account_id,handle,profile_kind,keywords,field,value); no account login or network")
    p.add_argument("--platform", help="Only this platform from the profile-audit registry")
    p.add_argument("--json", action="store_true", help="Detailed machine-readable checklist with evidence and coverage")
    p = sub.add_parser("post-audit", help="Offline platform-specific creative checks and comparable-age outcome summaries")
    p.add_argument("--metrics-csv", type=Path, help="Optional normalized owner-exported post observations; no DB/network writes")
    p.add_argument("--owner-authorized", action="store_true", help="Confirm the CSV is your own authorized export")
    p.add_argument("--file", type=Path, required=True, help="Same owner-supplied profile/post snapshot")
    p.add_argument("--platform", help="Limit to one registered platform")
    p.add_argument("--age-hours", type=int, default=168, help="Native metric post age 24..720 hours (default 168)")
    p.add_argument("--tolerance-hours", type=int, default=24, help="Observation age tolerance 1..168 hours")
    p.add_argument("--min-posts", type=int, default=3, help="Minimum matched-age posts per format cohort 3..30")
    p.add_argument("--json", action="store_true", help="Detailed no-write JSON results")
    p = sub.add_parser("peer-benchmark", help="Offline: compare your public counters with peers' public counters that you copied by hand into a local file (#11); no fetching, no logins, nothing stored")
    p.add_argument("--file", type=Path, required=True, help="Local JSON (schema reachweaver.peer-benchmark.v1); see examples/peer-benchmark.example.json")
    p.add_argument("--platform", help="Only this platform id")
    p.add_argument("--include-individuals", action="store_true", help="Also accept kind: person entries (excluded by default; privacy check first)")
    p.add_argument("--now", help="Reference time for the staleness warning (ISO 8601 with offset); default: the current time")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("distribution-plan", help="Offline regional channel research and hypothetical timing windows; NEVER schedules/posts")
    p.add_argument("--region", required=True, help="Target country/market, e.g. india, china, nigeria or global")
    p.add_argument("--goal", choices=GOALS, required=True, help="One measurable audience objective")
    p.add_argument("--limit", type=int, default=5, help="Number of candidate channels (1..20)")
    p.add_argument("--date", help="YYYY-MM-DD audience-local day for illustrative time tests, not an actual queue")
    p.add_argument("--timezone", help="IANA timezone override, required with --date for global audiences")
    p.add_argument("--json", action="store_true", help="Return machine-readable RESEARCH-ONLY plan")
    p = sub.add_parser("timing-evaluate", help="Read-only per-account matched-age outcome analysis; NOT optimal-time proof")
    p.add_argument("--account", required=True, help="Configured account ID; exact cohort only")
    p.add_argument("--timezone", required=True, help="Audience IANA timezone for posting time groups")
    p.add_argument("--source", required=True, help="Exact source of manually imported native snapshots")
    p.add_argument("--metric", choices=METRIC_FIELDS, required=True, help="Goal numerator, e.g. clicks")
    p.add_argument("--denominator", choices=("views", "impressions"), required=True)
    p.add_argument("--age-hours", type=int, default=168, help="Compare post ages (24..720 hours)")
    p.add_argument("--tolerance-hours", type=int, default=24, help="Observation tolerance, 1..168 hours")
    p.add_argument("--min-posts-per-slot", type=int, default=3, help="Min comparable posts per time group")
    p.add_argument("--campaign", help="Optional narrow cohort; important for topic/format comparability")
    p.add_argument("--since", help="Optional YYYY-MM-DD audience-local first date")
    p.add_argument("--until", help="Optional YYYY-MM-DD audience-local final date")
    p.add_argument("--stratify", choices=("format", "campaign"),
                   help="Also report each format (text/image/video) or campaign separately; a stratum with "
                        "too little data names no winner and points to calendar-plan's generic baseline")
    p = sub.add_parser("baseline-eval", help="Read-only: personalized slots/variant campaigns/readouts vs "
                       "calendar-plan's generic baseline over --since/--until; bootstrap CI + Holm; non-causal")
    p.add_argument("--account", required=True, help="Configured account ID; exact cohort only")
    p.add_argument("--timezone", required=True, help="Audience IANA timezone for slots and the period")
    p.add_argument("--source", help="Exact source of imported native snapshots (required without --attribution)")
    p.add_argument("--metric", choices=METRIC_FIELDS, help="Goal numerator, e.g. clicks (required without --attribution)")
    p.add_argument("--denominator", choices=("views", "impressions"), help="Required without --attribution")
    p.add_argument("--attribution", type=Path, metavar="ATTR.json",
                   help="Saved 'attribution-report --json' output: use each post's attributed count (--outcome) "
                        "instead of --source/--metric/--denominator; a post without one is excluded, never 0")
    p.add_argument("--outcome", choices=("docs_visits", "repo_stars_utm"),
                   help="With --attribution: docs_visits = attributed sessions, repo_stars_utm = attributed conversions")
    p.add_argument("--since", required=True, help="First audience-local send date YYYY-MM-DD (inclusive)")
    p.add_argument("--until", required=True, help="Last audience-local send date YYYY-MM-DD (inclusive)")
    p.add_argument("--slot", action="append", help="Personalized arm [explore:|exploit:]Wed@16:00; repeat, one comparison each")
    p.add_argument("--campaign-arm", action="append", help="Variant campaign arm [explore:|exploit:]CAMPAIGN; repeat")
    p.add_argument("--readout", action="append", type=Path, help="Saved 'experiment-readout --json' file; repeat")
    p.add_argument("--age-hours", type=int, default=168, help="Matched post age (24..720 hours)")
    p.add_argument("--tolerance-hours", type=int, default=24, help="Snapshot age tolerance, 1..168 hours")
    p.add_argument("--slot-tolerance-minutes", type=int, default=60, help="Send-time match around a slot, 0..120")
    p.add_argument("--min-posts", type=int, default=5, help="Minimum posts with a rate per group, 3..100")
    p.add_argument("--resamples", type=int, default=2000, help="Bootstrap/permutation resamples, 200..20000")
    p.add_argument("--confidence", type=float, default=0.95, help="Interval level; alpha = 1 - confidence")
    p.add_argument("--seed", type=int, default=16, help="Fixed seed: same inputs give identical output")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("experiment-evaluate", help="Matched-age, non-causal readout of preregistered post pairs")
    p.add_argument("--file", type=Path, required=True)
    p = sub.add_parser("experiment-preregister",
                       help="Offline: validate a growth plan and write a NEW hashed preregistration; never publishes")
    p.add_argument("plan", type=Path, help="Growth plan JSON (examples/growth-plan.example.json)")
    p.add_argument("--out", type=Path, required=True, help="NEW 0600 record file; an existing file is never overwritten")
    p = sub.add_parser("experiment-verify", help="Offline: detect post-hoc edits by recomputing a plan's sha256 hash")
    p.add_argument("plan", type=Path, help="Original growth plan JSON, or the preregistration record")
    p.add_argument("--hash", required=True, dest="expected_hash", help="Registered plan_hash, sha256:<hex>")
    p = sub.add_parser("experiment-readout", help="Read-only: verify a preregistration, map arms to posts, label the "
                                                  "matched-age readout; non-causal")
    p.add_argument("--prereg", type=Path, required=True, help="Record written by experiment-preregister")
    p.add_argument("--posts", type=Path, required=True, help="reachweaver.growth-post-map JSON: arm id -> post id per pair")
    p.add_argument("--hash", dest="expected_hash", help="Optional externally kept plan_hash that must also match")
    p.add_argument("--attribution", type=Path, metavar="ATTR.json",
                   help="Saved 'attribution-report --json' output; required for a docs_visits or repo_stars_utm "
                        "primary metric (per-post attributed sessions/conversions; unknown stays unknown, never 0)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("experiment-next", help="Offline: deterministic next dimension from prior classified results")
    p.add_argument("--results", type=Path, required=True, help="JSON list of {experiment_id, dimension, label, direction}")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("experiment-simulate", help="Offline SIMULATED label distribution for a seeded synthetic experiment")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--pairs", type=int, required=True, help="Pairs per synthetic experiment (2..100)")
    p.add_argument("--lift", type=float, required=True, help="True relative lift of the treatment, e.g. 0 or 0.2")
    p.add_argument("--noise", type=float, required=True, help="Per-post log-normal noise sd, e.g. 0.3")
    p.add_argument("--trials", type=int, default=1000, help="Synthetic experiments to run (1..20000)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("pilot-report",
                       help="Offline per-arm readout of an owner field-pilot CSV log; descriptive, never causal")
    p.add_argument("--log", type=Path, required=True, help="Operations log CSV (examples/pilot-log.template.csv)")
    p.add_argument("--min-weeks", type=int, default=DEFAULT_MIN_WEEKS,
                   help=f"Refuse the arm comparison unless every logged arm covers N ISO weeks (default {DEFAULT_MIN_WEEKS})")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    p = sub.add_parser("schema-validate", help="Offline canonical schema check (1.0, 1.1 or 1.2) of one JSON document")
    p.add_argument("--type", required=True, choices=SCHEMA_TYPES, dest="schema_type")
    p.add_argument("file", type=Path)
    p = sub.add_parser("schema-export", help="Read-only: local outbox rows as canonical schema JSON lines; "
                       "unmapped rows listed with reasons; never writes the DB or publishes")
    p.add_argument("--out", type=Path, help="NEW owner-only (0600) .jsonl file; never overwritten; default stdout")
    p.add_argument("--type", choices=EXPORT_TYPES, dest="export_type", help="Export only this document type")
    p = sub.add_parser("engagement-triage", help="Offline comment/mention triage + reply/issue DRAFTS; no send path")
    p.add_argument("input", type=Path, help="Owner-exported normalized engagement JSON")
    p.add_argument("--docs-map", type=Path, help="Owner docs map: topic keywords -> HTTPS docs URL")
    p.add_argument("--out", type=Path, help="New .json or .md file for the triage + drafts; never overwritten")
    p.add_argument("--issues-dir", type=Path, help="Write bug/feature GitHub issue DRAFTS here; never filed")
    p.add_argument("--json", action="store_true", help="Print the full machine-readable report")
    p.add_argument("--optout", type=Path, help=optout_help + "; listed authors get no draft")
    p = sub.add_parser("engagement-queue", help="Offline aging/SLA review queue over an engagement export; read-only, no send path")
    p.add_argument("input", type=Path, help="Owner-exported normalized engagement JSON")
    p.add_argument("--docs-map", type=Path, help="Owner docs map, as for engagement-triage")
    p.add_argument("--optout", type=Path, help=optout_help + "; listed authors are never queued")
    p.add_argument("--now", help="ISO 8601 time with timezone for the age calculation (default: current UTC time)")
    p.add_argument("--sla", action="append", metavar="LABEL=HOURS", help="Override one label's overdue target in hours; repeatable")
    p.add_argument("--json", action="store_true", help="Print the full machine-readable queue")
    p = sub.add_parser("data-export", help="Offline JSON export of your local DB with secret-looking text redacted")
    p.add_argument("--out", type=Path, help="NEW owner-only (0600) file; default prints to stdout")
    p = sub.add_parser("data-purge", help="Retention purge plan (dry run); --apply deletes/minimizes local rows only")
    p.add_argument("--policy", type=Path, help="Retention JSON (default $REACHWEAVER_RETENTION_POLICY or config/retention.json)")
    p.add_argument("--apply", action="store_true", help="Explicitly apply the plan; never touches active/in-flight posts")
    p = sub.add_parser("privacy-status", help="Offline DB permissions, size, row counts, oldest records, retention config")
    p.add_argument("--policy", type=Path, help="Retention JSON to validate (same defaults as data-purge)")
    p = sub.add_parser("utm-link", help="Offline canonical UTM-tagged destination URL; no network")
    p.add_argument("url")
    p.add_argument("--platform", required=True, help="utm_source; a ReachWeaver platform id")
    p.add_argument("--campaign", required=True, help="utm_campaign; slugged campaign id")
    p.add_argument("--content", help="utm_content; post id or campaign variant id")
    p.add_argument("--term", help="Optional utm_term")
    p.add_argument("--allow-origin", nargs="+", help="Reject destinations outside these https origins")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config; refuse a destination inside any visibility:private repository")
    p = sub.add_parser("attribution-report", help="Read-only UTM/referrer join of local analytics exports; NOT causal")
    p.add_argument("--analytics", type=Path, required=True, help="Website-analytics CSV export")
    p.add_argument("--github-traffic", type=Path, action="append",
                   help="Saved traffic/views or popular/referrers JSON; repeat for both")
    p.add_argument("--json", action="store_true", help="Full machine-readable report")
    p = sub.add_parser("seo-destinations", help="Offline canonical landing page + competing-message check")
    p.add_argument("--briefs", type=Path, required=True, help="Local SEO briefs JSON (docs/SEO-INTEGRATION.md)")
    p.add_argument("--type", choices=CAMPAIGN_TYPES, required=True)
    p.add_argument("--project", help="Restrict to briefs for this project")
    p.add_argument("--platform", help="With --campaign: also emit a UTM link for the destination")
    p.add_argument("--campaign", help="With --platform: utm_campaign for the destination link")
    p = sub.add_parser("seo-content-check", help="Discoverability checks on owner-supplied post texts (length, alt text, hashtags, "
                       "UTM links, name spelling, cross-platform duplicates); offline unless --online link previews; read-only, never publishes")
    p.add_argument("file", type=Path, help="Local JSON: {\"items\": [{id, platform, text, alt_text?, media_count?, link?, keywords?}], \"names\"?: [...]}")
    p.add_argument("--name", action="append", metavar="NAME", help="Canonical spelling of your project/brand name; variants are flagged; repeat")
    p.add_argument("--online", action="store_true", help="Also check each item's link preview (Open Graph) and liveness: robots.txt first, one GET per distinct page")
    p.add_argument("--allow-origin", action="append", metavar="ORIGIN", help="With --online: also follow redirects to this https origin; repeat")
    p.add_argument("--json", action="store_true", help="Full machine-readable report")
    p = sub.add_parser("media-check", help="Offline media manifest checks: headers, platform limits, alt text, captions, rights; never uploads")
    p.add_argument("manifest", type=Path, help="Media manifest JSON; asset paths are relative to it")
    p.add_argument("--json", action="store_true", help="Machine-readable errors/warnings report")
    p = sub.add_parser("media-storyboard", help="Offline: campaign brief -> vertical-video storyboard, SRT skeleton, "
                       "alt-text placeholders, image specs, media manifest, safe-area check; generates no media")
    p.add_argument("--campaign", type=Path, help="Campaign JSON (campaign contract or signal-brief scaffold)")
    p.add_argument("--out", type=Path, help="NEW output folder (0700, files 0600); refuses existing output")
    p.add_argument("--check", type=Path, metavar="STORYBOARD", help="Re-check an edited storyboard.json: safe areas, "
                   "subtitle timing and unresolved placeholders")
    p.add_argument("--duration", type=int, default=30, help="Video length in seconds, 10..180 (default 30)")
    p.add_argument("--aspect", default="9:16", help="Frame aspect W:H (default 9:16)")
    p.add_argument("--video-targets", nargs="+", default=["youtube_shorts", "tiktok", "instagram"])
    p.add_argument("--image-targets", nargs="+", default=["linkedin"])
    p.add_argument("--locale", default="en", help="BCP-47 subtitle/alt-text locale (default en)")
    p.add_argument("--fit", choices=("cover", "contain"), default="cover",
                   help="Assumed player fit for non-9:16 frames: centered crop (default) or letterbox")
    p.add_argument("--safe-zones", type=Path, help="Owner UI-overlay zones JSON overriding the built-ins (sourced from paid-ad specs, accessed 2026-10-09)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("manual-export", help="Offline creator-ready package for Threads/Instagram/Facebook Pages/YouTube/TikTok; never sends")
    p.add_argument("--platform", required=True, choices=MANUAL_PLATFORMS)
    p.add_argument("--variant", type=Path, required=True, help="Variant JSON: text, format, link_url, platform_options")
    p.add_argument("--media", type=Path, help="Media manifest JSON validated with media-check rules")
    p.add_argument("--account-meta", type=Path, help="PUBLIC account metadata JSON (handle, account_type); no secrets")
    p.add_argument("--out", type=Path, required=True, help="NEW output folder (0700, files 0600); refuses existing output")
    p.add_argument("--json", action="store_true", help="Print the full machine-readable package")
    p = sub.add_parser("claims-check", help="Offline LEXICAL claim grounding of variants vs declared evidence; not truth verification")
    p.add_argument("campaign", type=Path, help="Campaign JSON file")
    p.add_argument("--brand", type=Path, required=True, help="Brand JSON (banned_phrases, required_disclosure)")
    p.add_argument("--duplicate-threshold", type=float, default=DEFAULT_DUPLICATE_THRESHOLD,
                   help="Word 3-gram Jaccard at which variants are near-duplicates, (0,1] (default 0.6)")
    p.add_argument("--json", action="store_true", help="Machine-readable grounding report")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention is an error")
    p = sub.add_parser("sources-scan", help="Rank opt-in GitHub/feed/changelog/DOI source signals; local fixtures unless --online; never drafts/publishes")
    p.add_argument("--config", type=Path, required=True, help="sources.reachweaver.json opt-in source list")
    p.add_argument("--online", action="store_true",
                   help="Explicit GET of public GitHub API, https feeds/changelogs (robots.txt honoured) and Crossref; default reads local fixtures")
    p.add_argument("--state", type=Path, help="Optional JSON state of previously seen signal IDs (novelty)")
    p.add_argument("--mark-seen", action="store_true", help="Record shown candidate IDs in --state (local file only)")
    p.add_argument("--allow-private", action="store_true",
                   help="Third private opt-in: also read sources that set visibility AND sensitivity private "
                        "(GitHub, token required); their signals are marked private, never public evidence")
    p.add_argument("--top", type=int, default=10, help="Ranked signals to show (1..100, default 10)")
    p.add_argument("--json", action="store_true", help="Machine-readable RESEARCH-ONLY signals")
    p = sub.add_parser("signal-brief", help="Offline: one sources-scan signal -> NEW campaign scaffold + .brief.md; "
                       "placeholders block staging; no copy generated, never stages/publishes")
    p.add_argument("--scan", type=Path, required=True, help="Saved 'sources-scan --json' output (untrusted data)")
    p.add_argument("--signal-id", required=True, help="Signal id, or a unique prefix of at least 8 hex characters")
    p.add_argument("--brand", type=Path, required=True, help="Brand JSON (brand_id, approved_accounts)")
    p.add_argument("--out", type=Path, required=True,
                   help="NEW campaign .json (0600); a NEW sidecar .brief.md is written next to it; refuses overwrite")
    p.add_argument("--accounts", dest="brief_accounts", type=Path, metavar="A",
                   help="Optional PUBLIC accounts config; one placeholder variant per approved account with a "
                        "live or manual path (same as global --accounts)")
    p.add_argument("--objective", choices=sorted(OBJECTIVES), default="awareness",
                   help="Campaign objective (default awareness)")
    p.add_argument("--allow-duplicate", action="store_true",
                   help="Explicit human override to brief a near_duplicate or previously_seen signal")
    p.add_argument("--json", action="store_true", help="Machine-readable result (paths, validation, skipped accounts)")
    p = sub.add_parser("capabilities", help="Offline platform capability/API-approval registry; never grants permission")
    p.add_argument("--platform", help="Show one platform, e.g. x, reddit or pinterest")
    p.add_argument("--json", action="store_true", help="Machine-readable full registry entries")
    p.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                   help=f"Warn when verified_date is older than N days (default {DEFAULT_MAX_AGE_DAYS})")
    p = sub.add_parser("landing-check", help="SEO brief + campaign CTA landing-page checks; offline unless --online "
                       "(GET own pages, robots.txt honoured, redirects inspected); never publishes")
    p.add_argument("--briefs", type=Path, required=True, help="Local SEO briefs JSON (docs/SEO-INTEGRATION.md)")
    p.add_argument("--campaign", type=Path, help="Campaign JSON whose CTAs and grounded claim phrases are checked")
    p.add_argument("--online", action="store_true",
                   help="Explicit GET of each canonical page: status, redirects, rel=canonical, title, "
                        "Open Graph, noindex, lang, lexical claim phrases")
    p.add_argument("--allow-origin", nargs="+",
                   help="https origins a redirect may move to outside the brief's registrable domain")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    return parser


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


def _optional_account(args, account_id: str, platform: str) -> dict | None:
    """Offline review helper: account metadata if the local config loads, else None."""
    try:
        account = _accounts(args).get(account_id)
    except ValidationError:
        return None
    return account if account and account["platform"] == platform else None


def _offline_binding_problem(post, mode) -> str | None:
    """Binding check without the key (#7 D7): a keyed tag is verified only where the key is read."""
    problem = approval_binding_problem(post, mode)
    return None if problem == APPROVAL_KEY_UNAVAILABLE else problem


def _approval_view(post, mode=None) -> dict:
    """Approval binding state for ``show`` (#7): hashes and attribution, never a decision."""
    view: dict = {"binding": "not_approved"}
    if all(name in post.keys() for name in ("approved_payload_sha256", "approved_by", "approval_channel")):
        view.update(payload_sha256=post["approved_payload_sha256"], approved_by=post["approved_by"],
                    approval_channel=post["approval_channel"])
    if "approved_payload_mac" in post.keys():  # #7 D7: whether a keyed tag exists, never the tag
        view["keyed_tag"] = "present" if post["approved_payload_mac"] else None
    if post["status"] in ("approved", "scheduled"):
        problem = _offline_binding_problem(post, mode)
        view["binding"] = problem or "bound"
        if problem:
            view["reapprove"] = binding_refusal(problem, post["id"])
        ttl = getattr(mode, "max_age_hours", None)
        if ttl and post["approved_at"]:
            view["approval_expires_at"] = (datetime.fromisoformat(parse_utc(post["approved_at"]))
                                           + timedelta(hours=ttl)).isoformat(timespec="seconds")
    elif post["status"] == "draft":
        # What 'approve' would bind right now (approve always clears scheduled_at).
        view["approve_would_bind_sha256"] = approval_payload_sha256(post, scheduled_at=None, use_row_schedule=False)
        view["approve_adds"] = "a fresh per-approval nonce (approval/2) that this digest does not include"
    return view


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


def _media_view(post) -> dict | None:
    """Offline review of a post's attachments (#13, #20): records, local file check, live capability.

    A hosted image (#20) is not downloaded; its view says how to verify it yourself."""
    from .attachments import describe
    try:
        media = post_media(post)
    except ValidationError as exc:
        return {"error": str(exc), "send": "refused until the attachments are re-created"}
    if not media:
        return None
    try:
        require_capability(post["platform"], "image", "publish")
        capability = "enabled in the capability registry"
    except ValidationError as exc:
        capability = f"refused: {exc}"
    attachments = describe(media)
    view: dict = {"count": len(media), "attachments": attachments, "live_image_publish": capability}
    local = [item for item in attachments if item["source"] == "local"]
    if local:
        view["all_files_match_record"] = all(item["file_matches_record"] for item in local)
    if len(local) == len(attachments):
        view["bound_by_approval"] = "each attachment's sha256, mime, alt and focus, in this order"
    else:
        view["bound_by_approval"] = "each attachment's sha256, mime, alt, focus and url, in this order"
        view["sent_as"] = "image_url (Meta downloads it from your host), caption, alt_text"
    if any(item.get("rights") for item in attachments):  # #48
        view["bound_by_approval"] += ", plus each rights record (license, attribution, provenance, alt translations; approval/media-2)"
        view["rights_not_sent"] = "rights and alt translations are stored and bound but never published"
    return view


def _platform_preview(post, account: dict | None) -> dict | None:
    """Compact, offline description of what an adapter would send (no network)."""
    content = post["content"]
    if post["platform"] == "bluesky":
        preview = bluesky_preview(content)
        try:
            preview["record_key"] = deterministic_tid(post["id"], post["created_at"])
        except ValueError:
            preview["record_key"] = None
        preview["handle"] = account.get("handle") if account else None
        preview["service"] = bluesky_service(account) if account else None
        return preview
    if post["platform"] == "mastodon":
        if account is None:
            return {"account_config": "not loaded; visibility and instance limit unknown"}
        return {"server": account["server"], "handle": account.get("handle"),
                "visibility": account.get("visibility", "public"),
                "language": account.get("language"), "spoiler_text": account.get("spoiler_text"),
                "counted_length": mastodon_length(content), "text_limit": mastodon_text_limit(account),
                "idempotency_key": post["id"], "idempotency_window": "1 hour (server-side)"}
    if post["platform"] == "linkedin":  # private #14: account config only, never the token
        from .linkedin import VISIBILITY, author_urn, escape_commentary
        member_urn = None
        if account is not None and account.get("person_id"):
            try:
                member_urn = author_urn(account["person_id"])
            except ValidationError:
                member_urn = None
        counted, escaped = len(content), len(escape_commentary(content))
        preview = {"account_id": post["account_id"], "handle": account.get("handle") if account else None,
                   "member_urn": member_urn, "visibility": VISIBILITY,
                   "counted_length": counted, "text_limit": CONSERVATIVE_TEXT_LIMITS["linkedin"],
                   "length_method": "Unicode code points",
                   "escaped_commentary_length": escaped, "escaped_commentary_limit": LINKEDIN_ESCAPED_MAX,
                   "within_limits": counted <= CONSERVATIVE_TEXT_LIMITS["linkedin"] and escaped <= LINKEDIN_ESCAPED_MAX,
                   "dry_run": "show sends nothing; publish without --live is a dry run with no request; a live "
                              "send needs approval, REACHWEAVER_ENV=prod, REACHWEAVER_ALLOW_LIVE=1 and --live"}
        if account is None:
            preview["account_config"] = "not loaded; member URN unknown"
        return preview
    return None


def _video_issues(post) -> list[str]:
    """Blocking problems of a post's video (#22, #23), offline: malformed record, a record built for
    another platform, images and a video together, or a changed/unusable file."""
    from .video import BOTH_KINDS, file_issue
    try:
        record = post_video(post)
        if record is None:
            return []
        if "media_json" in post.keys() and post["media_json"]:
            return [f"video: {BOTH_KINDS}"]
        if record["platform"] != post["platform"]:
            return [f"video: record is for {record['platform']}, not this {post['platform']} post"]
        issue = file_issue(record)
    except ValidationError as exc:
        return [f"video: {exc}"]
    return [f"video: {issue}"] if issue else []


def _video_view(post) -> dict | None:
    """Offline review of a post's video (#22, #23): record, file check and the registry switches."""
    from .capabilities import VIDEO_MODES, require_video_mode
    try:
        record = post_video(post)
    except ValidationError as exc:
        return {"error": str(exc), "send": "refused until the video is re-attached"}
    if record is None:
        return None
    issues = _video_issues(post)
    live = {}
    for mode in VIDEO_MODES.get(record["platform"], {}):
        try:
            require_video_mode(record["platform"], mode)
            live[mode] = "enabled in the capability registry"
        except ValidationError as exc:
            live[mode] = f"refused: {exc}"
    view = {"record": record, "file_matches_record": not issues, "problems": issues, "live": live,
            "bound_by_approval": "the whole video record except its local path (file sha256, size, parsed "
                                 "headers, platform_options) and the text"}
    if record["platform"] == "youtube":  # #22 review fields: exactly what the upload sends
        from .video import file_issue
        options = record["platform_options"]
        view.update(title=options["title"], tags=options["tags"], category_id=options["category_id"],
                    privacy_status=options["privacy_status"], made_for_kids=options["self_declared_made_for_kids"],
                    file_status=file_issue(record) or "matches_recorded_sha256", live_upload=live.get("upload"))
    if record["platform"] == "tiktok":
        direct = video_mode(record) == "direct_post"
        view["caption_sent"] = ("yes, as post_info.title" if direct
                                else "no: in creator_inbox mode the creator writes the caption in the TikTok app")
        view["public_after_send"] = ("only for direct_post when TikTok returns a public post id; a creator_inbox "
                                     "upload is never recorded as published")
    return view


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
        blocked = unbound = cooling = sessions = replies_held = 0
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
            budget: str | None = None
            if post["platform"] == "bluesky" and not (reason or problem or cooldown or hold):
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
                         if cooldown else "session_budget" if budget else "capability_refused" if media_refused
                         else "dry_run")
        if replies_held:
            print(f"Replies: {replies_held} due reply post(s) would be HELD before any claim; they stay approved.")
        if cooling:
            print(f"Provider cooldown: {cooling} due post(s) would be SKIPPED; they stay approved/scheduled.")
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
                                 preflight_max_retries=rate["preflight_max_retries"])
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
        problem = check_approval_binding(db, post["id"], identity_fingerprint=account_fingerprint)
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
                paused=lambda: is_paused(db), retry_on=PublishError)
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


# Commands that only read the state database: opened with connect_readonly, so a missing
# database fails (exit 2) instead of being created, and an older schema is never migrated.
READ_ONLY_DB_COMMANDS = ("engagement-fetch", "ops-metrics", "youtube-analytics", "ops-summary", "calendar-gaps", "link-health")
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


def execute(args) -> int:
    if args.command == "schema-validate":
        try:
            document = json.loads(args.file.read_text(encoding="utf-8"))
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
        attribution = load_attribution_outcomes(args.attribution) if args.attribution is not None else None
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
        engagement = json.loads(_bounded(args.engagement_export).read_text(encoding="utf-8")) if args.engagement_export else None
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
        private_repos = private_repos_from_config(load_sources_config(args.sources)[0]) if args.sources else ()
        print(build_utm_link(args.url, platform=args.platform, campaign=args.campaign,
                             content=args.content, term=args.term, allow_origins=args.allow_origin,
                             private_repos=private_repos))
        return 0
    if args.command == "seo-destinations":
        result = campaign_destinations(load_seo_briefs(args.briefs), args.type, args.project,
                                       platform=args.platform, campaign=args.campaign)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
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
    if args.command == "media-storyboard":
        from .storyboard import run_command as run_storyboard
        return run_storyboard(args)
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
        except (HTTPError, URLError):
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
        accounts = _accounts(args)
        for name, account in accounts.items():
            status = "supported (text)" if account["platform"] in SUPPORTED_LIVE else "planned/manual only"
            if account.get("backend") is not None:
                status = f"backend={account['backend']} (opt-in; acceptance needs human reconcile)"
            missing = missing_credentials(account)
            print(f"{name}: {account['platform']}; {status}; env variables missing: {', '.join(missing) or 'none'}")
            if account["platform"] == "linkedin":  # offline API-version and token-expiry lifecycle
                from .linkedin import doctor_notes
                for note in doctor_notes(account):
                    print(f"  {name}: {note}")
            if account["platform"] == "instagram" and account.get("backend") is None:  # adapter, issue #20 (disabled until a live receipt)
                from .instagram import doctor_notes as instagram_doctor_notes
                for note in instagram_doctor_notes(account):
                    print(f"  {name}: {note}")
            if account["platform"] == "tiktok" and "open_id" in account:  # video adapter, issue #23
                from .tiktok import doctor_notes as tiktok_doctor_notes
                for note in tiktok_doctor_notes(account):
                    print(f"  {name}: {note}")
            if account["platform"] == "youtube" and account.get("backend") is None:  # video upload adapter, issue #22
                from .youtube import doctor_notes as youtube_doctor_notes
                for note in youtube_doctor_notes(account):
                    print(f"  {name}: {note}")
            if account["platform"] == "threads" and account.get("backend") is None:  # adapter, issue #19 (disabled until a live receipt)
                from .threads import doctor_notes as threads_doctor_notes
                for note in threads_doctor_notes(account):
                    print(f"  {name}: {note}")
            if account["platform"] == "facebook" and account.get("backend") is None:  # adapter, issue #21 (disabled until a live receipt)
                from .facebook import doctor_notes as facebook_doctor_notes
                for note in facebook_doctor_notes(account):
                    print(f"  {name}: {note}")
        print("Live opt-in:", "enabled" if live_opt_in() else "disabled")
        print(f"Environment: REACHWEAVER_ENV={runtime_environment()} (publish --live requires prod)")
        print(f"Credential source: {credential_source()} (names only; values are never shown)")
        print("Credential hygiene: run 'reachweaver credentials-audit [--history FILE]' (offline; names only, never values)")
        print("Metrics polling: 'metrics-fetch --online' reads PUBLIC Bluesky/Mastodon post counters only "
              "(read-only GET; not private analytics; views/impressions/clicks stay unknown)")
        return 0
    if args.command == "credentials-audit":
        audit = audit_environment(_accounts(args), os.environ, history=args.history)
        print(json.dumps(audit, indent=2) if args.json else render_audit(audit))
        return 1 if audit["findings"] else 0
    if args.command == "signal-brief":
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
    if args.command == "sources-scan":
        result = scan_config_file(args.config, online=args.online, state_path=args.state,
                                  top=args.top, mark_seen=args.mark_seen, allow_private=args.allow_private)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_scan(result))
        return 1 if result["errors"] else 0
    if args.command == "landing-check":
        from .landing import exit_code as landing_exit_code, landing_check, render_text as render_landing
        result = landing_check(args.briefs, args.campaign, online=args.online, allow_origins=args.allow_origin)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_landing(result))
        return landing_exit_code(result)
    if args.command in READ_ONLY_DB_COMMANDS:
        return _execute_read_only(args)
    with connect(args.db) as db:
        if args.command == "init":
            print("Initialized local database. No accounts were accessed.")
        elif args.command == "draft":
            accounts = _accounts(args)
            account = accounts.get(args.account)
            if not account:
                raise ValidationError("Unknown account. Run 'doctor' to list configured accounts")
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
            result = attribution_report(db, load_analytics_csv(args.analytics),
                                        [load_github_traffic(f) for f in args.github_traffic or []])
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
                print("None = unknown, not zero. Tagging join only; no causal claim.")
        elif args.command == "experiment-evaluate":
            result = evaluate_experiment(db,read_json(args.file))
            print(json.dumps(result,indent=2))
        elif args.command == "experiment-readout":
            attribution = load_attribution_outcomes(args.attribution) if args.attribution is not None else None
            result = run_readout(db, read_json(args.prereg), read_json(args.posts), expected_hash=args.expected_hash,
                                 attribution=attribution)
            print(json.dumps(result, indent=2) if args.json else render_readout(result))
        elif args.command == "list":
            query = "SELECT id,account_id,platform,status,scheduled_at,remote_url FROM posts"
            params: tuple = ()
            if args.status:
                query += " WHERE status=?"
                params = (args.status,)
            for row in db.execute(query + " ORDER BY created_at DESC",params):
                print(" | ".join(str(row[k] or "") for k in row.keys()))
        elif args.command == "show":
            post = get_post(db,args.post_id)
            fields=("id","account_id","platform","status","content","campaign","source_url","scheduled_at","approved_at")
            shown = {key:post[key] for key in fields}
            shown["approval"] = _approval_view(post, offline_key_mode(db))
            media_view = _media_view(post)
            if media_view is not None:
                shown["media"] = media_view
            video_view = _video_view(post)
            if video_view is not None:
                shown["video"] = video_view
            from .replies import reply_view, row_target
            try:
                shown_reply = row_target(post)
                if shown_reply is not None:
                    shown["reply"] = reply_view(shown_reply)
            except ValidationError as exc:
                shown["reply"] = {"kind": "reply", "invalid": str(exc)}
            platform_preview = _platform_preview(post, _optional_account(args, post["account_id"], post["platform"]))
            if platform_preview is not None:
                shown["platform_preview"] = platform_preview
            from .capabilities import api_cost_note
            shown["api_cost"] = api_cost_note(post["platform"])  # #39: estimated platform/API cost
            print(json.dumps(shown,indent=2,ensure_ascii=False))
        elif args.command == "check":
            post = get_post(db,args.post_id)
            account = _optional_account(args, post["account_id"], post["platform"])
            issues = validate_post(post["platform"],post["content"], account)
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
            print("OK" if not issues else "BLOCKED: " + "; ".join(issues))
            if post["platform"] not in SUPPORTED_LIVE:
                print("Note: this platform is draft/manual only")
            return 1 if issues else 0
        elif args.command == "approve":
            post = get_post(db,args.post_id)
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
            reply_problems = _reply_problems(post, _optional_account(args, post["account_id"], post["platform"]),
                                             optout)
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
                confirmation.show_payload(post, expected, heading="CONFIRM approval of")
                confirmed = confirmation.confirm_post(db, args.post_id, expected, action="approve")
            target = transition(db, args.post_id, "approve", approver=approver, approval_channel=channel,
                                expected_sha256=expected, confirmation=confirmed, identity=bound_identity)
            post = get_post(db, args.post_id)
            bound = post["approved_payload_sha256"]
            print(f"Post {args.post_id} -> {target} (payload sha256 {bound[:12]}, "
                  f"approver {approver or 'unattributed'} via {channel}"
                  + (", keyed hmac-sha256/1" if post["approved_payload_mac"] else "")
                  + (", identity bound (approval/id-1)" if bound_identity else "")
                  + (f", confirmed {confirmed}" if confirmed else "") + ")")
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
            print(f"Post {args.post_id} -> {transition(db,args.post_id,args.command)}")
        elif args.command == "schedule":
            when = parse_utc(args.at)
            print(f"Post {args.post_id} -> {transition(db,args.post_id,'schedule',when)} for {when}")
        elif args.command == "approval-mode":
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
            print(json.dumps(health_report, indent=2) if args.json else render_health(health_report))
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


def main(argv=None) -> int:
    try:
        return execute(make_parser().parse_args(argv))
    except (ValidationError, PublishError, OSError, ValueError, csv.Error) as exc:
        from .privacy import redact  # last line of defence: a library message may quote a secret
        print(f"ERROR: {redact(str(exc))}",file=sys.stderr)
        return 2
    except (TypeError, KeyError, AttributeError):
        # Safety net: user-supplied JSON of an unexpected shape (e.g. a list where an object
        # is expected). Never echo the exception text, which may quote input values.
        print("ERROR: invalid input structure",file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. If a live send was in progress, reconcile manually before trying again.",file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
