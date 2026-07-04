"""Second Brain — Gradio web UI.

A chat interface over the same agents (assistant.router_agent), with token
streaming and a "New chat" button that resets both the visible chat and the
agent's conversation memory.

Run:
    uv run app.py

Then open the printed local URL. Requires OPENAI_API_KEY (see .env.example).
"""

import gradio as gr
from agents import Runner
from openai.types.responses import ResponseTextDeltaEvent

from assistant import make_run_config, new_session_id, router_agent


def _message_text(content):
    """Normalize a chat message's content to plain text.

    Gradio 6's Chatbot hands message content back as a list of typed parts
    (e.g. [{"type": "text", "text": "..."}]); the OpenAI Responses API wants a
    plain string for our agent input, so flatten it here.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content)


def add_user(message, chat_history):
    """Append the user's message to the chat and clear the textbox."""
    message = (message or "").strip()
    if not message:
        return "", chat_history or []
    return "", (chat_history or []) + [{"role": "user", "content": message}]


async def stream_response(chat_history, state, group_id):
    """Stream the router agent's reply token-by-token into the last bubble.

    `state` is the SDK conversation input list (the agent's memory across turns);
    `chat_history` is what the Chatbot displays; `group_id` clusters this
    conversation's turns under one trace on the OpenAI Traces dashboard.
    """
    # Only respond when the latest turn is actually a user message.
    if not chat_history or chat_history[-1].get("role") != "user":
        yield chat_history, state, group_id
        return

    if not group_id:  # first turn of a fresh conversation
        group_id = new_session_id()

    user_message = _message_text(chat_history[-1]["content"])
    chat_history = chat_history + [{"role": "assistant", "content": ""}]
    sdk_input = (state or []) + [{"role": "user", "content": user_message}]

    result = Runner.run_streamed(
        router_agent, sdk_input, max_turns=12, run_config=make_run_config(group_id)
    )
    text = ""
    async for event in result.stream_events():
        if event.type == "raw_response_event" and isinstance(
            event.data, ResponseTextDeltaEvent
        ):
            text += event.data.delta
            chat_history[-1]["content"] = text
            yield chat_history, state, group_id

    # A turn may be only tool calls (store/recall) with no streamed text.
    if not text:
        chat_history[-1]["content"] = result.final_output or "(no response)"
    # Persist the full conversation (incl. tool calls) for the next turn.
    yield chat_history, result.to_input_list(), group_id


def new_chat():
    """Reset the visible chat, the agent's memory, and the trace group."""
    return [], [], None


with gr.Blocks(title="Second Brain") as demo:
    gr.Markdown(
        "# 🧠 Second Brain\n"
        "Tell me something to remember, or ask about your memories."
    )

    # Holds the SDK conversation input list — the agent's memory.
    state = gr.State([])
    # Holds this conversation's trace group id (for the OpenAI Traces dashboard).
    group_state = gr.State(None)
    chatbot = gr.Chatbot(height=520, label="Second Brain")

    with gr.Row():
        msg = gr.Textbox(
            placeholder="e.g.  'paid 649 for Netflix today'   or   "
            "'how much did I spend on subscriptions?'",
            show_label=False,
            autofocus=True,
            scale=8,
        )
        send = gr.Button("Send", variant="primary", scale=1)

    new_btn = gr.Button("🗑  New chat")

    # Enter and the Send button both: add the user message, then stream the reply.
    for trigger in (msg.submit, send.click):
        trigger(add_user, [msg, chatbot], [msg, chatbot], queue=False).then(
            stream_response,
            [chatbot, state, group_state],
            [chatbot, state, group_state],
        )

    new_btn.click(new_chat, outputs=[chatbot, state, group_state])


if __name__ == "__main__":
    demo.queue().launch()
