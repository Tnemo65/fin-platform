"""Streamlit v1. Chạy: streamlit run app/Home.py  (cần API đang chạy, mặc định http://localhost:8000)"""
from __future__ import annotations

import os
import threading

import altair as alt
import pandas as pd
import requests
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="Finance Platform", page_icon="📈", layout="wide")


@st.cache_data(ttl=300)
def api(path: str, **params):
    r = requests.get(f"{API_URL}{path}", params={k: v for k, v in params.items() if v is not None}, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def api_parallel(calls: dict[str, tuple[str, dict]]) -> dict:
    """Gọi nhiều endpoint cùng lúc (mỗi call một luồng) thay vì chờ lần lượt: trang tải nhanh gấp số call."""
    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def run(name, path, params):
        try:
            results[name] = api(path, **params)
        except Exception as e:  # noqa: BLE001
            errors[name] = e

    threads = [add_script_run_ctx(threading.Thread(target=run, args=(n, p, kw), daemon=True))
               for n, (p, kw) in calls.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise next(iter(errors.values()))
    return results


def fmt_vnd(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "–"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:,.1f} tỷ"
    if a >= 1e6:
        return f"{v / 1e6:,.1f} tr"
    return f"{v:,.0f}"


st.title("📈 Finance Platform")

# ----------------------------------------------------------------- tìm mã
query = st.text_input("Tìm mã hoặc tên công ty", placeholder="VNM, Hòa Phát, ...").strip()
ticker = None
if query:
    try:
        matches = api("/symbols", q=query, limit=20) or []
    except requests.RequestException as e:
        st.error(f"Không gọi được API {API_URL}: {e}")
        st.stop()
    if not matches:
        st.warning("Không tìm thấy mã phù hợp.")
        st.stop()
    labels = {f"{m['ticker']} · {m.get('short_name') or m.get('company_name') or ''} ({m.get('exchange') or '?'})": m["ticker"]
              for m in matches}
    ticker = labels[st.selectbox("Chọn mã", list(labels), index=0)]

if not ticker:
    st.info("Nhập mã cổ phiếu để xem giá, BCTC và tin tức.")
    st.stop()

# Vẽ khung + điều khiển của 3 khối trước, rồi gọi 4 API song song, sau đó điền dữ liệu vào từng khung.
head = st.container()
sec_price, sec_fin, sec_news = st.container(), st.container(), st.container()

with sec_price:
    st.markdown("### Biểu đồ giá")
    rng = st.radio("Khoảng thời gian", ["3T", "6T", "1N", "3N", "Tất cả"], index=2, horizontal=True)
    days = {"3T": 66, "6T": 130, "1N": 252, "3N": 756, "Tất cả": 10000}[rng]
with sec_fin:
    st.markdown("### Báo cáo tài chính theo quý")
    col_a, col_b = st.columns([3, 1])
    key_only = col_b.toggle("Chỉ chỉ tiêu chính", value=True)
    n_periods = col_b.slider("Số quý", 4, 12, 8)
with sec_news:
    st.markdown("### Tin tức")

try:
    data = api_parallel({
        "sym": (f"/symbols/{ticker}", {}),
        "prices": (f"/symbols/{ticker}/prices", {"limit": days}),
        "fin": (f"/symbols/{ticker}/financials", {"periods": n_periods, "key_only": key_only}),
        "news": (f"/symbols/{ticker}/news", {"limit": 50}),
    })
except requests.RequestException as e:
    st.error(f"Không gọi được API {API_URL}: {e}")
    st.stop()

sym = data["sym"] or {}
with head:
    st.subheader(f"{ticker} · {sym.get('company_name') or ''}")
    st.caption(" · ".join(x for x in [sym.get("exchange"), sym.get("industry")] if x))

# ----------------------------------------------------------------- 1. biểu đồ giá
sec_price.__enter__()
prices = pd.DataFrame((data["prices"] or {}).get("prices", []))
if prices.empty:
    st.info("Chưa có dữ liệu giá.")
else:
    prices["date"] = pd.to_datetime(prices["date"])
    last, prev = prices.iloc[-1], prices.iloc[-2] if len(prices) > 1 else prices.iloc[-1]
    chg = (last["close"] - prev["close"]) / prev["close"] * 100 if prev["close"] else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Giá đóng cửa", f"{last['close']:,.0f} đ", f"{chg:+.2f}%")
    c2.metric("Khối lượng", f"{last['volume']:,.0f}" if pd.notna(last["volume"]) else "–")
    c3.metric("Ngày", last["date"].strftime("%d/%m/%Y"))

    base = alt.Chart(prices).encode(x=alt.X("date:T", title=None))
    color = alt.condition("datum.open <= datum.close", alt.value("#16a34a"), alt.value("#dc2626"))
    tooltip = [alt.Tooltip("date:T", title="Ngày"), alt.Tooltip("open:Q", format=",.0f", title="Mở"),
               alt.Tooltip("high:Q", format=",.0f", title="Cao"), alt.Tooltip("low:Q", format=",.0f", title="Thấp"),
               alt.Tooltip("close:Q", format=",.0f", title="Đóng"), alt.Tooltip("volume:Q", format=",.0f", title="KL")]
    rule = base.mark_rule().encode(y=alt.Y("low:Q", title="Giá (VND)", scale=alt.Scale(zero=False)), y2="high:Q",
                                   color=color, tooltip=tooltip)
    bar = base.mark_bar().encode(y="open:Q", y2="close:Q", color=color, tooltip=tooltip)
    vol = base.mark_bar(opacity=0.5).encode(y=alt.Y("volume:Q", title="KL"), color=color).properties(height=100)
    st.altair_chart((rule + bar).properties(height=340), use_container_width=True)
    st.altair_chart(vol, use_container_width=True)
sec_price.__exit__(None, None, None)

# ----------------------------------------------------------------- 2. BCTC theo quý
sec_fin.__enter__()
fin = data["fin"]
items = pd.DataFrame(fin["items"]) if fin else pd.DataFrame()
if items.empty:
    st.info("Chưa có dữ liệu BCTC.")
else:
    tabs = st.tabs([fin["statements"][s] for s in ["IS", "BS", "CF"] if s in set(items["statement"])])
    for tab, stmt in zip(tabs, [s for s in ["IS", "BS", "CF"] if s in set(items["statement"])]):
        df = items[items["statement"] == stmt]
        periods = sorted(df["period"].unique())
        order = df.drop_duplicates("item_code")[["item_code", "item_name"]]

        def cell(row, p):
            if pd.isna(row.get(("value", p))):
                return "–"
            yoy = row.get(("yoy_pct", p))
            arrow = "" if pd.isna(yoy) else (f" ▲{yoy:.0f}%" if yoy > 0 else (f" ▼{abs(yoy):.0f}%" if yoy < 0 else " ="))
            return fmt_vnd(row[("value", p)]) + arrow

        wide = df.pivot_table(index="item_code", columns="period", values=["value", "yoy_pct"], aggfunc="first")
        table = pd.DataFrame({p: [cell(wide.loc[c], p) for c in order["item_code"]] for p in periods},
                             index=order["item_name"].tolist())

        def color(v: str):
            return "color:#16a34a" if "▲" in v else ("color:#dc2626" if "▼" in v else "")

        with tab:
            st.dataframe(table.style.map(color), use_container_width=True)
            st.caption("Đơn vị VND. ▲/▼: tăng/giảm so với cùng kỳ năm trước.")
    if fin.get("ratios"):
        r = pd.DataFrame(fin["ratios"]).set_index("period")
        r["roe"] = r["roe"].map(lambda x: f"{x * 100:.1f}%" if pd.notna(x) else "–")
        r["eps"] = r["eps"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "–")
        r["pe"] = r["pe"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "–")
        r["pb"] = r["pb"].map(lambda x: f"{x:.2f}" if pd.notna(x) else "–")
        st.markdown("**Chỉ số**")
        st.dataframe(r.rename(columns={"pe": "P/E", "pb": "P/B", "roe": "ROE", "eps": "EPS (đ)"}).T,
                     use_container_width=True)
sec_fin.__exit__(None, None, None)

# ----------------------------------------------------------------- 3. timeline tin tức
sec_news.__enter__()
news = (data["news"] or {}).get("news", [])
if not news:
    st.info("Chưa có tin gắn với mã này.")
for n in news:
    when = pd.to_datetime(n["published_at"]).tz_convert("Asia/Ho_Chi_Minh").strftime("%d/%m/%Y %H:%M") \
        if n.get("published_at") else "?"
    badge = "📢 Công bố" if n["kind"] == "disclosure" else "📰"
    others = f" · cùng nhắc: {', '.join(n['other_tickers'])}" if n.get("other_tickers") else ""
    st.markdown(f"**{when}** {badge} [{n['title']}]({n['url']})  \n"
                f"<small>{n['source']}{others}</small>", unsafe_allow_html=True)
    if n.get("summary"):
        st.caption(n["summary"][:280])
sec_news.__exit__(None, None, None)
