# Reviewing recovery and applying the prepared changes

## What has and has not been applied

The repository `db.sqlite3` is unchanged, with core migrations through `0004`. No live database, PostgreSQL service, deployment, or existing service was changed. The implementation includes migration files `0007`–`0010`; you do **not** need to generate these again with `makemigrations`. Applying migrations and applying scientific/bibliographic data are separate steps.

The current review database is `/tmp/vpod-msp-review.sqlite3`: it contains the complete original inventory, recovered metadata, 549 acuity rows and migrations through `0010`, including the corrected MSP duplicate rule. It is a disposable development artifact; preserve a copy somewhere suitable if you want to keep it. `/tmp/vpod-venv` is the tested virtual environment in this workspace. Neither temporary path is a production deployment.

To inspect that prepared copy:

```bash
cd /home/oakley/visual-physiology-db-backend
source /tmp/vpod-venv/bin/activate
export VPOD_DATABASE=sqlite
export VPOD_SQLITE_PATH=/tmp/vpod-msp-review.sqlite3
export DEBUG=True
python manage.py showmigrations core
# If you need an admin account on this isolated copy:
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Use `/admin/core/reference/` to edit references, `/admin/core/referencemetadataaudit/` for field history, and `/admin/core/curatedscp/` to inspect retained MSP duplicates. Creating an admin account was documented, not executed during this task.

## Exactly how recovery worked

1. The command iterated **all 602 ORM reference records**, including database-only IDs and source-publication helpers. It first classified the original identifier as a DOI, malformed DOI, URL, citation, placeholder, nonpublication or disputed source. Original citations/identifiers are retained separately before repairing DOI fields.
2. For an explicit DOI, it requested Crossref's `/works/{doi}` endpoint and required the returned DOI to match after normalization. If Crossref returned 404, it tried DataCite's `/dois/{doi}` endpoint. Titles and dates came from those deposited records, not model-generated text. A supplied DOI resolving successfully is evidence about that DOI's publication; it does not independently prove every experimental row originally cited the correct paper. The explicit source warning `DOI WRONG` prevents automatic acceptance.
3. For citations or malformed identifiers, Crossref bibliographic search returned up to five candidates. A citation match requires a punctuation/diacritic-normalized title match, first-author agreement, a matching publication year, and either journal agreement or matching volume and first page. Exactly one candidate must pass; its DOI is then fetched and checked again. Fuzzy title similarity or the first search result alone is insufficient. These conservative checks are automated bibliographic corroboration, not full-text verification of every paper. URL-only sources without identifiable citations and unsupported title-only matches remain unresolved; the command does not crawl arbitrary websites.
4. For Crossref dates, the policy selects print date, otherwise online date, otherwise published/issued date. Both print and online dates are retained separately. DataCite uses its Issued date or publication year. Precision stays `YYYY`, `YYYY-MM` or `YYYY-MM-DD`: no January 1 is invented. Deposit/index/creation dates are not treated as publication dates.
5. Blank normalized fields can be filled, but existing nonempty curated values are protected. The histogram uses the compatible `year_of_publication` field first. Four existing year disagreements therefore remain reported conflicts instead of being overwritten. Admin edits are audited and protected even when an administrator intentionally clears a field.
6. Each proposed change records the reference ID, field, old/new values, provider/evidence URL, retrieval time, decision and whether applied. Requests are cached, paced, timed out and retried with bounded limits. Nothing is looked up during migrations or normal page requests.

The delivered run recovered titles and supported publication dates for 543 records, increased known years from 25 to 544 and verified 37 DOI recoveries. It left 59 records unresolved and reported 4 field conflicts and 39 duplicate-DOI groups. "Unresolved" means the automatic procedure did not establish identity; it does not mean the publication lacks a title, year or DOI.

Measurement methods came from the source `MOM` values, with conservative spelling normalization and retained uncertainty. Methods were not inferred from bibliographic metadata or publication relevance.

## Easy review and correction

Open [reference-review.html](../reports/reference-review.html) in a browser. It contains searchable issue cards, separate unresolved/conflict/duplicate filters, expandable candidate evidence and **Edit reference in Django admin** links. Links default to `http://127.0.0.1:8000`; regenerating with `--admin-base-url` targets your actual site. The [CSV worklist](../reports/reference-review.csv) is an alternative for Excel/LibreOffice. Both contain 145 entries: 59 unresolved references, 4 conflicts and 82 reference records belonging to duplicate publication groups.

1. Check the original citation and provider evidence; resolve uncertain identity from the original paper or publisher when necessary.
2. Follow the admin link and edit the DOI, title, supported partial dates and compatible publication year. Include the supporting source URL or a note. Preserve raw citations and source snapshots.
3. Save in admin. This records the change in the field audit and protects it on subsequent enrichment. Correcting metadata does not automatically approve a pending publication or any related measurement.
4. Regenerate the report after another recovery run. Reports are snapshots and do not update when admin data changes. Editing the spreadsheet is **not** an import mechanism, and marking a candidate in the HTML does not accept it. The original delivered report remains a historical record.

