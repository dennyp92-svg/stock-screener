import yfinance as yf
import pandas as pd
import streamlit as st
import os, json
import smtplib
from email.mime.text import MIMEText

def send_email_alert(to_email, subject, body):
    try:
        gmail_user = st.secrets.get("GMAIL_ADDRESS", os.getenv("GMAIL_ADDRESS"))
        gmail_pass = st.secrets.get("GMAIL_APP_PASSWORD", os.getenv("GMAIL_APP_PASSWORD"))
    except:
        gmail_user = os.getenv("GMAIL_ADDRESS")
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        return False, "Email not configured"
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = gmail_user
        msg["To"] = to_email
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_email, msg.as_string())
        server.quit()
        return True, "Sent successfully"
    except Exception as e:
        return False, str(e)
from dotenv import load_dotenv
load_dotenv()
try:
    FMP_KEY = st.secrets.get("FMP_KEY", os.getenv("FMP_KEY"))
except:
    FMP_KEY = os.getenv("FMP_KEY")

def get_fmp_movers():
    try:
        url1 = f"https://financialmodelingprep.com/stable/biggest-gainers?apikey={FMP_KEY}"
        url2 = f"https://financialmodelingprep.com/stable/most-actives?apikey={FMP_KEY}"
        import requests
        r1 = requests.get(url1, timeout=10).json()
        r2 = requests.get(url2, timeout=10).json()
        t1 = [s["symbol"] for s in r1 if "symbol" in s]
        t2 = [s["symbol"] for s in r2 if "symbol" in s]
        combined = list(dict.fromkeys(t1 + t2))
        return combined
    except:
        return []
from concurrent.futures import ThreadPoolExecutor
ALL_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AVGO', 'AMD', 'ORCL', 'PLTR', 'CRM', 'SNOW', 'DDOG', 'NET', 'ARM', 'SMCI', 'SOFI', 'MSTR', 'COIN', 'NFLX', 'DIS', 'ROKU', 'SPOT', 'UBER', 'ABNB', 'SQ', 'PYPL', 'HOOD', 'NU', 'V', 'MA', 'JPM', 'BAC', 'WFC', 'GS', 'MS', 'XOM', 'CVX', 'COP', 'OXY', 'JNJ', 'PFE', 'MRNA', 'LLY', 'ABBV', 'BMY', 'MRK', 'AMGN', 'COST', 'WMT', 'TGT', 'HD', 'LOW', 'BA', 'LMT', 'RTX', 'NOC', 'NIO', 'RIVN', 'LCID', 'XPEV', 'F', 'GM', 'INTC', 'QCOM', 'MU', 'AMAT', 'KLAC', 'TXN', 'ADI', 'MRVL', 'ENPH', 'FSLR', 'ALAB', 'AEHR', 'IOT', 'COHR', 'SITM', 'MARA', 'RIOT', 'CRWD', 'PANW', 'ZM', 'SHOP', 'BABA', 'JD', 'PDD', 'RKLB', 'ASTS', 'GME', 'AMC', 'IREN', 'CLSK', 'HUT', 'IBIT', 'ARKK', 'ARKG', 'IONQ', 'RGTI', 'QUBT', 'ACHR', 'JOBY', 'WKHS', 'NKLA', 'LAZR', 'LYFT', 'ARGX', 'ASML', 'AXON', 'AVXL', 'AZPN', 'ASAN', 'ARWR', 'ARVN', 'AUPH', 'APLS', 'AGIO', 'VRTX', 'REGN', 'BIIB', 'ILMN', 'ALNY', 'BMRN', 'CRSP', 'BEAM', 'EDIT', 'NTLA', 'JAZZ']
WATCHLIST_FILE = os.path.expanduser("~/stock_screener/watchlist.json")
def load_watchlist():
    if "watchlist_data" not in st.session_state:
        try:
            if os.path.exists(WATCHLIST_FILE):
                with open(WATCHLIST_FILE) as wf:
                    st.session_state.watchlist_data = json.load(wf)
            else:
                st.session_state.watchlist_data = []
        except:
            st.session_state.watchlist_data = []
    return st.session_state.watchlist_data

