"""Readout, design labeling and seeded simulation for preregistered growth plans (issue #16).

Builds on ``growth.py`` (validation, hashing, labels) and ``experiments.py`` (matched-age
readout):

1. ``growth_plan_to_experiment``: a hash-verified preregistration record plus the owner's
   arm -> post-id map becomes an ``experiment-evaluate`` plan. Metric, denominator, window,
   tolerance, thresholds and minimum pairs are copied, never re-chosen.
2. ``describe_design``: the owner-declared assignment. Anything not declared
   ``randomized`` is described as associational, never as a randomized result.
3. ``run_readout``: verify -> convert -> ``evaluate_experiment`` -> ``classify_result``.
4. ``simulate_readout``: ``random.Random(seed)`` synthetic pairs, run through the same
   labeler, to show how often each label occurs. Simulated data is never stored.

The database is only read (``SELECT``). Nothing here drafts, approves, schedules,
publishes or contacts a network. No label here is causal or proven.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from .attribution import ATTRIBUTION_OUTCOMES, attributed_outcome  # parsing only; no network
from .experiments import _snapshot_at_age, evaluate_experiment, pooled_readout
from .growth import (
    ASSIGNMENTS, DIMENSIONS, LABELS, QUALIFIED_SIGNALS, RECORD_KIND, check_plan, classify_result,
    verify_preregistration,
)
from .policy import ValidationError
from .storage import METRIC_FIELDS, get_post, parse_utc
from .inputs import read_json_file

POST_MAP_KIND = "reachweaver.growth-post-map"
SIMULATION_KIND = "reachweaver.simulated-readout"
DISCLAIMER = ("Non-causal readout: organic feed comparisons are observational. This label is not proof, "
              "not statistical significance and not a promise of future reach.")
_DESIGNS = {
    "randomized": ("randomized_assignment_owner_declared",
                   "The owner declared randomized arm assignment. ReachWeaver cannot verify it. Randomized order "
                   "removes systematic order bias in expectation, but feed allocation, audience and topic still "
                   "vary, so the result is a within-cohort comparison, not a causal or proven effect."),
    "alternating": ("associational",
                    "Arm order alternated by a fixed rule. That balances order across pairs, but it is not random: "
                    "any difference is an association, not a randomized result."),
    "unrandomized": ("associational",
                     "Arms were not randomly assigned. Any difference is an association confounded by timing, "
                     "topic, audience and feed allocation, not a randomized result."),
}


def describe_design(assignment: object) -> dict[str, Any]:
    """Return the readout design block. Only ``randomized`` is more than associational."""
    declared = assignment if isinstance(assignment, str) and assignment in ASSIGNMENTS else "unrandomized"
    interpretation, statement = _DESIGNS[declared]
    if declared != "randomized" and interpretation != "associational":  # invariant, never relaxed
        raise ValidationError("An unrandomized design can only be described as associational")
    return {"assignment": declared, "declared": assignment is not None, "verified": False,
            "interpretation": interpretation, "statement": statement}


def _verified_plan(record: object, expected_hash: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(record, dict) or record.get("kind") != RECORD_KIND:
        raise ValidationError(f"--prereg must be a {RECORD_KIND} record written by experiment-preregister")
    registered_hash = record.get("plan_hash")
    if not isinstance(registered_hash, str):
        raise ValidationError("Preregistration record has no plan_hash")
    check = verify_preregistration(record, expected_hash or registered_hash)
    if not check["match"]:
        raise ValidationError("Preregistration hash does not verify: the plan was edited after registration "
                              "or the hash is wrong; refusing to read out")
    plan = record["plan"]
    errors, _ = check_plan(plan)
    if errors:
        raise ValidationError("Preregistered plan is not valid: " + "; ".join(errors))
    parse_utc(str(record.get("registered_at")))
    return record, plan


def _snapshot_field(signal: str) -> str | None:
    """Native snapshot column for a plan signal, or None when it needs first-party attribution."""
    if signal in METRIC_FIELDS:
        return signal
    spec = QUALIFIED_SIGNALS.get(signal)
    field = spec.get("metric_field") if isinstance(spec, dict) else None
    return field if isinstance(field, str) else None


def _guardrail(plan: dict[str, Any], primary: str, denominator: str) -> tuple[str, list[dict[str, str]]]:
    """First guardrail that is a snapshot field on the primary's denominator; others are listed."""
    chosen, skipped = None, []
    for item in plan["guardrail_metrics"]:
        signal = item["signal"]
        field = _snapshot_field(signal)
        reason = None
        if field is None:
            reason = "not a native snapshot field"
        elif item.get("denominator", "none") != denominator:
            reason = f"denominator {item.get('denominator', 'none')!r} differs from the primary's {denominator!r}"
        elif field in (primary, denominator):
            reason = "same field as the primary metric or the denominator"
        elif chosen is not None:
            reason = "experiment-evaluate reads one guardrail; the first eligible one is used"
        if reason:
            skipped.append({"signal": signal, "reason": reason})
        else:
            chosen = field
    if chosen is None:
        raise ValidationError("No guardrail is a native snapshot field on the primary metric's denominator; "
                              "experiment-evaluate cannot read this plan")
    return chosen, skipped


