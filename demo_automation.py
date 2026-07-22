"""
CareFlow Demo Automation Runner
Simulates n8n workflows running automatically.
Shows emails being generated, database being updated in real-time.
"""

import sys
import time
import sqlite3
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/Users/yamin/Downloads/careflow_complete')

import requests
from scoring.health_score import run_scoring

DB_PATH = Path('/Users/yamin/Downloads/careflow_complete/data/customers.db')
API_URL = "http://localhost:8000"


def unwrap_data(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return payload

def clear_emails():
    """Clear email log for fresh demo"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM email_log")
    conn.commit()
    conn.close()
    print("🗑️  Cleared email log for fresh demo\n")

def demo_workflow_1_health_check():
    """Simulate: Daily health check workflow"""
    print("=" * 70)
    print("🚀 WORKFLOW 1: Daily Health Check")
    print("=" * 70)
    print("Step 1: Running health scoring...")
    
    resp = requests.post(f"{API_URL}/scores/run", timeout=10)
    if resp.status_code != 200:
        print(f"❌ Error: {resp.text}")
        return
    
    data = resp.json()
    print(f"✅ Health scoring complete")
    print(f"   Distribution: {data.get('distribution', {})}")
    
    print("\nStep 2: Finding critical customers...")
    resp = requests.get(f"{API_URL}/scores/category/Critical", timeout=10)
    critical = unwrap_data(resp.json())
    print(f"✅ Found {len(critical)} critical customers")
    
    print("\nStep 3: Generating check-in emails for critical customers...")
    for i, customer in enumerate(critical[:5]):  # Show first 5
        cid = customer["customer_id"]
        org = customer["organization_name"]
        
        payload = {"customer_id": cid, "email_type": "check_in"}
        resp = requests.post(f"{API_URL}/email/generate", json=payload, timeout=10)
        
        if resp.status_code == 200:
            email = resp.json()
            print(f"   ✅ {i+1}. {org} ({cid})")
            print(f"      Subject: {email.get('subject', 'N/A')}")
            time.sleep(0.5)  # Stagger for visual effect
        else:
            print(f"   ❌ Failed for {cid}")
    
    print(f"\n✅ Workflow 1 Complete!")
    time.sleep(1)

def demo_workflow_2_inactivity():
    """Simulate: Inactivity check workflow"""
    print("\n" + "=" * 70)
    print("🚀 WORKFLOW 2: Inactivity Check")
    print("=" * 70)
    print("Step 1: Finding inactive customers (7+ days no login)...")
    
    resp = requests.get(f"{API_URL}/scores", timeout=10)
    scores = unwrap_data(resp.json())
    
    # Find customers with high days_since_login
    inactive = [s for s in scores if s.get('days_since_login', 0) >= 7]
    print(f"✅ Found {len(inactive)} inactive customers")
    
    print("\nStep 2: Sending re-engagement emails...")
    for i, customer in enumerate(inactive[:3]):
        cid = customer["customer_id"]
        org = customer["organization_name"]
        days = customer.get('days_since_login', 0)
        
        payload = {"customer_id": cid, "email_type": "re_engagement"}
        resp = requests.post(f"{API_URL}/email/generate", json=payload, timeout=10)
        
        if resp.status_code == 200:
            email = resp.json()
            print(f"   ✅ {i+1}. {org} (inactive {days} days)")
            print(f"      Subject: {email.get('subject', 'N/A')}")
            time.sleep(0.5)
    
    print(f"\n✅ Workflow 2 Complete!")
    time.sleep(1)

def demo_workflow_4_churn():
    """Simulate: Churn prediction workflow"""
    print("\n" + "=" * 70)
    print("🚀 WORKFLOW 4: Churn Risk Detection")
    print("=" * 70)
    print("Step 1: Running churn predictions...")
    
    run_resp = requests.post(f"{API_URL}/churn/run", timeout=30)
    if run_resp.status_code != 200:
        print(f"❌ Error running churn pipeline: {run_resp.text}")
        return

    resp = requests.get(f"{API_URL}/churn", timeout=10)
    churns = unwrap_data(resp.json())
    
    high_risk = [c for c in churns if c['risk_label'] == 'High Risk']
    print(f"✅ Found {len(high_risk)} high-risk churn accounts")
    
    print("\nStep 2: Top 5 at-risk accounts (would be logged to Google Sheets):")
    for i, account in enumerate(high_risk[:5]):
        cid = account["customer_id"]
        org = account["organization_name"]
        risk_pct = account["churn_probability"] * 100
        reason = account["shap_explanation"][:60]
        
        print(f"   {i+1}. {org} ({cid})")
        print(f"      Risk: {risk_pct:.0f}%")
        print(f"      Why: {reason}...")
        time.sleep(0.5)
    
    print(f"\n✅ Workflow 4 Complete!")
    time.sleep(1)

def demo_workflow_5_report():
    """Simulate: Weekly AI report workflow"""
    print("\n" + "=" * 70)
    print("🚀 WORKFLOW 5: Weekly AI Report")
    print("=" * 70)
    print("Step 1: Generating weekly AI report...")
    
    resp = requests.post(f"{API_URL}/report/weekly/run", timeout=20)
    if resp.status_code != 200:
        print(f"❌ Error: {resp.text}")
        return
    
    result = resp.json()
    report = result.get('report', '')
    
    print(f"✅ Report generated!")
    print("\n📋 Report Preview:")
    print("-" * 70)
    print(report[:800])
    print("-" * 70)
    print(f"\n(Full report would be emailed + posted to Slack)")
    
    print(f"\n✅ Workflow 5 Complete!")
    time.sleep(1)

def show_email_log():
    """Show all emails that were generated"""
    print("\n" + "=" * 70)
    print("📧 EMAIL LOG (All emails generated by workflows)")
    print("=" * 70)
    
    conn = sqlite3.connect(DB_PATH)
    emails = conn.execute("""
        SELECT customer_id, email_type, subject, sent_at
        FROM email_log
        ORDER BY sent_at DESC
        LIMIT 10
    """).fetchall()
    conn.close()
    
    if not emails:
        print("No emails yet")
        return
    
    print(f"\nTotal emails generated: {len(emails)}\n")
    for i, (cid, etype, subject, sent_at) in enumerate(emails):
        print(f"{i+1}. {cid} • {etype.replace('_', ' ').title()}")
        print(f"   Subject: {subject}")
        print(f"   Sent: {sent_at}\n")

def show_next_steps():
    """Show how to connect real services"""
    print("\n" + "=" * 70)
    print("🎯 NEXT STEPS: Connect Real Services")
    print("=" * 70)
    print("""
1️⃣  GMAIL SETUP
   • Go to: https://myaccount.google.com/apppasswords
   • Generate App Password for Gmail
   • Add to .env: GMAIL_APP_PASSWORD=your_password
   • Add to .env: GMAIL_ADDRESS=your_email@gmail.com

2️⃣  GOOGLE SHEETS SETUP
   • Create a new Google Sheet
   • Share it with your service account
   • Get credentials JSON file
   • Add to .env: GOOGLE_SHEETS_ID=sheet_id
   • Add to .env: GOOGLE_CREDENTIALS=path_to_credentials.json

3️⃣  SLACK WEBHOOK (Optional)
   • Go to: https://api.slack.com/apps
   • Create incoming webhook
   • Add to .env: SLACK_WEBHOOK_URL=https://hooks.slack.com/...

4️⃣  IMPORT WORKFLOWS INTO N8N
   • Open: http://localhost:5678
   • Login: admin / careflow123
   • Click "Workflows" → "Create new" → "Import from file"
   • Select each workflow JSON from automation/n8n_workflows/
   • Save and Activate each workflow
   • Set schedule (daily 9am for health check, etc)

Once configured, workflows will run automatically on schedule! 🚀
    """)

if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " CareFlow Automation Demo — Watch Workflows in Action ".center(68) + "║")
    print("╚" + "=" * 68 + "╝")
    print()
    
    try:
        clear_emails()
        
        # Run each workflow simulation
        demo_workflow_1_health_check()
        demo_workflow_2_inactivity()
        demo_workflow_4_churn()
        demo_workflow_5_report()
        
        # Show results
        show_email_log()
        show_next_steps()
        
        print("\n✅ Demo automation complete!")
        print(f"📊 Check the dashboard at: http://localhost:8000")
        print(f"💚 Open n8n at: http://localhost:5678\n")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
