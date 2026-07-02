import json
import os
import random
from datetime import datetime
from bot.clients import bot, BOT_INFO, store
from bot.config import (
    COMMIT_SHA,
    HF_SPACE_ID,
    HOSTING_LABEL,
    MODEL,
    RATE_LIMIT,
    SYSTEM_PROMPT,
)
from bot.ai import ask_ai, ask_fresh
from bot.helpers import is_allowed, keep_typing, send_reply, should_respond
from bot.history import clear_history
from bot.preferences import get_provider, set_provider
from bot.rate_limit import is_rate_limited

# Verbose console logging for local dev and teaching. Enabled by
# BOT_VERBOSE_LOG=1 (run_local.py sets this automatically). Prints one
# line per inbound/outbound message so kids and teachers can see the
# conversation flow in their terminal while the bot is running.
VERBOSE_LOG = os.environ.get("BOT_VERBOSE_LOG", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _log(message, direction: str, text: str) -> None:
    """Print a one-line trace of a message in verbose mode.

    direction is "in" (user → bot) or "out" (bot → user). Text is
    truncated to 500 characters so long AI replies don't flood the
    terminal. Newlines are collapsed for single-line readability.
    """
    if not VERBOSE_LOG:
        return
    user = message.from_user
    user_name = (
        f"@{user.username}" if user.username else (user.first_name or f"user:{user.id}")
    )
    bot_name = f"@{BOT_INFO.username}"
    snippet = (text or "").replace("\n", " ").replace("\r", " ")
    if len(snippet) > 500:
        snippet = snippet[:500] + "..."
    if direction == "in":
        sender, receiver = user_name, bot_name
    else:
        sender, receiver = bot_name, user_name
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {sender} → {receiver}: {snippet}", flush=True)


@bot.message_handler(commands=["start"], func=is_allowed)
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "Welcome! I'm your AI tutor, here to explain concepts clearly and help you learn by answering your questions.",
    )


# Single source of truth for the command list. Used both to render /help and
# to register Telegram's command menu (set_my_commands) so the two never drift.
# Each entry is (name, args, description); /model is appended when HF is enabled.
COMMANDS = [
    ("start", "", "welcome message"),
    ("help", "", "show this command list"),
    ("reset", "", "clear conversation history"),
    ("about", "", "about this bot"),
    ("explain", "<topic>", "clear, step-by-step explanation"),
    ("eli5", "<topic>", "explain like I'm five"),
    ("quiz", "[topic]", "a multiple-choice question"),
    ("practice", "[subject]", "get a problem to solve"),
    ("score", "", "your quiz score"),
    ("skip", "", "leave the current quiz/practice"),
    ("joke", "", "one short, clean programming joke"),
    ("quote", "", "a short motivational line"),
    ("fact", "", "a short, surprising fact"),
    ("compliment", "", "brighten someone's day"),
    ("roast", "[name]", "a short, playful roast"),
    ("roll", "", "roll a dice (1-6)"),
    ("remember", "", "save a note"),
    ("recall", "", "show your saved notes"),
    ("forget", "", "delete your saved notes"),
]


def _help_lines():
    lines = [
        f"/{name}{(' ' + args) if args else ''} — {desc}"
        for name, args, desc in COMMANDS
    ]
    if HF_SPACE_ID:
        lines.append("/model — switch AI provider")
    return lines


def telegram_commands():
    """(name, description) pairs for Telegram's set_my_commands menu."""
    pairs = [(name, desc) for name, _args, desc in COMMANDS]
    if HF_SPACE_ID:
        pairs.append(("model", "switch AI provider"))
    return pairs


@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    bot.send_message(message.chat.id, "\n".join(_help_lines()))


@bot.message_handler(commands=["reset"], func=is_allowed)
def cmd_reset(message):
    clear_history(message.from_user.id)
    _clear_session(message.from_user.id)
    bot.send_message(message.chat.id, "Conversation cleared. Starting fresh!")


@bot.message_handler(commands=["roll"], func=is_allowed)
def cmd_roll(message):
    result = random.randint(1, 6)
    bot.send_message(message.chat.id, f"You rolled a {result}! 🎲")


