# client/ws_client.py
# =====================================================================================
# PURPOSE
#   Reusable WebSocket client (no UI code) that:
#     - Maintains ONE persistent connection to the server
#     - Auto-reconnects with backoff if the connection drops
#     - Replies to 'ping' with 'pong' (heartbeat)
#     - Exposes `send(payload: dict)` and async callback `on_event(msg: dict)`
#
# KEY TECHNOLOGIES
#   - websockets: lightweight WS library for asyncio
#   - asyncio: Queue for outbound messages; tasks for recv/send loops
# =====================================================================================

import asyncio
import json
from typing import Awaitable, Callable

import websockets  # pip install websockets


class WSClient:
    """Transport-only WebSocket client.

    Parameters
    ----------
    url : str
        Full ws:// or wss:// URL, including query params for session/player.
        Example: ws://127.0.0.1:8000/ws?session_id=demo&player_id=alice
    on_event : Callable[[dict], Awaitable[None]]
        Async callback invoked with every non-heartbeat message from the server.
        Your UI (Textual/Qt/etc.) implements this to update the screen.
    """

    def __init__(self, url: str, on_event: Callable[[dict], Awaitable[None]]):
        self.url = url
        self.on_event = on_event
        # asyncio.Queue is a thread-safe (coroutine-safe) FIFO; we use it to
        # serialize all outbound messages through one place.
        self.send_q: asyncio.Queue[dict] = asyncio.Queue()
        self._stop = False

    async def start(self):
        """Run forever (until stop() is called) and keep a live connection.

        This method:
          - attempts to connect,
          - starts receiver & sender tasks,
          - waits until either finishes,
          - then reconnects with exponential backoff if needed.
        """
        backoff = 1
        while not self._stop:
            try:
                # Enable built-in ping/pong with a reasonable interval
                async with websockets.connect(
                    self.url,
                    ping_interval=20,  # Send ping every 20 seconds
                    ping_timeout=10,   # Wait 10 seconds for pong response
                    close_timeout=5,   # Wait 5 seconds for close handshake
                    max_size=2**23,    # Larger message size limit (~8MB)
                ) as ws:
                    sender = asyncio.create_task(self._sender(ws))
                    receiver = asyncio.create_task(self._receiver(ws))
                    pending = set()

                    try:
                        # Wait until either sender or receiver exits
                        done, pending = await asyncio.wait(
                            {sender, receiver},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        # Get any exceptions
                        for task in done:
                            try:
                                await task
                            except Exception as e:
                                print(f"Task failed: {e}")
                                
                    finally:
                        # Always clean up tasks
                        for t in pending:
                            t.cancel()
                        try:
                            await ws.close()
                        except Exception:
                            pass
                            
            except Exception as e:
                # Connection failed or dropped. Wait a bit (exponential backoff)
                # then try again.
                print(f"WebSocket error: {e}")
                if not self._stop:  # Only sleep if we're not stopping
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 15)
            else:
                # If the connection ran to completion "cleanly", reset backoff.
                backoff = 1

    async def _receiver(self, ws):
        """Receive loop (asyncio Task).

        Reads text frames from the socket, parses JSON, and:
          - if it's a 'ping', immediately sends a 'pong' (heartbeat)
          - else, forwards the message dict to the UI via on_event(...)
        """
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)

                    # Heartbeat handling: server pings → we pong
                    if msg.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong", "ts": msg.get("ts")}))
                        continue

                    # Domain events (welcome, question.next, histogram, etc.)
                    await self.on_event(msg)
                except Exception as e:
                    print(f"Error processing message: {e}")
                    import traceback
                    traceback.print_exc()
        except Exception as e:
            print(f"Receiver loop error: {e}")
            raise

    async def _sender(self, ws):
        """Sender loop (asyncio Task).

        Waits for dicts placed on the send queue and writes them
        to the websocket as JSON strings.
        """
        while True:
            payload = await self.send_q.get()
            try:
                await ws.send(json.dumps(payload))
            finally:
                # Signals that one queue item is fully processed.
                self.send_q.task_done()

    async def send(self, payload: dict):
        """Public API to enqueue an outbound message (non-blocking)."""
        await self.send_q.put(payload)

    def stop(self):
        """Signal the reconnect loop to exit (used on UI shutdown)."""
        self._stop = True
