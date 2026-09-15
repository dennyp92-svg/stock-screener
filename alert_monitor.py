import os
import time
import smtplib
import requests
from email.mime.text import MIMEText
from datetime import datetime
import pytz

SUPABASE_URL       = os.getenv("SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY")
FMP_KEY            = os.getenv("FMP_KEY")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_EMAIL        = os.getenv("GMAIL_ADDRESS")

CHANGE_THRESHOLD   = 5.0
VOL_SPIKE_MIN      = 1.5
MAX_ALERTS_PER_DAY = 3

def is_alert_window():
    ct = pytz.timezone("America/Chicago")
    now = datetime.now(ct)
    if now.weekday() >= 5:
        return False
    open_time  = now.replace(hour=8,  minute=30, second=0)
    close_time = now.replace(hour=10, minute=0,  second=0)
    return open_time <= now <= close_time

def is_market_open():
    ct = pytz.timezone("America/Chicago")
    now = datetime.now(ct)
    if now.weekday() >= 5:
        return False
    open_time  = now.replace(hour=8,  minute=30, second=0)
    close_time = now.replace(hour=14, minute=45, second=0)
    return open_time <= now <= close_time

def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = ALERT_EMAIL
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, ALERT_EMAIL, msg.as_string())
        server.quit()
        print(f"Email sent: {subject}")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False

def get_watchlist():
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }
        url = f"{SUPABASE_URL}/rest/v1/Watchlist?select=Ticker"
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        return [row["Ticker"] for row in data if "Ticker" in row]
    except Exception as e:
        print(f"Watchlist fetch failed: {e}")
        return []

def get_stock_data(ticker):
    try:
        url = f"https://financialmodelingprep.com/stable/quote/{ticker}?apikey={FMP_KEY}"
        r = requests.get(url, timeout=10).json()
        if r and len(r) > 0:
            d = r[0]
            price      = d.get("price", 0)
            change_pct = round(d.get("changesPercentage", 0), 2)
            volume     = d.get("volume", 0)
            avg_volume = d.get("avgVolume", 1)
            vol_spike  = round(volume / avg_volume, 2) if avg_volume > 0 else 0
            name       = d.get("name", ticker)
            return {
                "ticker":     ticker,
                "name":       name,
                "price":      price,
                "change":     change_pct,
                "volume":     volume,
                "avg_volume": avg_volume,
                "vol_spike":  vol_spike
            }
    except Exception as e:
        print(f"Data fetch failed for {ticker}: {e}")
    return None

def get_top_movers():
    try:
        url = f"https://financialmodelingprep.com/stable/biggest-gainers?apikey={FMP_KEY}"
        r = requests.get(url, timeout=10).json()
        movers = []
        for s in r[:10]:
            price  = s.get("price", 0)
            change = s.get("changesPercentage", 0)
            vol    = s.get("volume", 0)
            avg_v  = s.get("avgVolume", 1)
            spike  = round(vol / avg_v, 2) if avg_v > 0 else 0
            if 30 <= price <= 500:
                movers.append({
                    "ticker":    s.get("symbol", ""),
                    "name":      s.get("name", ""),
                    "price":     price,
                    "change":    round(change, 2),
                    "vol_spike": spike
                })
        return movers
    except Exception as e:
        print(f"Top movers fetch failed: {e}")
        return []

def send_daily_summary():
    print("Sending daily summary email...")
    ct = pytz.timezone("America/Chicago")
    now = datetime.now(ct)
    date_str = now.strftime("%A %B %d %Y")

    movers = get_top_movers()
    watchlist = get_watchlist()
    watchlist_data = []
    for ticker in watchlist:
        d = get_stock_data(ticker)
        if d:
            watchlist_data.append(d)

    body  = "STOCK SCANNER PRO — MORNING BRIEF\n"
    body += date_str + "\n"
    body += "=" * 50 + "\n\n"

    body += "YOUR WATCHLIST\n"
    body += "-" * 30 + "\n"
    if watchlist_data:
        for d in watchlist_data:
            direction = "▲" if d["change"] > 0 else "▼"
            body += f"{d['ticker']} — ${d['price']} — {direction} {d['change']}% — Vol Spike: {d['vol_spike']}x\n"
    else:
        body += "Watchlist is empty\n"

    body += "\n"
    body += "TOP MOVERS TODAY ($30-$500)\n"
    body += "-" * 30 + "\n"
    if movers:
        for i, m in enumerate(movers[:10], 1):
            body += f"{i}. {m['ticker']} — ${m['price']} — +{m['change']}% — Vol Spike: {m['vol_spike']}x\n"
            body += f"   {m['name']}\n"
    else:
        body += "No movers found\n"

    body += "\n"
    body += "=" * 50 + "\n"
    body += "Market opens 8:30am CT — Morning session 8:30am to 10:00am CT\n"
    body += "Force close by 2:45pm CT — Nothing rolls overnight\n\n"
    body += "For research only. Not financial advice.\n"
    body += "Stock Scanner Pro"

    subject = f"Morning Brief — {date_str}"
    send_email(subject, body)

def check_and_alert(ticker, data, already_alerted, alerts_sent_today):
    # Only alert during 8:30am to 10:00am CT
    if not is_alert_window():
        print(f"Outside alert window — skipping")
        return already_alerted, alerts_sent_today

    # Stop after 3 alerts for the day
    if alerts_sent_today >= MAX_ALERTS_PER_DAY:
        print(f"Max {MAX_ALERTS_PER_DAY} alerts reached for today — skipping")
        return already_alerted, alerts_sent_today

    alerts = []
    if abs(data["change"]) >= CHANGE_THRESHOLD:
        direction = "UP" if data["change"] > 0 else "DOWN"
        alerts.append(f"📈 {ticker} moved {direction} {data['change']}%")
    if data["vol_spike"] >= VOL_SPIKE_MIN:
        alerts.append(f"🔊 {ticker} volume spike {data['vol_spike']}x average")

    if alerts:
        # Once per day per stock
        alert_key = f"{ticker}_{datetime.now().strftime('%Y%m%d')}"
        if alert_key not in already_alerted:
            subject = f"Stock Alert {alerts_sent_today + 1} of {MAX_ALERTS_PER_DAY} - {ticker} ${data['price']}"
            body  = "\n".join(alerts)
            body += f"\n\nPrice:      ${data['price']}"
            body += f"\nChange:     {data['change']}%"
            body += f"\nVolume:     {data['volume']:,}"
            body += f"\nAvg Volume: {data['avg_volume']:,}"
            body += f"\nVol Spike:  {data['vol_spike']}x"
            body += f"\n\nAlert {alerts_sent_today + 1} of {MAX_ALERTS_PER_DAY} for today"
            body += f"\nTime: {datetime.now().strftime('%I:%M %p')} CT"
            body += "\n\nFor research only. Not financial advice."
            send_email(subject, body)
            already_alerted.add(alert_key)
            alerts_sent_today += 1
        else:
            print(f"Alert already sent today for {ticker}")

    return already_alerted, alerts_sent_today

def main():
    print("Stock Alert Monitor started")
    already_alerted    = set()
    alerts_sent_today  = 0
    ct    = pytz.timezone("America/Chicago")
    now   = datetime.now(ct)
    print(f"Current time: {now.strftime('%I:%M %p')} CT")
    print("Sending daily morning summary...")
    send_daily_summary()
    if is_market_open():
        print(f"Market open — checking watchlist")
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
        print(f"Market not open — time: {now.strftime('%I:%M %p')} CT")
    print(f"Monitor run complete — {alerts_sent_today} alerts sent today")

if __name__ == "__main__":
    main()
