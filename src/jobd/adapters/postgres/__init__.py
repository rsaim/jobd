"""Postgres — the derived working store (PRD §6).

Deliberately *not* behind a port. PRD G6 promises that the LLM, the storage, and
the message sources are swappable; the database is not on that list, and a
protocol with exactly one implementation and no prospect of a second is
speculation dressed as architecture. If a second store ever appears, the
repositories here are the seam to extract.

Everything in this package is rebuildable from raw storage by construction
(I3) — losing it costs re-derivation time, never data.
"""

from dataclasses import dataclass
from typing import Any

from jobd.adapters.postgres.migrator import Migration, MigrationError, Migrator
from jobd.adapters.postgres.repositories import (
    ApplicationRepository,
    CompanyRepository,
    CompanyVerificationRepository,
    ContactRepository,
    MessageCompanyRepository,
    MessageRepository,
    PromptSnippetRepository,
    ReviewQueueRepository,
    SenderCategoryRepository,
    SenderRuleRepository,
    StageEventRepository,
)
from jobd.services.classify import (
    ApplicationStore,
    CompanyStore,
    ContactStore,
    MessageCompanyStore,
    MessageStore,
    ReviewStore,
    SenderRuleStore,
    StageStore,
)


@dataclass(frozen=True, slots=True)
class PostgresRepositories:
    """Every repository, over one connection. Satisfies `services.Repositories`.

    Fields are annotated with the *protocols*, not the concrete classes.
    Protocol attributes are invariant, so a bundle typed with the repositories
    does not satisfy one typed with the narrow stores even though every method
    matches — a mismatch that reads as nonsense until you know the rule. It is
    stated once here rather than rediscovered at each call site.
    """

    companies: CompanyStore
    contacts: ContactStore
    applications: ApplicationStore
    messages: MessageStore
    stages: StageStore
    reviews: ReviewStore
    sender_rules: SenderRuleStore
    message_companies: MessageCompanyStore
    #: Concrete, not protocol-typed: only cli.main's `learn` command uses this
    #: (setting a whole category's verdict), never classify_pending's hot
    #: path — which reads categories only indirectly, already resolved, via
    #: SenderRuleRepository.all()'s join. No service-layer protocol needed.
    sender_categories: SenderCategoryRepository
    #: Concrete too, same reasoning: only cli.main's `verify` command and the
    #: dashboard read/write this — never classify_pending's hot path, which
    #: doesn't know verification exists.
    verifications: CompanyVerificationRepository
    #: Concrete too: a small human-curated library the dashboard reads/
    #: writes directly, no service-layer protocol needed — classify_pending
    #: never touches it.
    prompts: PromptSnippetRepository


def repositories(conn: Any) -> PostgresRepositories:
    """Assemble the repository bundle for one connection."""
    return PostgresRepositories(
        companies=CompanyRepository(conn),
        contacts=ContactRepository(conn),
        applications=ApplicationRepository(conn),
        messages=MessageRepository(conn),
        stages=StageEventRepository(conn),
        reviews=ReviewQueueRepository(conn),
        sender_rules=SenderRuleRepository(conn),
        message_companies=MessageCompanyRepository(conn),
        sender_categories=SenderCategoryRepository(conn),
        verifications=CompanyVerificationRepository(conn),
        prompts=PromptSnippetRepository(conn),
    )


__all__ = [
    "Migration",
    "MigrationError",
    "Migrator",
    "PostgresRepositories",
    "repositories",
]
