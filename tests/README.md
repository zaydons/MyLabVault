# Tests

API tests (pytest + FastAPI's TestClient) and browser tests (Playwright, with an axe-core
accessibility scan in light and dark mode). Each test starts from an empty database and
uploads folder in a temporary directory, so nothing touches real data.

```bash
pip install -r app/requirements.txt -r requirements-dev.txt
python -m playwright install chromium                         # browser for the browser tests
npm install --prefix tests/.node axe-core@4.10.2              # accessibility checker
python -m pytest                                              # everything
python -m pytest --ignore=tests/test_browser.py               # fast tests only (a few seconds)
python -m pytest -n auto tests/test_browser.py                # browser tests, in parallel
python -m pytest tests/test_pdf_import.py -k summary          # one area
```

- `conftest.py`: temporary data folder and database (`MYLABVAULT_DATA_DIR`, `DATABASE_URL`), and the
  `client`, `fresh_client` and `ai` fixtures.
- `ai_mock.py`: Bedrock replaced by a canned tool call; no AWS access or cost.
- `pdfs.py`: generated lab reports and health summaries. **Never commit real reports**; they
  contain personal health data.

CI (`.github/workflows/test.yml`) runs two jobs on each pull request:
- **Tests**: everything except the browser tests, always.
- **Browser & accessibility**: `test_browser.py` in parallel, when the pull request changes pages,
  scripts, styles, routes (`app/templates/`, `app/static/`, `app/api/routers/`), requirements or these
  tests; otherwise it passes without running them. It also runs on manual runs and every Monday on `main`.

`build.yml` runs the fast tests again before publishing an image (the pull request already ran the
browser tests). In browser tests, wait for a dialog with `modal_closed()` before opening another:
Bootstrap ignores opening one while the last is still fading out.
