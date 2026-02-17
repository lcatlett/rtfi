"""Optional StatsD-compatible metrics export (L3).

Emits metrics only when RTFI_STATSD_HOST is set. Uses UDP with fire-and-forget
semantics — metric emission never blocks or slows hook execution.

No external dependencies required.
"""

import os
import socket


class StatsD:
    """Minimal StatsD client using UDP (no dependencies)."""

    def __init__(
        self, host: str = "localhost", port: int = 8125, prefix: str = "rtfi"
    ):
        self.host = host
        self.port = port
        self.prefix = prefix
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def gauge(self, name: str, value: float) -> None:
        self._send(f"{self.prefix}.{name}:{value}|g")

    def incr(self, name: str, count: int = 1) -> None:
        self._send(f"{self.prefix}.{name}:{count}|c")

    def timing(self, name: str, ms: float) -> None:
        self._send(f"{self.prefix}.{name}:{ms}|ms")

    def _send(self, data: str) -> None:
        try:
            self._sock.sendto(data.encode(), (self.host, self.port))
        except OSError:
            pass  # Fire-and-forget — never block hook execution


def get_statsd() -> StatsD | None:
    """Return a StatsD client if RTFI_STATSD_HOST is set, else None."""
    host = os.environ.get("RTFI_STATSD_HOST")
    if not host:
        return None
    port = int(os.environ.get("RTFI_STATSD_PORT", "8125"))
    return StatsD(host=host, port=port)
