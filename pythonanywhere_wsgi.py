"""
PythonAnywhere WSGI entry point.

PA's web app loader (configured in the "Web" tab -> "WSGI configuration
file") imports a callable named `application` from a Python file. This
module exposes the Flask app from `api/index.py` under that name, after
loading a `.env` file from the project root so secrets work the same way
they do for `run_local.py`.

How to wire this up on PA:

  1. In your PA dashboard: Web -> (your app) -> Code -> "WSGI
     configuration file" (its path will be
     /var/www/<username>_pythonanywhere_com_wsgi.py). Replace its
     contents with:

         import sys
         project_home = "/home/<username>/telegram-pythonanywhere-bot"
         if project_home not in sys.path:
             sys.path.insert(0, project_home)
         from pythonanywhere_wsgi import application  # noqa: F401

     Substitute your PA username and the path where you cloned the repo.

  2. Upload a `.env` file to the project root on PA (same format as
     local). This file populates os.environ before bot.config is
     imported.

  3. Set the virtualenv path in the "Web" tab so Flask and the bot's
     dependencies resolve correctly.

  4. Reload the web app from the "Web" tab.

Only PA's WSGI loader ever imports this file.
"""

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    # Same minimal loader as run_local.py — keeps PA and local dev in
    # lockstep without pulling in a python-dotenv dependency.
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parent / ".env")

from api.index import app as application  # noqa: E402, F401

# Auto-register the Telegram webhook on every worker boot. Idempotent
# when WEBHOOK_URL is already current; a no-op when WEBHOOK_URL is
# unset (e.g. local dev). Logged so the result shows up in PA's
# server.log. Imported here (not at module top) so api/index.py's
# Flask app finishes setup first.
from bot.clients import register_commands, register_webhook  # noqa: E402

print(register_webhook())
# Push the command menu to Telegram so the "/" menu lists every command.
print(register_commands())
