# Architecture

Hexagonal. A pure domain core, five ports, and adapters that can be swapped
without the core noticing. Below that, event sourcing: raw messages in object
storage are the source of truth, and Postgres is a derived working store that
can be thrown away and rebuilt (I3).

## The whole system

```mermaid
flowchart TB
    subgraph outside["Outside world"]
        gmail[Gmail API]
        s3[(S3 · raw envelopes)]
        pg[(Postgres · derived)]
        model[LLM · local or cloud]
        keyring[OS keyring]
    end

    subgraph adapters["Adapters — replaceable"]
        gsrc[gmail.GmailSource]
        s3a[S3Storage]
        fsa[FilesystemStorage]
        repos[postgres.repositories]
        llm[llm.RuleBased / Ollama / LiteLLM]
        secrets[TokenStore]
    end

    subgraph ports["Ports — five protocols"]
        p1[[MessageSource]]
        p2[[Storage]]
        p3[[LLMProvider]]
        p4[[Backup]]
        p5[[Sender]]
    end

    subgraph services["Services — orchestration"]
        ingest[ingest_account]
        classify[classify_pending]
        rebuild[rebuild]
        timeline[timeline]
    end

    subgraph domain["Domain — pure, no I/O"]
        raw[raw · envelope + codec]
        keys[keys · content hash]
        pre[prefilter · cheap triage]
        extract[extraction · schema + prompt]
        resolve[resolve · entity matching]
        record[record · P3 dataclasses]
    end

    gmail --> gsrc --> p1 --> ingest
    secrets <--> keyring
    gsrc -.reads token.-> secrets
    ingest --> p2 --> s3a --> s3
    p2 --> fsa
    ingest --> repos --> pg
    classify --> p3 --> llm --> model
    classify --> repos
    rebuild -->|re-reads every object| p2
    rebuild --> repos
    timeline --> repos

    ingest --- keys
    ingest --- raw
    classify --- pre
    classify --- extract
    classify --- resolve
    classify --- record

    p5:::unbuilt
    p4:::unbuilt
    classDef unbuilt stroke-dasharray: 4 4
```

Dashed ports are declared and not yet implemented. `Sender` stays empty until
M8, and when it arrives it is reachable only behind a recorded human approval
(I1) — the planner never holds it.

## One message, end to end

```mermaid
sequenceDiagram
    autonumber
    participant G as Gmail
    participant I as ingest_account
    participant S as Storage (S3)
    participant P as Postgres
    participant C as classify_pending
    participant M as LLMProvider

    Note over I,G: history id read BEFORE fetching,<br/>or mail arriving mid-sync is lost forever
    I->>G: getProfile → head history id
    G-->>I: 184231
    I->>G: messages.list / history.list (format=raw)
    G-->>I: RFC-822 bytes

    I->>I: content_hash(source, account, payload)
    I->>S: put(key, envelope) — write-once, IfNoneMatch:*
    Note right of S: second run: precondition fails,<br/>swallowed. Idempotent (I2)
    I->>P: insert message row (ON CONFLICT DO NOTHING)
    I->>P: save cursor = head

    C->>P: unclassified messages
    C->>C: prefilter.score(...)
    alt below threshold
        C-->>P: skipped, no model call, no cost
    else candidate
        C->>M: render_for_model(...) + schema
        M-->>C: extraction + confidence
        alt confidence >= 0.75
            C->>P: company / application / stage_event (+ evidence link)
        else below 0.75
            C->>P: review_queue — never the record
        end
    end
```

## Why raw is the source of truth

```mermaid
flowchart LR
    s3[(raw envelopes<br/>immutable, versioned)] -->|derive| pg[(Postgres)]
    pg -->|"jobd rebuild"| drop[TRUNCATE derived tables]
    drop --> s3

    style s3 stroke-width:3px
```

Every derived table is disposable. A better prompt, a fixed resolver, a new
stage — none of them need a migration that back-fills, because the input is
still on disk and re-deriving the world is a routine command rather than an
event. The cursor table is the one exception: it records how far a *source* has
been read, which raw storage cannot tell you.

## Where the rules live

| Invariant | Enforced by |
|---|---|
| I1 · no send without recorded approval | `Sender` port unimplemented; planner holds no send-capable tool |
| I2 · idempotent ingestion | content hash + write-once `put` + `ON CONFLICT DO NOTHING` |
| I3 · rebuildable from raw | `services/rebuild.py`, tested by deriving twice and comparing |
| I4 · raw mail stays in your storage | only pre-filtered candidates reach a cloud model; corpus in CI is synthetic |
| I5 · ingested content is untrusted | extraction is schema-constrained; no tool the model can call |
