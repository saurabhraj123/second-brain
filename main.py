"""Second Brain — command-line chat.

The agents live in assistant.py (shared with the Gradio web UI in app.py); this
is just the terminal front-end.

Run:
    uv run main.py                                 # interactive chat loop
    uv run main.py "paid 649 for netflix today"    # one-shot

Requires OPENAI_API_KEY (put it in .env — see .env.example).
"""

import sys

from agents import Runner

from assistant import make_run_config, new_session_id, router_agent


def ask(prompt, history=None, group_id=None):
    """Run one turn through the router agent. Returns the SDK result object."""
    user_turn = [{"role": "user", "content": prompt}]
    return Runner.run_sync(
        router_agent,
        (history or []) + user_turn,
        max_turns=12,
        run_config=make_run_config(group_id),
    )


def main():
    group_id = new_session_id()  # group this session's turns in one trace

    # One-shot mode: prompt passed on the command line.
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:]), group_id=group_id).final_output)
        return

    # Interactive mode: a chat loop that remembers the session.
    print("Second Brain — chat (type 'exit' or Ctrl-C to quit)")
    history = []
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
        result = ask(user_input, history, group_id=group_id)
        print(f"brain> {result.final_output}")
        history = result.to_input_list()


if __name__ == "__main__":
    main()
