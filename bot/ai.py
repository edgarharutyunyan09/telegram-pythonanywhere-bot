from bot.config import SYSTEM_PROMPT
from bot.history import get_history, save_history
from bot.providers import generate


def ask_ai(user_id: int, user_message: str) -> str:
    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history

    reply = generate(user_id, messages)

    history.append({"role": "assistant", "content": reply})
    save_history(user_id, history)
    return reply


def ask_fresh(user_id: int, system_prompt: str, user_message: str) -> str:
    """One-off AI call with a custom system prompt and no conversation history.

    Used by structured commands (e.g. /quiz, /practice) that need an isolated
    response instead of a turn in the ongoing tutoring conversation — the
    generated JSON or problem statement shouldn't leak into (or be steered by)
    the user's chat history. The provider preference (main/hf) is still
    respected via generate().
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    return generate(user_id, messages)
