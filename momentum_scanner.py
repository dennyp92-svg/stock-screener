import yfinance as yf
import pandas as pd
import streamlit as st
import os, json
from datetime import datetime
ALL_TICKERS=["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","AMD","ORCL","PLTR","CRM","SNOW","DDOG","NET","ARM","SMCI","SOFI","MSTR","COIN","NFLX","DIS","ROKU","SPOT","UBER","ABNB","SQ","PYPL","HOOD","NU","V","MA","JPM","BAC","WFC","GS","MS","XOM","CVX","COP","OXY","JNJ","PFE","MRNA","LLY","ABBV","BMY","MRK","AMGN","COST","WMT","TGT","HD","LOW","BA","LMT","RTX","NOC","NIO","RIVN","LCID","XPEV","F","GM","INTC","QCOM","MU","AMAT","KLAC","TXN","ADI","MRVL","ENPH","FSLR","ALAB","AEHR","IOT","COHR","SITM","MARA","RIOT","CRWD","PANW","ZM","SHOP","BABA","JD","PDD","RKLB","ASTS","GME","AMC","IREN","CLSK","HUT","BTBT","CIFR","IBIT","ARKK","ARKG","ARKW","IONQ","RGTI","QUBT","QBTS","ACHR","JOBY","LILM","WKHS","NKLA","LAZR","LYFT","GRAB","ARGX","ASML","AXON","AVXL","AZPN","ASAN","ARWR","ARVN","AUPH","AUTL","APLS","APLT","AGIO","ACMR","PRTA","AMLX","ANIP","VRTX","REGN","BIIB","ILMN","PACB","SGEN","ALNY","RARE","BMRN","FOLD","KRYS","PTGX","NUVL","KYMR","RVMD","BEAM","EDIT","NTLA","CRSP","BLUE","FATE","KITE","IMVT","INVA","ITCI","JAZZ","LGND","LMNX","LNTH","MGNX","MNKD","MNTA","MNTX","MODN","MORF","MRNS","MRTX","MRUS","MSRT","MTEX","MTEM","MTNB","MTOR","MTRX","MTSI","MTTR","MTUS","MVEN","MVIS","MVST","MXCT","MYFW","MYGN","MYMD","MYNZ","MYPS","MYRG","MYSZ"]
WATCHLIST_FILE=os.path.expanduser("~/stock_screener/watchlist.json")
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE) as wf: return json.load(wf)
    return []
def save_watchlist(wl):
    with open(WATCHLIST_FILE,"w") as wf: json.dump(wl,wf)
from concurrent.futures import ThreadPoolExecutor
def get_stock_data(ticker):
    try:
        stock=yf.Ticker(ticker)
        info=stock.info
        hist=stock.history(period="5d").dropna(subset=["Close"])
        if len(hist)>=2:
            prev=float(hist["Close"].iloc[-2])
            curr=float(hist["Close"].iloc[-1])
            chg=round(((curr-prev)/prev)*100,2)
            vol=int(hist["Volume"].iloc[-1])
            avg_vol=int(hist["Volume"].mean())
            vol_spike=round(vol/avg_vol,2) if avg_vol>0 else 0
            rec=info.get("recommendationKey","none")
            rating="⭐ STRONG BUY" if rec=="strong_buy" else "✅ BUY" if rec=="buy" else "⏸ HOLD" if rec=="hold" else "❌ SELL" if rec=="sell" else "➖ N/A"
            return {"ticker":ticker,"price":curr,"chg":chg,"rating":rating,"target":info.get("targetMeanPrice","N/A"),"vol_spike":vol_spike,"high":info.get("fiftyTwoWeekHigh",0),"low":info.get("fiftyTwoWeekLow",0),"sector":info.get("sector","N/A")}
    except: pass
    return None
st.set_page_config(page_title="Stock Scanner Pro",page_icon="📈",layout="wide")

