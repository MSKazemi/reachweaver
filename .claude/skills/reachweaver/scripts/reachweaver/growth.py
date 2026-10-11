"""Offline guardrails for preregistered growth experiments (issue #16 slice).

Four pure, deterministic pieces sit around the existing matched-age readout in
``experiments.py``:

1. ``check_variant_set``: 2-3 single-dimension variants, no near-duplicates and no
   detectable drift in the dimensions that must stay constant.
2. ``preregister`` / ``verify_preregistration``: a validated plan, its canonical JSON
   and a sha256 ``plan_hash`` that reveals any later edit. ``write_preregistration``
   creates a NEW owner-only file and never overwrites.
3. ``classify_result``: maps ``evaluate_experiment`` output onto
   insufficient_data / inconclusive / directional. There is no "proven" label.
   Readout, design labels and seeded simulation live in ``growth_readout.py``.
4. ``next_hypotheses``: keeps negative results and suggests the next dimension from a
   fixed ordered list. It is not a learned recommender.

Nothing here contacts a network, opens the SQLite outbox, drafts, approves, schedules
or publishes. Organic feed comparisons are observational: they cannot show causation
or promise reach.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import date, datetime, timezone
from math import comb
from pathlib import Path
from statistics import median
from typing import Any, cast
from urllib.parse import urlparse

from .campaigns import SENSITIVE_KEY, SLUG, public_url
from .claims import DEFAULT_DUPLICATE_THRESHOLD, jaccard
from .policy import PLATFORMS, ValidationError
from .privacy import write_private_file
from .storage import parse_utc

PLAN_KIND = "reachweaver.growth-plan"
RECORD_KIND = "reachweaver.growth-preregistration"
# Fixed, documented order for the hypothesis backlog: content first (cheapest to change,
# least confounded), then packaging, then timing (most confounded by feed and audience).
DIMENSIONS = ("hook", "framing", "format", "cta", "timing")
MIN_VARIANTS, MAX_VARIANTS = 2, 3
# Power-style heuristic: with n pairs and a coin-flip null, the chance that every pair
# points the same way is 2 * 0.5**n (two-sided exact sign test). n = 5 gives 0.0625 and
# n = 6 gives 0.03125, so 6 is the smallest n at which even unanimity is unlikely by chance.
MIN_RECOMMENDED_PAIRS = 6
# The same heuristic as a gate: a directional label needs a descriptive two-sided sign-test
# p at or below 0.05 (unanimity at n = 6, 10 of 12). Seeded simulations (growth_readout.
# simulate_readout) showed that a strict majority alone labels about 30% of zero-lift
# experiments directional.
MAX_DIRECTIONAL_SIGN_P = 0.05
LABELS = ("insufficient_data", "inconclusive", "directional")
REQUIRED_CONFOUNDERS = ("account_baseline", "time", "topic", "platform", "follower_count", "feed_changes")
# Qualified signals that may be a PRIMARY outcome, with the denominators each accepts.
QUALIFIED_SIGNALS: dict[str, dict[str, Any]] = {
    "link_clicks": {"denominators": ("impressions",), "metric_field": "clicks"},
    "replies": {"denominators": ("impressions", "views", "none"), "metric_field": "replies"},
    "saves": {"denominators": ("impressions", "views", "none"), "metric_field": "saves"},
    "profile_visits": {"denominators": ("impressions", "none"), "metric_field": None},
    "repo_stars_utm": {"denominators": ("none",), "metric_field": None},
    "docs_visits": {"denominators": ("none",), "metric_field": None},
}
# Vanity metrics: allowed as guardrails only, never as the primary outcome.
GUARDRAIL_ONLY = frozenset({"likes", "views", "impressions", "reposts", "followers"})
STOP_TYPES = ("fixed_pairs", "window_end")
# Owner-declared arm assignment. Absent means unrandomized. ReachWeaver cannot verify a
# declared randomization; only "randomized" may be read as more than associational.
ASSIGNMENTS = ("randomized", "alternating", "unrandomized")
EARLY_STOP_REASONS = frozenset({"guardrail_breach", "account_incident", "platform_policy_change"})
_PLAN_KEYS = frozenset({
    "schema_version", "kind", "experiment_id", "hypothesis", "dimension", "cohort",
    "primary_metric", "guardrail_metrics", "observation_window", "minimum_pairs",
    "minimum_relative_lift", "maximum_guardrail_drop", "stopping_rule",
    "confounders_acknowledged", "variants", "notes", "assignment",
})
_VARIANT_KEYS = frozenset({"id", "role", "differs_in", "text", "hook", "framing", "format",
                           "cta_url", "cta_phrase", "timing"})
_OVERCLAIM = re.compile(r"\b(?:guarantee[ds]?|go(?:es)? viral|viral(?:ity)?|proven|definitely|"
                        r"always|certainly|causes? (?:more|higher))\b", re.I)
_TEXT_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
LIMITATIONS = (
    "Sequential organic posts are observational: feed allocation, timing, topic and audience "
    "changes confound every comparison. No label here implies causation or future reach.",
    "registered_at is the local clock at registration, not an independent timestamp. Commit the "
    "record to Git or store it with a third-party timestamp for stronger auditability.",
    "The variant checks compare declared labels and detectable links. They cannot prove that two "
    "texts differ only in the declared dimension.",
)


def canonical_json(obj) -> str:
    """Key-sorted, compact, UTF-8 JSON: whitespace and key order never change the hash."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def plan_hash(plan: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(plan).encode("utf-8")).hexdigest()


