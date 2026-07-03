"""Spaced-repetition review deck for missed quiz questions.

Missed /quiz questions are logged per user under ``missed:<user_id>`` (a JSON
array) and resurfaced by /review on a simple Leitner-box schedule: a freshly
missed question starts at box 0 (due in 1 day); each correct review bumps it up
a box (a longer gap) while a wrong review sends it back to box 0.

Like the other consumer modules (history, preferences), every function degrades
gracefully when the store is unavailable — no store just means no review deck,
never a crash. Scheduling is pull-based (the caller runs /review); due dates
live inside each item, so no background scheduler is required — which suits
PythonAnywhere's free tier, where there isn't one.
"""

import json
import time

from bot.clients import store

# Leitner intervals in days, indexed by box.
_INTERVALS_DAYS = [1, 3, 7, 16, 35]
_DAY = 86400


def _key(user_id) -> str:
    return f"missed:{user_id}"


def _load(user_id) -> list:
    if store is None:
        return []
    try:
        raw = store.get(_key(user_id))
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"Store read error (review): {e}")
        return []


def _save(user_id, items) -> None:
    if store is None:
        return
    try:
        store.set(_key(user_id), json.dumps(items))
    except Exception as e:
        print(f"Store write error (review): {e}")


def count_items(user_id) -> int:
    """Total questions in the user's review deck."""
    return len(_load(user_id))


def record_miss(user_id, quiz) -> None:
    """Add a missed quiz question to the deck, deduped by question text.

    A question already in the deck keeps its existing schedule rather than being
    reset, so missing the same item repeatedly in one sitting doesn't churn its
    timing.
    """
    if store is None:
        return
    items = _load(user_id)
    question = quiz.get("question", "")
    if not question or any(it.get("question") == question for it in items):
        return
    items.append(
        {
            "question": question,
            "options": quiz.get("options", {}),
            "correct": quiz.get("correct", ""),
            "explanation": quiz.get("explanation", ""),
            "box": 0,
            "due_at": time.time() + _INTERVALS_DAYS[0] * _DAY,
        }
    )
    _save(user_id, items)


def next_due(user_id, now=None):
    """Return the earliest-due review item (a dict), or None if none are due."""
    now = time.time() if now is None else now
    items = _load(user_id)
    due = [it for it in items if it.get("due_at", 0) <= now]
    if not due:
        return None
    return min(due, key=lambda it: it.get("due_at", 0))


def reschedule(user_id, item, correct, now=None) -> str:
    """Update an item's box + due date after a review answer.

    Correct → move up a box (longer interval); wrong → back to box 0. Returns a
    short human-friendly line describing when the item will next appear.
    """
    now = time.time() if now is None else now
    items = _load(user_id)
    question = item.get("question", "")
    target = next((it for it in items if it.get("question") == question), None)
    if target is None:
        return ""
    if correct:
        target["box"] = min(target.get("box", 0) + 1, len(_INTERVALS_DAYS) - 1)
    else:
        target["box"] = 0
    days = _INTERVALS_DAYS[target["box"]]
    target["due_at"] = now + days * _DAY
    _save(user_id, items)
    return "I'll show this again tomorrow." if days == 1 else f"I'll show this again in {days} days."
