"""engage-scout's connector tries IPv4 before IPv6, with a short per-address timeout (offline; sockets faked).

On a network with a broken IPv6 route, urllib tried every AAAA address with the full timeout first: one
mastodon.social request took 40 s (measured 2026-10-21).
"""
from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from reachweaver import engage_sources, netconnect

V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))


class FakeSocket:
    attempts: list = []

    def __init__(self, family, kind, proto):
        self.family, self.timeouts, self.closed = family, [], False

    def settimeout(self, value):
        self.timeouts.append(value)

    def bind(self, address):
        pass

    def connect(self, sockaddr):
        FakeSocket.attempts.append((self.family, sockaddr[0], self.timeouts[-1]))
        if sockaddr[0] in FakeSocket.unreachable:
            raise TimeoutError("timed out")

    def close(self):
        self.closed = True


class ConnectOrderTests(unittest.TestCase):
    def setUp(self):
        FakeSocket.attempts = []
        FakeSocket.unreachable = set()

    def connect(self, infos):
        with patch.object(netconnect.socket, "getaddrinfo", return_value=list(infos)), \
                patch.object(netconnect.socket, "socket", FakeSocket):
            return engage_sources.connect_ipv4_first(("example.org", 443), 15)

    def test_ipv4_is_tried_first_with_a_short_connect_timeout_then_the_read_timeout(self):
        sock = self.connect([V6, V4])
        self.assertEqual(FakeSocket.attempts, [(socket.AF_INET, "192.0.2.1", engage_sources.CONNECT_TIMEOUT_SECONDS)])
        self.assertEqual(sock.timeouts[-1], 15)

    def test_ipv6_still_works_when_ipv4_fails(self):
        FakeSocket.unreachable = {"192.0.2.1"}
        sock = self.connect([V6, V4])
        self.assertEqual([a[0] for a in FakeSocket.attempts], [socket.AF_INET, socket.AF_INET6])
        self.assertEqual(sock.family, socket.AF_INET6)

    def test_all_unreachable_raises_the_last_error(self):
        FakeSocket.unreachable = {"192.0.2.1", "2001:db8::1"}
        with self.assertRaises(TimeoutError):
            self.connect([V6, V4])

    def test_the_opener_uses_the_ipv4_first_handler_and_keeps_certificate_checks(self):
        import ssl
        # Python 3.12+ builds the default context in the handler; 3.11 leaves it None and the connection builds
        # the same default, verifying context. Check the context a connection actually uses.
        handler_context = engage_sources.IPv4FirstHTTPSHandler()._context
        context = netconnect.IPv4FirstHTTPSConnection("example.org", 443, context=handler_context)._context
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)



class SharedConnectorTests(unittest.TestCase):
    """The send path, the updater and the scout all open HTTPS through netconnect (draft for owner review)."""

    def test_every_opener_uses_the_ipv4_first_handler(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "reachweaver"
        for name in ("publishers.py", "updater.py", "engage_sources.py"):
            with self.subTest(module=name):
                self.assertIn("IPv4FirstHTTPSHandler()", (root / name).read_text(encoding="utf-8"))

    def test_a_proxy_tunnel_keeps_the_stdlib_connect(self):
        conn = netconnect.IPv4FirstHTTPSConnection("example.org", 443, timeout=5)
        conn.set_tunnel("target.example", 443)
        with patch.object(netconnect.http.client.HTTPSConnection, "connect", return_value=None) as stdlib, \
                patch.object(netconnect, "connect_ipv4_first") as ours:
            conn.connect()
        stdlib.assert_called_once()
        ours.assert_not_called()


if __name__ == "__main__":
    unittest.main()
