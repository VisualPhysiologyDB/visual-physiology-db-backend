# Browse tuning sites and mutations

The beta mapper now opens **by site**. Expand a site to see its mutations, then expand a mutation to see the individual studies and references.

- **Map site only** locates the corresponding residue without prescribing a substitution. It returns one row per site and target, with all currently public supporting/contextual evidence at that site.
- **Select a mutation** selects its visible studies. Expand the study list to select only particular observations. Partly selected lists show an indeterminate checkbox.
- A **combined construct** remains one experiment: selecting it maps all its sites and preserves its combined shift. The tool does not assign that shift independently to each site.
- Site-only and mutation selections can coexist. CSV and session exports retain which type was selected. Both work with structure highlighting and saved views.

Changing filters removes selections that are no longer visible. Changing reference numbering clears site-only selections; mutation/study selections retain their source identities. Numeric searches such as `83` search the displayed site position. Species, notation, DOI and reference text searches still work.

## What the amino-acid messages mean

The results assess each input sequence separately:

| Message | Meaning |
|---|---|
| Starting residue matches | The target has the required starting amino acid; target-numbered notation is provided. |
| Different starting residue | Applying the same replacement would be a different, unvalidated substitution. No equivalent mutation is silently suggested. |
| Replacement already present | That amino acid is already present, so the selected substitution would make no change. |
| Ambiguous amino acid / cannot assess | The residue or mapped coordinate is insufficiently resolved. |
| Position only | No particular substitution was selected. |

This checks sequence compatibility. It does **not** establish functional suitability, statistical significance, comparable assay conditions, or a transferable spectral shift. Mapping assessment remains a separate column; cross-family, divergent and ambiguous alignments keep their warnings. Input sequences are not modified or stored.

## How sites are grouped

A shared number alone does not establish a shared site. The catalogue uses the existing fixed reference alignment and source-protein sequence snapshots to express coordinates in bovine, squid, spider or human-LWS numbering. For example, human-LWS position 180 groups with bovine position 164, rather than bovine position 180.

Opsin families remain separate. Two alignment settings must agree on a source-to-reference coordinate. Missing or uncertain correspondences remain separate groups labelled with their exact source protein and numbering. The grouping is an alignment-based correspondence, not proof of an identical tuning mechanism across proteins or subtypes. The original mutation notation, biological context, measurements and citations stay attached to each study.

Custom-reference mapping still works. Browsing uses bovine or squid numbering according to the chosen context; custom coordinates are calculated when mapping.

## Install and maintain

No new dependency or database migration is required for this feature. Keep the migrations from earlier updates applied. Use the serving database configuration and `vpod_env`:

```bash
conda activate vpod_env
cd /home/oakley/visual-physiology-db-backend
python manage.py index_tuning_sites
python manage.py collectstatic --noinput
```

Reload your configured Gunicorn service through your normal deployment process. Development verification used an isolated SQLite copy; the serving database and services were not changed.

`index_tuning_sites` writes `data/tuning/site-index.json`. The supplied file covers the public catalogue inspected during development. It stores derived public-source coordinate maps keyed by protein-sequence and reference-profile hashes. It contains no user input sequences and changes no database records. It reuses the existing MAFFT cache and writes the index atomically.

Applied `build_tuning_candidates` runs refresh this index automatically. After manually publishing/editing catalogue evidence or adding a new source protein, rerun `index_tuning_sites`. `--output PATH` chooses another output; `VPOD_TUNING_SITE_INDEX_PATH` configures the file read by the website and builder. A custom path must be readable by the web process and writable by the indexing command.

Page requests never launch indexing or alignment. Missing/stale index entries fall back to source numbering; mapping and other explorer tabs remain available. Failed index alignments are reported for inspection. Refresh the index after changing the fixed profile or aligner; if the index is regenerated on another machine, deploy that artifact with the matching code/profile.

The API remains compatible with existing mutation requests using `evidence_keys`. A site-only request supplies `site_keys` from `/api/tuning-sites/` → `site_groups`. Responses add `selection_kind`, `mutation_status`, `mutation_note`, `target_mutation`, `mapped_sites` and `selected_site_keys`. Only public evidence can support selections. The existing limits remain: 20 targets, 100 combined selections, and at most 20 dynamic source proteins per mapping.

Run `python tools/test_tuning.py` for backend checks. The browser scripts are `tools/check_tuning_browser.py` and `tools/check_tuning_sites_browser.py`; run them with the approved separate Playwright environment and an isolated local server. See [verification results](../reports/tuning-site-groups-verification.json).
