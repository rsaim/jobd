"""Application services: orchestration over ports.

Services may import `domain` and `ports`. They may **not** import `adapters` —
which adapter is in play is the caller's decision, and a service that reaches
for a concrete one has quietly stopped being swappable.
"""

from jobd.services.classify import ClassifyResult, Repositories, classify_pending
from jobd.services.ingest import IngestResult, ingest_account, ingest_day
from jobd.services.rebuild import RebuildResult
from jobd.services.verify import VerifyOutcome, verify_companies

# `rebuild` the function is deliberately NOT re-exported here. It would shadow
# `jobd.services.rebuild` the module, so `from jobd.services import rebuild`
# would silently hand back a function — and the resulting AttributeError points
# at the call site rather than at this line. Import it from its module.

__all__ = [
    "ClassifyResult",
    "IngestResult",
    "RebuildResult",
    "Repositories",
    "VerifyOutcome",
    "classify_pending",
    "ingest_account",
    "ingest_day",
    "verify_companies",
]
