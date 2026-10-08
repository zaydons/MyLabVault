# Working on MyLabVault

## Repository rules
- **Always open a pull request and merge it** for every change. Work on a branch, push it, open the PR against `main`, and merge it once your checks pass. Every push to `main` publishes a new Docker image.
- **Track work in GitHub issues.** Open an issue for each piece of outstanding work (bugs, code-scanning alerts, planned features). A PR that resolves one says `Closes #<number>` in its description so merging it closes the issue; mention the issue in commits too.
- **Test every change.** Add or update tests in `tests/` with each feature or fix and run `python -m pytest` before pushing (see `tests/README.md`). CI runs them on every PR, and an image is only published when they pass. Never commit real lab reports or health data as fixtures; generate test PDFs in `tests/pdfs.py`.
- **Keep README.md up to date.** When a change affects anything the README describes (features, setup, configuration, endpoints, project structure, data and backups, troubleshooting), update `README.md` in the same PR. Update `deploy/truenas/README.md` too when the change affects running the app on TrueNAS.

## Project notes
- App code lives in `app/` (FastAPI + Jinja2 templates + SQLite). The Docker image is built from `app/` only.
- Database changes need an Alembic migration in `app/alembic/versions/`, written to be idempotent: `Base.metadata.create_all` runs before migrations at startup, so check for existing tables and columns first.
- Front-end libraries are bundled in `app/static/vendor/`; pages must not load scripts, styles or fonts from CDNs. To upgrade one, change its version in `scripts/vendor-assets.sh` and rerun it.
- Text shown in the UI should meet WCAG AA contrast in light and dark mode, and status must never be conveyed by color alone.
