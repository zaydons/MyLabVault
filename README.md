# MyLabVault 🔬

**Your personal health data, organized and accessible.**

MyLabVault is a comprehensive personal health data management system designed to help you track, analyze, and visualize your lab results over time. Upload PDF lab reports, automatically parse test data, and gain insights into your health trends through interactive charts and dashboards.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://docker.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-Framework-green.svg)](https://fastapi.tiangolo.com)

## ✨ Key Features

### 📄 **PDF Lab Report Processing**
- **Intelligent Parsing**: Automatically extract test results from LabCorp, Quest, and other major lab providers
- **Bulk Upload**: Process multiple PDF files simultaneously
- **Selective Import**: Choose which tests to import from each report
- **Duplicate Detection**: Automatically detect and prevent duplicate imports
- **Error Handling**: Robust parsing with fallback mechanisms for various PDF formats

### 📊 **Data Visualization & Analytics**
- **Interactive Dashboard**: Overview of health metrics with key statistics
- **Trending Charts**: Track test values over time using Chart.js
- **Panel-Based Organization**: Group related tests for better analysis
- **Abnormal Result Detection**: Automatic flagging of out-of-range values
- **Historical Comparisons**: Compare results across different time periods

### 🏥 **Health Data Management**
- **Multi-Patient Support**: Manage data for family members
- **Healthcare Provider Tracking**: Associate results with specific providers
- **Test Categorization**: Organized by medical panels (Lipid, Metabolic, CBC, etc.)
- **Reference Range Validation**: Automatic normal/abnormal classification
- **Search & Filter**: Advanced filtering across all results

### 🎨 **Modern User Interface**
- **Professional Design**: Built with AdminLTE 3.2.0 for a clean, medical-grade interface
- **Dark/Light Mode**: Toggle between themes with persistent user preferences
- **Responsive Layout**: Optimized for desktop, tablet, and mobile devices
- **Interactive Tables**: DataTables integration with search, sort, and pagination
- **Modal-Based Workflows**: Streamlined data entry and confirmation processes

### 🛡️ **Data Security & Privacy**
- **Local Storage**: All data stays on your infrastructure - no cloud dependencies
- **SQLite Database**: Lightweight, reliable, and private data storage
- **Docker Containerization**: Isolated environment with security controls
- **Health Check Monitoring**: Built-in application health monitoring

## 🚀 Quick Start

### Prerequisites
- **Docker** (20.10+) and **Docker Compose** (v2.0+)
- **Git** for cloning the repository

### Installation

#### Option 1: Using Pre-built Image (Recommended)

1. **Create docker-compose.yml**
   ```yaml
   version: '3.8'
   services:
     mylabvault:
       image: ghcr.io/zaydons/mylabvault:latest
       ports:
         - "8000:8000"
       volumes:
         - ./data:/app/data
       restart: unless-stopped
   ```
   
   *Optional: Add health monitoring for production deployments*
   ```yaml
   # Add these lines under mylabvault service for health monitoring:
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
         interval: 30s
         timeout: 10s
         retries: 3
         start_period: 40s
   ```

2. **Start the application**
   ```bash
   docker-compose up -d mylabvault
   ```

#### Option 2: Build from Source

1. **Clone the repository**
   ```bash
   git clone https://github.com/zaydons/MyLabVault.git
   cd MyLabVault
   ```

2. **Start the application**
   ```bash
   # Using the convenience script (recommended)
   ./start-dev.sh
   
   # Or manually with docker-compose
   docker-compose up -d mylabvault
   ```

3. **Access the application**
   - 🌐 **Web Application**: http://localhost:8000
   - 📚 **API Documentation**: http://localhost:8000/api/docs
   - 🔍 **Health Check**: http://localhost:8000/health

### First Steps
1. **Upload Your First PDF**: Go to "PDF Import" and upload a lab report
2. **Review Results**: Check the parsed data and select tests to import
3. **Explore Dashboard**: View your health metrics and trends
4. **Manage Providers**: Add your healthcare providers for better organization
5. **Customize Settings**: Set your preferred theme and UI preferences

## 🏗️ Architecture

### Technology Stack
- **Backend**: Python 3.11 + FastAPI + SQLAlchemy ORM
- **Frontend**: Server-side Jinja2 templates + AdminLTE 3.2.0
- **Database**: SQLite with Alembic migrations
- **PDF Processing**: pypdf + pdfplumber for intelligent parsing
- **UI Components**: Bootstrap 4 + DataTables + Chart.js + Material Design Icons
- **Containerization**: Docker with health checks and network isolation

### Project Structure
```
lablog/
├── app/                          # Application root
│   ├── api/                      # FastAPI backend
│   │   ├── models.py            # SQLAlchemy database models
│   │   ├── routers/             # API route handlers
│   │   │   ├── pdf_import.py    # PDF processing endpoints
│   │   │   ├── results.py       # Lab results management
│   │   │   └── pages.py         # Frontend page routes
│   │   └── services/            # Business logic
│   │       └── pdf_parser.py    # Advanced PDF parsing engine
│   ├── templates/               # Jinja2 HTML templates
│   │   ├── components/          # Reusable UI components
│   │   ├── dashboard.html       # Main dashboard interface
│   │   └── pdf-import.html      # PDF upload workflow
│   ├── data/                    # Persistent data storage
│   │   ├── mylabvault.db       # SQLite database
│   │   └── uploads/pdfs/       # Uploaded PDF files
│   └── alembic/                 # Database migrations
├── docker-compose.yml           # Container orchestration
└── start-dev.sh                # Quick start script
```