def _load_notes(user_id: int) -> list:
    """Return the user's saved notes as a list.

    Notes are stored as a JSON array under note:<user_id>. Older data
    saved as a bare string is treated as a single-item list so nothing
    is lost when upgrading from the single-note version.
    """
    raw = store.get(f"note:{user_id}")
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return [raw]  # legacy single-string note
    return value if isinstance(value, list) else [value]


@bot.message_handler(commands=["remember"], func=is_allowed)
def cmd_remember(message):
    parts = message.text.split(maxsplit=1)
    note = parts[1].strip() if len(parts) > 1 else ""
    if not note:
        bot.send_message(message.chat.id, "Usage: /remember <something to save>")
        return
    if store is None:
        bot.send_message(message.chat.id, "Memory isn't available right now.")
        return
    try:
        notes = _load_notes(message.from_user.id)
        notes.append(note)
        store.set(f"note:{message.from_user.id}", json.dumps(notes))
        bot.send_message(
            message.chat.id, f"Saved! You now have {len(notes)} note(s). Use /recall to see them."
        )
    except Exception as e:
        print(f"Store write error (remember): {e}")
        bot.send_message(message.chat.id, "Couldn't save that. Try again later.")


@bot.message_handler(commands=["recall"], func=is_allowed)
def cmd_recall(message):
    if store is None:
        bot.send_message(message.chat.id, "Memory isn't available right now.")
        return
    try:
        notes = _load_notes(message.from_user.id)
    except Exception as e:
        print(f"Store read error (recall): {e}")
        bot.send_message(message.chat.id, "Couldn't read your notes. Try again later.")
        return
    if notes:
        listed = "\n".join(f"{i}. {n}" for i, n in enumerate(notes, start=1))
        bot.send_message(message.chat.id, f"You asked me to remember:\n{listed}")
    else:
        bot.send_message(
            message.chat.id, "I don't have anything saved. Use /remember <text>."
        )


@bot.message_handler(commands=["forget"], func=is_allowed)
def cmd_forget(message):
    if store is None:
        bot.send_message(message.chat.id, "Memory isn't available right now.")
        return
    try:
        store.delete(f"note:{message.from_user.id}")
        bot.send_message(message.chat.id, "Forgotten! Your saved note is gone.")
    except Exception as e:
        print(f"Store delete error (forget): {e}")
        bot.send_message(message.chat.id, "Couldn't forget that. Try again later.")


@bot.message_handler(commands=["about"], func=is_allowed)
def cmd_about(message):
    if HF_SPACE_ID:
        provider = get_provider(message.from_user.id)
        model_line = f"{MODEL} (main)" if provider == "main" else f"{HF_SPACE_ID} (hf)"
    else:
        model_line = MODEL
    storage_line = "SQLite" if store is not None else "stateless (no memory)"
    # Summarize the system prompt with its first sentence so /about tells
    # the user what the bot is actually for, without dumping the whole prompt.
    summary = SYSTEM_PROMPT.split(". ")[0].strip().rstrip(".")
    lines = [
        f"Model  : {model_line}",
        f"Storage: {storage_line}",
        f"Hosting: {HOSTING_LABEL}",
        f"Role   : {summary}",
    ]
    if COMMIT_SHA:
        lines.append(f"Version: {COMMIT_SHA}")
    bot.send_message(message.chat.id, "\n".join(lines))


# ── Learning commands ────────────────────────────────────────────────────────
# Shared helpers so every AI-backed command gets the same rate limiting, typing
# indicator, long-message splitting, and error handling as the main chat handler
# — instead of each command re-implementing (and forgetting) those.

SESSION_TTL = 3600  # a pending /quiz or /practice expires after 1 hour
QUIZ_SYSTEM = (
    "You are a quiz generator for a student. Produce ONE multiple-choice question "
    "on the given topic. Respond with ONLY a compact JSON object — no markdown, no "
    "prose — in exactly this shape:\n"
    '{"question": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "..."}, '
    '"correct": "A", "explanation": "one or two sentences"}\n'
    "Exactly one option is correct. Keep it suitable for a learner."
)
PRACTICE_SYSTEM = (
    "You are a practice-problem generator for a student. Produce ONE clear practice "
    "problem on the given subject, suitable for a learner. State only the problem — "
    "do NOT reveal the solution or the answer. Keep it to a few sentences."
)
GRADE_SYSTEM = (
    "You are a patient tutor grading a student's answer to a practice problem. Say "
    "clearly whether they are right. If wrong, gently point out the mistake and guide "
    "them toward the correct approach without simply handing over the answer. Keep it "
    "brief and encouraging."
)


