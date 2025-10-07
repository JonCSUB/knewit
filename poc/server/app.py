# server/app.py
# =====================================================================================
# PURPOSE
#   Minimal quiz server using FastAPI (ASGI) that:
#     - Accepts WebSocket clients on /ws
#     - Sends/receives JSON messages (heartbeats + quiz events)
#     - Tracks connections per "session" (a game room)
#     - Ticks questions periodically per session (simple PoC)
#     - Tallies answers and broadcasts a histogram as a *list* [A,B,C,D]
#
# KEY TECHNOLOGIES
#   - FastAPI: web framework (HTTP + WebSocket routes)
#   - Uvicorn: ASGI server that runs this app (`uvicorn server.app:app ...`)
#   - asyncio: concurrency (background tasks; non-blocking I/O)
#
# RUN
#   uvicorn server.app:app --host 0.0.0.0 --port 8000 --log-level debug
# =====================================================================================

import asyncio
import json
import time
from typing import Dict, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# -----------------------------
# In-memory application state
# -----------------------------
# NOTE: This lives inside the *process*. In real deployments with multiple
# workers or machines, you'd move this to Redis/DB so it's shared.

# session_id -> set of active WebSocket connections
rooms: Dict[str, Set[WebSocket]] = {}

# session_id -> histogram counts (by option index)  {0: int, 1: int, 2: int, 3: int}
# We keep counts internally as a dict (easy to increment by key),
# but when we SEND to clients we convert to a list [A,B,C,D] to avoid the
# JSON "string keys" gotcha.
answers: Dict[str, Dict[int, int]] = {}

# Track last heartbeat "pong" time per WebSocket so we can close dead sockets.
last_pong: Dict[WebSocket, float] = {}

# For each session, we run one asyncio.Task that periodically starts a new question.
session_tasks: Dict[str, asyncio.Task] = {}

# Heartbeat config (seconds)
PING_INTERVAL = 10          # how often we send pings
DROP_AFTER = 25             # how long we'll tolerate no pong before closing

# Question loop config (seconds)
QUESTION_INTERVAL = 10      # how often to auto-advance questions (PoC)

# We'll keep a reference to the ping background task so we can cancel it on shutdown.
_ping_task: asyncio.Task | None = None


# -------------------------------------------------------------------------------------
# Lifespan handler (modern FastAPI)
# -------------------------------------------------------------------------------------
# Replaces deprecated @app.on_event("startup"/"shutdown").
# Starts background tasks when app boots; cancels them cleanly on shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ping_task
    print("[lifespan] starting ping loop")

    # Create a background asyncio task (non-blocking).
    # This runs concurrently with request handling in the same event loop.
    _ping_task = asyncio.create_task(ping_loop(), name="ping_loop")
    try:
        # Yield control back to FastAPI/Uvicorn; the app is now "running".
        yield
    finally:
        print("[lifespan] shutting down")

        # Cancel global ping loop.
        if _ping_task:
            _ping_task.cancel()
        await asyncio.gather(*[t for t in (_ping_task,) if t], return_exceptions=True)

        # Cancel per-session tickers.
        for t in session_tasks.values():
            t.cancel()
        await asyncio.gather(*session_tasks.values(), return_exceptions=True)

        print("[lifespan] bye")


# Create the FastAPI app, wiring in our lifespan handler above.
app = FastAPI(lifespan=lifespan)

# CORS middleware lets browser clients from other origins talk to us in PoC scenarios.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------------------------------------------------------------
# Simple REST endpoint (optional health check)
# -------------------------------------------------------------------------------------
@app.get("/ping")
def ping():
    """HTTP GET /ping → JSON. Quick health check via browser/curl."""
    return {"ok": True, "ts": time.time()}


