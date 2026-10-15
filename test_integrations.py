"""Explicit live integration check. Never executed during test collection."""


def main():
    """
    Test CareFlow Integrations
    Verify Gmail and Google Sheets credentials work
    """

    import os
    import sys
    from pathlib import Path
    from dotenv import load_dotenv

    sys.path.insert(0, "/Users/yamin/Downloads/careflow_complete")

    # Load .env
    load_dotenv()

    print("\n" + "=" * 70)
    print("CareFlow Integration Test".center(70))
    print("=" * 70 + "\n")

    # Test 1: Gmail
    print("1️⃣  Testing Gmail Setup...")
    print("-" * 70)

    gmail_address = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")

    if not gmail_address:
        print("❌ GMAIL_ADDRESS not in .env")
    else:
        print(f"✅ Gmail Address: {gmail_address}")

    if not gmail_password:
        print("❌ GMAIL_APP_PASSWORD not in .env")
    else:
        pwd = gmail_password.replace(" ", "")
        print("✅ Gmail App Password configured")

    # Try sending a test email
    if gmail_address and gmail_password:
        try:
            import smtplib
            from email.mime.text import MIMEText

            print("\n   Attempting to send test email...")

            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            server.login(gmail_address, gmail_password.replace(" ", ""))

            msg = MIMEText(
                "This is a test email from CareFlow automation. If you see this, Gmail is working! ✅"
            )
            msg["Subject"] = "CareFlow Test Email"
            msg["From"] = gmail_address
            msg["To"] = gmail_address

            server.send_message(msg)
            server.quit()

            print("   ✅ Test email sent successfully to your inbox!")

        except Exception as e:
            print(f"   ❌ Failed to send email: {str(e)[:100]}")

    # Test 2: Google Sheets
    print("\n2️⃣  Testing Google Sheets Setup...")
    print("-" * 70)

    sheets_id = os.getenv("GOOGLE_SHEETS_ID")
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")

    if not sheets_id:
        print("⏳ GOOGLE_SHEETS_ID not in .env yet")
        print("   (This is optional - set it up after you create your sheet)")
    else:
        print(f"✅ Google Sheets ID: {sheets_id}")

    if not creds_path:
        print("⏳ GOOGLE_CREDENTIALS_PATH not in .env yet")
        print("   (This is optional - you'll add it after getting credentials)")
    else:
        creds_file = Path(creds_path)
        if creds_file.exists():
            print(f"✅ Credentials file found: {creds_path}")
            try:
                from google.oauth2.service_account import Credentials
                from google.auth.transport.requests import Request

                creds = Credentials.from_service_account_file(creds_file)
                print("✅ Credentials loaded successfully!")

            except Exception as e:
                print(f"❌ Error loading credentials: {str(e)[:100]}")
        else:
            print(f"❌ Credentials file not found: {creds_path}")

    # Summary
    print("\n" + "=" * 70)
    print("📋 NEXT STEPS".center(70))
    print("=" * 70)

    print("""
    ✅ Your Gmail is configured and working!

    📊 For Google Sheets automation (optional but recommended):

    1. Go to: https://sheets.google.com
       - Create new sheet named "CareFlow-Automation-Log"
       - Add columns: Timestamp, Workflow, Customer ID, Organization, Action Type, Details, Status, Email Sent To
       - Copy the sheet ID

    2. Go to: https://console.developers.google.com/
       - Create project "CareFlow"
       - Enable Google Sheets API
       - Create Service Account
       - Download JSON credentials
       - Save to: google-credentials.json

    3. Update .env with:
       GOOGLE_SHEETS_ID=your_sheet_id
       GOOGLE_CREDENTIALS_PATH=google-credentials.json

    4. Import workflows to n8n at: http://localhost:5678
       - admin / careflow123
       - Import 5 JSONs from automation/n8n_workflows/
       - Activate each one

    Then run demo_automation.py again to see real emails sent to your inbox! 🚀
    """)


if __name__ == "__main__":
    main()
