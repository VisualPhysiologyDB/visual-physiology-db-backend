# Landing-page 400 and explorer styling fix — September 9, 2026

## Confirmed cause and deployed correction

The earlier settings edit changed the default `ALLOWED_HOSTS` from `visphys.eemb.ucsb.edu, localhost, 127.0.0.1` to only `localhost, 127.0.0.1`. The existing `.env` has `DEBUG=False` and no `ALLOWED_HOSTS` override. `django_errors.log` repeatedly recorded `Invalid HTTP_HOST header: 'visphys.eemb.ucsb.edu'`. A request to the running Gunicorn with that Host header returned 400 before the fix.

`vpod_backend/settings.py` now preserves the established hostname when the environment setting is absent or blank. Explicit comma-separated overrides are trimmed and respected. Unknown hosts remain rejected. `.env.example` and the README now include the existing hostname and explain the override/reload behavior; the actual `.env` was not modified.

The service is `/etc/systemd/system/gunicorn.service`, owned by `oakley`, running eight workers from `/home/oakley/miniconda3/envs/vpod_env/bin/gunicorn` on `127.0.0.1:8000`. That environment is Python 3.10.19, Django 5.2.8 and DRF 3.18.0. `sudo -n systemctl restart gunicorn` could not execute because sudo requires a password. The verified master process belonged to the current user, so Gunicorn's supported `HUP` handling was used for a graceful worker reload. Its locally installed source was checked to confirm that this reloads configuration and application workers. The service has no `ExecReload`; `systemctl start` also does not reload an already-running service.

After isolated browser verification, `collectstatic --noinput` ran using that deployment Python into the existing `staticfiles` directory, without `--clear`. The previous core static assets/manifest were backed up to `/tmp/vpod-landing-static-before.tar.gz`. Gunicorn was gracefully reloaded again to pick up the updated template and hashed asset manifest. The running master PID stayed 1026817. No dependency installations, migrations, imports or scientific data changes were performed on the live database in this turn. The user's previously applied database was inspected read-only and already had migration `0010`.

## Restored appearance

The baseline was the original `templates/index.html` at revision `374306a0df1ba556277a2a6c36f68ee62261a4aa`, before the implementation edits. Restored elements include:

- Italic organism names, muted phylum/gene-family subtitles, gene-family pill badges and monospace accessions.
- Uppercase spaced table headings, original desktop cell padding, row hover transitions and DOI external-link icons.
- Experimental/inferred badges, wavelength-colored measurement values with units/error values, the spectral background and wavelength-colored histogram bars with rounded corners.
- Reference approval badges and the original contribution-tier selected styling. The acuity tab now has a matching eye icon.

The original card/navigation design is retained. Mobile navigation wraps at readable sizes instead of shrinking controls to tiny text. New references and acuity retain metadata/details, safe DOM rendering, full-value access and correct units. Acuity and year charts remain nonspectral. Histogram bins/counting rules were not reverted when restoring colors.

## Executed checks

- **35 backend tests passed** in the actual deployment Python environment. New regressions cover absent/blank host overrides, whitespace/explicit overrides, production Host returning 200 with `DEBUG=False`, and an unrelated Host returning 400. Tests used isolated SQLite; the unused PostgreSQL alias was excluded in that test process because its optional driver is not installed in this environment. A standard test invocation initially encountered that missing optional driver; no production package was installed to work around it.
- JavaScript data checks passed: histogram boundaries, integer counts, unknowns, duplicate counting, safe links and CSV handling.
- **11 existing browser-check groups passed** against `/tmp/vpod-landing-browser.sqlite3`, including real tables/charts, filters, all 549 acuity CSV/copy rows, a pending submission on that isolated database, malicious/paginated reference fixtures, error handling and mobile full-text access. [Results](landing-browser-verification.json).
- **3 style-check groups passed**, comparing original/restored pages with local fixture data. Computed typography, badges, colors, chart palettes, error display, DOI icons, contribution selection and mobile layout were checked. Original/restored desktop and mobile screenshots were inspected. A first style assertion was corrected to inspect text content, because CSS intentionally transforms badge display to uppercase. [Results](landing-style-verification.json).
- `check`, `makemigrations --check --dry-run`, and `git diff --check` passed. There are no new migrations for this fix.
- Actual Gunicorn after reload: landing page **200**, unexpected Host **400**, all five public data endpoints **200**. The served hashed JavaScript returned **200** and exactly matched `static/core/explorer.js`; the public HTML references the same new hash. [Runtime results](landing-runtime-verification.json).

## Separate HTTPS certificate issue

The public Let's Encrypt certificate was issued June 3, 2026 and expired **September 1, 2026 at 02:25:42 UTC**, before these September 9 code changes. A normal HTTPS request fails certificate verification (`curl` exit 60). One unauthenticated request bypassing certificate verification **for diagnosis only** returned **200 through the public reverse proxy**, confirming the application 400 is fixed. Certificate metadata was inspected without sending credentials. No TLS configuration or certificate was changed; certificate renewal remains a separate server administration action. Disabling validation is not an operational fix.

## Reproduction

The temporary development server used `DEBUG=True`, port 8765 and `/tmp/vpod-landing-browser.sqlite3`, never the live database. It was stopped after verification; production Gunicorn remained active. The browser commands were:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/vpod-browsers /tmp/vpod-venv/bin/python tests/explorer_style_checks.py
PLAYWRIGHT_BROWSERS_PATH=/tmp/vpod-browsers VPOD_BROWSER_REPORT=reports/landing-browser-verification.json /tmp/vpod-venv/bin/python tests/browser_checks.py
```

For future updates, activate the intended deployment environment, collect changed static assets, and reload/restart the running Gunicorn workers. `sudo systemctl start gunicorn` alone has no effect when the service is already active. These particular code/static changes have already been applied to the running server.
