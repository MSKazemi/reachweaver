"""``reachweaver context``: one compact, read-only session-start summary for an agent (Claude Code).

It replaces the 4-6 calls a session used to start with (``doctor``, ``start``, ``health``, ``approval-mode``,
``list``...) by composing what those already compute offline:

* version, install kind and whether a plugin SessionStart hook already ran ``update --notice``;
* paths of the workspace, database, accounts file and profile (paths only, never contents);
* each account's offline readiness (``connect`` status rows) and persona, with credential variable NAMES only;
* the emergency pause, whether a live send is possible in this shell (booleans, never an environment value),
  the approval safeguards (read without the approval key), post counts by status and the posts waiting for
  the owner (approved but unsent, needs_review, sending, approvals needing re-approval);
* the next steps of ``start`` (``owner_runs`` marks owner-only and interactive ones) and the top offline
  ``suggest`` items;
* ``owner_only_commands`` from the command catalog (``catalog.py``): never run them.

The database is opened read-only (``mode=ro``) and only when it already exists; a missing one is reported,
never created. Nothing is sent, fetched, approved, scheduled or written; the document is redacted.
"""
from __future__ import annotations

from pathlib import Path

from . import catalog, home
from .accounts import accounts_path as resolve_accounts_path
from .accounts import load_accounts
from .policy import ValidationError, live_opt_in, runtime_environment
from .privacy import redact_value

SCHEMA_VERSION = 1
ID_LIMIT = 10
SUGGESTION_LIMIT = 3
NEEDS_OWNER = (("approved_not_sent", ("approved", "scheduled")), ("needs_review", ("needs_review",)),
               ("sending", ("sending",)))
NOTE = ("Read-only: no database was created or changed, nothing was sent or fetched, and no credential value "
        "was printed. Owner-only commands are for the owner's own terminal.")


def _ids(rows: list[str]) -> dict:
    """Count plus the first ``ID_LIMIT`` ids (oldest first); ``truncated`` appears only when ids were cut."""
    item: dict = {"count": len(rows), "ids": rows[:ID_LIMIT]}
    if len(rows) > ID_LIMIT:
        item["truncated"] = True
    return item


def _accounts_section(accounts: dict, error: str | None, profile: dict | None, disconnected: list[str]) -> dict:
    from .capabilities import load_registry
    from .connect_guide import account_rows
    from .persona_guard import account_persona
    items = []
    if accounts:
        try:
            registry = load_registry()
        except ValidationError as exc:  # a broken bundled registry degrades this section, not the call
            return {"error": error or str(exc), "items": []}
        for row in account_rows(accounts, registry, disconnected=disconnected):
            items.append({"id": row["account"], "platform": row["platform"], "adapter": row["adapter"],
                          "state": row["state"], "persona": account_persona(accounts[row["account"]], profile),
                          "missing": list(row["missing"])})
    return {"error": error, "items": items}


def _update_notice() -> dict:
    from .updater import install_kind
    kind = install_kind()
    hook = kind == "plugin"
    return {"install_kind": kind, "session_hook": hook,
            "advice": ("The plugin's SessionStart hook already ran 'update --notice'; do not run it again."
                       if hook else "Run 'update --notice' once this session if no update notice appeared yet.")}


def _database_section(database: Path) -> tuple[dict | None, str | None]:
    """(facts, error) from an EXISTING database opened read-only; (None, None) when there is none."""
    if not database.is_file():
        return None, None
    from . import confirmation
    from .disconnect import disconnected_accounts
    from .storage import approval_rebind_required, is_paused, list_posts, offline_key_mode
    from .ui_data import open_database, suggestions_view
    try:
        with open_database(database) as con:
            rows = list_posts(con)
            counts: dict[str, int] = {}
            for row in rows:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            created = sorted(rows, key=lambda r: (r["created_at"] or "", r["id"]))
            needs: dict[str, object] = {key: _ids([r["id"] for r in created if r["status"] in statuses]) for key, statuses in NEEDS_OWNER}
            mode = offline_key_mode(con)  # never reads the approval key
            needs["rebind_required"] = _ids([item["post_id"] for item in approval_rebind_required(con, mode)])
            needs["drafts"] = counts.get("draft", 0)
            try:
                suggestions = [{"priority": a["priority"], "title": a["title"], "next_step": a["next_step"]}
                               for a in suggestions_view(con)["actions"][:SUGGESTION_LIMIT]]
            except ValidationError:
                suggestions = []
            facts = {"paused": is_paused(con), "disconnected": disconnected_accounts(con),
                     "approval": {"confirmation": confirmation.confirmation_mode(con) or "off",
                                  "identity_required": bool(mode.identity_required),
                                  "keyed_binding_latched": bool(mode.latched),
                                  "max_age_hours": mode.max_age_hours},
                     "posts": {"total": len(rows), "by_status": dict(sorted(counts.items()))},
                     "needs_owner": needs, "suggestions": suggestions}
    except ValidationError as exc:
        return None, str(exc)
    return facts, None


