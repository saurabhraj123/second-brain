"""Tests for the Gradio app's message handling.

Gradio 6's Chatbot returns message content as a list of typed parts
(e.g. [{"type": "text", "text": "..."}]) rather than a plain string. The agent
input needs plain text, so `_message_text` flattens it — this is the bug that
made every web message 400 against the OpenAI Responses API.
"""

from app import _message_text


def test_message_text_passes_through_plain_string():
    assert _message_text("hello") == "hello"


def test_message_text_extracts_from_gradio_content_parts():
    content = [{"type": "text", "text": "hi there"}]
    assert _message_text(content) == "hi there"


def test_message_text_joins_multiple_parts():
    content = [{"type": "text", "text": "foo"}, {"type": "text", "text": "bar"}]
    assert _message_text(content) == "foobar"
