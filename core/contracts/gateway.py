"""What the gateway tells the other modules: how many windows hold the stream of events."""
from __future__ import annotations

from .frozen import Frozen


class SseClients(Frozen):
    """The event `gateway.sse.clients`: the count of the connected windows. Zero ends the ephemeral states that were meant for
    a window being there (a series watched); a count above zero is a window that connects, and it is told the pictures of the
    queues at once."""

    count: int
