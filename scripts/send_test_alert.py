



"""
Send a fake HIGH alert through the real email pipeline to verify the
Resend setup (.env → send_alert_emails → send_resend_email).

Run from the project root:  python3 scripts/send_test_alert.py

Goes straight to send_alert_emails, bypassing raise_alerts, so it never
touches data/alerts_state.json or the alert log — safe to rerun.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# .env must be loaded BEFORE importing stockanalysis modules: market_movers
# reads RESEND_API_KEY / ALERT_EMAIL_* at import time, not per call.
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import os
if os.environ.get("RESEND_API_KEY", "").startswith("re_your"):
    sys.exit("RESEND_API_KEY in .env is still the placeholder — paste your real key first.")

from stockanalysis.core import alerts

alert = alerts.make_alert(
    dedup_key="TEST:email_pipeline",
    category="test",
    priority="HIGH",
    ticker="TEST",
    headline="test alert — email pipeline check",
    why_it_matters="If you're reading this in your inbox, the Resend setup works.",
    expected_impact="None — this is a manually triggered test.",
    suggested_action="Nothing to do. Delete this email.",
    confidence=100,
    time_sensitivity="none",
    supporting_data={"source": "scripts/send_test_alert.py"},
)

ok = alerts.send_alert_emails([alert])
to = os.environ.get("ALERT_EMAIL_TO", "<unset>")
print(f"\n{'SENT' if ok else 'FAILED'} — check {to} (and spam folder)" if ok
      else "\nFAILED — see the log lines above for the Resend error.")