### Database Schema
- **Patients**: Personal information and demographics
- **Providers**: Healthcare providers and laboratories
- **Panels**: Test groupings (CBC, Metabolic, Lipid, etc.)
- **Labs**: Individual test definitions with reference ranges
- **LabResults**: Test results with values and metadata
- **PDFImportLog**: Import history and processing status
- **UserSettings**: UI preferences and application settings

## 📋 Usage Guide

### PDF Import Workflow
1. **Upload**: Drag & drop or select PDF lab reports
2. **Parse**: Automatic extraction of test data and metadata
3. **Review**: Preview parsed results with confidence indicators
4. **Select**: Choose specific tests to import (selective import)
5. **Import**: Save selected results to your database
6. **Track**: Monitor import history and processing status

### Data Management
- **View All Results**: Browse and filter all lab results
- **Individual Lab Analysis**: Detailed view with trend charts
- **Provider Management**: Add and organize healthcare providers
- **Panel Organization**: Group tests by medical categories
- **Patient Profiles**: Manage multiple family members

### Analytics & Reporting
- **Dashboard Overview**: Key health metrics and recent results
- **Trend Analysis**: Chart.js visualizations of test values over time
- **Abnormal Detection**: Automatic flagging of out-of-range results
- **Export Capabilities**: Download results for external analysis

### Data Persistence
- **Database**: `app/data/mylabvault.db` (SQLite)
- **Uploaded Files**: `app/data/uploads/pdfs/` (permanent storage)
- **Application Logs**: Docker container logs via `docker logs mylabvault`

### Backup & Recovery
```bash
# Backup your data
docker exec mylabvault cp -r /app/data /app/backup-$(date +%Y%m%d)

# Or backup from host
cp -r ./app/data ./backup-$(date +%Y%m%d)
```

## 🛠️ Development & Management

### Container Management
```bash
# View application status
docker-compose ps mylabvault

# View logs
docker logs mylabvault -f

# Restart application
docker-compose restart mylabvault

# Stop application
docker-compose down

# Rebuild and restart
docker-compose down && docker-compose build && docker-compose up -d mylabvault

# Access container shell
docker exec -it mylabvault /bin/sh
```

### API Development
- **Interactive Documentation**: http://localhost:8000/api/docs (Swagger UI)
- **Alternative Docs**: http://localhost:8000/api/redoc (ReDoc)
- **OpenAPI Spec**: http://localhost:8000/openapi.json

### Key Endpoints
```
GET  /api/results/              # Retrieve lab results with filtering
POST /api/pdf/upload           # Upload single PDF lab report  
POST /api/pdf/bulk-upload      # Upload multiple PDF files
POST /api/pdf/confirm          # Confirm and process PDF import
GET  /api/pdf/history          # Get PDF import history
GET  /api/labs/                # Manage lab test definitions
GET  /api/providers/           # Manage healthcare providers
GET  /api/patients/            # Manage patient profiles
```

## 🔍 Troubleshooting

### Common Issues

**PDF Import Problems**
```bash
# Check if PDF contains readable text
docker exec mylabvault python -c "import pdfplumber; print('PDF readable' if pdfplumber.open('/app/data/uploads/pdfs/yourfile.pdf').pages else 'PDF not readable')"

# View detailed import logs
docker logs mylabvault | grep "PDF"
```

**Database Issues**
```bash
# Check database connectivity
docker exec mylabvault python -c "from api.database import engine; print('DB OK' if engine.connect() else 'DB Error')"

# Reset database (⚠️ WARNING: This will delete all data)
docker exec mylabvault rm /app/data/mylabvault.db
docker-compose restart mylabvault
```

**Application Not Starting**
```bash
# Check Docker resources
docker system df

# Verify health status
docker-compose ps mylabvault

# View startup logs
docker logs mylabvault --tail 50
```

### Performance Optimization
- **Large PDF Files**: Files over 10MB may take longer to process
- **Bulk Imports**: Process in batches of 10-20 files for optimal performance
- **Database Size**: Regular cleanup of old import logs recommended for large datasets

### Getting Help
1. Check the application logs: `docker logs mylabvault`
2. Verify Docker resources and connectivity
3. Review the API documentation at `/api/docs` for endpoint details
4. Check file permissions in `app/data/` directory

## 📄 License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.

## 🏥 Medical Disclaimer

MyLabVault is a personal data management tool and is not intended to provide medical advice. Always consult with qualified healthcare professionals regarding your medical data and health decisions. This software is provided for informational and organizational purposes only.

---

**MyLabVault** - Take control of your health data with privacy, security, and intelligence.