watchlist=load_watchlist()
tab1,tab2=st.tabs(["📈 Scanner","⭐ Watchlist"])
with tab1:
    st.title("📈 Stock Scanner Pro")
    st.caption(f"Scanning {len(ALL_TICKERS)} stocks")
    with st.sidebar:
        st.subheader("⚙️ Filters")
        st.caption("% Change")
        col1,col2=st.columns(2)
        min_change=col1.number_input("Min %",value=0)
        max_change=col2.number_input("Max %",value=100)
        st.caption("Price Range")
        col3,col4=st.columns(2)
        min_price=col3.number_input("Min $",value=1)
        max_price=col4.number_input("Max $",value=1000)
        min_vol=st.number_input("Min Vol Spike",value=0.0,step=0.5)
        show_ai=st.checkbox("🤖 AI Analysis",value=False)
        extra=st.text_input("Look up any ticker","").upper().strip()
        run=st.button("🚀 Run Scan",use_container_width=True)
    if run:
        if extra:
            d = get_stock_data(extra)
            if d:
                st.success(f"Found {extra}!")
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Price", f"${round(d['price'],2)}")
                c2.metric("Change", f"{d['chg']}%")
                c3.metric("Rating", d["rating"])
                c4.metric("Vol Spike", f"{d['vol_spike']}x")
                if st.button("+ Watchlist", key="ws"):
                    if extra not in watchlist:
                        watchlist.append(extra)
                        save_watchlist(watchlist)
                        st.success(f"Added {extra}!")
            else:
                st.error(f"Could not find {extra}")
        else:
            results = []
            bar = st.progress(0, text="Scanning all stocks at once...")
            with ThreadPoolExecutor(max_workers=10) as executor:
                all_data = list(executor.map(get_stock_data, ALL_TICKERS))
            bar.progress(1.0, text="Done!")
            for d in all_data:
                if d:
                    price_ok = float(min_price)<=d["price"]<=float(max_price)
                    change_ok = float(min_change)<=d["chg"]<=float(max_change)
                    vol_ok = d["vol_spike"]>=float(min_vol) if min_vol>0 else True
                    if price_ok and change_ok and vol_ok:
                        results.append(d)
            bar.empty()
            if results:
                results = sorted(results, key=lambda x: x["chg"], reverse=True)
                c1,c2,c3 = st.columns(3)
                c1.metric("Scanned", len(ALL_TICKERS))
                c2.metric("Passed", len(results))
                c3.metric("Strong Buys", sum(1 for r in results if "STRONG BUY" in r["rating"]))
                st.divider()
                for r in results:
                    with st.expander(f"{r['ticker']} — ${round(r['price'],2)} — {r['chg']}% — {r['rating']}"):
                        c1,c2,c3,c4 = st.columns(4)
                        c1.metric("Price", f"${round(r['price'],2)}")
                        c2.metric("Change", f"{r['chg']}%")
                        c3.metric("Target", f"${r['target']}")
                        c4.metric("Vol Spike", f"{r['vol_spike']}x")
                        c5,c6,c7 = st.columns(3)
                        c5.metric("52W High", f"${r['high']}")
                        c6.metric("52W Low", f"${r['low']}")
                        c7.metric("Sector", r["sector"])
                        if show_ai:
                            import anthropic, os
                            from dotenv import load_dotenv
                            load_dotenv()
                            akey = os.getenv("ANTHROPIC_KEY")
                            if akey:
                                with st.spinner("Getting AI analysis..."):
                                    client = anthropic.Anthropic(api_key=akey)
                                    prompt = f"Analyze {r[chr(39)+chr(116)+chr(105)+chr(99)+chr(107)+chr(101)+chr(114)+chr(39)]} in 3 sentences. Price ${r[chr(39)+chr(112)+chr(114)+chr(105)+chr(99)+chr(101)+chr(39)]}, change {r[chr(39)+chr(99)+chr(104)+chr(103)+chr(39)]}%. End with AI RATING: STRONG BUY/BUY/HOLD/AVOID."
                                    msg = client.messages.create(model="claude-sonnet-4-6",max_tokens=150,messages=[{"role":"user","content":prompt}])
                                st.info(msg.content[0].text)

                        if st.button("+ Watchlist", key=f"w_{r['ticker']}"):
                            if r["ticker"] not in watchlist:
                                watchlist.append(r["ticker"])
                                save_watchlist(watchlist)
                                st.success(f"Added!")
                st.download_button("⬇️ Download CSV", pd.DataFrame(results).to_csv(index=False).encode(), "results.csv")
            else:
                st.warning("No stocks found. Try wider filters.")
    else:
        st.info("Set filters and click Run Scan")
