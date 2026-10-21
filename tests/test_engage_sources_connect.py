"""engage-scout's connector tries IPv4 before IPv6, with a short per-address timeout (offline; sockets faked).

On a network with a broken IPv6 route, urllib tried every AAAA address with the full timeout first: one
mastodon.social request took 40 s (measured 2026-10-21).
"""
from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from reachweaver import engage_sources

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
        with patch.object(engage_sources.socket, "getaddrinfo", return_value=list(infos)), \
                patch.object(engage_sources.socket, "socket", FakeSocket):
            return engage_sources._connect_ipv4_first(("example.org", 443), 15)

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
        context = engage_sources._IPv4FirstHTTPSHandler()._context
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
