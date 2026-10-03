"""Trang tình trạng dữ liệu: lần cập nhật gần nhất của từng nguồn và các job bị lỗi."""
from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
st.set_page_config(page_title="Tình trạng dữ liệu", page_icon="🩺", layout="wide")
st.title("🩺 Tình trạng dữ liệu")

days = st.slider("Job lỗi trong số ngày gần nhất", 1, 30, 7)
try:
    data = requests.get(f"{API_URL}/status", params={"days": days}, timeout=30).json()
except requests.RequestException as e:
    st.error(f"Không gọi được API {API_URL}: {e}")
    st.stop()


def to_vn(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col, utc=True).dt.tz_convert("Asia/Ho_Chi_Minh").dt.strftime("%d/%m %H:%M")


st.markdown("### Lần cập nhật gần nhất theo nguồn")
src = pd.DataFrame(data["sources"])
if src.empty:
    st.info("Chưa có nguồn nào chạy.")
else:
    age_h = (pd.Timestamp.now(tz="UTC") - pd.to_datetime(src["last_fetched"], utc=True)).dt.total_seconds() / 3600
    limit = src["dataset"].map(lambda d: 12 if d in ("news", "disclosures") else 72)
    src["tình trạng"] = ["🟢" if a <= lim else "🔴 cũ" for a, lim in zip(age_h, limit)]
    src["last_fetched"] = to_vn(src["last_fetched"])
    st.dataframe(src.rename(columns={"source": "nguồn", "dataset": "loại", "last_fetched": "lần cuối (giờ VN)",
                                     "batches": "số lần"}), use_container_width=True, hide_index=True)

st.markdown("### Độ phủ từng nguồn (lần crawl gần nhất)")
cov = pd.DataFrame(data.get("coverage", []))
if cov.empty:
    st.info("Chưa có lần crawl nào.")
else:
    cov["độ phủ"] = cov.apply(lambda r: f"{r['ok']}/{int(r['requested'])} ({r['coverage']:.0%})"
                              if pd.notna(r["requested"]) and r["requested"] else f"{r['ok']} bản ghi", axis=1)
    cov["tình trạng"] = ["🟢" if c >= 0.9 else ("🟠" if c >= 0.5 else "🔴") for c in cov["coverage"]]
    cov["fetched_at"] = to_vn(cov["fetched_at"])
    st.dataframe(cov[["source", "dataset", "độ phủ", "errors", "fetched_at", "tình trạng"]].rename(
        columns={"source": "nguồn", "dataset": "loại", "errors": "lỗi", "fetched_at": "lúc (giờ VN)"}),
        use_container_width=True, hide_index=True)
    st.caption("Mọi nguồn đều được crawl đầy đủ; nguồn dưới 90% số mã là nguồn đó có vấn đề, không phải do nguồn khác thay thế.")

dis = data.get("disagreements", {})
st.markdown("### Sai lệch giữa các nguồn")
pd_dis = pd.DataFrame(dis.get("prices", []))
if pd_dis.empty:
    st.success("Giá đóng cửa ngày giao dịch gần nhất khớp giữa các nguồn (hoặc mới có một nguồn).")
else:
    pd_dis["giá theo nguồn"] = pd_dis["values"].map(lambda v: ", ".join(f"{k}: {x:,.0f}" for k, x in v.items()))
    st.dataframe(pd_dis[["ticker", "date", "diff_pct", "giá theo nguồn"]].rename(
        columns={"ticker": "mã", "date": "ngày", "diff_pct": "lệch %"}), use_container_width=True, hide_index=True)
fd = pd.DataFrame(dis.get("financials", []))
if not fd.empty:
    fd["giá trị theo nguồn"] = fd["values"].map(lambda v: ", ".join(f"{k}: {x:,.0f}" for k, x in v.items()))
    st.dataframe(fd[["ticker", "period", "item_code", "diff_pct", "giá trị theo nguồn"]].rename(
        columns={"ticker": "mã", "period": "kỳ", "item_code": "chỉ tiêu", "diff_pct": "lệch %"}),
        use_container_width=True, hide_index=True)

st.markdown("### Lần chạy gần nhất của từng job")
runs = pd.DataFrame(data["last_runs"])
if not runs.empty:
    icon = {"success": "🟢", "partial": "🟠", "failed": "🔴", "skipped": "⚪", "running": "🔵"}
    runs["status"] = runs["status"].map(lambda s: f"{icon.get(s, '')} {s}")
    runs["started_at"] = to_vn(runs["started_at"])
    st.dataframe(runs[["job_name", "status", "started_at", "records", "message"]], use_container_width=True,
                 hide_index=True)

st.markdown(f"### Job lỗi ({days} ngày)")
failed = data["failed_jobs"]
if not failed:
    st.success("Không có job lỗi.")
for f in failed:
    with st.expander(f"🔴 {f['job_name']} · {f['status']} · {pd.to_datetime(f['started_at'], utc=True).tz_convert('Asia/Ho_Chi_Minh'):%d/%m %H:%M}"):
        if f.get("message"):
            st.text(f["message"])
        if f.get("error"):
            st.code(f["error"])

st.caption(f"Batch raw: {data['raw_batches']}")
