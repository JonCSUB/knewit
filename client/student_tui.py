"""Textual TUI for quiz participants/students."""
import os
import sys
import asyncio
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Button, Input, Label
from textual.containers import Horizontal, Vertical
from textual_plotext import PlotextPlot
from textual.reactive import reactive

from ws_client import WSClient

LABELS = ["A", "B", "C", "D"]

class StudentTUI(App):
    """Quiz participant interface for joining and answering questions."""
    
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
        margin-bottom: 1;
    }
    
    Input {
        width: 100%;
        margin-bottom: 1;
        border: solid green;
    }
    
    Input:focus {
        border: solid yellow;
    }
    
    #prompt {
        height: 3;
        margin-top: 1;
        color: cyan;
    }
    
    #options {
        height: auto;
        min-height: 6;
    }
    
    #plot {
        height: 12;
    }
    
    Button {
        min-width: 20;
        margin: 1;
    }
    
    #join {
        width: 100%;
        margin-top: 1;
    }
    """
    
    # State
    session_id = reactive("")
    player_id = reactive("")
    state = reactive("disconnected")
    current_question = reactive("")
    options = reactive([])
    answer_counts = reactive([0, 0, 0, 0])
    can_answer = reactive(False)
    
    def __init__(self):
        super().__init__()
        self.server_url = os.environ.get("QUIZ_SERVER", "ws://127.0.0.1:8000")
        self.ws_client = None
        self.ws_worker = None
        self.plot = None
        self._exiting = False
        # Incremented on each new question to generate unique widget IDs
        self._question_seq = 0
    
    def compose(self) -> ComposeResult:
        """Create and yield widgets."""
        yield Header()
        
        yield Static("Quiz Student Interface", id="header")
        yield Static("", id="status")
        
        # Join form
        yield Static("Session ID (leave blank for 'demo'):")
        yield Input(id="session_id", placeholder="demo", value="")
        yield Static("Your Name:")
        yield Input(id="player_id", placeholder="Enter your name", value="")
        yield Button("Join Quiz", id="join")
        
        # Quiz interface (hidden until joined)
        yield Static("", id="prompt")
        
        # Use a container for answer buttons
        with Vertical(id="options"):
            pass
        
        self.plot = PlotextPlot(id="plot")
        yield self.plot
        
        yield Footer()
    
    async def on_mount(self) -> None:
        """Set focus to the player name input when app starts."""
        try:
            # Set focus to the player_id input field
            player_input = self.query_one("#player_id", Input)
            player_input.focus()
        except Exception as e:
            print(f"Error setting focus: {e}")
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "join":
            # Get form values
            try:
                session_id = self.query_one("#session_id", Input).value or "demo"
                player_id = self.query_one("#player_id", Input).value
                
                if not player_id:
                    self.query_one("#status").update("[red]Please enter your name")
                    return
            except Exception as e:
                self.query_one("#status").update(f"[red]Error: {e}")
                print(f"Error getting input values: {e}")
                import traceback
                traceback.print_exc()
                return
            
            # Build WebSocket URL
            url = f"{self.server_url}/ws?player_id={player_id}&is_host=false"
            
            # Connect to server
            self.ws_client = WSClient(url, self.handle_event)
            self.ws_worker = self.run_worker(
                self.ws_client.start(),
                name="websocket",
                group="system",
                description="WebSocket connection manager"
            )
            self.query_one("#status").update("Connecting...")
            
            # Send join request
            await asyncio.sleep(0.5)  # Wait for connection
            if self.ws_client:
                await self.ws_client.send({
                    "type": "session.join",
                    "session_id": session_id,
                    "name": player_id
                })
        
        elif event.button.id and event.button.id.startswith("answer_"):
            if not self.can_answer or not self.ws_client:
                return
            
            # Extract answer index from button ID
            try:
                parts = event.button.id.split("_")
                # expected: ["answer", index, seq]
                idx = int(parts[1])
            except Exception:
                return
            await self.ws_client.send({
                "type": "answer.submit",
                "answer_idx": idx
            })
            self.can_answer = False  # Prevent multiple answers
    
    async def handle_event(self, msg: dict) -> None:
        """Handle WebSocket messages from server."""
        try:
            msg_type = msg.get("type")
            
            if msg_type == "welcome":
                self.query_one("#status").update("[yellow]Connected to server...")
            
            elif msg_type == "session.joined":
                self.session_id = msg.get("session_id", "")
                self.query_one("#status").update(f"[green]Joined session: {self.session_id}")
            
            elif msg_type == "lobby.update":
                # Show lobby state
                players = msg.get("players", [])
                player_names = ", ".join(p['name'] for p in players)
                self.query_one("#prompt").update(
                    f"Lobby - Waiting for host to start...\n"
                    f"Players: {player_names or 'None'}"
                )
            
            elif msg_type == "kicked":
                self.query_one("#status").update("[red]You were kicked from the session")
                await asyncio.sleep(2)
                self.exit()
            
            elif msg_type == "question.next":
                # Reading phase - show question, enable answering
                self.current_question = msg.get("prompt", "")
                self.options = msg.get("options", ["A", "B", "C", "D"])
                self.can_answer = True  # Enable answering immediately
                self.query_one("#prompt").update(f"Q: {msg.get('prompt', 'No question')}")
                # Bump question sequence so button IDs are unique per question
                self._question_seq += 1
                self._update_options(enable_buttons=True)  # Explicitly enable buttons
            
            elif msg_type == "question.answers":
                # Answer phase - show options and enable answering
                self.options = msg.get("options", ["A", "B", "C", "D"])
                self.can_answer = True
                self._update_options(enable_buttons=True)
            
            elif msg_type == "histogram":
                # Server sends histogram with "bins" field
                bins = msg.get("bins", [0, 0, 0, 0])
                self.answer_counts = bins
                self._draw_bars(bins)
            
            elif msg_type == "answer.recorded":
                # Confirmation that answer was recorded
                correct = msg.get("correct", False)
                if correct:
                    self.query_one("#status").update("[green]Correct! ✓")
                else:
                    self.query_one("#status").update("[red]Incorrect ✗")
            
            elif msg_type == "answer.counts":
                self.answer_counts = msg.get("counts", [0, 0, 0, 0])
                self._draw_bars(msg.get("counts", [0, 0, 0, 0]))
            
            elif msg_type == "question.results":
                # Show correct answer
                self.can_answer = False
                correct_idx = msg.get("correct_idx", 0)
                options = [
                    f"{'✓' if i == correct_idx else ' '} {opt}"
                    for i, opt in enumerate(self.options)
                ]
                self.options = options
                self._update_options(enable_buttons=False)  # Disable buttons for results
                self._draw_bars(msg.get("counts", [0, 0, 0, 0]))
            
            elif msg_type == "quiz.finished":
                # Quiz is over, show results
                leaderboard = msg.get("leaderboard", [])
                self.can_answer = False
                self.query_one("#prompt").update("Quiz Finished!")
                if leaderboard:
                    top_3 = leaderboard[:3]
                    results = "\n".join(
                        f"{i+1}. {p['name']}: {p['score']} points"
                        for i, p in enumerate(top_3)
                    )
                    self.query_one("#status").update(f"[green]Top Scores:\n{results}")
                
        except Exception as e:
            print(f"Error handling event: {e}")
            import traceback
            traceback.print_exc()
    
    async def on_unmount(self) -> None:
        """Clean up WebSocket client when app exits."""
        self._exiting = True
        if self.ws_client:
            self.ws_client.stop()
            if self.ws_worker and not self.ws_worker.is_finished:
                try:
                    await asyncio.wait_for(self.ws_worker.stop(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass  # Worker didn't stop in time, but we're exiting anyway
    
    def _update_options(self, enable_buttons=True):
        """Update the options display."""
        if not self.options:
            return
        
        options_container = self.query_one("#options", Vertical)
        options_container.remove_children()
        
        for i, opt in enumerate(self.options):
            # Check if student has already answered
            is_disabled = not enable_buttons or not self.can_answer
            btn = Button(
                f"{LABELS[i]}) {opt}",
                # Include question sequence to avoid duplicate IDs across questions
                id=f"answer_{i}_{self._question_seq}",
                disabled=is_disabled
            )
            options_container.mount(btn)
    
    def _draw_bars(self, values: list[int]) -> None:
        """Update the bar chart with current answer counts."""
        plt = self.plot.plt
        
        # Clear previous plot
        for fn in ("clp", "clear_plot", "clear_figure"):
            if hasattr(plt, fn):
                getattr(plt, fn)()
                break
        
        # Set plot size
        width = max(20, self.size.width - 6)
        height = 8
        if hasattr(plt, "plotsize"):
            plt.plotsize(width, height)
        
        # Ensure visible range
        ymax = max(1, max(values) if values else 1)
        if hasattr(plt, "ylim"):
            plt.ylim(0, ymax)
        
        # Draw bars
        plt.bar(LABELS, values)
        
        # Clean look
        for name in ("title", "xlabel", "ylabel"):
            if hasattr(plt, name):
                getattr(plt, name)("")
        
        self.plot.refresh()

def main():
    """Entry point for the student TUI."""
    app = StudentTUI()
    app.run()

if __name__ == "__main__":
    main()