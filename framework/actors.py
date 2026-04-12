"""WebSocket client wrappers for admin and client actors."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

import anyio
from httpx_ws import AsyncWebSocketSession, WebSocketDisconnect, aconnect_ws

from framework.models import ActorRole

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _safe_aconnect_ws(
    url: str, client: httpx.AsyncClient
) -> AsyncIterator[AsyncWebSocketSession]:
    """Wrap aconnect_ws, suppressing EndOfStream on exit."""
    try:
        async with aconnect_ws(url, client) as ws:
            yield ws
    except* BaseException as eg:  # noqa: BLE001
        # Filter out EndOfStream / WebSocketDisconnect that happen when
        # the server or test intentionally closes the connection.
        real_errors = [exc for exc in eg.exceptions if not _is_benign_close(exc)]
        if real_errors:
            msg = "ws errors"
            raise BaseExceptionGroup(msg, real_errors) from None


def _is_benign_close(exc: BaseException) -> bool:
    """Return True if the exception is a normal connection-close side effect."""
    if isinstance(exc, (anyio.EndOfStream, WebSocketDisconnect)):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return all(_is_benign_close(e) for e in exc.exceptions)
    return False


class Actor:
    """Base actor holding a WebSocket session."""

    def __init__(
        self,
        name: str,
        role: ActorRole,
        client: httpx.AsyncClient,
        exit_stack: AsyncExitStack,
    ) -> None:
        """Initialise actor with connection details (not yet connected)."""
        self.name = name
        self.role = role
        self._client = client
        self._exit_stack = exit_stack
        self._ws: AsyncWebSocketSession | None = None

    @property
    def ws(self) -> AsyncWebSocketSession:
        """Return the active WebSocket session."""
        if self._ws is None:
            msg = f"Actor '{self.name}' is not connected"
            raise RuntimeError(msg)
        return self._ws

    async def connect(
        self,
        base_url: str,
        quiz_data: dict[str, Any] | None = None,
    ) -> None:
        """Open a WebSocket connection."""
        if self.role == ActorRole.admin:
            url = f"{base_url}/admin"
        else:
            url = f"{base_url}/connect/{self.name}"

        self._ws = await self._exit_stack.enter_async_context(
            _safe_aconnect_ws(url, self._client),
        )

        if self.role == ActorRole.admin and quiz_data is not None:
            await self._ws.send_json(quiz_data)

    async def send(self, data: str | dict[str, Any]) -> None:
        """Send a text or JSON message."""
        if isinstance(data, str):
            await self.ws.send_text(data)
        else:
            await self.ws.send_json(data)

    async def receive_raw(self, timeout: float = 5.0) -> str:  # noqa: ASYNC109
        """
        Receive the next message as a raw string.

        FastAPI sends both send_text and send_json as text frames,
        so receive_text captures everything.
        """
        return await self.ws.receive_text(timeout=timeout)

    async def expect_nothing(self, timeout: float = 0.5) -> None:  # noqa: ASYNC109
        """Assert no message arrives within *timeout* seconds."""
        try:
            msg = await self.ws.receive_text(timeout=timeout)
        except TimeoutError:
            return
        else:
            err = f"Actor '{self.name}' expected no message but received: {msg!r}"
            raise AssertionError(err)

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
