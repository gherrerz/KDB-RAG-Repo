<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 2.0.0
Bump rationale: MAJOR. Core Principles redefined and recounted per explicit user
direction — the prior five project-domain principles are replaced by four
quality-pillar principles (code quality, testing, UX consistency, performance).
Project-critical runtime invariants (backend integrity, anti-hallucination) are
preserved under Security & Operational Constraints so no governance is silently lost.

Modified principles (v1.0.0 → v2.0.0):
  I. Evidence-Based & Anti-Hallucination   → folded into Security & Operational Constraints
  II. Storage & Runtime Backend Integrity  → folded into Security & Operational Constraints
  III. Contract-First API Readiness         → absorbed into II. Testing Standards + III. UX Consistency
  IV. Python Engineering Standards          → I. Code Quality
  V. Documentation & Changelog Discipline   → folded into Development Workflow & Quality Gates
  (new) I. Code Quality
  (new) II. Testing Standards
  (new) III. User Experience Consistency
  (new) IV. Performance Requirements

Added sections: none (section names retained; content updated)
Removed sections: none

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — Constitution Check is generic; no edit needed.
  ✅ .specify/templates/spec-template.md — no constitution references; no edit needed.
  ✅ .specify/templates/tasks-template.md — no constitution references; no edit needed.
  ✅ .claude/skills/speckit-constitution — command file; no outdated references.

Follow-up TODOs: none. Ratification date unchanged (original adoption 2026-06-30).
-->
# RAG Hybrid Response Validator Constitution

## Core Principles

### I. Code Quality

Code MUST be readable, modular, and verifiable before it is fast or clever. Non-negotiable rules:
- All public functions MUST carry type hints using built-in generics (`list[str]`, `dict[str, int]`).
  Docstrings are required on public functions; inline comments are reserved for non-obvious WHY.
- SOLID MUST be honored: single responsibility per unit, dependencies injected by parameter or
  constructor (never instantiated inside business logic), composition preferred over inheritance,
  and I/O separated from business logic.
- PEP 8 MUST be observed (4-space indent, lines ≤ 79 chars); linting and formatting MUST pass in CI.
- Abstractions with multiple implementations MUST be expressed via `Protocol` or ABC.
- Added complexity MUST be justified against a simpler rejected alternative; unjustified
  complexity is grounds to block the change.

**Rationale**: A multi-backend hybrid system (vector / graph / lexical) survives only with strict
seams. DI, typed contracts, and clean abstractions keep the implementations interchangeable and the
codebase auditable, which directly enables the testing and performance guarantees below.

### II. Testing Standards (NON-NEGOTIABLE)

Behavior MUST be protected by tests proportional to its blast radius:
- Every abstraction with multiple implementations MUST ship contract tests, so any backend
  (Postgres metadata, Chroma vectors, Neo4j graph, lexical store) is verified against the same
  behavioral contract.
- API readiness and error semantics MUST be tested: `/query` and `/query/retrieval` MUST be covered
  for the `query_ready=false` / incompatible-embedding paths returning HTTP 422, and the MCP surface
  MUST be tested to expose only query/read/ingest operations (admin/destructive excluded).
- Tests MUST exercise behavior beyond the happy path — boundary conditions, error scenarios, and
  incremental-vs-full reingestion paths.
- A bug fix MUST add a regression test that fails before the fix and passes after.
- Tests MUST be deterministic; external services MUST be faked or isolated, not depended upon
  implicitly.

**Rationale**: Retrieval correctness is the product. Contract tests keep the stores mutually
consistent, and readiness tests guarantee the system fails closed (422) rather than answering over an
incompatible or not-yet-ready index.

### III. User Experience Consistency

The system presents two coherent surfaces — the PySide6 desktop UI and the API/MCP contracts — and
both MUST behave predictably:
- API contracts MUST be stable and versioned in documentation; request/response shapes, status codes,
  and readiness semantics MUST NOT change without updating `docs/API_REFERENCE.md` in the same change.
- Error handling MUST be uniform: contract violations return well-formed HTTP errors (e.g. 422 for
  not-ready / incompatible embeddings) with actionable messages, never opaque failures or partial
  speculative results.
- The MCP tool surface MUST stay consistent with the REST OpenAPI (tool name = `operation_id`) and
  expose the same read/query/ingest capabilities, so agents and humans see one coherent system.
- UI and API MUST report the same readiness state (`query_ready`, `embedding_compatible`) for a repo;
  the user MUST never be offered an action the backend will reject.

