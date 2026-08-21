"""Profile resolution (PRD P6): one image, switched by environment.

``local`` runs real data on the user's machine. ``demo`` runs synthetic data on
AWS and must never see a real mailbox. The split is an environment variable, not
a build flag, because P6 says *one image*.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class Profile(StrEnum):
    """Deployment profile."""

    LOCAL = "local"
    DEMO = "demo"


class ConfigError(ValueError):
    """Raised when the environment describes a configuration jobd will not run."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything jobd needs before it touches anything.

    Deliberately small. Adapter-specific settings (bucket name, model routing,
    OAuth client) arrive with their adapters — putting them here now would mean
    a config object listing capabilities the system does not have.
    """

    profile: Profile
    database_url: str | None = None
    #: A real LiteLLM model id (e.g. `openrouter/google/gemini-2.5-flash-lite`)
    #: turns the chat panel on. Absent means the feature does not exist on
    #: this deployment, not that it is broken — `/` renders with no chat
    #: toggle and no error (docs/chat-and-summaries.md §10 gate 8).
    chat_model: str | None = None

    @property
    def is_demo(self) -> bool:
        """True when running the synthetic public profile."""
        return self.profile is Profile.DEMO


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build settings from the environment.

    Args:
        env: Override mapping. Defaults to ``os.environ``. Injected rather than
            read globally so tests need no monkeypatching.

    Raises:
        ConfigError: If ``JOBD_PROFILE`` is set to something that is not a
            profile. Defaulting a typo to ``local`` would silently run real-data
            behaviour on a box the user thought was the demo.
    """
    source = os.environ if env is None else env

    raw = source.get("JOBD_PROFILE", Profile.LOCAL.value).strip().lower()
    try:
        profile = Profile(raw)
    except ValueError:
        valid = ", ".join(p.value for p in Profile)
        raise ConfigError(
            f"unknown JOBD_PROFILE {raw!r}; expected one of: {valid}"
        ) from None

    database_url = source.get("DATABASE_URL") or None
    chat_model = source.get("JOBD_CHAT_MODEL") or None
    return Settings(profile=profile, database_url=database_url, chat_model=chat_model)
