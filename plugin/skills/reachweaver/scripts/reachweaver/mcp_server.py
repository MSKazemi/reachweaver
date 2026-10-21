"""``reachweaver mcp-serve``: a local MCP server on stdio, for MCP clients other than Claude Code (experimental).

Status: **experimental, owner-reviewed 2026-10-20.** An MCP client (Claude Desktop, another agent) can
call a server's tools without asking a person before each call, so this server is read-only by construction:

* **Allowlist.** A tool exists only for a subcommand whose ``catalog.COMMANDS`` entry is tagged ``read_only``,
  is not ``interactive``, ``owner_only`` or ``network``, has no ``owner_only_flags``, prints JSON, and is not in
  ``WITHHELD_COMMANDS``. With ``--allow-drafts`` the one extra tool is ``draft`` (it writes a local draft that
  still needs the owner's own approval). ``NEVER_EXPOSED`` is a backstop: building the tool list fails if any
  of those commands would ever pass the filter. There is no approve, review, publish, resume, reconcile,
  reconnect, connect or approval-mode tool, so a client cannot run them even when told to.
* **Arguments.** Each tool's input schema is generated from the command's argparse definition, like
  ``describe``. Arguments are checked against it (unknown, wrong type, too long, not a choice), flags in
  ``WITHHELD_FLAGS`` (network, human attestations, output format) do not exist, and every file argument must
  resolve inside ``--root`` (default: the workspace, else the current folder). The command line is built
  here from the allowlist; a client never supplies a command name, a global option or a raw argument list.
* **Execution.** The command runs in this process through ``cli.main`` with ``--json``, global options fixed
  at server start, an empty stdin and captured stdout/stderr. Outputs are redacted (``privacy.redact_value``)
  and refused above ``--max-output-bytes`` instead of being cut. Socket connects and name lookups raise while
  a tool runs. Calls are rate limited. The server logs one line per call to stderr (tool, exit code, bytes,
  milliseconds; never arguments or output); stdout carries only JSON-RPC messages.

Protocol: JSON-RPC 2.0, one message per line on stdin/stdout. It serves the current revision 2026-07-28
(per-request ``_meta``, ``server/discover``, no handshake) and, for older clients, the ``initialize``
handshake of revisions 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05 ("dual-era" in the spec's terms).
Source: https://modelcontextprotocol.io/specification/2026-07-28 (basic, basic/versioning,
basic/transports/stdio, server/tools, server/discover) and /specification/2025-11-25/basic/lifecycle,
fetched 2026-10-20. Written from the specification only; no MCP SDK code is used.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import socket
import sys
import time
from collections import deque
from pathlib import Path

from . import catalog
from .inputs import nesting_depth
from .policy import ValidationError
from .privacy import redact, redact_value

MODERN_VERSIONS = ("2026-07-28",)
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS
STRUCTURED_SINCE = "2025-06-18"  # structuredContent first appears in this revision
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

DRAFT_COMMAND = "draft"
# Read-only in the catalog but not for an unattended client: it reads credential values to audit them.
WITHHELD_COMMANDS = {"credentials-audit": "reads credential values (never prints them); the owner runs it"}
# Commands that must never become a tool, whatever the catalog says. A backstop, not the rule.
NEVER_EXPOSED = frozenset({
    "approve", "review", "unapprove", "publish", "resume", "reconcile", "reconnect", "approval-mode", "connect",
    "connect-check", "account-kit", "update", "schedule", "cancel", "pause", "disconnect", "mcp-serve", "ui",
    "share", "data-purge", "metrics-fetch", "engagement-fetch", "sources-scan", "postiz-check"})
# Flags no tool offers: network switches, human attestations and overrides, and output-format flags (the
# server always asks for JSON itself).
WITHHELD_FLAGS = frozenset({
    "--online", "--live", "--allow-origin", "--reply-to", "--override-flagged", "--owner-authorized",
    "--include-individuals", "--approve-plan", "--yes", "--apply", "--json", "--markdown", "--format", "--out"})
PATH_METAVARS = frozenset({"FILE", "DIR", "PATH"})

DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_CALLS_PER_MINUTE = 60
MAX_LINE_BYTES = 1024 * 1024
# Real MCP messages nest a few levels; deeper input is refused before json.loads instead of relying on the
# interpreter's recursion limit (Python 3.14 parses 100,000 levels without a RecursionError).
MAX_JSON_DEPTH = 64
MAX_STRING = 20_000
MAX_ITEMS = 50
SAFE_INT = 2 ** 53 - 1
STDERR_KEEP = 4000

INSTRUCTIONS = ("ReachWeaver, local and read-only. Start with the context tool, then describe for exact flags. "
                "These tools never approve, schedule, publish, pause or reconnect anything; those steps belong to "
                "the account owner in their own terminal. File arguments must be inside the server's root folder.")


class ArgumentProblem(ValueError):
    """A tool argument the schema rejects; reported to the client as a tool execution error."""


def tool_name(command: str) -> str:
    return command.replace("-", "_")


def _exposable(command: str, meta: dict, sub: argparse.ArgumentParser) -> bool:
    tags = set(meta["tags"])
    return ("read_only" in tags and not tags & {"interactive", "owner_only", "network", "writes"}
            and not meta["owner_only_flags"] and command not in WITHHELD_COMMANDS
            and catalog.json_option(sub) is not None)


def exposed_commands(parser: argparse.ArgumentParser, *, allow_drafts: bool = False) -> list[str]:
    """The allowlist: subcommands that become tools, sorted. Raises if the backstop would be crossed."""
    subs = catalog._subparsers(parser).choices
    names = [name for name, sub in sorted(subs.items())
             if name in catalog.COMMANDS and _exposable(name, catalog.COMMANDS[name], sub)]
    if allow_drafts and DRAFT_COMMAND in subs:
        names.append(DRAFT_COMMAND)
    crossed = set(names) & (NEVER_EXPOSED | {c.split()[0] for c in catalog.owner_only_commands()})
    if crossed:
        raise ValidationError(f"mcp-serve refuses to start: {', '.join(sorted(crossed))} would become tools")
    return sorted(names)


class _Param:
    __slots__ = ("name", "option", "kind", "repeatable", "required", "choices", "is_path", "help")

    def __init__(self, action: argparse.Action):
        self.option = catalog._option_name(action) if action.option_strings else None
        positional = action.metavar.lower() if isinstance(action.metavar, str) else action.dest
        self.name = (self.option[2:] if self.option else positional).replace("-", "_")
        if isinstance(action, argparse._StoreTrueAction):
            self.kind = "flag"
        elif action.type is int:
            self.kind = "integer"
        elif action.type is float:
            self.kind = "number"
        else:
            self.kind = "string"
        self.repeatable = isinstance(action, argparse._AppendAction)
        self.required = bool(action.required)
        self.choices = list(action.choices) if action.choices else None
        self.is_path = action.type is Path or action.metavar in PATH_METAVARS
        self.help = action.help if isinstance(action.help, str) and action.help != argparse.SUPPRESS else None

    def schema(self) -> dict:
        kind = {"flag": "boolean"}.get(self.kind, self.kind)
        item: dict = {"type": kind}
        if self.kind == "string":
            item["maxLength"] = MAX_STRING
        if self.kind == "integer":
            item.update(minimum=-SAFE_INT, maximum=SAFE_INT)
        if self.choices:
            item["enum"] = self.choices
        if self.repeatable:
            item = {"type": "array", "items": item, "maxItems": MAX_ITEMS}
        description = self.help or ""
        if self.is_path:
            description = (description + " " if description else "") + "A file path inside the server's root folder."
        if description:
            item["description"] = description
        return item


def _supported(action: argparse.Action) -> bool:
    if isinstance(action, argparse._StoreTrueAction):
        return True
    if isinstance(action, argparse._AppendAction):
        return action.nargs is None
    return type(action) is argparse._StoreAction and action.nargs in (None, "?")


class Tool:
    """One MCP tool generated from one argparse subparser."""

    def __init__(self, command: str, sub: argparse.ArgumentParser, help_text: str | None):
        self.command, self.name = command, tool_name(command)
        self.json_args = (catalog.json_option(sub) or "").split()
        self.params: dict[str, _Param] = {}
        for action in sub._actions:
            if isinstance(action, argparse._HelpAction) or set(action.option_strings) & WITHHELD_FLAGS:
                if action.required:
                    raise ValidationError(f"{command}: required flag {action.option_strings} is withheld")
                continue
            if not _supported(action):
                if action.required:
                    raise ValidationError(f"{command}: unsupported required argument {action.dest}")
                continue
            param = _Param(action)
            if param.name in self.params:
                raise ValidationError(f"{command}: two arguments map to {param.name}")
            self.params[param.name] = param
        meta = catalog.COMMANDS[command]
        read_only = command != DRAFT_COMMAND
        groups = [[_Param(a).name for a in g._group_actions if _Param(a).name in self.params]
                  for g in sub._mutually_exclusive_groups]
        parts = [help_text or command]
        if meta["note"]:
            parts.append(f"Note: {meta['note']}.")
        parts += [f"Give at most one of: {', '.join(g)}." for g in groups if len(g) > 1]
        parts.append("Read-only: changes nothing and sends nothing." if read_only else
                     "Writes one local draft per account; it is never approved, scheduled or sent, and the "
                     "owner approves it in their own terminal.")
        self.read_only = read_only
        self.description = " ".join(parts)

    def definition(self) -> dict:
        properties = {name: param.schema() for name, param in self.params.items()}
        schema: dict = {"type": "object", "properties": properties, "additionalProperties": False}
        required = [name for name, param in self.params.items() if param.required]
        if required:
            schema["required"] = required
        title = f"ReachWeaver {self.command}"
        annotations = {"title": title, "readOnlyHint": self.read_only, "destructiveHint": False,
                       "idempotentHint": self.read_only, "openWorldHint": False}
        return {"name": self.name, "title": title, "description": self.description, "inputSchema": schema,
                "annotations": annotations}

    def argv(self, arguments, root: Path) -> list[str]:
        """The command line for these arguments, or ArgumentProblem. Never takes raw argv from the client."""
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ArgumentProblem("arguments must be an object")
        unknown = sorted(set(arguments) - set(self.params))
        if unknown:
            raise ArgumentProblem(f"unknown argument(s) for {self.name}: {', '.join(unknown)}")
        missing = [name for name, param in self.params.items() if param.required and name not in arguments]
        if missing:
            raise ArgumentProblem(f"missing required argument(s): {', '.join(missing)}")
        options: list[str] = []
        positionals: list[str] = []
        for name, param in self.params.items():
            if name not in arguments:
                continue
            value = arguments[name]
            if param.kind == "flag":
                if not isinstance(value, bool):
                    raise ArgumentProblem(f"{name} must be true or false")
                if value and param.option:
                    options.append(param.option)
                continue
            if param.repeatable:
                if not isinstance(value, list) or len(value) > MAX_ITEMS:
                    raise ArgumentProblem(f"{name} must be a list of at most {MAX_ITEMS} values")
                values = value
            else:
                values = [value]
            for item in values:
                text = _scalar(name, param, item)
                if param.is_path:
                    text = confine(text, root, name)
                if param.option is None:
                    if text.startswith("-"):
                        raise ArgumentProblem(f"{name} must not start with '-'")
                    positionals.append(text)
                else:
                    options.append(f"{param.option}={text}")
        return [self.command, *options, *self.json_args, *positionals]


def _scalar(name: str, param: _Param, value) -> str:
    if param.kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > SAFE_INT:
            raise ArgumentProblem(f"{name} must be an integer")
        text = str(value)
    elif param.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ArgumentProblem(f"{name} must be a finite number")
        text = repr(float(value))
    else:
        if not isinstance(value, str) or len(value) > MAX_STRING or "\x00" in value:
            raise ArgumentProblem(f"{name} must be a string of at most {MAX_STRING} characters")
        text = value
    if param.choices is not None and value not in param.choices:
        raise ArgumentProblem(f"{name} must be one of: {', '.join(str(c) for c in param.choices)}")
    return text


def confine(text: str, root: Path, name: str = "path") -> str:
    """The absolute, symlink-resolved path, refused unless it lies inside ``root``. No '~', no stdin."""
    if text in ("", "-") or text.startswith("~"):
        raise ArgumentProblem(f"{name} must be a file path inside the server's root folder")
    candidate = Path(text)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ArgumentProblem(f"{name} is outside the server's root folder; only files under it can be read")
    return str(resolved)


class _Capture(io.TextIOBase):
    """A text sink that keeps at most ``limit`` characters and remembers that it overflowed."""

    def __init__(self, limit: int):
        super().__init__()
        self.limit, self.size, self.overflow = limit, 0, False
        self.parts: list[str] = []

    def writable(self) -> bool:
        return True

    def write(self, text) -> int:
        if self.size + len(text) > self.limit:
            self.overflow = True
            keep = max(0, self.limit - self.size)
            text = text[:keep]
        self.parts.append(text)
        self.size += len(text)
        return len(text)

    def getvalue(self) -> str:
        return "".join(self.parts)


def _refuse_network(*_args, **_kwargs):
    raise OSError("network access is disabled inside reachweaver mcp-serve")


@contextlib.contextmanager
def network_blocked():
    """Socket connects, sendto and name lookups raise ``OSError`` while the block is active."""
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto, socket.create_connection,
             socket.getaddrinfo)
    socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = _refuse_network  # type: ignore[method-assign, assignment]
    socket.create_connection = socket.getaddrinfo = _refuse_network  # type: ignore[assignment]
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto, socket.create_connection,  # type: ignore[method-assign]
         socket.getaddrinfo) = saved


def _error(message_id, code: int, message: str, data=None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


class McpServer:
    """Protocol state and tool dispatch; ``serve`` adds the stdio loop."""

    def __init__(self, *, global_argv: list[str], root: Path, allow_drafts: bool = False,
                 max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES, calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE,
                 log=None, clock=time.monotonic):
        from . import __version__
        from .cli import make_parser
        parser = make_parser()
        subs = catalog._subparsers(parser)
        helps = {choice.dest: choice.help for choice in subs._choices_actions}
        self.tools = {tool_name(c): Tool(c, subs.choices[c], helps.get(c))
                      for c in exposed_commands(parser, allow_drafts=allow_drafts)}
        self.global_argv, self.root = list(global_argv), root.resolve()
        self.max_output_bytes, self.calls_per_minute = max_output_bytes, calls_per_minute
        self.log, self.clock = log, clock
        self.calls: deque[float] = deque()
        self.legacy_version: str | None = None
        self.server_info = {"name": "reachweaver", "title": "ReachWeaver (read-only)", "version": __version__}

    # -- protocol ------------------------------------------------------------------------------------------
    def handle_line(self, raw: bytes) -> dict | None:
        try:
            text = raw.decode("utf-8")
            if nesting_depth(text) > MAX_JSON_DEPTH:
                return _error(None, PARSE_ERROR, f"Parse error: JSON nested deeper than {MAX_JSON_DEPTH} levels")
            message = json.loads(text)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return _error(None, PARSE_ERROR, "Parse error: not a UTF-8 JSON message")
        if isinstance(message, list):
            return _error(None, INVALID_REQUEST, "JSON-RPC batches are not supported; send one message per line")
        return self.handle(message)

    def handle(self, message) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Invalid Request: expected a JSON-RPC 2.0 object")
        method = message.get("method")
        if "id" not in message:  # a notification (initialized, cancelled...) or a stray response: no reply
            return None if isinstance(method, str) or "result" in message or "error" in message else _error(
                None, INVALID_REQUEST, "Invalid Request: no method")
        message_id = message["id"]
        if isinstance(message_id, bool) or not isinstance(message_id, (str, int)):
            return _error(None, INVALID_REQUEST, "Invalid Request: id must be a string or an integer")
        if not isinstance(method, str):
            return None if "result" in message or "error" in message else _error(
                message_id, INVALID_REQUEST, "Invalid Request: no method")
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(message_id, INVALID_PARAMS, "params must be an object")
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
        modern = META_VERSION in meta
        if method == "initialize" and not modern:
            return self._initialize(message_id, params)
        if modern:
            version = meta.get(META_VERSION)
            if version not in MODERN_VERSIONS:
                return _error(message_id, UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version",
                              {"supported": list(SUPPORTED_VERSIONS), "requested": version
                               if isinstance(version, str) else str(version)})
            if not isinstance(meta.get(META_CAPABILITIES), dict):
                return _error(message_id, INVALID_PARAMS, f"_meta must carry {META_CAPABILITIES}")
            version_in_use = version
        elif self.legacy_version is not None:
            version_in_use = self.legacy_version
        else:
            return _error(message_id, INVALID_PARAMS,
                          f"No {META_VERSION} in params._meta and no initialize first. Supported protocol "
                          f"versions: {', '.join(SUPPORTED_VERSIONS)}")
        try:
            result = self._dispatch(message_id, method, params, version_in_use, modern)
        except Exception as exc:  # last resort; never echo the exception text (it may quote input)
            self._log(f"internal error in {method}: {type(exc).__name__}")
            return _error(message_id, INTERNAL_ERROR, "Internal error")
        if "error" in result:
            return result
        if modern:
            result["result"]["resultType"] = "complete"
            result["result"].setdefault("_meta", {})[META_SERVER_INFO] = dict(self.server_info)
        return result

    def _initialize(self, message_id, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        self.legacy_version = version
        return {"jsonrpc": "2.0", "id": message_id, "result": {
            "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": dict(self.server_info), "instructions": INSTRUCTIONS}}

    def _dispatch(self, message_id, method: str, params: dict, version: str, modern: bool) -> dict:
        if method == "server/discover" and modern:
            body = {"supportedVersions": list(SUPPORTED_VERSIONS), "capabilities": {"tools": {"listChanged": False}},
                    "instructions": INSTRUCTIONS, "ttlMs": 0, "cacheScope": "private"}
        elif method == "ping" and not modern:
            body = {}
        elif method == "tools/list":
            if params.get("cursor") is not None:
                return _error(message_id, INVALID_PARAMS, "Invalid cursor: this server returns every tool at once")
            body = {"tools": [tool.definition() for _, tool in sorted(self.tools.items())]}
            if modern:
                body.update(ttlMs=0, cacheScope="private")
        elif method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                return _error(message_id, INVALID_PARAMS, "tools/call needs a tool name")
            tool = self.tools.get(name)
            if tool is None:
                return _error(message_id, INVALID_PARAMS, f"Unknown tool: {name[:80]}")
            body = self.call(tool, params.get("arguments"), structured=version >= STRUCTURED_SINCE)
        else:
            return _error(message_id, METHOD_NOT_FOUND, f"Method not found: {method[:80]}")
        return {"jsonrpc": "2.0", "id": message_id, "result": body}

    # -- tools ---------------------------------------------------------------------------------------------
    def _rate_limited(self) -> bool:
        now = self.clock()
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        if len(self.calls) >= self.calls_per_minute:
            return True
        self.calls.append(now)
        return False

    def call(self, tool: Tool, arguments, *, structured: bool = True) -> dict:
        if self._rate_limited():
            self._log(f"tool {tool.name} refused: rate limit")
            return _tool_error(f"Rate limit: at most {self.calls_per_minute} tool calls per minute; try again later")
        try:
            argv = tool.argv(arguments, self.root)
        except ArgumentProblem as exc:
            return _tool_error(f"Invalid arguments: {exc}")
        started = self.clock()
        out, err = _Capture(self.max_output_bytes + 1), _Capture(STDERR_KEEP)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), _empty_stdin(), network_blocked():
            try:
                from .cli import main
                code = main([*self.global_argv, *argv])
            except SystemExit as exc:  # argparse rejected the command line
                code = exc.code if isinstance(exc.code, int) else 2
            except Exception as exc:  # noqa: BLE001 - reported by class name only
                print(f"ERROR: {type(exc).__name__}", file=err)
                code = 2
        size = len(out.getvalue().encode("utf-8"))
        self._log(f"tool {tool.name} exit {code} {size} bytes {int((self.clock() - started) * 1000)} ms")
        if out.overflow or size > self.max_output_bytes:
            return _tool_error(f"The result is larger than this server's {self.max_output_bytes}-byte cap and was "
                               "not returned; narrow the query (for example describe one command, or a filter)")
        text = out.getvalue()
        try:
            document = redact_value(json.loads(text)) if text.strip() else None
        except ValueError:
            document = {"text": redact(text)}
        payload: dict = {"exit_code": code, "result": document}
        messages = redact(err.getvalue()).strip()
        if messages:
            payload["messages"] = messages
        body: dict = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                      "isError": code not in (0, 1)}
        if structured:
            body["structuredContent"] = payload
        return body

    def _log(self, line: str) -> None:
        if self.log is not None:
            print(f"reachweaver mcp-serve: {line}", file=self.log, flush=True)


def _tool_error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


@contextlib.contextmanager
def _empty_stdin():
    saved = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        yield
    finally:
        sys.stdin = saved


def _global_argv(args) -> list[str]:
    from . import home
    argv = ["--plain"]
    for flag, value in (("--db", args.db), ("--accounts", args.accounts), ("--profile", args.profile)):
        if value:
            argv += [flag, str(value)]
    if home.root() is not None:
        argv += ["--home", str(home.root())]
    return argv


def server_from_args(args, *, log=None) -> McpServer:
    from . import home
    if not 1024 <= args.max_output_bytes <= 8 * 1024 * 1024:
        raise ValidationError("--max-output-bytes must be between 1024 and 8388608")
    if not 1 <= args.max_calls_per_minute <= 600:
        raise ValidationError("--max-calls-per-minute must be between 1 and 600")
    root = args.root if args.root is not None else (home.root() or Path.cwd())
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValidationError(f"--root {root} is not a folder")
    return McpServer(global_argv=_global_argv(args), root=root, allow_drafts=args.allow_drafts,
                     max_output_bytes=args.max_output_bytes, calls_per_minute=args.max_calls_per_minute, log=log)


def serve(args, *, stdin=None, stdout=None, stderr=None) -> int:
    """Run until stdin closes. stdout carries only JSON-RPC messages; everything else goes to stderr."""
    log = stderr if stderr is not None else sys.stderr
    server = server_from_args(args, log=log)
    reader = stdin if stdin is not None else sys.stdin.buffer
    writer = stdout if stdout is not None else sys.stdout.buffer
    saved_stdout = sys.stdout
    sys.stdout = log  # a stray print anywhere can never corrupt the protocol stream
    server._log(f"ready: {len(server.tools)} tools, root {server.root}, drafts "
                f"{'allowed' if any(not t.read_only for t in server.tools.values()) else 'off'}; stop with end of input")
    try:
        with network_blocked():
            while True:
                line = reader.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
                    while True:  # skip the rest of the oversized line
                        rest = reader.readline(MAX_LINE_BYTES)
                        if not rest or rest.endswith(b"\n"):
                            break
                    response: dict | None = _error(None, INVALID_REQUEST,
                                                   f"Message larger than {MAX_LINE_BYTES} bytes")
                elif not line.strip():
                    continue
                else:
                    response = server.handle_line(line)
                if response is not None:
                    writer.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                                 + b"\n")
                    writer.flush()
    except BrokenPipeError:
        pass
    finally:
        sys.stdout = saved_stdout
    server._log("end of input; stopped")
    return 0
