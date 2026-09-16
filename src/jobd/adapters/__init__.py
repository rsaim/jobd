"""Adapters — implementations of the five ports. Empty on purpose in M2.

Each arrives with the milestone that needs it:

    gmail          MessageSource   M4
    linkedin       push renderer   M6 (no pull API exists)
    s3             Storage         M4 (write-through on ingest)
    litellm/ollama LLMProvider     M5
    postgres       Backup          M3
    gmail_drafts   Sender          M8

Nothing under this package may be imported by ``jobd.domain`` or ``jobd.ports``.
tests/test_import_graph.py fails the build if it is.
"""
