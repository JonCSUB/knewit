"""Enhanced host TUI with lobby, quiz creation, and session management."""
import os
import sys
import asyncio
import json
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Button, Label, Input, ListView, ListItem
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual_plotext import PlotextPlot

from ws_client import WSClient
from quiz_creator import QuizCreator
from quiz_selector import QuizSelector

LABELS = ["A", "B", "C", "D"]

class HostTUI(App):
    """Enhanced quiz host interface."""
    
    CSS = """
    Screen {
        layout: vertical;
        padding: 1;
    }
    
    #header {
        height: 3;
        content-align: center middle;
        background: blue;
    }
    
    #status {
        height: 1;
        color: yellow;
    }
    
    #session-info {
        height: 3;
        background: green;
        padding: 1;
    }
    
    #lobby {
        height: 15;
        border: solid cyan;
        padding: 1;
    }
    
    .player-item {
        height: 1;
        margin-bottom: 1;
    }
    
    #current {
        height: 3;
        color: cyan;
    }
    
    #plot {
        height: 12;
    }
    
    Button {
        margin: 1;
    }
    
    .action-btn {
        width: 100%;
        margin-bottom: 1;
    }
    
    /* Quiz selection overlay */
    #quiz-selection {
        height: 100%;
        background: $panel;
        border: thick cyan;
        padding: 2;
    }
    
    .selection-title {
        height: 3;
        content-align: center middle;
        text-style: bold;
        color: cyan;
        background: blue;
    }
    
    .selection-subtitle {
        height: 2;
        content-align: center middle;
        color: yellow;
    }
    
    #quiz-list-scroll {
        height: 1fr;
        border: solid green;
        padding: 1;
        margin: 1;
    }
    
    .quiz-select-btn {
        width: 100%;
        height: 5;
        margin-bottom: 1;
        background: green 30%;
        border: solid green;
    }
    
    .quiz-select-btn:hover {
        background: green 50%;
    }
    
    #cancel-selection {
        width: 100%;
        height: 3;
    }
    """
    
    # State
    session_id = reactive("")
    state = reactive("disconnected")
    players = reactive([])
    current_question = reactive("")
    quiz_loaded = reactive(False)
    quiz_title = reactive("")
    
    def __init__(self, server_url: str):
        super().__init__()
        self.server_url = server_url
        self.ws_client = None
        self.ws_worker = None
        self.plot = None
        self._exiting = False
        self.quiz_data = None
        self._quizzes_dir = None
    
    def compose(self) -> ComposeResult:
        """Create widgets."""
        yield Header()
        
        yield Static("Quiz Host Interface", id="header")
        yield Static("Not connected", id="status")
        yield Static("", id="session-info")
        
        # Initial setup buttons
        with Vertical(id="setup-controls"):
            yield Button("Create New Session", id="create-session", classes="action-btn")
        
        # Lobby (hidden initially)
        with Vertical(id="lobby"):
            yield Static("Lobby - Waiting for players...", id="lobby-title")
            yield ScrollableContainer(id="player-list")
        
        # Quiz controls (hidden initially)
        with Horizontal(id="quiz-controls"):
            yield Button("Create Quiz", id="create-quiz")
            yield Button("Load Quiz", id="load-quiz")
            yield Button("Start Quiz", id="start-quiz", disabled=True)
            yield Button("Next Question", id="next-question", disabled=True)
        
        # Current question display
        yield Static("", id="current")
        
        # Answer histogram
        self.plot = PlotextPlot(id="plot")
        yield self.plot
        
        yield Footer()
    
    async def on_mount(self) -> None:
        """Connect to server."""
        # Hide lobby and quiz controls initially
        self.query_one("#lobby").display = False
        self.query_one("#quiz-controls").display = False
        
        url = f"{self.server_url}/ws?player_id=host&is_host=true"
        self.ws_client = WSClient(url, self.handle_event)
        self.ws_worker = self.run_worker(
            self.ws_client.start(),
            name="websocket",
            group="system"
        )
        self.query_one("#status").update("Connecting...")
    
    async def handle_event(self, msg: dict) -> None:
        """Handle WebSocket messages."""
        try:
            msg_type = msg.get("type")
            
            if msg_type == "welcome":
                self.query_one("#status").update("[green]Connected - Create a session to begin")
            
            elif msg_type == "session.created":
                self.session_id = msg.get("session_id", "")
                self.query_one("#status").update(f"[green]Session created!")
                self.query_one("#session-info").update(
                    f"Session ID: {self.session_id}\n"
                    f"Share this code with students!"
                )
                self.query_one("#setup-controls").display = False
                self.query_one("#lobby").display = True
                self.query_one("#quiz-controls").display = True
            
            elif msg_type == "lobby.update":
                self.players = msg.get("players", [])
                self._update_player_list()
            
            elif msg_type == "quiz.loaded":
                self.quiz_loaded = True
                self.quiz_title = msg.get("quiz_title", "")
                self.query_one("#start-quiz", Button).disabled = False
                self.query_one("#status").update(f"[green]Quiz loaded: {self.quiz_title}")
            
            elif msg_type == "question.next":
                self.current_question = msg.get("prompt", "")
                q_num = msg.get("question_num", 1)
                total = msg.get("total_questions", 1)
                self.query_one("#current").update(
                    f"Q{q_num}/{total}: {msg.get('prompt', '')}"
                )
                self.query_one("#next-question", Button).disabled = False
                self.query_one("#start-quiz", Button).disabled = True
            
            elif msg_type == "histogram":
                bins = msg.get("bins", [0, 0, 0, 0])
                self._draw_bars(bins)
            
            elif msg_type == "quiz.finished":
                leaderboard = msg.get("leaderboard", [])
                self.query_one("#current").update("Quiz Finished!")
                self.query_one("#status").update(f"[green]Winners: " + ", ".join(
                    f"{p['name']} ({p['score']})" for p in leaderboard[:3]
                ))
                self.query_one("#next-question", Button).disabled = True
            
            elif msg_type == "error":
                self.query_one("#status").update(f"[red]{msg.get('message', 'Error')}")
                
        except Exception as e:
            print(f"Error handling event: {e}")
            import traceback
            traceback.print_exc()
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        button_id = event.button.id
        
        if button_id == "create-session" and self.ws_client:
            await self.ws_client.send({"type": "session.create"})
        
        elif button_id == "create-quiz":
            await self._create_quiz()
        
        elif button_id == "load-quiz":
            await self._load_quiz()
        
        elif button_id == "start-quiz" and self.ws_client:
            await self.ws_client.send({"type": "quiz.start"})
        
        elif button_id == "next-question" and self.ws_client:
            await self.ws_client.send({"type": "question.next"})
        
        elif button_id and button_id.startswith("kick-"):
            player_id = button_id[5:]  # Remove "kick-" prefix
            if self.ws_client:
                await self.ws_client.send({
                    "type": "player.kick",
                    "player_id": player_id
                })
        
        elif button_id and button_id.startswith("select-quiz-"):
            # Handle quiz selection
            quiz_id = button_id[12:]  # Remove "select-quiz-" prefix
            await self._handle_quiz_selected(quiz_id)
        
        elif button_id == "cancel-selection":
            # Hide selection, show main controls
            try:
                self.query_one("#quiz-selection").display = False
            except:
                pass
            self.query_one("#lobby").display = True
            self.query_one("#quiz-controls").display = True
            self.query_one("#status").update("[yellow]Quiz selection cancelled")
    
    async def _create_quiz(self):
        """Launch quiz creator."""
        self.query_one("#status").update("[yellow]To create a quiz, run: python client/quiz_creator.py")
        self.notify("Run 'python client/quiz_creator.py' in a separate terminal to create a quiz", 
                    severity="information", timeout=10)
    
    async def _show_quiz_creator(self):
        """Simple quiz creator (placeholder)."""
        # This is a simplified version - in production, this would be a full TUI
        return {
            "title": "Sample Quiz",
            "questions": [
                {
                    "prompt": "What is 2+2?",
                    "options": ["3", "4", "5", "6"],
                    "correct_idx": 1
                },
                {
                    "prompt": "What color is the sky?",
                    "options": ["Red", "Blue", "Green", "Yellow"],
                    "correct_idx": 1
                }
            ]
        }
    
    async def _load_quiz(self):
        """Load a saved quiz."""
        self.query_one("#status").update("[yellow]Loading quiz list...")
        
        try:
            # Get list of saved quizzes
            quizzes_dir = Path(__file__).parent.parent / "quizzes"
            if not quizzes_dir.exists():
                self.query_one("#status").update("[red]No quizzes directory found")
                return
            
            # Read all quiz files
            quiz_files = list(quizzes_dir.glob("*.json"))
            if not quiz_files:
                self.query_one("#status").update("[red]No saved quizzes found. Create one first!")
                return
            
            # Build quiz list
            quiz_list = []
            for quiz_file in quiz_files:
                try:
                    with open(quiz_file, 'r') as f:
                        data = json.load(f)
                        quiz_list.append({
                            'quiz_id': quiz_file.stem,
                            'title': data.get('title', 'Untitled'),
                            'num_questions': len(data.get('questions', []))
                        })
                except Exception as e:
                    print(f"Error reading quiz {quiz_file}: {e}")
            
            if not quiz_list:
                self.query_one("#status").update("[red]No valid quizzes found")
                return
            
            # Show quiz selection overlay
            await self._show_quiz_selection(quiz_list, quizzes_dir)
                
        except Exception as e:
            self.query_one("#status").update(f"[red]Error loading quiz: {e}")
            import traceback
            traceback.print_exc()
    
    async def _show_quiz_selection(self, quiz_list: list, quizzes_dir: Path):
        """Show an inline quiz selection menu."""
        # Hide main controls
        self.query_one("#lobby").display = False
        self.query_one("#quiz-controls").display = False
        
        # Get or create selection container
        try:
            selection_container = self.query_one("#quiz-selection")
            # Clear all children to start fresh
            selection_container.remove_children()
        except:
            selection_container = Vertical(id="quiz-selection")
            self.mount(selection_container)
        
        selection_container.display = True
        
        # Add title
        selection_container.mount(Static("Select a Quiz", classes="selection-title"))
        selection_container.mount(Static(f"Found {len(quiz_list)} saved quizzes", classes="selection-subtitle"))
        
        # Add quiz buttons
        scroll = ScrollableContainer(id="quiz-list-scroll")
        selection_container.mount(scroll)
        
        for quiz in quiz_list:
            btn = Button(
                f"{quiz['title']}\n({quiz['num_questions']} questions)",
                id=f"select-quiz-{quiz['quiz_id']}",
                classes="quiz-select-btn"
            )
            scroll.mount(btn)
        
        # Add cancel button
        cancel_btn = Button("Cancel", id="cancel-selection", variant="error")
        selection_container.mount(cancel_btn)
        
        # Store quizzes_dir for later use
        self._quizzes_dir = quizzes_dir
    
    async def _handle_quiz_selected(self, quiz_id: str):
        """Handle a quiz being selected."""
        try:
            # Load the quiz file
            quiz_file = self._quizzes_dir / f"{quiz_id}.json"
            with open(quiz_file, 'r') as f:
                quiz_data = json.load(f)
            
            # Hide selection
            try:
                self.query_one("#quiz-selection").display = False
            except:
                pass
            
            # Show main controls
            self.query_one("#lobby").display = True
            self.query_one("#quiz-controls").display = True
            
            # Send to server
            if self.ws_client:
                self.query_one("#status").update(f"[yellow]Loading quiz: {quiz_data.get('title', 'Untitled')}")
                await self.ws_client.send({
                    "type": "quiz.load",
                    "quiz": quiz_data
                })
        except Exception as e:
            self.query_one("#status").update(f"[red]Error loading quiz: {e}")
            import traceback
            traceback.print_exc()
    
    def _update_player_list(self):
        """Update the player list display."""
        container = self.query_one("#player-list", ScrollableContainer)
        container.remove_children()
        
        if not self.players:
            container.mount(Static("No players yet..."))
            return
        
        for player in self.players:
            # Create horizontal item for each player
            item = Horizontal(classes="player-item")
            item.mount(Static(f"{player['name']} ({player['score']} pts)"))
            item.mount(Button("Kick", id=f"kick-{player['player_id']}"))
            container.mount(item)
    
    def _draw_bars(self, values: list[int]) -> None:
        """Update answer histogram."""
        plt = self.plot.plt
        
        for fn in ("clp", "clear_plot", "clear_figure"):
            if hasattr(plt, fn):
                getattr(plt, fn)()
                break
        
        width = max(20, self.size.width - 6)
        height = 8
        if hasattr(plt, "plotsize"):
            plt.plotsize(width, height)
        
        ymax = max(1, max(values) if values else 1)
        if hasattr(plt, "ylim"):
            plt.ylim(0, ymax)
        
        plt.bar(LABELS, values)
        
        for name in ("title", "xlabel", "ylabel"):
            if hasattr(plt, name):
                getattr(plt, name)("")
        
        self.plot.refresh()
    
    async def on_unmount(self) -> None:
        """Cleanup."""
        self._exiting = True
        if self.ws_client:
            self.ws_client.stop()
            if self.ws_worker and not self.ws_worker.is_finished:
                try:
                    self.ws_worker.cancel()
                    await asyncio.sleep(0.1)  # Give it time to cancel
                except Exception:
                    pass

def main():
    """Entry point."""
    server = os.environ.get("QUIZ_SERVER", "ws://127.0.0.1:8000")
    if len(sys.argv) >= 2:
        server = sys.argv[1]
    
    app = HostTUI(server)
    app.run()

if __name__ == "__main__":
    main()
