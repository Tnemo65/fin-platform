# Finance Platform

Crawl dữ liệu chứng khoán Việt Nam từ nhiều nguồn (vnstock, RSS báo, công bố thông tin HOSE/HNX),
chuẩn hoá về một khoá chung (mã + ngày/kỳ), phục vụ qua FastAPI và giao diện Streamlit.

![Màn hình chính](docs/screenshot-home.png)

## Chạy nhanh

```bash
pip install -r requirements.txt
pip install -r requirements-vnstock.txt   # số liệu tài chính; xem ghi chú vnstock bên dưới
python -m finplat init-db

# Có mạng tới nguồn thật:
python -m finplat run symbols_events      # danh sách mã + sự kiện
python -m finplat run prices_eod --force  # giá EOD
python -m finplat run financials --tickers VNM,FPT,HPG
python -m finplat run news

# Hoặc chỉ muốn xem giao diện: nạp dữ liệu GIẢ LẬP (source = demo, tin có nhãn [DEMO])
python -m finplat seed-demo

python -m finplat api                      # http://localhost:8000/docs  (--workers N để chạy N tiến trình)
streamlit run app/Home.py                  # http://localhost:8501
python -m finplat scheduler                # chạy lịch hằng ngày
```

Mặc định dùng SQLite (`data/finplat.db`). Dùng PostgreSQL: `docker compose up -d` rồi đặt
`DATABASE_URL=postgresql+psycopg://finplat:finplat@localhost:5432/finplat` trong `.env` (xem `.env.example`).

**vnstock**: để riêng trong `requirements-vnstock.txt` vì tại thời điểm viết (03/10/2026) PyPI
không có `vnstock` lẫn các gói nó phụ thuộc (`vnai`, `vnstock_ezchart`), nên nếu gộp chung thì
`pip install -r requirements.txt` hỏng cả. Khi cài được thì chạy lệnh trên; nếu không, cài từ
nguồn theo hướng dẫn của tác giả (repo `thinh-vu/vnstock`). Chưa có vnstock thì các job
`symbols_events`, `prices_eod`, `financials` ghi `failed` vào `job_runs`; tin tức, API, giao diện
vẫn chạy.

**Mạng ra ngoài** (máy chạy crawler cần tới được): `cafef.vn`, `vietstock.vn`, `vnexpress.net`,
`vneconomy.vn`, `www.hsx.vn`, `www.hnx.vn`, và các API vnstock dùng: `api.vietcap.com.vn` (VCI),
`apipubaws.tcbs.com.vn` (TCBS). Lệnh `python -m finplat run <job>` trả exit code 1 khi job
`failed`, nên cron/CI bắt được.

## Cấu trúc

```
config/settings.toml      Cấu hình nghiệp vụ: ưu tiên nguồn, feed RSS, đơn vị, ngày nghỉ, mùa BCTC, gắn mã
finplat/
  sources/                Mỗi nguồn một module, cùng interface (base.py)
    vnstock_source.py     vnstock_vci, vnstock_tcbs: mã, giá, BCTC, chỉ số, sự kiện
    rss.py                cafef, vietstock, vnexpress, vneconomy (sinh từ [feeds.*])
    disclosures.py        hose, hnx (sinh từ [disclosures.*])
    demo.py               dữ liệu giả lập để thử UI
  schemas.py              Format chung mọi nguồn trả về (PriceRec, FinancialRec, NewsRec, ...)
  raw_store.py            Lưu raw (.jsonl.gz) + bảng raw_batches
  retrying.py             Retry (tenacity) cho mọi request crawl
  processing/
    normalize.py          VND, kỳ 2026Q2, URL/tiêu đề, mã chỉ tiêu BCTC
    tagger.py             Gắn mã cho tin
    pipeline.py           raw -> bảng core, upsert theo ưu tiên nguồn, khử trùng
  db.py                   Engine + upsert (PostgreSQL/SQLite)
  models.py               Các bảng
  jobs.py                 Job hằng ngày + ghi job_runs
  scheduler.py            APScheduler (giờ VN)
  checks.py               Kiểm tra dữ liệu, báo cáo lỗi, dữ liệu trang tình trạng
  api/main.py             FastAPI
app/                      Streamlit: Home.py + pages/1_Tinh_trang_du_lieu.py
tests/                    pytest (chạy được trên SQLite và PostgreSQL)
```

