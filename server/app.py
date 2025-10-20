# server/app_new.py
"""
Enhanced quiz server with lobby system, quiz creation, and session management.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import sys
from pathlib import Path
# Add server directory to path so we can import quiz_types
sys.path.insert(0, str(Path(__file__).parent))

from quiz_types import (
    QuizSession, Quiz, Question, QuizState,
    create_session, get_session, delete_session
)

# Heartbeat config
PING_INTERVAL = 20

# Background task reference
_ping_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan handler."""
    global _ping_task
    print("[lifespan] starting")
    
    _ping_task = asyncio.create_task(ping_loop())
    
    try:
        yield
    finally:
        print("[lifespan] shutting down")
        if _ping_task:
            _ping_task.cancel()
        await asyncio.gather(_ping_task, return_exceptions=True)
        print("[lifespan] bye")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ping")
def health_check():
    """Health check endpoint."""
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    player_id: str = "anon",
    is_host: str = "false"
):
    """WebSocket endpoint for quiz sessions."""
    await ws.accept()
    is_host_bool = is_host.lower() == "true"
    session: QuizSession | None = None
    session_id: str | None = None
    
    print(f"[ws] connect player={player_id} is_host={is_host_bool}")
    
    try:
        # Send welcome
        await ws.send_text(json.dumps({
            "type": "welcome",
            "player_id": player_id,
            "is_host": is_host_bool
        }))
        
        # Main message loop
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")
            
            # Heartbeat
            if msg_type == "pong":
                continue
            
            # Session creation (host only)
            elif msg_type == "session.create" and is_host_bool:
                session = create_session(player_id)
                session_id = session.id
                session.connections[player_id] = ws
                
                await ws.send_text(json.dumps({
                    "type": "session.created",
                    "session_id": session_id
                }))
                print(f"[session] created session_id={session_id} host={player_id}")
            
            # Join session
            elif msg_type == "session.join":
                session_id = data.get("session_id")
                name = data.get("name")
                
                if not session_id or not name:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Missing session_id or name"
                    }))
                    continue
                
                session = get_session(session_id)
                if not session:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Session not found"
                    }))
                    continue
                
                # Add player
                player = session.add_player(player_id, name)
                if not player:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Name already taken"
                    }))
                    continue
                
                session.connections[player_id] = ws
                
                await ws.send_text(json.dumps({
                    "type": "session.joined",
                    "session_id": session_id,
                    "name": name
                }))
                
                # Broadcast updated lobby
                await broadcast_lobby(session)
                print(f"[session] {name} joined session_id={session_id}")
            
            # Load quiz (host only)
            elif msg_type == "quiz.load" and is_host_bool and session:
                quiz_data = data.get("quiz")
                if quiz_data:
                    quiz = Quiz.from_dict(quiz_data)
                    session.load_quiz(quiz)
                    
                    await broadcast(session, {
                        "type": "quiz.loaded",
                        "quiz_title": quiz.title,
                        "num_questions": len(quiz.questions)
                    })
                    print(f"[quiz] loaded quiz={quiz.title} in session={session_id}")
            
            # Save quiz (host only)
            elif msg_type == "quiz.save" and is_host_bool:
                quiz_data = data.get("quiz")
                if quiz_data:
                    quiz = Quiz.from_dict(quiz_data)
                    filepath = quiz.save_to_file()
                    
                    await ws.send_text(json.dumps({
                        "type": "quiz.saved",
                        "filepath": filepath,
                        "quiz_id": quiz.quiz_id
                    }))
                    print(f"[quiz] saved quiz={quiz.title} to {filepath}")
            
            # List saved quizzes
            elif msg_type == "quiz.list":
                quizzes = Quiz.list_saved_quizzes()
                await ws.send_text(json.dumps({
                    "type": "quiz.list",
                    "quizzes": quizzes
                }))
            
            # Start quiz (host only)
            elif msg_type == "quiz.start" and is_host_bool and session:
                if session.start_quiz():
                    # Send first question
                    question = session.next_question()
                    if question:
                        await broadcast(session, {
                            "type": "question.next",
                            "prompt": question.prompt,
                            "options": question.options,
                            "question_num": session.current_question_idx + 1,
                            "total_questions": len(session.quiz.questions)
                        })
                        print(f"[quiz] started in session={session_id}")
                else:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "No quiz loaded"
                    }))
            
            # Next question (host only)
            elif msg_type == "question.next" and is_host_bool and session:
                question = session.next_question()
                if question:
                    await broadcast(session, {
                        "type": "question.next",
                        "prompt": question.prompt,
                        "options": question.options,
                        "question_num": session.current_question_idx + 1,
                        "total_questions": len(session.quiz.questions)
                    })
                else:
                    # Quiz finished
                    await broadcast(session, {
                        "type": "quiz.finished",
                        "leaderboard": [
                            {"name": p.name, "score": p.score}
                            for p in sorted(session.players.values(), key=lambda x: x.score, reverse=True)
                        ]
                    })
                    print(f"[quiz] finished in session={session_id}")
            
            # Submit answer (students)
            elif msg_type == "answer.submit" and session:
                answer_idx = int(data.get("answer_idx", 0))
                correct = session.record_answer(player_id, answer_idx)
                
                # Send confirmation to player
                await ws.send_text(json.dumps({
                    "type": "answer.recorded",
                    "correct": correct
                }))
                
                # Broadcast updated histogram
                bins = [session.answer_counts.get(i, 0) for i in range(4)]
                await broadcast(session, {
                    "type": "histogram",
                    "bins": bins
                })
            
            # Kick player (host only)
            elif msg_type == "player.kick" and is_host_bool and session:
                kick_player_id = data.get("player_id")
                if kick_player_id and kick_player_id in session.connections:
                    kick_ws = session.connections[kick_player_id]
                    await kick_ws.send_text(json.dumps({
                        "type": "kicked",
                        "message": "You were removed from the session"
                    }))
                    await kick_ws.close()
                    session.remove_player(kick_player_id)
                    await broadcast_lobby(session)
                    print(f"[session] kicked player={kick_player_id} from session={session_id}")
    
    except WebSocketDisconnect:
        print(f"[ws] disconnect player={player_id}")
    finally:
        # Cleanup
        if session and player_id in session.connections:
            session.connections.pop(player_id, None)
            
            if is_host_bool:
                # Host disconnected - close session
                print(f"[session] host disconnected, closing session={session_id}")
                await broadcast(session, {
                    "type": "session.closed",
                    "message": "Host disconnected"
                })
                for p_ws in list(session.connections.values()):
                    try:
                        await p_ws.close()
                    except:
                        pass
                if session_id:
                    delete_session(session_id)
            else:
                # Student disconnected
                session.remove_player(player_id)
                await broadcast_lobby(session)


async def broadcast(session: QuizSession, payload: dict):
    """Broadcast message to all connections in a session."""
    dead = []
    for pid, ws in list(session.connections.items()):
        try:
            await ws.send_text(json.dumps(payload))
        except:
            dead.append(pid)
    
    for pid in dead:
        session.connections.pop(pid, None)


async def broadcast_lobby(session: QuizSession):
    """Broadcast lobby state to all connections."""
    await broadcast(session, {
        "type": "lobby.update",
        "players": [p.to_dict() for p in session.players.values()],
        "state": session.state.value
    })


async def ping_loop():
    """Send periodic pings to all connections."""
    while True:
        await asyncio.sleep(PING_INTERVAL)
        # For now, we rely on websocket's built-in ping/pong
        # Could add custom heartbeat logic here if needed


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