def save_watchlist(wl):
    st.session_state.watchlist_data = wl
    try:
        with open(WATCHLIST_FILE, "w") as wf: json.dump(wl, wf)
    except: pass
@st.cache_data(ttl=120)
def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="5d").dropna(subset=["Close"])
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            chg = round(((curr-prev)/prev)*100,2)
            vol = int(hist["Volume"].iloc[-1])
            avg_vol = int(hist["Volume"].mean())
            vol_spike = round(vol/avg_vol,2) if avg_vol>0 else 0
            rec = info.get("recommendationKey","none")
            if rec == "strong_buy": rating = "STRONG BUY"
            elif rec == "buy": rating = "BUY"
            elif rec == "hold": rating = "HOLD"
            elif rec == "sell": rating = "SELL"
            else: rating = "N/A"
            return {"ticker":ticker,"price":curr,"chg":chg,"rating":rating,"target":info.get("targetMeanPrice","N/A"),"vol_spike":vol_spike,"high":info.get("fiftyTwoWeekHigh",0),"low":info.get("fiftyTwoWeekLow",0),"sector":info.get("sector","N/A")}
    except: pass
    return None
st.set_page_config(page_title="Stock Scanner Pro", page_icon="📈", layout="wide")
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)
watchlist = load_watchlist()
st.title("Stock Scanner Pro")
with st.expander("⚙️ Filters (tap to open/close)", expanded=True):
    col1, col2, col3 = st.columns(3)
    min_change = col1.number_input("Min %", value=0)
    max_change = col2.number_input("Max %", value=100)
    min_vol = col3.number_input("Vol Spike", value=0.0, step=0.5)
    col4, col5 = st.columns(2)
    min_price = col4.number_input("Min $", value=1)
    max_price = col5.number_input("Max $", value=1000)
    use_live = True
    show_ai = st.checkbox("Enable AI Analysis", value=False)
    auto_ai_strong = st.checkbox("Auto-run AI on Strong Buy stocks", value=False)
    extra = st.text_input("Look up any ticker", "").upper().strip()
    run = st.button("Run Scan", use_container_width=True)
    if "results" not in st.session_state:
        st.session_state.results = None
        st.session_state.tickers_scanned = 0
    st.caption("Live market discovery enabled - real movers pulled fresh each scan")
