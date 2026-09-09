# Monthly literature discovery — design proposal only

Historical design: the deterministic command, private inbox and disabled systemd timer have since been implemented. Use the [short everyday guide](literature-quickstart.md) for the actual controls. The agent/model approach below remains a proposal; no scheduled searches have been activated.

## Starting point and boundaries

VPOD is a Django application with a relational approval workflow. `POST /api/submissions/` accepts publication suggestions through `SubmissionCreateSerializer`, creating a pending Reference if its identifier is new. It also creates a private SubmissionReceipt. The legacy DataSubmission table is not the destination. The public reference and observation collections expose approved records; review and editing use Django admin.

The supplied VPOD paper (Frazer et al., DOI [10.1093/gigascience/giae073](https://doi.org/10.1093/gigascience/giae073), methods, p. 3) describes tracking engine, search terms, date and the search-to-paper relationship in `litsearch` and `references`. The local `litsearch.csv` contains 34 heterogeneous search records, from 2018 through 2025: PubMed, EBSCO Host, Google Scholar, meta-analyses, reference chasing and incidental discoveries. Search 29 explicitly describes a future search; it is not evidence that the search was executed. The research repository README and VPOD 1.3 raw references/search files were inspected at commit `c2912f843b0dc520f7f8631d025d00c8c601fd6b`. They are historical provenance, not a complete current search strategy or this application's runtime environment.

The pipeline proposes publications and relevance categories. A relevant paper is not proof of a measurement method, an opsin sequence/phenotype pairing, a λmax or a CPD value. Every scientific measurement requires a curator to inspect the paper, its methods, conditions, units and exact provenance before manual entry and approval.

## Current payload and deduplication

This is an **accepted payload for the implemented endpoint**, shown for a local test instance. Do not run it against a deployed site as part of this task:

```http
POST /api/submissions/
Content-Type: application/json

{
  "submission_type": "PUBLICATION",
  "doi": "10.1093/gigascience/giae073",
  "year_of_publication": 2024,
  "relevance": "Opsin; Heterologous; computational inference",
  "notes": "Candidate for curator review; source evidence belongs in a review record.",
  "submitter_email": "curator@example.org"
}
```

`doi` is required and accepts a syntactic DOI or a validated HTTP(S) source URL; citation-only text is currently rejected. `year_of_publication`, `relevance`, `notes` and `submitter_email` are optional. No title, run ID, reviewer state or provider payload is currently accepted as a first-class discovery field. Supplying status, approval identity or source keys is rejected. A typical response is `201` with `submission_type`, `status: "PENDING"`, `reference_id`, `reference_status`, `relevance`, and `receipt_id`. `PENDING` describes the submitted suggestion; a reused reference can already be approved or rejected.

The serializer compares canonical identifiers across DOI case and DOI-URL variants, prefers approved then pending then rejected references, and uses the lowest refid within a status. It preserves duplicate legacy records rather than merging them. Existing references are reused without changing title, notes, year or approval. A rejected reference remains rejected. Each submission creates a private receipt, so repeated calls still create repeated receipts. There is no race-safe identifier uniqueness or idempotency key on this public route. It offers neither the identity, admission limits nor rejection-history controls needed for an unattended service.

**Decision: introduce a separate LiteratureCandidate review record**, linked to an existing Reference where possible. Pending Reference alone cannot adequately represent repeated discoveries, decision history, versions, assignment or accepted-but-uncurated papers. Do not overload the new private receipt into a discovery state machine. Never use submitted search relevance to populate `measurement_methods`.

## Scientific scope and queries

Include animal opsin sequence/function relationships, heterologous expression and spectral tuning (including mutants and chimeras), cell-level MSP/SCP, organ-level ERG, and spatial acuity across animal eye types. Computational inference and reviews are relevant but separate categories. Keep nonvisual opsins and microbial rhodopsins in an explicit scope-review queue rather than assuming all papers with “opsin” belong in the main comparative dataset.

Example Europe PMC queries (exact syntax must be validated against its `fields` endpoint during implementation):

```text
(opsin OR rhodopsin OR "visual pigment") AND
("spectral tuning" OR "spectral sensitivity" OR "lambda max" OR lambda_max OR λmax)

(opsin OR rhodopsin) AND (heterologous OR "in vitro" OR mutagenesis)

(microspectrophotometry OR "single cell photometry" OR "single-cell photometry")
AND (photoreceptor OR retina OR pigment)

(electroretinogram OR electroretinography OR ERG) AND
("spectral sensitivity" OR wavelength) AND (animal OR insect OR fish OR bird)

("visual acuity" OR "spatial resolution" OR "interommatidial angle" OR
"acceptance angle" OR "cycles per degree") AND
(compound OR eye OR photoreceptor OR optomotor OR optokinetic)
```

Do not require the word “animal” for every query: taxonomic terms and field-specific terminology are needed for recall. Include spelling, hyphenation and Unicode variants. Historical kinetics, nonvisual-opsin and localization searches justify additional query families; track their yield separately. Add forward/backward citation chasing around key syntheses in a bounded, separately labeled phase. Retain reviews as discovery leads without representing their compilations as newly measured observations.

Exclude clearly unrelated optics, computer-vision spatial-resolution articles without biological measurements, clinical treatment trials with only human clinical acuity endpoints, and papers without relevant organisms or vision questions. The human/clinical exclusion is a proposed initial scope choice for curator approval, not an assertion that such work lacks scientific value. Papers with inadequate abstracts go to “insufficient evidence”, not automatic permanent rejection. Use categories `OPSIN_SEQUENCE`, `HETEROLOGOUS`, `MSP_SCP`, `ERG`, `VISUAL_ACUITY`, `COMPUTATIONAL`, `REVIEW`, `SCOPE_UNCERTAIN`; allow multiple categories.

## Providers checked and operational constraints

Documentation was checked on September 9, 2026. Provider policies can change; honor response headers and recheck before deploying.

| Provider | Coverage and access | Pagination, limits and limitations |
|---|---|---|
| Crossref REST | Publisher/member-deposited DOI bibliographic metadata across disciplines; no registration for read access. Identify with a real maintainer `mailto`. Good for DOI validation and supplementary discovery. | `/works`, `query.bibliographic`, filters and cursor pagination. Current access documentation lists public 5 requests/s, concurrency 1; polite 10 requests/s, concurrency 3. Earlier announcements differ; runtime headers govern. Use 1 request/s and one concurrent request initially. Abstracts and methods are inconsistently deposited; DOI metadata cannot establish measurement methods. [Official REST guide](https://www.crossref.org/documentation/retrieve-metadata/rest-api/), [access and limits](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/), [filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/) |
| Europe PMC REST | Life-science literature, PubMed-linked content and preprints, with abstracts and licensed full text where available. Public search access; no key required for the proposed read queries. | `/search`, JSON, `pageSize`, `cursorMark`, and returned `nextCursorMark`; query fields available at `/fields`. No numeric service-wide rate guarantee was found in the checked REST overview; 1 request/s is our conservative configuration, not a provider promise. Its coverage is not identical to all zoological journals. [Official REST documentation](https://europepmc.org/RestfulWebService), [live fields endpoint](https://www.ebi.ac.uk/europepmc/webservices/rest/fields?format=json) |
| NCBI PubMed E-utilities | Optional independent search query/coverage check for biomedical literature. Use `ESearch` then batched `ESummary`/`EFetch`; identify tool/email. | Limit 3 requests/s per IP without API key, 10/s by default with key; shared NAT traffic matters. Use history/batching and incremental date queries; PubMed ESearch returns only the first 10,000 matches, so subdivide large windows or use documented EDirect handling. PubMed is not comprehensive for comparative visual ecology. [Official usage guide](https://www.ncbi.nlm.nih.gov/books/NBK25497/), [ESearch details](https://www.ncbi.nlm.nih.gov/books/NBK25499/) |
| DataCite REST | DOI resolution for datasets, theses and other registered objects; supplementary evidence, not a substitute for article discovery. Read public/findable records without credentials. | `/dois/{doi}` returns JSON:API metadata. Current limits: unidentified 500/5 min/IP; identified 1,000/5 min/IP; authenticated 3,000/5 min/IP. Use one request/s with caching. Do not use member write credentials. [Official API guide](https://support.datacite.org/docs/api), [rate limits](https://support.datacite.org/docs/rate-limit) |

Historical Google Scholar and EBSCO searches remain useful for manual coverage checks; no supported unrestricted API for those services is assumed or promised here. Do not scrape publisher paywalls or automatically download subscription-only full text. Retain links and license information; search metadata and abstracts may have reuse restrictions.

## State, identity and persistence — proposed schema/API changes

Add `DiscoveryRun` (UUID, query/config version, started/completed time, provider checkpoints, counters, failures and budgets) and `LiteratureCandidate` (stable internal UUID, canonical DOI or fallback identity, state, linked Reference, assigned curator, data-curation state). Add a `DiscoveryEvidence` child row for each provider/query/version encounter; repeated discoveries append evidence rather than recreating candidates. Store original provider identifiers and raw normalized metadata separately from curator edits.

Recommended candidate states: `DISCOVERED → NEEDS_REVIEW → ACCEPTED | REJECTED | DEFERRED`. Acceptance can create/reuse a pending Reference; a curator explicitly approves its bibliographic fields in admin. Separately track `NOT_ASSESSED → NO_DATA_NEEDED | DATA_ENTRY_NEEDED → IN_PROGRESS → CURATED`. A reviewed review article can be accepted with no data entry. Approval of a publication never approves measurements. Maintain append-only review events with reviewer, timestamp, previous/new state and rejection/defer reason. Avoid a batch “approve all scientific data” action.

Maintain an identifier table with unique `(scheme, normalized_value)` identities across approved, pending and rejected candidates/references. Resolve duplicate legacy References to a non-destructive identity group; keep every refid and foreign key. DOI case/URL normalization is deterministic. Citation fallback uses normalized title, first author, year and venue; weak or conflicting matches require review. Do not merge solely on fuzzy titles. DOI absence does not imply a different paper.

Track preprint and version-of-record as linked manifestations (`is_preprint_of`, `is_version_of`), preserving both provider IDs and DOI evidence. Never silently substitute a preprint DOI for the journal DOI. A rejected candidate remains discoverable in the identity index, with its reviewer decision retained. New metadata merely appends evidence. Reconsider only after an explicit curator action, or flag a materially new published version for reconsideration when an approved policy permits it; even then preserve the previous decision and do not automatically reopen.

Proposed endpoint (does **not** exist):

```http
POST /api/discovery-candidates/
Authorization: Bearer <scoped-service-credential>
Idempotency-Key: <run-uuid>:<provider>:<provider-id>
Content-Type: application/json

{
  "run_id": "<uuid>",
  "provider": "europe_pmc",
  "provider_id": "MED:<pmid>",
  "doi": "10.1093/gigascience/giae073",
  "title": "Discovering genotype–phenotype relationships with machine learning and the Visual Physiology Opsin Database (VPOD)",
  "query_id": "opsin_spectral_v1",
  "discovered_at": "2026-10-01T09:00:00Z",
  "evidence_links": ["https://doi.org/10.1093/gigascience/giae073"],
  "relevance_categories": ["HETEROLOGOUS", "COMPUTATIONAL"],
  "rationale": "Candidate: title and abstract discuss opsin genotype–phenotype prediction.",
  "classification_status": "candidate",
  "model": null,
  "prompt_version": null
}
```

Do not quietly add those fields to `/api/submissions/` and assume they are validated. Implement a dedicated serializer with explicit sizes, enums, provider/link allowlists, date validation, idempotency-key uniqueness and transaction-safe upserts. The service principal may create candidate/evidence records and read minimal identity/decision information. It cannot approve, update or delete References or observations, read private submitter contacts, or impersonate reviewers. Use TLS, expiring/rotatable scoped credentials in a service environment file or GitHub Actions secrets; redact authorization headers from logs. A new endpoint needs throttling, request-size limits and replay/idempotency protection. If a management command writes locally, use the same domain service and validation, with a least-privilege database role and append-only audit events.

Admin additions: candidate queue filtered by state/category/date, evidence side panel, duplicate/manifestation links, explicit accept/reject/defer actions, required rejection reason, assignments, a link to Reference editing and a separate accepted-but-uncurated queue. Preserve contacts in private receipts and discovery service identity in audit records; neither belongs in public exports.

## Deterministic approach

Use tested query families and inspect provider fields deterministically. Rank by explicit evidence tokens and metadata availability; title and abstract terms are suggestions, not confirmed experimental methods. Maintain positive terms plus narrowly scoped exclusions; log which rule fired. Version queries and thresholds. A rule that excludes “computer vision” should not remove a paper on biological vision that happens to compare computational models.

```text
acquire single-run lock; create run(config_hash, code_revision)
for provider and query:
    start = last_successful_checkpoint - 90 days  # proposed indexing overlap
    end = fixed UTC run-start timestamp
    query provider's indexing/entry date window where supported
    otherwise search publication window plus quarterly backfill
    resume page cursor within this run; validate response and schema
    for item on page:
        persist evidence(provider_id, query, run, retrieved_at, links)
        normalize DOI; retain original identifiers and citation
        resolve identity against approved/pending/rejected/version groups
        if identity exists: append evidence; preserve decisions; continue
        evaluate inclusion/exclusion rules using available evidence
        if clearly out of scope: retain disposition for evaluation
        else: upsert NEEDS_REVIEW candidate using idempotency key
        never write a measurement or publish a Reference
    commit page evidence and cursor together after successful processing
    after all pages succeed, advance this query/provider checkpoint to end
record per-provider failures, counts, time, budgets and review backlog
release lock; emit summary; alert only on configured operational failures
```

Use Crossref `from-index-date`/`until-index-date` (or deposit-update dates) for delayed deposits. A live Europe PMC `/fields?format=json` request returned `FIRST_IDATE`, `INDEX_DATE`, `UPDATE_DATE` and `FIRST_PDATE`; use `FIRST_IDATE:[start TO end]` for first-indexed windows and separately test `UPDATE_DATE` semantics for refreshes before implementation. A live one-result `FIRST_IDATE` query with `cursorMark=*` returned HTTP 200 and a `nextCursorMark`, confirming the basic incremental-query/pagination shape. No discovered candidates were submitted. The web search-syntax page returned 403 during this review; the live fields schema and REST documentation were accessible. `FIRST_PDATE` is a publication date, not an indexing timestamp. Freeze the end time for each run. Keep 90-day overlap as an initial tunable policy, not a guarantee of complete backfill. Run a quarterly wider-window audit and a yearly historical query sample. Preserve a failed provider's checkpoint while successful providers advance independently. Restart expired cursors at that provider/query's window start; identity keys make replay harmless. Detect cursor loops and missing/truncated pages.

All requests have connect/read timeouts, bounded retries, exponential backoff with jitter, `Retry-After`, per-provider throttles, response caching and size caps. A long `Retry-After` defers work rather than retrying too early. Persist failed items and unprocessed pages. Never label a partial run complete. Do not trust retrieved URLs as arbitrary download targets; provider-only API hosts and approved article hosts prevent SSRF.

Benefits: inexpensive operations, transparent decisions, easy reproducibility and mock tests. Limitations: vocabulary drift, missing abstracts, poor recall for unanticipated methods/species, difficulty separating reviews and measurement evidence. Curators still inspect candidates.

## Agent-assisted approach

Use the same deterministic collection, persistence, identity and submission layers. A model helps **after retrieval and initial deduplication** by suggesting relevance categories, explaining ambiguous terminology, and identifying which evidence a curator should inspect. It can help propose new query terms offline; a human approves query changes before scheduled use. It never resolves DOI identity based on language plausibility alone and never writes verified measurement metadata.

Allowed evidence: supplied title, abstract, provider bibliographic fields, source links, and at most two explicitly permitted open-access passages with document location. No arbitrary browsing, shell, secrets, email or production write tools. Omit private contacts and credentials from model input. Retrieved literature is untrusted data: it cannot alter the system/task instructions, request tool use, reveal secrets or authorize actions. Reject outputs that contain tool commands or new external destinations. Do not rely on a model to enforce these boundaries.

Proposed strict output schema:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["candidate_id", "categories", "decision", "evidence", "uncertainties", "rationale"],
  "properties": {
    "candidate_id": {"type": "string"},
    "categories": {"type": "array", "items": {"enum": ["OPSIN_SEQUENCE", "HETEROLOGOUS", "MSP_SCP", "ERG", "VISUAL_ACUITY", "COMPUTATIONAL", "REVIEW", "SCOPE_UNCERTAIN"]}},
    "decision": {"enum": ["suggest_review", "likely_out_of_scope", "insufficient_evidence"]},
    "evidence": {"type": "array", "maxItems": 5, "items": {
      "type": "object", "additionalProperties": false,
      "required": ["source_id", "location", "quote"],
      "properties": {"source_id": {"type": "string"}, "location": {"type": "string"}, "quote": {"type": "string", "maxLength": 300}}
    }},
    "uncertainties": {"type": "array", "items": {"type": "string", "maxLength": 300}},
    "rationale": {"type": "string", "maxLength": 1000}
  }
}
```

The wrapper checks candidate IDs against input, verifies each cited quote against the provided passage, limits category count and refuses unknown fields, especially DOIs/measurements/status changes. Unsupported classifications remain unverified suggestions or are discarded. A numerical confidence score is deliberately omitted; it would not be calibrated without evaluation. Missing abstracts or contradictory evidence yield `insufficient_evidence`. `likely_out_of_scope` is a triage suggestion, not a durable human rejection. Sample these suggestions to measure missed positives.

Store model/provider ID, exact version if available, prompt and schema versions, input/evidence hashes, token usage, latency and validation failures. Initial policy limits: at most 100 candidates/run, 2 passage reads/candidate, 4,000 input and 600 output tokens/candidate, no recursive agents, one retry on invalid output, configurable currency budget enforced by the wrapper. These are proposed ceilings, not cost forecasts. Price the selected provider/model at pilot time and record actual billed usage. If budgets are exhausted or the model fails, preserve deterministic candidates and mark model triage incomplete; never discard the run.

Benefits: better explanations across varied terminology, potentially less curator time per abstract, query-gap suggestions. Costs: inference charges, prompt/schema maintenance, nondeterminism, hallucinated relevance, adversarial text and evaluation overhead. It cannot replace an expert's source inspection.

## Scheduling, deployment and operations

Start with a **management command** `discover_literature` (proposed) on the application host or an isolated worker host, invoking the shared candidate service. Suggested schedule: first day of each month at **02:00 America/Los_Angeles**, recorded in UTC; local 02:00 on the first is unambiguous under current US DST rules. Use `systemd` timer with `Persistent=true`, a dedicated OS user, and a lock. Alternative cron needs explicit timezone support, missed-run handling and lock management. GitHub Actions runs in UTC: schedule one UTC trigger and gate inside the command by local month, or consciously choose a fixed UTC time; do not imply that a static cron string follows Los Angeles DST. Pin dependencies and code revision, store outputs as restricted artifacts, and never put the working database or service credentials in public CI artifacts.

No Celery/Redis is initially necessary. Add a queue only when bounded monthly work exceeds the available execution window, multiple curators need interactive retrieval, reliable concurrent provider tasks become necessary, or hosting constraints prevent long commands. Keep worker jobs idempotent and separate model/search credentials from moderation privileges.

Example **proposed** configuration is in `discovery.example.json`; it is documentation only. Keep private deployment paths, provider contact, API keys and service tokens in environment variables, never this committed file. A server timer example below is intentionally non-installable until the command and service exist:

```ini
# PROPOSAL: /etc/systemd/system/vpod-discovery.timer (do not install yet)
[Timer]
OnCalendar=*-*-01 02:00:00 America/Los_Angeles
Persistent=true
Unit=vpod-discovery.service
```

The service would use deployment-supplied `WorkingDirectory`, virtualenv path, `User`, and `EnvironmentFile`, and run the proposed command with `--config` and an explicit selected database/API target. Begin with output-only mode, then enable candidate submission only after pilot approval. A failed scheduler or missing secret must not affect VPOD page requests.

Monitoring: run ID/status, provider/query counts, pages, retry/deferred counts, cache hits, normalized/duplicate/rejected matches, new review candidates, model usage/cost, invalid outputs, backlog size/age and curator decisions. Alert on missed monthly completion, repeated provider failures, abnormal zero yield, identity collisions, excessive duplicates or budget exhaustion. Produce a private JSON run artifact and a curator summary; do not automatically message people without a separately configured and approved notification policy. Keep logs free of credentials and personal contact details.

Costs are hosting time, storage, optional provider subscriptions, model usage and curator labor. No numerical accuracy or dollar-cost claim is justified before a pilot. Cache metadata and process deltas to control costs; review API/dependency versions quarterly and preserve query/config revisions for reproducibility. Limit the initial monthly review queue to 40 new candidates, with remaining candidates queued in chronological order and a visible backlog—never silently thrown away. A curator can adjust the cap after observing workload.

## Recommendation, phases and pilot gates

Recommend a **hybrid with deterministic foundations**, beginning without a model. Implement the candidate identity/review ledger first; an optional evidence-constrained model can then assist triage without publication rights.

1. Curators approve scope/query families and label a benchmark sample spanning opsin, heterologous, MSP/SCP, ERG, acuity, computational and irrelevant articles. Include known legacy papers absent from one provider and ambiguous/rejected cases. Freeze the labels and split tuning from evaluation.
2. Add candidate/run/evidence/decision models, race-safe identity groups, scoped service authentication, idempotent API/domain service and admin review/data-entry queues. Test duplicate legacy references and preprint transitions. No live scheduler yet.
3. Implement provider adapters, checkpoints, cache/retry/rate-limit controls and a deterministic output-only management command. Compare searches with manual PubMed/Scholar/reference checks over a defined recent window.
4. Run two manually triggered monthly-window pilots and review workload. If deterministic results are acceptable, enable only pending-candidate submission under an approved service identity. A human separately enables the timer.
5. Evaluate model triage on the same held-out sample. Adopt it only if observed curator time or review yield improves without unacceptable false negatives; retain deterministic fallback.

Proposed measurable pilot gates (targets to agree with curators, not achieved results):

- Zero reference/measurement auto-publications; every approval/rejection records a human identity and reason.
- Zero new candidate identities on an identical rerun; repeated evidence is idempotent; 100% of tested rejected identities stay rejected.
- Interrupted/paginated provider fixtures resume without lost candidates; failed providers never advance checkpoints.
- Every reviewed suggestion has provider ID, query, run, discovery date and valid evidence links; all model quotes trace to supplied passages.
- On a manually labeled held-out sample of at least 100 candidates, report precision, sample recall, confusion by category and uncertainty intervals. Initial targets: ≥80% precision for “review” suggestions and ≥90% recall within the labeled pool. These do not measure recall over all literature; separately report provider coverage against known relevant papers.
- Review no more than the agreed monthly cap; report median review time and backlog age. Compare deterministic and assisted arms on the same cases, with disagreements adjudicated by a biologist.
- No private contacts in public metadata or reports; malformed requests, prompt injection and unauthorized approve/update/delete attempts fail in automated tests.
- Report actual run cost and duration within the configured budget and deployment window; partial or degraded runs are clearly labeled.
