"""
CareFlow Google Sheets Integration Setup
Creates a template sheet and enables real logging.
"""

import sys
sys.path.insert(0, '/Users/yamin/Downloads/careflow_complete')

print("""
╔═══════════════════════════════════════════════════════════════════╗
║        CareFlow Google Sheets Integration Setup                   ║
╚═══════════════════════════════════════════════════════════════════╝

To enable automatic logging to Google Sheets, follow these steps:

STEP 1: Create a Google Sheet
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Go to: https://sheets.google.com
2. Click "+" (Create new spreadsheet)
3. Name it: "CareFlow-Automation-Log"
4. Add these columns to Sheet1:

   A: Timestamp
   B: Workflow
   C: Customer ID
   D: Organization
   E: Action Type
   F: Details
   G: Status
   H: Email Sent To

5. Format Header (Row 1):
   - Make it bold
   - Add light gray background

6. Copy the Sheet ID from the URL:
   docs.google.com/spreadsheets/d/{SHEET_ID_HERE}/edit
   
   Save the ID: _________________________________


STEP 2: Enable Google Sheets API
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Go to: https://console.developers.google.com/
2. Create a NEW PROJECT:
   - Click "Select a Project" (top left)
   - Click "NEW PROJECT"
   - Name: "CareFlow"
   - Create

3. Enable Google Sheets API:
   - Search for "Google Sheets API"
   - Click "Enable"

4. Create Service Account:
   - Go to "Credentials" (left menu)
   - Click "Create Credentials" → "Service Account"
   - Fill in:
     * Service account name: careflow-automation
     * Click "Create and Continue"
   - Skip optional steps, click "Done"

5. Get the Key:
   - Click the service account name
   - Go to "Keys" tab
   - Click "Add Key" → "Create new key"
   - Choose "JSON"
   - Click "Create"
   - A JSON file downloads

6. Save the JSON file:
   - Save to: /Users/yamin/Downloads/careflow_complete/google-credentials.json
   - Keep it safe!


STEP 3: Share Sheet with Service Account
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Open the JSON file you just downloaded
2. Find the "client_email" field (looks like: xxxxx@xxxxx.iam.gserviceaccount.com)
3. Copy that email address

4. Go back to your Google Sheet
5. Click "Share" (top right)
6. Paste the service account email
7. Give it "Editor" access
8. Click "Share"


STEP 4: Update .env File
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Add these to your .env file:

GOOGLE_SHEETS_ID=your_sheet_id_from_step_1
GOOGLE_CREDENTIALS_PATH=google-credentials.json


STEP 5: Test It Works
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After completing steps 1-4, run:

    python3 test_integrations.py

This will:
✅ Test Gmail sending
✅ Test Google Sheets writing
✅ Show any errors


STEP 6: Enable in n8n Workflows
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Open n8n: http://localhost:5678 (admin/careflow123)
2. Import workflows from: automation/n8n_workflows/
3. For each workflow:
   - Edit the Gmail node → set your email credentials
   - Edit the Google Sheets node → set your sheet ID and credentials
   - Click "Save" and "Activate"


RESULT: Fully Automated! 🚀
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Once configured:
✅ Workflows send real emails to imteshar.ahmed@g.bracu.ac.bd
✅ Every action logged to your Google Sheet automatically
✅ Dashboard shows live data
✅ Fully hands-off automation

Questions?
- Check API error logs: n8n execution history
- Verify credentials: .env file
- Test manually: run test_integrations.py
""")
