# Schema and storage layout (M3)

Two layers, and it matter which is which.

| Layer | Where | Status |
|---|---|---|
| Raw messages | S3 (or filesystem), content-hash keyed JSONL | **Source of truth.** Immutable. Cannot be rebuilt |
| The record | Postgres | **Derived.** Rebuildable from raw by `jobd rebuild` (I3) |

Lose Postgres and you lose time. Lose the bucket and you lose the project.

---

## 1. Raw storage key layout

```
raw/<source>/<ab>/<sha256>.jsonl
```

- `raw/` — lifecycle rules in `infra/terraform/storage` target this prefix. Change it and every future object silently opt out of the STANDARD_IA transition.
- `<source>` — `gmail`, `linkedin` (companion-extension push, docs/linkedin.md), `linkedin-archive`, …
- `<ab>` — first two hex chars of the hash. Keep any one listing prefix small; `jobd rebuild` walk these.
- `<sha256>.jsonl` — one JSON object, one line. Single-line files concatenate into a valid JSONL stream, so bulk re-derive is cheap.

### What go into the hash

`sha256(framed(scheme, source, account, payload))`. Each field length-prefixed
with 8 bytes big-endian, so `("gmail", "ab")` and `("gmaila", "b")` cannot
collide.

**In:** scheme version, source, account, payload bytes.
**Out, deliberately:**

| Excluded | Why |
|---|---|
| `fetched_at` | Differ every run. In the hash = a re-run duplicate the whole five-year backfill |
| `external_id` | Provider that renumber, or an archive re-exported next year, must not produce a second copy |
| `metadata` | Labels change over time. The message not |

`account` **is** in. Same recruiter mail landing in two of your inboxes is two
real arrivals. Collapse them at raw layer and you destroy evidence the record
layer need to merge properly.

### SHA-256, not `hash()`

Python `hash()` is salted per interpreter. An implementation using it pass every
single-process test and still re-upload everything after a restart. Gate 4 test
this with two subprocesses at different `PYTHONHASHSEED`.

### Write-once

`put()` check `exists()` first and skip. Re-fetch produce an envelope whose
`fetched_at` differ, so overwrite would mean a **new S3 version of every object
on every run** — a cost, and a false claim something changed. First write win.
`fetched_at` therefore record first retrieval, not latest.

### Envelope

```json
{"v":1,"source":"gmail","external_id":"18c0f","account":"me@example.com",
 "fetched_at":"2026-08-10T12:00:00+00:00","payload_b64":"...","metadata":{}}
```

Payload base64 — mail is arbitrary bytes, JSON strings are not, and
re-encoding through a text codec would corrupt attachments *and* change the
hash. Reader meeting unknown `v` refuse rather than guess: a misparsed archive
is worse than no archive, because it look like it worked.

`encode`/`decode` live in `jobd.domain.raw`, not in an adapter. Envelope format
**is** the source of truth format. Adapter-private format = archive readable
only by that adapter, opposite of what I3 promise.

---

## 2. The record

```
company ──< company_alias
   │
   ├──< application ──< stage_event ──> message (evidence, NOT NULL)
   │
   └──< message
        │
contact ──< contact_identity
   └──< contact_company >── company
```

### Decisions that a later "tidy-up" would break

**No unique constraint on `(company_id, role_title)`.** Applying to the same
role at the same company in 2021 and again in 2024 is two applications.
Uniqueness here would fuse two hiring processes into one corrupted timeline.
M3 gate 3 exist to keep this from being tidied up.

**`stage_event.evidence_message_id` is NOT NULL.** G2 promise every claim link
to its evidence. Nullable would make that promise optional in practice.

**`message.storage_key` is UNIQUE.** This is where idempotency lands at the
record layer: a second ingest of the same content collide instead of inserting
a duplicate row (I2).

**`contact_identity(channel, lower(identifier))` is UNIQUE.** An address belong
to exactly one contact. A merge rewrite the pointer; it never leave the same
address on two people. This table *is* the cross-channel merge (G1).

**Unique index on `lower(domain)` is partial** (`WHERE domain IS NOT NULL`).
Most companies are first seen in a signature block with no domain, and NULLs
must not collide.

**`contact_company` is time-bounded, not a column on `contact`.** A recruiter
who pinged you from three firms keep all three relationships instead of
overwriting them.

**TEXT + CHECK, not native ENUM.** Adding a value to a native enum is a
migration that cannot run inside a transaction with other DDL. Removing one is
worse. CHECK is boring and reversible.

**timestamptz everywhere.** A job search cross timezones. Naive timestamps
would silently reorder a timeline.

### "Ghosted" is a derived flag, not a stage

This resolve the PRD §10 open question, and M3 was the deadline because it is a
schema decision.

Ghosting is the **absence** of an event. Nobody send the mail meaning "we have
stopped replying to you", so there is no evidence message — and every
`stage_event` is evidence-linked by construction. It cannot be a row.

Derived instead by `jobd.domain.record.is_ghosted`, from three conditions:

1. last message went **outbound** — silence after *their* mail is your turn, the opposite signal;
2. application have not reached a terminal stage — rejection then silence is an ending, and counting it would inflate the ghost rate G5 want measured;
3. more than `after_days` (default 21) passed.

Because derived, changing the threshold re-derive the world for free (I3).
Storing it would have made that a migration.

Same two facts — last-touch recency plus direction — drive whose-turn-is-it in
P4.1. One derivation, two surfaces.

### Search

`message.search_tsv` is `GENERATED ALWAYS AS ... STORED` over subject + body,
with a GIN index. Generated, not trigger-maintained: cannot drift from the text
it index, and no ordering hazard on bulk insert.

Search run **in SQL** (`websearch_to_tsquery`), never in Python. P4.1 require
that of dashboard filters, and M9 reuse the same query — start anywhere else
and you write it twice.

### Embeddings

`message.embedding vector(1536)`, nullable, populated in M5.

**Known limit:** dimension is fixed at the column, so switching to an embedder
of different width (nomic-embed is 768) is a migration, not a re-derive. The
alternative — a separate `message_embedding` table keyed by model — is the right
shape if more than one embedder ever matter. Not built now, because one embedder
is speculation about the second.

---

## 3. Migrations

Plain SQL, `NNNN_name.up.sql` + `NNNN_name.down.sql`. No ORM, no Alembic: the
PRD pick no ORM, and adopting one here would decide that question by accident.
Plain SQL also make the *down* direction real work rather than autogenerated
guesswork — and down is half the gate.

```bash
jobd migrate status
jobd migrate up
jobd migrate down            # to 0 — drops everything, including the ledger
jobd migrate down --to 1     # stop at version 1
```

Two safeguards:

- **Every migration must ship a down.** Missing one is an error at discovery time, not a surprise the first time somebody need a rollback.
- **Applied migrations are checksummed.** Edit one that already ran and the migrator refuse: the database can no longer be reproduced from the repo, and failing now is recoverable where discovering it during a rebuild months later is not.

Each migration run in its own transaction. Failure halfway through a set leave
the database on the last migration that fully succeeded — never half-applied.

`down` to 0 drop `schema_migrations` too. Gate 1 say *empty*, and leftover
bookkeeping make "does down really work?" un-answerable by looking.
