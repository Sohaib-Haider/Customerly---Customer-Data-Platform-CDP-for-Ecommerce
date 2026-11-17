---
title: "Identity resolution: future decision and backend plan"
status: "PROPOSAL ONLY — NOT APPROVED FOR IMPLEMENTATION"
date: "2026-11-17"
scope: "Customerly CDP; store-scoped, first-party CSV data only"
agent_instruction: "Coding agents: do not implement this document, treat it as an approved decision, or use it to change current behavior. Ignore it during implementation work unless the user explicitly asks to revisit and approve this future proposal. It exists only to decide later whether identity resolution should be built."
implementation_status: "Not implemented by this document"
---

# Identity resolution: future decision and backend plan

> **Future consideration only.** This document explains an optional feature and how it could be built if approved later. It does not authorize code, migrations, database changes, a new Django app, new workers, or frontend changes. The existing product must continue working without this feature. All new names and endpoints below are proposals, not existing functionality.

## 1. What identity resolution means here

Identity resolution links different customer records that belong to the same person into one unified customer identity. The original records remain in their original source context.

For Customerly, the question would be: **within this store and an explicitly compatible set of sources, do these saved customer records describe the same person?**

It would not mean guessing the identity of someone across unrelated businesses, looking people up on the internet, or using a machine-learning model to invent a connection.

### Visual reference: separate records becoming connected profiles

