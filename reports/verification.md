# Verification and delivery record

**Follow-up correction:** [MSP duplicate policy and review workflow verification](followup-verification.md) supersedes the unrestricted SCP-repeat behavior and adds migration `0010`. The results below record the initial implementation through `0009`; reference recovery and acuity totals remain unchanged.

All implementation and data verification used isolated SQLite databases under `/tmp`. The repository database remains at core migration `0004`; original counts remain 602 references, 3,460 opsins, 1,987 heterologous records and 3,871 SCP records. Identifiers, foreign keys and approval statuses match the migrated/enriched copy exactly ([relationship verification](relationship-verification.json)). Original CSVs and the supplied PDF match their recorded SHA-256 hashes ([source inventory](source-inventory.json)). No database was flushed. No deployment or existing application service restart occurred.

## Final bibliographic results

The complete 602-record inventory includes the CSV references, database-only references and existing source-publication helpers. The authoritative replay artifact is [reference-enrichment.json](reference-enrichment.json); [curator-review.json](curator-review.json) contains unresolved candidates, attempted providers/URLs, reasons, conflicts and duplicate groups.

| Completeness measure | Before enrichment | After enrichment |
|---|---:|---:|
| Publication titles | 0 | 543 |
| Supported partial publication dates | 0 | 543 |
| Known compatibility publication years | 25 | 544 |
| Syntactically valid DOI identifiers | 514 | 550 |
| Raw MOM values retained | 276 | 276 |
| Retained normalized method assertions | 263 | 263 |

543 reference records resolved through authoritative bibliographic metadata; 37 DOI recoveries came from citation/malformed identifiers, as distinct from case/prefix normalization of already syntactic DOIs. 59 remain unresolved. Seven syntactically supplied DOI identifiers lack verified registry metadata; they are not counted as verified resolutions. There are 4 protected year conflicts (refids 67, 599, 600, 606). 39 normalized DOI groups contain 82 reference records; none were merged. Ref 407 is explicitly flagged `DOI WRONG` in its source, so the DOI is retained only as raw text pending review. Six invalid YOP prose cells are retained/reported, not converted to years.

There are 14 validated source URLs and 166 preserved original citation/identifier values. Online dates: 322; print dates: 472. Histograms use the compatible year first; 58 records lack a histogram year. Counts are records, including duplicates.

The final isolated run has 3421 durable field-audit rows. The artifact records each proposed field's original/recovered values, evidence URL/provider, retrieval time and decision; candidates/conflicts are not applied. Fresh offline replay returned **zero conflicts** and exactly matched reference metadata in the enriched original-database copy. Reimport of all 597 reference source rows preserved every normalized metadata field and approval decision.

## Acuity accounting

All 549 rows were imported as approved legacy observations, with all source columns retained. All 549 reference foreign keys resolve. CPD is present in 546 observations (0.02–160 cycles/degree), and null in three. No CPD was recomputed. There are 82 taxonomy warnings requiring source inspection; no observation was dropped because of taxonomy or optional measurements. Repeat imports on both copied and fresh databases report 549 unchanged rows and create no duplicates. New public observations remain pending.

See [acuity-import.json](acuity-import.json), [acuity-reimport.json](acuity-reimport.json) and [fresh-replay-verification.json](fresh-replay-verification.json).

## Executed checks

