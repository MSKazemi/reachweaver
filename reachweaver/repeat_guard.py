"""Have we already shared this material on this platform? A read-only check against the local history.

Before a post is approved, ReachWeaver compares its material with what the same platform has already
received from this installation:

* the **video file** (SHA-256 of the exact bytes), so the same video is not posted twice;
* each **image file** (SHA-256 of the exact bytes);
* the **text** (word 3-gram Jaccard, the same measure ``campaign-check --history-db`` uses): identical wording is an
  exact repeat, 0.6 or more is similar;
* the **link** (canonical form, tracking parameters removed), as a reminder only.

History comes from two places: posts in the local database (published, in flight, unresolved, or still queued) and the
shared-material ledger, where ``mark-shared`` records what you posted by hand (manual export, a platform app).

Honest limits. Matching is exact for files: a re-encoded or re-cropped video has different bytes and is not caught,
and nothing is compared with what exists on the platform itself. A repeat the owner really wants (an evergreen
reminder) is allowed with ``approve --allow-repeat``, which is bound into that approval and records an audit event.
Findings never carry the earlier post's text, only its id, status and date.

Send time (owner-approved 2026-10-21, sheet item 1): ``storage.claim_sending`` re-runs :func:`blocking` under its lock
(``storage.repeat_send_problem``), so two copies approved before either was sent cannot both go out; only a bound
``--allow-repeat`` passes.

Pure functions over a SQLite connection, plus :func:`sha256_file` for files you name. No network, browser or
credential is involved.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .claims import DEFAULT_DUPLICATE_THRESHOLD, jaccard
from .storage import parse_utc, post_media, post_video, repeat_candidates, shared_entries, utc_now

SHARED_STATUSES = ("published", "sending", "needs_review")  # the platform has it, or may have it
QUEUED_STATUSES = ("draft", "approved", "scheduled")  # not sent yet
SIMILAR_THRESHOLD = DEFAULT_DUPLICATE_THRESHOLD
DEFAULT_DAYS = 365
DAYS_RANGE = (1, 3650)
MAX_FINDINGS = 20
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref_src", "igshid")

def sha256_file(path) -> str:
    """SHA-256 of a regular file's exact bytes, read in chunks. Symlinks and non-files are refused."""
    import hashlib
    from pathlib import Path
    from .policy import ValidationError
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValidationError(f"{target} is not a regular file")
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


KINDS = ("same_video", "same_image", "same_text", "similar_text", "same_link")
BLOCKING_KINDS = frozenset({"same_video", "same_image", "same_text"})