## Nguyên tắc crawler

- **Một nguồn một module, cùng format.** Nguồn cài 2 hàm: `fetch()` trả raw đúng như nguồn,
  `parse()` chuyển raw sang record chung trong `schemas.py`. Đăng ký bằng `register(...)`.
  Thêm nguồn = thêm file + thêm tên vào `[sources]`/`[priority]` trong settings.toml. Feed RSS
  hoặc trang công bố mới chỉ cần thêm cấu hình, không cần code.
- **Raw trước.** Mỗi lần fetch ghi `data/raw/<nguồn>/<loại>/<ngày>/<batch>.jsonl.gz` và một dòng
  `raw_batches`. Xử lý lỗi thì batch ở trạng thái `failed`; sửa xong chạy
  `python -m finplat process --retry-failed` hoặc `python -m finplat reprocess --since 2026-10-01`,
  không crawl lại.
- **Upsert.** Mọi bảng ghi bằng `INSERT ... ON CONFLICT DO UPDATE`, chạy lại không sinh trùng.
- **Thử lại (tenacity).** Mọi request của crawler (feed, bài viết, trang công bố, từng mã vnstock)
  đi qua `finplat/retrying.py`: lỗi tạm thời (mất kết nối, timeout, HTTP 408/425/429/5xx) thử
  lại với backoff mũ có jitter, server trả `Retry-After` thì chờ theo đó; lỗi dữ liệu (404,
  parse) ném ngay. Cấu hình `[retry]` (`attempts`, `wait_min`, `wait_max`). Hết lượt thì lỗi
  được ghi vào raw/`job_runs` như trước, không làm dừng job.

## Xử lý và hợp nhất

- Khoá chung: `ticker + date` (giá) hoặc `ticker + period` (BCTC, chỉ số), kỳ dạng `2026Q2`
  (cả năm: `2026Y`).
- Tiền tệ quy về VND (nguồn khai báo đơn vị gốc trong `[units.*]`, hoặc trong tên cột "(Tỷ đồng)").
- **Ưu tiên nguồn:** mỗi dòng giữ `source` và `source_priority`. Upsert chỉ ghi đè khi nguồn mới
  ưu tiên bằng hoặc hơn, nên kết quả không phụ thuộc thứ tự chạy. Thứ tự đặt trong `[priority]`.
- **Khử trùng tin:** `news.id = sha1(URL đã chuẩn hoá)` (bỏ utm, www, dấu / cuối...). Khác URL
  nhưng trùng `title_hash` (tiêu đề bỏ dấu, bỏ ký tự đặc biệt) thì coi là bài đăng lại và bỏ qua.
- **Gắn mã:** dò mã và tên công ty/alias trong tiêu đề và nội dung, có điểm tin cậy; một tin gắn
  nhiều mã (`news_tickers`). Mã trùng từ viết tắt (VND, CEO, ...) chỉ gắn khi có ngữ cảnh rõ.
  Chạy lại toàn bộ: `python -m finplat tag --all`.

## Bảng

`symbols`, `price_daily`, `financial_items` (dạng dài: mã, kỳ, báo cáo IS/BS/CF, chỉ tiêu),
`ratios` (P/E, P/B, ROE, EPS), `corporate_events` (cổ tức, phát hành thêm), `news`, `news_tickers`,
`raw_batches`, `job_runs`.

## Lịch chạy (giờ VN)

