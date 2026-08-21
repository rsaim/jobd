"""Pure domain core.

Nothing here performs I/O or imports an adapter. The record proper — Company,
Application, StageEvent, Message, Contact (PRD P3) — lands in M3. M2 holds only
the raw envelope, because a port cannot have a signature without it.
"""
