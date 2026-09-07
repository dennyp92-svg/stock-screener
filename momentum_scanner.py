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
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = prices.diff().dropna()
    gains = deltas.where(deltas > 0, 0)
    losses = -deltas.where(deltas < 0, 0)
    avg_gain = gains.rolling(window=period).mean().iloc[-1]
    avg_loss = losses.rolling(window=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 1)

def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="30d").dropna(subset=["Close"])
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            chg = round(((curr-prev)/prev)*100,2)
            vol = int(hist["Volume"].iloc[-1])
            avg_vol = int(hist["Volume"].mean())
            vol_spike = round(vol/avg_vol,2) if avg_vol>0 else 0
            rsi_val = calc_rsi(hist["Close"])
            rec = info.get("recommendationKey","none")
            if rec == "strong_buy": rating = "STRONG BUY"
            elif rec == "buy": rating = "BUY"
            elif rec == "hold": rating = "HOLD"
            elif rec == "sell": rating = "SELL"
            else: rating = "N/A"

            week52_high = info.get("fiftyTwoWeekHigh", 0)
            pct_from_high = round(((week52_high - curr) / week52_high) * 100, 1) if week52_high > 0 else None

            today_high = float(hist["High"].iloc[-1])
            today_low = float(hist["Low"].iloc[-1])
            candle_range = today_high - today_low
            if candle_range > 0:
                candle_quality = round(((curr - today_low) / candle_range) * 100, 1)
            else:
                candle_quality = 100

            return {"ticker":ticker,"price":curr,"chg":chg,"rating":rating,"target":info.get("targetMeanPrice","N/A"),"vol_spike":vol_spike,"high":week52_high,"low":info.get("fiftyTwoWeekLow",0),"sector":info.get("sector","N/A"),"rsi":rsi_val,"pct_from_high":pct_from_high,"candle_quality":candle_quality}
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
    min_vol_pct = col3.number_input("Vol Spike %", value=0, step=25, help="e.g. 200 means volume is 2x normal")
    min_vol = min_vol_pct / 100
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
            st.session_state["manual_lookup"] = extra

        if not st.session_state.get("manual_lookup"):
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
                @st.fragment
                def render_results_list(results_list):
                    for r in results_list:
                        label = f"{r['ticker']} - ${round(r['price'],2)} - {r['chg']}% - {r['rating']}"
                        with st.expander(label, expanded=(auto_ai_strong and r["rating"]=="STRONG BUY")):
                            c1,c2,c3 = st.columns(3)
                            c1.metric("Price", f"${round(r['price'],2)}")
                            c2.metric("Change", f"{r['chg']}%")
                            c3.metric("Rating", r["rating"])
                            c4,c5,c6 = st.columns(3)
                            c4.metric("Target", f"${r['target']}")
                            c5.metric("52W High", f"${r['high']}")
                            c6.metric("52W Low", f"${r['low']}")
                            pct_high_r = r.get("pct_from_high", "N/A")
                            candle_r = r.get("candle_quality", "N/A")
                            rsi_r = r.get("rsi", "N/A")
                            st.caption("Sector: " + r.get("sector","N/A") + " | RSI: " + str(rsi_r) + " | " + str(pct_high_r) + "% below 52W high | Candle close: " + str(candle_r) + "%")

                            if show_ai or (auto_ai_strong and r["rating"]=="STRONG BUY"):
                                import anthropic
                                try:
                                    akey = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
                                except:
                                    akey = os.getenv("ANTHROPIC_KEY")
                                if akey:
                                    with st.spinner("Getting AI analysis..."):
                                        client = anthropic.Anthropic(api_key=akey)
                                        prompt = "Analyze " + r["ticker"] + " for a short-term momentum trade. Price $" + str(r["price"]) + ", change " + str(r["chg"]) + "%, rating " + r["rating"] + ", RSI " + str(r.get("rsi","N/A")) + ", volume spike " + str(r.get("vol_spike","N/A")) + "x, " + str(r.get("pct_from_high","N/A")) + "% below 52-week high, candle closed at " + str(r.get("candle_quality","N/A")) + "% of its range. Give 2-3 sentences of reasoning, then suggest a specific BUY entry price and a SELL target price for a short-term trade, formatted exactly as: BUY: $X.XX | SELL: $Y.YY. End with AI RATING: STRONG BUY/BUY/HOLD/AVOID. This is an algorithmic estimate for research only, not financial advice."
                                        msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=250, messages=[{"role":"user","content":prompt}])
                                    st.info(msg.content[0].text)

                            if st.button("+ Add to Watchlist", key="scanadd_" + r["ticker"], use_container_width=True):
                                fresh_watchlist = load_watchlist()
                                if r["ticker"] not in fresh_watchlist:
                                    fresh_watchlist.append(r["ticker"])
                                    save_watchlist(fresh_watchlist)
                                    st.session_state["watchlist_data"] = fresh_watchlist
                                    st.success("Added " + r["ticker"] + " to watchlist!")
                                else:
                                    st.info(r["ticker"] + " already in watchlist")

                render_results_list(display_results)

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
            label = f"{r['ticker']} - ${round(r['price'],2)} - {r['chg']}% - {r['rating']}"
            with st.expander(label, expanded=(auto_ai_strong and r["rating"]=="STRONG BUY")):
                c1,c2,c3 = st.columns(3)
                c1.metric("Price", f"${round(r['price'],2)}")
                c2.metric("Change", f"{r['chg']}%")
                c3.metric("Rating", r["rating"])
                c4,c5,c6 = st.columns(3)
                c4.metric("Target", f"${r['target']}")
                c5.metric("52W High", f"${r['high']}")
                c6.metric("52W Low", f"${r['low']}")
                pct_high_r2 = r.get("pct_from_high", "N/A")
                candle_r2 = r.get("candle_quality", "N/A")
                rsi_r2 = r.get("rsi", "N/A")
                st.caption("Sector: " + r.get("sector","N/A") + " | RSI: " + str(rsi_r2) + " | " + str(pct_high_r2) + "% below 52W high | Candle close: " + str(candle_r2) + "%")

                if show_ai or (auto_ai_strong and r["rating"]=="STRONG BUY"):
                    import anthropic
                    try:
                        akey2 = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
                    except:
                        akey2 = os.getenv("ANTHROPIC_KEY")
                    if akey2:
                        with st.spinner("Getting AI analysis..."):
                            client2 = anthropic.Anthropic(api_key=akey2)
                            prompt2 = "Analyze " + r["ticker"] + " for a short-term momentum trade. Price $" + str(r["price"]) + ", change " + str(r["chg"]) + "%, rating " + r["rating"] + ", RSI " + str(r.get("rsi","N/A")) + ", volume spike " + str(r.get("vol_spike","N/A")) + "x, " + str(r.get("pct_from_high","N/A")) + "% below 52-week high, candle closed at " + str(r.get("candle_quality","N/A")) + "% of its range. Give 2-3 sentences of reasoning, then suggest a specific BUY entry price and a SELL target price for a short-term trade, formatted exactly as: BUY: $X.XX | SELL: $Y.YY. End with AI RATING: STRONG BUY/BUY/HOLD/AVOID. This is an algorithmic estimate for research only, not financial advice."
                            msg2 = client2.messages.create(model="claude-sonnet-4-6", max_tokens=250, messages=[{"role":"user","content":prompt2}])
                        st.info(msg2.content[0].text)

                if st.button("+ Add to Watchlist", key="scanadd2_" + r["ticker"], use_container_width=True):
                    fresh_watchlist2 = load_watchlist()
                    if r["ticker"] not in fresh_watchlist2:
                        fresh_watchlist2.append(r["ticker"])
                        save_watchlist(fresh_watchlist2)
                        st.session_state["watchlist_data"] = fresh_watchlist2
                        st.success("Added " + r["ticker"] + " to watchlist!")
                    else:
                        st.info(r["ticker"] + " already in watchlist")

    if st.session_state.get("manual_lookup"):
        d = get_stock_data(st.session_state["manual_lookup"])
        if d:
            st.divider()
            st.success(f"Found {d['ticker']}")
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Price", f"${round(d['price'],2)}")
            c2.metric("Change", f"{d['chg']}%")
            c3.metric("Rating", d["rating"])
            c4.metric("Vol Spike", f"{d['vol_spike']}x")
            c5,c6,c7 = st.columns(3)
            c5.metric("Target", f"${d['target']}")
            c6.metric("52W High", f"${d['high']}")
            c7.metric("52W Low", f"${d['low']}")
            st.caption("Sector: " + d.get("sector","N/A") + " | RSI: " + str(d.get("rsi","N/A")))

            extra_col1, extra_col2 = st.columns(2)
            if extra_col1.button("+ Add to Watchlist", key="extra_add"):
                fresh_wl = load_watchlist()
                if d["ticker"] not in fresh_wl:
                    fresh_wl.append(d["ticker"])
                    save_watchlist(fresh_wl)
                    st.success("Added " + d["ticker"] + " to watchlist!")
                else:
                    st.info(d["ticker"] + " already in watchlist")

            if extra_col2.button("Get AI Analysis", key="extra_ai"):
                try:
                    akey4 = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
                except:
                    akey4 = os.getenv("ANTHROPIC_KEY")
                if akey4:
                    import anthropic
                    with st.spinner("Analyzing..."):
                        client4 = anthropic.Anthropic(api_key=akey4)
                        prompt4 = "Analyze " + d["ticker"] + " stock. Price $" + str(d["price"]) + ", change " + str(d["chg"]) + "%, RSI " + str(d.get("rsi","N/A")) + ", rating " + d["rating"] + ", target $" + str(d["target"]) + ". Give 2-3 sentence reasoning then end with SIGNAL: BUY or SIGNAL: SELL or SIGNAL: HOLD. Research only, not financial advice."
                        msg4 = client4.messages.create(model="claude-sonnet-4-6", max_tokens=200, messages=[{"role":"user","content":prompt4}])
                    st.info(msg4.content[0].text)
        else:
            st.error("Could not find " + st.session_state["manual_lookup"])

