"""Quiz selector - allows host to choose from saved quizzes."""
import asyncio
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Button, ListView, ListItem, Label
from textual.containers import Vertical, ScrollableContainer
from textual.reactive import reactive

class QuizSelector(App):
    """Select a quiz from saved quizzes."""
    
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
    
    #quiz-list {
        height: 20;
        border: solid cyan;
        padding: 1;
    }

    #cancel-btn {
        margin-top: 1;
        align-horizontal: right;
    }
    
    .quiz-item {
        height: 3;
        padding: 1;
        margin-bottom: 1;
        border: solid green;
    }
    
    .quiz-item:hover {
        background: green 20%;
    }
    
    Button {
        margin: 1;
    }
    """
    
    selected_quiz = reactive(None)
    
    def __init__(self, quiz_list: list):
        super().__init__()
        self.quiz_list = quiz_list
    
    def compose(self) -> ComposeResult:
        """Create widgets."""
        yield Header()
        
        yield Static("Select a Quiz", id="header")
        yield Static(f"Found {len(self.quiz_list)} saved quizzes", id="status")
        
        with ScrollableContainer(id="quiz-list"):
            if not self.quiz_list:
                yield Static("No saved quizzes found. Create one first!")
            else:
                for quiz in self.quiz_list:
                    yield Button(
                        f"{quiz['title']}\n{quiz['num_questions']} questions",
                        id=f"quiz-{quiz['quiz_id']}",
                        classes="quiz-item"
                    )
        
        with Vertical():
            yield Button("Cancel", id="cancel-btn")
        
        yield Footer()
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        button_id = event.button.id
        
        if button_id == "cancel-btn":
            self.exit(None)
        elif button_id and button_id.startswith("quiz-"):
            # Extract quiz_id
            quiz_id = button_id[5:]  # Remove "quiz-" prefix
            
            # Find the quiz data
            for quiz in self.quiz_list:
                if quiz['quiz_id'] == quiz_id:
                    self.exit(quiz)
                    return

def main(quiz_list):
    """Run the quiz selector."""
    app = QuizSelector(quiz_list)
    return app.run()

async def run_async(quiz_list):
    """Run quiz selector asynchronously."""
    app = QuizSelector(quiz_list)
    return await app.run_async()

if __name__ == "__main__":
    # Test with sample data
    sample_quizzes = [
        {"quiz_id": "abc123", "title": "Math Quiz", "num_questions": 10},
        {"quiz_id": "def456", "title": "Science Quiz", "num_questions": 15},
    ]
    result = main(sample_quizzes)
    print(f"Selected: {result}")
