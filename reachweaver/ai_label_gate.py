"""AI-content label precondition for posts with media (owner-approved 2026-10-21).

Security review: ``docs/SECURITY-REVIEW-PROPOSAL-ai-label-precondition.md`` (private), owner decisions D1-D4 and
D6; D5 (mapping an answer into TikTok ``is_aigc`` / YouTube ``containsSyntheticMedia``) is NOT part of this change.

The owner records declarations on a DRAFT with ``ai-declare`` (stored in three nullable ``posts`` columns):

* ``authorship``: ``human`` | ``ai_assisted`` | ``ai_generated`` (who wrote the text);
* ``realistic_synthetic_json``: canonical JSON ``{"<attachment sha256>": true|false}``, one owner answer per image or
  video, keyed by the attachment's SHA-256 so that a replaced file has no answer until the owner gives one;
* ``approved_ai_label``: ``applied`` | ``not_needed``, written by ``approve --ai-label`` only.

D1 + D2: ``approve`` of a post WITH media (images or a video) computes the ``ai-label-check`` platform decision
(``ai_labels.assess``, EU Art. 50 left out) from those bound values and the bundled registry. ``required`` or
``unknown`` refuses approval with ``ai_label_unanswered`` unless the owner passes ``--ai-label applied|not_needed``.
Text-only posts are never blocked; ``check`` and ``show`` print the advice.

D3: for a post with media whose declarations or answer are set, the approval hash binds them under the add-on tag
``approval/media-3`` (key ``ai_label_binding``, next to the media-1/media-2/video-1 base tag). With all three NULL
the payload is byte-identical to before, so an approval made by an older release keeps its hash. A text-only post
never carries the key. Changing a bound value after approval is a hash mismatch.

Send time (``claim_sending``, ``check_approval_binding``, ``schedule``, ``health``, dry runs): the same pure decision
is re-run on the bound values; a media post that needs an answer and has none bound is refused with
``ai_label_unanswered`` and stays approved/scheduled. This covers approvals made before this change (no
grandfathering: re-approve with an answer). The registry file itself is not bound: no fetch, no network.

D4: EU AI Act Art. 50 stays advisory (printed, never blocking). D6: ``health`` warns when the registry's checked
dates are older than ``REGISTRY_WARN_AFTER_DAYS`` and never blocks.

Honest limit: every input is an owner declaration. A bound answer proves the owner answered, not that the answer or
the platform label was correct. Nothing here sets a platform label, sends, uploads or reads a credential.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from .policy import ValidationError

AI_LABEL_TAG = "reachweaver.storage.approval/media-3"
FIELDS = ("authorship", "realistic_synthetic_json", "approved_ai_label")
AUTHORSHIP = ("human", "ai_assisted", "ai_generated")
ANSWERS = ("applied", "not_needed")
BLOCKING = ("required", "unknown")
UNANSWERED = "ai_label_unanswered"
TEXT_ONLY_ANSWER = ("ai_label_text_only: --ai-label applies to posts with images or a video; a text-only post "
                    "stays advisory (see check). Nothing approved")
REGISTRY_WARN_AFTER_DAYS = 90
MAX_DECLARATIONS = 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


# ---- stored values ---------------------------------------------------------------------------------------------

def _column(row: Any, name: str) -> Any:
    return row[name] if row is not None and name in row.keys() else None


def carries_media(row: Any) -> bool:
    """True when the row holds images (``media_json``) or a video (``video_json``), even a malformed one."""
    return bool(_column(row, "media_json")) or bool(_column(row, "video_json"))


def encode_realism(answers: dict[str, bool]) -> str | None:
    """Canonical JSON for ``realistic_synthetic_json``; None for no answers."""
    if not answers:
        return None
    if len(answers) > MAX_DECLARATIONS or not all(
            isinstance(k, str) and _SHA256.fullmatch(k) and isinstance(v, bool) for k, v in answers.items()):
        raise ValidationError("realistic_synthetic answers must map attachment SHA-256 values to true/false")
    return json.dumps(dict(sorted(answers.items())), sort_keys=True, separators=(",", ":"))


def decode_realism(text: object) -> dict[str, bool]:
    """Strict parse of ``realistic_synthetic_json``; NULL/empty -> {}. A malformed value raises."""
    if text in (None, ""):
        return {}
    try:
        data = json.loads(str(text))
    except ValueError:
        raise ValidationError("realistic_synthetic_json is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValidationError("realistic_synthetic_json must be a JSON object")
    encode_realism(data)  # same validation as a fresh value
    return dict(data)


def attachments(row: Any) -> list[dict]:
    """The row's images and video as ``ai_labels`` media items (with ``sha256``) and the owner's realism answers.

    A malformed media/video record or answer column yields no answer for the affected item, so the decision is
    ``unknown`` and fails closed; the approval checks refuse a malformed record on their own.
    """
    from .ai_labels import media_from_post
    try:
        decode_realism(_column(row, "realistic_synthetic_json"))
        return media_from_post(row)
    except ValidationError:
        pass
    # A malformed record or answer column: strip every answer so each item is decided as unknown.
    stripped = {key: row[key] for key in row.keys()}
    stripped["realistic_synthetic_json"] = None
    try:
        return media_from_post(stripped)
    except ValidationError:
        return [{"id": "media (malformed record)", "kind": "video" if _column(row, "video_json") else "image",
                 "sha256": None, "realistic_synthetic": None, "created_by": None, "targets": []}]


def bound_answer(row: Any) -> str | None:
    value = _column(row, "approved_ai_label")
    return value if value in ANSWERS else None


# ---- approval payload (D3) -------------------------------------------------------------------------------------

def payload_fields(row: Any) -> dict:
    """Keys ``approval_payload`` adds: only for a row with media and at least one declaration/answer set.

    The stored values are bound as they are (strings or None), so any later edit is a hash mismatch."""
    if not carries_media(row):
        return {}
    values = {name: _column(row, name) for name in FIELDS}
    if all(value in (None, "") for value in values.values()):
        return {}
    return {"ai_label_binding": AI_LABEL_TAG,
            "ai_label_declarations": {name: (None if value in (None, "") else str(value))
                                      for name, value in values.items()}}


# ---- decision (D1, D2) -----------------------------------------------------------------------------------------

def decision(row: Any, *, registry: dict | None = None, eu: bool = False) -> dict:
    """The ``ai-label-check`` target for this post's own platform, from its stored values. Pure apart from reading
    the bundled registry file; no network. An unusable registry yields ``unknown`` (fails closed)."""
    from .ai_labels import assess, load_registry
    authorship = _column(row, "authorship")
    target = {"id": str(row["id"]), "platform": str(row["platform"]),
              "authorship": authorship if authorship in AUTHORSHIP else None}
    try:
        registry = registry or load_registry()
        report = assess([target], attachments(row), registry=registry, eu=eu)
    except (ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        return {"decision": "unknown", "platform_decision": "unknown", "registry_error": str(exc) or type(exc).__name__,
                "what_to_tick": "The AI-disclosure registry could not be read; decide yourself.", "reasons": [],
                "report": None}
    result = report["targets"][0]
    entry = registry["platforms"].get(result["registry_key"]) if result["family"] == "platform" else None
    verified = result["family"] == "china" or bool(entry and entry["verification"] == "verified")
    return {**result, "report": report, "verified_rule": verified}


def problem(row: Any) -> str | None:
    """None, or ``ai_label_unanswered`` for a media post whose platform decision is required/unknown and which
    carries no valid ``approved_ai_label``. Text-only rows are never a problem."""
    if row is None or not carries_media(row) or bound_answer(row) is not None:
        return None
    return UNANSWERED if decision(row)["platform_decision"] in BLOCKING else None


def approve_check(row: Any, answer: str | None) -> tuple[str | None, str | None]:
    """``(decision, refusal)`` for approving ``row`` with ``answer`` (the ``--ai-label`` value or None).

    ``decision`` is None for a text-only post. ``refusal`` is the operator text when approval must be refused."""
    if answer is not None and answer not in ANSWERS:
        return None, f"--ai-label must be one of {', '.join(ANSWERS)}. Nothing approved"
    if not carries_media(row):
        return None, (TEXT_ONLY_ANSWER if answer is not None else None)
    found = decision(row)["platform_decision"]
    return found, (approve_refusal(row) if found in BLOCKING and answer is None else None)


def approve_refusal(row: Any) -> str:
    found = decision(row)
    pid = row["id"]
    why = "; ".join(r["why"] for r in found.get("reasons") or [] if r["decision"] in BLOCKING)[:600]
    return (f"{UNANSWERED}: post {pid} carries media and the AI-content label rule for {row['platform']} says "
            f"{found['platform_decision'].upper()}" + (f" ({why})" if why else "")
            + f". Look at reachweaver ai-label-check --post-id {pid} (what to tick: {found['what_to_tick']}); record "
            f"realism with reachweaver ai-declare {pid} --realistic-synthetic yes|no if that settles it; or answer "
            f"explicitly: reachweaver approve {pid} --ai-label applied (you label it yourself, in the platform's "
            "composer or the text; ReachWeaver sets no platform label) or --ai-label not_needed (you decided no "
            "label is needed). The answer is bound into the approval. Nothing approved")


def refusal_text(post_id: str) -> str:
    """Send-time text for ``ai_label_unanswered``; fixed strings plus the post id."""
    return (f"{UNANSWERED}: post {post_id} carries media whose AI-content label decision is required or unknown and "
            "no --ai-label answer is bound to its approval (approved before the precondition existed, or the "
            "bundled rules changed since); no grandfathering. Re-approve after review: "
            f"reachweaver unapprove {post_id}; reachweaver show {post_id}; "
            f"reachweaver approve {post_id} --ai-label applied|not_needed")


# ---- advice for check / show (D1 text, D4 EU) -------------------------------------------------------------------

def advice(row: Any) -> dict | None:
    """Read-only advice for ``check`` / ``show``: None for a text-only post with no AI authorship recorded."""
    media = carries_media(row)
    authorship = _column(row, "authorship")
    if not media and authorship not in ("ai_assisted", "ai_generated"):
        return None
    found = decision(row)
    answer = bound_answer(row)
    platform_decision = found["platform_decision"]
    if not media:
        gate = "advisory only: a text-only post is never blocked"
    elif answer:
        gate = f"answered: --ai-label {answer} (bound, approval/media-3)"
    elif platform_decision in BLOCKING:
        if not found.get("verified_rule"):
            settles = "no verified rule is recorded for this platform, so only the answer settles it"
        elif platform_decision == "required":
            settles = "the recorded rule asks for a label: label it yourself, then answer applied"
        else:
            settles = "or record realism with ai-declare --realistic-synthetic yes|no"
        gate = f"approve needs --ai-label applied|not_needed ({settles})"
    else:
        gate = "no answer needed for approval"
    report = found.get("report") or {}
    eu = (report.get("eu_ai_act_art50") or {}).get("hint")
    view = {"decision": platform_decision, "platform": row["platform"],
            "authorship": authorship if authorship in AUTHORSHIP else None,
            "media": [{"id": item["id"], "realistic_synthetic": item["realistic_synthetic"],
                       "created_by": item["created_by"]} for item in attachments(row)],
            "what_to_tick": found["what_to_tick"], "approval": gate,
            "eu_ai_act_art50": (eu + " Advisory only, never blocks; not legal advice.") if eu else None,
            "registry_checked_on": report.get("registry_checked_on"),
            "command": f"reachweaver ai-label-check --post-id {row['id']}"}
    if found.get("registry_error"):
        view["registry_error"] = found["registry_error"]
    return view


def answer_text(value: object) -> str:
    return "yes" if value is True else "no" if value is False else "unanswered"


def advice_lines(view: dict | None) -> list[str]:
    if not view:
        return []
    lines = [f"AI label ({view['platform']}): {view['decision']}; {view['approval']}",
             f"  Tick: {view['what_to_tick']}"]
    if view["media"]:
        lines.append("  Realistic synthetic: " + ", ".join(f"{item['id']} {answer_text(item['realistic_synthetic'])}"
                                                           for item in view["media"]))
    if view.get("eu_ai_act_art50"):
        lines.append(f"  EU: {view['eu_ai_act_art50']}")
    lines.append(f"  Detail: {view['command']}")
    return lines


# ---- registry age (D6) -----------------------------------------------------------------------------------------

def registry_age(today: date | None = None) -> dict:
    """Age of the oldest checked date in ``ai_disclosure_registry.json``; ``warn`` after 90 days. Never blocks."""
    from .ai_labels import load_registry
    current = today or date.today()
    try:
        data = load_registry()
    except (ValidationError, OSError, ValueError) as exc:
        return {"status": "invalid", "error": str(exc) or type(exc).__name__, "checked_on": None,
                "oldest_checked_on": None, "age_days": None, "warn_after_days": REGISTRY_WARN_AFTER_DAYS}
    dates = [data["checked_on"], *(src["checked_on"] for src in data["sources"].values())]
    oldest = min(dates)
    age = (current - date.fromisoformat(oldest)).days
    return {"status": "aging" if age > REGISTRY_WARN_AFTER_DAYS else "ok", "checked_on": data["checked_on"],
            "oldest_checked_on": oldest, "age_days": age, "warn_after_days": REGISTRY_WARN_AFTER_DAYS,
            "command": "reachweaver ai-label-check (re-verify the cited pages, then update the registry)"}


# ---- owner declarations (ai-declare) ---------------------------------------------------------------------------

def declare(con, post_id: str, *, authorship: str | None = None, realistic: str | None = None,
            attachment: int | None = None) -> Any:
    """Record the owner's declarations on a DRAFT and return the updated row. ``authorship``: one of ``AUTHORSHIP`` or ``clear``;
    ``realistic``: ``yes`` | ``no`` | ``clear`` for every attachment, or only ``attachment`` (1-based).

    The approval binds these values for a post with media, so they can only change on a draft. The audit event
    carries field names, the answer and SHA-256 prefixes only."""
    from .storage import _atomic, event, get_post
    if authorship is None and realistic is None:
        raise ValidationError("nothing to declare: pass --authorship and/or --realistic-synthetic")
    if authorship is not None and authorship not in (*AUTHORSHIP, "clear"):
        raise ValidationError(f"--authorship must be one of {', '.join(AUTHORSHIP)} or clear")
    if realistic is not None and realistic not in ("yes", "no", "clear"):
        raise ValidationError("--realistic-synthetic must be yes, no or clear")
    if attachment is not None and realistic is None:
        raise ValidationError("--attachment needs --realistic-synthetic")
    with _atomic(con):
        row = get_post(con, post_id)
        if row["status"] != "draft":
            raise ValidationError(f"AI declarations can only change on a draft (post {post_id} is {row['status']}); "
                                  f"the approval binds them: run 'reachweaver unapprove {post_id}' first")
        if any(name not in row.keys() for name in FIELDS):
            raise ValidationError("database lacks the AI-declaration columns; open it with storage.connect()")
        detail: list[str] = []
        if authorship is not None:
            value = None if authorship == "clear" else authorship
            con.execute("UPDATE posts SET authorship=? WHERE id=? AND status='draft'", (value, post_id))
            detail.append(f"authorship={value or 'cleared'}")
        if realistic is not None:
            items = [item for item in attachments(row) if item["sha256"]]
            if not items:
                raise ValidationError(f"post {post_id} has no image or video to declare; attach one first")
            if attachment is not None and not 1 <= attachment <= len(items):
                raise ValidationError(f"--attachment must be 1..{len(items)} for post {post_id}")
            chosen = items if attachment is None else [items[attachment - 1]]
            current = {item["sha256"]: item["realistic_synthetic"] for item in items
                       if item["realistic_synthetic"] is not None}  # answers for removed files are dropped
            for item in chosen:
                if realistic == "clear":
                    current.pop(item["sha256"], None)
                else:
                    current[item["sha256"]] = realistic == "yes"
            con.execute("UPDATE posts SET realistic_synthetic_json=? WHERE id=? AND status='draft'",
                        (encode_realism(current), post_id))
            detail.append(f"realistic_synthetic={realistic} "
                          + ",".join(str(item["sha256"])[:12] for item in chosen))
        event(con, "ai_declaration", post_id, " ".join(detail))
        return get_post(con, post_id)
