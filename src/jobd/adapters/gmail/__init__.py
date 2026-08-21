"""Gmail adapter: BYO OAuth (`auth`) and the `MessageSource` (`source`)."""

from jobd.adapters.gmail.auth import AuthError, load_credentials, run_wizard
from jobd.adapters.gmail.source import GmailSource, HistoryTooOld

__all__ = [
    "AuthError",
    "GmailSource",
    "HistoryTooOld",
    "load_credentials",
    "run_wizard",
]
