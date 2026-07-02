"""
Run the bot locally via polling — no webhook needed.

This is the SAME bot code that runs in production. The only difference is
how Telegram delivers messages to us:

    Production (PythonAnywhere): Telegram → POST /api/webhook → api/index.py
    Local (this file):           we ask Telegram "any new messages?" in a loop

Polling is perfect for learning and local development because you can
edit a file, rerun this script, and see your changes instantly — no
deploy step.

Usage:
    python run_local.py

Requirements:
    Create a .env file in the project root with at least:

        TELEGRAM_BOT_TOKEN=123456:ABC...
        AI_API_KEY=csk-...

    See .env.example for the full list of optional variables
    (SQLITE_PATH, WEBHOOK_SECRET, HF_SPACE_ID, etc.).
"""

import os
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader with zero dependencies.

    Reads KEY=VALUE lines from a .env file and copies them into
    os.environ. Skips blank lines and comments. Strips surrounding
    quotes from values. Does not overwrite variables that are already
    set in the shell environment.
    """
    env_file = Path(path)
    if not env_file.exists():
        print(f"No {path} file found. Create one from .env.example first.")
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# IMPORTANT: load .env BEFORE importing anything from bot/. bot.config
# reads environment variables at import time and will crash if any
# required variable is missing.
load_dotenv()

# Turn on verbose console logging for local dev. bot/handlers.py reads
# this at import time and prints one line per inbound/outbound message.
# Set BEFORE the bot imports below.
os.environ.setdefault("BOT_VERBOSE_LOG", "1")


def preflight() -> None:
    """Fail fast with a friendly message if required env vars are missing.

    Without this, bot.config raises a bare KeyError deep inside an import
    chain, which is baffling for anyone learning the codebase.
    """
    required = {
        "TELEGRAM_BOT_TOKEN": "from @BotFather on Telegram",
        "AI_API_KEY": "from your AI provider (e.g. cloud.cerebras.ai → API Keys)",
    }
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if not missing:
        return

    print("ERROR: Missing required environment variable(s):\n")
    for key in missing:
        print(f"  - {key}  ({required[key]})")
    print()
    print("Add them to your .env file (see .env.example for the full list).")
    print("If you don't have a .env yet, run: cp .env.example .env")
    raise SystemExit(1)


preflight()

import bot.handlers  # noqa: F401  — registers all @bot.message_handler decorators
from bot.clients import bot, BOT_INFO, register_commands


def main() -> None:
    print(f"Bot @{BOT_INFO.username} starting in polling mode.\n")

    # Telegram only allows ONE delivery method at a time (webhook OR
    # polling, not both). If a production webhook is registered,
    # polling will silently receive zero updates until the webhook is
    # removed. Check first and ask the user before clobbering it.
    info = bot.get_webhook_info()
    if info.url:
        print("WARNING: this bot token already has a webhook registered:")
        print(f"    {info.url}")
        print()
        print("Telegram only allows one delivery method per bot token at a")
        print("time. To run locally via polling, the webhook must be removed.")
        print()
        print("If this is your PRODUCTION bot, your deployed bot will stop")
        print("receiving messages until you re-register the webhook with:")
        print()
        print('    curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<URL>"')
        print()
        answer = input("Remove the webhook and start polling? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted. No changes made.")
            return
        bot.remove_webhook()
        print("Webhook removed.\n")

    # Push the command menu to Telegram so the "/" menu lists every command.
    print(register_commands())

    print("Send your bot a message on Telegram to try it out.")
    print("Press Ctrl+C to stop.\n")

    # infinity_polling blocks the main thread and automatically
    # reconnects on transient network errors. Ctrl+C stops it cleanly.
    bot.infinity_polling(timeout=20, long_polling_timeout=20)


if __name__ == "__main__":
    main()
