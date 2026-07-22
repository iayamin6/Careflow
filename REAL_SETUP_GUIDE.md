# CareFlow Real Integration Setup

## Step 1: Gmail Setup (Email Sending)

### 1.1 Enable Gmail App Passwords
1. Go to: https://myaccount.google.com/apppasswords
2. Select **Mail** and **Windows Computer** (or your device)
3. Google will generate a 16-character **App Password**
4. Copy this password

### 1.2 Add to `.env` file
```
GMAIL_ADDRESS=your_email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

### 1.3 Update n8n Gmail node
In n8n workflow, the Gmail node needs:
- **Email**: your_email@gmail.com
- **App Password**: (paste the 16-char password)

**Result**: Emails from workflows will be sent to your inbox ✅

---

## Step 2: Google Sheets Setup (Logging Activity)

### 2.1 Create a Google Sheet
1. Go to: https://sheets.google.com
2. Create new blank spreadsheet
3. Name it: `CareFlow-Logs`
4. Copy the sheet ID from URL: `docs.google.com/spreadsheets/d/{SHEET_ID}/edit`

### 2.2 Create columns
```
Column A: Customer ID
Column B: Organization
Column C: Action Type (Health Check, Re-engagement, Churn Alert, etc)
Column D: Status (Critical, At Risk, High Churn, etc)
Column E: Details (Health score, churn %, reason)
Column F: Timestamp
Column G: Email Sent
```

### 2.3 Add Google Sheets API
1. Go to: https://console.developers.google.com/
2. Create new project: "CareFlow"
3. Enable **Google Sheets API**
4. Create **Service Account**
5. Download JSON credentials file
6. Save to: `/Users/yamin/Downloads/careflow_complete/google-credentials.json`

### 2.4 Add to `.env`
```
GOOGLE_SHEETS_ID=your_sheet_id_here
GOOGLE_CREDENTIALS_PATH=google-credentials.json
```

### 2.5 Update n8n Google Sheets node
In n8n workflow:
- **Spreadsheet ID**: (paste your sheet ID)
- **Credentials**: (select service account JSON)
- **Sheet**: "Sheet1"
- **Mode**: "Append"

**Result**: Every workflow action logged to Google Sheets in real-time ✅

---

## Step 3: Slack Webhook (Optional - Alerts)

### 3.1 Create Slack Webhook
1. Go to: https://api.slack.com/apps
2. Click **"Create New App"** → **"From scratch"**
3. Name: "CareFlow"
4. Choose your workspace
5. Go to **"Incoming Webhooks"** → Enable it
6. Click **"Add New Webhook to Workspace"**
7. Select a channel (e.g., #careflow-alerts)
8. Copy the **Webhook URL**

### 3.2 Add to `.env`
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### 3.3 Update n8n Slack node
In n8n workflow:
- **Webhook URL**: (paste your webhook)
- **Message**: (customize the message from the workflow)

**Result**: Real-time Slack alerts when workflows run ✅

---

## Step 4: Import Workflows into n8n

### 4.1 Open n8n
Go to: `http://localhost:5678`
Login: `admin` / `careflow123`

### 4.2 Import each workflow
For each of the 5 workflow files:

1. Click **"Workflows"** (top menu)
2. Click **"Create new"**
3. Click menu (⋮) → **"Import from file"**
4. Select from:
   ```
   /Users/yamin/Downloads/careflow_complete/automation/n8n_workflows/
   ```
   - workflow_1_daily_health_check.json
   - workflow_2_inactivity_check.json
   - workflow_3_new_customer_onboarding.json
   - workflow_4_churn_crm_update.json
   - workflow_5_weekly_ai_report.json

5. Review the nodes (they should show your API endpoints)
6. Click **"Save"** (Cmd+S)
7. Toggle **"Active"** (top right) to enable

### 4.3 Set schedules
For each workflow, edit the trigger node:

- **Workflow 1** (Daily health check): Every day at 9:00 AM
- **Workflow 2** (Inactivity check): Every day at 10:00 AM
- **Workflow 3** (Onboarding): Trigger manually or on webhook
- **Workflow 4** (Churn update): Every day at 2:00 PM
- **Workflow 5** (Weekly report): Every Monday at 9:00 AM

---

## Step 5: Verify Everything Works

After setting up, test each workflow:

1. **Test Workflow 1**:
   - Click **"Execute workflow"** (play button)
   - Check your inbox for emails
   - Check Google Sheets for log entries
   - Check Slack for alert

2. **Check CareFlow dashboard**:
   - Open `http://localhost:8000`
   - Email log should show new emails
   - Metrics should update

3. **Monitor n8n**:
   - Click workflow → **"Executions"**
   - Green checkmark = success
   - Red X = check error logs

---

## Troubleshooting

### Emails not sending
- ✅ Check `.env` has `GMAIL_APP_PASSWORD` (not your regular password)
- ✅ Check Gmail security: https://myaccount.google.com/security
- ✅ Check n8n Gmail node configuration

### Google Sheets not updating
- ✅ Check service account has edit access to sheet
- ✅ Verify `GOOGLE_SHEETS_ID` matches your sheet URL
- ✅ Check n8n Google Sheets node has correct service account selected

### Workflows not running on schedule
- ✅ Check workflow is **Active** (toggle on)
- ✅ Check trigger node has cron schedule set
- ✅ Check n8n is still running: `http://localhost:5678`

---

## Full `.env` example

```
GROQ_API_KEY= gsk_your_key_here

GMAIL_ADDRESS=your_email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

GOOGLE_SHEETS_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz_ABC123DEF456
GOOGLE_CREDENTIALS_PATH=google-credentials.json

SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX

API_BASE_URL=http://localhost:8000
```

---

## Result: Full Automation 🚀

Once configured:
- ✅ Every day at 9am: Health check runs → critical customers get emails
- ✅ Every day at 10am: Inactivity check → inactive users get re-engagement emails
- ✅ Every day at 2pm: Churn analysis → high-risk accounts logged to Google Sheets
- ✅ Every Monday 9am: Weekly AI report → emailed + posted to Slack
- ✅ All activity logged to Google Sheets for team visibility
- ✅ Dashboard shows real-time data from SQLite

**No manual work. Fully automated. Working 24/7.** 🎯
