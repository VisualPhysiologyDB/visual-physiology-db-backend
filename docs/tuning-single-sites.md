# Updating mutation accessions and the tuning catalogue

Use `vpod_env`. The repair and builder work offline, can be rerun, and never reverse a mutation automatically. No new package is required.

## Apply this update

Back up the serving database and keep the previous code/static release. Check which database the service actually uses; the commands below use your configured database. Test them on a copy first.

```bash
cd /home/oakley/visual-physiology-db-backend
conda activate vpod_env
python manage.py check
python manage.py migrate --plan
python manage.py migrate
python manage.py repair_mutation_accessions \
  --source-corrections data/tuning/source-corrections.json \
  --output reports/accession-preview.json
# Review the preview; then apply the guarded corrections and duplicate merges:
python manage.py repair_mutation_accessions --apply \
  --source-corrections data/tuning/source-corrections.json \
  --output reports/accession-applied.json
python manage.py build_tuning_candidates --output reports/sites-preview.json
# Review sites-preview.html, then apply the checked snapshot:
python manage.py build_tuning_candidates --apply \
  --from-report reports/sites-preview.json --output reports/sites-applied.json
python manage.py collectstatic --noinput
```

Apply the supplied migrations **0014–0016**; do not generate migrations on the server. Reload your configured Gunicorn service after database/static updates. The development work used isolated SQLite copies; it did **not** modify the serving database or reload services. No flush is needed.

The repair preview does not write to the database. It evaluates current values; outcomes can change after its listed source corrections. An applied rehearsal on a copy gives the complete result. The builder's snapshot replay checks source values and algorithm identity and stops on disagreement. Neither command trusts approval decisions embedded in report files.

## What the repair does

- Leaves already correctly tagged proteins alone. Legacy comma and underscore separators between mutations are accepted. Accessions allow 512 characters so long construct labels are retained.
- Gives each mutant an accession such as `MT625929.1_Y178F`. A mutant sharing its WT's protein gets a separate protein record; WT accessions and sequences are not renamed into mutants.
- Constructs a missing mutant only from an unambiguous same-species base WT and the stated **forward** substitution(s). Numbering priority remains bovine → human (human opsins only) → squid → self → spider. Two MAFFT gap settings must agree. This verifies internal consistency, not independent mutant sequencing or the publication's numbering convention.
- When construction fails, preserves the assay, tags its accession and reports the reason. A shared WT sequence is not presented as a mutant: the separate mutant sequence remains null. Original protein records and hashes remain in the audit. A conflicting existing mutant sequence is retained and flagged for review.
- Restores missing protein relationships using the source accession and matching species, ignoring surrounding whitespace. A recognized mutant suffix can identify the base WT when its mutant construct is absent; it is then built separately. Conflicting WT sequences are not chosen arbitrarily. Taxonomy typos or conflicting names stay unresolved. Chimeras are excluded, as requested.

For the zebra shark, the reviewed correction file archives **1388 → 1089, 1389 → 1091, 1390 → 1092, 1391 → 1090**. These are repeated entries for the same experiment from the preprint and published paper, under synonymous names. Legacy IDs, references and original values remain available; `duplicate_of` points to the retained measurement. Archived duplicates are excluded from public tables and tuning evidence, even if mistakenly marked approved later. Measurements from separate experiments are not merged by species or wavelength.