def _arm_ids(plan: dict[str, Any], post_map: dict[str, Any]) -> tuple[str, str]:
    control = next(v["id"] for v in plan["variants"] if v["role"] == "control")
    treatments = [v["id"] for v in plan["variants"] if v["role"] == "treatment"]
    treatment = post_map.get("treatment_arm")
    if treatment is None and len(treatments) == 1:
        treatment = treatments[0]
    if treatment not in treatments:
        raise ValidationError(f"treatment_arm must name one treatment arm of the plan: {', '.join(treatments)}")
    return control, str(treatment)


def _sent_day(post: sqlite3.Row) -> date:
    return datetime.fromisoformat(parse_utc(post["send_started_at"])).date()


def growth_plan_to_experiment(prereg_record: object, post_map: object, con: sqlite3.Connection | None = None, *,
                              expected_hash: str | None = None, attribution: dict | None = None) -> dict[str, Any]:
    """Convert a hash-verified preregistration plus an arm -> post-id map into an evaluate plan.

    ``post_map`` is ``{"kind": "reachweaver.growth-post-map", "experiment_id": ...,
    "treatment_arm"?: ..., "pairs": [{"<control arm id>": "<post id>", "<treatment arm id>": ...}]}``.
    With ``con``, every post must be published with a send timestamp on the cohort's account
    and platform; the database is only read. ``attribution`` (``attribution.load_attribution_outcomes``,
    issue #16 R4) is required for, and only accepted with, a ``docs_visits``/``repo_stars_utm``
    primary metric; the plan then reads that per-post count instead of a snapshot field.
    """
    record, plan = _verified_plan(prereg_record, expected_hash)
    if not isinstance(post_map, dict) or post_map.get("kind") != POST_MAP_KIND:
        raise ValidationError(f"--posts must be a {POST_MAP_KIND} JSON object")
    unknown = sorted(set(post_map) - {"kind", "schema_version", "experiment_id", "treatment_arm", "pairs", "notes"})
    if unknown:
        raise ValidationError(f"Unknown post map fields: {', '.join(unknown)}")
    if post_map.get("experiment_id") != plan["experiment_id"]:
        raise ValidationError("Post map experiment_id does not match the preregistered plan")
    control, treatment = _arm_ids(plan, post_map)
    known = {v["id"] for v in plan["variants"]}
    pairs = post_map.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValidationError("Post map pairs must be a nonempty list of {arm_id: post_id} objects")
    max_pairs = plan["stopping_rule"]["max_pairs"]
    if len(pairs) > max_pairs:
        raise ValidationError(f"Post map lists {len(pairs)} pairs, above the preregistered "
                              f"stopping_rule.max_pairs={max_pairs}")
    cohort = plan["cohort"]
    window = plan["observation_window"]
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    seen: set[str] = set()
    out_pairs, warnings = [], []
    order = {"control_first": 0, "treatment_first": 0, "same_time": 0}
    for index, pair in enumerate(pairs):
        path = f"pairs[{index}]"
        if not isinstance(pair, dict) or not set(pair) <= known:
            raise ValidationError(f"{path} must map only this plan's arm ids ({', '.join(sorted(known))}) to post ids")
        ids = {arm: pair.get(arm) for arm in (control, treatment)}
        for arm, post_id in {**pair, **ids}.items():
            if not isinstance(post_id, str) or not post_id.strip():
                raise ValidationError(f"{path}.{arm} must be a published post id")
        for post_id in pair.values():
            if post_id in seen:
                raise ValidationError(f"{path}: post {post_id} is reused; every post belongs to one pair")
            seen.add(post_id)
        if con is not None:
            rows = {arm: get_post(con, str(post_id)) for arm, post_id in ids.items()}
            for arm, row in rows.items():
                if row["status"] != "published" or not row["send_started_at"]:
                    raise ValidationError(f"{path}.{arm}: post {row['id']} is not published with a send timestamp")
                if row["account_id"] != cohort["account_id"] or row["platform"] != cohort["platform"]:
                    raise ValidationError(f"{path}.{arm}: post {row['id']} is on {row['account_id']}/{row['platform']}, "
                                          f"not the preregistered cohort {cohort['account_id']}/{cohort['platform']}")
                if not start <= _sent_day(row) <= end:
                    warnings.append(f"{path}.{arm}: sent outside the preregistered window {start}..{end}")
            c_sent, t_sent = (parse_utc(rows[a]["send_started_at"]) for a in (control, treatment))
            order["control_first" if c_sent < t_sent else "treatment_first" if t_sent < c_sent else "same_time"] += 1
        out_pairs.append({"control": ids[control], "treatment": ids[treatment]})
    assignment = describe_design(plan.get("assignment"))["assignment"]
    if con is not None and assignment == "alternating" and len(out_pairs) > 1 and \
            min(order["control_first"], order["treatment_first"]) * 2 + 1 < len(out_pairs):
        warnings.append("assignment is declared alternating but control-first and treatment-first pairs "
                        "are not balanced")
    if con is not None and len(out_pairs) > 1 and order["same_time"] == 0 and \
            min(order["control_first"], order["treatment_first"]) == 0:
        warnings.append("one arm was always sent first: send order is fully confounded with the arm")
    signal = plan["primary_metric"]["signal"]
    if attribution is not None and signal not in ATTRIBUTION_OUTCOMES:
        raise ValidationError(f"--attribution reads only {', '.join(ATTRIBUTION_OUTCOMES)} primary metrics; "
                              f"this plan's primary metric is {signal}")
    primary: str | None
    primary_source = "native_snapshot"
    if attribution is not None:
        primary, primary_source = ATTRIBUTION_OUTCOMES[signal], "attribution_report"
    else:
        primary = _snapshot_field(signal) if signal in QUALIFIED_SIGNALS else None
    if primary is None:
        hint = (" Pass a saved 'attribution-report --json' file with --attribution."
                if signal in ATTRIBUTION_OUTCOMES else "")
        raise ValidationError(f"{signal} is not a native snapshot field; read it with attribution-report, "
                              f"not experiment-evaluate.{hint}")
    denominator = plan["primary_metric"].get("denominator", "none")
    guardrail, skipped_guardrails = _guardrail(plan, primary, denominator)
    return {
        "schema_version": 1, "experiment_id": plan["experiment_id"], "hypothesis": plan["hypothesis"],
        "changed_variable": f"{plan['dimension']} only ({control} vs {treatment}); other declared dimensions "
                            "held constant",
        "pre_registered_at": record["registered_at"], "primary_metric": primary, "guardrail_metric": guardrail,
        "denominator": denominator, "minimum_pairs": plan["minimum_pairs"],
        "minimum_relative_lift": plan["minimum_relative_lift"],
        "maximum_guardrail_drop": plan["maximum_guardrail_drop"],
        "metric_age_hours": window["metric_age_hours"], "age_tolerance_hours": window["age_tolerance_hours"],
        "pairs": out_pairs,
        "plan_hash": record["plan_hash"], "source_kind": RECORD_KIND, "dimension": plan["dimension"],
        "cohort": {"platform": cohort["platform"], "account_id": cohort["account_id"]},
        "assignment": assignment, "control_arm": control, "treatment_arm": treatment,
        "primary_signal": signal, "primary_source": primary_source,
        "conversion": {"posts_checked": con is not None, "arm_order": order if con is not None else None,
                       "guardrails_not_evaluated": skipped_guardrails, "warnings": warnings},
    }