with tab2:
    st.title("⭐ My Watchlist")
    add_manual = st.text_input("Add ticker", "").upper().strip()
    if st.button("Add") and add_manual:
        if add_manual not in watchlist:
            watchlist.append(add_manual)
            save_watchlist(watchlist)
            st.success(f"Added {add_manual}!")
        else:
            st.warning(f"{add_manual} already in watchlist")
    if watchlist:
        refresh = st.button("🔄 Refresh")
        for ticker in watchlist:
            c1,c2 = st.columns([4,1])
            with c2:
                if st.button("Remove", key=f"rem_{ticker}"):
                    watchlist.remove(ticker)
                    save_watchlist(watchlist)
                    st.rerun()
            with c1:
                if refresh:
                    d = get_stock_data(ticker)
                    if d: st.write(f"{ticker} — ${round(d['price'],2)} — {d['chg']}% — {d[chr(39)+chr(114)+chr(97)+chr(116)+chr(105)+chr(110)+chr(103)+chr(39)]}")
                else: st.write(f"📊 {ticker}")
    else:
        st.info("Watchlist is empty")ALL_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AVGO', 'AMD', 'ORCL', 'PLTR', 'CRM', 'SNOW', 'DDOG', 'NET', 'ARM', 'SMCI', 'SOFI', 'MSTR', 'COIN', 'NFLX', 'DIS', 'ROKU', 'SPOT', 'UBER', 'ABNB', 'SQ', 'PYPL', 'HOOD', 'NU', 'V', 'MA', 'JPM', 'BAC', 'WFC', 'GS', 'MS', 'XOM', 'CVX', 'COP', 'OXY', 'JNJ', 'PFE', 'MRNA', 'LLY', 'ABBV', 'BMY', 'MRK', 'AMGN', 'COST', 'WMT', 'TGT', 'HD', 'LOW', 'BA', 'LMT', 'RTX', 'NOC', 'NIO', 'RIVN', 'LCID', 'XPEV', 'F', 'GM', 'INTC', 'QCOM', 'MU', 'AMAT', 'KLAC', 'TXN', 'ADI', 'MRVL', 'ENPH', 'FSLR', 'ALAB', 'AEHR', 'IOT', 'COHR', 'SITM', 'MARA', 'RIOT', 'CRWD', 'PANW', 'ZM', 'SHOP', 'BABA', 'JD', 'PDD', 'RKLB', 'ASTS', 'GME', 'AMC', 'IREN', 'CLSK', 'HUT', 'BTBT', 'CIFR', 'IBIT', 'ARKK', 'ARKG', 'ARKW', 'IONQ', 'RGTI', 'QUBT', 'QBTS', 'ACHR', 'JOBY', 'LILM', 'WKHS', 'NKLA', 'LAZR', 'LYFT', 'GRAB', 'ARGX', 'ASML', 'AXON', 'AVDL', 'AVXL', 'AZPN', 'ASAN', 'ARWR', 'ARVN', 'AUPH', 'AUTL', 'APLS', 'APLT', 'AGIO', 'ACMR', 'PRTA', 'AMLX', 'ANIP']import yfinance as yf
