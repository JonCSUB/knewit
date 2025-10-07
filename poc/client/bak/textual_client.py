# quiz-poc/client/textual_client.py
# ---------------------------------------------
# Textual-based terminal UI client that:
# - Keeps ONE persistent WebSocket connection open
# - Receives events AND sends answers over the same socket
# - Replies to heartbeat pings with 'pong'
# - Shows a sparkline histogram; press 1–4 to submit answers
# ---------------------------------------------

import asyncio
import json
import os

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Sparkline
from textual.reactive import reactive

import websockets  # pip install websockets

SERVER = os.environ.get("QUIZ_SERVER", "ws://127.0.0.1:8000")
SESSION = os.environ.get("QUIZ_SESSION", "demo")
PLAYER  = os.environ.get("QUIZ_PLAYER", "player1")


class QuizClient(App):
    CSS = """
    Screen { layout: vertical; padding: 1; }
    #title { content-align: center middle; height: 3; }
    #prompt { height: 3; }
    #options { height: 3; }
    #hist { height: 5; }
    """

    # Reactive state (Textual redraws bound widgets when these change)
    bins = reactive([0, 0, 0, 0])
    prompt_txt = reactive("Waiting for next question...")
    options_txt = reactive("")

    # --- Runtime fields (set at runtime) ---
    ws = None                      # current WebSocket (or None)
    send_q: asyncio.Queue | None = None  # queue of outgoing messages (coroutines put dicts here)
    stop = False                   # flip to True on app shutdown

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Quiz TUI (press 1–4 to answer) — q to quit", id="title")
        yield Static(self.prompt_txt, id="prompt")
        yield Static(self.options_txt, id="options")
        self.spark = Sparkline(self.bins, id="hist")
        yield self.spark
        yield Footer()

    async def on_mount(self):
        """Start (and maintain) a persistent WebSocket connection."""
        self.run_worker(self.connection_supervisor(), exclusive=True)

    async def connection_supervisor(self):
        """
        Keep reconnecting until app stops:
        - Connect WS
        - Start receiver and sender tasks sharing the same WS
        - If either fails, cancel both and retry after a short backoff
        """
        backoff = 1
        while not self.stop:
            uri = f"{SERVER}/ws?session_id={SESSION}&player_id={PLAYER}"
            try:
                async with websockets.connect(uri, ping_interval=None) as ws:
                    self.ws = ws
                    self.send_q = asyncio.Queue()
                    # Kick off sender & receiver tasks
                    sender = asyncio.create_task(self.sender_task(ws, self.send_q))
                    receiver = asyncio.create_task(self.receiver_task(ws))
                    # Wait for either to finish (error/close)
                    done, pending = await asyncio.wait(
                        {sender, receiver},
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    # Cancel the other task and drain queue
                    for t in pending:
                        t.cancel()
                    # If we got here, connection ended; reset fields
                    self.ws = None
                    self.send_q = None
            except Exception:
                # Couldn’t connect; fall through to backoff
                pass

            # Reconnect backoff (up to 15s)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15)

    async def receiver_task(self, ws):
        """Receive messages from server and update UI / reply to heartbeats."""
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "ping":
                # Reply to server heartbeat promptly
                await ws.send(json.dumps({"type": "pong", "ts": msg.get("ts")}))
                continue

            if t == "question.next":
                self.prompt_txt = f"Q: {msg['prompt']}"
                self.options_txt = "  ".join(
                    f"{i+1}) {opt}" for i, opt in enumerate(msg["options"])
                )
                continue

            if t == "histogram":
                # Ensure list order 0..3 for Sparkline
                self.bins = [msg["bins"].get(i, 0) for i in range(4)]
                self.spark.data = self.bins
                self.spark.refresh()
                continue

    async def sender_task(self, ws, q: asyncio.Queue):
        """Continuously send JSON messages placed on send_q."""
        while True:
            payload = await q.get()  # dict
            try:
                await ws.send(json.dumps(payload))
            finally:
                q.task_done()

    async def on_key(self, event):
        """
        Handle key presses:
        - 1–4: enqueue an 'answer.submit' message onto the persistent WS
        """
        if event.key in ("1", "2", "3", "4"):
            idx = int(event.key) - 1
            if self.send_q is not None:
                await self.send_q.put({"type": "answer.submit", "answer_idx": idx})

    async def on_unmount(self):
        """Signal shutdown to the supervisor loop."""
        self.stop = True


if __name__ == "__main__":
    QuizClient().run()
