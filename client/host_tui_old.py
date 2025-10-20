"""Textual TUI for quiz host/teacher."""
import os
import sys
import asyncio
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Button, Label
from textual.containers import Horizontal, Vertical
from textual_plotext import PlotextPlot
from textual.reactive import reactive

from ws_client import WSClient

LABELS = ["A", "B", "C", "D"]

class HostTUI(App):
    """Quiz host interface for creating and managing quiz sessions."""
    
    CSS = """
    Screen {
        layout: vertical;
        padding: 1;
    }
    
    #header {
        height: 3;
        content-align: center middle;
    }
    
    #status {
        height: 1;
        color: blue;
    }
    
    #info {
        height: 3;
    }
    
    #current {
        height: 3;
    }
    
    #plot {
        height: 12;
    }
    
    Button {
        width: 20;
    }
    """
    
    # State
    session_id = reactive("")
    password = reactive("")
    state = reactive("disconnected")
    current_question = reactive("")
    answer_counts = reactive([0, 0, 0, 0])
    
    def __init__(self, server_url: str):
        super().__init__()
        self.server_url = server_url
        self.ws_client = None
        self.ws_worker = None
        self.plot = None
        self._exiting = False  # Flag to track app exit state
    
    def compose(self) -> ComposeResult:
        """Create and yield widgets."""
        yield Header()
        
        yield Static("Quiz Host Interface", id="header")
        yield Static("", id="status")
        yield Static("", id="info")
        yield Static("", id="current")
        
        with Horizontal():
            yield Button("Start Quiz", id="start")
            yield Button("Next Question", id="next", disabled=True)
        
        self.plot = PlotextPlot(id="plot")
        yield self.plot
        
        yield Footer()
    
    async def on_mount(self) -> None:
        """Start WebSocket connection when UI is ready."""
        # Build WebSocket URL with host flag
        url = f"{self.server_url}/ws?player_id=teacher&is_host=true"
        
        # Create new client if needed
        if not self.ws_client:
            self.ws_client = WSClient(url, self.handle_event)
            
        # Start WebSocket in background but store worker reference
        if not self.ws_worker or self.ws_worker.is_finished:
            self.ws_worker = self.run_worker(
                self.ws_client.start(),
                name="websocket",
                group="system",  # Mark as system worker
                description="WebSocket connection manager"
            )
        
        self.query_one("#status").update("Connecting...")
    
    async def handle_event(self, msg: dict) -> None:
        """Handle WebSocket messages from server."""
        try:
            msg_type = msg.get("type")
            
            if msg_type == "welcome":
                self.session_id = msg.get("session_id", "demo")
                self.state = msg.get("state", "connected")
                self.query_one("#status").update("[green]Connected")
                self.query_one("#info").update(
                    f"Session ID: {self.session_id}\n"
                    f"Share this with students!"
                )
            
            elif msg_type == "question.next":
                self.current_question = msg.get("prompt", "")
                self.query_one("#current").update(f"Q: {msg.get('prompt', 'No question')}")
            
            elif msg_type == "answer.counts":
                self.answer_counts = msg.get("counts", [0, 0, 0, 0])
                self._draw_bars(msg.get("counts", [0, 0, 0, 0]))
            
            elif msg_type == "histogram":
                # Server sends histogram with "bins" field
                bins = msg.get("bins", [0, 0, 0, 0])
                self.answer_counts = bins
                self._draw_bars(bins)
                
        except Exception as e:
            print(f"Error handling event: {e}")
            import traceback
            traceback.print_exc()
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "start" and self.ws_client:
            await self.ws_client.send({"type": "quiz.start"})
    
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
    """Entry point for the host TUI."""
    server = os.environ.get("QUIZ_SERVER", "ws://127.0.0.1:8000")
    if len(sys.argv) >= 2:
        server = sys.argv[1]
    
    app = HostTUI(server)
    app.run()

if __name__ == "__main__":
    main()