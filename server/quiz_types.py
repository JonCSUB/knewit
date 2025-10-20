"""Quiz data types and state management."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
import time
import secrets
import json
import uuid
from pathlib import Path
from fastapi import WebSocket

class QuizState(Enum):
    LOBBY = "lobby"
    ACTIVE = "active"
    FINISHED = "finished"

@dataclass
class Question:
    prompt: str
    options: List[str]  # 4 options
    correct_idx: int    # 0-3
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "options": self.options,
            "correct_idx": self.correct_idx
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Question":
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            prompt=data["prompt"],
            options=data["options"],
            correct_idx=data["correct_idx"]
        )

@dataclass
class Player:
    """A player in a quiz session."""
    player_id: str
    name: str
    score: int = 0
    answered_current: bool = False
    
    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "score": self.score
        }

@dataclass
class Quiz:
    """A saved quiz with multiple questions."""
    title: str
    questions: List[Question]
    quiz_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    def to_dict(self) -> dict:
        return {
            "quiz_id": self.quiz_id,
            "title": self.title,
            "questions": [q.to_dict() for q in self.questions]
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Quiz":
        return cls(
            quiz_id=data.get("quiz_id", str(uuid.uuid4())[:8]),
            title=data["title"],
            questions=[Question.from_dict(q) for q in data["questions"]]
        )
    
    def save_to_file(self, directory: str = "quizzes"):
        """Save quiz to JSON file."""
        Path(directory).mkdir(exist_ok=True)
        filepath = Path(directory) / f"{self.quiz_id}.json"
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        return str(filepath)
    
    @classmethod
    def load_from_file(cls, filepath: str) -> "Quiz":
        """Load quiz from JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    @classmethod
    def list_saved_quizzes(cls, directory: str = "quizzes") -> List[dict]:
        """List all saved quizzes."""
        quiz_dir = Path(directory)
        if not quiz_dir.exists():
            return []
        
        quizzes = []
        for filepath in quiz_dir.glob("*.json"):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                quizzes.append({
                    "quiz_id": data["quiz_id"],
                    "title": data["title"],
                    "num_questions": len(data["questions"])
                })
            except Exception:
                continue
        return quizzes

@dataclass
class QuizSession:
    id: str
    host_id: str
    state: QuizState = QuizState.LOBBY
    players: Dict[str, Player] = field(default_factory=dict)  # player_id -> Player
    quiz: Optional[Quiz] = None
    current_question_idx: int = -1
    answer_counts: Dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0, 2: 0, 3: 0})
    connections: Dict[str, WebSocket] = field(default_factory=dict)  # player_id -> ws
    
    def add_player(self, player_id: str, name: str) -> Optional[Player]:
        """Add player to lobby. Returns None if name is taken."""
        # Check if name is already taken
        for player in self.players.values():
            if player.name == name:
                return None
        
        player = Player(player_id=player_id, name=name)
        self.players[player_id] = player
        return player
    
    def remove_player(self, player_id: str):
        """Remove a player from the session."""
        self.players.pop(player_id, None)
        self.connections.pop(player_id, None)
    
    def load_quiz(self, quiz: Quiz):
        """Load a quiz into the session."""
        self.quiz = quiz
        self.current_question_idx = -1
    
    def start_quiz(self) -> bool:
        """Start the quiz. Returns False if no quiz loaded."""
        if not self.quiz or len(self.quiz.questions) == 0:
            return False
        self.state = QuizState.ACTIVE
        return True
    
    def next_question(self) -> Optional[Question]:
        """Move to next question. Returns None if quiz is over."""
        if not self.quiz:
            return None
        
        self.current_question_idx += 1
        if self.current_question_idx >= len(self.quiz.questions):
            self.state = QuizState.FINISHED
            return None
        
        # Reset answer tracking
        self.answer_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        for player in self.players.values():
            player.answered_current = False
        
        return self.quiz.questions[self.current_question_idx]
    
    def get_current_question(self) -> Optional[Question]:
        """Get current question."""
        if not self.quiz or self.current_question_idx < 0:
            return None
        if self.current_question_idx >= len(self.quiz.questions):
            return None
        return self.quiz.questions[self.current_question_idx]
    
    def record_answer(self, player_id: str, answer_idx: int) -> bool:
        """Record answer. Returns True if correct."""
        player = self.players.get(player_id)
        if not player or player.answered_current:
            return False
        
        player.answered_current = True
        
        if answer_idx in self.answer_counts:
            self.answer_counts[answer_idx] += 1
        
        question = self.get_current_question()
        if question and answer_idx == question.correct_idx:
            player.score += 1
            return True
        
        return False
    
    def to_dict(self) -> dict:
        """Convert to dict for JSON."""
        return {
            "session_id": self.id,
            "host_id": self.host_id,
            "state": self.state.value,
            "players": [p.to_dict() for p in self.players.values()],
            "quiz_title": self.quiz.title if self.quiz else None,
            "current_question": self.current_question_idx + 1,
            "total_questions": len(self.quiz.questions) if self.quiz else 0
        }

# Global state (in real app, use Redis)
quiz_sessions: Dict[str, QuizSession] = {}

def create_session(host_id: str) -> QuizSession:
    """Create a new quiz session with a unique ID."""
    session_id = secrets.token_urlsafe(6)  # Shorter, easier to share
    session = QuizSession(
        id=session_id,
        host_id=host_id
    )
    quiz_sessions[session_id] = session
    return session

def get_session(session_id: str) -> Optional[QuizSession]:
    """Get a quiz session by ID."""
    return quiz_sessions.get(session_id)

def delete_session(session_id: str):
    """Delete a session."""
    quiz_sessions.pop(session_id, None)