def evaluate_attribution_experiment(con: sqlite3.Connection, plan: dict[str, Any],
                                    attribution: dict[str, Any]) -> dict[str, Any]:
    """``evaluate_experiment`` for a ``docs_visits``/``repo_stars_utm`` primary (issue #16 R4).

    Each post's primary value is its attributed count from ``attribution`` (denominator
    ``none``); the guardrail is still the native snapshot nearest the preregistered age. A
    pair is skipped, never zero-filled, when either post's count or guardrail is unknown.
    The pooled lift and the strict gate are ``experiments.pooled_readout``, unchanged.
    """
    signal = plan["primary_signal"]
    used: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    paired_direction: list[bool] = []
    primary_num, exposures, guard_num = [0, 0], [0, 0], [0, 0]
    prereg = datetime.fromisoformat(parse_utc(plan["pre_registered_at"]))
    warnings = [
        "Attributed visits are a UTM join over the analytics export window, not a matched post age: posts sent "
        "earlier in the window had longer to collect visits. Only posts with at least the preregistered age "
        "inside the window are used.",
        "A post without an exact UTM row is unknown, never 0: an untagged link and a real absence look the same.",
        "Sequential organic posts are observational: timing and algorithmic allocation confound comparisons.",
    ]
    for index, pair in enumerate(plan["pairs"]):
        label = f"pair[{index}]"
        rows = [get_post(con, pair[key]) for key in ("control", "treatment")]
        if any(datetime.fromisoformat(parse_utc(row["send_started_at"])) < prereg for row in rows):
            skipped.append({"pair": label, "reason": "first_publication_predates_declared_plan"})
            continue
        values: list[int] = []
        guards: list[int] = []
        reason: str | None = None
        for arm, row in zip(("control", "treatment"), rows):
            value, why = attributed_outcome(attribution, row, signal, age_hours=plan["metric_age_hours"])
            if why or value is None:
                reason = f"{why}:{arm}"
                break
            guard = _snapshot_at_age(con, row, plan["guardrail_metric"], plan["guardrail_metric"], "none",
                                     plan["metric_age_hours"], plan["age_tolerance_hours"])
            if guard is None:
                reason = f"missing_comparable_guardrail_snapshot:{arm}"
                break
            values.append(value)
            guards.append(guard["guardrail"])
        if reason:
            skipped.append({"pair": label, "reason": reason})
            continue
        paired_direction.append(values[1] > values[0])
        used.append({"pair": label, "account_id": rows[0]["account_id"], "platform": rows[0]["platform"],
                     "control": rows[0]["id"], "treatment": rows[1]["id"],
                     "control_rate": values[0], "treatment_rate": values[1],
                     "source_control": "attribution-report", "source_treatment": "attribution-report"})
        for i in (0, 1):
            primary_num[i] += values[i]
            exposures[i] += 1
            guard_num[i] += guards[i]
    window = attribution["analytics_date_range"]
    result = {"experiment_id": plan["experiment_id"], "primary_metric": plan["primary_metric"],
              "primary_signal": signal, "primary_source": "attribution_report",
              "attribution_source": attribution.get("source", "analytics"),
              "analytics_date_range": [d.isoformat() for d in window] if window else None,
              "guardrail_metric": plan["guardrail_metric"], "denominator": plan["denominator"],
              "metric_age_hours": plan["metric_age_hours"], "pairs_planned": len(plan["pairs"]),
              "pairs_usable": len(used), "pairs_skipped": skipped, "pair_results": used,
              "verdict": "inconclusive", "relative_lift": None, "guardrail_relative_change": None,
              "direction_agreement": None, "warnings": warnings, "windowed_snapshots_skipped": 0,
              "note": "Directional association only; never causal or statistically significant by itself. "
                      "Pair 'rates' are attributed counts per post (denominator none)."}
    return pooled_readout(result, plan, paired_direction=paired_direction, primary_num=primary_num,
                          exposures=exposures, guard_num=guard_num)


