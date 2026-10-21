"""Human-readable review sheet for one post (``show`` at a terminal) and the length facts the dashboard shares.

``show`` prints JSON when piped (unchanged, for scripts). At a terminal it prints this sheet: the
exact text in a box, a length meter against the platform limit, the approval state in a sentence,
attachments in one line each and the commands you could run next. Everything here is derived from the
dict ``post_view.build_post_view`` returns; nothing is read, written or sent.
"""
from __future__ import annotations

import textwrap

from . import term
from .results import account_label

METER_WIDTH = 20
# platform_preview keys that carry the platform's own count and limit, per platform.
LENGTH_KEYS = {
    "bluesky": ("graphemes", "grapheme_limit", "graphemes"),
    "mastodon": ("counted_length", "text_limit", "characters"),
    "linkedin": ("counted_length", "text_limit", "characters"),
}


def length_of(view: dict) -> dict | None:
    """{"used", "limit", "unit", "ratio", "state"} from the platform preview, or None when unknown.

    ``state`` is ``ok`` (under 90%), ``tight`` (90-100%) or ``over``. Only a platform that reports its own
    count and limit gets a meter; for any other the length is simply not shown (never guessed)."""
    keys = LENGTH_KEYS.get(str(view.get("platform")))
    preview = view.get("platform_preview") or {}
    if not keys or preview.get(keys[0]) is None or not preview.get(keys[1]):
        return None
    used, limit = int(preview[keys[0]]), int(preview[keys[1]])
    ratio = used / limit
    return {"used": used, "limit": limit, "unit": keys[2], "ratio": round(ratio, 3),
            "state": "over" if used > limit else "tight" if ratio >= 0.9 else "ok"}


def meter(length: dict, stream) -> str:
    filled = min(METER_WIDTH, round(length["ratio"] * METER_WIDTH))
    colour = {"ok": "green", "tight": "yellow", "over": "red"}[length["state"]]
    bar = term.style("█" * filled, colour, stream) + term.style("░" * (METER_WIDTH - filled), "grey", stream)
    return f"{bar} {length['used']}/{length['limit']} {length['unit']}"


def approval_sentence(view: dict) -> str:
    approval = view.get("approval") or {}
    status, binding = view["status"], approval.get("binding")
    who = approval.get("approved_by")
    digest = (approval.get("payload_sha256") or "")[:12]
    if status in ("approved", "scheduled"):
        if binding == "bound":
            return f"Approved by {who or 'someone'}; the approval matches this exact text" + (f" (hash {digest}…)" if digest else "")
        return f"Approval no longer matches this post ({binding}); approve it again after reviewing"
    if status == "draft":
        return "Not approved yet. Nothing can be sent until you approve this exact text"
    if status == "needs_review":
        return "A send ended in an unknown state; check the native platform before anything else"
    if status == "published":
        return "Published"
    if status == "published_manual":
        return "Posted by hand: ReachWeaver never sent it and can never approve, schedule or send it"
    if status == "cancelled":
        return "Cancelled; it will never be sent"
    if status == "sending":
        return "A send is in progress or was interrupted"
    return status


def _box(text: str, width: int) -> list[str]:
    inner = max(20, width - 4)
    wrapped: list[str] = []
    for paragraph in term.clean(text).split("\n"):
        wrapped += textwrap.wrap(paragraph, inner, replace_whitespace=False, drop_whitespace=False) or [""]
    pad = max(len(line) for line in wrapped)
    top = "┌" + "─" * (pad + 2) + "┐"
    return [top, *[f"│ {line.ljust(pad)} │" for line in wrapped], "└" + "─" * (pad + 2) + "┘"]


