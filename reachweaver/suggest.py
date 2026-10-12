"""Deterministic, evidence-ranked *read-only* next actions across ReachWeaver.

This is an operations triage, NOT a hidden recommender/ranking algorithm, a
prediction of views or consent to post. All returned wording is curated; no
third-party comment, URL, profile bio, token or remote API response is repeated.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .policy import ValidationError
from .profile_audit import audit_posts, audit_profiles
from .storage import queue_health, report

PRIORITY = ("P0", "P1", "P2", "P3")
CONFIDENCE = ("verified_local_state", "owner_reported", "rule_based_classification",
              "observational_only", "not_observed")
MAX_ACTIONS = 50
ACTION_SCHEMA = "reachweaver.suggestions.v1"

# Static, non-LLM weights: policy urgency first; do not present as probability.
SEVERITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def build_suggestions(
    *, snapshot: dict | None = None, engagement: dict | None = None,
    seo_briefs: dict | None = None, experiments: list | None = None,
    con=None, limit: int = 10, now: str | None = None,
) -> dict:
    """Collect checks from existing modules; never load accounts or mutate state."""
    if type(limit) is not int or not 1 <= limit <= MAX_ACTIONS:
        raise ValidationError("suggest --limit must be 1..50")
    if all(x is None for x in (snapshot, engagement, seo_briefs, experiments, con)):
        raise ValidationError("suggest requires at least one --snapshot, --engagement-export, --seo-briefs, --experiments or --include-db input")
    actions: list[dict[str, Any]] = []
    sources: list[str] = []
    coverage: dict[str, Any] = {
        "profile": "not_supplied", "posts": "not_supplied",
        "queue": "not_supplied", "metrics": "not_supplied",
        "engagement": "not_supplied", "seo": "not_supplied", "experiments": "not_supplied",
    }

    def add(priority: str, kind: str, signal: str, title: str, next_step: str, *,
            source: str, confidence: str, evidence: dict,
            platform: str | None = None, account_id: str | None = None) -> None:
        if priority not in PRIORITY or confidence not in CONFIDENCE:
            raise ValidationError("Invalid recommendation classification")
        actions.append({
            "priority": priority, "kind": kind, "signal": signal,
            "title": title, "next_step": next_step,
            "platform": platform, "account_id": account_id,
            "confidence": confidence,
            "evidence": {"source": source, **evidence},
            "requires_human_review": True, "executes_changes": False,
        })

    if con is not None:
        sources.append("existing_local_database_readonly")
        q = queue_health(con, now=now)
        stats = report(con)
        coverage["queue"] = "locally_observed"
        coverage["metrics"] = {
            "platforms_with_published_posts": sum(1 for row in stats.values() if row["published"] > 0),
            "scope": "whole_local_database_platform_aggregates",
            "not_account_attribution": True,
        }
        for key, title, step, priority in (
            ("ambiguous_needs_review", "Reconcile uncertain remote outcomes",
             "Check each affected post in the native account; reconcile manually before any retry.", "P0"),
            ("stale_sending", "Investigate stalled send attempts",
             "Check the provider before touching any stuck sending entry; never blindly resend.", "P0"),
            ("approval_rebind_required", "Reapprove changed or untrusted post bindings",
             "Inspect exact content and destination, then explicitly reapprove before delivery.", "P1"),
            ("overdue_scheduled", "Review missed scheduled posts",
             "Review whether each outdated slot still makes sense; reschedule only with human approval.", "P1"),
        ):
            count = len(q.get(key, []))
            if count:
                add(priority, "publishing_safety", key, title, step,
                    source="sqlite_queue_health", confidence="verified_local_state",
                    evidence={"count": count, "detail": "post_ids_withheld"})
        if q["paused"]:
            add("P0", "publishing_safety", "emergency_pause_active",
                "Investigate the active emergency pause",
                "Keep publishing paused while investigating. Resume only after explicit operator review.",
                source="sqlite_queue_health", confidence="verified_local_state",
                evidence={"paused": True})
        for platform, info in sorted(stats.items()):
            published = info["published"]
            observed = info["metric_coverage"]
            if not published:
                continue
            if observed < published:
                add("P2", "measurement", "native_metric_coverage_incomplete",
                    "Capture missing first-party post observations",
                    "Use owner-authorized platform insights/native exports at matching post ages. Never fill missing metrics with zero.",
                    source="sqlite_report", confidence="verified_local_state",
                    evidence={"published_posts": published, "posts_with_any_snapshot": observed,
                              "missing_snapshots": published - observed, "scope": "per_platform_not_per_account"},
                    platform=platform)
            # Snapshot existence is not sufficient to imply compatible clicks/reach.
            kinds = info["coverage_by_metric"]
            if published >= 3 and all(kinds.get(k, 0) == 0 for k in ("views", "impressions")):
                add("P2", "measurement", "missing_native_exposure_denominator",
                    "Verify the platform's exposure metrics before comparing rates",
                    "Collect only officially available views/impressions or mark the denominator unavailable. Do not invent reach.",
                    source="sqlite_report", confidence="verified_local_state",
                    evidence={"published_posts": published, "views_exact_posts": kinds.get("views", 0),
                              "impressions_exact_posts": kinds.get("impressions", 0)},
                    platform=platform)

    if snapshot is not None:
        sources.append("owner_supplied_profile_and_post_snapshot")
        prof = audit_profiles(snapshot)
        posts = audit_posts(snapshot)
        coverage["profile"] = {
            "profiles": len(prof["reports"]),
            "complete_enough_to_score": sum(r["readiness_heuristic_score"] is not None for r in prof["reports"]),
            "field_values": "owner_reported_not_verified",
        }
        coverage["posts"] = {
            "posts": posts["post_count"],
            "matched_observations": sum(g["sample_size"] for g in posts["format_cohorts"]),
            "excluded": posts["excluded"],
            "native_metric_export_not_verified": True,
        }
        for p in prof["reports"]:
            platform, account_id = p["platform"], p["account_id"]
            if p["status"] == "insufficient_profile_data":
                add("P2", "profile_evidence", "profile_coverage_low",
                    "Inspect more of this network's profile fields",
                    "Verify the account's visible fields manually or with an authorized read-only export; unknown fields are not missing features.",
                    source="profile_audit", confidence="not_observed",
                    evidence={"observed_fields": p["observed_checks"], "required_fields": p["total_checks"],
                              "coverage": p["coverage"]},
                    platform=platform, account_id=account_id)
            # Limit per-account fixes to keep diversity; each fix is *actually reported absent*.
            for fix in p["fixes"][:3]:
                field = fix["field"]
                add(fix["priority"], "profile_fix", f"profile_{field}_needs_fix",
                    f"Improve the {field.replace('_', ' ')} on this profile",
                    f"{fix['action']}. Verify account eligibility and edit manually; don't infer an algorithm penalty.",
                    source="profile_audit", confidence="owner_reported",
                    evidence={"field": field, "status": "owner_reported_needs_fix",
                              "official_source": p["official_source"]},
                    platform=platform, account_id=account_id)
            for alert in p["alerts"]:
                if alert["kind"] in ("claim_review", "length_review"):
                    add("P1", "profile_claims", alert["kind"],
                        "Review profile wording or platform field limits",
                        "Verify supporting evidence and the current native editor before changing the claim or length.",
                        source="profile_audit", confidence="owner_reported",
                        evidence={"alert": alert["kind"], "source_tier": p["source_tier"]},
                        platform=platform, account_id=account_id)

        creative_counts: Counter[tuple[str, str, str]] = Counter()
        exclusion_counts: Counter[tuple[str, str, str]] = Counter()
        for post in posts["post_reports"]:
            pair = (post["platform"], post["account_id"])
            for item in post["suggestions"]:
                if item["signal"] not in ("format_hypothesis",):
                    creative_counts[(*pair, item["signal"])] += 1
            if post["missing_reason"]:
                exclusion_counts[(*pair, post["missing_reason"])] += 1
        for (platform, account_id, signal), count in sorted(creative_counts.items()):
            add("P2", "post_creative", f"creative_{signal}",
                "Improve a manually reviewed post component",
                f"Review {signal.replace('_', ' ')} for the affected original posts; test one change on a future, comparable cohort.",
                source="post_audit", confidence="owner_reported",
                evidence={"owner_flagged_posts": count, "issue": signal},
                platform=platform, account_id=account_id)
        for (platform, account_id, reason), count in sorted(exclusion_counts.items()):
            add("P2", "measurement", f"post_{reason}",
                "Collect comparable post analytics before optimizing",
                "Provide actual native timestamps, source and comparable metric definitions; avoid comparing mismatched post ages.",
                source="post_audit", confidence="not_observed",
                evidence={"excluded_posts": count, "missingness_code": reason},
                platform=platform, account_id=account_id)
        if posts["post_count"] == 0:
            add("P3", "measurement", "no_post_snapshots",
                "Build an initial performance baseline for owned posts",
                "Export a few recent native post observations with timestamps, format, source and available metrics.",
                source="post_audit", confidence="not_observed",
                evidence={"observed_posts": 0})

    if engagement is not None:
        from .engagement import triage_engagement
        triage = triage_engagement(engagement)
        sources.append("owner_exported_engagement")
        coverage["engagement"] = {"items": len(triage["items"]), "labels_are_heuristic": True}
        buckets: Counter[tuple[str, str, str]] = Counter()
        for item in triage["items"]:
            if item["author_is_owner"] or item["flags"]["opt_out"] or item["flags"]["optout_listed"]:
                continue
            # These are suggestions to human-review, not permission to contact.
            if item["label"] in ("bug_report", "technical_question", "feature_request",
                                 "collaboration_opportunity", "actionable_feedback"):
                buckets[(item["platform"], item["account_id"], item["label"])] += 1
        for (platform, account_id, label), count in sorted(buckets.items()):
            urgent = label in ("bug_report", "technical_question", "collaboration_opportunity")
            add("P1" if urgent else "P2", "engagement_review", f"review_{label}",
                f"Review relevant {label.replace('_', ' ')} messages",
                "Read original messages yourself, respect opt-outs/community rules, and respond manually only if appropriate.",
                source="offline_engagement_triage", confidence="rule_based_classification",
                evidence={"classified_items": count, "label": label, "comment_text_withheld": True},
                platform=platform, account_id=account_id)
        if triage["optout"]["requests_to_record"]:
            add("P0", "privacy", "opt_out_requests_pending",
                "Review requests not to be contacted",
                "Validate each request and record it in the private opt-out list; do not reach out to these authors.",
                source="offline_engagement_triage", confidence="rule_based_classification",
                evidence={"requests": len(triage["optout"]["requests_to_record"]),
                          "identities_withheld": True})

    if seo_briefs is not None:
        from .attribution import seo_conflicts
        conflicts = seo_conflicts(seo_briefs)
        sources.append("owner_supplied_seo_briefs")
        coverage["seo"] = {"briefs": len(seo_briefs["briefs"]), "conflicts": len(conflicts),
                           "online_indexing_not_checked": True}
        grouped = Counter(c["type"] for c in conflicts)
        for key, n in sorted(grouped.items()):
            add("P1" if key in ("duplicate_canonical_url_across_briefs", "same_canonical_url") else "P2",
                "seo", f"seo_{key}", "Review overlapping SEO destinations or keywords",
                "Inspect the affected canonical pages/campaigns in the private SEO report; resolve duplicates before promoting.",
                source="seo_briefs_conflicts", confidence="owner_reported",
                evidence={"conflict_type": key, "conflict_count": n,
                          "private_destination_values_withheld": True})
        if not conflicts:
            add("P3", "seo", "seo_export_no_internal_conflicts",
                "Verify live SEO destination health separately",
                "The supplied brief has no detected internal conflicts; check real indexing and conversions with consented tools.",
                source="seo_briefs_conflicts", confidence="observational_only",
                evidence={"briefs_checked": len(seo_briefs["briefs"]), "live_indexing_verified": False})

    if experiments is not None:
        from .growth import next_hypotheses
        nxt = next_hypotheses(experiments)
        sources.append("owner_supplied_experiment_results")
        coverage["experiments"] = {
            "results": len(nxt["results"]), "negative_or_inconclusive": len(nxt["negative_results"]),
            "not_a_causal_optimization": True,
        }
        candidate = nxt["next"]
        if candidate:
            add("P2" if candidate["rule"] == "finish_pending" else "P3",
                "experiment", f"experiment_{candidate['rule']}",
                "Plan the next controlled social content experiment",
                "Preregister a small, owner-approved test with a fixed metric, age and stopping rule; "
                "do not treat a single directional result as proof.",
                source="experiment_next", confidence="observational_only",
                evidence={"dimension": candidate["dimension"], "rule": candidate["rule"],
                          "prior_results": len(nxt["results"])})

    # Pre-declared order, not a model trained on engagement data. Sort IDs for stable runs.
    actions.sort(key=lambda x: (SEVERITY[x["priority"]], x["kind"], x["platform"] or "",
                                x["account_id"] or "", x["signal"]))
    unique: list[dict] = []
    found: set[tuple] = set()
    for item in actions:
        key = (item["kind"], item["signal"], item["platform"], item["account_id"])
        if key in found:
            continue
        found.add(key)
        unique.append(item)
    shown = unique[:limit]
    for index, item in enumerate(shown, 1):
        item["rank"] = index
    return {
        "schema_version": ACTION_SCHEMA,
        "status": "offline_evidence_ranked_human_review_only",
        "priority_method": "P0 unresolved safety/privacy > P1 blockers > P2 observed improvements > P3 experiments",
        "source_coverage": coverage,
        "sources_used": sources,
        "total_actionable_signals": len(unique),
        "shown": len(shown), "omitted_due_to_limit": len(unique) - len(shown),
        "actions": shown,
        "limitations": [
            "Recommendations are deterministic rules over owner-provided data and existing local state, NOT a platform algorithm or predictive social ranking.",
            "No data source is contacted online; missing data is not negative evidence. Analytics labels/provenance are not independently attested.",
            "No post, profile, account, opt-out list, database or file is modified; all actions require separate owner review.",
            "Do not treat reported improvements as causal without a controlled follow-up and qualified conversion evidence.",
        ],
    }


def render_text(result: dict) -> str:
    lines = [
        "REACHWEAVER SUGGEST — OFFLINE evidence-ranked actions (not a reach prediction)",
        f"{result['shown']} shown / {result['total_actionable_signals']} signals; all require human review",
    ]
    for a in result["actions"]:
        where = " / ".join(x for x in (a["platform"], a["account_id"]) if x)
        if where:
            where = f" [{where}]"
        lines.append(f"{a['rank']}. {a['priority']} {a['kind']}{where}: {a['title']}")
        lines.append(f"   Evidence: {a['evidence']['source']} ({a['confidence']}); signal {a['signal']}")
        lines.append(f"   Next: {a['next_step']}")
    if not result["actions"]:
        lines.append("No actionable findings from supplied inputs; this does not prove accounts are optimized.")
    lines.append("No API requests, publishing, comments, follows, DMs or database mutations occurred.")
    return "\n".join(lines)
