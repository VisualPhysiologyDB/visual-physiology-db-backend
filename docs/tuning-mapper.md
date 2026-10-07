# Tuning Site Mapper — Beta

For expandable site groups, mutation selection and target-residue compatibility, see the [site browser guide](tuning-site-browser.md).

To expand the catalogue from VPOD's single-site mutations, use the [repeatable extraction and review workflow](tuning-single-sites.md). It supports accession-independent WT matching and explicit numbering review.

The new explorer tab maps **published residue positions** onto opsin proteins and structures. It does **not** predict a mutant’s wavelength or establish that an effect transfers between species. Chimeras are outside this beta.

## For a biologist

1. Paste one or more protein sequences, read a FASTA file, or find a public VPOD protein.
2. Choose the opsin family. Vertebrate/ciliary starts with bovine numbering; invertebrate/rhabdomeric starts with squid. Spider, human LWS and a custom reference are also available. Animal groups and opsin families are not identical.
3. Select relevant evidence. Experimental shifts must be at least **1 nm in absolute magnitude**; direct literature assertions can qualify without a numerical shift. Proposed candidates are off by default. Subtype and text filters narrow the list.
4. Map, inspect warnings, and download the mapped-sites and evidence CSVs. Both include source references. An absent or unstable coordinate stays unresolved. A combined mutant retains one combined effect, even though it produces multiple site rows.
5. Optionally open a reference structure or your own PDB/mmCIF, select a chain, and mark the sites. Save a PNG or a view session. Structure labels use author numbering, which may differ from sequence numbering.

Changing inputs invalidates previous results. Mapping sessions save inputs, evidence and alignment; restoring one requires mapping again. View sessions restore coordinates, saved marks and camera locally; those saved marks are explicitly labelled unverified until mapped again. Downloads can contain your private sequences/coordinates.

**Important examples:** Spider-paper E181D uses bovine numbering: it maps to **E194 in spider B1B1U5**, and E180 in squid P31356. The vertebrate review’s five LWS sites use **human LWS** numbering despite the broad figure caption: 180/197/277/285/308 correspond to bovine 164/181/261/269/292. The catalogue records that discrepancy and its supporting primary reference. It never converts all positions by a fixed offset.

## What is included

The first release has **22 evidence entries**: 4 measured entries (3 meet the cutoff; one small-effect result is contextual), and 18 literature-supported entries. Four frozen sequences provide numbering anchors. Reference structures are 1U19, 2Z73 and 6I9K; the spider structure contains **9-cis retinal**, which is not the 11-cis experimental context of every cited measurement.

This is a **starter catalogue**, not automatic interpretation of every historical mutation row. `reports/tuning-import.csv` accounts for the legacy experimental inventory: included/comparator, wild type, chimeric, not public, or needing primary-source review. It is a curator worklist, not a list of newly established tuning sites. Inferred wavelengths are never experimental evidence. Passing the 1-nm numerical threshold does not establish statistical significance; the echidna F169A entry explicitly retains the authors’ nonsignificance finding.

## Development and installation

Development used `/home/oakley/miniconda3/envs/vpod_env/bin/python` (**Python 3.10.19, Django 5.2.8**) and existing **MAFFT 7.505**. No new Python runtime package is needed. Mol* **5.13.0** is pinned under `static/vendor/molstar/`, with its MIT licence; it loads only when a visitor opens structures. No npm build is needed. The frozen catalogue and reference coordinates work without external lookups.

Use an isolated database first:

```bash
conda activate vpod_env
cd /home/oakley/visual-physiology-db-backend
export VPOD_DATABASE=sqlite
export VPOD_SQLITE_PATH=/tmp/vpod-tuning-local.sqlite3
export VPOD_LOG_PATH=/tmp/vpod-tuning-local.log
export VPOD_STATIC_ROOT=/tmp/vpod-tuning-static
export VPOD_MAFFT_PATH=/usr/local/bin/mafft
python manage.py migrate --plan
python manage.py migrate
python manage.py import_tuning_catalogue --output /tmp/tuning-preview.json
# Inspect the JSON and matching CSV, then apply the reviewed local artifact:
python manage.py import_tuning_catalogue --apply --output /tmp/tuning-applied.json
DEBUG=True python manage.py runserver 127.0.0.1:8000
```

Migration **0012** creates the schema; it does not import evidence or contact any service. The import creates reviewed catalogue entries and missing cited references as approved curated data. It reuses existing references by canonical DOI, preserving their metadata and decisions, including rejection. Existing tuning records are never overwritten: differences are reported as conflicts. Repeating an import creates no duplicates. Source assay links require a matching ID, DOI, mutation, wavelength and sequence fingerprint; a fresh database without those assays gets explicit unavailable-link reports while retaining independently cited evidence.

For an **existing installation**, take a backup and run the same migration/import sequence against a staging copy first. Review the reports, then use the intended deployment database settings to apply the committed migration and import. Collect static files into that deployment’s configured static root, and reload its application service through your normal deployment procedure. Do not run `makemigrations` on the server, flush data, or copy the development database over production. None of these production steps was run during this task. A rollback can remove/hide the beta UI while retaining the additive tables; preserve curator edits and use the backup for a full restoration.

## Maintaining the science

