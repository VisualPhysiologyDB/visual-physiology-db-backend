# Back up and rebuild VPOD

**Migrations alone do not recreate the populated database.** They create tables, fields and constraints, plus a few explicit data transformations. They do not contain the CSV measurements, recovered bibliography, tuning catalogue, new submissions or curator decisions. [Django explains this distinction](https://docs.djangoproject.com/en/5.2/topics/migrations/).

Choose the starting point:

| What you have | What to do | What you recover |
|---|---|---|
| A complete database snapshot | Restore a copy, then apply any newer migrations | All saved records, relationships, edits, moderation decisions and audit history at backup time |
| Code, source CSVs and the supplied data artifacts | Run the fresh-build steps below | The supplied baseline dataset and reproducible corrections; not subsequent manual edits or submissions |
| Only migration files | Run `migrate` | The schema; scientific tables remain empty |

For the exact current state, use a **database snapshot paired with the matching application code**. A CSV reconstruction is not an exact replacement for that snapshot.

## 1. Identify the database and save it

Activate the application environment and use the same database environment variables as Gunicorn:

```bash
conda activate vpod_env
cd /home/oakley/visual-physiology-db-backend
python manage.py shell -c "from django.conf import settings; d=settings.DATABASES['default']; print(d['ENGINE']); print(d['NAME'])"
```

This prints only the engine and database name/path. It describes **this shell's configuration**; confirm it matches the service configuration. `VPOD_SQLITE_PATH` can change the file. `VPOD_DATABASE=postgres` selects PostgreSQL; the existence of the `vpod_db` alias alone does not select it.

For the default SQLite configuration, the file holding the database is:

```text
/home/oakley/visual-physiology-db-backend/db.sqlite3
```

Create a dated, consistent snapshot; substitute the actual source path printed above if different:

```bash
install -d -m 700 ~/vpod-backups
python tools/backup_sqlite.py db.sqlite3 ~/vpod-backups/vpod-2026-10-07.sqlite3
```

Choose a new filename each time. The helper opens the source read-only, includes committed WAL data, refuses to overwrite a destination, sets owner-only permissions, checks SQLite integrity, and prints a SHA-256 checksum. It uses Python's [SQLite online backup API](https://docs.python.org/3.10/library/sqlite3.html#sqlite3.Connection.backup), which supports concurrent access. This avoids an incomplete ordinary copy of a running database. The resulting snapshot is a standalone SQLite file; do not separately assemble copies of `-wal` or `-shm` files.

Keep the snapshot, its printed checksum, the backup date and a matching code release together. Also save deployment configuration/secrets privately, and any external uploaded files if your deployment has them. The database does not contain the Python code, FASTA/profile files, structure files or JavaScript assets.

**Current repository detail:** several delivered files are still untracked, including migrations `0012`–`0016`, tuning code and `data/tuning/`. Commit the reviewed source changes and assets before relying on a GitHub clone for recovery. `git status --short` shows what needs review; a commit ID alone does not capture uncommitted or untracked files. Do not use a blanket `git add .` to include private files accidentally.

## 2. Restore a saved SQLite state

Restore into a **new file**, leaving both the backup and the serving database untouched:

```bash
python tools/backup_sqlite.py ~/vpod-backups/vpod-2026-10-07.sqlite3 ~/vpod-backups/restored.sqlite3
export VPOD_DATABASE=sqlite
export VPOD_SQLITE_PATH="$HOME/vpod-backups/restored.sqlite3"
export VPOD_LOG_PATH="$HOME/vpod-backups/restore.log"
export VPOD_TUNING_SITE_INDEX_PATH="$HOME/vpod-backups/restored-site-index.json"
export VPOD_STATIC_ROOT="$HOME/vpod-backups/restored-static"
export DEBUG=True
export ALLOWED_HOSTS=localhost,127.0.0.1
python manage.py showmigrations
python manage.py migrate --plan
python manage.py migrate
python manage.py check
python manage.py index_tuning_sites
python manage.py runserver 127.0.0.1:8001
```

Use code compatible with the snapshot. With newer code, review the forward migration plan first; do not use older code against a newer schema. Never generate migrations or flush the database during recovery. Do not rerun the CSV import sequence just to restore a snapshot: the saved data is already present.

Check the local website/admin, record counts, references, approval states and tuning catalogue before switching production. An older snapshot that fails historical constraints needs a reviewed repair on a copy; do not fake migrations or delete rows to bypass it.

Production cutover is a separate operation: stop writes (including discovery jobs), take a final backup, configure the serving database path and file/directory permissions, use production settings, collect static files, and restart the configured service. The service user must be able to write the SQLite file and its containing directory; the helper's private permissions may need a deliberate ownership change. Keep the old code/database pair for rollback. Restoring an older backup discards changes made after it, so reconcile those changes first. Nothing in the commands above switches Gunicorn to the restored file.

## 3. Rebuild the supplied baseline from an empty database

Use the complete reviewed code release, `vpod_env`, and MAFFT as described in the [mapper setup guide](tuning-mapper.md). Keep the original source files. This sequence is for a **new empty target**, not an existing populated database.

**A. Set an isolated target and create its schema.** Use a new directory name; `mkdir` should fail if it already exists. Run subsequent commands in this same shell and stop if any command fails.

```bash
mkdir -m 700 ~/vpod-rebuild-2026-10-07
export VPOD_REBUILD_DIR="$HOME/vpod-rebuild-2026-10-07"
export VPOD_DATABASE=sqlite
export VPOD_SQLITE_PATH="$VPOD_REBUILD_DIR/db.sqlite3"
export VPOD_LOG_PATH="$VPOD_REBUILD_DIR/django.log"
export VPOD_TUNING_SITE_INDEX_PATH="$VPOD_REBUILD_DIR/site-index.json"
export VPOD_STATIC_ROOT="$VPOD_REBUILD_DIR/static"
export DEBUG=True
export ALLOWED_HOSTS=localhost,127.0.0.1
python manage.py migrate --noinput
```

**B. Restore reference IDs and accepted bibliography first, then import measurements.** The saved enrichment artifact works offline and preserves database-only reference IDs. Do not use `--seed-inventory` on an unrelated populated database.

```bash
python manage.py enrich_references --from-artifact reports/reference-enrichment.json \
  --seed-inventory --apply --output "$VPOD_REBUILD_DIR/enrichment.json"
python manage.py import_csvs . --report "$VPOD_REBUILD_DIR/legacy.json"
python manage.py import_visual_acuity visual_acuity.csv --report "$VPOD_REBUILD_DIR/acuity.json"
```

`import_csvs` reads `references.csv`, `opsins.csv`, `heterologous.csv` and `curated_scp.csv`. It also applies the shipped guarded source corrections to newly imported rows. Reports account for imported and unresolved rows; successful command exit does not mean every scientific ambiguity was resolved.

**Optional additional datasets:** run these only if you want the supplied compendium and inferred MNM data. They are part of the broader explorer, but their importers can overwrite existing values, so use them here on the fresh target. The existing compendium importer can leave a partial import and print an error while returning exit code zero. In this rehearsal it stopped on a same-species/wavelength/publication duplicate after adding one row. Its source also includes values outside the current SCP wavelength constraint (208 and 220 nm). Do not treat its exit status as proof of a complete import; the full optional compendium import needs a separate importer repair/review. Retain unresolved rows for scientific review. A snapshot is the way to preserve an already curated compendium state exactly.

```bash
python manage.py import_scp_compendium VPOD_in_vivo_1.0_2026-03-13_12-54-05.csv
python manage.py import_mnm_data mnm_on_vpod_in_vivo_results_fully_filtered.csv \
  --compendium-csv VPOD_in_vivo_1.0_2026-03-13_12-54-05.csv
```

**C. Reconcile mutation accessions and load the curated tuning catalogue.** Preview each artifact before its apply command. Review reported conflicts and unavailable assay links rather than forcing them through.

```bash
python manage.py repair_mutation_accessions \
  --source-corrections data/tuning/source-corrections.json --output "$VPOD_REBUILD_DIR/repair-preview.json"
python manage.py repair_mutation_accessions --apply \
  --source-corrections data/tuning/source-corrections.json --output "$VPOD_REBUILD_DIR/repair.json"
python manage.py import_tuning_catalogue --output "$VPOD_REBUILD_DIR/catalogue-preview.json"
python manage.py import_tuning_catalogue --apply --output "$VPOD_REBUILD_DIR/catalogue.json"
```

**D. Build the single-site catalogue and browsing index.** Inspect the generated preview HTML before applying. Eligible unambiguous comparisons are automatically approved by the existing policy; unresolved candidates remain private. To keep new matches pending, add `--no-auto-approve` to the applied builder command.

```bash
python manage.py build_tuning_candidates --output "$VPOD_REBUILD_DIR/candidates-preview.json"
python manage.py build_tuning_candidates --apply \
  --from-report "$VPOD_REBUILD_DIR/candidates-preview.json" --output "$VPOD_REBUILD_DIR/candidates.json"
python manage.py index_tuning_sites
```

Replay your newly generated preview; an old extraction report may intentionally fail its source fingerprint checks. `data/tuning/catalogue.json`, `source-corrections.json`, reference FASTA/profile files and the pinned sequence correction must accompany the code. Keep vendored structure/browser assets for the interactive views. Cached alignments in `var/` are disposable. The site index is regenerated from the actual rebuilt catalogue.

**E. Verify and open the rebuilt site.**

```bash
python manage.py check
python manage.py migrate --check
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8001
```

Review every import report and compare counts with the release you intended to reconstruct. No discovery scheduler is enabled by this rebuild, and it does not recover later discovery/rejection history. Use a full snapshot when you need those decisions, manual curation, private submissions or existing admin accounts. Back up the completed rebuild with the helper in step 1.

## 4. What should go to GitHub?

- **Application repository:** reviewed code, all migrations, dependency manifests, secret-free configuration examples, permitted source CSVs, scientific correction/catalogue artifacts, reference assets, and this guide. These support a reproducible baseline build.
- **Full database backup:** keep privately, with an off-server copy and a tested restore. It contains account password hashes, possibly active sessions, private submitter details, unpublished records and audit history. Do not publish the raw SQLite file or an unrestricted Django `dumpdata` fixture. Database snapshots are deliberately gitignored.
- **If using GitHub for backup storage:** encrypt the snapshot before uploading it to a restricted private backup repository or private release; keep the decryption key separately and retain another backup elsewhere. Upload the encrypted backup, not `db.sqlite3`. No encrypted upload or key management is implemented by the helper. GitHub says [Git is not designed as a backup tool and regular Git blocks files above 100 MiB](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
- **Public data release:** use a separately reviewed export of approved scientific records. The website's CSV exports are useful for sharing, but they do not restore all relationships, audits or private workflow state. Renaming a full backup or removing only the users table does not make it a public data release.

## PostgreSQL alternative

There is no single `db.sqlite3` file for PostgreSQL. With the deployment's connection settings supplied securely through libpq environment variables or a protected password file, use a [custom-format `pg_dump`](https://www.postgresql.org/docs/current/app-pgdump.html):

```bash
umask 077
pg_dump --format=custom --file=/private/backup/vpod.dump "$PGDATABASE"
# Restore only into a newly created empty database with appropriate ownership:
createdb vpod_restore
pg_restore --exit-on-error --no-owner --no-acl --dbname=vpod_restore /private/backup/vpod.dump
```

Supply the real host, port, role and database; the paths/names above are examples. VPOD's `POSTGRES_*` names do not automatically configure these command-line tools: supply matching `PGHOST`, `PGPORT`, `PGUSER` and `PGDATABASE` or explicit connection flags. Choose a new dump filename and use compatible PostgreSQL client/server versions. The restore role needs the required ownership/permissions; recreate application grants separately. `pg_dump` covers one database, not cluster roles, deployment secrets or server configuration. See [pg_restore documentation](https://www.postgresql.org/docs/current/app-pgrestore.html). Point `VPOD_DATABASE=postgres` and `POSTGRES_DB=vpod_restore` at the restored database before reviewing/applying migrations. Never restore over the serving database as a rehearsal.

## Verification for this guide

The isolated rehearsal applied all migrations, replayed bibliography without conflicts, imported all 2,986 source opsins, 1,397 heterologous rows and 549 acuity rows, and rebuilt the tuning catalogue/index. Of 1,570 source SCP rows, 1,442 imported; 118 duplicate observations and 10 wavelength ranges remain explicitly reported. The optional compendium stopped after one additional row; the MNM import completed 589 rows with two unresolved references.

The rebuilt database was backed up and restored into another new file. All table counts and the complete logical SQL dump matched, SQLite integrity passed, and no foreign-key violations were found. Five backup-helper tests passed, including committed WAL data and overwrite protection. See [the rebuild verification report](../reports/database-rebuild-verification.json) for commands, counts and unresolved outcomes.

PostgreSQL restore, production cutover, superuser creation and encrypted GitHub upload were not executed. No production service or database was modified.
