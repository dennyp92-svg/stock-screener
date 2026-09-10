import os
import requests
import pandas as pd
from dotenv import load_dotenv
import time

load_dotenv()

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")

if not ALPHA_VANTAGE_KEY:
    raise ValueError("Missing ALPHA_VANTAGE_KEY in .env file")

print("Keys loaded OK")

TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]

def fetch_stock(ticker):
    url = "https://www.alphavantage.co/query"
    params = {"function": "OVERVIEW", "symbol": ticker, "apikey": ALPHA_VANTAGE_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if "Symbol" not in data:
            print(f"  No data for {ticker}")
            return None
        return data
    except Exception as e:
        print(f"  Error: {e}")
        return None

results = []
for i, ticker in enumerate(TICKERS):
    print(f"Fetching {ticker} ({i+1}/{len(TICKERS)})...")
    data = fetch_stock(ticker)
    if data:
        print(f"  {data.get(chr(78)+chr(97)+chr(109)+chr(101))} | PE: {data.get(chr(80)+chr(69)+chr(82)+chr(97)+chr(116)+chr(105)+chr(111))}")
        results.append({"ticker": ticker, "name": data.get("Name"), "pe": data.get("PERatio")})
    if i < len(TICKERS) - 1:
        time.sleep(13)

df = pd.DataFrame(results)
df.to_csv("results.csv", index=False)
print(f"Done! {len(results)} stocks saved to results.csv")
