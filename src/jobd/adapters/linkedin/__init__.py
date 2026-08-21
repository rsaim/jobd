"""LinkedIn adapter — DMs pushed by the companion browser extension (M6)."""

from jobd.adapters.linkedin.push import PushedMessage, to_raw_message

__all__ = ["PushedMessage", "to_raw_message"]