import pandas as pd
import streamlit as st
import os, json
from datetime import datetime
ALL_TICKERS=["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","AMD","ORCL","PLTR","CRM","SNOW","DDOG","NET","ARM","SMCI","SOFI","MSTR","COIN","NFLX","DIS","ROKU","SPOT","UBER","ABNB","SQ","PYPL","HOOD","NU","V","MA","JPM","BAC","WFC","GS","MS","XOM","CVX","COP","OXY","JNJ","PFE","MRNA","LLY","ABBV","BMY","MRK","AMGN","COST","WMT","TGT","HD","LOW","BA","LMT","RTX","NOC","NIO","RIVN","LCID","XPEV","F","GM","INTC","QCOM","MU","AMAT","KLAC","TXN","ADI","MRVL","ENPH","FSLR","ALAB","AEHR","IOT","COHR","SITM","MARA","RIOT","CRWD","PANW","ZM","SHOP","BABA","JD","PDD","RKLB","ASTS","GME","AMC"]
WATCHLIST_FILE=os.path.expanduser("~/stock_screener/watchlist.json")
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE) as wf: return json.load(wf)
    return []
def save_watchlist(wl):
    with open(WATCHLIST_FILE,"w") as wf: json.dump(wl,wf)
from concurrent.futures import ThreadPoolExecutor
def get_stock_data(ticker):
    try:
        stock=yf.Ticker(ticker)
        info=stock.info
        hist=stock.history(period="5d").dropna(subset=["Close"])
        if len(hist)>=2:
            prev=float(hist["Close"].iloc[-2])
            curr=float(hist["Close"].iloc[-1])
            chg=round(((curr-prev)/prev)*100,2)
            vol=int(hist["Volume"].iloc[-1])
            avg_vol=int(hist["Volume"].mean())
            vol_spike=round(vol/avg_vol,2) if avg_vol>0 else 0
            rec=info.get("recommendationKey","none")
            rating="⭐ STRONG BUY" if rec=="strong_buy" else "✅ BUY" if rec=="buy" else "⏸ HOLD" if rec=="hold" else "❌ SELL" if rec=="sell" else "➖ N/A"
            return {"ticker":ticker,"price":curr,"chg":chg,"rating":rating,"target":info.get("targetMeanPrice","N/A"),"vol_spike":vol_spike,"high":info.get("fiftyTwoWeekHigh",0),"low":info.get("fiftyTwoWeekLow",0),"sector":info.get("sector","N/A")}
    except: pass
    return None
st.set_page_config(page_title="Stock Scanner Pro",page_icon="📈",layout="wide")