def _arg(message) -> str:
    """Return the text after the command word, or '' if none.

    Guarding the split length avoids the IndexError a bare command with a
    trailing space (e.g. ``/roast ``) would otherwise raise.
    """
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _rate_limited_notice(message) -> bool:
    """If the user is over their daily limit, tell them and return True."""
    if is_rate_limited(message.from_user.id):
        limit_msg = f"You've reached the daily limit of {RATE_LIMIT} messages. Try again tomorrow."
        bot.send_message(message.chat.id, limit_msg)
        _log(message, "out", f"[rate limited] {limit_msg}")
        return True
    return False


def _ai_reply(message, prompt: str) -> None:
    """Rate-limit, show typing, call the AI over the user's history, and reply."""
    if _rate_limited_notice(message):
        return
    try:
        with keep_typing(message.chat.id):
            reply = ask_ai(message.from_user.id, prompt)
        send_reply(message, reply)
        _log(message, "out", reply)
    except Exception as e:
        print(f"Error in AI command: {e}")
        bot.send_message(message.chat.id, "Something went wrong. Please try again.")
        _log(message, "out", f"[error] {e}")


# ── Quiz / practice session state (stored via the KV store) ───────────────────

def _get_session(user_id: int):
    """Return the user's pending {'kind', 'data'} session, or None."""
    if store is None:
        return None
    try:
        raw = store.get(f"session:{user_id}")
        return json.loads(raw) if raw else None
    except Exception as e:
        print(f"Store read error (session): {e}")
        return None


def _set_session(user_id: int, kind: str, data: dict) -> None:
    try:
        store.set(
            f"session:{user_id}",
            json.dumps({"kind": kind, "data": data}),
            ex=SESSION_TTL,
        )
    except Exception as e:
        print(f"Store write error (session): {e}")


def _clear_session(user_id: int) -> None:
    if store is None:
        return
    try:
        store.delete(f"session:{user_id}")
    except Exception as e:
        print(f"Store delete error (session): {e}")


def _get_score(user_id: int):
    """Return (correct, total) for the user's running quiz score."""
    try:
        raw = store.get(f"score:{user_id}")
        if raw:
            d = json.loads(raw)
            return int(d.get("correct", 0)), int(d.get("total", 0))
    except Exception as e:
        print(f"Store read error (score): {e}")
    return 0, 0


def _bump_score(user_id: int, correct: bool) -> None:
    try:
        c, t = _get_score(user_id)
        store.set(
            f"score:{user_id}",
            json.dumps({"correct": c + (1 if correct else 0), "total": t + 1}),
        )
    except Exception as e:
        print(f"Store write error (score): {e}")


def _parse_quiz(raw: str):
    """Parse the model's JSON quiz, tolerating code fences or surrounding prose.

    Returns a normalized dict, or None if the response isn't a usable quiz.
    """
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    question = data.get("question")
    options = data.get("options")
    correct = str(data.get("correct", "")).strip().upper()
    if not isinstance(question, str) or not isinstance(options, dict):
        return None
    if not all(k in options for k in ("A", "B", "C", "D")):
        return None
    if correct not in ("A", "B", "C", "D"):
        return None
    return {
        "question": question.strip(),
        "options": {k: str(options[k]).strip() for k in ("A", "B", "C", "D")},
        "correct": correct,
        "explanation": str(data.get("explanation", "")).strip(),
    }


def _extract_choice(text: str):
    """Map a quiz reply to 'A'..'D', accepting a letter or the digits 1-4."""
    t = (text or "").strip().upper()
    if not t:
        return None
    ch = t[0]
    if ch in ("A", "B", "C", "D"):
        return ch
    if ch in ("1", "2", "3", "4"):
        return "ABCD"[int(ch) - 1]
    return None


@bot.message_handler(commands=["explain"], func=is_allowed)
def cmd_explain(message):
    topic = _arg(message)
    if not topic:
        bot.send_message(
            message.chat.id, "Usage: /explain <topic> — e.g. /explain how a for loop works"
        )
        return
    _ai_reply(
        message,
        f"Explain this clearly, step by step, for a student who is learning it: {topic}",
    )


