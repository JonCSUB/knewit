Knewit is a real-time multiplayer quiz platform built with FastAPI (WebSocket server) and Textual (terminal UI clients). Teachers create sessions with unique codes, students join via their terminals, and everyone participates in live quizzes with instant answer tracking and leaderboards. The system features a lobby system, quiz creation tools, answer histograms, and score tracking—all running entirely in the terminal.

Start Uvicorn server:
source ../.venv/bin/activate
uvicorn server.app:app --host 0.0.0.0 --port 8000 --log-level debug

In another terminal start teacher/host:
source ../.venv/bin/activate
python client/host_tui.py

Start student client(s):
source ../.venv/bin/activate
python client/student_tui.py

Create a quiz:
source ../.venv/bin/activate
python client/quiz_creator.py

## Future Enhancement Ideas

1. **Quiz Editor**: Edit existing quizzes
2. **Question Timer**: Countdown for each question
3. **Partial Saves**: Auto-save quiz progress
4. **Quiz Categories**: Organize by topic
5. **Leaderboard**: Track quiz performance over time
6. **Images**: Support images in questions
7. **Export**: Export results to CSV/PDF
8. **Persistence**: Database backend for sessions
9. **Replay**: Review quiz after completion
