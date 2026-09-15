def main():
    print("Stock Alert Monitor started")
    already_alerted  = set()
    summary_sent_today = None

    while True:
        ct  = pytz.timezone("America/Chicago")
        now = datetime.now(ct)
        today = now.strftime("%Y%m%d")

        # Send daily summary at 8:00am CT once per day
        if is_summary_time() and summary_sent_today != today:
            send_daily_summary()
            summary_sent_today = today