tab1, tab2 = st.tabs(["📈 Scanner", "⭐ Watchlist"])
with tab1:
    if run:
        if extra:
            d = get_stock_data(extra)
            if d:
                st.success(f"Found {extra}")
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Price", f"${round(d["price"],2)}")
                c2.metric("Change", f"{d["chg"]}%")
                c3.metric("Rating", d["rating"])
                c4.metric("Vol Spike", f"{d["vol_spike"]}x")
                c5,c6,c7 = st.columns(3)
                c5.metric("Target", f"${d["target"]}")
                c6.metric("52W High", f"${d["high"]}")
                c7.metric("52W Low", f"${d["low"]}")
            else:
                st.error(f"Could not find {extra}")
        else:
            tickers_to_scan = ALL_TICKERS
            if use_live:
                live = get_fmp_movers()
                if live:
                    tickers_to_scan = list(dict.fromkeys(live + ALL_TICKERS))
                    st.success(f"Added {len(live)} live movers from FMP")
                else:
                    st.warning("Could not fetch live movers, using default list")
            bar = st.progress(0, text="Scanning all stocks...")
            with ThreadPoolExecutor(max_workers=30) as executor:
                all_data = list(executor.map(get_stock_data, tickers_to_scan))
            bar.empty()
            results = []
            for d in all_data:
                if d:
                    price_ok = float(min_price) <= d["price"] <= float(max_price)
                    change_ok = float(min_change) <= d["chg"] <= float(max_change)
                    vol_ok = d["vol_spike"] >= float(min_vol) if min_vol > 0 else True
                    if price_ok and change_ok and vol_ok:
                        results.append(d)
            if results:
                results = sorted(results, key=lambda x: x["chg"], reverse=True)
                st.session_state.results = results
                st.session_state.tickers_scanned = len(tickers_to_scan)
                c1,c2,c3 = st.columns(3)
                c1.metric("Scanned", len(tickers_to_scan))
                c2.metric("Passed", len(results))
                c3.metric("Strong Buys", sum(1 for r in results if r["rating"]=="STRONG BUY"))
                st.divider()
                if st.button("Analyze Top 5 with AI"):
                    try:
                        akey = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
                    except:
                        akey = os.getenv("ANTHROPIC_KEY")
                    if akey:
                        import anthropic
                        client = anthropic.Anthropic(api_key=akey)
                        for r in results[:5]:
                            with st.spinner(f"Analyzing {r['ticker']}..."):
                                prompt = "Analyze " + r["ticker"] + " stock in 3 sentences. Price $" + str(r["price"]) + ", change " + str(r["chg"]) + "%, rating " + r["rating"] + ". End with AI RATING: STRONG BUY/BUY/HOLD/AVOID. Research only, not financial advice."
                                msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=150, messages=[{"role":"user","content":prompt}])
                            st.markdown(f"**{r['ticker']}** - ${round(r['price'],2)} - {r['chg']}%")
                            st.info(msg.content[0].text)
                st.divider()
                show_only_strong = st.checkbox("Show only Strong Buy", value=False, key="filter_run2")
                display_results = [r for r in results if r["rating"]=="STRONG BUY"] if show_only_strong else results
                for r in display_results:
                    label = f"{r["ticker"]} - ${round(r["price"],2)} - {r["chg"]}% - {r["rating"]}"
                    auto_expand = auto_ai_strong and r["rating"]=="STRONG BUY"
                    with st.expander(label, expanded=auto_expand):
                        c1,c2 = st.columns(2)
                        c1.metric("Price", f"${round(r["price"],2)}")
                        c2.metric("Change", f"{r["chg"]}%")
                        c3,c4 = st.columns(2)
                        c3.metric("Rating", r["rating"])
                        c4.metric("Vol Spike", f"{r["vol_spike"]}x")
                        c5,c6 = st.columns(2)
                        c5.metric("Target", f"${r["target"]}")
                        c6.metric("Sector", r["sector"])
                        c7,c8 = st.columns(2)
                        c7.metric("52W High", f"${r["high"]}")
                        c8.metric("52W Low", f"${r["low"]}")
                        if show_ai or (auto_ai_strong and r["rating"]=="STRONG BUY"):
                            import anthropic
                            try:
                                akey = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
                            except:
                                akey = os.getenv("ANTHROPIC_KEY")
                            if akey:
                                with st.spinner("Getting AI analysis..."):
                                    client = anthropic.Anthropic(api_key=akey)
                                    prompt = "Analyze " + r["ticker"] + " stock in 3 sentences. Price $" + str(r["price"]) + ", change " + str(r["chg"]) + "%, rating " + r["rating"] + ". End with AI RATING: STRONG BUY/BUY/HOLD/AVOID. Research only, not financial advice."
                                    msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=200, messages=[{"role":"user","content":prompt}])
                                st.info(msg.content[0].text)
                st.download_button("Download CSV", pd.DataFrame(results).to_csv(index=False).encode(), "results.csv")

            else:
                st.warning("No stocks found. Try wider filters.")
    elif st.session_state.results:
        results = st.session_state.results
        tickers_to_scan = list(range(st.session_state.tickers_scanned))
        c1,c2,c3 = st.columns(3)
        c1.metric("Scanned", st.session_state.tickers_scanned)
        c2.metric("Passed", len(results))
        c3.metric("Strong Buys", sum(1 for r in results if r["rating"]=="STRONG BUY"))
        st.divider()
        if st.button("Analyze Top 5 with AI", key="analyze_saved"):
            try:
                akey = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
            except:
                akey = os.getenv("ANTHROPIC_KEY")
            if akey:
                import anthropic
                client = anthropic.Anthropic(api_key=akey)
                for r in results[:5]:
                    with st.spinner(f"Analyzing {r["ticker"]}..."):
                        prompt = "Analyze " + r["ticker"] + " stock in 3 sentences. Price $" + str(r["price"]) + ", change " + str(r["chg"]) + "%, rating " + r["rating"] + ". End with AI RATING: STRONG BUY/BUY/HOLD/AVOID. Research only, not financial advice."
                        msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=150, messages=[{"role":"user","content":prompt}])
                    st.markdown(f"**{r["ticker"]}** - ${round(r["price"],2)} - {r["chg"]}%")
                    st.info(msg.content[0].text)
        st.divider()
        show_only_strong = st.checkbox("Show only Strong Buy", value=False)
        display_results = [r for r in results if r["rating"]=="STRONG BUY"] if show_only_strong else results
        for r in display_results:
            label = f"{r["ticker"]} - ${round(r["price"],2)} - {r["chg"]}% - {r["rating"]}"
            auto_expand2 = auto_ai_strong and r["rating"]=="STRONG BUY"
            with st.expander(label, expanded=auto_expand2):
                c1,c2,c3,c4,c5 = st.columns(5)
                c1.metric("Price", f"${round(r["price"],2)}")
                c2.metric("Change", f"{r["chg"]}%")
                c3.metric("Rating", r["rating"])
                c4.metric("Target", f"${r["target"]}")
                c5.metric("Vol Spike", f"{r["vol_spike"]}x")
                c6,c7,c8 = st.columns(3)
                c6.metric("52W High", f"${r["high"]}")
                c7.metric("52W Low", f"${r["low"]}")
                c8.metric("Sector", r["sector"])
                if show_ai or (auto_ai_strong and r["rating"]=="STRONG BUY"):
                    import anthropic
                    try:
                        akey2 = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
                    except:
                        akey2 = os.getenv("ANTHROPIC_KEY")
                    if akey2:
                        with st.spinner("Getting AI analysis..."):
                            client2 = anthropic.Anthropic(api_key=akey2)
                            prompt2 = "Analyze " + r["ticker"] + " stock in 3 sentences. Price $" + str(r["price"]) + ", change " + str(r["chg"]) + "%, rating " + r["rating"] + ". End with AI RATING: STRONG BUY/BUY/HOLD/AVOID. Research only, not financial advice."
                            msg2 = client2.messages.create(model="claude-sonnet-4-6", max_tokens=200, messages=[{"role":"user","content":prompt2}])
                        st.info(msg2.content[0].text)
