import os, requests, time, pandas as pd, streamlit as st
from dotenv import load_dotenv
load_dotenv()
KEY = os.getenv("ALPHA_VANTAGE_KEY")
st.title("AI Stock Screener")
tickers_input = st.sidebar.text_area("Tickers", "AAPL,MSFT,GOOGL,AMZN,NVDA")
max_pe = st.sidebar.slider("Max PE", 5, 100, 50)
run = st.sidebar.button("Run Screener")
if run:
    tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]
    results = []
    for i,ticker in enumerate(tickers):
        st.write(f"Fetching {ticker}...")
        r = requests.get("https://www.alphavantage.co/query",params={"function":"OVERVIEW","symbol":ticker,"apikey":KEY},timeout=10)
        d = r.json()
        if "Symbol" in d:
            pe = float(d.get("PERatio") or 0)
            eps = float(d.get("EPS") or 0)
            if 0 < pe < max_pe and eps > 0:
                results.append({"Ticker":ticker,"Name":d.get("Name"),"Sector":d.get("Sector"),"PE":pe,"EPS":eps})
        if i < len(tickers)-1: time.sleep(13)
    if results:
        df = pd.DataFrame(results)
        st.success(f"{len(results)} stocks passed")
        st.dataframe(df,use_container_width=True)
        st.download_button("Download CSV",df.to_csv(index=False).encode(),"results.csv")
    else:
        st.error("No stocks passed. Try raising PE limit.")