| Giờ | Job | Ghi chú |
|---|---|---|
| 07:00 | `symbols_events` | Danh sách mã + sự kiện (sự kiện xoay vòng 1/7 số mã mỗi ngày + watchlist) |
| 07:00, 12:00, 17:00 | `news` | RSS + công bố thông tin, bỏ qua tải lại bài đã có |
| 15:30 T2-T6 | `prices_eod` | Bỏ qua ngày nghỉ lễ (`[calendar]`), lấy lùi 7 ngày để vá ngày thiếu |
| 18:00 T2-T6 | `financials` | Mùa BCTC (`[financial_season]`) chạy toàn bộ mã, ngoài mùa xoay vòng |
| 18:30 | `processing` | Xử lý batch raw còn tồn/lỗi + gắn mã |
| 19:00 | `checks` | Job lỗi/không chạy, độ phủ giá EOD, nguồn cũ, batch lỗi; ghi `data/reports/<ngày>.md`, gửi webhook/Telegram nếu có lỗi |

Mỗi job ghi một dòng `job_runs` (`success`, `partial`, `failed`, `skipped`). Mặc định crawl xong
xử lý ngay (`process_inline = true`) để tin hiện lên trong ngày; job 18:30 vẫn gom lại mọi thứ
còn tồn. Đặt `false` để chỉ xử lý lúc 18:30.

Chạy bằng cron thay vì APScheduler: gọi `python -m finplat run <job>` đúng giờ.

## Hiệu năng (đa luồng)

- **Crawl:** trong một job, các nguồn được fetch song song (`source_workers`, vd 4 feed RSS + 2
  trang công bố cùng lúc). Trong mỗi nguồn lại song song theo mã (vnstock) hoặc theo bài (RSS)
  với `crawl_workers` luồng. Mỗi nguồn có một bộ giãn cách dùng chung cho mọi luồng
  (`request_delay`), nên tổng tốc độ gửi request tới một trang không vượt 1/`request_delay`
  request/giây dù bật bao nhiêu luồng; muốn nhanh hơn thì giảm `request_delay`. Ghi raw và xử
  lý vào DB vẫn tuần tự theo thứ tự nguồn để không tranh chấp ghi và kết quả ổn định.
- **API:** `python -m finplat api --workers N` chạy N tiến trình uvicorn (mặc định
  `API_WORKERS` hoặc min(4, số CPU)); trong mỗi tiến trình các endpoint chạy trên thread pool
  `API_THREADS` luồng (mặc định 64). Phản hồi lớn nén gzip. Pool PostgreSQL `DB_POOL_SIZE`.
- **Streamlit:** màn hình chính gọi 4 endpoint (mã, giá, BCTC, tin) cùng lúc thay vì lần lượt.

## API

- `GET /symbols?q=` tìm mã
- `GET /symbols/{ticker}/prices?start=&end=&limit=` giá EOD (VND)
- `GET /symbols/{ticker}/financials?periods=8&statement=IS&key_only=true` BCTC theo quý kèm
  `yoy_pct` so với cùng kỳ, và chỉ số
- `GET /symbols/{ticker}/news` timeline tin
- `GET /symbols/{ticker}/events`, `GET /status` (cho trang tình trạng dữ liệu)

## Cần kiểm chứng khi chạy với mạng thật

Môi trường dựng ban đầu không truy cập được các trang Việt Nam và không cài được vnstock, nên
những phần sau mới được kiểm thử bằng dữ liệu mẫu:

1. Tên cột vnstock (`parse` dò theo nhiều tên, nhưng nên chạy thử 1-2 mã và xem raw).
2. Đơn vị BCTC của từng provider trong `[units.*]`. Riêng giá đã tự nhận diện nghìn đồng/đồng
   (`price = "auto"`), nên không cần chỉnh.
3. URL feed Vietstock/VnEconomy/CafeF trong `[feeds.*]`.
4. URL và XPath trang công bố HOSE/HNX trong `[disclosures.*]` (hai sàn hay đổi giao diện;
   trang tải bằng JavaScript thì cần đổi sang gọi API JSON của sàn).

## Test

```bash
pytest
TEST_DATABASE_URL=postgresql+psycopg://user@host/db pytest   # chạy trên PostgreSQL
```
