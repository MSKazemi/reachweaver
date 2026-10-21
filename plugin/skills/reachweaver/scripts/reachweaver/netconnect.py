"""HTTPS connections that try IPv4 before IPv6, each address with a short connect timeout.

On a network with a broken IPv6 route, ``socket.create_connection`` (used by urllib) tried every AAAA address
with the full request timeout before any IPv4 address: one mastodon.social request took 40 s (measured
2026-10-21 on the owner's laptop; curl, which races both families, answered in 0.3 s). IPv6 is still tried
after IPv4, so an IPv6-only network keeps working. Read timeouts, certificate and hostname checks, redirect
handling and every request header are unchanged: only the order and per-address timeout of the connect
step differ. Standard library only; no request is made here.
"""
from __future__ import annotations

import http.client
import socket
import time
from urllib.request import HTTPSHandler

CONNECT_TIMEOUT_SECONDS = 4  # per address; the whole connect is still bounded by the request timeout
DEFAULT_TIMEOUT_SECONDS = 15


def connect_ipv4_first(address, timeout, source_address=None, *_args, **_kwargs):
    """``socket.create_connection`` with IPv4 addresses first and a short connect timeout per address."""
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    infos.sort(key=lambda info: info[0] != socket.AF_INET)  # stable: IPv4 first, resolver order kept
    budget = timeout if isinstance(timeout, (int, float)) else DEFAULT_TIMEOUT_SECONDS
    deadline = time.monotonic() + budget
    last: OSError | None = None
    for family, kind, proto, _name, sockaddr in infos:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(min(CONNECT_TIMEOUT_SECONDS, left))
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            sock.settimeout(timeout)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise last or OSError("no address could be reached")


class IPv4FirstHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):  # the stdlib method with the IPv4-first connector
        if getattr(self, "_tunnel_host", None):  # through an HTTPS proxy: keep the stdlib path unchanged
            return super().connect()
        sock = connect_ipv4_first((self.host, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


class IPv4FirstHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(IPv4FirstHTTPSConnection, req, context=self._context)  # certificate checks unchanged