def _secret_keys(obj, location="plan") -> list[str]:
    issues = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if SENSITIVE_KEY.search(str(key)):
                issues.append(f"{location}.{key}: secret-like fields are forbidden in growth plans")
            issues.extend(_secret_keys(value, f"{location}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            issues.extend(_secret_keys(value, f"{location}[{index}]"))
    return issues


def _label(value) -> str | None:
    return " ".join(value.casefold().split()) if isinstance(value, str) and value.strip() else None


def _link_key(url: str) -> tuple[str, str] | None:
    """Host + path only: per-variant UTM query values legitimately differ."""
    try:
        parsed = urlparse(url.rstrip(".,;:!?"))
    except ValueError:
        return None
    return ((parsed.hostname or "").casefold(), parsed.path.rstrip("/"))


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def check_variant_set(variants, dimension, *,
                      duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD) -> tuple[list[str], list[str]]:
    """Validate a 2-3 arm, single-dimension variant set. Errors block preregistration."""
    errors: list[str] = []
    warnings: list[str] = []
    if dimension not in DIMENSIONS:
        errors.append(f"dimension must be exactly one of {', '.join(DIMENSIONS)}")
    if not isinstance(variants, list):
        return errors + ["variants must be a list of 2-3 variant objects"], warnings
    if len(variants) < MIN_VARIANTS:
        errors.append("variants: at least 2 arms (one control, one treatment) are required")
    if len(variants) > MAX_VARIANTS:
        errors.append(f"variants: {len(variants)} arms is mass blasting; at most {MAX_VARIANTS} "
                      "meaningful variants per experiment")
        return errors, warnings
    arms: list[dict[str, Any]] = []
    ids = set()
    for index, variant in enumerate(variants):
        path = f"variants[{index}]"
        if not isinstance(variant, dict):
            errors.append(f"{path} must be an object")
            continue
        unknown = sorted(set(variant) - _VARIANT_KEYS)
        if unknown:
            errors.append(f"{path}: unknown fields {', '.join(unknown)}")
        vid = variant.get("id")
        if not isinstance(vid, str) or not SLUG.fullmatch(vid) or vid in ids:
            errors.append(f"{path}.id must be a unique lowercase slug")
        else:
            ids.add(vid)
        if variant.get("role") not in ("control", "treatment"):
            errors.append(f"{path}.role must be control or treatment")
        differs = variant.get("differs_in")
        if isinstance(differs, list):
            errors.append(f"{path}.differs_in must name ONE dimension; multi-dimension variants are rejected")
        elif differs not in DIMENSIONS:
            errors.append(f"{path}.differs_in must be one of {', '.join(DIMENSIONS)}")
        elif dimension in DIMENSIONS and differs != dimension:
            errors.append(f"{path}.differs_in {differs!r} does not match the plan dimension {dimension!r}")
        text = variant.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{path}.text must be the nonempty draft text")
            text = ""
        for field in ("hook", "framing", "format", "timing"):
            if _label(variant.get(field)) is None:
                errors.append(f"{path}.{field} must be a nonempty label (held constant unless tested)")
        cta_url, cta_phrase = variant.get("cta_url"), variant.get("cta_phrase")
        if cta_url is not None and not public_url(cta_url):
            errors.append(f"{path}.cta_url must be a public HTTPS URL without credentials")
        if cta_phrase is not None and _label(cta_phrase) is None:
            errors.append(f"{path}.cta_phrase must be a nonempty string when present")
        if cta_url is None and cta_phrase is None:
            errors.append(f"{path}: declare cta_url and/or cta_phrase so the CTA can be held constant")
        cta_link = _link_key(cta_url) if isinstance(cta_url, str) else None
        text_links = {k for k in (_link_key(m.group()) for m in _TEXT_URL.finditer(text)) if k}
        if cta_link and text and cta_link not in text_links:
            warnings.append(f"{path}: declared cta_url is not present in the text")
        arms.append({"path": path, "id": vid, "role": variant.get("role"), "text": text,
                     "labels": {f: _label(variant.get(f)) for f in ("hook", "framing", "format", "timing")},
                     "cta": (cta_link, _label(cta_phrase)), "links": frozenset(text_links)})
    if arms and sum(1 for a in arms if a["role"] == "control") != 1:
        errors.append("variants: exactly one arm must have role control")
    if len(arms) < 2:
        return errors, warnings
    control = next((a for a in arms if a["role"] == "control"), arms[0])
    for arm in arms:
        if arm is control:
            continue
        # (a) declared labels of every NON-tested dimension must be identical
        for field in ("hook", "framing", "format", "timing"):
            if field != dimension and arm["labels"][field] != control["labels"][field]:
                errors.append(f"{arm['path']}: {field} differs from control but the declared dimension is "
                              f"{dimension!r}; change one dimension per experiment")
        if dimension != "cta" and arm["cta"] != control["cta"]:
            errors.append(f"{arm['path']}: CTA (cta_url/cta_phrase) differs from control but the declared "
                          f"dimension is {dimension!r}")
        # (b) detectable drift in the text itself: different destination links
        if dimension != "cta" and arm["links"] != control["links"]:
            errors.append(f"{arm['path']}: links in the text differ from control (host/path) but the declared "
                          f"dimension is {dimension!r}")
        # (c) the tested dimension must actually differ
        if dimension in ("hook", "framing", "format", "timing") and \
                arm["labels"][dimension] == control["labels"][dimension]:
            errors.append(f"{arm['path']}: declared {dimension} label is identical to control; nothing is tested")
        if dimension == "cta" and arm["cta"] == control["cta"]:
            errors.append(f"{arm['path']}: CTA is identical to control; nothing is tested")
        if dimension == "hook" and arm["text"] and control["text"] and \
                jaccard(_first_line(arm["text"]), _first_line(control["text"])) >= duplicate_threshold:
            errors.append(f"{arm['path']}: opening line is a near-duplicate of the control hook")
    # (d) no near-duplicate posts to the same audience (claims.py word 3-gram Jaccard)
    for i in range(len(arms)):
        for j in range(i + 1, len(arms)):
            if arms[i]["text"] and arms[j]["text"]:
                score = jaccard(arms[i]["text"], arms[j]["text"])
                if score >= duplicate_threshold:
                    errors.append(f"{arms[j]['path']}: near-duplicate of {arms[i]['path']} (word 3-gram Jaccard "
                                  f"{score:.2f} >= {duplicate_threshold:.2f}); write a distinct post, not a respin")
    return errors, warnings


def _check_metric(item, path, *, primary: bool) -> list[str]:
    if not isinstance(item, dict):
        return [f"{path} must be an object with signal and denominator"]
    signal, denominator = item.get("signal"), item.get("denominator", "none")
    if primary:
        if signal in GUARDRAIL_ONLY:
            return [f"{path}.signal {signal!r} is a vanity metric; allowed only as a guardrail. Use one of "
                    f"{', '.join(QUALIFIED_SIGNALS)}"]
        if signal not in QUALIFIED_SIGNALS:
            return [f"{path}.signal must be a qualified signal: {', '.join(QUALIFIED_SIGNALS)}"]
        allowed = QUALIFIED_SIGNALS[signal]["denominators"]
        if denominator not in allowed:
            return [f"{path}.denominator for {signal} must be one of {', '.join(allowed)}"]
        return []
    if signal not in GUARDRAIL_ONLY and signal not in QUALIFIED_SIGNALS:
        return [f"{path}.signal must be a known qualified or vanity metric"]
    if denominator not in ("impressions", "views", "none"):
        return [f"{path}.denominator must be impressions, views or none"]
    return []


def _iso_day(value) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_plan(plan) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a growth plan; errors block preregistration."""
    if not isinstance(plan, dict):
        return ["Growth plan must be a JSON object"], []
    errors, warnings = _secret_keys(plan), []
    unknown = sorted(set(plan) - _PLAN_KEYS)
    if unknown:
        errors.append(f"Unknown plan fields: {', '.join(unknown)}")
    if plan.get("schema_version") != 1 or plan.get("kind") != PLAN_KIND:
        errors.append(f"schema_version must be 1 and kind must be {PLAN_KIND!r}")
    if not isinstance(plan.get("experiment_id"), str) or not SLUG.fullmatch(plan["experiment_id"]):
        errors.append("experiment_id must be a lowercase slug")
    hypothesis = plan.get("hypothesis")
    if not isinstance(hypothesis, str) or not 20 <= len(hypothesis.strip()) <= 1000:
        errors.append("hypothesis must be a falsifiable statement of 20-1000 characters")
    elif _OVERCLAIM.search(hypothesis):
        errors.append(f"hypothesis overclaims ({cast(re.Match, _OVERCLAIM.search(hypothesis)).group()!r}); "
                      "state a testable direction, not a guarantee")
    cohort = plan.get("cohort")
    if not isinstance(cohort, dict) or cohort.get("platform") not in PLATFORMS or \
            not isinstance(cohort.get("account_id"), str) or not SLUG.fullmatch(cohort["account_id"]):
        errors.append("cohort must name ONE platform and ONE account_id slug; cohorts are never pooled")
    errors.extend(_check_metric(plan.get("primary_metric"), "primary_metric", primary=True))
    guardrails = plan.get("guardrail_metrics")
    if not isinstance(guardrails, list) or not guardrails:
        errors.append("guardrail_metrics must list at least one guardrail")
    else:
        for index, item in enumerate(guardrails):
            errors.extend(_check_metric(item, f"guardrail_metrics[{index}]", primary=False))
        primary = cast(dict, plan.get("primary_metric")) if isinstance(plan.get("primary_metric"), dict) else {}
        if any(isinstance(g, dict) and g.get("signal") == primary.get("signal") for g in guardrails):
            errors.append("a guardrail cannot be the primary metric")
    window = plan.get("observation_window")
    start = end = None
    if not isinstance(window, dict):
        errors.append("observation_window must declare metric_age_hours, age_tolerance_hours, start and end")
    else:
        age, tolerance = window.get("metric_age_hours"), window.get("age_tolerance_hours")
        if type(age) is not int or not 24 <= age <= 720:
            errors.append("observation_window.metric_age_hours must be an integer in [24, 720]")
        if type(tolerance) is not int or not 1 <= tolerance <= 168 or (type(age) is int and tolerance >= age):
            errors.append("observation_window.age_tolerance_hours must be an integer in [1, 168] below metric age")
        start, end = _iso_day(window.get("start")), _iso_day(window.get("end"))
        if start is None or end is None or end <= start:
            errors.append("observation_window.start/end must be YYYY-MM-DD with end after start")
        elif (end - start).days > 120:
            errors.append("observation_window spans more than 120 days; feed and audience drift dominate")
    minimum = plan.get("minimum_pairs")
    if type(minimum) is not int or not 2 <= minimum <= 100:
        errors.append("minimum_pairs must be an integer in [2, 100]")
    elif minimum < MIN_RECOMMENDED_PAIRS:
        warnings.append(f"minimum_pairs={minimum} is below the {MIN_RECOMMENDED_PAIRS}-pair heuristic: even if "
                        f"every pair agrees, a coin-flip null gives p={_sign_p(minimum, minimum):.3f}; such a "
                        "readout can only be inconclusive")
    for field in ("minimum_relative_lift", "maximum_guardrail_drop"):
        value = plan.get(field)
        if type(value) not in (int, float) or not 0 <= cast(float, value) <= 1:
            errors.append(f"{field} must be a number in [0, 1]")
    rule = plan.get("stopping_rule")
    if not isinstance(rule, dict) or rule.get("type") not in STOP_TYPES:
        errors.append(f"stopping_rule.type must be one of {', '.join(STOP_TYPES)}")
    else:
        extra = sorted(set(rule) - {"type", "max_pairs", "stop_early_only_for", "description"})
        if extra:
            errors.append(f"stopping_rule has unsupported keys {', '.join(extra)}; early stopping on a "
                          "favourable result is not allowed")
        max_pairs = rule.get("max_pairs")
        if type(max_pairs) is not int or type(minimum) is not int or not cast(int, minimum) <= cast(int, max_pairs) <= 100:
            errors.append("stopping_rule.max_pairs must be an integer >= minimum_pairs and <= 100")
        early = rule.get("stop_early_only_for", [])
        if not isinstance(early, list) or not set(early) <= EARLY_STOP_REASONS:
            errors.append("stopping_rule.stop_early_only_for may list only "
                          f"{', '.join(sorted(EARLY_STOP_REASONS))} (never a winning result)")
    confounders = plan.get("confounders_acknowledged")
    if not isinstance(confounders, dict):
        errors.append("confounders_acknowledged must map each confounder to an acknowledgement note")
    else:
        missing = [c for c in REQUIRED_CONFOUNDERS if _label(confounders.get(c)) is None]
        if missing:
            errors.append(f"confounders_acknowledged is missing a note for: {', '.join(missing)}")
        if any(not isinstance(v, str) or not v.strip() for v in confounders.values()):
            errors.append("every confounder acknowledgement must be a nonempty note")
    if "assignment" in plan and plan["assignment"] not in ASSIGNMENTS:
        errors.append(f"assignment must be one of {', '.join(ASSIGNMENTS)} (omit it for unrandomized)")
    v_errors, v_warnings = check_variant_set(plan.get("variants"), plan.get("dimension"))
    errors.extend(v_errors)
    warnings.extend(v_warnings)
    primary = cast(dict, plan.get("primary_metric")) if isinstance(plan.get("primary_metric"), dict) else {}
    if primary.get("signal") in QUALIFIED_SIGNALS and QUALIFIED_SIGNALS[primary["signal"]]["metric_field"] is None:
        warnings.append(f"{primary['signal']} is not a native snapshot field; collect it via UTM/first-party "
                        "attribution (attribution-report), not experiment-evaluate")
    return errors, warnings


def _sign_p(n: int, k: int) -> float:
    """Two-sided exact sign test: P(at least k of n agree) under p=0.5, doubled, capped at 1."""
    if n <= 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def preregister(plan: dict, *, now: datetime | None = None) -> dict:
    """Validate a growth plan and return a hashed registration record (no file written)."""
    errors, warnings = check_plan(plan)
    if errors:
        raise ValidationError("Preregistration blocked: " + "; ".join(errors))
    moment = (now or datetime.now(timezone.utc))
    registered = parse_utc(moment.isoformat())
    minimum = plan["minimum_pairs"]
    return {
        "kind": RECORD_KIND, "schema_version": 1, "experiment_id": plan["experiment_id"],
        "plan_hash": plan_hash(plan), "hash_algorithm": "sha256",
        "canonicalization": "JSON, keys sorted, separators (',', ':'), UTF-8, ensure_ascii=false",
        "registered_at": registered,
        "power_heuristic": {
            "minimum_pairs": minimum, "recommended_minimum_pairs": MIN_RECOMMENDED_PAIRS,
            "best_case_sign_test_p": round(_sign_p(minimum, minimum), 6),
            "rule": "two-sided exact sign test if every pair agrees; descriptive only" +
                    ("; owner-declared randomization is not verified" if plan.get("assignment") == "randomized"
                     else ", pairs are not randomized"),
        },
        "warnings": warnings, "limitations": list(LIMITATIONS),
        "status": "registered_not_approved",
        "note": "Registration is not approval to publish. Every post still needs per-item human approval "
                "and the existing dry-run, budget and emergency-stop rules.",
        "plan": plan,
    }


def write_preregistration(record: dict, path: str | Path) -> Path:
    """Write the record to a NEW 0600 file; an existing path is never overwritten."""
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise ValidationError(f"Refusing to overwrite existing preregistration {target}")
    try:
        write_private_file(target, json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise ValidationError(f"Refusing to overwrite existing preregistration {target}") from exc
    return target


def verify_preregistration(plan: dict, expected_hash: str) -> dict:
    """Recompute the hash of a plan (or of a record's embedded plan) and compare."""
    if not isinstance(plan, dict):
        raise ValidationError("Plan must be a JSON object")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", expected_hash.strip()):
        raise ValidationError("--hash must be sha256:<64 hex> or 64 hex characters")
    record_hash = None
    if plan.get("kind") == RECORD_KIND:
        record_hash = plan.get("plan_hash")
        plan = cast(dict, plan.get("plan"))
        if not isinstance(plan, dict):
            raise ValidationError("Preregistration record has no embedded plan")
    computed = plan_hash(plan)
    expected = "sha256:" + expected_hash.strip().lower().removeprefix("sha256:")
    match = hmac.compare_digest(computed, expected)
    if record_hash is not None:
        match = match and isinstance(record_hash, str) and hmac.compare_digest(computed, record_hash.lower())
    return {"match": match, "computed_hash": computed, "expected_hash": expected,
            "experiment_id": plan.get("experiment_id"),
            "verdict": "unchanged_since_registration" if match else "edited_after_registration_or_wrong_hash",
            "note": "A match shows the plan text is unchanged; it does not prove when it was written."}


def is_windowed_snapshot(row: Any) -> bool:
    """True for a snapshot that carries window_start/window_end: a windowed count, not cumulative.

    Matched-age comparisons assume cumulative counts at post age, so windowed rows are excluded.
    """
    try:
        return bool(row["window_start"] or row["window_end"])
    except (KeyError, IndexError, TypeError):
        return False


def classify_result(pairs_summary: dict, plan: dict | None = None) -> dict:
    """Map ``experiments.evaluate_experiment`` output to a conservative readout label.

    Labels are only insufficient_data, inconclusive and directional. ``plan`` may be the
    growth or experiment plan; its minimum_pairs / minimum_relative_lift /
    maximum_guardrail_drop are used, otherwise conservative defaults apply.
    """
    if not isinstance(pairs_summary, dict):
        raise ValidationError("pairs_summary must be evaluate_experiment output")
    plan = plan if isinstance(plan, dict) else {}
    minimum = cast(int, plan.get("minimum_pairs")) if type(plan.get("minimum_pairs")) is int else MIN_RECOMMENDED_PAIRS
    minimum = max(minimum, 2)
    min_lift = plan.get("minimum_relative_lift", 0.1)
    max_drop = plan.get("maximum_guardrail_drop", 0.1)
    pairs = [p for p in pairs_summary.get("pair_results") or []
             if isinstance(p, dict) and isinstance(p.get("control_rate"), (int, float))
             and isinstance(p.get("treatment_rate"), (int, float))]
    diffs = [p["treatment_rate"] - p["control_rate"] for p in pairs]
    n = len(diffs)
    positive, negative = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    ties = n - positive - negative
    total = sum(diffs)
    direction = "positive" if total > 0 else "negative" if total < 0 else "none"
    agreeing = positive if direction == "positive" else negative if direction == "negative" else 0
    explanations: list[str] = []
    flags = {"novelty_effect": False, "single_outlier": False}
    out: dict[str, Any] = {"label": "insufficient_data", "direction": direction if n else None,
           "pairs_usable": n, "minimum_pairs": minimum, "recommended_minimum_pairs": MIN_RECOMMENDED_PAIRS,
           "direction_counts": {"treatment_higher": positive, "control_higher": negative, "tied": ties},
           "majority_share": round(agreeing / n, 4) if n else None,
           "sign_test_two_sided_p": round(_sign_p(positive + negative, agreeing), 6) if n else None,
           "median_pair_difference": round(median(diffs), 6) if n else None,
           "relative_lift": pairs_summary.get("relative_lift"),
           "guardrail_relative_change": pairs_summary.get("guardrail_relative_change"),
           "source_verdict": pairs_summary.get("verdict"),
           "flags": flags, "explanations": explanations, "allowed_labels": list(LABELS),
           "note": "Observational association at best; never causal, never proven, no reach guarantee."}
    if n < minimum:
        explanations.append(f"{n} usable pair(s) is below the preregistered minimum of {minimum}; collect more "
                            "comparable pairs before reading a direction")
        if pairs_summary.get("pairs_skipped"):
            explanations.append(f"{len(pairs_summary['pairs_skipped'])} pair(s) were excluded by the matched-age "
                                "readout (missing data is not zero)")
        return out
    # Outlier and novelty checks need at least three pairs to be meaningful.
    abs_total = sum(abs(d) for d in diffs)
    if n >= 3 and abs_total > 0:
        first_share = abs(diffs[0]) / abs_total
        rest = sum(diffs[1:])
        if (first_share >= 0.5 and (diffs[0] > 0) == (total > 0)) or (total and rest and (rest > 0) != (total > 0)):
            flags["novelty_effect"] = True
            explanations.append(f"novelty effect: the first pair carries {first_share:.0%} of the total difference "
                                "or dropping it reverses the direction; early novelty may not persist")
        for index, d in enumerate(diffs):
            loo = total - d
            share = abs(d) / abs_total
            if index == 0 and flags["novelty_effect"]:
                continue
            if share >= 0.5 or (total and loo and (loo > 0) != (total > 0)):
                flags["single_outlier"] = True
                explanations.append(f"single outlier: pair {index} carries {share:.0%} of the total difference "
                                    "or removing it reverses the direction")
                break
        med = median(diffs)
        if not flags["single_outlier"] and total and med and (med > 0) != (total > 0):
            flags["single_outlier"] = True
            explanations.append("single outlier: the median pair points the other way from the pooled total")
    if direction == "none" or agreeing * 2 <= n:
        out["label"] = "inconclusive"
        explanations.append(f"no consistent direction: {agreeing} of {n} pairs agree with the pooled direction "
                            "(a strict majority is required)")
        return out
    lift = pairs_summary.get("relative_lift")
    if not isinstance(lift, (int, float)):
        out["label"] = "inconclusive"
        explanations.append("the readout reported no pooled lift (zero baseline or below its own minimum pairs)")
        return out
    if lift != 0 and (lift > 0) != (direction == "positive"):
        out["label"] = "inconclusive"
        explanations.append(f"pooled relative lift ({lift:+.3f}) disagrees in sign with the per-pair direction "
                            f"({direction}); the summary is internally inconsistent, so no direction is read")
        return out
    if abs(lift) < min_lift:
        out["label"] = "inconclusive"
        explanations.append(f"|relative lift| {abs(lift):.3f} is below the preregistered minimum useful effect {min_lift}")
        return out
    guard = pairs_summary.get("guardrail_relative_change")
    if direction == "positive" and isinstance(guard, (int, float)) and guard < -max_drop:
        out["label"] = "inconclusive"
        explanations.append(f"guardrail fell {guard:.3f}, beyond the allowed drop {max_drop}; the gain is not usable")
        return out
    if n < MIN_RECOMMENDED_PAIRS:
        out["label"] = "inconclusive"
        explanations.append(f"{n} pairs is below the {MIN_RECOMMENDED_PAIRS}-pair heuristic; even unanimous "
                            "agreement is compatible with chance")
        return out
    if out["sign_test_two_sided_p"] > MAX_DIRECTIONAL_SIGN_P:
        out["label"] = "inconclusive"
        explanations.append(f"{agreeing} of {positive + negative} untied pairs agree; descriptive sign-test "
                            f"p={out['sign_test_two_sided_p']:.3f} is above {MAX_DIRECTIONAL_SIGN_P}, so chance "
                            "agreement is not ruled out even heuristically")
        return out
    if flags["novelty_effect"] or flags["single_outlier"]:
        out["label"] = "inconclusive"
        explanations.append("direction depends on one pair; replicate before acting")
        return out
    out["label"] = "directional"
    explanations.append(f"{agreeing} of {n} pairs agree ({direction}); a directional association to replicate "
                        "on a fresh cohort, not a proven or causal effect")
    return out


def next_hypotheses(results) -> dict:
    """Deterministic backlog: keep negative results, suggest one next dimension.

    ``results`` is a list of {experiment_id, dimension, label, direction?}. The rules,
    applied in order: finish an experiment that lacks data; replicate a directional
    positive result seen only once; test the first untested dimension in DIMENSIONS;
    otherwise the least-tested dimension (ties broken by DIMENSIONS order).
    """
    if not isinstance(results, list):
        raise ValidationError("results must be a list of classified experiment results")
    recorded, negatives = [], []
    for index, item in enumerate(results):
        if not isinstance(item, dict) or item.get("dimension") not in DIMENSIONS or item.get("label") not in LABELS \
                or not isinstance(item.get("experiment_id"), str):
            raise ValidationError(f"results[{index}] needs experiment_id, a known dimension and a known label")
        entry = {"experiment_id": item["experiment_id"], "dimension": item["dimension"],
                 "label": item["label"], "direction": item.get("direction")}
        recorded.append(entry)
        if item["label"] == "inconclusive" or (item["label"] == "directional" and item.get("direction") != "positive"):
            negatives.append({**entry, "reason": "treatment did not show a usable positive direction; "
                                                 "kept so the idea is not silently retried"})
    completed = {d: [r for r in recorded if r["dimension"] == d and r["label"] != "insufficient_data"]
                 for d in DIMENSIONS}
    suggestion = None
    pending = [r for r in recorded if r["label"] == "insufficient_data"
               and not any(c["experiment_id"] == r["experiment_id"] for c in completed[r["dimension"]])]
    if pending:
        suggestion = {"dimension": pending[0]["dimension"], "rule": "finish_pending",
                      "reason": f"{pending[0]['experiment_id']} lacks the minimum pairs; finish it before a new test"}
    if suggestion is None:
        for d in DIMENSIONS:
            positives = [r for r in completed[d] if r["label"] == "directional" and r["direction"] == "positive"]
            if len(positives) == 1 and completed[d][-1] is positives[0]:
                suggestion = {"dimension": d, "rule": "replicate",
                              "reason": f"{positives[0]['experiment_id']} is a single directional result; replicate on "
                                        "a fresh cohort before treating it as a pattern"}
                break
    if suggestion is None:
        untested = [d for d in DIMENSIONS if not completed[d]]
        if untested:
            suggestion = {"dimension": untested[0], "rule": "next_untested",
                          "reason": f"first untested dimension in the fixed order {', '.join(DIMENSIONS)}"}
        else:
            d = min(DIMENSIONS, key=lambda x: (len(completed[x]), DIMENSIONS.index(x)))
            suggestion = {"dimension": d, "rule": "least_tested",
                          "reason": "every dimension has a completed test; revisit the least-tested one"}
    return {"method": "fixed ordered list; deterministic rules; no learned recommender",
            "order": list(DIMENSIONS), "results": recorded, "negative_results": negatives,
            "next": suggestion, "requires_human_approval": True,
            "note": "A suggestion is a hypothesis to preregister, not a draft, schedule or permission to publish."}