def build_context(db: str | Path | None, accounts_arg: str | None, profile_arg: str | None, *, env=None) -> dict:
    from . import __version__
    from . import profile as profile_store
    from .start import build_guide
    from .storage import database_path
    database = database_path(db)
    accounts_file = resolve_accounts_path(accounts_arg)
    profile_file = profile_store.profile_path(profile_arg)
    try:
        accounts, accounts_error = load_accounts(accounts_arg), None
    except ValidationError as exc:
        accounts, accounts_error = {}, str(exc)
    try:
        profile, profile_error = profile_store.load_optional(profile_arg), None
    except ValidationError as exc:
        profile, profile_error = None, str(exc)
    facts, database_error = _database_section(database)
    guide = build_guide(db, accounts_arg)
    allow_live = live_opt_in(env)
    env_is_prod = runtime_environment(env) == "prod"
    document = {
        "context": "reachweaver_context", "schema_version": SCHEMA_VERSION, "version": __version__,
        "update": _update_notice(),
        "paths": {"workspace": str(home.root()) if home.root() else None, "database": str(database),
                  "database_exists": database.is_file(), "accounts_file": str(accounts_file),
                  "accounts_file_exists": accounts_file.is_file(), "profile_file": str(profile_file),
                  "profile_file_exists": profile_file.is_file()},
        "state": guide["state"], "database_error": database_error, "profile_error": profile_error,
        "accounts": _accounts_section(accounts, accounts_error, profile, facts["disconnected"] if facts else []),
        "safety": {"emergency_paused": facts["paused"] if facts else None,
                   "live_gate": {"allow_live_set": allow_live, "env_is_prod": env_is_prod,
                                 "live_possible_in_this_shell": allow_live and env_is_prod}},
        "approval": facts["approval"] if facts else None,
        "posts": facts["posts"] if facts else None,
        "needs_owner": facts["needs_owner"] if facts else None,
        "next_actions": [{"label": step["label"], "command": step["command"],
                          "owner_runs": catalog.owner_runs(step["command"])} for step in guide["steps"]],
        "suggestions": facts["suggestions"] if facts else [],
        "owner_only_commands": {"note": catalog.OWNER_ONLY_NOTE, "commands": catalog.owner_only_commands()},
        "note": NOTE,
    }
    return redact_value(document)


def render_text(context: dict) -> str:
    """A short summary for a person."""
    lines = [f"ReachWeaver {context['version']} ({context['update']['install_kind']}); state: {context['state']}"]
    paths = context["paths"]
    lines.append(f"Database: {paths['database']}" + ("" if paths["database_exists"] else " (not created yet)"))
    if context["database_error"]:
        lines.append(f"  problem: {context['database_error']}")
    accounts = context["accounts"]
    if accounts["error"]:
        lines.append(f"Accounts: {accounts['error']}")
    for item in accounts["items"]:
        lines.append(f"  {item['id']}: {item['platform']}, {item['state'].replace('_', ' ')}"
                     + (f", persona {item['persona']}" if item["persona"] else "")
                     + (f", missing {', '.join(item['missing'])}" if item["missing"] else ""))
    safety = context["safety"]
    gate = safety["live_gate"]
    pause = {None: "unknown (no database)", True: "ON", False: "off"}[safety["emergency_paused"]]
    lines.append(f"Emergency pause: {pause}; live send possible in this shell: "
                 f"{'yes' if gate['live_possible_in_this_shell'] else 'no'}")
    if context["approval"]:
        approval = context["approval"]
        lines.append(f"Approval: confirmation {approval['confirmation']}, identity "
                     f"{'required' if approval['identity_required'] else 'off'}, keyed binding "
                     f"{'latched' if approval['keyed_binding_latched'] else 'off'}, max age "
                     f"{approval['max_age_hours'] if approval['max_age_hours'] is not None else 'off'}")
    if context["posts"]:
        counts = context["posts"]["by_status"]
        lines.append(f"Posts: {context['posts']['total']}" + (" (" + ", ".join(f"{n} {k}" for k, n in counts.items())
                                                           + ")" if counts else ""))
        needs = context["needs_owner"]
        waiting = [f"{needs[key]['count']} {key.replace('_', ' ')}" for key in
                   ("approved_not_sent", "needs_review", "sending", "rebind_required") if needs[key]["count"]]
        if waiting:
            lines.append("Waiting for the owner: " + ", ".join(waiting))
    if context["next_actions"]:
        lines.append("Next:")
        lines += [f"  {step['command'] or step['label']}" + ("  (the owner runs this)" if step["owner_runs"] else "")
                  for step in context["next_actions"]]
    lines.append("Owner only, never run by an agent: " + ", ".join(context["owner_only_commands"]["commands"]))
    return "\n".join(lines)
