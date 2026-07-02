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
from bot.ai import ask_ai
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


@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    lines = [
        "/start — welcome message",
        "/help  — show this message",
        "/reset — clear conversation history",
        "/about — about this bot",
        "/joke — tell one short, clean programming joke",
        "/quote — share a short motivational line",
        "/fact — share a short, surprising fact",
        "/compliment — brighten someone's day",
        "/roll — roll a dice (1-6)",
        "/remember — save a note",
        "/recall — show your saved note",
        "/forget — delete your saved note",
    ]
    if HF_SPACE_ID:
        lines.append("/model — switch AI provider")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["reset"], func=is_allowed)
def cmd_reset(message):
    clear_history(message.from_user.id)
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


    @bot.message_handler(commands=["joke"], func=is_allowed)
    def cmd_joke(message):
        reply = ask_ai(message.from_user.id, "Tell one short, clean programming joke.")
        bot.send_message(message.chat.id, reply)


    @bot.message_handler(commands=["quote"], func=is_allowed)
    def cmd_quote(message):
        reply = ask_ai(message.from_user.id, "Give one short, original motivational line.")
        bot.send_message(message.chat.id, reply)


    @bot.message_handler(commands=["fact"], func=is_allowed)
    def cmd_fact(message):
        reply = ask_ai(message.from_user.id, "Tell one short, surprising fact.")
        bot.send_message(message.chat.id, reply)


    @bot.message_handler(commands=["compliment"], func=is_allowed)
    def cmd_compliment(message):
        reply = ask_ai(message.from_user.id, "Give one short, warm, genuine compliment.")
        bot.send_message(message.chat.id, reply)


    @bot.message_handler(commands=["roast"], func=is_allowed)
    def cmd_roast(message):
        name = message.text.split(maxsplit=1)[1] if " " in message.text else "you"
        reply = ask_ai(message.from_user.id, f"Write a short, playful, friendly roast of {name}.")
        bot.send_message(message.chat.id, reply)


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
    if is_rate_limited(message.from_user.id):
        limit_msg = f"You've reached the daily limit of {RATE_LIMIT} messages. Try again tomorrow."
        bot.send_message(message.chat.id, limit_msg)
        _log(message, "out", f"[rate limited] {limit_msg}")
        return
    try:
        with keep_typing(message.chat.id):
            reply = ask_ai(message.from_user.id, text)
        send_reply(message, reply)
        _log(message, "out", reply)
    except Exception as e:
        print(f"Error in handle_message: {e}")
        bot.send_message(message.chat.id, "Something went wrong. Please try again.")
        _log(message, "out", f"[error] {e}")


