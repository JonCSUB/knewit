"""Quiz game logic and state management."""
import asyncio
import json
import time
from typing import Optional, Dict

from .quiz_types import (
    QuizSession, QuizState, get_session, update_session_state
)

READ_TIME = 5  # seconds to read question before answers appear
ANSWER_TIME = 10  # seconds to answer each question
REVIEW_TIME = 3  # seconds to show results before next question

async def handle_quiz_tick(session: QuizSession):
    """Handle state transitions and timing for a quiz session."""
    now = time.time()
    elapsed = now - session.state_change_time

    if session.state == QuizState.LOBBY:
        # Stay in lobby until host starts
        return

    elif session.state == QuizState.READING:
        if elapsed >= READ_TIME:
            # Reading time over, show answers
            update_session_state(session, QuizState.ANSWERING)
            await broadcast_question_answers(session)

    elif session.state == QuizState.ANSWERING:
        if elapsed >= ANSWER_TIME:
            # Answer time over, show results
            update_session_state(session, QuizState.REVIEWING)
            await broadcast_results(session)

    elif session.state == QuizState.REVIEWING:
        if elapsed >= REVIEW_TIME:
            # Move to next question or end quiz
            if session.current_question_idx + 1 < len(session.questions):
                session.current_question_idx += 1
                update_session_state(session, QuizState.READING)
                await broadcast_next_question(session)
            else:
                # Quiz complete
                await broadcast_final_scores(session)
                session.state = QuizState.LOBBY

async def start_quiz(session: QuizSession):
    """Start the quiz, moving from lobby to first question."""
    if session.state != QuizState.LOBBY:
        return
    
    if not session.players:
        return  # Need at least one player
    
    session.current_question_idx = 0
    session.answers.clear()
    update_session_state(session, QuizState.READING)
    await broadcast_next_question(session)

async def submit_answer(session: QuizSession, player_id: str, answer_idx: int) -> bool:
    """Submit an answer for the current question. Returns True if accepted."""
    if session.state != QuizState.ANSWERING:
        return False
    
    if player_id not in session.players:
        return False
    
    if player_id in session.answers:
        return False  # Already answered
    
    session.answers[player_id] = answer_idx
    
    # Update score if correct
    current_q = session.questions[session.current_question_idx]
    if answer_idx == current_q.correct_idx:
        # Basic scoring: +1 for correct answer
        session.scores[player_id] = session.scores.get(player_id, 0) + 1
    
    # Broadcast updated histogram
    await broadcast_answer_counts(session)
    return True

async def broadcast_next_question(session: QuizSession):
    """Send the next question to all players (without answers during reading)."""
    if session.current_question_idx >= len(session.questions):
        return
    
    q = session.questions[session.current_question_idx]
    msg = {
        "type": "question.next",
        "question_id": q.id,
        "prompt": q.prompt,
        "options": ["?" for _ in q.options],  # Hide answers during reading
        "state": session.state.value,
        "ends_in": READ_TIME
    }
    await broadcast(session, msg)

async def broadcast_question_answers(session: QuizSession):
    """Show the answer options after reading period."""
    if session.current_question_idx >= len(session.questions):
        return
    
    q = session.questions[session.current_question_idx]
    msg = {
        "type": "question.answers",
        "question_id": q.id,
        "options": q.options,
        "state": session.state.value,
        "ends_in": ANSWER_TIME
    }
    await broadcast(session, msg)

async def broadcast_results(session: QuizSession):
    """Send results for current question to all players."""
    if session.current_question_idx >= len(session.questions):
        return
    
    q = session.questions[session.current_question_idx]
    counts = [0] * len(q.options)
    for ans in session.answers.values():
        if 0 <= ans < len(counts):
            counts[ans] += 1
    
    msg = {
        "type": "question.results",
        "question_id": q.id,
        "counts": counts,
        "correct_idx": q.correct_idx,
        "state": session.state.value,
        "ends_in": REVIEW_TIME
    }
    await broadcast(session, msg)

async def broadcast_answer_counts(session: QuizSession):
    """Send updated answer counts (during answering phase)."""
    if session.current_question_idx >= len(session.questions):
        return
    
    q = session.questions[session.current_question_idx]
    counts = [0] * len(q.options)
    for ans in session.answers.values():
        if 0 <= ans < len(counts):
            counts[ans] += 1
    
    msg = {
        "type": "answer.counts",
        "counts": counts
    }
    await broadcast(session, msg)

async def broadcast_final_scores(session: QuizSession):
    """Send final scores to all players."""
    scores_list = [
        {"player_id": pid, "score": score}
        for pid, score in session.scores.items()
    ]
    scores_list.sort(key=lambda x: x["score"], reverse=True)
    
    msg = {
        "type": "quiz.end",
        "scores": scores_list
    }
    await broadcast(session, msg)

async def broadcast(session: QuizSession, payload: dict):
    """Send a message to all connected players in the session."""
    data = json.dumps(payload)
    dead = []
    
    for player_id, ws in session.connections.items():
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(player_id)
    
    # Clean up dead connections
    for player_id in dead:
        session.connections.pop(player_id, None)