# -------------------------------------------------------------------------------------
# WebSocket endpoint (core transport)
# -------------------------------------------------------------------------------------
# REQUIRED BY: FastAPI (route) + ASGI (WebSocket support)
# PURPOSE:
#   - Accept a persistent, bidirectional connection.
#   - Receive JSON messages from client (e.g., 'pong', 'answer.submit').
#   - Send JSON events to client (e.g., 'welcome', 'question.next', 'histogram').
@app.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    session_id: str = "demo",   # query param ?session_id=demo
    player_id: str = "anon",    # query param ?player_id=alice
):
    # Accept the WebSocket handshake.
    await ws.accept()
    print(f"[ws] accept session={session_id} player={player_id}")

    # Get-or-create the room and the tally dict for this session.
    rooms.setdefault(session_id, set()).add(ws)
    answers.setdefault(session_id, {0: 0, 1: 0, 2: 0, 3: 0})

    # Record a "last ping" time so the heartbeat loop knows you're alive.
    last_pong[ws] = time.time()

    # If this is the first connection for the session, start its ticker task.
    if session_id not in session_tasks:
        print(f"[ws] start session_ticker for {session_id}")
        session_tasks[session_id] = asyncio.create_task(session_ticker(session_id))

    # Send a welcome message (useful for UI status lines).
    await ws.send_text(json.dumps({"type": "welcome", "session_id": session_id}))

    # Immediately send a first question *just* to this socket, so there's no wait.
    await send_first_question_direct(ws, session_id)

    try:
        # Main receive loop: read messages from this client until it disconnects.
        while True:
            # await = yield control while waiting for network I/O (asyncio non-blocking)
            raw = await ws.receive_text()
            data = json.loads(raw)
            t = data.get("type")

            # Heartbeat reply from client.
            if t == "pong":
                last_pong[ws] = time.time()
                continue

            # Player submitted an answer.
            if t == "answer.submit":
                # Integer index 0..3 corresponding to A..D
                opt = int(data["answer_idx"])

                # Increment internal dict count if the index exists
                if opt in answers[session_id]:
                    answers[session_id][opt] += 1

                # Convert dict with int keys -> list [A,B,C,D] for clients (JSON-safe)
                bins_list = [answers[session_id].get(i, 0) for i in range(4)]

                # Broadcast histogram as a list to everyone in this session
                await broadcast(session_id, {"type": "histogram", "bins": bins_list})
                print(f"[ws] answer session={session_id} bins={bins_list}")

    except WebSocketDisconnect:
        # Normal close (tab closed, Wi-Fi hiccup, etc.)
        print(f"[ws] disconnect session={session_id} player={player_id}")
    finally:
        # Clean up: remove from room and heartbeat tracking
        rooms.get(session_id, set()).discard(ws)
        last_pong.pop(ws, None)

        # If room is empty, stop its ticker task
        if not rooms.get(session_id):
            task = session_tasks.pop(session_id, None)
            if task:
                print(f"[ws] stop session_ticker for {session_id} (room empty)")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


# -------------------------------------------------------------------------------------
# Utility: send first question directly to one socket (no waiting)
# -------------------------------------------------------------------------------------
async def send_first_question_direct(ws: WebSocket, session_id: str):
    """Send a fresh question to just this socket, and reset bins."""
    answers[session_id] = {0: 0, 1: 0, 2: 0, 3: 0}
    payload = {
        "type": "question.next",
        "prompt": "Which option do you pick?",
        "options": ["A", "B", "C", "D"],  # list of answer labels
        "ends_in": QUESTION_INTERVAL - 2,  # cosmetic hint for clients
    }
    try:
        await ws.send_text(json.dumps(payload))
        print(f"[direct] sent question to one client in session={session_id}")
    except Exception as e:
        print(f"[direct] failed to send direct question: {e}")


# -------------------------------------------------------------------------------------
# Utility: broadcast JSON payload to all sockets in a session
# -------------------------------------------------------------------------------------
async def broadcast(session_id: str, payload: dict):
    """Loop over the room's sockets and send the same message to each."""
    dead = []
    for ws in rooms.get(session_id, set()):
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            # If we can't write, mark socket for removal
            dead.append(ws)

    # Remove sockets that failed during send (connection likely dead)
    for ws in dead:
        rooms[session_id].discard(ws)
        last_pong.pop(ws, None)

    # Optional instrumentation
    if payload.get("type") == "question.next":
        print(f"[broadcast] question to session={session_id} size={len(rooms.get(session_id,set()))}")
    if payload.get("type") == "histogram":
        print(f"[broadcast] histogram to session={session_id} {payload.get('bins')}")


# -------------------------------------------------------------------------------------
# Background task: heartbeat (server -> client)
# -------------------------------------------------------------------------------------
# REQUIRED BY: "liveness" detection. We proactively ping; client must respond "pong".
async def ping_loop():
    """Every PING_INTERVAL seconds:
       - send 'ping' to all sockets
       - close sockets that haven't ponged within DROP_AFTER seconds.
    """
    while True:
        await asyncio.sleep(PING_INTERVAL)
        now = time.time()

        # Send ping to all sockets we track
        for ws in list(last_pong.keys()):
            try:
                await ws.send_text(json.dumps({"type": "ping", "ts": now}))
            except Exception:
                # If we can't send, forget this socket; receiver will clean up
                last_pong.pop(ws, None)

        # Drop sockets that haven't ponged in time
        for ws, ts in list(last_pong.items()):
            if now - ts > DROP_AFTER:
                try:
                    await ws.close()
                except Exception:
                    pass
                last_pong.pop(ws, None)


# -------------------------------------------------------------------------------------
# Background task: per-session question ticker (PoC)
# -------------------------------------------------------------------------------------
# REQUIRED BY: Nothing—purely PoC. In a real app, the "host" would trigger next question.
async def session_ticker(session_id: str):
    """While a session has members, auto-advance questions every QUESTION_INTERVAL."""
    try:
        while True:
            await asyncio.sleep(QUESTION_INTERVAL)

            # If empty, idle briefly and loop
            if not rooms.get(session_id):
                await asyncio.sleep(1)
                continue

            # Reset counts and broadcast a new question to everyone in this session
            answers[session_id] = {0: 0, 1: 0, 2: 0, 3: 0}
            await broadcast(
                session_id,
                {
                    "type": "question.next",
                    "prompt": "Which option do you pick?",
                    "options": ["A", "B", "C", "D"],
                    "ends_in": QUESTION_INTERVAL - 2,
                },
            )
    except asyncio.CancelledError:
        # Normal cancellation on shutdown or when room empties
        pass