def run_readout(con: sqlite3.Connection, prereg_record: object, post_map: object, *,
                expected_hash: str | None = None, attribution: dict | None = None) -> dict[str, Any]:
    """Verify the hash, convert, evaluate against the local DB and classify. Read-only.

    ``attribution`` (issue #16 R4) supplies per-post counts for a ``docs_visits`` or
    ``repo_stars_utm`` primary metric; see ``evaluate_attribution_experiment``.
    """
    plan = growth_plan_to_experiment(prereg_record, post_map, con, expected_hash=expected_hash,
                                     attribution=attribution)
    if attribution is not None:
        evaluation = evaluate_attribution_experiment(con, plan, attribution)
    else:
        evaluation = evaluate_experiment(con, plan)
    classified = classify_result(evaluation, plan)
    design = describe_design(plan["assignment"])
    explanations = list(classified["explanations"])
    if design["interpretation"] == "associational":
        explanations.append(f"design is {design['assignment']}: read any direction as an association only")
    return {
        "kind": "reachweaver.growth-readout", "experiment_id": plan["experiment_id"],
        "dimension": plan["dimension"], "plan_hash": plan["plan_hash"], "label": classified["label"],
        "direction": classified["direction"], "design": design, "flags": classified["flags"],
        "explanations": explanations, "pairs_planned": evaluation["pairs_planned"],
        "pairs_usable": classified["pairs_usable"], "pairs_skipped": evaluation["pairs_skipped"],
        "classification": classified, "evaluation": evaluation, "experiment_plan": plan,
        "warnings": plan["conversion"]["warnings"], "disclaimer": DISCLAIMER,
    }


