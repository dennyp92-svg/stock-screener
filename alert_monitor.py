def main():
    print("Stock Alert Monitor started")
    already_alerted   = set()
    alerts_sent_today = 0
    ct  = pytz.timezone("America/Chicago")
    now = datetime.now(ct)
    print(f"Current time: {now.strftime('%I:%M %p')} CT")

    # Only send morning brief if explicitly triggered
    send_brief = os.getenv("SEND_BRIEF", "true").lower() == "true"
    if send_brief:
        print("Sending daily morning summary...")
        send_daily_summary()

    # Check watchlist during alert window only
    if is_alert_window():
        print(f"Alert window open — checking watchlist")
        watchlist = get_watchlist()
        if watchlist:
            print(f"Checking {len(watchlist)} stocks: {watchlist}")
            for ticker in watchlist:
                data = get_stock_data(ticker)
                if data:
                    print(f"{ticker} — ${data['price']} — {data['change']}% — {data['vol_spike']}x vol")
                    already_alerted, alerts_sent_today = check_and_alert(
                        ticker, data, already_alerted, alerts_sent_today
                    )
                time.sleep(1)
        else:
            print("Watchlist is empty")
    else:
        print(f"Outside alert window — time: {now.strftime('%I:%M %p')} CT")

    print(f"Monitor run complete — {alerts_sent_today} alerts sent today")

if __name__ == "__main__":
    main()
