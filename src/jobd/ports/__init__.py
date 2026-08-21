"""The five ports (PRD §6). Protocols only — zero implementations in M2.

Every one is ``@runtime_checkable`` so tests/test_ports.py can assert that
nothing in the tree satisfies them yet. That assertion is M2's gate 4: the shape
is declared while it is still cheap to change, and no adapter exists to make it
expensive.
"""

from jobd.ports.backup import Backup
from jobd.ports.llm_provider import LLMProvider
from jobd.ports.message_source import MessageSource
from jobd.ports.sender import Sender
from jobd.ports.storage import Storage

ALL_PORTS: tuple[type, ...] = (MessageSource, LLMProvider, Storage, Backup, Sender)

__all__ = ["ALL_PORTS", "Backup", "LLMProvider", "MessageSource", "Sender", "Storage"]
