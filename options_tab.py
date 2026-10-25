"""Options tab for Stock Scanner Pro.

Shows what the options screener on the MacBook Air (~/options_screener) found and which
spreads it is watching for exits. The screener writes to two Supabase tables:
options_screener_scans and options_screener_positions. This tab only reads them.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

CT = ZoneInfo("America/Chicago")
TP_PCT, STOP_PCT, BE_PCT = 0.50, -0.35, 0.25   # same exit rules as the screener


def _side(s):
    return {"C": "Call", "P": "Put"}.get(s, s or "")


def _rows(sb, table, **kw):
    q = sb.table(table).select("*")
    for col, val in kw.get("eq", {}).items():
        q = q.eq(col, val)
    if kw.get("order"):
        q = q.order(kw["order"], desc=True)
    return q.limit(kw.get("limit", 1000)).execute().data or []


def positions_frame(rows):
    out = []
    for p in rows:
        entry = float(p["entry"] or 0)
        last = p.get("last_value")
        pnl = (float(last) - entry) / entry * 100 if (last is not None and entry) else None
        out.append({
            "ID": p["alert_id"],
            "Spread": f"{p['symbol']} {_side(p['side'])} {float(p['long_strike']):g}/{float(p['short_strike']):g}",
            "Expiry": p.get("expiry"),
            "Bought at": round(entry, 2),
            "Now": round(float(last), 2) if last is not None else None,
            "P/L %": round(pnl, 1) if pnl is not None else None,
            "Take profit": round(entry * (1 + TP_PCT), 2),
            "Stop": round(entry if p.get("be_armed") else entry * (1 + STOP_PCT), 2),
            "Invalidation": p.get("invalidate"),
        })
    return pd.DataFrame(out)


def scans_frame(rows):
    out = []
    for r in rows:
        spread = ""
        if r.get("long_strike") is not None:
            spread = f"{_side(r.get('side'))} {float(r['long_strike']):g}/{float(r['short_strike']):g}"
        out.append({
            "Time": pd.to_datetime(r["scanned_at"]).tz_convert(CT).strftime("%H:%M"),
            "Symbol": r["symbol"],
            "Result": "PASS" if r["passed"] else "skip",
            "Spread": spread,
            "Expiry": r.get("expiry") or "",
            "Mid": r.get("mid"),
            "Limit": r.get("limit_price"),
            "Max loss $": r.get("max_loss"),
            "RVOL": r.get("rvol"),
            "Why": r.get("reason") or "",
            "Alert sent": "yes" if r.get("alerted") else "",
            "ID": r.get("alert_id") or "",
        })
    return pd.DataFrame(out)


@st.fragment(run_every=5)
def _scan_status(sb):
    """Shows the Scan button's progress. The MacBook Air picks the request up within about a minute."""
    try:
        r = (sb.table("options_screener_requests").select("*").order("requested_at", desc=True)
             .limit(1).execute().data or [None])[0]
    except Exception as e:
        st.caption(f"Scan status unavailable: {e}")
        return
    if not r:
        return
    asked = pd.to_datetime(r["requested_at"]).tz_convert(CT)
    waited = (dt.datetime.now(CT) - asked).total_seconds()
    status = r["status"]
    if status == "pending":
        if waited > 120:
            st.warning(f"Scan requested at {asked:%H:%M} is still waiting. The MacBook Air may be asleep or offline.")
        else:
            st.info(f"Scan requested at {asked:%H:%M}. Waiting for the MacBook Air to start it (up to a minute)...")
    elif status == "running":
        st.info(f"Scanning now (started {pd.to_datetime(r['started_at']).tz_convert(CT):%H:%M:%S})...")
    elif status == "done":
        done = pd.to_datetime(r["finished_at"]).tz_convert(CT)
        if st.session_state.get("opt_seen_done") != r["id"]:
            st.session_state.opt_seen_done = r["id"]
            st.rerun(scope="app")          # reload the tables below with the new results
        st.success(f"Scan finished at {done:%H:%M:%S} CT. Results are below.")
        st.session_state.opt_scan_at = None
    elif status == "expired":
        st.warning("That scan request expired: the MacBook Air didn't pick it up within 15 minutes.")
        st.session_state.opt_scan_at = None
    else:
        st.error(f"Scan failed: {r.get('note') or 'unknown error'}")
        st.session_state.opt_scan_at = None


