# SAM BD Pipeline

A lightweight pipeline that pulls federal contracting opportunities from **SAM.gov**, filters them based on business development criteria, and writes results to a Google Sheet.

This tool is designed to support **business development workflows** by automatically surfacing relevant opportunities from key U.S. government agencies.

---

## Overview

The pipeline:

- Queries the **SAM.gov Opportunities API**
- Filters opportunities by:
  - agency (e.g., State, USAID, DOL, DFC)
  - NAICS codes (e.g., services starting with `5`)
  - custom exclusions
- De-duplicates results
- Writes new opportunities to a Google Sheet
- Logs each run for traceability
- Runs locally or via **GitHub Actions (automated)**

---

## Key Features

- ✅ Automated SAM.gov data extraction  
- ✅ Customizable filtering logic (agency, NAICS, keywords)  
- ✅ Google Sheets integration  
- ✅ De-duplication across runs  
- ✅ Run logging  
- ✅ GitHub Actions automation (scheduled + manual runs)  

---

## Architecture

```text
SAM.gov API
     ↓
Python pipeline (src/main.py)
     ↓
Google Sheets (Opportunities + RunLog)
     ↓
(Optional) Future: scoring / email alerts / LLM analysis
Quick Start
1. Clone the repository
git clone https://github.com/abreulastra/sam-bd-pipeline.git
cd sam-bd-pipeline
2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate
3. Install dependencies
pip install -r requirements.txt
4. Configure environment

Create a .env file:

SAM_API_KEY=your_sam_api_key
SHEET_URL=your_google_sheet_url
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account", ...}
5. Run locally
python src/main.py
Setup (Detailed)

This project requires:

SAM.gov API key

Google Sheet

Google Cloud service account

👉 Full setup guide: docs/setup.md

Automation (GitHub Actions)

The pipeline runs automatically using GitHub Actions.

Trigger modes

Manual:

GitHub → Actions → Run workflow

Scheduled:

Weekdays via cron

Required repository secrets
Name	Description
SAM_API_KEY	SAM.gov API key
SHEET_URL	Google Sheet URL
GOOGLE_SERVICE_ACCOUNT_JSON	Service account credentials
Configuration

Core configuration is defined in:

config/settings.yaml

You can modify:

agency_codes

days_back

limit

max_records

exclude_naics

Use Case

This tool is intended for:

consulting firms

development organizations

BD teams tracking U.S. federal opportunities

analysts monitoring procurement pipelines

It is especially relevant for:

monitoring & evaluation (M&E / MEL)

labor and workforce programs

international development

migration and humanitarian sectors

Roadmap

Planned enhancements:

Opportunity scoring (rule-based or ML)

Email alerts / digests

LLM-based summarization and classification

Pipeline tracking (win probability, BD funnel)

Multi-source aggregation (beyond SAM.gov)

Security Notes

Do not commit API keys or service account JSON files

Use .env for local development

Use GitHub Secrets for automation

Rotate keys if exposed

License

MIT License

Author

Raúl Abreu
