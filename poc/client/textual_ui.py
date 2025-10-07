# client/textual_ui.py
# =====================================================================================
# PURPOSE
#   Textual TUI that:
#     - Shows status, current question, options
#     - Renders a 4-bar chart (A..D) using textual-plotext
#     - Sends answers on keys 1..4
#     - Updates live in response to server events (via WSClient)
#
# KEY TECHNOLOGIES
#   - Textual: terminal UI framework (asyncio-based)
#   - textual-plotext: a Textual widget that embeds Plotext charts
# =====================================================================================

from typing import Any

from textual.app import App, ComposeResult               # App = main Textual application
from textual.widgets import Header, Footer, Static       # Common Textual widgets
from textual.reactive import reactive                    # For reactive state

from textual_plotext import PlotextPlot                  # pip install textual-plotext
from .ws_client import WSClient                          # our UI-agnostic WebSocket client

LABELS = ["A", "B", "C", "D"]


class QuizTUI(App):
    """Main Textual Application for the quiz client.

    Textual concepts you'll see:
      - subclass App: where we define the UI's behavior and lifecycle
      - compose(): declare the widget tree (static layout)
      - on_mount(): called once the UI is ready (start background tasks)
      - on_unmount(): called when UI is closing (cleanup)
      - on_key(): handle keyboard input
      - reactive(...): state variables that can trigger redraws
    """

    # Textual CSS (yes, Textual has a CSS-like syntax for layout/styling).
    # Height rules ensure the plot has enough vertical space to display bars.
    CSS = """
    Screen  { layout: vertical; padding: 1; }
    #title  { content-align: center middle; height: 3; }
    #status { height: 1; color: green; }
    #prompt { height: 3; }
    #options{ height: 3; }
    #plot   { height: 12; }   /* space for 4 bars and axes */
    """

    # Reactive state (not strictly required for the plot, but handy if you
    # later want to reflect data in text widgets too).
    bins = reactive([0, 0, 0, 0])

    # Runtime configuration passed at construction (server URL, session, player).
    server_url: str
    session_id: str
    player_id: str

    # Handle to our WebSocket transport layer (starts in on_mount()).
    ws_client: WSClient | None = None

    # We'll keep explicit references to widgets we need to update later.
    title_w: Static
    status_w: Static
    prompt_w: Static
    options_w: Static
    plot: PlotextPlot

    def __init__(self, server_url: str, session_id: str, player_id: str) -> None:
        """Constructor (plain Python).
        Assign fields so we can build the WS URL on mount.
        """
        super().__init__()
        self.server_url = server_url
        self.session_id = session_id
        self.player_id = player_id

    # -----------------------------------------------------------------------------
    # Compose the widget tree (Textual calls this to build the UI)
    # -----------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        """Create and yield widgets in the order they should appear."""
        yield Header()

        self.title_w = Static(
            f"Quiz TUI (press 1–4 to answer) — Player: {self.player_id}",
            id="title",
        )
        yield self.title_w

        self.status_w = Static("status: connecting...", id="status")
        yield self.status_w

        self.prompt_w = Static("Waiting for next question...", id="prompt")
        yield self.prompt_w

        self.options_w = Static("", id="options")
        yield self.options_w

        # PlotextPlot is a Textual widget that wraps the "plotext" library.
        # We don't call plt.show(); Textual does the rendering inside the widget.
        self.plot = PlotextPlot(id="plot")
        yield self.plot

        yield Footer()

    # -----------------------------------------------------------------------------
    # Lifecycle hooks (Textual)
    # -----------------------------------------------------------------------------
    async def on_mount(self) -> None:
        """Called once the UI is ready.
        We:
          - draw an empty chart
          - build the WebSocket URL and start WSClient as a background worker
        """
        # Draw an initial empty chart so there's something visible.
        self._draw_bars([0, 0, 0, 0])

        # Build ws:// URL with session & player identity (query params).
        url = f"{self.server_url}/ws?session_id={self.session_id}&player_id={self.player_id}"

        # Start the persistent WS connection.
        # Textual's run_worker(...) ties the asyncio task to the App's lifecycle.
        self.ws_client = WSClient(url, on_event=self.handle_event)
        self.run_worker(self.ws_client.start(), exclusive=True)

        self.status_w.update("status: connecting...")

    async def on_unmount(self) -> None:
        """Called when the UI is closing. Stop WS reconnect loop."""
        if self.ws_client:
            self.ws_client.stop()

    # -----------------------------------------------------------------------------
    # Network -> UI (called by WSClient)
    # -----------------------------------------------------------------------------
    async def handle_event(self, msg: dict[str, Any]) -> None:
        """Handle non-heartbeat messages from the server."""
        t = msg.get("type")

        if t == "welcome":
            # Acknowledge we are connected + know our session_id
            self.status_w.update(f"status: connected (session={self.session_id})")
            return

        if t == "question.next":
            # Update the prompt and options (list of labels)
            self.prompt_w.update(f"Q: {msg['prompt']}")
            self.options_w.update(
                "  ".join(f"{i+1}) {opt}" for i, opt in enumerate(msg["options"]))
            )
            # Visual reset of bars for the new question
            self._draw_bars([0, 0, 0, 0])
            return

        if t == "histogram":
            # Server sends a *list* [A,B,C,D]. Cast to list for safety.
            self._draw_bars(list(msg["bins"]))
            return

    # -----------------------------------------------------------------------------
    # UI -> Network (keyboard)
    # -----------------------------------------------------------------------------
    async def on_key(self, event) -> None:
        """Handle keystrokes. Send answer on keys 1..4."""
        if event.key in ("1", "2", "3", "4") and self.ws_client:
            await self.ws_client.send(
                {"type": "answer.submit", "answer_idx": int(event.key) - 1}
            )

    # -----------------------------------------------------------------------------
    # Chart rendering (Plotext inside PlotextPlot)
    # -----------------------------------------------------------------------------
    def _draw_bars(self, values: list[int]) -> None:
        """Draw/update the 4 bars in the PlotextPlot widget.

        Plotext usage notes:
          - self.plot.plt exposes a plotext-like API
          - we clear the plot before each redraw
          - we enforce a non-zero y-range so equal bars aren't "invisible"
        """
        # Keep local reactive state in sync (handy if you later bind to other widgets).
        self.bins = values[:]

        plt = self.plot.plt

        # Clear the previous plot (Plotext versions differ; try common names).
        for fn in ("clp", "clear_plot", "clear_figure"):
            if hasattr(plt, fn):
                getattr(plt, fn)()
                break

        # Choose plot size based on widget size (Textual provides self.size).
        # plotsize(width, height)
        width = max(20, self.size.width - 6)
        height = 8
        if hasattr(plt, "plotsize"):
            plt.plotsize(width, height)

        # Ensure there is visible vertical range (even when all bars equal / zero).
        ymax = max(1, max(values) if values else 1)
        if hasattr(plt, "ylim"):
            plt.ylim(0, ymax)

        # Draw the bars. LABELS maps choice indices to labels A..D.
        plt.bar(LABELS, values)

        # Hide titles/labels for a clean look (optional).
        for name in ("title", "xlabel", "ylabel"):
            if hasattr(plt, name):
                getattr(plt, name)("")

        # Ask the widget to repaint with current plot data.
        self.plot.refresh()