- `manage.py makemigrations --check --dry-run` before model edits: no pre-existing model/migration drift. Later checks: no unintended drift.
- Committed migrations `0005` through `0009` applied on a copy of the original `0004` database. A fresh database applied `0001` through `0009`. `check`, `showmigrations`, `migrate --plan` and migration SQL inspection ran successfully.
- `manage.py test core`: **25 tests passed** on the final code. External metadata requests were mocked. Tests cover identifier/citation classification, ambiguous candidates, DataCite fallback, partial/missing dates, multiple/uncertain methods, source warnings, metadata/approval preservation, source-helper rejection preservation, raw numeric null handling, stable acuity IDs, foreign keys, curator conflicts, pending submissions, nested/public visibility and unauthorized writes.
- `tests/data_checks.js`: passed under Playwright's bundled Node. It checks histogram boundaries (including overflow), empty bins, missing/nonfinite values, duplicate record counting, safe links and CSV escaping/zero preservation. The host `/usr/bin/node` is too old for this application's modern JavaScript; Node 18+ is documented.
- `tests/browser_checks.py`: **11 groups passed**, zero JavaScript exceptions, using Playwright Chromium with real local API data and paginated/adversarial fixtures. Existing tabs, real Chart.js counts, all 549 acuity export/copy rows, filters, loading/error/empty states, required/positive acuity validation, a real pending local form submission, visual ellipsis, full titles by hover/keyboard/emulated touch, safe text rendering and 390px containment were exercised. See [browser-verification.json](browser-verification.json). Screenshots were inspected locally. Physical devices and other browser engines were not tested.
- `collectstatic --noinput` with `VPOD_STATIC_ROOT=/tmp/vpod-static`: 156 files copied, 450 post-processed. The actual deployment static directory was not changed.
- Full fresh curated imports: references 597 unchanged; opsins 2,974 imported / 12 unresolved; heterologous 1,397 imported; SCP 1,560 imported / 10 unresolved. These reconcile every input row. The 12 opsin accessions exceed the existing 100-character limit; ten SCP cells contain ranges (`399–402` or `403–404`) rather than single numbers. They are reported in [fresh-legacy-import.json](fresh-legacy-import.json), not coerced or silently dropped. Existing corresponding records in the original copy are preserved.
- Optional historical commands were exercised only on a separate disposable fresh database. `import_scp_compendium` reported a constraint error on source wavelengths outside the existing 300–800 nm schema (208 and 220 nm). Its existing broad exception handler returns normally and can leave partial results, so it is **not a successful complete import**. `import_mnm_data` imported 589 records and reported two unresolved source references. See [optional-import-verification.json](optional-import-verification.json). Neither historical command was used to alter the original or authoritative enriched copy.

## Exact isolated reproduction commands

In the implementation environment the virtualenv was `/tmp/vpod-venv`; replace it with your verified `.venv` after following the README installation instructions. Choose a **new empty path**, or a verified consistent backup copy, rather than the repository database.

```bash
export VPOD_DATABASE=sqlite
export VPOD_SQLITE_PATH=/tmp/vpod-your-review.sqlite3
/tmp/vpod-venv/bin/python manage.py migrate
# For a fresh empty target: seed exact reference IDs and replay accepted corrections.
/tmp/vpod-venv/bin/python manage.py enrich_references \
  --from-artifact reports/reference-enrichment.json --seed-inventory --apply \
  --output /tmp/vpod-your-replay.json
/tmp/vpod-venv/bin/python manage.py import_csvs . --report /tmp/vpod-your-import.json
/tmp/vpod-venv/bin/python manage.py import_visual_acuity visual_acuity.csv \
  --report /tmp/vpod-your-acuity.json
/tmp/vpod-venv/bin/python manage.py test core
/tmp/vpod-venv/bin/python manage.py makemigrations --check --dry-run
```

For an existing matching database copy, **omit `--seed-inventory`**, first run replay without `--apply`, inspect conflicts and apply the reviewed artifact explicitly. The README documents fresh network recovery, caches and retry/offline options. No network occurs in offline artifact replay or migrations.

## Limits, environmental issue and unexecuted operations

PostgreSQL execution, actual deployment settings, service reloads and production rollback were not tested. PostgreSQL alias configuration and a driver manifest were supplied; the application uses the default connection unless explicitly configured otherwise. `createsuperuser` is documented but was not executed. The Feller publisher full-text fetch returned 403; field definitions are supported by the supplied headings and accessible primary sources, with row-level conventions/protocols flagged as unverified. Current public Crossref, DataCite and Europe PMC response shapes were checked; a one-result incremental Europe PMC query returned HTTP 200 and a pagination cursor. No discovery candidates were submitted to a live API.

The first dependency installation unexpectedly inherited a host pip target, `/home/PIA/.local/lib/python3.9/site-packages`, despite invoking the temporary virtualenv. Pip reported placing packages there and warned about pre-existing package directories. The setup was then rerun with `PIP_TARGET` unset and `PIP_CONFIG_FILE=/dev/null`, explicitly installing into `/tmp/vpod-venv`; **all reported application tests used that isolated environment**. The pre-existing target was not blindly uninstalled or cleaned because its prior contents were not known. This host-target side effect should be reviewed by the environment owner; it did not involve database writes or service reloads.

The seven feature recommendations and monthly discovery design/configuration are **proposals**. No scheduler, worker, model service, discovery schema or proposed discovery endpoint has been installed or activated.
