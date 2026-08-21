"""jobd-ai — local-first agentic personal CRM for a job search.

Layout follows the hexagonal stance in docs/jobd-prd.md §6:

    domain/    pure core. Imports nothing outside itself and the stdlib.
    ports/     protocols the core talks through. No I/O, no SDKs.
    adapters/  implementations of those ports. Empty until M4.
    config/    profile resolution (`local` | `demo`).
    cli/       the `jobd` entrypoint (PRD P4(b)).

The one-way rule — domain and ports never import adapters — is enforced by
tests/test_import_graph.py, not by convention.
"""

__version__ = "0.1.0"
