"""Tests for the learning commands: /explain, /eli5, /quiz, /practice, /score,
/skip, plus the shared _ai_reply helper and quiz/practice session routing."""

import json
from unittest.mock import MagicMock, patch


def make_message(text="hello", user_id=123, chat_id=456):
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    return msg


class FakeStore:
    """Minimal in-memory KV standing in for SqliteStore in tests."""

    def __init__(self, initial=None):
        self.d = dict(initial or {})

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ex=None):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


# ── _parse_quiz ───────────────────────────────────────────────────────────────

VALID_QUIZ = {
    "question": "What does len() return?",
    "options": {"A": "length", "B": "type", "C": "memory", "D": "error"},
    "correct": "A",
    "explanation": "len() returns the number of items.",
}


def test_parse_quiz_valid():
    from bot.handlers import _parse_quiz

    q = _parse_quiz(json.dumps(VALID_QUIZ))
    assert q["question"] == "What does len() return?"
    assert q["correct"] == "A"
    assert q["options"]["C"] == "memory"


def test_parse_quiz_tolerates_code_fence_and_prose():
    from bot.handlers import _parse_quiz

    raw = "Sure! Here you go:\n```json\n" + json.dumps(VALID_QUIZ) + "\n```"
    q = _parse_quiz(raw)
    assert q is not None
    assert q["correct"] == "A"


def test_parse_quiz_rejects_non_json():
    from bot.handlers import _parse_quiz

    assert _parse_quiz("not json at all") is None


def test_parse_quiz_rejects_missing_option():
    from bot.handlers import _parse_quiz

    bad = {**VALID_QUIZ, "options": {"A": "x", "B": "y", "C": "z"}}  # no D
    assert _parse_quiz(json.dumps(bad)) is None


def test_parse_quiz_rejects_bad_correct_key():
    from bot.handlers import _parse_quiz

    bad = {**VALID_QUIZ, "correct": "E"}
    assert _parse_quiz(json.dumps(bad)) is None


# ── _extract_choice ─────────────────────────────────────────────────────────

def test_extract_choice_letters_and_digits():
    from bot.handlers import _extract_choice

    assert _extract_choice("A") == "A"
    assert _extract_choice("  c ") == "C"
    assert _extract_choice("b) because") == "B"
    assert _extract_choice("3") == "C"
    assert _extract_choice("nope") is None
    assert _extract_choice("") is None


# ── /quiz ────────────────────────────────────────────────────────────────────

def test_cmd_quiz_generates_and_stores_session():
    store = FakeStore()
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.ask_fresh", return_value=json.dumps(VALID_QUIZ)),
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz python"))
        sent = mock_bot.send_message.call_args[0][1]
        assert "len()" in sent and "A)" in sent
        saved = json.loads(store.d["session:123"])
        assert saved["kind"] == "quiz"
        assert saved["data"]["correct"] == "A"


def test_cmd_quiz_handles_unparseable_response():
    store = FakeStore()
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.ask_fresh", return_value="garbage"),
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz python"))
        assert "couldn't build a quiz" in mock_bot.send_message.call_args[0][1].lower()
        assert "session:123" not in store.d


def test_quiz_correct_answer_scores_and_clears():
    session = {"kind": "quiz", "data": VALID_QUIZ}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="A"))
        mock_ask.assert_not_called()  # quiz grading is local, no AI call
        sent = mock_bot.send_message.call_args[0][1]
        assert "Correct" in sent and "1/1" in sent
        assert "session:123" not in store.d  # session consumed
        assert json.loads(store.d["score:123"]) == {"correct": 1, "total": 1}


def test_quiz_wrong_answer_reports_correct_option():
    session = {"kind": "quiz", "data": VALID_QUIZ}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_ai"),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="B"))
        sent = mock_bot.send_message.call_args[0][1]
        assert "Not quite" in sent and "A)" in sent
        assert json.loads(store.d["score:123"]) == {"correct": 0, "total": 1}


def test_quiz_non_answer_keeps_session():
    session = {"kind": "quiz", "data": VALID_QUIZ}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="what?"))
        mock_ask.assert_not_called()
        assert "session:123" in store.d  # still pending
        assert "A, B, C, or D" in mock_bot.send_message.call_args[0][1]


# ── /practice ────────────────────────────────────────────────────────────────

def test_cmd_practice_stores_problem_session():
    store = FakeStore()
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.ask_fresh", return_value="Solve: 2/3 + 1/6 = ?"),
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_practice

        cmd_practice(make_message(text="/practice fractions"))
        assert "2/3" in mock_bot.send_message.call_args[0][1]
        saved = json.loads(store.d["session:123"])
        assert saved["kind"] == "practice"
        assert "2/3" in saved["data"]["problem"]


def test_practice_answer_is_graded_by_ai():
    session = {"kind": "practice", "data": {"problem": "2/3 + 1/6 = ?"}}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_fresh", return_value="Correct! Well done.") as mock_ask,
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="5/6"))
        assert mock_ask.called
        graded_prompt = mock_ask.call_args[0][2]
        assert "5/6" in graded_prompt and "2/3 + 1/6" in graded_prompt
        mock_send.assert_called_once()
        assert "session:123" not in store.d  # consumed


# ── /score and /skip ─────────────────────────────────────────────────────────

def test_cmd_score_reports_percentage():
    store = FakeStore({"score:123": json.dumps({"correct": 3, "total": 4})})
    with patch("bot.handlers.store", store), patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import cmd_score

        cmd_score(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "3/4" in sent and "75%" in sent


def test_cmd_skip_clears_pending_session():
    store = FakeStore({"session:123": json.dumps({"kind": "quiz", "data": VALID_QUIZ})})
    with patch("bot.handlers.store", store), patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import cmd_skip

        cmd_skip(make_message())
        assert "session:123" not in store.d
        assert "cleared" in mock_bot.send_message.call_args[0][1].lower()


# ── regression: fun commands now respect the rate limit ──────────────────────

def test_joke_respects_rate_limit():
    """/joke used to call ask_ai unconditionally; it now goes through _ai_reply
    so the daily rate limit applies."""
    with (
        patch("bot.handlers.is_rate_limited", return_value=True),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_joke

        cmd_joke(make_message(text="/joke"))
        mock_ask.assert_not_called()
        assert "daily limit" in mock_bot.send_message.call_args[0][1]


def test_roast_with_trailing_space_does_not_crash():
    """'/roast ' (trailing space, no name) used to raise IndexError."""
    with (
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.ask_ai", return_value="ok") as mock_ask,
        patch("bot.handlers.send_reply"),
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_roast

        cmd_roast(make_message(text="/roast "))  # trailing space, no target
        assert "roast of you" in mock_ask.call_args[0][1]
