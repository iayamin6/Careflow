"""
CareFlow n8n Workflow Import & Configuration
Prepares workflows to send real emails from your Gmail
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

WORKFLOWS_DIR = Path('/Users/yamin/Downloads/careflow_complete/automation/n8n_workflows')
GMAIL_ADDRESS = os.getenv('GMAIL_ADDRESS')
API_URL = 'http://localhost:8000'

print("""
╔═══════════════════════════════════════════════════════════════════╗
║         CareFlow n8n Workflow Configuration                       ║
╚═══════════════════════════════════════════════════════════════════╝

IMPORTANT: Before importing workflows to n8n, read this:

STEP 1: Open n8n
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Go to: http://localhost:5678
Login: admin / careflow123


STEP 2: Import Each Workflow
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For EACH of these 5 workflows:

Workflow 1: workflow_1_daily_health_check.json
   └─ Imports customer data, sends check-in emails
   
Workflow 2: workflow_2_inactivity_check.json  
   └─ Finds inactive customers, sends re-engagement emails

Workflow 3: workflow_3_new_customer_onboarding.json
   └─ Triggered for new signups, sends onboarding sequence
   
Workflow 4: workflow_4_churn_crm_update.json
   └─ Logs high-risk churn accounts to CRM
   
Workflow 5: workflow_5_weekly_ai_report.json
   └─ Generates AI report and sends weekly summary

HOW TO IMPORT:

1. In n8n, click "Workflows" (top left)
2. Click "+" (Create new) or "Import"
3. Click the menu icon (⋮) → "Import from file"
4. Navigate to:
   /Users/yamin/Downloads/careflow_complete/automation/n8n_workflows/
5. Select workflow JSON file
6. Click "Open"
7. Review the nodes (they should be pre-configured)
8. Click "Save" (Cmd+S)
9. Toggle "Active" (top right) to enable it


STEP 3: Configure Gmail Credentials
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For workflows that send emails (1, 2, 3):

1. Double-click the "Gmail" node
2. Click "Authentication" dropdown
3. Click "Create new credentials"
4. Enter:
   - Email: imteshar.ahmed@g.bracu.ac.bd
   - App Password: mvqs mjmj djjt hset
5. Click "Authenticate"
6. Save the workflow


STEP 4: Test Each Workflow
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After importing and configuring:

1. Click the workflow
2. Click "Execute workflow" (blue play button)
3. Watch it run in real-time
4. Check your inbox for emails!
5. Check the execution log for any errors


STEP 5: Enable Schedules
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

To make workflows run automatically:

Workflow 1 (Daily health check):
   - Double-click "Schedule" trigger node
   - Set: "Every day at 09:00"
   - Save

Workflow 2 (Inactivity check):
   - Set: "Every day at 10:00"

Workflow 4 (Churn update):
   - Set: "Every day at 14:00"

Workflow 5 (Weekly report):
   - Set: "Every Monday at 09:00"

Workflow 3 (Onboarding):
   - Keep as "Manual" (triggered when new customer added)


YOUR CREDENTIALS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Gmail Address: imteshar.ahmed@g.bracu.ac.bd
App Password: mvqs mjmj djjt hset
API Endpoint: http://localhost:8000

Use these when configuring n8n nodes.


RESULT: Real Automation! 🚀
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ 9am daily: Health check runs → critical customers get emails
✅ 10am daily: Inactivity check → inactive users get re-engagement
✅ 2pm daily: Churn analysis → high-risk accounts detected
✅ 9am Monday: Weekly report → AI summary sent to your email
✅ ALL activity logged to your database


TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ "Gmail authentication failed"
   → Check app password is correct (no typos)
   → Check 2FA enabled on Gmail account
   → Regenerate app password if needed

❌ "API connection failed"
   → Check FastAPI running: http://localhost:8000/health
   → Check endpoint URL is correct in n8n

❌ "Workflow not executing on schedule"
   → Check workflow is "Active" (toggle on)
   → Check n8n service is running
   → Check trigger node has cron schedule set

Questions? Check n8n documentation: https://docs.n8n.io/
""")

# Show workflow details
print("\n" + "="*70)
print("WORKFLOW DETAILS".center(70))
print("="*70 + "\n")

workflows = [
    ("workflow_1_daily_health_check.json", "Daily Health Check", "09:00"),
    ("workflow_2_inactivity_check.json", "Inactivity Check", "10:00"),
    ("workflow_3_new_customer_onboarding.json", "Onboarding", "Manual"),
    ("workflow_4_churn_crm_update.json", "Churn CRM Update", "14:00"),
    ("workflow_5_weekly_ai_report.json", "Weekly Report", "Mon 09:00"),
]

for fname, name, schedule in workflows:
    path = WORKFLOWS_DIR / fname
    if path.exists():
        print(f"✅ {name}")
        print(f"   File: {fname}")
        print(f"   Schedule: {schedule}")
        print()
    else:
        print(f"❌ {name} - NOT FOUND")
        print()

print("Ready to import! 🚀\n")