def render_readout(result: dict[str, Any]) -> str:
    flags = result["flags"]
    lines = [f"READOUT {result['experiment_id']} ({result['dimension']}) plan {result['plan_hash']}",
             f"LABEL: {result['label']} (direction: {result['direction'] or 'none'})",
             f"DESIGN: {result['design']['assignment']} -> {result['design']['interpretation']}: "
             f"{result['design']['statement']}",
             f"PAIRS: {result['pairs_usable']} usable of {result['pairs_planned']} mapped; "
             f"{len(result['pairs_skipped'])} skipped",
             f"FLAGS: novelty_effect={'yes' if flags['novelty_effect'] else 'no'} "
             f"single_outlier={'yes' if flags['single_outlier'] else 'no'}"]
    lines += [f"- {item}" for item in result["explanations"]]
    lines += [f"SKIPPED {item['pair']}: {item['reason']}" for item in result["pairs_skipped"]]
    lines += [f"WARNING: {item}" for item in result["warnings"]]
    lines.append(f"DISCLAIMER: {DISCLAIMER}")
    return "\n".join(lines)


def load_results(path: str | Path) -> Any:
    """Read the JSON list of prior classified results for ``next_hypotheses`` (validated there)."""
    try:
        return read_json_file(Path(path))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid results JSON: {exc.msg}") from exc


READOUT_KIND = "reachweaver.growth-readout"
MAX_READOUT_FILES = 200
MAX_READOUT_BYTES = 5_000_000


