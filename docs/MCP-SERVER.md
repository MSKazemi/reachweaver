# Local MCP server (`mcp-serve`) — experimental (owner-reviewed 2026-10-20)

> **Status: draft.** This command is on a review branch and is not part of a release. It exists so the owner can review it; it may change or be removed.

`reachweaver mcp-serve` runs a small [Model Context Protocol](https://modelcontextprotocol.io) server on your own computer. It lets an MCP client other than Claude Code (for example Claude Desktop) read your local ReachWeaver state and run ReachWeaver's offline checks. Claude Code does not need it: the plugin and skill already run the command line directly.

The server is **read-only**. It has no tool that approves, schedules, publishes, pauses, resumes, reconciles, reconnects, connects an account or changes the approval mode. A client cannot run those steps even if a prompt tells it to, because the tools do not exist. Those steps stay with you, in your own terminal.

## What it offers

- **One tool per read-only command.** A tool exists only for a command that the command catalog (`reachweaver describe`) tags `read_only`, that is not `interactive`, `owner_only` or `network`, that has no owner-only flag and that can print JSON. Today that is about 30 tools, among them `context`, `describe`, `start`, `doctor`, `version`, `campaign_check`, `campaign_preview`, `claims_check`, `capabilities`, `distribution_plan`, `voice_check`, `repeat_check`, `ops_summary` and `trace`. Tool names use `_` where the command uses `-`. Ask the server for the exact list (`tools/list`).
- **Not offered:**
  - `list`, `show` and `check`: they open the database read-write, so they create a missing one, and the catalog tags them `writes`;
  - `credentials-audit`: it reads credential values to audit them;
  - the network-capable checks (`link-health`, `landing-check`, `seo-content-check`);
  - every owner-only command.
- **Flags that no tool accepts:** `--online`, `--allow-origin`, `--reply-to`, `--override-flagged`, `--owner-authorized`, `--include-individuals` and the output-format flags. The server always asks for JSON itself.
- **Drafts, only if you opt in.** With `--allow-drafts` there is one more tool, `draft`. It writes a local draft for one or more of your accounts and nothing else: no `reply_to`, no `online`, no `override_flagged`. Each draft still needs your own `approve` in your terminal before anything can be sent.

Each tool's input schema is generated from the command's real flags, the same way `describe` is. A tool result is the command's JSON output with its exit code, in `structuredContent` (from protocol revision 2025-06-18) and as JSON text. Exit code 1 means "findings", as on the command line. Exit code 2 is an error, and the result then carries the `{"error": {"code", "message"}}` document.

## Set it up in Claude Desktop

1. Install ReachWeaver ([install guide](INSTALL.md)) and note the absolute path of the `reachweaver` command (`command -v reachweaver`) and of your workspace folder.
2. In Claude Desktop open **Settings → Developer → Edit Config**. The file is `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS and `%APPDATA%\Claude\claude_desktop_config.json` on Windows (source: [Connect to local MCP servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers), checked 2026-10-20).
3. Add a server entry. Use absolute paths. Global options such as `--home` go before `mcp-serve`:

```json
{
  "mcpServers": {
    "reachweaver": {
      "command": "/home/you/.local/bin/reachweaver",
      "args": ["--home", "/home/you/reachweaver-work", "mcp-serve"]
    }
  }
}
```

To let the client write drafts as well, add `"--allow-drafts"` after `"mcp-serve"`. Restart Claude Desktop after editing the file.

ReachWeaver has not been tested against Claude Desktop itself. The tests drive the server with the protocol messages from the specification (see below).

## Options

```text
reachweaver [--home DIR] [--db DB] [--accounts FILE] [--profile FILE] mcp-serve
            [--allow-drafts] [--root DIR] [--max-output-bytes N] [--max-calls-per-minute N]
```

- `--root DIR`: every file argument (`file`, `brand`, `campaign`, `log`...) must resolve inside this folder, after symlinks are followed. The default is the workspace, or the current folder when there is none. `~` and `-` (stdin) are refused.
- `--max-output-bytes N` (default 262144): a larger result is refused, not cut, so the client never receives broken JSON. Ask for less instead, for example `describe` with one `command`.
- `--max-calls-per-minute N` (default 60): a simple rate limit on tool calls.
- The global `--db`, `--accounts`, `--profile` and `--home` are fixed when the server starts. A client cannot choose another database or workspace.

The server runs until its input closes. It writes only protocol messages to stdout and one log line per call to stderr: the tool, its exit code, the size and the time. Arguments and results are never logged.

## Protocol

JSON-RPC 2.0 over stdio, one message per line (at most 1 MiB and 64 levels of nesting), UTF-8, no batches. The server is "dual-era" in the specification's terms:

- **Current revision `2026-07-28`.** There is no handshake. Every request carries `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` in `params._meta`. The server implements `server/discover`, `tools/list` and `tools/call`, adds `resultType: "complete"` and its `serverInfo` to every result, and answers an unknown version with error `-32022` and the list of supported versions.
- **Earlier revisions `2025-11-25`, `2025-06-18`, `2025-03-26` and `2024-11-05`.** These use the `initialize` handshake. The server answers with the requested version, or with `2025-11-25` when it does not know the requested one. It then serves `ping`, `tools/list` and `tools/call`.

Sources, fetched 2026-10-20: [specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) ([versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning), [stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio), [tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [server/discover](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)) and the [2025-11-25 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle). The server is written from the specification with the standard library only; it uses no MCP SDK.

## Threat notes

- **An MCP client can call tools without asking you first.** The specification says a human SHOULD be able to deny tool calls, but whether a client asks before each call is up to the client and its settings. Prompt injection (text in a web page, an email or a document the model reads) can make a model call any tool it has. That is why the server is read-only and has no approve or publish tool at all. Tool annotations (`readOnlyHint`) are hints the specification tells clients to treat as untrusted; the guarantee is that the tools do not exist.
- **Reading is still disclosure.** Whatever a tool returns goes to the client and, usually, to a cloud model: post counts and ids, account ids, credential variable *names* (never values), file paths, draft text checked by `voice_check` or `repeat_check`, and the content of any file under `--root` that a check echoes back. Choose `--root` accordingly; do not point it at your home folder. Outputs go through ReachWeaver's secret redaction (`privacy.redact_value`), which catches known secret patterns and the values of secret-named environment variables, but redaction is a safety net, not a promise.
- **Drafts (`--allow-drafts`) fill your queue.** A misled client could create many drafts (bounded by the rate limit). None of them can be approved or sent without you; review or `cancel` them.
- **No network.** While a tool runs, socket connections and name lookups in the server process raise an error, in addition to the tools that could use the network being withheld. The server does not open a port; it talks only through the pipes its client gives it.
- **Same user, same files.** The server runs with your account's permissions. It is not a sandbox against a malicious local program; it limits what a client can ask for.

## Not implemented

Resources, prompts, subscriptions, progress, cancellation of a running tool (each call runs to the end; the commands are local and short), a time limit per call, pagination of `tools/list`, and the Streamable HTTP transport.
