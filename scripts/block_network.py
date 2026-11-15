"""A pytest plugin that proves a test run never uses the network.

It refuses, and counts, every connection or DNS lookup that is not to this machine. Use it like this:

    GEMINI_API_KEY=fake-key PYTHONPATH=scripts python -m pytest -p block_network -q

The last lines of the run report how many attempts were made; for this project it must be 0.
"""

import socket

attempts: list[tuple[str, object]] = []
_connect = socket.socket.connect
_getaddrinfo = socket.getaddrinfo
LOCAL = {"127.0.0.1", "::1", "localhost", "", None}


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else None
    if isinstance(address, tuple) and host not in LOCAL:
        attempts.append(("connect", host))
        raise OSError("network blocked by scripts/block_network.py")
    return _connect(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host not in LOCAL:
        attempts.append(("dns", host))
        raise OSError("network blocked by scripts/block_network.py")
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _guarded_connect
socket.getaddrinfo = _guarded_getaddrinfo


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"NETWORK ATTEMPTS (non-loopback connect or DNS lookup): {len(attempts)} {attempts[:5]}")