**Rationale**: Inconsistent contracts break the agents and operators that depend on this service.
A single, predictable interface across UI, REST, and MCP is what makes the validator trustworthy to
automate against.

### IV. Performance Requirements

The system MUST stay responsive and resource-disciplined under real ingestion and query load:
- Reingestion MUST be incremental by commit diff for Chroma and the lexical store when a
  `last_indexed_commit` and resolvable diff exist; only changed files are re-embedded/re-indexed.
  Full purge + reindex is the fallback path, NOT the default, to avoid redundant embedding cost.
- Long-running ingestion MUST run as async jobs (JobManager / RQ) and MUST NOT block the API request
  path; clients track progress via `/jobs/{id}`.
- Query and retrieval paths MUST avoid unbounded work: result sets, graph expansion, and rerank
  candidates MUST be bounded by explicit limits.
- Performance-sensitive changes (retrieval, reranking, embedding, ingestion) MUST state their expected
  impact and MUST NOT regress latency or embedding/token cost without justification.
- Startup MUST be resilient: migrations and storage health checks MUST be isolated so a slow or failing
  dependency cannot hang the process indefinitely.

**Rationale**: Embedding and graph rebuilds are the expensive operations; making them incremental,
asynchronous, and bounded is what keeps the service usable at repository scale instead of degrading on
every commit.

## Security & Operational Constraints

These project-critical runtime invariants are binding and MUST NOT be silently overridden:
- **Backend integrity**: Metadata persistence MUST use Postgres (NO SQLite fallback; `metadata.db`
  exists only in isolated test artifacts). Chroma runs remote by default (`CHROMA_MODE=remote`). The
  Neo4j graph MUST be rebuilt in full on reingestion to preserve cross-file edge consistency. Active
  LLM runtime providers are OpenAI, Gemini, or Vertex — Anthropic is NOT an active runtime provider.
- **Evidence-based output**: The system MUST NOT invent modules, relationships, or capabilities absent
  from the code, MUST state when the repo provides no evidence, and MUST reject queries against a
  non-`query_ready` repository with a contract error rather than a speculative answer.
- **Secrets & containers**: Secrets MUST NOT be committed or baked into build layers/logs. Container
  changes (`Dockerfile`, `docker-compose*.yml`) MUST use multi-stage builds, pinned slim/alpine base
  tags, a non-root user, and a `HEALTHCHECK`.
- **Webhook & MCP auth**: The Bitbucket webhook MUST validate inbound signatures via
  `WEBHOOK_BITBUCKET_SECRET` (HMAC-SHA256). The MCP server MUST honor its token gate (`MCP_API_TOKEN`
  via `X-MCP-Token`) and only pass through the allowlisted identity headers
  (`x-role-id` / `x-user-id` / `x-country-id`).

## Development Workflow & Quality Gates

- Every change MUST satisfy the applicable Core Principles before merge; reviewers MUST verify
  compliance explicitly.
- **Documentation gate**: Any change to endpoints, environment variables, configuration, or ingestion
  flows MUST update docs in the same change. `CHANGELOG.md` MUST always be updated under `[Unreleased]`
  in the correct Keep a Changelog category; `docs/API_REFERENCE.md`, `docs/CONFIGURATION.md`, and
  `README.md` MUST be updated when their surface changes.
- **Testing gate**: New or changed abstractions with multiple implementations MUST include contract
  tests; bug fixes MUST include a regression test (Principle II).
- **Contract gate**: API/MCP changes MUST keep UI, REST, and MCP consistent and preserve readiness/error
  semantics (Principle III).
- **Performance gate**: Changes to ingestion or retrieval MUST state expected performance impact and
  MUST NOT regress latency or embedding/token cost without justification (Principle IV).

## Governance

This constitution supersedes other ad-hoc practices for this repository. Amendments MUST be proposed
via pull request that states the change, its rationale, and any required migration of code, docs, or
templates. Approval requires reviewer sign-off confirming the dependent templates
(`.specify/templates/*`) and runtime guidance (`CLAUDE.md`, `README.md`, `docs/`) remain consistent.

Versioning follows semantic versioning of governance: MAJOR for backward-incompatible principle
removals or redefinitions, MINOR for a newly added principle or materially expanded guidance, PATCH
for clarifications and non-semantic refinements. All PRs and reviews MUST verify compliance with the
principles above; the backend-integrity and evidence-based invariants in Security & Operational
Constraints are non-negotiable and may not be waived per-change. Use `CLAUDE.md` for day-to-day
runtime development guidance.

**Version**: 2.0.0 | **Ratified**: 2026-06-30 | **Last Amended**: 2026-06-30