def _readout_file(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_READOUT_BYTES:
        raise ValidationError(f"Readout {path} must be a regular file <= {MAX_READOUT_BYTES} bytes (no symlinks)")
    try:
        return read_json_file(path)
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValidationError(f"Readout {path} is not valid JSON") from exc


def results_from_readouts(files: list[Path] | None = None, folder: Path | None = None,
                          known_ids: set[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Results for ``next_hypotheses`` from saved ``experiment-readout --json`` files (read-only).

    ``experiment-readout`` and ``experiment-evaluate`` keep nothing on disk: the owner saves the readout JSON.
    ``files`` must each be such a readout (anything else is an error); in ``folder`` every ``*.json`` that is a
    readout is used and any other JSON file is listed as ignored. One experiment counts once: the readout with
    the most usable pairs wins (ties: the later file), and an id already in ``known_ids`` (the hand-made
    ``--results`` list) is kept from that list. Labels and directions are copied unchanged.
    """
    candidates: list[tuple[Path, bool]] = [(Path(p), True) for p in files or []]
    if folder is not None:
        folder = Path(folder)
        if folder.is_symlink() or not folder.is_dir():
            raise ValidationError(f"--readout-dir {folder} must be an existing directory (no symlink)")
        candidates += [(p, False) for p in sorted(folder.glob("*.json")) if not p.is_symlink() and p.is_file()]
    if len(candidates) > MAX_READOUT_FILES:
        raise ValidationError(f"At most {MAX_READOUT_FILES} readout files")
    chosen: dict[str, tuple[int, dict[str, Any]]] = {}
    ignored: list[dict[str, str]] = []
    for path, explicit in candidates:
        try:
            data = _readout_file(path)
        except ValidationError:
            if explicit:
                raise
            ignored.append({"file": path.name, "reason": "not valid JSON or too large"})
            continue
        if not isinstance(data, dict) or data.get("kind") != READOUT_KIND:
            if explicit:
                raise ValidationError(f"{path} is not saved 'experiment-readout --json' output ({READOUT_KIND})")
            ignored.append({"file": path.name, "reason": f"not an experiment-readout file ({READOUT_KIND})"})
            continue
        experiment_id, dimension, label = data.get("experiment_id"), data.get("dimension"), data.get("label")
        if not isinstance(experiment_id, str) or dimension not in DIMENSIONS or label not in LABELS:
            raise ValidationError(f"{path}: readout needs experiment_id, a known dimension and a known label")
        raw_pairs = data.get("pairs_usable")
        usable: int = raw_pairs if isinstance(raw_pairs, int) and not isinstance(raw_pairs, bool) else 0
        entry = {"experiment_id": experiment_id, "dimension": dimension, "label": label,
                 "direction": data.get("direction"), "readout_file": path.name, "pairs_usable": usable}
        if experiment_id in (known_ids or set()):
            ignored.append({"file": path.name, "reason": f"{experiment_id} is already in --results"})
            continue
        previous = chosen.get(experiment_id)
        if previous is not None:
            loser = previous[1] if usable >= previous[0] else entry
            ignored.append({"file": loser["readout_file"], "reason": f"another readout of {experiment_id} has "
                                                                     "at least as many usable pairs"})
            if usable < previous[0]:
                continue
        chosen[experiment_id] = (usable, entry)
    results = [entry for _, entry in chosen.values()]
    return results, {"readouts_used": [{k: r[k] for k in ("readout_file", "experiment_id", "dimension", "label",
                                                          "direction", "pairs_usable")} for r in results],
                     "readouts_ignored": ignored}


def render_next(result: dict[str, Any]) -> str:
    nxt = result["next"]
    lines = [f"NEXT: {nxt['dimension']} ({nxt['rule']}): {nxt['reason']}",
             f"RECORDED: {len(result['results'])} result(s); {len(result['negative_results'])} negative kept"]
    sources = result.get("sources")
    if sources:
        lines += [f"READOUT {r['readout_file']}: {r['experiment_id']} ({r['dimension']}, {r['label']}"
                  + (f", {r['direction']}" if r["direction"] else "") + f", {r['pairs_usable']} usable pairs)"
                  for r in sources["readouts_used"]]
        lines += [f"IGNORED {r['file']}: {r['reason']}" for r in sources["readouts_ignored"]]
    lines += [f"NEGATIVE {item['experiment_id']} ({item['dimension']}, {item['label']})"
              for item in result["negative_results"]]
    lines.append(result["note"])
    return "\n".join(lines)


# --- Seeded simulation -------------------------------------------------------------------

def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 50:  # normal approximation keeps large means fast; never negative
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, product = math.exp(-lam), 0, rng.random()
    while product > limit:
        k += 1
        product *= rng.random()
    return k


def _wilson(k: int, n: int, z: float = 1.959964) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def synthetic_pairs(rng: random.Random, n_pairs: int, true_lift: float, noise: float, *,
                    base_rate: float = 0.01, guardrail_rate: float = 0.02, impressions: int = 1000,
                    pair_sd: float = 0.3) -> list[dict[str, dict[str, int]]]:
    """One synthetic experiment: per pair, {control, treatment} snapshot counts.

    A shared pair factor (topic/week) multiplies both arms; each post adds log-normal
    ``noise`` and Poisson count noise. Only the primary rate carries ``true_lift``.
    """
    pairs = []
    for _ in range(n_pairs):
        pair_factor = math.exp(rng.gauss(0, pair_sd))
        snapshot: dict[str, dict[str, int]] = {}
        for arm, lift in (("control", 0.0), ("treatment", true_lift)):
            exposure = max(1, round(impressions * math.exp(rng.gauss(0, 0.25))))
            rate = base_rate * pair_factor * (1 + lift) * math.exp(rng.gauss(0, noise))
            guard = guardrail_rate * pair_factor * math.exp(rng.gauss(0, noise))
            snapshot[arm] = {"impressions": exposure, "clicks": _poisson(rng, rate * exposure),
                             "saves": _poisson(rng, guard * exposure)}
        pairs.append(snapshot)
    return pairs


def summarize_pairs(pairs: list[dict[str, dict[str, int]]], minimum_pairs: int) -> dict[str, Any]:
    """The pooled fields of ``evaluate_experiment`` (clicks/saves per impression), in memory."""
    totals = {arm: {k: sum(p[arm][k] for p in pairs) for k in ("impressions", "clicks", "saves")}
              for arm in ("control", "treatment")}
    results = [{"pair": f"pair[{i}]",
                "control_rate": round(p["control"]["clicks"] / p["control"]["impressions"], 6),
                "treatment_rate": round(p["treatment"]["clicks"] / p["treatment"]["impressions"], 6)}
               for i, p in enumerate(pairs)]
    out: dict[str, Any] = {"pairs_usable": len(pairs), "pairs_skipped": [], "pair_results": results,
                           "verdict": "inconclusive", "relative_lift": None, "guardrail_relative_change": None}
    r0, r1 = (totals[a]["clicks"] / totals[a]["impressions"] for a in ("control", "treatment"))
    g0, g1 = (totals[a]["saves"] / totals[a]["impressions"] for a in ("control", "treatment"))
    if len(pairs) >= minimum_pairs and r0 and g0:
        out["relative_lift"] = round((r1 - r0) / r0, 4)
        out["guardrail_relative_change"] = round((g1 - g0) / g0, 4)
    return out


def simulate_readout(seed: int, n_pairs: int, true_lift: float, noise: float, *, trials: int = 1000,
                     minimum_pairs: int | None = None, minimum_relative_lift: float = 0.1,
                     maximum_guardrail_drop: float = 0.1, base_rate: float = 0.01,
                     impressions: int = 1000, pair_sd: float = 0.3) -> dict[str, Any]:
    """Label distribution of ``classify_result`` over seeded synthetic experiments. Never stored."""
    if type(seed) is not int:
        raise ValidationError("seed must be an integer")
    if type(n_pairs) is not int or not 2 <= n_pairs <= 100:
        raise ValidationError("pairs must be an integer in [2, 100]")
    if type(trials) is not int or not 1 <= trials <= 20000:
        raise ValidationError("trials must be an integer in [1, 20000]")
    if not -0.9 <= true_lift <= 10 or not 0 <= noise <= 3 or not 0 <= pair_sd <= 3:
        raise ValidationError("lift must be in [-0.9, 10], noise and pair_sd in [0, 3]")
    if not 0 < base_rate <= 0.5 or type(impressions) is not int or not 10 <= impressions <= 10_000_000:
        raise ValidationError("base_rate must be in (0, 0.5] and impressions an integer in [10, 10000000]")
    minimum = n_pairs if minimum_pairs is None else minimum_pairs
    if type(minimum) is not int or not 2 <= minimum <= 100:
        raise ValidationError("minimum_pairs must be an integer in [2, 100]")
    plan = {"minimum_pairs": minimum, "minimum_relative_lift": minimum_relative_lift,
            "maximum_guardrail_drop": maximum_guardrail_drop}
    rng = random.Random(seed)
    counts = {label: 0 for label in LABELS}
    directions = {"positive": 0, "negative": 0}
    flags = {"novelty_effect": 0, "single_outlier": 0}
    lifts: list[float] = []
    for _ in range(trials):
        summary = summarize_pairs(synthetic_pairs(rng, n_pairs, true_lift, noise, base_rate=base_rate,
                                                  impressions=impressions, pair_sd=pair_sd), minimum)
        result = classify_result(summary, plan)
        counts[result["label"]] += 1
        if result["label"] == "directional" and result["direction"] in directions:
            directions[result["direction"]] += 1
        for name, fired in result["flags"].items():
            flags[name] += bool(fired)
        if isinstance(summary["relative_lift"], (int, float)):
            lifts.append(summary["relative_lift"])
    return {
        "kind": SIMULATION_KIND, "simulated": True, "seed": seed, "trials": trials,
        "parameters": {"pairs": n_pairs, "true_lift": true_lift, "noise": noise, "pair_sd": pair_sd,
                       "base_rate": base_rate, "impressions": impressions, **plan},
        "label_counts": counts,
        "label_rates": {k: round(v / trials, 4) for k, v in counts.items()},
        "label_rate_95ci": {k: _wilson(v, trials) for k, v in counts.items()},
        "directional_by_direction": directions,
        "flag_counts": flags,
        "estimated_lift": ({"n": len(lifts), "median": round(median(lifts), 4),
                            "p05": round(_quantile(lifts, 0.05), 4), "p95": round(_quantile(lifts, 0.95), 4)}
                           if lifts else {"n": 0}),
        "model": "per pair: shared log-normal topic/week factor (pair_sd); per post: log-normal noise, "
                 "log-normal impressions (sd 0.25), Poisson clicks and saves; only clicks carry true_lift",
        "note": "SIMULATED data from random.Random(seed); calibrates the labels, says nothing about any real "
                "account, and is never written to the database. The 95% intervals are Wilson intervals for "
                "the Monte Carlo label rates.",
    }


def render_simulation(result: dict[str, Any]) -> str:
    p = result["parameters"]
    lines = [f"SIMULATED seed={result['seed']} trials={result['trials']} pairs={p['pairs']} "
             f"true_lift={p['true_lift']} noise={p['noise']} (minimum_pairs={p['minimum_pairs']}, "
             f"min_lift={p['minimum_relative_lift']})"]
    for label in LABELS:
        low, high = result["label_rate_95ci"][label]
        lines.append(f"{label:<18} {result['label_counts'][label]:>6}  {result['label_rates'][label]:.4f} "
                     f"[95% CI {low:.4f}-{high:.4f}]")
    d = result["directional_by_direction"]
    lines.append(f"directional by direction: positive={d['positive']} negative={d['negative']}")
    lift = result["estimated_lift"]
    if lift["n"]:
        lines.append(f"estimated pooled lift: median={lift['median']} p05={lift['p05']} p95={lift['p95']} "
                     f"(n={lift['n']})")
    lines.append(result["note"])
    return "\n".join(lines)
