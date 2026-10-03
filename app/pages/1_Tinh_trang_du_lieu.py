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
