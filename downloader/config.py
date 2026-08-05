"""Runtime configuration loaded from the environment.

Values are read from process environment variables, with a `.env` file (if
present in the current directory or any parent) loaded first as a convenience
so secrets like API keys don't have to be exported on every run.
"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

# Env var names.
GOOGLE_API_KEY_ENV = "GOOGLE_API_KEY"
FILESTER_API_KEY_ENV = "FILESTER_API_KEY"

_loaded = False  # pylint: disable=invalid-name  # mutable module state, not a constant


def load_env() -> None:
    """Load a `.env` file into the process environment (idempotent).

    Existing environment variables are never overridden by the `.env` file.
    """
    global _loaded  # pylint: disable=global-statement  # one-shot guard for an idempotent loader
    if _loaded:
        return
    load_dotenv(find_dotenv(usecwd=True), override=False)
    _loaded = True


def get(name: str, default: str = "") -> str:
    """Return an env var, ensuring the `.env` file has been loaded first."""
    load_env()
    return os.environ.get(name, default).strip()


def google_api_key() -> str:
    """Google Drive / Google APIs key, or an empty string if unset."""
    return get(GOOGLE_API_KEY_ENV)


def filester_api_key() -> str:
    """Filester API key (sent as a Bearer token), or an empty string if unset."""
    return get(FILESTER_API_KEY_ENV)