def render_sheet(view: dict, steps: list[dict], stream, *, columns: int | None = None) -> str:
    columns = min(columns or term.terminal_columns(stream), 100)
    status = view["status"]
    colour = term.STATUS_COLOUR.get(status, "grey")
    head = f"POST {term.clean(view['id'])}  " + term.style(f"[{status.replace('_', ' ')}]", colour, stream)
    lines = [head,
             f"{account_label(view['account_id'])} on {view['platform']}"
             + (f" · campaign {view['campaign']}" if view.get("campaign") else ""),
             "", term.style("EXACT TEXT", "bold", stream), *_box(view["content"], columns)]
    length = length_of(view)
    if length:
        lines.append("Length  " + meter(length, stream) + (term.style("  OVER THE LIMIT", "red", stream) if length["state"] == "over" else ""))
    if "results" in view:
        lines += ["", term.style("RESULTS", "bold", stream), *results_lines(view["results"])]
    lines += ["", term.style("APPROVAL", "bold", stream), approval_sentence(view)]
    options = (view.get("approval") or {}).get("send_options")
    if isinstance(options, dict):  # sheet items 5/9: Mastodon visibility/language/CW, Postiz server/integration
        state = str(options.get("state", ""))
        changed = state.startswith("approval_send_options_")
        lines.append(term.style(f"Send options: {term.clean(str(options.get('values', '')))} ({state})",
                                "red" if changed else "grey", stream))
    if (view.get("approval") or {}).get("allow_repeat"):
        lines.append("Repeat override: approved with --allow-repeat")
    if view.get("ai_label"):  # AI-content label advice (D1/D4): read-only; the approve precondition is in storage
        from .ai_label_gate import advice_lines
        lines += [term.clean(line) for line in advice_lines(view["ai_label"])]
    if view.get("scheduled_at"):
        lines.append(f"Due: {view['scheduled_at']}")
    for key, label in (("media", "Images"), ("video", "Video"), ("reply", "Reply")):
        if key in view:
            lines.append(f"{label}: " + _one_line(view[key]))
    repeat = view.get("repeat_check") or {}
    if repeat.get("findings"):
        lines.append(term.style(f"Repeat check: {repeat.get('blocking', 0)} blocking, {repeat.get('warnings', 0)} warning(s)", "yellow", stream))
        lines += [f"  {term.clean(str(item.get('message', '')))}" for item in repeat["findings"][:3]]
    preview = view.get("platform_preview") or {}
    if preview.get("handle"):
        lines.append(f"Posts as: {preview['handle']}")
    if view.get("source_url"):
        lines.append(f"Source: {view['source_url']}")
    if steps:
        lines += ["", term.style("NEXT (run in your terminal; nothing below runs by itself)", "bold", stream)]
        for step in steps:
            lines += [f"  {step['label']}", f"    {step['command']}"]
    lines += ["", "Full details as JSON: reachweaver --plain show " + view["id"]]
    return "\n".join(lines)


def results_lines(results: dict) -> list[str]:
    """The RESULTS block: the latest numbers per source, with when and how long after posting."""
    from .results import numbers_text
    latest = results.get("latest_by_source") or []
    if not latest:
        return ["No numbers recorded yet. When you have them:", f"  {results['record_more']}"]
    lines = []
    for item in latest[:5]:
        age = item.get("hours_after_posting")
        when = f"{str(item['observed_at'])[:16].replace('T', ' ')} UTC" + (
            f", {age} hours after posting" if isinstance(age, int) and age >= 0 else "")
        numbers = numbers_text(item["numbers"], ("likes", "replies", "reposts", "views", "impressions", "saves",
                                                 "clicks", "signups"))
        other = ", ".join(f"{key} {label}" for key, label in sorted(item.get("other_states", {}).items()))
        extra = f" ({other})" if other else ""
        lines.append(term.clean(f"{numbers}{extra}  [{when}; source '{item['source']}']"))
    lines.append(f"Record newer numbers: {results['record_more']}")
    return lines


def _one_line(value) -> str:
    if isinstance(value, dict):
        if "error" in value or "invalid" in value:
            return "problem: " + str(value.get("error") or value.get("invalid"))
        if "count" in value:
            return f"{value['count']} attached"
        if "record" in value:
            return "attached"
        return "present"
    return str(value)