@bot.message_handler(commands=["eli5"], func=is_allowed)
def cmd_eli5(message):
    topic = _arg(message)
    if not topic:
        bot.send_message(message.chat.id, "Usage: /eli5 <topic> — e.g. /eli5 recursion")
        return
    _ai_reply(
        message,
        "Explain this to a complete beginner in the simplest possible terms, using a "
        f"short everyday analogy: {topic}",
    )


@bot.message_handler(commands=["quiz"], func=is_allowed)
def cmd_quiz(message):
    if store is None:
        bot.send_message(message.chat.id, "Quizzes need memory, which isn't available right now.")
        return
    if _rate_limited_notice(message):
        return
    topic = _arg(message) or "general knowledge"
    try:
        with keep_typing(message.chat.id):
            raw = ask_fresh(message.from_user.id, QUIZ_SYSTEM, f"Topic: {topic}")
        quiz = _parse_quiz(raw)
    except Exception as e:
        print(f"Quiz generation error: {e}")
        bot.send_message(message.chat.id, "Couldn't make a quiz right now. Please try again.")
        return
    if not quiz:
        bot.send_message(message.chat.id, "I couldn't build a quiz on that. Try a different topic.")
        return
    _set_session(message.from_user.id, "quiz", quiz)
    opts = quiz["options"]
    body = (
        f"❓ {quiz['question']}\n\n"
        + "\n".join(f"{k}) {opts[k]}" for k in ("A", "B", "C", "D"))
        + "\n\nReply A, B, C, or D. (/skip to leave the quiz)"
    )
    bot.send_message(message.chat.id, body)
    _log(message, "out", body)


@bot.message_handler(commands=["practice"], func=is_allowed)
def cmd_practice(message):
    if store is None:
        bot.send_message(message.chat.id, "Practice needs memory, which isn't available right now.")
        return
    if _rate_limited_notice(message):
        return
    subject = _arg(message) or "general knowledge"
    try:
        with keep_typing(message.chat.id):
            problem = ask_fresh(message.from_user.id, PRACTICE_SYSTEM, f"Subject: {subject}")
    except Exception as e:
        print(f"Practice generation error: {e}")
        bot.send_message(
            message.chat.id, "Couldn't make a practice problem right now. Please try again."
        )
        return
    problem = (problem or "").strip()
    if not problem:
        bot.send_message(message.chat.id, "I couldn't build a problem on that. Try a different subject.")
        return
    _set_session(message.from_user.id, "practice", {"problem": problem})
    body = f"📝 {problem}\n\nReply with your answer and I'll check it. (/skip to give up)"
    bot.send_message(message.chat.id, body)
    _log(message, "out", body)


@bot.message_handler(commands=["score"], func=is_allowed)
def cmd_score(message):
    if store is None:
        bot.send_message(message.chat.id, "Scores need memory, which isn't available right now.")
        return
    correct, total = _get_score(message.from_user.id)
    if total == 0:
        bot.send_message(message.chat.id, "No quiz answers yet. Start one with /quiz.")
        return
    pct = round(100 * correct / total)
    bot.send_message(
        message.chat.id, f"Quiz score: {correct}/{total} ({pct}%). Keep going with /quiz!"
    )


@bot.message_handler(commands=["skip"], func=is_allowed)
def cmd_skip(message):
    if _get_session(message.from_user.id) is None:
        bot.send_message(message.chat.id, "Nothing to skip. Start something with /quiz or /practice.")
        return
    _clear_session(message.from_user.id)
    bot.send_message(message.chat.id, "Okay, cleared. What next?")


def _handle_quiz_answer(message, quiz) -> None:
    choice = _extract_choice(message.text)
    if choice is None:
        # Not a recognizable A-D answer — keep the session so they can retry.
        bot.send_message(message.chat.id, "Reply with A, B, C, or D — or /skip to leave the quiz.")
        return
    _clear_session(message.from_user.id)
    correct = quiz["correct"]
    _bump_score(message.from_user.id, choice == correct)
    got, total = _get_score(message.from_user.id)
    if choice == correct:
        head = f"✅ Correct — {correct}) {quiz['options'][correct]}"
    else:
        head = f"❌ Not quite. You chose {choice}; the answer is {correct}) {quiz['options'][correct]}"
    expl = quiz.get("explanation", "")
    body = head + (f"\n\n{expl}" if expl else "") + f"\n\nScore: {got}/{total} — next? /quiz"
    bot.send_message(message.chat.id, body)
    _log(message, "out", body)


