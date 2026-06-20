"""Second Brain — a personal AI memory.

v1 / step 1: a bare OpenAI Agents SDK agent. No tools, no database yet —
this just proves the agent loop works end to end. Storage (the `entries`
table) and tools (`remember`, `log_expense`, `run_query`, `make_chart`)
come in the next steps.

Run:
    uv run main.py                       # interactive chat loop
    uv run main.py "ate a burger today"  # one-shot

Requires OPENAI_API_KEY (put it in a .env file — see .env.example).
"""

import sys

from agents import Agent, Runner
from dotenv import load_dotenv

load_dotenv()  # load OPENAI_API_KEY from .env if present

agent = Agent(
    name="Second Brain",
    model="gpt-5.4-mini",
    instructions=(
        "You are Second Brain, the user's personal memory assistant. "
        "Today you can only chat — soon you'll be able to store the user's "
        "memories and expenses and answer questions about them. "
        "Be concise and warm."
    ),
)


def ask(prompt: str, history: list | None = None):
    """Run one turn through the agent. Returns the SDK result object."""
    user_turn = [{"role": "user", "content": prompt}]
    return Runner.run_sync(agent, (history or []) + user_turn)


def main() -> None:
    # One-shot mode: prompt passed on the command line.
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:])).final_output)
        return

    # Interactive mode: a simple chat loop that remembers the session.
    print("Second Brain — chat (type 'exit' or Ctrl-C to quit)")
    history: list = []
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue
        result = ask(user_input, history)
        print(f"brain> {result.final_output}")
        history = result.to_input_list()


if __name__ == "__main__":
    main()