The [published paper, Figure 1C–D](https://pmc.ncbi.nlm.nih.gov/articles/PMC10068813/) supports zebra-shark `T94A`/`Y178F` and whale-shark `A94T`/`F178Y`. Explicit corrections fix the reversed zebra-shark labels and `187` transcription errors in both species. [WoRMS](https://www.marinespecies.org/aphia.php?id=220032&p=taxdetails) records *Stegostoma fasciatum* as a synonym of *S. tigrinum*. The retained rows keep their supplied taxonomic text; the merge audit documents the synonym.

One additional source error was confirmed: Opsin 2976 (`MT625928`, whale shark) contained the zebra-shark WT sequence. Its corrected accession-specific GenBank translation is pinned in `data/tuning/MT625928.1.fasta`; the original sequence and evidence URL are audited. Every explicit source correction/merge has original-value guards. A differing curator value produces a conflict rather than a forced overwrite. The raw CSVs are unchanged.

## Approval and safeguards

The builder requires a same-species, unmutated WT, an exact one-residue sequence difference in the recorded direction, supported coordinates, approved source records/references, positive measured wavelengths, and an absolute shift of **at least 1 nm**. It tries base accession matches first, then same-species one-residue matches. It prefers the same publication within that tier. Equivalent WT observations with identical sequence and wavelength can share a preferred comparison; conflicting wavelengths or coordinates remain ambiguous.

**Unambiguous eligible matches are automatically approved by applied builder runs.** Their evidence explicitly says automatic sequence comparison; manual literature-check boxes remain unchecked. Both publications are cited for cross-study pairs. Matching sequences do not establish comparable experimental conditions or predict a transferable spectral effect.

Exact cell-culture, purification and spectrum-label agreement is **off by default**. Available differences and missing values remain visible. Use `--strict-conditions`, set `VPOD_TUNING_STRICT_CONDITIONS=true`, or enable **Require condition match** on a candidate to require matching nonblank labels. Use `--no-auto-approve` or `VPOD_TUNING_AUTO_APPROVE=false` to keep new matches pending. These options govern new decisions, not retroactive withdrawal of existing approvals.

Rejected decisions, curator selections/notes and reviewed evidence are preserved. Changed source inputs mark reviewed candidates stale and hide affected evidence until reconciled. Existing manually curated assertions are not duplicated. Zero wavelength sentinels, indels, unresolved sequences and duplicates cannot auto-publish. Ordinary API users cannot approve, update or delete published records.

## Correcting flagged entries

Open **Django admin → Tuning candidates**. The source link opens the assay; **Sequence diagnostics** explains identical/shared proteins, differences and lengths. Archived duplicates link to their retained assay.

1. Check the paper. Correct **Mutations**, the linked **Opsin** sequence/accession, or **Mutation numbering** on the source assay. Do not edit a WT shared by other assays into a mutant.
2. Select the candidate and run **Repair selected mutant accessions**. Alternatively use the corresponding action on **Heterologous data**, or the command below. It can recheck a flagged construct after source correction. Original and new values are audited.
3. Rerun the builder. A curator can also select a valid comparison and approve it manually. Stale previously reviewed entries require explicit acknowledgment and reconciliation of linked evidence. A numbering hypothesis alone is not a valid WT–mutant pair.

```bash
python manage.py repair_mutation_accessions --apply --retry-unresolved --assay-ids 123
python manage.py build_tuning_candidates --apply --output reports/sites-latest.json
```

Reports are available as searchable **HTML**, spreadsheet **CSV**, and full **JSON**. Search for `needs_review`, `unresolved`, or `conflict`. Editing report files does not edit the database. **Tuning audits** records the applied changes; `mutation_build` shows construct provenance on the assay.

## Future data

`import_csvs` applies the shipped guarded source fixes to newly imported legacy rows before tagging their mutants; existing records and approval decisions remain unchanged on reimport. Public heterologous submissions automatically append a recognized mutation suffix, reject conflicting suffixes, and remain pending. For other imports/admin entry, run the repair and builder after adding data. Neither runs on public page requests, and no scheduler is activated.

```bash
python manage.py repair_mutation_accessions --apply --output reports/accessions-latest.json
python manage.py build_tuning_candidates --apply --output reports/sites-latest.json
```

Keep the database/audit history and reports in backups. The ignored `var/tuning-extraction-cache/` is disposable: it caches sequence/profile/MAFFT-version-specific alignments, with two 20-second bounded calls per uncached sequence. Failed rows remain reviewable. Recovery uses your paired code/database backup or an explicit curator correction; do not flush or blindly reverse migrations after new data is entered.

Run `python tools/test_tuning.py` in development/staging. It uses an isolated test database. Applied repair results: [HTML](../reports/mutation-accessions-applied.html), [CSV](../reports/mutation-accessions-applied.csv), [JSON](../reports/mutation-accessions-applied.json). Catalogue results: [HTML](../reports/tuning-repaired.html). Verification and limitations: [JSON](../reports/mutation-repair-verification.json).

## Verified development results

On an isolated copy: four zebra-shark merges, 72 accession-label repairs (46 WT-consistent constructs; 26 require sequence/notation review), four restored sequence links, and 235 new automatic approvals. All 898 linked, recognized mutant assays have matching suffixes. Assays 50, 1027 and 1029 still lack a reliable protein relationship. Repeated repair and CSV import preserve the repaired source/decision/audit records; catalogue replay leaves all 482 candidate rows unchanged.

133 backend tests and 51 Chromium checks passed, including the previously failing admin form. Migrations passed on a copy and a fresh SQLite database, with no unintended drift. A fresh CSV import accounted for every input row and now accepts all 2,986 source opsins; its raw taxonomic/sequence discrepancies leave 12 mutant relationships unresolved, recorded in the verification report. PostgreSQL and other browsers were not tested.
