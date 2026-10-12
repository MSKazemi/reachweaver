"""Offline, cautious readout of sequential organic social experiments.

This cannot establish randomization or causality. It compares documented native
snapshots at matched post ages, enforces missing-data rules, and reports coverage.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .growth import classify_result, is_windowed_snapshot
from .policy import ValidationError
from .storage import METRIC_FIELDS, get_post, parse_utc, snapshots_for


def _check_plan(plan):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValidationError("Experiment must be a schema_version 1 JSON object")
    for field in ("experiment_id", "hypothesis", "changed_variable", "pre_registered_at"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise ValidationError(f"Experiment {field} required")
    parse_utc(plan["pre_registered_at"])
    metric = plan.get("primary_metric")
    guardrail = plan.get("guardrail_metric")
    denom = plan.get("denominator")
    if metric not in METRIC_FIELDS or guardrail not in METRIC_FIELDS or metric == guardrail:
        raise ValidationError("Primary and guardrail must be distinct supported metrics")
    if denom not in ("none", "views", "impressions") or denom in (metric, guardrail):
        raise ValidationError("Denominator must be none/views/impressions, distinct from both metrics")
    pairs = plan.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValidationError("Experiment needs at least one paired comparison")
    ids = []
    for pair in pairs:
        if not isinstance(pair, dict) or not all(isinstance(pair.get(k), str) and pair[k] for k in ("control", "treatment")):
            raise ValidationError("Each pair requires control and treatment post IDs")
        ids.extend((pair["control"],pair["treatment"]))
    if len(ids) != len(set(ids)):
        raise ValidationError("Experiment cannot reuse a post across pairs")
    for field, lower, upper in (("minimum_pairs",2,100),("metric_age_hours",24,720),("age_tolerance_hours",1,168)):
        value = plan.get(field)
        if type(value) is not int or not lower <= value <= upper:
            raise ValidationError(f"{field} must be an integer in [{lower}, {upper}]")
    if plan["age_tolerance_hours"] >= plan["metric_age_hours"]:
        raise ValidationError("Age tolerance must be smaller than metric age")
    for field in ("minimum_relative_lift", "maximum_guardrail_drop"):
        value = plan.get(field)
        if type(value) not in (int,float) or not 0 <= value <= 1:
            raise ValidationError(f"{field} must be in [0,1]")


def _snapshot_at_age(con, post, primary, guardrail, denominator, target_hours, tolerance_hours, windowed=None):
    started = datetime.fromisoformat(parse_utc(post["send_started_at"]))
    target = started + timedelta(hours=target_hours)
    candidates = snapshots_for(con, post["id"])  # exact counts only (#15)
    found = []
    for item in candidates:
        if is_windowed_snapshot(item):  # windowed counts are not cumulative at post age
            if windowed is not None:
                windowed[0] += 1
            continue
        observed = datetime.fromisoformat(parse_utc(item["observed_at"]))
        distance = abs((observed-target).total_seconds())/3600
        if (distance > tolerance_hours or item[primary] is None or item[guardrail] is None
                or (denominator != "none" and (item[denominator] is None or item[denominator] == 0))):
            continue
        found.append((distance,item["observed_at"],item))
    if not found:
        return None
    row = min(found,key=lambda x:(x[0],x[1]))[2]
    return {"primary":row[primary],"guardrail":row[guardrail],
            "exposure":row[denominator] if denominator != "none" else 1,
            "source":row["source"],"observed_at":row["observed_at"]}


def evaluate_experiment(con, plan: dict) -> dict:
    _check_plan(plan)
    used, skipped, paired_direction, primary_num = [], [], [], [0,0]
    cohort = None
    exposures = [0,0]
    windowed_skipped = [0]
    guard_num = [0,0]
    prereg = datetime.fromisoformat(parse_utc(plan["pre_registered_at"]))
    warnings = ["Sequential organic posts are observational: timing and algorithmic allocation confound comparisons.",
                "The preregistration date is user supplied, not an independently timestamped preregistration."]
    for index,pair in enumerate(plan["pairs"]):
        label = f"pair[{index}]"
        control, treatment = (get_post(con,pair[k]) for k in ("control","treatment"))
        if any(p["status"] != "published" or not p["send_started_at"] for p in (control,treatment)):
            skipped.append({"pair":label,"reason":"not_both_published_with_send_timestamp"})
            continue
        if control["platform"] != treatment["platform"] or control["account_id"] != treatment["account_id"]:
            skipped.append({"pair":label,"reason":"platform_or_account_mismatch"})
            continue
        pair_cohort = (control["platform"],control["account_id"])
        if cohort is not None and pair_cohort != cohort:
            skipped.append({"pair":label,"reason":"different_platform_or_account_cohort"})
            continue
        if any(datetime.fromisoformat(parse_utc(p["send_started_at"])) < prereg for p in (control,treatment)):
            skipped.append({"pair":label,"reason":"first_publication_predates_declared_plan"})
            continue
        data=[]
        for post in (control,treatment):
            snapshot = _snapshot_at_age(con,post,plan["primary_metric"],plan["guardrail_metric"],
                                        plan["denominator"],plan["metric_age_hours"],plan["age_tolerance_hours"],windowed_skipped)
            if snapshot is None:
                break
            data.append(snapshot)
        if len(data) != 2:
            skipped.append({"pair":label,"reason":"missing_comparable_snapshot"})
            continue
        if data[0]["source"] != data[1]["source"]:
            skipped.append({"pair":label,"reason":"different_native_metric_sources"})
            continue
        cohort = pair_cohort
        cp,treat = data
        cr=cp["primary"]/cp["exposure"]
        tr=treat["primary"]/treat["exposure"]
        paired_direction.append(tr > cr)
        used.append({"pair":label,"account_id":control["account_id"],"platform":control["platform"],
                     "control":control["id"],"treatment":treatment["id"],
                     "control_rate":round(cr,6),"treatment_rate":round(tr,6),
                     "source_control":cp["source"],"source_treatment":treat["source"]})
        for i,p in enumerate(data):
            primary_num[i]+=p["primary"]
            exposures[i]+=p["exposure"]
            guard_num[i]+=p["guardrail"]
    complete=len(used)
    if windowed_skipped[0]:
        warnings.append(f"{windowed_skipped[0]} windowed snapshot(s) (window_start/window_end) were skipped: "
                        "windowed counts are not cumulative at post age")
    result={"experiment_id":plan["experiment_id"],"primary_metric":plan["primary_metric"],
            "guardrail_metric":plan["guardrail_metric"],"denominator":plan["denominator"],
            "metric_age_hours":plan["metric_age_hours"],"pairs_planned":len(plan["pairs"]),
            "pairs_usable":complete,"pairs_skipped":skipped,"pair_results":used,
            "verdict":"inconclusive", "relative_lift":None,"guardrail_relative_change":None,
            "direction_agreement": None,"warnings":warnings,"windowed_snapshots_skipped":windowed_skipped[0],
            "note":"Directional association only; never causal or statistically significant by itself."}
    if not complete:
        result["reason"]="no_comparable_data"
        return result
    r0,r1=(primary_num[i]/exposures[i] for i in (0,1))
    g0,g1=(guard_num[i]/exposures[i] for i in (0,1))
    result["direction_agreement"]=round(sum(paired_direction)/complete,4)
    result["control_rate"]=round(r0,6)
    result["treatment_rate"]=round(r1,6)
    if complete < plan["minimum_pairs"] or r0 == 0 or g0 == 0:
        result["reason"]="insufficient_pairs_or_zero_baseline"
        return result
    lift=(r1-r0)/r0
    guard=(g1-g0)/g0
    result["relative_lift"]=round(lift,4)
    result["guardrail_relative_change"]=round(guard,4)
    # Same minimum-pairs / sign-test / outlier gate as the growth readout: a tiny sample can
    # never earn a directional verdict, in either direction.
    gate = classify_result(result, plan)
    result["readout_label"] = gate["label"]
    result["readout_explanations"] = gate["explanations"]
    verdict = "no_consistent_win"
    if gate["label"] == "directional":
        if (lift >= plan["minimum_relative_lift"] and result["direction_agreement"] >= 2/3
                and guard >= -plan["maximum_guardrail_drop"]):
            verdict = "directional_positive"
        elif lift <= -plan["minimum_relative_lift"] and (1 - result["direction_agreement"]) >= 2/3:
            verdict = "directional_negative"
    result["verdict"] = verdict
    return result
