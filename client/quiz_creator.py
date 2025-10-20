"""Quiz creation interface for hosts."""
import asyncio
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Button, Input, Label
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.validation import Length

class QuizCreator(App):
    """Interface for creating quizzes."""
    
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
    
    #quiz-title-section {
        height: 4;
        margin-bottom: 1;
    }
    
    #quiz_title {
        width: 100%;
    }
    
    #questions-container {
        height: 1fr;
        border: solid cyan;
        margin-bottom: 1;
    }
    
    .question-block {
        border: solid green;
        padding: 1;
        margin-bottom: 1;
        height: auto;
    }
    
    .question-num {
        color: cyan;
        text-style: bold;
        margin-bottom: 1;
    }
    
    .answer-row {
        height: auto;
        margin-bottom: 1;
    }
    
    .answer-label {
        width: 3;
        content-align: right middle;
    }
    
    .answer-input {
        width: 1fr;
    }
    
    .correct-btn {
        width: 5;
        min-width: 5;
    }
    
    #button-container {
        height: 3;
        dock: bottom;
    }
    
    Button {
        margin: 0 1;
    }
    
    #save-btn {
        background: green;
    }
    
    #add-question-btn {
        background: blue;
    }
    
    #cancel-btn {
        background: red;
    }
    """
    
    # State
    quiz_title = reactive("")
    questions = reactive([])
    
    def __init__(self):
        super().__init__()
        self.questions_data = []  # List of {prompt, options: [4], correct_idx}
    
    def compose(self) -> ComposeResult:
        """Create widgets."""
        yield Header()
        
        yield Static("Quiz Creator", id="header")
        yield Static("", id="status")
        
        with Vertical(id="quiz-title-section"):
            yield Static("Quiz Title:")
            yield Input(id="quiz_title", placeholder="Enter quiz title")
        
        with ScrollableContainer(id="questions-container"):
            # Questions will be added dynamically
            pass
        
        with Horizontal(id="button-container"):
            yield Button("Add Question", id="add-question-btn")
            yield Button("Save Quiz", id="save-btn")
            yield Button("Cancel", id="cancel-btn")
        
        yield Footer()
    
    async def on_mount(self) -> None:
        """Initialize with one question."""
        self.add_question_block()
    
    def add_question_block(self):
        """Add a new question input block."""
        if len(self.questions_data) >= 20:
            self.query_one("#status").update("[red]Maximum 20 questions reached")
            return
        
        question_num = len(self.questions_data) + 1
        self.questions_data.append({
            "prompt": "",
            "options": ["", "", "", ""],
            "correct_idx": 0
        })
        
        container = self.query_one("#questions-container")
        
        # Create question block with all its children
        block = Vertical(classes="question-block", id=f"q-block-{question_num}")
        block.compose_add_child(Static(f"Question {question_num}", classes="question-num"))
        block.compose_add_child(Input(
            placeholder="Enter question prompt",
            id=f"q-prompt-{question_num}"
        ))
        
        for i, label in enumerate(["A", "B", "C", "D"]):
            row = Horizontal(classes="answer-row")
            row.compose_add_child(Static(f"{label}:", classes="answer-label"))
            row.compose_add_child(Input(
                placeholder=f"Answer option {label}",
                id=f"q-{question_num}-opt-{i}",
                classes="answer-input"
            ))
            row.compose_add_child(Button(
                "✓" if i == 0 else " ",
                id=f"q-{question_num}-correct-{i}",
                classes="correct-btn",
                variant="success" if i == 0 else "default"
            ))
            block.compose_add_child(row)
        
        # Now mount the complete block to the container
        container.mount(block)
        
        # Update status
        self.query_one("#status").update(f"[green]Question {question_num} added. Total: {len(self.questions_data)}")
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        button_id = event.button.id
        
        if button_id == "add-question-btn":
            self.add_question_block()
        
        elif button_id == "save-btn":
            await self.save_quiz()
        
        elif button_id == "cancel-btn":
            self.exit()
        
        elif button_id and "correct" in button_id:
            # Handle correct answer selection
            # Format: q-{num}-correct-{idx}
            parts = button_id.split("-")
            q_num = int(parts[1])
            opt_idx = int(parts[3])
            
            # Update the stored correct index
            self.questions_data[q_num - 1]["correct_idx"] = opt_idx
            
            # Update button labels
            for i in range(4):
                btn = self.query_one(f"#q-{q_num}-correct-{i}", Button)
                btn.label = "✓" if i == opt_idx else " "
    
    async def save_quiz(self):
        """Collect and save the quiz."""
        try:
            title = self.query_one("#quiz_title", Input).value
            if not title:
                self.query_one("#status").update("[red]Please enter a quiz title")
                return
            
            # Collect all questions
            quiz_data = {
                "title": title,
                "questions": []
            }
            
            for i, q_data in enumerate(self.questions_data):
                q_num = i + 1
                
                # Get prompt
                prompt = self.query_one(f"#q-prompt-{q_num}", Input).value
                if not prompt:
                    self.query_one("#status").update(f"[red]Question {q_num} is missing a prompt")
                    return
                
                # Get options
                options = []
                for opt_idx in range(4):
                    opt_val = self.query_one(f"#q-{q_num}-opt-{opt_idx}", Input).value
                    if not opt_val:
                        self.query_one("#status").update(
                            f"[red]Question {q_num} option {chr(65 + opt_idx)} is empty"
                        )
                        return
                    options.append(opt_val)
                
                quiz_data["questions"].append({
                    "prompt": prompt,
                    "options": options,
                    "correct_idx": q_data["correct_idx"]
                })
            
            if len(quiz_data["questions"]) == 0:
                self.query_one("#status").update("[red]Please add at least one question")
                return
            
            # Save to file (return quiz data to caller)
            self.exit(quiz_data)
            
        except Exception as e:
            self.query_one("#status").update(f"[red]Error: {e}")
            import traceback
            traceback.print_exc()

def main():
    """Run the quiz creator."""
    import secrets
    from pathlib import Path
    
    app = QuizCreator()
    result = app.run()
    
    if result:
        # Save to quizzes directory
        quiz_id = secrets.token_urlsafe(6)
        quizzes_dir = Path(__file__).parent.parent / "quizzes"
        quizzes_dir.mkdir(exist_ok=True)
        
        quiz_file = quizzes_dir / f"{quiz_id}.json"
        with open(quiz_file, 'w') as f:
            import json
            json.dump(result, f, indent=2)
        
        print(f"\n✓ Quiz '{result['title']}' saved as {quiz_id}.json")
        print(f"   Location: {quiz_file}")
        print(f"   Questions: {len(result['questions'])}")
        print("\nYou can now load this quiz in the host interface!\n")
    
    return result

async def run_async():
    """Run quiz creator asynchronously."""
    app = QuizCreator()
    return await app.run_async()

if __name__ == "__main__":
    result = main()