def render(get_supabase):
    st.title("🎯 Options Screener")
    st.caption("Debit-spread alerts from the screener on your MacBook Air. It only alerts; it never places orders. "
               "Not financial advice.")
    st.info("After you buy or sell, type it to **@Dennys_oprions_screener_bot** in Telegram: "
            "`buy 0.88`, `sell`, or `list`.")

    try:
        sb = get_supabase()
    except Exception as e:
        st.error(f"Supabase not configured: {e}")
        return

    day = st.date_input("Day", value=dt.datetime.now(CT).date(), key="opt_day")
    if st.button("🔍 Scan", key="opt_scan", type="primary"):
        try:
            sb.table("options_screener_requests").insert({"status": "pending"}).execute()
            st.session_state.opt_scan_at = dt.datetime.now(CT)
        except Exception as e:
            st.error(f"Could not request a scan: {e}")
    if st.session_state.get("opt_scan_at"):
        _scan_status(sb)

    try:
        scans = _rows(sb, "options_screener_scans", eq={"day": str(day)}, order="scanned_at", limit=3000)
        open_pos = _rows(sb, "options_screener_positions", eq={"status": "open"}, order="opened_at", limit=50)
        closed = _rows(sb, "options_screener_positions", eq={"status": "closed"}, order="closed_at", limit=20)
    except Exception as e:
        st.error(f"Could not read the options tables: {e}")
        return

    # Health: is the screener on the Air actually running?
    now = dt.datetime.now(CT)
    if scans:
        last = pd.to_datetime(scans[0]["scanned_at"]).tz_convert(CT)
        mins = (now - last).total_seconds() / 60
        market = now.weekday() < 5 and dt.time(9, 0) <= now.time() <= dt.time(14, 0) and day == now.date()
        if market and mins > 12:
            st.warning(f"Last scan was {mins:.0f} minutes ago. The MacBook Air may be asleep or offline.")
        else:
            st.caption(f"Last scan: {last:%Y-%m-%d %H:%M} CT")
    else:
        st.caption("No scans for this day yet. The screener runs every 5 minutes, 9:00-14:00 CT on weekdays, "
                   "or tap Scan to run one now.")

    st.subheader("Watching (exit alerts on)")
    if open_pos:
        st.dataframe(positions_frame(open_pos), hide_index=True, use_container_width=True)
    else:
        st.write("Nothing open. Type `buy <price>` to the bot after you enter an alert.")

    df = scans_frame(scans)
    st.subheader("Passed today")
    passed = df[df["Result"] == "PASS"].drop_duplicates("Symbol") if not df.empty else df
    if not passed.empty:
        st.dataframe(passed.drop(columns=["Result"]), hide_index=True, use_container_width=True)
    else:
        st.write("No spreads have passed every rule yet.")

    st.subheader("Latest scan")
    if not df.empty:
        latest = df[df["Time"] == df["Time"].iloc[0]]
        st.dataframe(latest[["Symbol", "Result", "Spread", "Mid", "RVOL", "Why"]], hide_index=True,
                     use_container_width=True)
        with st.expander("Why stocks were skipped today (counts)"):
            skips = df[df["Result"] == "skip"]["Why"].str.replace(r"[\d.]+", "#", regex=True)
            st.dataframe(skips.value_counts().rename("Times").to_frame(), use_container_width=True)

    if closed:
        st.subheader("Recently closed")
        cdf = pd.DataFrame([{
            "ID": p["alert_id"],
            "Spread": f"{p['symbol']} {_side(p['side'])} {float(p['long_strike']):g}/{float(p['short_strike']):g}",
            "Bought at": p.get("entry"),
            "Last value": p.get("last_value"),
            "Why closed": p.get("exit_reason"),
            "Closed": pd.to_datetime(p["closed_at"]).tz_convert(CT).strftime("%m-%d %H:%M") if p.get("closed_at") else "",
        } for p in closed])
        st.dataframe(cdf, hide_index=True, use_container_width=True)
