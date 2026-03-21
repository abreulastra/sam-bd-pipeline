# Setup Guide

This project pulls federal opportunities from the **SAM.gov Opportunities API** and writes filtered results into a Google Sheet.

To run it, you need:

1. A **SAM.gov API key**
2. A **Google Sheet**
3. A **Google Cloud service account (JSON key)**
4. Environment variables or GitHub Actions secrets

---

## 1. Get a SAM.gov API Key

This project uses the **SAM.gov Get Opportunities Public API**.

### Steps

1. Go to: https://sam.gov
2. Log in to your account
3. Click your username (top right)
4. Open **Account Details**
5. Find **Public API Key**
6. Generate or copy your key

### Notes

- Endpoint used:

https://api.sam.gov/opportunities/v2/search

- Required parameters:
- `api_key`
- `postedFrom`
- `postedTo`
- Dates must be in:

MM/dd/yyyy


---

## 2. Create the Google Sheet

Create a Google Sheet where results will be stored.

### Steps

1. Go to: https://sheets.google.com
2. Create a new spreadsheet
3. Copy the URL

Example:


https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit#gid=0


### Required tabs

The script will use:

- `Opportunities`
- `RunLog`

If they don’t exist, they will be created automatically.

---

## 3. Set up Google Cloud

This project uses a **service account** to access Google Sheets programmatically.

### Step 3.1 — Create a project

1. Go to: https://console.cloud.google.com
2. Click **Select Project**
3. Click **New Project**
4. Give it a name
5. Click **Create**

---

### Step 3.2 — Enable APIs

Enable these APIs:

- Google Sheets API
- Google Drive API

Steps:
1. Go to **APIs & Services → Library**
2. Search and enable:
   - “Google Sheets API”
   - “Google Drive API”

---

### Step 3.3 — Create a Service Account

1. Go to:

IAM & Admin → Service Accounts

2. Click **Create Service Account**
3. Give it a name
4. Click **Create and Continue**
5. Skip optional steps
6. Click **Done**

---

### Step 3.4 — Create JSON Key

1. Click the service account you created
2. Go to **Keys**
3. Click:

Add Key → Create new key → JSON

4. Download the file

⚠️ This is the only time you can download it.

---

## 4. Share the Google Sheet

Open your JSON file and find:

```json
"client_email": "your-service-account@your-project.iam.gserviceaccount.com"
Steps

Open your Google Sheet

Click Share

Add the service account email

Set role to Editor

Save

5. Local Environment Setup

Create a .env file in the root of the repo:

SAM_API_KEY=your_sam_api_key
SHEET_URL=https://docs.google.com/spreadsheets/d/YOUR_ID/edit#gid=0
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account", ...}
Important

The JSON must be one line

Do NOT commit .env

.gitignore should include .env

6. GitHub Actions Setup

For automated runs, store secrets in GitHub:

Go to:

Settings → Secrets and variables → Actions

Create these:

Name	Description
SAM_API_KEY	Your SAM.gov API key
SHEET_URL	Your Google Sheet URL
GOOGLE_SERVICE_ACCOUNT_JSON	Full JSON (one line)
7. Run Locally
python src/main.py
Expected result

New opportunities inserted at top of sheet

Run logged in RunLog

8. Common Errors
Missing environment variable
Missing required environment variable

Fix:

Check .env or GitHub secrets

Spreadsheet not found
gspread.exceptions.SpreadsheetNotFound

Fix:

Share sheet with service account email

Verify correct SHEET_URL

SAM API 401 / 403

Fix:

API key invalid or expired

Regenerate key in SAM.gov

JSON credential error
Could not deserialize key data

Fix:

JSON not properly formatted

Must be one line in .env / secrets

9. Security Notes

Never commit API keys

Never commit service account JSON

Use .env locally

Use GitHub Secrets in production

Rotate keys if exposed

Summary
Component	Purpose
SAM API Key	Access federal opportunities
Google Sheet	Store results
Service Account JSON	Authenticate to Google
GitHub Secrets	Secure automation

---

## Optional: README snippet (to link this)

Add this to your `README.md`:

```markdown
## Setup

This project requires:

- SAM.gov API key  
- Google Sheet  
- Google Cloud service account  

Full setup guide: [docs/setup.md](docs/setup.md)
