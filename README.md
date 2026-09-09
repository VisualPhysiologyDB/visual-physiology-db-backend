# Visual Physiology Opsin Database (VPOD)

VPOD connects animal opsin sequences, experimental and computational spectral measurements, visual acuity observations, and their original references. Public users explore and export approved data; curators review pending contributions in Django admin.

Cite **Frazer SA, Baghbanzadeh M, Rahnavard A, Crandall KA, Oakley TH. Discovering genotype–phenotype relationships with machine learning and the Visual Physiology Opsin Database (VPOD). GigaScience. 2024;13:giae073. [doi:10.1093/gigascience/giae073](https://doi.org/10.1093/gigascience/giae073)**, the source publications for measurements, and the dataset version/retrieval date used. The supplied `giae073.pdf` documents the research background. The [research/ML repository](https://github.com/VisualPhysiologyDB/visual-physiology-opsin-db/) is a separate environment: do not install its ML dependency stack into this web application.

## Architecture and layout

- `core/models.py`, `serializers.py`, `views.py`, `admin.py`: Django models, DRF API, validation, publication permissions and curator interface.
- `core/migrations/`: schema and privacy migration files through `0011`. Review and commit migration files during development; apply those files during deployment.
- `core/bibliography.py`, `metadata_recovery.py`: conservative classification, partial dates, method vocabulary and cached external metadata access.
- `core/management/commands/`: curated CSV imports, enrichment and existing compendium/MNM commands.
- `templates/index.html`: active vanilla-JavaScript/Tailwind frontend; `static/core/explorer.js` handles DOM and interactions, `vpod-data.js` provides histogram/export functions. Lucide and Chart.js remain the existing CDN-based dependencies. CDN unavailability leaves readable tables and text histogram counts; a production asset-vendoring decision remains with the deployment maintainer.
- `vpod_backend/settings.py`, `urls.py`: environment settings and routes. `working_copy`, `copy` and `templates/old/` files are inactive.
- `core/test_vpod.py`, `tests/`: focused Python and browser verification. `core/tests.py` is an older ignored placeholder.
- `reports/`: enrichment artifact, curator report, row reconciliation and verification results. `docs/`: scientific policies and **unimplemented** follow-up proposals.

No lookup occurs during a migration, an API request or a page render. The site works with missing bibliography fields and external-service failures.

## Development setup

The verified baseline is **CPython 3.12.1 and Django 5.2.17**, with the direct application dependencies pinned in `requirements.txt`. Use Python 3.12 for this tested setup; other interpreters/database backends require their own validation. DRF, django-filter, python-dotenv, WhiteNoise, pandas (existing compendium/MNM importers) and requests are the web application's dependencies. PostgreSQL support is optional in `requirements-postgres.txt`; browser tooling is in `requirements-dev.txt`.

```bash
cd /path/to/visual-physiology-db-backend
python3.12 -m venv .venv
source .venv/bin/activate
# Disable inherited pip target/config settings that can defeat a virtualenv.
env -u PIP_TARGET PIP_CONFIG_FILE=/dev/null python -m pip install -r requirements.txt
# Preserve any existing .env; inspect and edit it locally, never commit secrets.
test -e .env || cp .env.example .env
```

Set a separate development database explicitly. The examples below never use the repository's potentially live `db.sqlite3`:

```bash
export DEBUG=True
export VPOD_DATABASE=sqlite
export VPOD_SQLITE_PATH=/tmp/vpod-development.sqlite3
export VPOD_STATIC_ROOT=/tmp/vpod-static
export VPOD_LOG_PATH=/tmp/vpod-django-errors.log
python manage.py check
python manage.py showmigrations
python manage.py migrate --plan
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Choose a random local `SECRET_KEY` (for example with `python -c 'import secrets; print(secrets.token_urlsafe(50))'`) and keep it in the ignored `.env`. `.env.example` is secret-free. Environment variables already set by the process take precedence over dotenv values. Do not replace an existing deployment `.env` with the example. Open `http://127.0.0.1:8000/` and `/admin/`.

### Selecting a database

Without overrides, `default` remains SQLite at `BASE_DIR/db.sqlite3`. There is also a `vpod_db` PostgreSQL alias. **An alias does not route application queries**: models, API queries and these import/enrichment commands use `default`. `VPOD_DATABASE=postgres` explicitly makes `default` a copy of the PostgreSQL configuration. Install `requirements-postgres.txt` and supply `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` for a provisioned database and role. Running `migrate --database=vpod_db` alone would migrate that alias without making it serve the site.

`ALLOWED_HOSTS` accepts comma-separated hostnames, with surrounding whitespace removed. If it is absent or blank, the established `visphys.eemb.ucsb.edu`, `localhost`, and `127.0.0.1` hosts remain allowed. An explicit nonblank value replaces that list; include every hostname used by your deployment, without a scheme, path or port.

The deployed database cannot be determined from repository settings alone; inspect the actual service environment and any deployment-specific settings module. This task did not connect to PostgreSQL or a deployed service. For staging, use a separate database/role. Never run imports or metadata recovery while unknowingly targeting production.

## API and moderation

| Route | Public behavior |
|---|---|
| `/api/references/` | Approved references; DOI, source link, title, partial dates, compatible year, methods, raw citation/MOM and scientific notes. |
| `/api/opsins/` | Approved sequences and approved nested reference metadata. |
| `/api/heterologous/` | Approved heterologous/inferred measurements, with explicit inference flags. |
| `/api/scp/` | Approved single-cell measurements. |
| `/api/visual-acuity/` | Approved acuity observations, all measurements, source keys, raw columns and quality flags. |
| `/api/submissions/` | Public POST for pending relational contributions or publication suggestions; legacy inbox actions require staff. |

Public collection/detail routes are read-only for everyone, including staff. Curators edit and approve through admin. Pending and rejected records are excluded from collections even for logged-in submitters and staff browsing the public website. Nested pending/rejected reference or opsin details are also hidden. A curator reviews/approves a new reference and its related observation separately; approving one does not automatically publish the other. An approved observation with an unapproved reference remains visible with “Reference unavailable”.

```bash
curl 'http://127.0.0.1:8000/api/references/?search=microspectrophotometry'
curl 'http://127.0.0.1:8000/api/references/?year_of_publication=2020'
curl 'http://127.0.0.1:8000/api/visual-acuity/?search=Apis&cpd_min=0.1&cpd_max=2'
curl 'http://127.0.0.1:8000/api/visual-acuity/?cpd_missing=true'
```

On an isolated local instance, a valid example contribution is:

```bash
curl -X POST http://127.0.0.1:8000/api/submissions/ \
  -H 'Content-Type: application/json' \
  -d '{"submission_type":"DATA","data_type":"Visual Acuity","doi":"10.1234/example","genus":"Apis","species":"mellifera","cpd":0.5,"eye_type":"apposition","body_length_cm":1.2,"notes":"Example only: replace with a documented observation and measurement protocol."}'
```

This is a syntax example with a placeholder DOI, not scientific data. Use `submission_type: "PUBLICATION"` with `doi`, optional `year_of_publication`, `relevance`, `notes` and `submitter_email` for suggestions. Existing Heterologous and SCP submissions use `DATA` and their respective `data_type`, genus, species and `lambda_max`. The existing 300–800 nm database range and legacy zero sentinel are now consistent with serializer/frontend validation for those types. Acuity has no sequence or λmax requirement.

New observations/references are pending. Canonical DOI or source URL matching reuses approved/pending/rejected references without modifying them. Existing opsins are reused only if all supplied data agree with an approved record. Proposed changes do not fill published fields. Private submitter email and suggestion provenance live in **SubmissionReceipt**, available only through staff admin. Migration `0009` widens the legacy DOI column to text so raw citations longer than 255 characters can be preserved before normalization, including on PostgreSQL. Migration `0008` moves historical “Submitter email:” note lines into private receipts; it intentionally does not restore them to public notes when reversed. User-supplied scientific notes should not contain personal information.

The current public suggestion endpoint has no service-token/idempotency contract and does not provide a production discovery ledger. See the separate design before building unattended clients.

## CSV import order and preservation

Keep original files unchanged. Commands decode UTF-8 with BOM support. Reference IDs and source keys are never renumbered or merged. Source material is data, never executable instructions.

1. `references.csv` (597 rows) is processed first by `import_csvs`, followed by 2,986 opsins, 1,397 heterologous rows and 1,570 curated SCP rows. Source helpers ensure six compendium-publication identities as needed; some already exist.
2. `visual_acuity.csv` is its own import (549 rows); the supplied filename is not `visual_acuit.csv`.
3. The existing in-vivo compendium and optional MNM importers can then be run against **an isolated target**, if those additional datasets are needed. Their historical update policies differ; inspect their reports and backups before using them elsewhere.

```bash
python manage.py import_csvs . --report /tmp/vpod-legacy-import.json
python manage.py import_visual_acuity visual_acuity.csv --report /tmp/vpod-acuity-import.json
# Optional datasets, after reviewing their source mapping:
python manage.py import_scp_compendium VPOD_in_vivo_1.0_2026-03-13_12-54-05.csv
python manage.py import_mnm_data mnm_on_vpod_in_vivo_results_fully_filtered.csv \
  --compendium-csv VPOD_in_vivo_1.0_2026-03-13_12-54-05.csv
```

The MNM command uses `--compendium-csv` to map source references; its default is the supplied in-vivo filename in the application root. Do not use these commands as an automatic production update step.

`import_csvs` creates missing legacy records approved, preserves all existing records and moderation decisions, and fills original reference source provenance/methods once when identifiers agree. It no longer replaces enriched fields with blank CSV values. Invalid year text is retained in the source snapshot and reported; it is not a publication year. Invalid assay rows/unresolved links are reported for curation. It does not resolve changed CSV values into existing curated rows automatically. Explicit PostgreSQL primary-key sequences are reset after legacy imports.

`import_visual_acuity` uses `(source_dataset='visual_acuity_legacy', source_record_id=acuID)`. It keeps distinct observations of the same species, every original column in `source_data`, and a normalized `import_baseline`. On reimport, it updates a field only if the current value still equals its prior imported value. Curator edits are preserved and reported as conflicts. It never changes an existing approval decision. Duplicate/missing source IDs are unresolved outcomes; invalid measurements become null with raw values retained and flags reported. A valid legacy row with no CPD remains importable. `--dry-run` rolls back writes and produces a report. All rows reconcile to imported, updated, unchanged or explicitly unresolved.

The remaining existing compendium/MNM commands use `update_or_create` and can overwrite existing values/statuses; this change does not promise curator protection for those historical commands. They are optional data integration tools, not the normal reimport path. The source-publication helper itself now preserves existing approval decisions.

## Bibliographic enrichment and reproducible replay

For the concrete review/correction workflow, runnable examples, current isolated database and the MSP duplicate correction, see [metadata review guide](docs/metadata-review.md). Open [the standalone review page](reports/reference-review.html) directly in a browser, or use [the spreadsheet worklist](reports/reference-review.csv). Both are snapshots; edits are saved in Django admin, not in these files.

Scientific policy is in [docs/data-policies.md](docs/data-policies.md). The delivered full-inventory artifact is [reports/reference-enrichment.json](reports/reference-enrichment.json); its inventory, accepted field changes, candidates, conflicts, evidence URLs, retrieval times and before/after completeness are reviewable. [reports/curator-review.json](reports/curator-review.json) is the compact unresolved/conflict/duplicate worklist. The JSON artifacts are not migrations and are not automatically loaded by the website.

Preview a new recovery against an isolated database:

```bash
python manage.py enrich_references --dry-run \
  --output /tmp/vpod-enrichment-review.json --cache /tmp/vpod-metadata-cache
# After reviewing that concrete artifact, apply the same corrections without network:
python manage.py enrich_references --from-artifact /tmp/vpod-enrichment-review.json \
  --apply --output /tmp/vpod-enrichment-applied.json
```

To reproduce this task's accepted corrections on a **fresh empty development database**, first migrate, seed the exact delivered legacy reference inventory (including database-only references and source-helper IDs), then import the CSVs. Seeding preserves IDs rather than guessing whether a fresh helper-created ID is the same publication:

```bash
python manage.py migrate
python manage.py enrich_references --from-artifact reports/reference-enrichment.json \
  --seed-inventory --apply --output /tmp/vpod-replay.json
python manage.py import_csvs . --report /tmp/vpod-import-after-replay.json
python manage.py import_visual_acuity visual_acuity.csv --report /tmp/vpod-acuity-after-replay.json
```

For an **existing database copy**, omit `--seed-inventory` and inspect `replay_conflicts`. Replay uses reference ID, original field value and DOI/raw-citation identity guards, and protects fields edited in admin. A mismatch is reported, not overwritten. A fresh dry-run with `--seed-inventory` does not create missing IDs; to preview such a seed, apply it to a disposable fresh database and inspect the result. Do not seed IDs into an unrelated populated database. The artifact includes bibliographic inventory/status/source snapshots, not submitter/user tables or scientific measurements.

An enrichment run always iterates the entire ORM reference inventory, not only a CSV list. `--apply` explicitly permits changes; without it the database is unchanged. `--cache` stores responses by request hash; each reference commits its audits and every 25 records checkpoints the output artifact. Re-running resumes from cached responses. Use `--retry-failures` to retry cached provider failures; `--offline` forbids new HTTP requests. A new cache directory is the explicit way to refresh successful metadata. Default pacing is one serial request approximately every 1.05 seconds, with 5-second connect/20-second read timeouts and two retries; timeout/retry options are bounded. `Retry-After` is honored or the item deferred. Set `METADATA_MAILTO` to a real contact for identified API access. No service credentials are required for public Crossref/DataCite lookups.

Field-level applied/conflicting/candidate decisions are stored in `ReferenceMetadataAudit`. Administrator edits also create immutable audit rows. Nonempty curated bibliographic fields are protected; conflicts require review. No duplicates are merged and no DOI uniqueness constraint is added. Successful lookup of a supplied DOI verifies its registry metadata, not whether every linked experimental row was cited correctly. Source warnings such as `DOI WRONG` block automatic acceptance. Unresolved citation/title-only records keep their exact raw citation and candidate evidence; fuzzy title similarity alone never confirms identity. Raw URLs are retained as URLs rather than transformed into DOI links.

## Acuity fields, dates and histograms

| Source | Stored field | Meaning / units |
|---|---|---|
| `acuID` | `source_record_id` | Original row ID; distinct from database `acuid`. |
| `Genus`, `Species` | `genus`, `species` plus `source_data` | Full unambiguous binomials can be split when genus is blank; ambiguous or nonstandard names are flagged, never silently corrected. |
| `Eye Type` | `eye_type` | Supplied apposition/superposition/`NS` label; `NS` is retained without inventing its expansion. |
| `BL (cm)` | `body_length_cm` | Body length in cm. Exact anatomical length convention is not documented per row. |
| `Δϕ (deg)` | `interommatidial_angle_deg` | Angular separation of adjacent optical axes, in degrees; row-specific axis/estimation protocol may be unknown. |
| `Δρ (deg)` | `acceptance_angle_deg` | Photoreceptor acceptance angle in degrees; width conventions/methods can differ. |
| `CPD` | `cpd` | Spatial acuity, cycles per degree; original values retained. |
| `Lens Diameter (mm)` | `lens_diameter_mm` | Source lens diameter in mm; not assumed equivalent to facet diameter. |
| `FellerRefID` | `feller_ref_id` | Retained secondary source-reference key; not a VPOD foreign key. |
| `refid` | `reference` | VPOD reference foreign key. |
| `Notes` | `notes` | Original notes; every source column also remains in `source_data`. |

See [data policies and scientific evidence](docs/data-policies.md) for definitions and limits. Public acuity contributions require positive finite CPD, genus/species and a source identifier. Optional numeric measurements must be positive finite if present. Legacy missing/invalid measurements remain null and explicitly flagged. The database has positive finite upper/lower constraints and requires CPD for records without a source dataset. SQLite converts NaN to NULL at the driver boundary; model/serializer validation rejects nonfinite values before that boundary. Do not bypass validation with raw SQL.

Publication dates are ISO partial strings: `YYYY`, `YYYY-MM`, or `YYYY-MM-DD`. Print date takes precedence over online date, followed by generic published/issued dates; the separate online and print fields preserve both. A year is never converted to January 1. Existing `year_of_publication` is retained for compatibility, with conflicts reported; the histogram uses it first, then the selected date's year. A year/date disagreement is visible in reference details.

The reference histogram counts **reference records**, including remaining duplicate publications; multiple methods do not multiply counts. Unknown years are excluded/reported, and all intervening years appear, including zero counts. The CPD histogram uses variable-width categorical intervals with boundaries `0, .05, .1, .2, .5, 1, 2, 5, 10, 20, 50, 100, 200, +∞`. Positive values are lower-inclusive/upper-exclusive. Bars are counts, not density and not a logarithmic axis. Displayed ranges run from the first to the last populated bin of the filtered data; empty bins within that range remain visible. Wavelength charts use the same rule for their 10 nm bins. It reports plotted/excluded observations. Both charts and exports use the complete filtered public table population; the client follows every API page if pagination is later enabled. Copy produces the same CSV as download, retaining full text and quoting; spreadsheet-formula-leading strings receive a protective apostrophe, while JSON API values remain unchanged.

## Tests and verification

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core
node tests/data_checks.js  # Node 18+; browser toolchain includes a compatible Node binary
# Optional browser checks: isolated localhost database only; creates one pending test submission.
env -u PIP_TARGET PIP_CONFIG_FILE=/dev/null python -m pip install -r requirements-dev.txt
python -m playwright install chromium
VPOD_BROWSER_URL=http://127.0.0.1:8000 python tests/browser_checks.py
```

Django's test runner creates a separate test database; metadata HTTP services are mocked in automated tests. Browser tests exercise actual local API data plus adversarial/paginated fixtures and actual Chart.js rendering. The original [verification report](reports/verification.md) records that implementation stage; subsequent fixes and discovery checks have separate reports. No superuser was created on your live database. PostgreSQL and deployment rollback remain untested. Literature discovery is implemented with a disabled schedule; no model service is used.

## Production operations — deployment maintainer only

Determine the active database/settings/service configuration before any change. Take a consistent backup of the selected database, configuration and current application revision; rehearse restoration on a separate target. Use SQLite's online backup API (or an offline file copy with the service stopped), not an arbitrary copy of a busy WAL database. PostgreSQL deployments need their own `pg_dump`/restore or managed backup procedure, permissions and retention.

Review committed migration SQL and plan (`showmigrations`, `migrate --plan`, `sqlmigrate core 0007`) on a restored staging copy. Existing migrations `0005` and `0006` add an SCP species/wavelength constraint before `0007` removes it; `0010` replaces it with the reference-aware rule. The inspected database passed those historical migrations. Migration `0010` restores uniqueness per normalized species, measured wavelength and reference, while retaining existing source duplicates under `duplicate_of`; different publications remain separate. If another old database contains duplicates or invalid ranges that prevent `0005`, stop and design a reviewed non-destructive migration path; do not delete observations, fake migrations or flush the database to get past it. Do not generate migrations during deployment.

Deploy reviewed application files and migrations using the site's release process, apply `migrate` to the selected database, collect static files with `collectstatic --noinput` into the configured deployment static root, and only then reload the actual application service through its deployment tooling. The inspected host uses `/etc/systemd/system/gunicorn.service`, the executable `/home/oakley/miniconda3/envs/vpod_env/bin/gunicorn`, eight workers, and `127.0.0.1:8000`. Other deployments must supply their own paths and service configuration. **`systemctl start gunicorn` does not reload a service that is already running**; `sudo systemctl restart gunicorn` restarts it with updated code. This unit has no `ExecReload`; a verified Gunicorn master can instead receive `HUP` from its owning user for a graceful worker reload. Do not signal a guessed process ID. The landing-page incident and verified fix are recorded in [reports/landing-fix-verification.md](reports/landing-fix-verification.md).

Production needs a unique secret key, `DEBUG=False`, accurate allowed hosts, TLS/proxy configuration, secure cookies and CSRF trusted origins as appropriate, backup/restore access, logging/monitoring and a maintained WSGI/ASGI server. The development fallback secret and `runserver` are not production configuration. Do not expose database files, `.env`, metadata cache or private audit/receipt exports through static serving. Public `reports/` delivery should be reviewed for source licensing and contacts before publishing artifacts.

Apply a reviewed enrichment artifact separately from schema deployment, first to a backup/staging copy. Inspect conflicts and counts before applying elsewhere; do not put network enrichment in a service startup command. Preserve generated reports with the release record. For rollback, restore the prior application revision and a compatible database backup to a verified target, or use a reviewed forward fix. Reversing `0007` drops the new scientific/metadata/audit tables and can fail if newly legitimate duplicate SCP observations exist; it is not a routine rollback strategy. Restore private contact information only to private storage. Database flushing is never part of the normal update path.

## Common failures

- **Django missing despite successful pip output:** inspect inherited pip configuration locally. `PIP_TARGET` or a configured `target` can bypass a virtualenv; use the isolated installation command above. Do not print unrelated environment secrets.
- **Landing page returns 400:** inspect `django_errors.log` for `Invalid HTTP_HOST header`. The requested hostname must be in the effective `ALLOWED_HOSTS`. An earlier implementation accidentally removed `visphys.eemb.ucsb.edu` from the fallback list; that is fixed. Keep host validation enabled and reload workers after changing settings. Do not use `ALLOWED_HOSTS=*` as a workaround.
- **Wrong data/missing migrations:** confirm `VPOD_DATABASE`, `VPOD_SQLITE_PATH`, service environment and `showmigrations`. Never infer the live backend from the existence of the PostgreSQL alias.
- **Integrity error importing old data:** inspect the per-row report, source IDs, ranges and migration state; preserve the source row for curation. Do not replace unknown values with zero.
- **Metadata timeout, 404 or ambiguity:** the app still works; inspect attempts/candidates, retry failures with a bounded run, or curate in admin. An unresolved DOI isn't proof that a paper lacks a DOI.
- **Changes not visible:** verify approval status and the selected database. Nested references require separate approval. Check browser/API errors and static-file collection before considering the deployment-specific reload procedure.
- **Blank chart:** inspect text histogram counts and the plotted/excluded totals; check CDN availability. Unknown years or missing CPD do not belong in a zero bin.

[Feature recommendations](docs/feature-proposals.md) remain proposals. The deterministic literature inbox is now implemented: see the [short everyday guide](docs/literature-quickstart.md) for reviewing papers, changing search terms and frequency, previewing results, and enabling the supplied timer after pilot review. Migration `0011` adds only private discovery tables. The [earlier design](docs/monthly-literature-discovery.md) also describes optional approaches that remain proposals.
