# client/main_tui.py
# Entry point for the Textual TUI client.
# Allows overriding server/session/player via env vars or CLI args.

import os
import sys
from .textual_ui import QuizTUI

def main():
    server = os.environ.get("QUIZ_SERVER", "ws://127.0.0.1:8000")
    session = os.environ.get("QUIZ_SESSION", "demo")
    player  = os.environ.get("QUIZ_PLAYER", "player1")

    # Optional CLI overrides: main_tui.py ws://ip:8000 demo alice
    if len(sys.argv) >= 2: server = sys.argv[1]
    if len(sys.argv) >= 3: session = sys.argv[2]
    if len(sys.argv) >= 4: player  = sys.argv[3]

    app = QuizTUI(server_url=server, session_id=session, player_id=player)
    app.run()

if __name__ == "__main__":
    main()
