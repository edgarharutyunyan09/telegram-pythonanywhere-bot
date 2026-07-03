"""Tests for bot/review.py — the spaced-repetition review deck."""

import json
from unittest.mock import patch


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


QUIZ = {
    "question": "What does len() return?",
    "options": {"A": "length", "B": "type", "C": "memory", "D": "error"},
    "correct": "A",
    "explanation": "len() returns the number of items.",
}


def test_record_miss_adds_item():
    store = FakeStore()
    with patch("bot.review.store", store):
        from bot.review import record_miss, count_items

        record_miss(123, QUIZ)
        assert count_items(123) == 1
        saved = json.loads(store.d["missed:123"])[0]
        assert saved["question"] == QUIZ["question"]
        assert saved["box"] == 0
        assert saved["due_at"] > 0


def test_record_miss_is_deduped_by_question():
    store = FakeStore()
    with patch("bot.review.store", store):
        from bot.review import record_miss, count_items

        record_miss(123, QUIZ)
        record_miss(123, QUIZ)  # same question again
        assert count_items(123) == 1


def test_next_due_returns_only_due_items():
    now = 1_000_000
    items = [
        {**QUIZ, "question": "due one", "box": 0, "due_at": now - 10},
        {**QUIZ, "question": "future one", "box": 1, "due_at": now + 10_000},
    ]
    store = FakeStore({"missed:123": json.dumps(items)})
    with patch("bot.review.store", store):
        from bot.review import next_due

        item = next_due(123, now=now)
        assert item is not None
        assert item["question"] == "due one"


def test_next_due_none_when_nothing_due():
    now = 1_000_000
    items = [{**QUIZ, "box": 1, "due_at": now + 10_000}]
    store = FakeStore({"missed:123": json.dumps(items)})
    with patch("bot.review.store", store):
        from bot.review import next_due

        assert next_due(123, now=now) is None


def test_reschedule_correct_moves_up_a_box():
    now = 1_000_000
    items = [{**QUIZ, "box": 0, "due_at": now}]
    store = FakeStore({"missed:123": json.dumps(items)})
    with patch("bot.review.store", store):
        from bot.review import reschedule

        msg = reschedule(123, items[0], correct=True, now=now)
        saved = json.loads(store.d["missed:123"])[0]
        assert saved["box"] == 1
        assert saved["due_at"] == now + 3 * 86400  # box 1 = 3 days
        assert "3 days" in msg


def test_reschedule_wrong_resets_to_box_zero():
    now = 1_000_000
    items = [{**QUIZ, "box": 3, "due_at": now}]
    store = FakeStore({"missed:123": json.dumps(items)})
    with patch("bot.review.store", store):
        from bot.review import reschedule

        msg = reschedule(123, items[0], correct=False, now=now)
        saved = json.loads(store.d["missed:123"])[0]
        assert saved["box"] == 0
        assert saved["due_at"] == now + 1 * 86400  # box 0 = 1 day
        assert "tomorrow" in msg


# ── Graceful degradation (stateless mode) ────────────────────────────────────

def test_functions_are_safe_no_ops_without_store():
    with patch("bot.review.store", None):
        from bot.review import record_miss, count_items, next_due, reschedule

        record_miss(123, QUIZ)  # should not raise
        assert count_items(123) == 0
        assert next_due(123) is None
        assert reschedule(123, QUIZ, correct=True) == ""