with tab2:
    st.title("My Watchlist")
    if st.button("Debug: Check Secrets"):
        try:
            keys = list(st.secrets.keys())
            st.write("Secret keys found:", keys)
        except Exception as e:
            st.write("Error reading secrets:", str(e))
    add_manual = st.text_input("Add ticker to watchlist", "").upper().strip()
    if st.button("Add") and add_manual:
        if add_manual not in watchlist:
            watchlist.append(add_manual)
            save_watchlist(watchlist)
            st.success(f"Added {add_manual}")
        else:
            st.warning(f"{add_manual} already in watchlist")
    if watchlist:
        alert_email = st.text_input("Your email for alerts", key="watchlist_email")
        st.divider()
        for ticker in watchlist:
            c1,c2,c3 = st.columns([3,2,1])
            c1.write(ticker)
            if c2.button("Send Alert", key=f"alert_{ticker}"):
                if not alert_email:
                    st.error("Enter your email above first")
                else:
                    with st.spinner(f"Fetching {ticker} data..."):
                        d = get_stock_data(ticker)
                    if d:
                        body = f"Stock Alert: {ticker}\n\nPrice: ${round(d['price'],2)}\nChange: {d['chg']}%\nRating: {d['rating']}\nTarget: ${d['target']}\nSector: {d['sector']}"
                        success, msg = send_email_alert(alert_email, f"Stock Scanner Pro Alert - {ticker}", body)
                        if success:
                            st.success(f"Alert sent for {ticker}!")
                        else:
                            st.error(f"Failed: {msg}")
                    else:
                        st.error(f"Could not fetch data for {ticker}")
            if c3.button("Remove", key=f"rem_{ticker}"):
                watchlist.remove(ticker)
                save_watchlist(watchlist)
                st.rerun()
    else:
        st.info("Watchlist is empty")