def canonical_link(url: str) -> str | None:
    """Lower-case host, no fragment, no tracking parameters, no trailing slash; None when not an http(s) link."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not k.lower().startswith(_TRACKING)))
    path = parts.path.rstrip("/") or ""
    return urlunsplit(("https", parts.hostname.lower(), path, query, ""))


def links_in(text: str, extra: str = "") -> list[str]:
    import re
    found = re.findall(r"https?://[^\s<>\"')\]]+", text or "")
    if extra:
        found.append(extra)
    seen: dict[str, None] = {}
    for url in found:
        canon = canonical_link(url.rstrip(".,;:!?"))
        if canon:
            seen[canon] = None
    return list(seen)


def material_of_post(post: Mapping) -> dict:
    """The comparable material of a stored post: text, image hashes, video hash, links."""
    video = post_video(post)
    return {"text": post["content"], "images": [item["sha256"] for item in post_media(post)],
            "video": video["sha256"] if video else None,
            "links": links_in(post["content"], post["source_url"] or "")}


def _finding(kind: str, strength: str, state: str, same_account: bool, source: str, ref: str,
             account_id: str | None, status: str, when: str | None) -> dict:
    return {"kind": kind, "strength": strength, "state": state, "same_account": same_account, "source": source,
            "ref": ref, "account_id": account_id, "status": status, "when": when}


def _compare(material: dict, prior: dict, *, kind_state: str, same_account: bool, source: str, ref: str,
             account_id: str | None, status: str, when: str | None, near_ok: bool) -> list[dict]:
    out = []

    def add(kind: str, strength: str) -> None:
        out.append(_finding(kind, strength, kind_state, same_account, source, ref, account_id, status, when))

    if material["video"] and material["video"] == prior.get("video"):
        add("same_video", "exact")
    if set(material["images"]) & set(prior.get("images") or ()):
        add("same_image", "exact")
    left, right = material.get("text") or "", prior.get("text") or ""
    if left and right:
        score = jaccard(left, right)
        if score >= 1.0:
            add("same_text", "exact")
        elif score >= SIMILAR_THRESHOLD and near_ok:
            add("similar_text", "similar")
    if set(material["links"]) & set(prior.get("links") or ()):
        add("same_link", "reminder")
    return out


def find_repeats(con, *, platform: str, account_id: str | None, material: dict, exclude_post_id: str | None = None,
                 days: int = DEFAULT_DAYS, now: str | None = None) -> list[dict]:
    """Findings against this platform's history, strongest first. Reads only."""
    if isinstance(days, bool) or not isinstance(days, int) or not DAYS_RANGE[0] <= days <= DAYS_RANGE[1]:
        raise ValueError(f"days must be an integer in {DAYS_RANGE[0]}..{DAYS_RANGE[1]}")
    cutoff = parse_utc((datetime.fromisoformat(parse_utc(now or utc_now())) - timedelta(days=days)).isoformat())
    findings: list[dict] = []
    for row in repeat_candidates(con, platform, exclude_post_id):
        state = "already_shared" if row["status"] in SHARED_STATUSES else "also_queued"
        try:
            prior = material_of_post(row)
        except Exception:  # a malformed stored attachment must not hide the other checks
            prior = {"text": row["content"], "images": [], "video": None, "links": []}
        recent = (row["activity_at"] or "") >= cutoff
        findings.extend(_compare(material, prior, kind_state=state, same_account=account_id is None or row["account_id"] == account_id,
                                 source="post", ref=row["id"], account_id=row["account_id"], status=row["status"],
                                 when=row["activity_at"], near_ok=recent))
    for entry in shared_entries(con, platform):
        prior = {"text": entry["content"], "images": entry["images"], "video": entry["video"],
                 "links": entry["links"]}
        same = account_id is None or entry["account_id"] is None or entry["account_id"] == account_id  # unknown: assume same
        recent = (entry["posted_at"] or entry["recorded_at"] or "") >= cutoff
        findings.extend(_compare(material, prior, kind_state="already_shared", same_account=same, source="ledger",
                                 ref=entry["id"], account_id=entry["account_id"], status="shared_by_hand",
                                 when=entry["posted_at"] or entry["recorded_at"], near_ok=recent))
    order = {"exact": 0, "similar": 1, "reminder": 2}
    findings.sort(key=lambda f: (f["state"] != "already_shared", order[f["strength"]], not f["same_account"],
                                 f["when"] or "", f["ref"]))
    return findings[:MAX_FINDINGS]


def findings_for_post(con, post: Mapping, days: int = DEFAULT_DAYS) -> list[dict]:
    """Findings for a stored post against its platform's history (read-only; never carries earlier text).

    A malformed stored attachment is refused elsewhere; the text is still compared."""
    from .policy import ValidationError
    try:
        material = material_of_post(post)
    except ValidationError:
        material = {"text": post["content"], "images": [], "video": None, "links": []}
    return find_repeats(con, platform=post["platform"], account_id=post["account_id"], material=material,
                        exclude_post_id=post["id"], days=days)


def blocking(findings: list[dict]) -> list[dict]:
    """Exact repeats of material this account has already shared (or may have shared). Approval refuses these."""
    return [f for f in findings if f["kind"] in BLOCKING_KINDS and f["strength"] == "exact"
            and f["state"] == "already_shared" and f["same_account"]]


_WORDS = {"same_video": "the same video file", "same_image": "the same image file", "same_text": "the same wording",
          "similar_text": "very similar wording", "same_link": "the same link"}
_WHERE = {"post": "post", "ledger": "an entry you recorded with mark-shared"}


def describe(finding: dict) -> str:
    who = "this account" if finding["same_account"] else f"another account ({finding['account_id'] or 'unknown'})"
    state = "already shared" if finding["state"] == "already_shared" else "also queued"
    when = f", {finding['when'][:10]}" if finding["when"] else ""
    return (f"{_WORDS[finding['kind']]} is {state} on this platform by {who} "
            f"({_WHERE[finding['source']]} {finding['ref']}, {finding['status']}{when})")


def summary(findings: list[dict]) -> dict:
    return {"blocking": len(blocking(findings)), "warnings": len(findings) - len(blocking(findings)),
            "findings": [{**f, "message": describe(f)} for f in findings]}