def _handle_practice_answer(message, data) -> None:
    if _rate_limited_notice(message):
        return
    _clear_session(message.from_user.id)
    prompt = (
        "Here is a practice problem the student was given:\n\n"
        f"{data.get('problem', '')}\n\n"
        f"The student's answer:\n\n{message.text}"
    )
    try:
        with keep_typing(message.chat.id):
            reply = ask_fresh(message.from_user.id, GRADE_SYSTEM, prompt)
        send_reply(message, reply)
        _log(message, "out", reply)
    except Exception as e:
        print(f"Practice grading error: {e}")
        bot.send_message(message.chat.id, "Something went wrong grading that. Please try again.")


def _handle_session_reply(message, session) -> bool:
    """Route a message that answers a pending /quiz or /practice.

    Returns True if the message was consumed by a session, False if the session
    was unrecognized (dropped) and the message should fall through to chat.
    """
    kind = session.get("kind")
    data = session.get("data") or {}
    if kind == "quiz":
        _handle_quiz_answer(message, data)
        return True
    if kind == "practice":
        _handle_practice_answer(message, data)
        return True
    _clear_session(message.from_user.id)
    return False


if HF_SPACE_ID:

    @bot.message_handler(commands=["model"], func=is_allowed)
    def cmd_model(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = get_provider(message.from_user.id)
            bot.send_message(
                message.chat.id,
                f"Current provider: {current}\n\n"
                "Options:\n"
                "/model main — Cerebras (fast, multilingual, with memory)\n"
                "/model hf — ArmGPT (Armenian only, slow, no memory)",
            )
            return
        choice = parts[1].strip().lower()
        if choice not in ("main", "hf"):
            bot.send_message(
                message.chat.id, "Invalid choice. Use: /model main or /model hf"
            )
            return
        if not set_provider(message.from_user.id, choice):
            bot.send_message(
                message.chat.id, "Could not save preference. Try again later."
            )
            return
        if choice == "hf":
            bot.send_message(
                message.chat.id,
                "Switched to hf (ArmGPT).\n\n"
                "Note: this is a tiny base completion model trained only on Armenian text. "
                "It will continue whatever you write rather than answer questions, "
                "and it does not understand English. Replies take ~30-60s and there is no memory.",
            )
        else:
            bot.send_message(message.chat.id, "Switched to Main Provider.")


# Fun commands — registered on every install (they only need the main AI, not
# HF) and routed through _ai_reply so they share the rate limiting, typing
# indicator, and error handling of the main chat handler.

@bot.message_handler(commands=["joke"], func=is_allowed)
def cmd_joke(message):
    _ai_reply(message, "Tell one short, clean programming joke.")


@bot.message_handler(commands=["quote"], func=is_allowed)
def cmd_quote(message):
    _ai_reply(message, "Give one short, original motivational line.")


@bot.message_handler(commands=["fact"], func=is_allowed)
def cmd_fact(message):
    _ai_reply(message, "Tell one short, surprising fact.")


@bot.message_handler(commands=["compliment"], func=is_allowed)
def cmd_compliment(message):
    _ai_reply(message, "Give one short, warm, genuine compliment.")


@bot.message_handler(commands=["roast"], func=is_allowed)
def cmd_roast(message):
    name = _arg(message) or "you"
    _ai_reply(message, f"Write a short, playful, friendly roast of {name}.")


@bot.message_handler(content_types=["text"], func=is_allowed)
def handle_message(message):
    if not should_respond(message):
        return
    text = (message.text or "").replace(f"@{BOT_INFO.username}", "").strip()
    if not text:
        # Edited messages, forwards, or stickers-with-empty-caption can
        # arrive with no usable text. Don't burn rate-limit / AI calls on them.
        return
    _log(message, "in", text)
    # If the user has a pending /quiz or /practice, this message answers it.
    session = _get_session(message.from_user.id)
    if session and _handle_session_reply(message, session):
        return
    _ai_reply(message, text)