watchlist=load_watchlist()
tab1,tab2=st.tabs(["📈 Scanner","⭐ Watchlist"])
with tab1:
    st.title("📈 Stock Scanner Pro")
    st.caption(f"Scanning {len(ALL_TICKERS)} stocks")
    with st.sidebar:
        st.subheader("⚙️ Filters")
        st.caption("% Change")
        col1,col2=st.columns(2)
        min_change=col1.number_input("Min %",value=0)
        max_change=col2.number_input("Max %",value=100)
        st.caption("Price Range")
        col3,col4=st.columns(2)
        min_price=col3.number_input("Min $",value=1)
        max_price=col4.number_input("Max $",value=1000)
        min_vol=st.number_input("Min Vol Spike",value=0.0,step=0.5)
        show_ai=st.checkbox("🤖 AI Analysis",value=False)
        extra=st.text_input("Look up any ticker","").upper().strip()
        run=st.button("🚀 Run Scan",use_container_width=True)
    if run:
        if extra:
            d = get_stock_data(extra)
            if d:
                st.success(f"Found {extra}!")
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Price", f"${round(d['price'],2)}")
                c2.metric("Change", f"{d['chg']}%")
                c3.metric("Rating", d["rating"])
                c4.metric("Vol Spike", f"{d['vol_spike']}x")
                if st.button("+ Watchlist", key="ws"):
                    if extra not in watchlist:
                        watchlist.append(extra)
                        save_watchlist(watchlist)
                        st.success(f"Added {extra}!")
            else:
                st.error(f"Could not find {extra}")
        else:
            results = []
            bar = st.progress(0, text="Scanning all stocks at once...")
            with ThreadPoolExecutor(max_workers=10) as executor:
                all_data = list(executor.map(get_stock_data, ALL_TICKERS))
            bar.progress(1.0, text="Done!")
            for d in all_data:
                if d:
                    price_ok = float(min_price)<=d["price"]<=float(max_price)
                    change_ok = float(min_change)<=d["chg"]<=float(max_change)
                    vol_ok = d["vol_spike"]>=float(min_vol) if min_vol>0 else True
                    if price_ok and change_ok and vol_ok:
                        results.append(d)
            bar.empty()
            if results:
                results = sorted(results, key=lambda x: x["chg"], reverse=True)
                c1,c2,c3 = st.columns(3)
                c1.metric("Scanned", len(ALL_TICKERS))
                c2.metric("Passed", len(results))
                c3.metric("Strong Buys", sum(1 for r in results if "STRONG BUY" in r["rating"]))
                st.divider()
                for r in results:
                    with st.expander(f"{r['ticker']} — ${round(r['price'],2)} — {r['chg']}% — {r['rating']}"):
                        c1,c2,c3,c4 = st.columns(4)
                        c1.metric("Price", f"${round(r['price'],2)}")
                        c2.metric("Change", f"{r['chg']}%")
                        c3.metric("Target", f"${r['target']}")
                        c4.metric("Vol Spike", f"{r['vol_spike']}x")
                        c5,c6,c7 = st.columns(3)
                        c5.metric("52W High", f"${r['high']}")
                        c6.metric("52W Low", f"${r['low']}")
                        c7.metric("Sector", r["sector"])
                        if show_ai:
                            import anthropic, os
                            from dotenv import load_dotenv
                            load_dotenv()
                            akey = os.getenv("ANTHROPIC_KEY")
                            if akey:
                                with st.spinner("Getting AI analysis..."):
                                    client = anthropic.Anthropic(api_key=akey)
                                    prompt = f"Analyze {r[chr(39)+chr(116)+chr(105)+chr(99)+chr(107)+chr(101)+chr(114)+chr(39)]} in 3 sentences. Price ${r[chr(39)+chr(112)+chr(114)+chr(105)+chr(99)+chr(101)+chr(39)]}, change {r[chr(39)+chr(99)+chr(104)+chr(103)+chr(39)]}%. End with AI RATING: STRONG BUY/BUY/HOLD/AVOID."
                                    msg = client.messages.create(model="claude-sonnet-4-6",max_tokens=150,messages=[{"role":"user","content":prompt}])
                                st.info(msg.content[0].text)

                        if st.button("+ Watchlist", key=f"w_{r['ticker']}"):
                            if r["ticker"] not in watchlist:
                                watchlist.append(r["ticker"])
                                save_watchlist(watchlist)
                                st.success(f"Added!")
                st.download_button("⬇️ Download CSV", pd.DataFrame(results).to_csv(index=False).encode(), "results.csv")
            else:
                st.warning("No stocks found. Try wider filters.")
    else:
        st.info("Set filters and click Run Scan")
with tab2:
    st.title("⭐ My Watchlist")
    add_manual = st.text_input("Add ticker", "").upper().strip()
    if st.button("Add") and add_manual:
        if add_manual not in watchlist:
            watchlist.append(add_manual)
            save_watchlist(watchlist)
            st.success(f"Added {add_manual}!")
        else:
            st.warning(f"{add_manual} already in watchlist")
    if watchlist:
        refresh = st.button("🔄 Refresh")
        for ticker in watchlist:
            c1,c2 = st.columns([4,1])
            with c2:
                if st.button("Remove", key=f"rem_{ticker}"):
                    watchlist.remove(ticker)
                    save_watchlist(watchlist)
                    st.rerun()
            with c1:
                if refresh:
                    d = get_stock_data(ticker)
                    if d: st.write(f"{ticker} — ${round(d['price'],2)} — {d['chg']}% — {d[chr(39)+chr(114)+chr(97)+chr(116)+chr(105)+chr(110)+chr(103)+chr(39)]}")
                else: st.write(f"📊 {ticker}")
    else:
        st.info("Watchlist is empty")