Django admin adds **Tuning proteins**, **Tuning evidence**, and read-only **Tuning audits**. Open an evidence entry to edit the comparator, conditions, original notation, source coordinates, citation roles and precise page/figure locator. Starter literature assertions require curator approval; the single-site builder can also approve unambiguous sequence comparisons automatically, with distinct provenance. An entry appears publicly only when the evidence, anchor protein, every citation and any linked assays are approved. Curator changes and citation changes are audited. Public endpoints cannot approve, update or delete records.

To add an entry, verify the exact source construct and numbering first. Record all mutations in a multi-site construct together. Retain incompatible conditions, uncertain identity and negative findings as context; do not subtract unrelated spectra. Literature relevance is not experimental evidence. The import artifact is `data/tuning/catalogue.json`; use a new release label for reviewed additions. Preserve existing keys. Check conflicts rather than forcing an overwrite.

Protein snapshots are deliberately fixed. Editing a released anchor sequence makes mapping fail closed until its reference alignment is reviewed and rebuilt. To reproduce the existing alignment (MAFFT 7.505):

```bash
mafft --quiet --thread 1 --localpair --maxiterate 1000 \
  data/tuning/reference-sequences.fasta > /tmp/reference-profile.fasta
# Compare with the released profile and rerun numbering tests before replacing it.
```

Run `python tools/verify_tuning_assets.py` to check the shipped files without network access. Source URLs and checksums are in `data/tuning/downloads.json`; scientific decisions and citations are in [tuning-source-notes.md](tuning-source-notes.md). Do not refresh pinned sequences or structures from “latest” automatically.

## Safeguards and practical limits

- MAFFT adds targets to the frozen reference alignment twice, with gap penalties 1.53 and 3.0. Disagreement withholds the target coordinate. Agreement is a diagnostic, not a probability of homology. Global identity <25% or target overlap <40% withholds a coordinate; local gaps/divergence and cross-family comparisons require review. These conservative beta heuristics are not a validated classifier.
- A request allows 20 proteins, each 30–2,000 residues; 60 KB FASTA; up to 100 evidence entries; a 20-second alignment budget. Private temporary files are deleted on success/error. No shell interpolation or sequence persistence is used. Avoid logging request bodies at the reverse proxy.
- Two filesystem locks cap concurrent alignments across processes on **one host**. The service account needs write access to `var/tuning-locks/`. Multiple hosts need a shared limiter or queue before increasing capacity. DRF also throttles anonymous requests at 12/minute; the default local-memory throttle is per worker. Configure trustworthy proxy/client-IP handling and an upstream rate limit for production.
- User structures stay in the browser; the chosen chain sequence is sent to the mapping endpoint. The first coordinate model is used, with an explicit chain selector. Complete polymer sequence is used when supplied; missing PDB sequence records can limit mapping. Unobserved residues never get invented coordinates. Uploaded structures are capped at 10 MB. WebGL failure leaves sequence mapping/exports usable.
- Family selection is user guidance. There is no automatic phylogenetic assignment, predicted target Δλmax, energy calculation or structural-quality guarantee.

## API and checks

`GET /api/tuning-sites/` returns the full approved catalogue, including contextual below-cutoff entries (`qualifies: false`). `GET /api/tuning-templates/` returns sequence snapshots and local structure links. `POST /api/tuning-mappings/` performs a stateless calculation:

```json
{"fasta": ">my_protein\nREPLACE_WITH_AMINO_ACIDS", "evidence_keys": ["spider-e181d"], "reference": "bovine", "target_family": "R_OPSIN"}
```

The placeholder is not a valid sequence. Optional fields are `custom_reference` (one FASTA) and `opsin_ids` (approved VPOD integer IDs). Unknown fields, unavailable evidence and below-cutoff measured selections are rejected. No request writes scientific records.

Run tests with `vpod_env`. On a SQLite-only development machine without the optional PostgreSQL driver, exclude the unused PostgreSQL alias in the test process:

```bash
python tools/test_tuning.py
```

Browser checks use the separately approved Playwright environment, not the runtime environment:

```bash
# If recreating the test environment, explicitly neutralize inherited pip targets.
python -m venv --system-site-packages /tmp/vpod-tuning-browser-env
env -u PIP_TARGET -u PIP_PREFIX -u PYTHONUSERBASE PIP_CONFIG_FILE=/dev/null \
  /tmp/vpod-tuning-browser-env/bin/python -m pip install --ignore-installed playwright==1.62.0
PLAYWRIGHT_BROWSERS_PATH=/tmp/vpod-browsers \
  /tmp/vpod-tuning-browser-env/bin/python -m playwright install chromium
# Start an isolated local server first; this checker does not start/reload services.
PLAYWRIGHT_BROWSERS_PATH=/tmp/vpod-browsers \
  /tmp/vpod-tuning-browser-env/bin/python tools/check_tuning_browser.py --url http://127.0.0.1:8000
PLAYWRIGHT_BROWSERS_PATH=/tmp/vpod-browsers \
  /tmp/vpod-tuning-browser-env/bin/python tools/check_tuning_inputs.py --url http://127.0.0.1:8000
```

`reports/tuning-verification.json` records the actual checks and limits. PostgreSQL, Safari/Firefox, physical touch devices and a production Gunicorn deployment require separate validation.