with tab2:
    st.title("My Watchlist")
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
        auto_threshold = st.number_input("Auto-alert if change % exceeds", value=5.0, step=1.0)
        st.divider()
        for ticker in watchlist:
            d = get_stock_data(ticker)
            if d:
                price = round(d["price"], 2)
                chg = d["chg"]
                rating = d["rating"]
                target = d["target"]
                high = d["high"]
                low = d["low"]
                sector = d["sector"]
                label = ticker + " - $" + str(price) + " - " + str(chg) + "% - " + rating
                with st.expander(label):
                    c1,c2,c3 = st.columns(3)
                    c1.metric("Price", "$" + str(price))
                    c2.metric("Change", str(chg) + "%")
                    c3.metric("Rating", rating)
                    c4,c5,c6 = st.columns(3)
                    c4.metric("Target", "$" + str(target))
                    c5.metric("52W High", "$" + str(high))
                    c6.metric("52W Low", "$" + str(low))
                    rsi_display = d.get("rsi", "N/A")
                    pct_high_display = d.get("pct_from_high", "N/A")
                    candle_q_display = d.get("candle_quality", "N/A")
                    st.caption("Sector: " + sector + " | RSI: " + str(rsi_display) + " | " + str(pct_high_display) + "% below 52W high | Candle close: " + str(candle_q_display) + "%")

                    @st.dialog("AI Signal")
                    def show_ai_signal_dialog(tkr, prc, chng, rsi_v, vspike, rtng, tgt, em, pct_high=None, candle_q=None):
                        st.subheader(tkr)
                        try:
                            akey3 = st.secrets.get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY"))
                        except:
                            akey3 = os.getenv("ANTHROPIC_KEY")
                        if akey3:
                            import anthropic
                            with st.spinner("Analyzing " + tkr + "..."):
                                client3 = anthropic.Anthropic(api_key=akey3)
                                extra_context = ""
                                if pct_high is not None:
                                    extra_context += " Stock is " + str(pct_high) + "% below its 52-week high (0% means at the high)."
                                if candle_q is not None:
                                    extra_context += " Today's candle closed at " + str(candle_q) + "% of its daily range (100% = closed at the high, strong; 0% = closed at the low, weak, long upper wick)."
                                prompt3 = "You are a stock trading assistant. Analyze " + tkr + " for a short-term momentum trade. Data: Price $" + str(prc) + ", change today " + str(chng) + "%, RSI " + str(rsi_v) + ", volume spike " + str(vspike) + "x, analyst rating " + rtng + ", target price $" + str(tgt) + "." + extra_context + " Give a 2-3 sentence reasoning, then suggest a specific BUY entry price and a SELL target price, formatted exactly as: BUY: $X.XX | SELL: $Y.YY. Then end with exactly one line: SIGNAL: BUY or SIGNAL: SELL or SIGNAL: HOLD. This is an algorithmic estimate for research only, not financial advice."
                                msg3 = client3.messages.create(model="claude-sonnet-4-6", max_tokens=200, messages=[{"role":"user","content":prompt3}])
                                ai_text = msg3.content[0].text
                            st.info(ai_text)

                            if "SIGNAL: BUY" in ai_text or "SIGNAL: SELL" in ai_text:
                                if em:
                                    signal_type = "BUY" if "SIGNAL: BUY" in ai_text else "SELL"
                                    email_subj = "AI " + signal_type + " Signal - " + tkr
                                    success3, msg_result3 = send_email_alert(em, email_subj, ai_text)
                                    if success3:
                                        st.success("AI " + signal_type + " signal emailed to you!")

                    if st.button("Get AI Buy/Sell Signal", key="aisig_" + ticker):
                        show_ai_signal_dialog(ticker, price, chg, rsi_display, d.get("vol_spike","N/A"), rating, target, alert_email, d.get("pct_from_high"), d.get("candle_quality"))
                    if abs(chg) >= auto_threshold and alert_email:
                        alert_key = "auto_sent_" + ticker
                        if alert_key not in st.session_state:
                            body = "AUTOMATIC ALERT: " + ticker + " moved " + str(chg) + "%\n\nPrice: $" + str(price) + "\nRating: " + rating + "\nTarget: $" + str(target)
                            subj = "BIG MOVE ALERT - " + ticker + " " + str(chg) + "%"
                            success, msg = send_email_alert(alert_email, subj, body)
                            if success:
                                st.session_state[alert_key] = True
                                st.success("Automatic alert sent for " + ticker)
                            else:
                                st.warning("Auto-alert failed: " + msg)
                        else:
                            st.info("Alert already sent for this move")
                    c7,c8 = st.columns(2)
                    if c7.button("Send Alert Now", key="alert_" + ticker):
                        if not alert_email:
                            st.error("Enter your email above first")
                        else:
                            body2 = "Stock Alert: " + ticker + "\n\nPrice: $" + str(price) + "\nChange: " + str(chg) + "%\nRating: " + rating + "\nTarget: $" + str(target)
                            subj2 = "Stock Scanner Pro Alert - " + ticker
                            success2, msg2 = send_email_alert(alert_email, subj2, body2)
                            if success2:
                                st.success("Alert sent for " + ticker)
                            else:
                                st.error("Failed: " + msg2)
                    if c8.button("Remove", key="rem_" + ticker):
                        watchlist.remove(ticker)
                        save_watchlist(watchlist)
                        st.rerun()
            else:
                st.warning("Could not fetch data for " + ticker)
    else:
        st.info("Watchlist is empty")
