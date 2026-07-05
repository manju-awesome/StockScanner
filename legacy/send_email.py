def send_categories_email(sections: dict, csv_path: str):
    """Email all four category tables via Resend as a CSV attachment with a
    readable text body summarising each category."""
    if not RESEND_API_KEY:
        log.error("Missing RESEND_API_KEY environment variable. Cannot send email.")
        return

    now_str = datetime.now(MARKET_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    subject = f"Stock Categories - {datetime.now(MARKET_TZ).strftime('%Y-%m-%d')}"

    body_lines = [f"Stock category scan results — {now_str}\n"]
    for cat, (label, df_section) in sections.items():
        tickers = df_section["Ticker"].tolist() if not df_section.empty else []
        body_lines.append(f"{cat} ({len(tickers)}): {', '.join(tickers) if tickers else 'none'}")
    body_lines.append("\nFull details attached as CSV.")
    body = "\n".join(body_lines)

    # Base64-encode the CSV attachment
    try:
        with open(csv_path, "rb") as f:
            csv_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        log.error("Could not read CSV for attachment: %s", e)
        csv_b64 = None

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [EMAIL_TO],
        "subject": subject,
        "text": body,
    }
    if csv_b64:
        payload["attachments"] = [{"filename": os.path.basename(csv_path), "content": csv_b64}]

    if HAVE_RESEND_SDK:
        try:
            resend_sdk.api_key = RESEND_API_KEY
            result = resend_sdk.Emails.send(payload)
            log.info("Email sent via Resend SDK (id=%s)",
                     result.get("id") if isinstance(result, dict) else result)
            print(f"Email sent to {EMAIL_TO}", file=sys.stderr)
            return
        except Exception as e:
            log.warning("Resend SDK failed (%s) — trying raw HTTP fallback", e)

    import urllib.request, urllib.error
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; stock-metrics-table/1.0)",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("Email sent via Resend HTTP (status %s)", resp.status)
            print(f"Email sent to {EMAIL_TO}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        log.error("Resend API error %s: %s", e.code, e.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log.error("Failed to send email: %s", e)