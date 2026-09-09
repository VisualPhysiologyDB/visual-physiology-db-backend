# Literature discovery: everyday guide

**Search → private literature inbox → your decision → manual data entry.**

VPOD checks Europe PMC and Crossref for papers about opsins, heterologous expression, MSP/SCP, ERG and visual acuity. It saves suggested papers in Django admin. You remain responsible for deciding relevance, checking the paper and entering scientific measurements. No AI or paid model is involved.

## Review papers

In `/admin/`, open **Literature inbox** and filter **Decision → New**. Open a paper, follow its source link, then choose:

| Decision | What happens |
|---|---|
| Accepted | Creates a **pending** Reference, or links an existing one without changing it. Follow the Reference link to check/correct metadata and separately approve publication. |
| Rejected | Requires a reason. Later searches keep this decision. |
| Deferred | Requires a reason; keeps the paper for later review. |

After accepting, use **Data status** to track *Needs data entry*, *Data entry complete* or *No data entry needed*. Acceptance does not enter measurements. To reconsider a rejected suggestion, change its decision and write a new explanation. Accepted items remain linked to their References; change publication status in References if necessary.

## Tune one file

Edit [`config/literature.json`](../config/literature.json). It contains no secrets.

| Setting | Default / easy change |
|---|---|
| `enabled` | `false` until you approve the pilot. Set `false` again to pause scheduled searching. |
| `interval_days` | `7`; use `14` for fortnightly or `30` for roughly monthly. |
| `max_new_candidates` | At most `40` new suggestions per run. Lower this to reduce your workload. |
| `max_unreviewed` | Stops adding papers when New + Deferred reaches `200`. Review that backlog to resume. |
| `any_terms` | A paper must contain at least one phrase from this group in its title/abstract. |
| `required_any` | If nonempty, it must also contain at least one of these phrases. |
| `exclude_terms` | Any matching phrase excludes a result. Start cautiously: exclusions can hide useful papers. |

Example: add `"dragonfly"` to the visual-acuity query’s `required_any` list. Phrases ignore case and hyphen differences; plurals and synonyms need their own entries. Copy an existing query to add a topic, giving it a unique `name`. The command checks the file for mistakes before searching.

The first search looks back 90 days. Subsequent searches overlap 30 days to catch delayed indexing. Dates refer to indexing, so an older publication can appear as a new discovery. Changes to search phrases start a fresh search window while preserving all review decisions.

## Preview and enable

Run from the repository with the application environment activated. Commands use the database selected by your environment; see the README before switching databases.

```bash
# Preview: creates JSON and a readable HTML report, with NO database changes.
python manage.py discover_literature --dry-run --output var/discovery/preview.json

# Explicitly populate the private inbox once, after reviewing the preview.
python manage.py discover_literature --apply
```

Migration **0011 is already generated**. Back up the chosen database, review and apply committed migrations before using the inbox:

```bash
python manage.py migrate --plan
python manage.py migrate
```

After the pilot is accepted, check the Python path, user and working directory in `ops/systemd/vpod-literature.service`. It loads the same `.env` as Django. Set `enabled` to `true`, then install the supplied units:

```bash
sudo cp ops/systemd/vpod-literature.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vpod-literature.timer
```

The timer checks daily around **02:00 America/Los_Angeles**; `interval_days` decides whether searching is due. A capped or failed run resumes on a later daily check. Changing the JSON needs no Gunicorn restart. To stop the timer: `sudo systemctl disable --now vpod-literature.timer`.

## Safeguards and maintenance

- Nothing is published automatically. Search labels never become verified measurement methods. Existing reference metadata and approval decisions are preserved.
- Canonical DOIs and provider IDs prevent repeat suggestions. All existing Reference statuses count, including rejected. Similar titles and preprint/final versions are flagged for review, not automatically merged.
- Requests use fixed provider hosts, timeouts, retries, caching and rate pacing. Page checkpoints survive failures; a local lock prevents overlapping runs. Retrieved text cannot run commands or change these rules.
- Each week: review the inbox and check **Discovery runs** for `PARTIAL` or `INTERRUPTED`. Open the report under `var/discovery/reports/`, or run `journalctl -u vpod-literature.service -n 50`. Repeating `--apply` resumes safely. Budget limits are adjustable in the JSON; a repeated offset-limit warning needs a narrower query/window.
- Back up the database and configuration. The database holds decisions, evidence and checkpoints. Old HTTP cache folders can be deleted; **do not delete review decisions** to clear a backlog. Review query usefulness periodically; provider coverage and missing abstracts can cause missed papers.

No scheduler was installed or activated during implementation. See [verification results](../reports/literature-verification.json) and the [readable pilot](../reports/literature-pilot.html).
