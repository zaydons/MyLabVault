# MyLabVault 🔬

**Your personal health data, organized and accessible.**

MyLabVault is a self-hosted app for tracking your lab results and vitals over time. Upload PDF lab reports, review what was extracted, and follow your trends on a dashboard and charts. Everything is stored on your own server.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://docker.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-Framework-green.svg)](https://fastapi.tiangolo.com)

## ✨ Key Features

### 📄 **PDF Lab Report Import**
- **Automatic parsing**: Extracts test results from LabCorp, Quest and similar reports, including each result's reference range, the lab's flag (H/L), comments and fasting status
- **Collection date detection**: Uses the date next to "Collected", "Collection Date" or "Date Drawn", never the date of birth
- **Health summaries with several dates**: Patient-portal exports that list results from many draws in one table (such as an athenahealth *Ambulatory Summary* / *Data Portability* PDF) are read row by row; the review groups results under each collection date, and each date can be corrected before import
- **Already saved results**: Results already saved with the same value and date are marked and left unticked, so re-importing an updated "all time" summary only adds what's new
- **Provider matching**: The ordering provider on the report is selected automatically when it matches a saved provider, or offered as a one-click new provider
- **Review next to the PDF**: Each report's results are shown beside the PDF, with High/Low status, so you can check them before anything is saved
- **Correct before importing**: Edit any test name, result, unit or range, choose which saved test a row belongs to, or untick rows you don't want
- **Nothing silently wrong**: A missing collection date must be entered before import; results in a different unit than the saved test (e.g. mmol/L vs mg/dL) are flagged and kept as a separate test by default; rows that couldn't be read are listed for you to fill in
- **No copies of existing tests**: A result is never saved as a new copy of a test that already exists in that unit, including when several reports are imported together
- **Several reports at once**: Each file uploads and is read separately with its own progress, and a summary afterwards lists what was saved and what's out of range
- **Duplicate detection**: Re-uploading a report is recognised; unfinished imports can be reopened from the import history. A re-uploaded report with nothing saved from it yet is read again, so improvements to the reader apply (an earlier AI reading is kept to compare with)
- **Optional AI parsing**: Use Claude through Amazon Bedrock for scanned or unusual reports (off by default; see [deploy/truenas/README.md](deploy/truenas/README.md#optional-ai-parsing-with-amazon-bedrock))
- **Compare readings**: After *Re-scan with AI*, a side-by-side table shows what the built-in reader and the AI each found: values that differ, results only one of them caught, and a different collection date or provider. Rows in the review are marked too, and you can switch back to the built-in reader's results before importing

### 📊 **Dashboard and Charts**
- **Dashboard**: Your latest draw, how many tests are out of range right now, a *Needs attention* list, your latest vitals, and the latest value of every test with the change since the previous result
- **Trend charts**: Straight lines on a real time axis, the reference range shaded behind each point, and out-of-range results marked with a triangle and an H/L label
- **Charts page**: Opens on your most recent panel; link straight to a chart with `/charts?lab=<id>` or `/charts?panel=<id>`
- **Plain-language descriptions**: Each test's page explains what it measures (built in for about 70 common tests, or write your own)

### ❤️ **Vitals**
- **Record vitals**: Log weight, blood pressure and heart rate in one form from the dashboard or the Vitals page
- **Blood pressure categories**: Readings are labelled Normal, Elevated, Stage 1, Stage 2 or Hypertensive crisis (AHA adult categories)
- **More measurements**: Height, temperature, oxygen saturation, respiratory rate and blood glucose
- **Your units**: Choose lb or kg, feet and inches or cm, °F or °C and mg/dL or mmol/L (or *US units* / *Metric units* in one click) in *Settings → General*. Every reading, chart, change and typical range is shown in those units and new readings default to them; readings keep the unit they were entered in, so switching never alters your data

### 🏥 **Health Data Management**
- **Multiple patients**: Keep results for family members separately and switch between them from the top bar
- **Reference ranges**: Ranges can be low–high, greater than or less than, and each result can carry the range printed on its own report
- **Search**: Press `/` anywhere to search for a test or page
- **Merge duplicates**: *Settings → Clean up* finds lab tests, panels, units and providers saved more than once (such as *Albumin* and *Albumin (g/dL)*, *mg/dl* and *mg/dL*, or a provider with and without a middle initial). With AI enabled, *Ask AI to review* also catches differently worded names. You tick each group to merge (or *Tick all*), choose which item to keep and its name, and confirm; tests in units that can't be compared are never merged
- **Backup and restore**: Download all data, including uploaded PDFs, and restore it from *Settings → Backup & restore*

### 🎨 **Interface**
- **Settings in one place**: *General* (theme, date format), *Backup & restore*, *Clean up*, *About* (version, update status, what's stored) and a separate *Danger zone*; link straight to one with `/settings#backup` and the like
- **Responsive**: Works on phones as well as desktops; results tables keep the value, status and date visible on small screens
- **Accessible**: Meets WCAG AA text contrast in light and dark mode, works with the keyboard and screen readers, and respects the reduced-motion setting
- **Dark and light mode**, remembered between visits
- **Version and updates**: The footer shows the running version and an *Update available* badge when a newer image has been published

### 🛡️ **Privacy**
- **Self-hosted**: Data is stored in a SQLite database on your server
- **No third-party page requests**: Scripts, styles, icons and fonts are bundled with the app, so pages work without internet access
- **Outbound connections** happen only for optional AI features (Amazon Bedrock, off unless configured: PDF reading, and the duplicate review, which sends only the names of your tests, panels, units and providers) and the update check (GitHub, every few hours; set `MYLABVAULT_UPDATE_CHECK=false` to turn it off)
- **No login yet**: Anyone who can reach the app on your network can open it, so keep it on a trusted LAN

## 🚀 Quick Start

### Prerequisites
- **Docker** (20.10+) and **Docker Compose** (v2.0+)

### Installation

#### Option 1: Pre-built image (recommended)

1. **Create `docker-compose.yml`**
   ```yaml
   services:
     mylabvault:
       image: ghcr.io/zaydons/mylabvault:latest
       ports:
         - "8000:8000"
       volumes:
         - ./data:/app/data
       restart: unless-stopped
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
         interval: 30s
         timeout: 10s
         retries: 3
         start_period: 40s
   ```

2. **Start the application**
   ```bash
   docker compose up -d mylabvault
   ```

   The app runs as an unprivileged user (uid 1001) and takes ownership of `./data` on start. To use another user, for example your own so you can read the files, add `environment: { PUID: "1000", PGID: "1000" }`.

#### Option 2: TrueNAS SCALE

Install as a custom app via YAML on TrueNAS SCALE 24.10+. See [deploy/truenas/README.md](deploy/truenas/README.md).

#### Option 3: Build from source

1. **Clone the repository**
   ```bash
   git clone https://github.com/zaydons/MyLabVault.git
   cd MyLabVault
   ```

2. **Start the application**
   ```bash
   # Using the convenience script (recommended)
   ./start-dev.sh

   # Or manually
   docker compose up -d mylabvault
   ```

3. **Open the application**
   - 🌐 **Web application**: http://localhost:8000
   - 📚 **API documentation**: http://localhost:8000/api/docs
   - 🔍 **Health check**: http://localhost:8000/health

### First Steps
1. **Enter your name**: On first launch a welcome screen asks who the results are for (you can skip it and rename the patient later)
2. **Import a PDF**: Go to *PDF Import* and upload a lab report
3. **Review and import**: Check the results against the PDF, fix anything that's off, confirm the collection date and provider (often filled in for you), and import
4. **Explore**: Open the dashboard, a test's page or *Charts* to see trends
5. **Record vitals**: Use *Record vitals* on the dashboard

## 🔄 Updating

Every push to `main` publishes a new `ghcr.io/zaydons/mylabvault:latest` image, also tagged with its version (for example `2026.10.07.35`) and commit. Pull it and recreate the container:

```bash
docker compose pull mylabvault && docker compose up -d mylabvault
```

Database changes are applied automatically on startup. For TrueNAS, see [Updating](deploy/truenas/README.md#updating).

## 🏗️ Architecture

### Technology Stack
- **Backend**: Python 3.13, FastAPI, SQLAlchemy
- **Frontend**: Server-rendered Jinja2 templates with AdminLTE 3.2 (Bootstrap 4), DataTables, Chart.js and Material Design Icons, all served locally from `app/static/vendor`
- **Database**: SQLite with Alembic migrations
- **PDF processing**: pdfplumber and pypdf, plus optional Claude through Amazon Bedrock
- **Container**: Alpine-based Docker image with a health check, built by GitHub Actions after the tests pass; the app runs as an unprivileged user

### Project Structure
```
MyLabVault/
├── app/
│   ├── api/
│   │   ├── main.py               # FastAPI app, routers, welcome-screen redirect
│   │   ├── models.py             # SQLAlchemy models
│   │   ├── schemas.py            # Request/response schemas
│   │   ├── build_info.py         # Build version and update check
│   │   ├── logging_setup.py      # Log format, request IDs, audit lines
│   │   ├── routers/              # API and page routes (results, labs, vitals, pdf_import, search, setup, ...)
│   │   └── services/
│   │       ├── pdf_parser.py     # Built-in PDF parser
│   │       ├── summary_parser.py # Results tables with several collection dates (health summaries)
│   │       ├── import_review.py  # Review rows, test matching, reader comparison
│   │       ├── cleanup.py        # Duplicate suggestions and merging
│   │       ├── ai_parser.py      # Optional AI parsing (Amazon Bedrock)
│   │       └── test_descriptions.py  # Plain-language test descriptions
│   ├── templates/                # Jinja2 pages and components
│   ├── static/
│   │   ├── js/                   # Shared scripts (charts, modals)
│   │   └── vendor/               # Bundled front-end libraries and fonts
│   ├── alembic/                  # Database migrations
│   └── data/                     # Database and uploaded PDFs (mounted volume)
├── tests/                        # API and browser tests (pytest, Playwright, axe)
├── deploy/truenas/               # TrueNAS SCALE app and guide
├── scripts/vendor-assets.sh      # Re-downloads the bundled front-end libraries
├── docker-compose.yml            # Local development
└── start-dev.sh                  # Quick start script
```

### Database Schema
- **Patients**: Name, date of birth and gender
- **Providers**: Healthcare providers
- **Panels**: Test groupings (Lipid, Metabolic, CBC, ...)
- **Labs**: Test definitions with unit, reference range and optional description
- **LabResults**: Values with date, provider, the report's own range, flag, comment and fasting status
- **Vitals**: Weight, blood pressure and other measurements
- **PDFImportLog**: Import history and parsed data
- **UserSettings**: Preferences such as dark mode and first-run setup

## 💾 Data and Backups

- **Database**: `data/mylabvault.db` in the mounted volume (`/app/data` in the container)
- **Uploaded PDFs**: `data/uploads/pdfs/`
- **Portable backup**: *Settings → Backup & restore* downloads all data (a ZIP with PDFs, or JSON) and restores it, either added to your data or replacing it
- **File backup**: Copy the whole data folder while the app is stopped, or snapshot the dataset (TrueNAS)

```bash
docker compose stop mylabvault
cp -r ./data ./backup-$(date +%Y%m%d)
docker compose start mylabvault
```

## 🛠️ Development & Management

### Container Management
```bash
docker compose ps mylabvault          # Status
docker logs mylabvault -f             # Logs
docker compose restart mylabvault     # Restart
docker compose down                   # Stop
docker exec -it mylabvault /bin/sh    # Shell
```

### Logs
The container log has one line per event:

```
INFO    app        MyLabVault 2026.10.08.44 (a1b2c3d) ready: database=/app/data/mylabvault.db ai=on model=... update_check=on log_level=INFO
INFO    request    POST /api/pdf/confirm 200 412ms req=3f9a1c2e
INFO    audit      import.confirmed import_id=12 saved=37 new_tests=0 dates=3 provider_id=2 patient_id=1 req=3f9a1c2e
WARNING app        POST /api/pdf/confirm refused (400): report.pdf: Choose the provider for report.pdf. req=8d01b7aa
ERROR   app        Unhandled error in POST /api/pdf/upload (error ID 5c2e9f10) req=5c2e9f10
  File "/app/api/routers/pdf_import.py", line 240, in upload_pdf
    ...
```

- **request**: every page and API call, with its status and time (static files and health checks only at `DEBUG`).
- **audit**: every change. Imports, merges, data import/export/reset and deletions get their own line (`import.uploaded`, `import.confirmed`, `import.deleted`, `cleanup.merged`, `data.imported`, `data.exported`, `data.reset`, ...); other edits are logged as `api.change` with the route and IDs.
- **Errors**: a refused request logs why; an unexpected error logs its traceback and an error ID, which is also shown in the app's error message so the two can be matched. Each response carries the same ID in its `X-Request-ID` header.
- **What's not logged**: results, health values, names and search terms. Lines contain IDs, counts and kinds of change; error messages can mention a file or test name.
- **AI**: each Bedrock call logs its duration and token use.
- **Settings**: `MYLABVAULT_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default `INFO`) and `MYLABVAULT_LOG_FORMAT=json` for one JSON object per line (for Loki, Graylog and similar).

### Tests
API and browser tests live in `tests/` and run on every pull request; an image is only published when they pass.

```bash
pip install -r app/requirements.txt -r requirements-dev.txt
python -m playwright install chromium
npm install --prefix tests/.node axe-core@4.10.2
python -m pytest
```

See [tests/README.md](tests/README.md) for what's covered and how test data is generated.

### Updating bundled front-end libraries
Versions are pinned in `scripts/vendor-assets.sh`. Change a version, run the script and test the UI; it rewrites `app/static/vendor` and its `SHA256SUMS`.

### API
- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc

### Key Endpoints
```
GET  /api/results/             # Lab results
POST /api/results/             # Add a result
POST /api/pdf/upload           # Upload one PDF (add ?ai=true to use AI parsing)
POST /api/pdf/bulk-upload      # Upload several PDFs
POST /api/pdf/rescan-ai/{id}   # Re-parse a pending import with AI (keeps the built-in reading to compare)
POST /api/pdf/{id}/switch-reading  # Switch a pending import between the built-in and AI readings
GET  /api/pdf/review/{id}      # Review data for an earlier upload (rows, matches, issues)
GET  /api/pdf/{id}/file        # The uploaded PDF
POST /api/pdf/confirm          # Import selected rows (with optional edits and test choices)
POST /api/pdf/batch-confirm    # Import several reports at once
GET  /api/pdf/history          # Import history
GET  /api/labs/                # Lab test definitions
GET  /api/vitals/              # Vitals (filter with patient_id, vital_type)
GET  /api/vitals/types         # Vital types, their units and the preferred unit for each
GET  /api/search/?q=           # Quick search for tests and pages
GET  /api/cleanup/suggestions  # Likely duplicate tests, panels, units and providers
POST /api/cleanup/suggestions/ai  # The same, plus an AI review of the names
POST /api/cleanup/merge        # Merge the groups the user confirmed
GET  /api/patients/            # Patients
GET  /api/providers/           # Providers
POST /api/settings/export      # Export all data
GET  /version                  # Running version and build
GET  /api/update-check         # Whether a newer image has been published
```

## 🔍 Troubleshooting

**A PDF imports no tests**
- Scanned reports have no text to read. Turn on AI parsing and use *Re-scan with AI*, or add the results by hand.

**The AI read some values differently**
- Open *Compare the built-in reader with the AI* on the review screen to see each difference, check them against the PDF, and correct a row or switch back to the built-in reader's results.
- Check the logs: `docker logs mylabvault | grep -i pdf`

**The wrong collection date was detected**
- Change the *Collection date* field on the review screen before importing. If no date was found, the field is highlighted and import waits until you enter one.
- For a health summary with several dates, each date is a heading above its results; change it there.

**A PDF has results from many dates (a health summary or results history)**
- Upload it as usual. Results are grouped by collection date and each is saved on its own date. If the built-in reader doesn't recognise the layout, use *Re-scan with AI*, which also reads a date for each result.

**The same test appears more than once (e.g. "Albumin" and "Albumin (g/dL)")**
- Use *Settings → Clean up*: tick the group, choose the test to keep and its name, and confirm. All results move to the kept test.

**A result was saved under the wrong test**
- On the review screen, each row has a *Save as* choice: pick the right saved test, or *New test*. Results in a different unit than the saved test default to a new test.

**The app doesn't show a change you just deployed**
- Compare the version in the footer with the latest build. Pull the image again and recreate the container (see [Updating](#-updating)).

**Start over with an empty database** (⚠️ deletes all data)
- Use *Settings → Danger zone → Reset all data*, or stop the app and delete `data/mylabvault.db`.

**An error message shows an error ID**
- Search the log for it: `docker logs mylabvault 2>&1 | grep -A 15 <error ID>` shows the request and the traceback. On TrueNAS, open the app's **Logs**.

**The application doesn't start**
```bash
docker compose ps mylabvault
docker logs mylabvault --tail 50
```

## 📄 License

This project is licensed under the MIT License; see [LICENSE.md](LICENSE.md). Bundled front-end libraries keep their own licenses, listed in [app/static/vendor/README.md](app/static/vendor/README.md).

## 🏥 Medical Disclaimer

MyLabVault is a personal data management tool and is not intended to provide medical advice. Test descriptions, reference ranges and blood pressure categories are general information. Always consult qualified healthcare professionals about your results and health decisions.

---

**MyLabVault** - Take control of your health data, on your own server.