The enrichment command still visits a manually corrected record to compare registry evidence; a remaining mismatch can therefore appear as a protected conflict. Source flags such as `DOI WRONG` are intentionally conservative and require curator interpretation even after a manual correction.

## Reuse the same scripts

After selecting an isolated database and activating your virtual environment:

```bash
# Inspect current records and proposed changes without altering the database.
python manage.py enrich_references --dry-run \
  --cache /tmp/vpod-metadata-cache --output /tmp/recovery-review.json
python manage.py export_reference_review \
  --artifact /tmp/recovery-review.json --output /tmp/recovery-review.html \
  --csv-output /tmp/recovery-review.csv --admin-base-url http://127.0.0.1:8000

# Apply exactly the reviewed, accepted changes, with no additional network lookup.
python manage.py enrich_references --from-artifact /tmp/recovery-review.json \
  --apply --output /tmp/recovery-applied.json
```

`--retry-failures` retries cached lookup failures. `--offline` permits cached responses only. A new cache directory refreshes successful responses. Use `--mailto` or `METADATA_MAILTO` for a real maintainer contact. Rerunning preserves curated values and approval decisions. The full delivered artifact can be replayed with no network: substitute `reports/reference-enrichment.json` for `/tmp/recovery-review.json`.

For an **existing matching database copy**, the integration sequence is:

```bash
python manage.py migrate --plan
python manage.py migrate
python manage.py enrich_references --from-artifact reports/reference-enrichment.json \
  --output /tmp/replay-preview.json
# Inspect replay_conflicts before applying.
python manage.py enrich_references --from-artifact reports/reference-enrichment.json \
  --apply --output /tmp/replay-applied.json
python manage.py import_visual_acuity visual_acuity.csv --report /tmp/acuity-import.json
python manage.py clear_scp_duplicates --report /tmp/scp-duplicates.json
# Only if the report has new same-publication repeats to link:
python manage.py clear_scp_duplicates --commit --report /tmp/scp-duplicates-applied.json
```

Choose and back up the intended target explicitly before doing this elsewhere. `migrate` changes the schema and applies the committed data migrations, but does **not** load the enrichment JSON or acuity CSV. Existing copies must omit `--seed-inventory`. Fresh empty database reproduction requires `--seed-inventory` as documented in the README. No command here calls `makemigrations`, flushes a database, or reloads a service.

## Corrected MSP duplicate policy

A public MSP/SCP observation is distinct by **genus + species + measured λmax + publication**. Leading/trailing spaces and letter case do not create a new taxon. Different reference IDs carrying the same canonical DOI are the same publication for validation/reconciliation. Without a DOI, the existing reference ID is used; identical titles alone are not sufficient to merge publication identities. Missing λmax and the legacy zero sentinel do not establish a duplicate measured value. Wavelength comparisons use the stored numeric value, without rounding or tolerance-based merging.

The original species/wavelength-only constraint was too restrictive across publications. Removing it without a replacement was too permissive within one publication. Migration `0010` supplies the replacement and links existing repeats to a primary row using `duplicate_of`. Primary selection prefers an approved record, then pending, then rejected, and the lowest ID within that status. All original rows, scientific fields, source IDs, reference relationships and approval values remain stored. Linked repeats are excluded from the public API, table, chart, copy and export; admin still shows them.

The isolated full inventory contains 54 repeated groups with 75 extra rows: 3,871 stored MSP/SCP records and 3,796 public primary records. Of those repeats, 54 are exposed by trimming/case-normalizing taxonomy within the same reference ID; another 21 involve separate reference IDs with the same DOI. No references or measurements were deleted. [The duplicate report](../reports/scp-duplicate-review.json) lists every affected source record and retained primary ID.

Model/admin/submission validation blocks new repeats, including DOI aliases; a database constraint also prevents normalized taxon/wavelength/reference-ID repeats and handles missing references consistently. Reference DOI edits and direct bulk SQL can make formerly separate publication IDs equivalent, so run the reporting command after such metadata changes. Its historical name `clear_scp_duplicates` is retained, but `--commit` now **links and hides**, never deletes. New source imports report duplicate observations for review instead of creating a second public observation. To correct an incorrectly linked legacy row, inspect its evidence in admin, correct its identity fields, and clear `duplicate_of`; validation must then pass.

On a fresh CSV-only import, the corrected rule imports 1,442 SCP records and reports 118 same-publication repeats plus 10 invalid wavelength-range cells (all 1,570 rows accounted for). This differs from the full original database, which also contains compendium observations and already had its own historical deduplication. The original database copy is preserved in full; the fresh importer reports repeats instead of creating them.
