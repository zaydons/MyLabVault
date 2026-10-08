# Tests

API tests (pytest + FastAPI's TestClient) and browser tests (Playwright, with an axe-core
accessibility scan in light and dark mode). Each test starts from an empty database and
uploads folder in a temporary directory, so nothing touches real data.

```bash
pip install -r app/requirements.txt -r requirements-dev.txt
python -m playwright install chromium                         # browser for the browser tests
npm install --prefix tests/.node axe-core@4.10.2              # accessibility checker
python -m pytest                                              # everything
python -m pytest tests/test_pdf_import.py -k summary          # one area
```

- `conftest.py`: temporary data folder and database (`MYLABVAULT_DATA_DIR`, `DATABASE_URL`), and the
  `client`, `fresh_client` and `ai` fixtures.
- `ai_mock.py`: Bedrock replaced by a canned tool call; no AWS access or cost.
- `pdfs.py`: generated lab reports and health summaries. **Never commit real reports**; they
  contain personal health data.

CI runs these on every pull request (`.github/workflows/test.yml`), and `build.yml` publishes an
image only when they pass.