![Hightouch illustration showing source records being connected into unified customer profiles](https://cdn.sanity.io/images/pwmfmi47/production/0b873c346e0c8fe0ba0e108ae6e72b318e27068c-2400x1256.jpg)

Image credit and explanation: [Hightouch — What is Identity Resolution?](https://hightouch.com/blog/what-is-identity-resolution). This is the published diagram corresponding to the user's visual example, not a screenshot of functionality implemented in Customerly.

## 2. What the current product already does

The following observations come from the repository, not from the reference product:

| Existing part | Current responsibility | What it does not establish |
|---|---|---|
| Mapping app | Recognizes uploaded columns and maps approved fields to canonical schema attributes | Whether two customer rows from different sources are the same person |
| Preprocessing app / ingestion cleaning | Cleans approved data under the ingestion contract | Permission to combine identities or invent missing contact identifiers |
| Ingestion app | Imports multiple CSVs, validates relationships, saves source records, schedules segment work | A customer identity shared across different source namespaces |
| `customers` | Stores source-specific customer records with nullable email and phone | A separate unified-person layer or verified contact ownership |
| Analytics / Customer 360 | Reads a particular saved customer and its linked records | A unified history across separately identified customers |
| Segments app | Uses final churn and CLV PKL files, checks eligible data, saves predictions and audience memberships | Identity matching, household resolution, or safe cross-source feature fusion |
| Background preparation worker | Processes churn/CLV customers in batches and saves durable progress | Identity-resolution runs |

In `backend/apps/ingestion/compiler.py`, incoming business identifiers are qualified with a source namespace, and deterministic UUIDs use the store, table, and qualified identifier. The database also has a unique `(store_id, source_customer_id)` constraint. This helps repeated imports reuse the same source record.

For example, `ordersystem:101` and `messaging:509` remain different customer records even if both have the same email. That is deliberate source preservation; it is not cross-source identity resolution.

CSV upload is the current **input method**. Different CSV files may represent different business sources, while multiple files in one ingestion batch may be tables from the same registered source. A filename alone does not define a new identity namespace.

## 3. Why this could be useful for our CDP

One store might upload purchase history, email interactions, and website activity with different customer IDs. Today, each customer profile can show only records attached to its particular canonical customer ID. One person may therefore appear as several customers with incomplete histories.

An identity layer could provide:

- One unified Customer 360 with references to the original purchase, messaging, and activity records.
- A distinct-person count alongside the existing source-customer count.
- Explainable links showing which rule or reviewer connected records.
- A later basis for person-level audiences, without duplicate people in audience lists.
- Visibility into how many records are linked, unresolved, or awaiting review.

This is valuable only when uploaded records actually have trustworthy identifiers in common. A page showing matching statistics without a working, auditable matching backend would not solve the problem.

## 4. Example limited to our product

One store uploads three source datasets:

| Source namespace | Source customer ID | Email | Phone | Available history |
|---|---|---|---|---|
| `shop` | `shop:101` | `sara@example.com` | Not supplied | Orders |
| `emailtool` | `emailtool:509` | `sara@example.com` | `+923001234567` | Messages |
| `website` | `website:882` | Not supplied | `+923001234567` | Events |

1. The store declares that these sources describe the same business/customer population.
2. The email produces an exact-match candidate between the first two records.
3. The phone produces a candidate involving the website record.
4. The engine checks identifier quality, trust settings, existing links, rejection decisions, and cluster conflicts.
5. Only an approved rule with adequate evidence, or a reviewer, can accept a link. An email/phone match by itself is not proof of verified ownership.
6. Accepted records become members of one unified profile, such as `CUSTOMER-001`.
7. A unified read follows the member customer IDs to their original orders, messages, and events.

The phone connection must not silently bridge several people sharing a family number. If the evidence is ambiguous, that candidate remains in review and the existing profiles remain separate.

The original UUIDs, source IDs, amounts, event timestamps, model results, and relational links remain intact.

## 5. Important limits in our existing data

### Unrelated demo datasets must stay unrelated

The demo store contains source populations such as `ret`, `dnb`, `chr`, `evb`, `msg`, and `cpn`. Putting them under one store ID for demonstration does not prove that their customers describe the same real business or people.

Do not automatically compare or connect these populations. Equal stripped IDs, matching row positions, similar values, or convenient examples are not identity evidence. Real evaluation needs a small, controlled fixture containing deliberately related records and known nonmatches.

### Contact data is not necessarily verified

The current customer schema has email and phone fields, but these are not contact-ownership verification records. The product must not describe an imported email as verified just because it was mapped, formatted, or present in a CSV.

If there are no common identifiers, the correct outcome is an unresolved source record or a singleton unified profile. Do not force a match to improve a dashboard percentage.

### Source identity is not transaction identity

Linking two customers does not prove that their two orders are the same order. Identity resolution does not remove duplicated order exports, convert currencies, or reconcile inconsistent definitions of revenue.

Likewise, it does not make different currencies safe to sum, make unrelated datasets compatible, create behavioral events, or make a customer eligible for an ML model without its required evidence.

## 6. Proposed initial scope

If this feature is approved, the first version would support:

1. Only records inside the authenticated store.
2. Only saved first-party CSV customer records and their already-linked canonical history.
3. Explicit source compatibility groups: sources must be declared to describe the same customer population before comparison.
4. Deterministic candidate generation from valid normalized email, phone with known country context, and explicitly declared shared business identifiers.
5. Review-first matching for ordinary uploaded contact data; automatic linking only for explicitly trusted rules with documented evidence and conflict checks.
6. Non-destructive links, stable unified IDs, durable runs, decision history, rejected-match blocks, and an undo/split procedure.
7. Read APIs for summaries, runs, source membership, candidates, identifiers, and unified Customer 360.

### Not in the first version

- Fuzzy names, probabilistic matching, a new trained matching model, or LLM-based person matching.
- Browser fingerprinting, IP matching, advertising identifiers, tracking SDKs, external enrichment, or third-party identity graphs.
- Cross-store or cross-business matching, household/account identity, or guessed links based on common names.
- Automatic attachment of anonymous events that lack a supported identifier/customer relationship.
- Deleting/rekeying existing canonical customer records or changing all existing foreign keys.
- Merging/retraining the current churn or CLV models, changing their training features, or combining conflicting source snapshots for them.
- Currency conversion, universal transaction deduplication, campaign delivery, consent-management implementation, or signup/signin development.
- The full feature set of the reference product, including warehouse connectors and real-time streaming resolution.

## 7. Proposed architecture and pipeline position

Use one optional Django app named `identity_resolution`. Its boundaries are matching policy, identifier claims, candidates, accepted links, review decisions, and resolution jobs. It should not be a module inside preprocessing or segmentation.

Proposed flow:

**CSV upload → Mapping → Preprocessing → Save source records → Queue identity resolution → Publish accepted links → Unified reads**

Saving source data must succeed independently of resolution. Queue work after the source-data transaction commits, using a durable outbox/job record written in the same transaction so a crash cannot lose the request. An `on_commit` notification may wake a worker, but must not be the only durable queue mechanism.

Existing Customer 360, source-level KPIs, and current segmentation continue to work while matching is disabled, queued, incomplete, or failed. Identity resolution is not a new mandatory preprocessing condition for the existing segment models.

Future app structure, if approved:

```text
backend/apps/identity_resolution/
  models.py
  authentication.py / permissions.py
  serializers.py
  views.py / urls.py
  services/
    normalization.py
    policies.py
    identifiers.py
    candidates.py
    conflicts.py
    linking.py
    review.py
    jobs.py
    selectors.py
  management/commands/process_identity_jobs.py
  migrations/
  tests/
```

The app's HTTP endpoints would run through the existing Django server. Long matching runs would need a worker, using the durable-job pattern already demonstrated by segmentation. A future development launcher/process supervisor can start required processes together; a Django app does not inherently require its own terminal.

### Visual reference: identifiers, matching, and unified output

![Hightouch process illustration for identity resolution](https://cdn.sanity.io/images/pwmfmi47/production/feb87f10ad8420bfaac115114d17682b940d39cd-2400x1256.jpg)

Credit: [Hightouch's explanation of the resolution process](https://hightouch.com/blog/what-is-identity-resolution). External images are linked for explanation, not downloaded or copied into this repository. The publisher may change the URLs; the source link remains a fallback. These diagrams illustrate a broader commercial product and do not expand our proposed scope.

## 8. Proposed database additions

All names in this section are **proposed**, and exact migration design requires a later implementation decision. The existing canonical schema and operational managed tables should not be treated as already containing these structures.

| Proposed table | Purpose | Important fields/invariants |
|---|---|---|
| `identity_domains` | Defines a compatible customer population within a store | ID, store, name, enabled status; no cross-store domains |
| `identity_source_policies` | Declares which source namespace belongs to a domain and what rules it permits | Store, domain, namespace, trust/authority settings, phone region if known, priority, policy version; unique store/namespace |
| `unified_customers` | Stable, internal unified-person record | UUID, store, domain, status, revision, timestamps; no replacement of canonical customer UUIDs |
| `identity_customer_links` | Current membership of each saved customer in a unified profile | Store, canonical customer, unified customer, accepted decision, timestamps; at most one active unified identity per source customer |
| `identity_identifiers` | Derived identifier claims used for indexed matching | Store, domain, customer, type, token, key version, provenance, quality/trust, active/stale state; a token may occur on multiple customers |
| `identity_candidates` | Reviewable customer-pair proposals | Ordered customer pair, store/domain, rule version, evidence references, status, evidence revision; avoid duplicate current candidates |
| `identity_decisions` | Immutable audit of accept/reject/link/unlink/split actions | Actor or system rule, before/after revisions, evidence references, reason, run, timestamps; accepted edges remain traceable |
| `identity_match_blocks` | Prevents known rejected links from being recreated automatically | Store/domain, ordered customer pair, reason, reviewer, active state; check across whole clusters |
| `identity_resolution_runs` | Durable run state and published counters | Store/domain, policy version, input boundary, cursor, lease, attempts, counts, safe error code, timestamps |

No raw orders, events, or monetary columns need to be duplicated into these tables.

### Required database protections

- Every relationship must enforce same-store membership. A UUID FK alone does not establish tenant consistency.
- Use composite uniqueness/FKs where practical, adding the required referenced unique keys through reviewed migrations. Supplement with service validation for domain/source-policy consistency and test that it cannot be bypassed.
- Index identifiers by store, domain, identifier type, key version and token. Do not compare all customers with every other customer.
- Do not make an email/phone token globally unique: shared identifiers and collisions must remain representable.
- Use an active-link uniqueness constraint, canonical ordering for candidate/block pairs, and a partial uniqueness constraint for active runs per store/domain.
- Enable RLS on new managed tables. Existing owner-level backend connections can bypass RLS, so application-level store filtering remains mandatory. Browser roles must not receive direct write access.
- Keep existing canonical IDs, source IDs, FK targets and source relationships intact. Do not reuse the existing `customers.id` column as the new unified ID.
- Keep run history and decision history separate from mutable current links. Decide retention and store-deletion behavior explicitly before migration.

## 9. Identifier normalization and trust rules

Normalization produces derived matching values; it must not overwrite contact data in `customers`.

### Email

- Trim surrounding whitespace and validate usable syntax.
- Normalize the domain conservatively; handle domain case/IDNA consistently.
- Specify the local-part case policy instead of blindly assuming every provider ignores case.
- Do not strip dots, remove plus tags, guess typo corrections, or equate similar addresses by default.
- Exclude blanks, placeholders and clearly shared/system addresses from automatic linking.
- A normal-looking imported email remains an unverified claim unless explicit source evidence says otherwise.

### Phone

- Parse into a consistent international representation only if the country code or trusted region is known.
- Do not guess a region from the developer's location or use last-N-digits matching.
- Treat invalid, shared, ambiguous or extension-dependent values as review-only or unusable, according to the rule version.

### Business IDs

- Source customer IDs remain meaningful within their own namespaces.
- Matching a shared external business ID across sources requires an explicit declaration that both fields use the same authoritative identity system.
- Never strip `ret:` / `msg:` / other prefixes and match equal numeric suffixes.

### Storage and evidence

Prefer store-scoped keyed tokens for normalized contact matching, with a separate server-side HMAC secret and recorded key version. Plain hashes of predictable emails/phone numbers are not an anonymity guarantee. Tokens are still sensitive derived data. Raw values already stored canonically need not be copied into general logs, run results, or audit JSON. Authorized review may show masked evidence and provenance.

The first implementation must define key rotation/reindexing and candidate invalidation when identifiers or rules change. A stale token must not keep creating new matches after the underlying contact field is removed.

## 10. Matching and conflict logic

1. Load the store's enabled domain and immutable policy version.
2. Read an affected batch of saved source customers with their source provenance.
3. Extract supported identifier claims; record unsupported/missing evidence without fabricating values.
4. Match indexed tokens only within the same store/domain and allowed source pairs.
5. Exclude self-pairs and existing accepted/rejected decisions whose evidence has not changed.
6. Detect shared identifiers, conflicting authoritative IDs, rejected member pairs, unexpected cluster growth, and ambiguous ownership.
7. Produce candidates with exact evidence and rule names, not invented confidence percentages.
8. Auto-accept only when a configured trusted rule permits it and the entire resulting cluster passes conflict checks. Otherwise keep the candidate for review.
9. Publish accepted links and a new identity revision atomically for the affected component.

Exact equality creates an explainable candidate; it does not guarantee a true identity match. Do not advertise 100% accuracy.

### Transitive links need their own checks

If A matches B by email and B matches C by phone, do not automatically assume A, B and C are safe to combine. Check trust and conflicts across the whole proposed cluster, including rejected pairs between any two members. A shared number must not merge a large household or a whole placeholder-email population.

Duplicate records inside one source may also be candidates when explicitly enabled. Import-row deduplication is not the same thing as linking two existing customer records.

## 11. Stable identities, review and undo

Each source customer initially remains unresolved or receives a singleton unified profile. Neither option counts as a successful cross-source resolution by itself; pick and document one behavior before building the schema.

For an accepted cluster merge, choose a deterministic survivor among existing unified IDs, retain an audit trail and a redirect/alias policy for retired unified IDs. Never silently change a source customer's original UUID.

Review mutations must require an authenticated owner/admin role, explicit reason, idempotency key, and expected revision. Regular permitted members may read store-scoped summaries. Reuse live store-user membership checking rather than trusting a request-body store or actor ID; signup/signin remains a separate project task.

Undo cannot just delete one row and assume the cluster is separated. Recompute connected components from remaining accepted evidence, honor rejection blocks, publish the split atomically, and mark downstream unified projections stale. Record new/retained IDs and redirects in the decision history. Test merge → undo → rerun so an automatically generated candidate cannot immediately recreate a rejected link.

If contact details change or evidence is withdrawn, mark affected claims/candidates stale and queue reconciliation. Do not silently claim that old evidence still verifies the current identity, and do not automatically delete historical source records.

## 12. Runs, performance and recovery

Use short HTTP requests to queue runs and read status. Do not repeat the former per-customer synchronous segmentation pattern.

- Store a durable run and work cursor; capture policy version and a clear input boundary/revision.
- Incremental imports should enqueue affected customers; initial backfill should use bounded keyset batches.
- Record changes arriving during a run for a subsequent reconciliation pass so a saved cursor does not permanently miss updates or late arrivals.
- Bulk-read source fields/identifiers and bulk-write candidates, claims, links and counters.
- Use indexed equality joins and bounded candidate/cluster limits; never full all-pairs scans.
- Serialize overlapping work per store/domain. Use leases, ownership tokens, heartbeat/expiry rules and atomic checkpoints for worker crash recovery.
- Limit concurrency to the database/worker capacity; do not open one connection per customer.
- Commit each coherent link/component change before advancing the cursor or displaying its count.
- Publish failed/partial status honestly and allow retries from the last committed checkpoint.
- Keep source ingestion and existing profile reads working if a resolution run fails.

Log run IDs, safe internal IDs, stage, counts, elapsed times and safe reason codes. Do not log normalized contact values, authentication tokens, secrets, or raw matching payloads.

## 13. Proposed backend API contracts

These routes are design targets only:

| Endpoint | Purpose |
|---|---|
| `GET /api/identity-resolution/summary/` | Counts, latest run, policy state and coverage for the authenticated store/domain |
| `GET /api/identity-resolution/configuration/` | Source compatibility and matching policy |
| `PATCH /api/identity-resolution/configuration/` | Owner/admin changes with version checking and audit |
| `POST /api/identity-resolution/runs/` | Queue a run; return 202 and run ID |
| `GET /api/identity-resolution/runs/` | Paginated run history |
| `GET /api/identity-resolution/runs/{id}/` | Durable progress, safe errors, policy version and counters |
| `GET /api/identity-resolution/candidates/` | Paginated review queue, with rule/evidence summaries |
| `POST /api/identity-resolution/candidates/{id}/decision/` | Accept/reject a candidate with reason and expected revision |
| `GET /api/identity-resolution/profiles/` | Paginated unified identities and source-member counts |
| `GET /api/identity-resolution/profiles/{id}/` | Members, allowed masked identifiers, provenance, revision and decisions |
| `POST /api/identity-resolution/profiles/{id}/split/` | Audited unlink/split request; potentially queued if component work is large |
| `GET /api/unified-customers/{id}/360/` | New unified Customer 360, preserving existing source-customer endpoint behavior |

Use bounded pagination, strict allowed filters, literal search escaping, same-store 404s, permission checks on every mutation, safe structured errors, and private/no-store caching for sensitive responses. GETs must not start runs or create links. Do not expose arbitrary SQL, executable user rules, or raw contact tokens.

## 14. What downstream apps would change later

### Ingestion

After approved source data is saved, record affected identity work durably. Existing source-specific upserts remain unchanged. Registering source compatibility/trust is explicit and does not come from an LLM guess.

### Customer 360

Add unified profile selectors that obtain canonical member IDs from accepted links and fetch history using store-scoped joins. Preserve the source of every record. A display name/contact preference must use a documented, versioned source-priority rule; show conflicting alternatives rather than erasing source values. Do not treat a preference rule as proof that an attribute is true.

Avoid join fan-out: one order/event must not be repeated because its source customer has several identifier claims. Existing `/api/customers/{id}/360/` keeps its source-record meaning; a new endpoint provides unified reads.

### KPIs

Expose source-customer counts separately from distinct unified-person counts. Identity resolution may change the denominator of an explicitly unified customer metric; it should not change observed revenue just because two customer profiles were linked. Transaction deduplication, reporting currencies and compatible revenue definitions need separate decisions. Keep the current source-based KPI contract until unified metric definitions are explicitly approved.

### Churn and CLV

Keep the final models and their source/customer eligibility rules intact. Initially, a unified profile can display saved predictions from its member records, labeled by source and computation/cutoff date. Do not average probabilities, sum CLV predictions, select the largest value as truth, or replace source-level membership with an unsupported unified prediction.

Using combined histories for a new prediction requires its own feature contract and model-validity decision. This feature does not authorize that change. Likewise, person-level audiences require a documented roll-up rule before being offered; do not silently change existing audience counts or scores.

## 15. Backend data needed for a future page like the screenshot

The UI would consume real counters, not decorative sample values:

| Display item | Proposed meaning |
|---|---|
| Total source profiles | Canonical customer records in the selected compatible domain |
| Unified profiles | Current unified identity nodes, explicitly stating whether singletons are included |
| Linked source profiles | Source records belonging to accepted multi-record identities |
| Cross-source resolved profiles | Source records in identities containing at least two distinct source namespaces |
| Unresolved / review pending | Records without adequate accepted links, and pending candidates, shown separately |
| Total events | Eligible saved canonical events in the selected store/domain and stated date scope |
| Events attached to unified profiles | Those events whose existing source customer link resolves to a current unified identity |
| Events attached to cross-source identities | A separately labeled subset; not automatically all singleton events |
| Identifier coverage | Records with usable evidence per identifier type, with an explicit denominator |
| Last run / duration / status | Stored run timestamps and state, not frontend timers presented as database facts |
| Source breakdown | Scanned, linked, pending and skipped counts for configured source namespaces |

A linked-profile count is not the number of people removed, and a candidate match count is not the number of accepted identities. If singletons exist, an event-to-singleton join must not be advertised as successful cross-source matching. Zero/missing denominators return unavailable, not an invented percentage.

The proposed page may eventually have Summary, Runs, Rules/Configuration and Review sections. Scheduling, probabilistic models and live warehouse connectors shown in commercial screenshots are outside the first version.

## 16. Implementation phases if this proposal is approved later

### Phase 1 — settle the product rules

Agree the source compatibility domain, review permissions, trusted identifier criteria, conservative email/phone normalization, stable-ID/singleton policy, conflict rules, rejection blocks and precise metric denominators. Confirm that cross-source matching has a real product need beyond unrelated demo datasets. Do not start by copying the reference dashboard.

### Phase 2 — introduce the optional app and schema

Create the app, reviewed migrations, constraints, RLS and indexes. Add disabled-by-default store/domain configuration. Test migrations against an isolated PostgreSQL schema and verify canonical source records remain untouched. Keep existing routes working without enabled resolution.

### Phase 3 — derive and index identifiers

Implement pure normalization functions, provenance, tokenization/key versioning, claim retirement and index backfill. Test invalid/ambiguous contact data and ensure no key/PII leakage. No linking yet.

### Phase 4 — generate candidates and apply decisions

Implement indexed candidate generation, cluster conflict checks, review actions, deterministic trusted rules, negative-match blocks, stable-ID publication and immutable audit. Start with review-first operation and controlled fixtures; do not enable broad auto-linking for unverified uploads.

### Phase 5 — add durable background execution

Implement incremental/outbox triggers, initial backfill, leases/heartbeat, cursor checkpoints, bounded bulk queries, retry and progress APIs. Benchmark query count and runtime on realistic linked fixtures. Verify concurrent runs and stale workers cannot publish conflicting links.

### Phase 6 — expose summary and unified reads

Implement paginated profiles, candidates, runs, evidence and unified Customer 360 selectors. Keep all history provenance and ensure no joins multiply records or money. Existing source-level views and KPI definitions stay compatible.

### Phase 7 — prove reversibility and operational readiness

Complete undo/split, evidence-change reconciliation, downstream stale flags, permissions, data-retention behavior, migrations and failure-recovery tests. Document safe operator commands and limitations before enabling it for a store.

### Phase 8 — optional frontend follow-up

Only after the backend contracts and real counts are verified, design the identity-resolution page and a switch between source and unified Customer 360. This document itself requests no frontend work and no navbar item.

## 17. Required validation before enabling the feature

- Cross-store records with identical email/phone never become candidates or links.
- Incompatible/unconfigured demo populations remain separate.
- Same source ID reimports are idempotent and do not become repeated people.
- Empty, placeholder, malformed and ambiguous identifiers do not auto-match.
- Unverified exact matches enter review unless the configured trust rule explicitly permits linking.
- Shared phone/email values and conflicting authoritative IDs do not cause cluster explosions.
- Transitive bridges check every resulting cluster and rejection block.
- Concurrent acceptance, stale review revisions, retries and worker restarts preserve one coherent current identity membership per source customer.
- Failed batch commits do not advance counters/cursors or expose half-published identity changes.
- Undo/split and changed/deleted contact claims invalidate stale evidence without deleting original histories.
- Unified reads preserve original order/event IDs, amounts, currency, dates and source attribution; totals do not multiply through joins.
- Current churn/CLV predictions, source-level audiences and all existing KPI APIs retain their contract.
- Coverage percentages state their denominator and distinguish missing evidence from failed matching.
- Migration rollback/disable paths preserve original customer data and keep existing profile routes usable.

## 18. Decision still to make

**Do not implement now.** The next decision is whether this feature is needed for the project's current delivery scope. If approved later, begin with conservative, store-scoped, reversible identity links and reviewable deterministic rules. Do not promise enterprise identity coverage for arbitrary CSV datasets.

This plan deliberately preserves the existing product's mapping, preprocessing, ingestion, two trained segment models, source-based analytics, and Customer 360 until specific integrations are approved.

## 19. Repository context and external visual sources

Repository files reviewed for this proposal:

- `backend/schema.sql`: current source-customer schema, contact fields and canonical relationships.
- `backend/apps/ingestion/compiler.py`: namespace qualification and deterministic source IDs.
- `backend/apps/ingestion/models.py`: registered sources, batches and durable ingestion jobs.
- `backend/apps/ingestion/authentication.py`: live store-user membership checking.
- `backend/apps/analytics/customer360/selectors.py`: current single-customer, store-scoped history reads.
- Existing segmentation, ingestion, frontend and Customer 360 implementation documentation in `docs/implementation/`.

External reference: [Hightouch's identity-resolution explanation and diagrams](https://hightouch.com/blog/what-is-identity-resolution). It explains linking identifiers into profiles and contrasts matching approaches; the restricted Customerly scope, proposed schema and implementation phases above are our own project-specific proposal. No Hightouch service integration or dependency is proposed.

The image URLs were located through web search and traced to the publisher's page rather than embedding a Google Images search-result URL. They are third-party explanatory references, not repository assets or a claim that the feature already exists.
