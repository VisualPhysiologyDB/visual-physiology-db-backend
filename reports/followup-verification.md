# Follow-up: MSP duplicate correction and recovery review

This report supersedes the unrestricted MSP/SCP duplicate behavior in the initial implementation. Bibliographic recovery results and visual acuity totals are unchanged.

## Executed verification

- `manage.py test core --noinput`: **32 tests passed** after the correction. The old test allowing two identical unreferenced MSP rows was replaced. Added coverage exercises same-publication rejection, separate-publication acceptance, whitespace/case normalization, duplicate DOI reference IDs, database enforcement, missing reference handling, null/zero wavelength treatment, retained duplicate visibility, pending submissions and idempotent non-destructive reconciliation.
- Fresh migrations `0001`–`0010`, migration of an isolated original `0004` database copy, and migration of the enriched `0009` copy all succeeded. `check` and `makemigrations --check --dry-run` passed. No unintended migration drift was found.
- The corrected full review database is `/tmp/vpod-msp-review.sqlite3`. All **3,871** original MSP rows remain stored; **75** repeated rows in **54** groups point to their primary record. **3,796** approved primary rows are publicly visible. Every original MSP column, including IDs, foreign keys, scientific/source fields, timestamps and approval status, was compared to the untouched original and matched exactly. Only the new `duplicate_of` field changes publication visibility.
- The same 54 groups/75 repeats were found when migrating the original database copy, before enrichment. [scp-duplicate-review.json](scp-duplicate-review.json) identifies every primary/source record. The reconciliation command reported zero newly needed links after migration. No record was deleted.
- `tests/review_browser_checks.py`: both check groups passed in Playwright Chromium with no JavaScript exceptions. The standalone report showed 145 issue entries (59 unresolved, 4 conflicts, 82 records in duplicate-publication groups). Issue/text filtering, admin URL targets, keyboard expansion of evidence, and 390px mobile containment passed. The real local MSP table, API and CSV export each contained 3,796 records; chart counts agreed. [Browser results](followup-browser-verification.json).
- Reimport on the corrected full review copy reported all 597 source references unchanged and all 549 acuity rows unchanged.
- Full fresh legacy CSV import under the corrected rule completed: 597 references unchanged; 2,974 opsins imported / 12 unresolved; 1,397 heterologous records imported; 1,442 SCP records imported / 128 explicitly reported unresolved. The 128 SCP outcomes comprise **118 same-publication repeats** plus the **10 existing wavelength-range cells**. All 1,570 source SCP rows are accounted for in [followup-fresh-import.json](followup-fresh-import.json); source files remain intact. This supersedes the initial unrestricted fresh-import SCP count of 1,560.
- Offline artifact replay on the new fresh database returned zero conflicts. The 602-record metadata artifact is unchanged. The isolated full review database still contains all 549 visual acuity rows.
- The repository database still has migrations only through `0004`; no live migrations, enrichment or acuity imports were performed. PostgreSQL remains untested. A temporary localhost server used for browser verification was stopped afterward; no existing application service was restarted.

See [database comparison](followup-database-verification.json), [the review/correction guide](../docs/metadata-review.md), [the generated review page](reference-review.html) and [the spreadsheet worklist](reference-review.csv). Review links point to `http://127.0.0.1:8000` by default; regenerate with `--admin-base-url` for a different site. These are read-only snapshots; corrections are saved through Django admin and field audits.

## Commands used

```bash
VPOD_SQLITE_PATH=/tmp/vpod-followup-tests.sqlite3 /tmp/vpod-venv/bin/python manage.py test core --noinput
VPOD_SQLITE_PATH=/tmp/vpod-msp-review.sqlite3 /tmp/vpod-venv/bin/python manage.py migrate --noinput
VPOD_SQLITE_PATH=/tmp/vpod-msp-review.sqlite3 /tmp/vpod-venv/bin/python manage.py clear_scp_duplicates --report reports/scp-duplicate-review.json
VPOD_SQLITE_PATH=/tmp/vpod-msp-review.sqlite3 /tmp/vpod-venv/bin/python manage.py export_reference_review --artifact reports/reference-enrichment.json --output reports/reference-review.html --csv-output reports/reference-review.csv
PLAYWRIGHT_BROWSERS_PATH=/tmp/vpod-browsers /tmp/vpod-venv/bin/python tests/review_browser_checks.py
```

Browser execution required permission to launch a local process outside the restricted sandbox; the first sandboxed attempt could not launch Chromium. The successful rerun used the same read-only checks against the isolated localhost database. No additional packages were installed during this follow-up.
