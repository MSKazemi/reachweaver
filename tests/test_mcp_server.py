"""`mcp-serve` (draft, pending security review): the stdio MCP server exposes only allowlisted read-only tools.

Offline and synthetic. Protocol facts come from https://modelcontextprotocol.io/specification/2026-07-28 and
/specification/2025-11-25/basic/lifecycle (fetched 2026-10-20); see docs/MCP-SERVER.md.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reachweaver import catalog, mcp_server
from reachweaver.cli import make_parser
from reachweaver.policy import ValidationError
from test_ui_data import FORBIDDEN_NAMES, referenced_names

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = ('{"accounts":[{"id":"a-bsky","platform":"bluesky","env_prefix":"REACHWEAVER_A_BSKY",'
            '"handle":"a.bsky.social"}]}')
CLEAN_ENV = {"REACHWEAVER_ALLOW_LIVE": "", "REACHWEAVER_ENV": ""}
MODERN_META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
               "io.modelcontextprotocol/clientCapabilities": {}}
SIM = {"seed": 1, "pairs": 2, "lift": 0.1, "noise": 0.1}
# Commands that must never be reachable from an MCP client, whatever else changes.
NEVER = ("approve", "publish", "resume", "reconcile", "reconnect", "approval-mode", "connect", "account-kit",
         "unapprove", "schedule", "cancel", "update", "mcp-serve", "ui")


def subcommands() -> dict[str, argparse.ArgumentParser]:
    return dict(catalog._subparsers(make_parser()).choices)


class Base(unittest.TestCase):
    allow_drafts = False

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.db = self.base / "state" / "mcp.sqlite"
        self.accounts = self.base / "accounts.json"
        self.accounts.write_text(ACCOUNTS, encoding="utf-8")
        self.profile = self.base / "profile.json"
        patcher = mock.patch.dict("os.environ", CLEAN_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = io.StringIO()
        self.server = self.make_server()

    def make_server(self, **kwargs) -> mcp_server.McpServer:
        options = {"allow_drafts": self.allow_drafts, "log": self.log, **kwargs}
        return mcp_server.McpServer(
            global_argv=["--plain", "--db", str(self.db), "--accounts", str(self.accounts), "--profile",
                         str(self.profile)], root=self.base, **options)

    def legacy(self, server=None) -> mcp_server.McpServer:
        server = server or self.server
        reply = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                               "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                          "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-11-25")
        return server

    def call(self, name: str, arguments=None, server=None) -> dict:
        server = server or self.legacy()
        params: dict = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params})

    def ok(self, name: str, arguments=None, server=None) -> dict:
        reply = self.call(name, arguments, server)
        self.assertIn("result", reply, reply)
        result = reply["result"]
        self.assertFalse(result["isError"], result)
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        return result["structuredContent"]

    def tool_error(self, name: str, arguments=None, server=None) -> str:
        reply = self.call(name, arguments, server)
        self.assertTrue(reply["result"]["isError"], reply)
        return reply["result"]["content"][0]["text"]


class HandshakeTests(Base):
    def test_legacy_initialize_negotiates_and_then_serves(self):
        reply = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        result = reply["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(result["serverInfo"]["name"], "reachweaver")
        self.assertIn("never approve", result["instructions"])
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"], {})
        listed = self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]
        self.assertNotIn("resultType", listed)
        self.assertIn("context", [tool["name"] for tool in listed["tools"]])

    def test_unknown_legacy_version_gets_the_latest_legacy_one(self):
        reply = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "1999-01-01", "capabilities": {}}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-11-25")

    def test_old_revisions_get_no_structured_content(self):
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
        result = self.call("version", {}, self.server)["result"]
        self.assertNotIn("structuredContent", result)
        self.assertEqual(json.loads(result["content"][0]["text"])["exit_code"], 0)

    def test_request_without_initialize_or_meta_is_refused_with_the_versions(self):
        reply = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertIn("2026-07-28", reply["error"]["message"])

    def test_modern_discover_and_list_carry_result_type_and_server_info(self):
        reply = self.server.handle({"jsonrpc": "2.0", "id": "d", "method": "server/discover",
                                    "params": {"_meta": MODERN_META}})
        result = reply["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"][0], "2026-07-28")
        self.assertIn("2025-11-25", result["supportedVersions"])
        self.assertEqual(result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "reachweaver")
        self.assertEqual((result["ttlMs"], result["cacheScope"]), (0, "private"))
        listed = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                     "params": {"_meta": MODERN_META}})["result"]
        self.assertEqual(listed["resultType"], "complete")
        self.assertIn("tools", listed)
        called = self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                     "params": {"_meta": MODERN_META, "name": "version", "arguments": {}}})
        self.assertEqual(called["result"]["resultType"], "complete")
        self.assertEqual(called["result"]["structuredContent"]["exit_code"], 0)

    def test_modern_unsupported_version_and_missing_capabilities(self):
        reply = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01",
                      "io.modelcontextprotocol/clientCapabilities": {}}}})
        self.assertEqual(reply["error"]["code"], -32022)
        self.assertEqual(reply["error"]["data"]["requested"], "1900-01-01")
        self.assertEqual(reply["error"]["data"]["supported"], list(mcp_server.SUPPORTED_VERSIONS))
        reply = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}})
        self.assertEqual(reply["error"]["code"], -32602)
        reply = self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"_meta": MODERN_META}})
        self.assertEqual(reply["error"]["code"], -32601)  # no ping in the modern revision
        reply = self.server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list",
                                    "params": {"_meta": MODERN_META, "cursor": "x"}})
        self.assertEqual(reply["error"]["code"], -32602)


class AllowlistTests(Base):
    def tool_names(self, server=None) -> list[str]:
        server = self.legacy(server)
        return [tool["name"] for tool in server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                ["result"]["tools"]]

    def test_every_tool_maps_to_a_read_only_catalog_command(self):
        subs = subcommands()
        names = self.tool_names()
        self.assertEqual(names, sorted(names))
        self.assertGreater(len(names), 10)
        for name in names:
            command = self.server.tools[name].command
            meta = catalog.COMMANDS[command]
            tags = set(meta["tags"])
            self.assertIn("read_only", tags, command)
            self.assertFalse(tags & {"writes", "interactive", "owner_only", "network"}, command)
            self.assertEqual(meta["owner_only_flags"], (), command)
            self.assertIsNotNone(catalog.json_option(subs[command]), command)
            self.assertNotIn(command, mcp_server.WITHHELD_COMMANDS)

    def test_tools_list_is_exactly_the_allowlist(self):
        expected = sorted(mcp_server.tool_name(c) for c in mcp_server.exposed_commands(make_parser()))
        self.assertEqual(self.tool_names(), expected)
        for command in ("context", "describe", "claims-check", "campaign-check", "doctor", "start", "version"):
            self.assertIn(mcp_server.tool_name(command), expected)
        # list/show/check open the database read-write (they create a missing one), so they are tagged writes.
        for command in ("list", "show", "check", "draft", "credentials-audit", "link-health", "landing-check"):
            self.assertNotIn(mcp_server.tool_name(command), expected)

    def test_no_owner_only_or_never_exposed_command_and_no_withheld_flag(self):
        owner = {c.split()[0] for c in catalog.owner_only_commands()}
        for server in (self.server, self.make_server(allow_drafts=True)):
            names = {tool.command for tool in server.tools.values()}
            self.assertFalse(names & owner, names & owner)
            self.assertFalse(names & set(NEVER), names & set(NEVER))
            for tool in server.tools.values():
                options = {p.option for p in tool.params.values() if p.option}
                self.assertFalse(options & mcp_server.WITHHELD_FLAGS, tool.name)
                every_owner_flag = {f for meta in catalog.COMMANDS.values() for f in meta["owner_only_flags"]}
                self.assertFalse(options & every_owner_flag, tool.name)

    def test_allow_drafts_adds_draft_only_without_reply_or_network_flags(self):
        server = self.make_server(allow_drafts=True)
        self.assertEqual(set(server.tools) - set(self.server.tools), {"draft"})
        draft = server.tools["draft"].definition()
        self.assertEqual(set(draft["inputSchema"]["properties"]) & {"reply_to", "online", "override_flagged", "json"},
                         set())
        self.assertEqual(draft["inputSchema"]["required"], ["account"])
        self.assertFalse(draft["annotations"]["readOnlyHint"])
        self.assertFalse(draft["annotations"]["destructiveHint"])

    def test_annotations_and_schemas_are_closed(self):
        for tool in self.server.tools.values():
            definition = tool.definition()
            self.assertTrue(definition["annotations"]["readOnlyHint"], tool.name)
            self.assertFalse(definition["annotations"]["openWorldHint"], tool.name)
            self.assertFalse(definition["inputSchema"]["additionalProperties"], tool.name)
            self.assertRegex(definition["name"], r"^[A-Za-z0-9_.-]{1,128}$")

    def test_backstop_refuses_a_misclassified_owner_only_command(self):
        tampered = dict(catalog.COMMANDS)
        tampered["publish"] = catalog._c(("read_only",))  # both print JSON, so only the backstop stops them
        tampered["schedule"] = catalog._c(("read_only",))
        with mock.patch.object(catalog, "COMMANDS", tampered), \
                mock.patch.object(mcp_server.catalog, "owner_only_commands", lambda: []):
            with self.assertRaises(ValidationError) as caught:
                mcp_server.exposed_commands(make_parser())
        self.assertIn("publish", str(caught.exception))
        self.assertIn("schedule", str(caught.exception))

    def test_mcp_serve_itself_is_interactive_and_never_a_tool(self):
        self.assertIn("interactive", catalog.COMMANDS["mcp-serve"]["tags"])
        self.assertTrue(catalog.owner_runs("reachweaver mcp-serve"))
        self.assertNotIn("mcp_serve", self.server.tools)

    def test_the_module_references_no_send_or_approval_path(self):
        # socket/connect: the network block patches socket.socket.connect; nothing here opens a connection.
        found = referenced_names(mcp_server.__file__) & (FORBIDDEN_NAMES - {"socket", "connect"})
        self.assertEqual(found, set())


class CallTests(Base):
    def setUp(self):
        super().setUp()
        for name in ("campaign.reachweaver.json", "brand.reachweaver.json"):
            shutil.copy(ROOT / "examples" / name, self.base / name)

    def test_context_describe_and_offline_checks(self):
        context = self.ok("context", {})
        self.assertEqual(context["exit_code"], 0)
        self.assertEqual(context["result"]["context"], "reachweaver_context")
        self.assertIn("publish --live", context["result"]["owner_only_commands"]["commands"])
        described = self.ok("describe", {"command": "claims-check"})
        self.assertEqual(described["result"]["command"]["name"], "claims-check")
        claims = self.ok("claims_check", {"campaign": "campaign.reachweaver.json",
                                          "brand": str(self.base / "brand.reachweaver.json")})
        self.assertIn(claims["exit_code"], (0, 1))
        self.assertIsInstance(claims["result"], dict)
        checked = self.ok("campaign_check", {"file": "campaign.reachweaver.json", "brand": "brand.reachweaver.json"})
        self.assertIn(checked["exit_code"], (0, 1))
        self.assertFalse(self.db.exists())
        self.assertIn("tool context exit 0", self.log.getvalue())
        self.assertNotIn("campaign.reachweaver.json", self.log.getvalue())  # arguments are never logged

    def test_unknown_and_owner_only_tools_are_protocol_errors(self):
        server = self.legacy()
        for name in ("approve", "publish", "resume", "reconcile", "reconnect", "approval_mode", "approval-mode",
                     "list", "check", "draft", "mcp_serve", "nope", "context "):
            reply = self.call(name, {}, server)
            self.assertEqual(reply["error"]["code"], -32602, name)
            self.assertTrue(reply["error"]["message"].startswith("Unknown tool"), name)
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"arguments": {}}})
        self.assertEqual(reply["error"]["code"], -32602)

    def test_argument_validation(self):
        cases = [
            ("describe", {"command": "x", "extra": 1}, "unknown argument"),
            ("describe", {"json": True}, "unknown argument"),
            ("describe", {"command": 3}, "must be a string"),
            ("describe", {"command": "-h"}, "must not start with '-'"),
            ("experiment_simulate", {**SIM, "seed": True}, "must be an integer"),
            ("experiment_simulate", {**SIM, "seed": "3"}, "must be an integer"),
            ("experiment_simulate", {**SIM, "lift": float("inf")}, "finite number"),
            ("capabilities", {"platform": "x" * (mcp_server.MAX_STRING + 1)}, "at most"),
            ("capabilities", {"platform": "a\x00b"}, "must be a string"),
            ("campaign_check", {}, "missing required"),
            ("campaign_check", {"file": "/etc/hostname"}, "outside the server's root"),
            ("campaign_check", {"file": "../outside.json"}, "outside the server's root"),
            ("campaign_check", {"file": "~/x.json"}, "inside the server's root"),
            ("campaign_check", {"file": "-"}, "inside the server's root"),
            ("account_advisor", {"existing": "x"}, "must be a list"),
            ("account_advisor", {"existing": ["x"] * (mcp_server.MAX_ITEMS + 1)}, "must be a list"),
            ("post_audit", {"owner_authorized": True}, "unknown argument"),
            ("context", [], "must be an object"),
        ]
        server = self.legacy()
        for name, arguments, expected in cases:
            self.assertIn(expected, self.tool_error(name, arguments, server), (name, arguments))
        choices = [p for p in server.tools["distribution_plan"].params.values() if p.choices]
        self.assertTrue(choices)
        base = {p.name: (p.choices[0] if p.choices else "x")
                for p in server.tools["distribution_plan"].params.values() if p.required}
        self.assertIn("must be one of",
                      self.tool_error("distribution_plan", {**base, choices[0].name: "not-a-choice"}, server))

    def test_symlink_out_of_root_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "c.json"
        target.write_text("{}", encoding="utf-8")
        (self.base / "link.json").symlink_to(target)
        self.assertIn("outside the server's root", self.tool_error("campaign_check", {"file": "link.json"}))

    def test_command_errors_come_back_as_tool_errors_with_the_error_document(self):
        result = self.call("describe", {"command": "nope"})["result"]
        self.assertTrue(result["isError"])
        payload = result["structuredContent"]
        self.assertEqual(payload["exit_code"], 2)
        self.assertEqual(payload["result"]["error"]["code"], "not_found")
        self.assertIn("Unknown command", payload["messages"])

    def test_output_cap_refuses_instead_of_truncating(self):
        server = self.legacy(self.make_server(max_output_bytes=1024))
        text = self.tool_error("describe", {}, server)
        self.assertIn("1024-byte cap", text)
        self.assertIn("exit 0", self.log.getvalue())

    def test_read_only_tools_never_create_the_database(self):
        server = self.legacy(self.make_server(calls_per_minute=600))
        called = 0
        for name, tool in sorted(server.tools.items()):
            if any(param.required for param in tool.params.values()):
                continue
            reply = self.call(name, {}, server)
            self.assertIn("result", reply, name)
            called += 1
            self.assertFalse(self.db.exists(), f"{name} created the database")
            self.assertFalse(self.db.parent.exists(), f"{name} created the state folder")
        self.assertGreater(called, 10)

    def test_draft_tool_writes_a_draft_only_with_allow_drafts(self):
        server = self.legacy(self.make_server(allow_drafts=True))
        created = self.ok("draft", {"account": ["a-bsky"], "text": "hello from an MCP client"}, server)
        self.assertEqual(created["exit_code"], 0)
        self.assertEqual(created["result"]["created"][0]["status"], "draft")
        self.assertTrue(self.db.exists())
        self.assertIn("outside the server's root", self.tool_error("draft", {"account": ["a-bsky"],
                                                                             "file": "/etc/hostname"}, server))
        both = self.call("draft", {"account": ["a-bsky"], "text": "a", "file": "x.txt"}, server)["result"]
        self.assertTrue(both["isError"])  # argparse refuses the exclusive pair; nothing was created twice

    def test_outputs_are_redacted(self):
        secret = "s3cr3t-value-9f8e7d6c5b4a"

        def fake_main(argv):
            print(json.dumps({"echo": f"token {secret}"}))
            print(f"warning {secret}", file=sys.stderr)
            return 0

        with mock.patch.dict("os.environ", {"REACHWEAVER_A_BSKY_APP_PASSWORD": secret}), \
                mock.patch("reachweaver.cli.main", fake_main):
            result = self.ok("version", {})
        text = json.dumps(result)
        self.assertNotIn(secret, text)
        self.assertIn("REDACTED", text)

    def test_tool_runs_with_empty_stdin_and_network_blocked(self):
        seen = {}

        def fake_main(argv):
            seen["argv"] = argv
            seen["stdin"] = sys.stdin.read()
            try:
                socket.create_connection(("192.0.2.1", 443), timeout=0.01)
            except OSError as exc:
                seen["network"] = str(exc)
            print("{}")
            return 0

        with mock.patch("reachweaver.cli.main", fake_main):
            self.ok("describe", {"command": "draft"})
        self.assertEqual(seen["stdin"], "")
        self.assertIn("disabled", seen["network"])
        self.assertEqual(seen["argv"][:7], ["--plain", "--db", str(self.db), "--accounts", str(self.accounts),
                                            "--profile", str(self.profile)])
        self.assertEqual(seen["argv"][7:], ["describe", "--json", "draft"])
        self.assertIsNot(socket.create_connection, mcp_server._refuse_network)  # restored afterwards

    def test_rate_limit(self):
        now = [100.0]
        server = self.legacy(self.make_server(calls_per_minute=2, clock=lambda: now[0]))
        self.ok("version", {}, server)
        self.ok("version", {}, server)
        self.assertIn("Rate limit", self.tool_error("version", {}, server))
        now[0] += 61
        self.ok("version", {}, server)


class MalformedTests(Base):
    def test_malformed_messages(self):
        handle_line = self.server.handle_line
        self.assertEqual(handle_line(b"not json")["error"]["code"], -32700)
        self.assertEqual(handle_line(b"\xff\xfe")["error"]["code"], -32700)
        self.assertEqual(handle_line(b"[{}]")["error"]["code"], -32600)
        self.assertEqual(handle_line(b'"text"')["error"]["code"], -32600)
        self.assertEqual(handle_line(b'{"id":1,"method":"ping"}')["error"]["code"], -32600)
        self.assertEqual(handle_line(b'{"jsonrpc":"2.0","id":null,"method":"ping"}')["error"]["code"], -32600)
        self.assertEqual(handle_line(b'{"jsonrpc":"2.0","id":true,"method":"ping"}')["error"]["code"], -32600)
        self.assertEqual(handle_line(b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":[1]}')["error"]["code"],
                         -32602)
        self.assertEqual(handle_line(b'{"jsonrpc":"2.0","id":1}')["error"]["code"], -32600)
        self.assertIsNone(handle_line(b'{"jsonrpc":"2.0","id":1,"result":{}}'))  # a stray response: ignored
        self.assertIsNone(handle_line(b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{}}'))
        self.legacy()
        reply = handle_line(b'{"jsonrpc":"2.0","id":"m","method":"resources/list"}')
        self.assertEqual((reply["id"], reply["error"]["code"]), ("m", -32601))
        self.assertEqual(handle_line(b"[" * 100000 + b"]" * 100000)["error"]["code"], -32700)


class StdioTests(Base):
    def serve(self, data: bytes, **kwargs) -> tuple[int, list[dict], str]:
        args = make_parser().parse_args(["--db", str(self.db), "--accounts", str(self.accounts), "mcp-serve",
                                         "--root", str(self.base), *kwargs.get("extra", [])])
        out, err = io.BytesIO(), io.StringIO()
        code = mcp_server.serve(args, stdin=io.BytesIO(data), stdout=out, stderr=err)
        lines = out.getvalue().decode("utf-8").splitlines()
        return code, [json.loads(line) for line in lines], err.getvalue()

    def test_eof_and_blank_lines(self):
        code, replies, log = self.serve(b"")
        self.assertEqual((code, replies), (0, []))
        self.assertIn("end of input", log)
        code, replies, _ = self.serve(b"\n   \n")
        self.assertEqual((code, replies), (0, []))

    def test_session_over_stdio_and_oversized_line(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-11-25", "capabilities": {}}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "version", "arguments": {}}}]
        data = b"".join(json.dumps(m).encode() + b"\n" for m in messages)
        data += b"x" * (mcp_server.MAX_LINE_BYTES + 10) + b"\n"
        data += json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}).encode()  # last line without newline
        code, replies, log = self.serve(data)
        self.assertEqual(code, 0)
        self.assertEqual([r.get("id") for r in replies], [1, 2, None, 3])
        self.assertEqual(replies[1]["result"]["structuredContent"]["exit_code"], 0)
        self.assertEqual(replies[2]["error"]["code"], -32600)
        self.assertNotIn("version", log.split("tool version")[0].split("ready")[0])  # nothing logged before ready

    def test_subprocess_stdout_carries_only_json_rpc(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": MODERN_META}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"_meta": MODERN_META, "name": "context", "arguments": {}}}]
        data = "".join(json.dumps(m) + "\n" for m in messages) + "{broken\n"
        proc = subprocess.run([sys.executable, "-m", "reachweaver", "--db", str(self.db), "mcp-serve", "--root",
                               str(self.base)], input=data.encode(), capture_output=True, cwd=self.base,
                              env={**_child_env(), **CLEAN_ENV}, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines()]
        self.assertEqual([r.get("id") for r in replies], [1, 2, None])
        self.assertEqual(replies[1]["result"]["structuredContent"]["result"]["context"], "reachweaver_context")
        self.assertIn(b"ready:", proc.stderr)
        self.assertFalse(self.db.exists())

    def test_bad_limits_are_refused(self):
        for extra in (["--max-output-bytes", "10"], ["--max-calls-per-minute", "0"], ["--root", "/nonexistent-x"]):
            args = make_parser().parse_args(["mcp-serve", *extra])
            with self.assertRaises(ValidationError):
                mcp_server.server_from_args(args)


def _child_env() -> dict:
    import os
    env = {key: value for key, value in os.environ.items() if not key.startswith("REACHWEAVER_")}
    env["PYTHONPATH"] = str(ROOT)
    return env


if __name__ == "__main__":
    unittest.main()
