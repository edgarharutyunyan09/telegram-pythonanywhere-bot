"""Tests for the learning commands: /explain, /quiz, /practice, /hint, /feynman,
/review, /score, /skip, plus the shared _ai_reply helper and session routing."""

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


def test_cmd_quiz_without_topic_uses_recent_conversation():
    """A bare /quiz should quiz on what the student was just studying, drawing
    the topic from recent chat history instead of firing random trivia."""
    store = FakeStore()
    history = [
        {"role": "user", "content": "Explain photosynthesis"},
        {"role": "assistant", "content": "Photosynthesis is how plants make food..."},
    ]
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.get_history", return_value=history),
        patch("bot.handlers.ask_fresh", return_value=json.dumps(VALID_QUIZ)) as mock_ask,
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz"))  # no topic
        quiz_input = mock_ask.call_args[0][2]
        assert "photosynthesis" in quiz_input.lower()


def test_cmd_quiz_without_topic_or_history_falls_back_to_general():
    """With no topic and no history, /quiz still works via a generic topic."""
    store = FakeStore()
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.get_history", return_value=[]),
        patch("bot.handlers.ask_fresh", return_value=json.dumps(VALID_QUIZ)) as mock_ask,
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz"))
        assert "general knowledge" in mock_ask.call_args[0][2].lower()


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


# ── _arg guard ───────────────────────────────────────────────────────────────

def test_command_with_trailing_space_does_not_crash():
    """'/explain ' (trailing space, no topic) must not raise IndexError; it
    should fall through to the usage message via the _arg length guard."""
    with (
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_explain

        cmd_explain(make_message(text="/explain "))  # trailing space, no topic
        mock_ask.assert_not_called()
        assert "Usage" in mock_bot.send_message.call_args[0][1]


# ── /hint ──────────────────────────────────────────────────────────────────

def test_hint_without_session_prompts_to_start():
    store = FakeStore()
    with patch("bot.handlers.store", store), patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import cmd_hint

        cmd_hint(make_message(text="/hint"))
        assert "No problem in progress" in mock_bot.send_message.call_args[0][1]


def test_hint_gives_hint_and_increments_counter():
    session = {"kind": "practice", "data": {"problem": "2/3 + 1/6 = ?"}}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.ask_fresh", return_value="Find a common denominator.") as mock_ask,
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_hint

        cmd_hint(make_message(text="/hint"))
        assert "common denominator" in mock_bot.send_message.call_args[0][1]
        # The problem and hint count reach the model, and the counter is bumped.
        assert "2/3 + 1/6" in mock_ask.call_args[0][2]
        saved = json.loads(store.d["session:123"])
        assert saved["data"]["hints_given"] == 1


# ── /feynman ─────────────────────────────────────────────────────────────────

def test_cmd_feynman_opens_session():
    store = FakeStore()
    with patch("bot.handlers.store", store), patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import cmd_feynman

        cmd_feynman(make_message(text="/feynman recursion"))
        saved = json.loads(store.d["session:123"])
        assert saved["kind"] == "feynman"
        assert saved["data"]["concept"] == "recursion"
        assert "your own words" in mock_bot.send_message.call_args[0][1]


def test_feynman_explanation_is_probed_by_ai():
    session = {"kind": "feynman", "data": {"concept": "recursion"}}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_fresh", return_value="Good start! But what stops it?") as mock_ask,
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="a function that calls itself"))
        assert mock_ask.called
        probed = mock_ask.call_args[0][2]
        assert "recursion" in probed and "calls itself" in probed
        mock_send.assert_called_once()
        assert "session:123" not in store.d  # consumed


# ── /choose (topic recommender) ──────────────────────────────────────────────

def test_cmd_choose_starts_session_and_asks_first_question():
    store = FakeStore()
    with patch("bot.handlers.store", store), patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import cmd_choose, CHOOSE_QUESTIONS

        cmd_choose(make_message(text="/choose"))
        saved = json.loads(store.d["session:123"])
        assert saved["kind"] == "choose"
        assert saved["data"] == {"step": 0, "answers": []}
        assert CHOOSE_QUESTIONS[0] in mock_bot.send_message.call_args[0][1]


def test_choose_answer_advances_to_next_question_without_ai():
    from bot.handlers import CHOOSE_QUESTIONS

    session = {"kind": "choose", "data": {"step": 0, "answers": []}}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_fresh") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="coding"))
        mock_ask.assert_not_called()  # no AI call until the final answer
        saved = json.loads(store.d["session:123"])
        assert saved["data"]["answers"] == ["coding"]
        assert saved["data"]["step"] == 1
        assert CHOOSE_QUESTIONS[1] in mock_bot.send_message.call_args[0][1]


def test_choose_final_answer_recommends_topic_and_clears():
    from bot.handlers import CHOOSE_QUESTIONS

    n = len(CHOOSE_QUESTIONS)
    prior = [f"answer{i}" for i in range(n - 1)]  # all but the last answered
    session = {"kind": "choose", "data": {"step": n - 1, "answers": prior}}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_fresh", return_value="Try Python basics! /explain variables") as mock_ask,
        patch("bot.handlers.keep_typing", MagicMock()),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="build a game"))
        assert mock_ask.called
        qa = mock_ask.call_args[0][2]
        assert "build a game" in qa  # the final answer reaches the model
        assert CHOOSE_QUESTIONS[0] in qa  # questions are included for context
        mock_send.assert_called_once()
        assert "session:123" not in store.d  # session consumed


# ── /review (spaced repetition) ──────────────────────────────────────────────

def test_quiz_wrong_answer_records_a_miss():
    session = {"kind": "quiz", "data": VALID_QUIZ}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_ai"),
        patch("bot.handlers.review.record_miss") as mock_record,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="B"))  # wrong (correct is A)
        mock_record.assert_called_once()
        assert mock_record.call_args[0][1]["question"] == VALID_QUIZ["question"]


def test_review_empty_deck_message():
    store = FakeStore()
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.review.count_items", return_value=0),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_review

        cmd_review(make_message(text="/review"))
        assert "No review items yet" in mock_bot.send_message.call_args[0][1]


def test_review_presents_due_item_and_opens_session():
    store = FakeStore()
    due_item = {**VALID_QUIZ, "box": 0, "due_at": 0}
    with (
        patch("bot.handlers.store", store),
        patch("bot.handlers.review.count_items", return_value=1),
        patch("bot.handlers.review.next_due", return_value=due_item),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_review

        cmd_review(make_message(text="/review"))
        assert "len()" in mock_bot.send_message.call_args[0][1]
        saved = json.loads(store.d["session:123"])
        assert saved["kind"] == "review"


def test_review_answer_reschedules():
    item = {**VALID_QUIZ, "box": 0, "due_at": 0}
    session = {"kind": "review", "data": item}
    store = FakeStore({"session:123": json.dumps(session)})
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.store", store),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.review.reschedule", return_value="I'll show this again in 3 days.") as mock_resched,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message(text="A"))  # correct
        mock_ask.assert_not_called()  # review grading is local
        mock_resched.assert_called_once()
        assert mock_resched.call_args[0][2] is True  # correct flag
        sent = mock_bot.send_message.call_args[0][1]
        assert "Correct" in sent and "3 days" in sent
        assert "session:123" not in store.d
