"""Path helpers for Hermes profile-local LifeEngine storage."""

from __future__ import annotations

import os
from pathlib import Path

from .constants import DB_FILENAME


def hermes_home() -> Path:
    """Return the active Hermes profile home.

    Hermes exposes hermes_constants.get_hermes_home() in current main.  The
    fallback keeps CLI scripts testable outside Hermes.
    """
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()


def lifeengine_home() -> Path:
    p = hermes_home() / "lifeengine"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return lifeengine_home() / DB_FILENAME


def exports_dir() -> Path:
    p = lifeengine_home() / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p
