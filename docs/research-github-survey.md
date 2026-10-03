# Khảo sát GitHub: crawler dữ liệu chứng khoán đa nguồn & nền tảng theo dõi TTCK Việt Nam

Ngày khảo sát: 2026-10-03. Phương pháp: ~60 lượt tìm kiếm web (Anh + Việt), GitHub Topics, GitHub Search API
công khai, và mở README / file mã nguồn của ~110 repo. Chỉ ghi điều đã nhìn thấy; mục nào chưa xác minh được
ghi rõ ở cuối. Số sao là ảnh chụp tại thời điểm fetch. Các website nền tảng đóng (FireAnt, Vietstock, Simplize,
WiChart, 24HMoney, FiinTrade, TCBS, DNSE, SSI, VNDirect, Vietcap, vnstocks.com, docs.openbb.co) bị chặn mạng
từ môi trường khảo sát, nên phần về chúng chỉ dựa trên trích dẫn kết quả tìm kiếm.

---

## A. Kết luận chính cho dự án fin-platform

1. **vnstock không còn trên PyPI, cài qua index riêng.** README vnstock hướng dẫn
   `pip install --extra-index-url https://vnstocks.com/api/simple vnstock vnai`. Bản v4.0.9 (27/09/2026).
   Hạn mức gọi: Guest 20 req/phút, Community 60, Sponsor 180–600. License tự định nghĩa, **không phải OSI**,
   cấm "redistribution" và "operating products that resell data access" nếu không có thoả thuận. Issue #186:
   các nguồn gốc chặn IP ngoài Việt Nam (403 trên Colab/Kaggle).
2. **vnstock đã gỡ TCBS (v3.5.0, 03/2026).** Nguồn hiện có: KBS (mặc định), VCI, MSN, FMarket. Cấu hình
   `[sources] events = ["vnstock_tcbs"]` của dự án sẽ không chạy; cần chuyển sang VCI (corporate actions qua
   IQ-Insight) hoặc KBS. v4.0.2 chuyển Listing/Company/Finance từ GraphQL sang REST → tên cột có thể đổi.
   Tháng 7/2026 KBS Quote đổi sang nghìn đồng (theo snippet, chưa mở được trang); tháng 5/2026 thêm
   `unit_multiplier` và map cột chung KBS↔VCI cho BCTC.
3. **Không repo nào cào công bố thông tin HOSE/HNX.** Gần nhất là mmbbot đọc PDF `owa.hnx.vn/ftp/THONGKEGIAODICH/`
   và vn-annual-report-miner dùng dataset Zenodo. Module `disclosures.py` của dự án là phần không có tiền lệ,
   rủi ro cao nhất (trang JS, đổi giao diện).
4. **Không repo Việt Nam nào mô tả rõ cách gắn mã cho tin.** Các dự án VN dừng ở URL dedupe (vietnam-news-monitor)
   hoặc giao hết cho LLM không kiểm chứng (finance-news-scraper). Cách làm tốt nhất thấy được là ở repo quốc tế
   (Samarthpatel29, NewsAgent, pulsarium) — xem mục D.
5. **Quy ước màu VN: đỏ = tăng, xanh = giảm** (vn-stock-analytics-platform ghi rõ). UI hiện tại dùng xanh tăng
   theo kiểu Mỹ.
6. **Có endpoint trực tiếp không qua vnstock** đã thấy trong mã nguồn các repo (mục C): VNDirect finfo-api
   (giá, BCTC, ratios), CafeF PriceHistory.ashx, KBS kbbuddywts, Vietcap trading.vietcap.com.vn. Đây là đường
   dự phòng khi vnstock không cài được hoặc vướng license.

---

## B. Kiến trúc đáng học (đã đọc tận file mã nguồn)

| Mẫu | Repo | Chi tiết đã thấy | Áp dụng cho fin-platform |
|---|---|---|---|
| Standard model + alias dict per provider | OpenBB (`provider/standard_models/equity_historical.py`, `providers/cboe/.../equity_historical.py`) | `EquityHistoricalData(date, open, high, low, close, volume, vwap)`; provider kế thừa và khai `__alias_dict__ = {"volume": "stock_volume"}`; fetcher 3 bước `transform_query → extract_data → transform_data`; rỗng → `EmptyDataError` | `schemas.py` đã là standard model; thêm alias dict thay cho danh sách tên cột rải trong `vnstock_source.py` |
| Registry decorator `(domain, source)` + schema cột một chỗ | akshare-one (`modules/registry.py`, `schema.py`) | `@provider("historical", "sina")`; `df.reindex(columns=SCHEMA)` cột thiếu → NaN; lỗi nguồn lạ liệt kê nguồn đã đăng ký; TTLCache theo namespace | Registry `base.py` đã tương đương; `get_source` đã liệt kê nguồn khi KeyError |
| Raw mỗi nguồn giữ riêng, không đè; bronze append-only | zvt (`contract/api.py` `get_storage_id(provider, db_name)`), VN-Hugo/VNStock-ELT-Pipeline, IbrahimKhan25/market-data-pipeline | "Bronze chỉ append, không ghi đè. Silver giữ bản trích xuất mới nhất cho mỗi khoá"; Pandera contract, quarantine dòng lỗi | `data/raw/<nguồn>/...jsonl.gz` + `raw_batches` đã là bronze; thêm quarantine dòng lỗi thay vì bỏ im |
| Incremental theo watermark + lookback | zvt `get_latest_saved_record`, IbrahimKhan25 `watermark − lookback_days`, pulsevn-market overlap 120 phút cho news | | `price_lookback_days = 7` đã tương đương; mã mới nên backfill dài (vn-stock-analytics-platform backfill 10 năm) |
| Router vendor theo category, lỗi có kiểu | TradingAgents (`dataflows/router.py`, `errors.py`, `default_config.py`) | `"fundamental_data": "sec_edgar,yfinance"` là chain tường minh, "NOT silently routed"; `VendorUnavailableError` → thử vendor kế; `NoMarketDataError` → dừng, trả sentinel; nguồn tuỳ chọn degrade thay vì fail | `[sources]`/`[priority]` đã là chain; nên phân loại lỗi: lỗi mạng → thử nguồn kế, "không có dữ liệu" → không thử |
| Priority chỉnh lúc chạy | TradingAgents-CN (`data_source_manager.py`) | Bảng `system_configs {source, market, enabled, priority}` trong MongoDB; DB cache là tier 0 | Có thể đưa `[priority]` vào bảng để chỉnh không cần deploy |
| Cache client theo hash tham số, không cache lỗi | ai-hedge-fund (`hedge_fund/data/cached.py`) | key `sha256(method|canonical_json)[:24]`; giá ngày chỉ revalidate khi `fetched_at < end_date`; `refresh=True` | Streamlit `st.cache_data` đã có; API có thể cache tương tự |
| Không cache nến chưa đóng | trangcm/vn-market-data | Cố ý không cache `get_index_live()`, `get_market_turnover()` | Job 15:30 sau phiên là đúng; nếu thêm intraday thì tách |
| Collector → Normalize → Dump, retry theo danh sách mã lỗi | qlib `scripts/data_collector/base.py` | `max_collector_count`, `delay=0.5`, `@deco_retry`; normalize: `volume<=0` → NaN OHLC, phát hiện nhảy ×100 | Thêm kiểm tra nhảy giá/volume vào `checks.py` |
| 8 kiểm tra chất lượng + bảng audit | abdallah-farahat/Stock-Market-Data-Pipeline-Analysis | thiếu cột, null, trùng (date,ticker), giá ≤0, high<low, volume 0/âm, return ±50%; `audit_quality_issues` | `process_prices` mới chỉ check high<low |
| Mapping chỉ tiêu BCTC bằng CSV per provider, log chỉ tiêu chưa map | FinanceToolkit (`normalization/balance.csv`, `balance_yf.csv`), edgartools (`concept_mappings.json`, `unmapped_logger.py`) | Non-dev sửa được; kỳ báo cáo quy về calendar quarter; TTM `trailing=4` | `CANONICAL_ITEMS` đang hard-code trong `normalize.py` → chuyển ra TOML/CSV + log item_code chưa map |
| Danh mục mã chuẩn | FinanceDatabase | CSV `symbol, name, sector, industry, exchange, ISIN, FIGI`; cập nhật Chủ nhật | Bảng `symbols` + job tuần |
| ELT nhỏ gọn gần nhất với mục tiêu | hoangthanh300405-ops/vn-stock-analytics-platform | tenacity + rate limiter + circuit breaker → DuckDB `INSERT BY NAME` → dbt test → Streamlit; GitHub Actions 15:45 T2–T6 + Chủ nhật; concurrency group | Mẫu tham chiếu sát nhất |
| Kafka → Postgres, dedup 3 lớp, alias → mã | anhphong0311/pulsevn-market (VN) | "deduplication by source identity, canonical URL and SHA-256 content hash"; "maps company names and aliases to stock symbols"; RSS VnEconomy, CafeF; giá từ KBS daily chart | Dedup hiện có URL + title_hash; thêm hash nội dung |

---

## C. Endpoint & đơn vị đã thấy trong mã nguồn (chưa tự gọi thử)

| Nhà cung cấp | Endpoint | Thấy ở | Ghi chú |
|---|---|---|---|
| Vietcap (VCI) | `mt.vietcap.com.vn/api/`, `trading.vietcap.com.vn/api/`, `trading.vietcap.com.vn/data-mt/graphql`, `iq.vietcap.com.vn/api/iq-insight-service` | vnstock `explorer/vci/const.py` | map đơn vị `BILLION→tỷ`, `MILLION→triệu` |
| Vietcap public | `/api/market-data-service/v1/tickers/price/top-stock`, `/api/price/symbols/getList`, `wss://trading.vietcap.com.vn/ws/price/socket.io/` (protobuf) | LySyaoran/API-Vietcap-Price | "mọi API giá đều public" |
| KBS | `kbbuddywts.kbsec.com.vn/iis-server/investment` (`/stock/search/data`, `/sector/all`, `/stockinfo`, `/trade/history`), `kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store/stock/{symbol}/historical-quotes` | vnstock `explorer/kbs/const.py` | interval `1P,5P,15P,30P,60P,day,week,month` |
| VNDirect | `finfo-api.vndirect.com.vn/v4/stock_prices/?q=code:X~date:gte:..~date:lte:..&size=&sort=date`; `/v3/stocks/financialStatement?reportTypes=QUARTER&modelTypes=1,89,101,411` (CĐKT) / `2,90,102,412` (KQKD) / `3,91,103,413` (LCTT); `/v4/ratios` | vnquant `configs.py`, `finance.py`; MiAI_Airflow | vnquant không ghi đơn vị, phải kiểm tra |
| VNDirect | `dchart-api.vndirect.com.vn` (OHLC nhiều khung); `wss://price-cmc-04.vndirect.com.vn/realtime/websocket` | qnaut; Real-time-data-vndirect | |
| CafeF | `s.cafef.vn/Ajax/PageNew/DataHistory/PriceHistory.ashx` (POST form) → `GiaDongCua, GiaMoCua, GiaCaoNhat, GiaThapNhat, GiaDieuChinh, KhoiLuongKhopLenh` | vnquant `loader/cafe.py` | |
| Vietstock | `finance.vietstock.vn/data/gettradingresult` (cần User-Agent); BCTC cần cookie `vts_usr_lg` + `__RequestVerificationToken` | Crawl_VietStock; Scrape-Finance-Data-v2 (chết từ 2021) | anti-bot, chi phí bảo trì cao |
| SSI FastConnect | `fc-data.ssi.com.vn` (REST), `fc-datahub.ssi.com.vn` (SignalR) | ssi-stock-mcp-server, streamflow | cần consumer id/secret |
| DNSE | `openapi.dnse.com.vn` (HMAC); MQTT `datafeed-lts.dnse.com.vn:443/wss` topic `plaintext/quotes/stock/OHLC/1H/+` | dnse-tech/dnse-py (SDK chính thức, MIT) | cần tài khoản |
| VPS | `bgapidatafeed.vps.com.vn/getlistckindex/hose|hnx|upcom` | lotusmarket | danh sách mã theo sàn |
| HNX | `owa.hnx.vn/ftp/THONGKEGIAODICH/` (PDF thống kê) | mmbbot | |
| TCBS | `apipubaws.tcbs.com.vn/...` | chỉ từ snippet tìm kiếm | **chưa xác minh**; docs chính thức developers.tcbs.com.vn, token 10 req/ngày |

Đơn vị đã thấy: vnstock-js README "giá chia 1000: đơn vị nghìn VND (25.5 = 25,500 VND)". vnstock VCI BCTC map tỷ/triệu.
Các repo CafeF/VNDirect không ghi đơn vị.

---

## D. Tin tức: khử trùng và gắn mã (cách làm tốt nhất thấy được)

- **Dedup 3 lớp** (pulsevn-market): id nguồn → URL chuẩn hoá → SHA-256 nội dung. Near-duplicate: MinHash+LSH
  `num_perm=240, threshold=0.7` (ChenghaoMou/text-dedup). Gom cụm "một sự kiện – nhiều báo" (NewsAgent).
- **Title key** (Samarthpatel29 `pipeline.py`): lower → bỏ ký tự không alnum → gộp khoảng trắng. Với tiếng Việt phải
  bỏ dấu trước (fin-platform đã làm trong `normalize_title`).
- **Gắn mã 3 pass có kiểm chứng** (Samarthpatel29 `sentiment.py`): (1) `$MÃ` / mã viết hoa lọc qua TICKER_UNIVERSE
  + stoplist; (2) alias tên công ty whole-word, tên nhập nhằng phải viết hoa trong văn bản gốc; (3) mỗi mã giữ pass
  có precision cao nhất. (NewsAgent): tin khuyến nghị thì **bỏ tên CTCK phát hành trước** ("SSI khuyến nghị mua HPG"
  là tin về HPG); kiểm tra vị trí chủ ngữ; validate mã với danh sách niêm yết (SEC `company_tickers.json`,
  VN dùng listing VCI/KBS/VPS). Polling theo tier: nguồn sàn/công bố trước, báo chí sau.
- **LLM chỉ là bước tuỳ chọn** (finance-news-scraper VN: LLM trích `tickers`, `is_relevant`, cache theo
  `sha256(page_text)` TTL 3 ngày; nhưng **không** kiểm tra mã với danh sách niêm yết — điểm yếu cần tránh).
- Tiếng Việt: PhoBERT phân loại tiêu đề CafeF 3 lớp (209sontung, ~1000 tiêu đề); ViSoBERT sentiment.

---

## E. Nền tảng đóng (chỉ từ trích dẫn tìm kiếm, chưa thấy giao diện thật)

- FireAnt: dòng tiền realtime, tin theo ngữ cảnh, bảng giá theo sàn/ngành/watchlist; gói 0/149k/399k/599k.
- VietstockFinance: >3.000 công ty, lịch sự kiện (niêm yết, cổ tức, ĐHCĐ), giao dịch nội bộ, BCTC bảng + biểu đồ,
  xuất Excel; DataFeed cho tổ chức.
- Simplize: định giá tổng hợp, bộ lọc Canslim/4M; Basic giới hạn 2 năm dữ liệu.
- WiChart: >400 chỉ tiêu, chart riêng theo ngành (ngân hàng, chứng khoán, holdings).
- 24HMoney: tin "liên quan" theo mã, cảnh báo giá/thanh khoản bất thường.
- FiinPro-X/FiinTrade: 3.600+ DN, 15 năm, BCTC "định dạng chuẩn" so sánh chéo; FiinQuantX SDK (repo FiinGroup/FiinQuant).
- TCBS TCInvest (TCAnalysis >1.350 công ty), DNSE Entrade X + LightSpeed, SSI iBoard + FastConnect, VNDirect DSTOCK
  (D-Rating), Vietcap IQ (BCTC, lọc, sự kiện).
- **Không nền tảng nào thấy có trang "tình trạng dữ liệu"** — điểm riêng của fin-platform.
- Tính năng đáng mô phỏng: tin theo mã, lịch sự kiện, giao dịch nội bộ, dòng tiền khối ngoại/tự doanh theo ngày,
  BCTC chuẩn hoá so sánh chéo, xuất Excel, màu đỏ tăng/xanh giảm.

---

## F. Agent AI & MCP (để mở rộng sau)

- MCP trên vnstock: mrgoonie/vnstock-agent (103★, 21 tool, stdio/SSE/HTTP), gahoccode/vnstock-mcp, ttqteo/vnstock-js
  (22 tool, `aiContext()` trả JSON trend/RSI/MACD), Long0308/vn-stock-api-mcp (FireAnt + CafeF).
- OpenBB MCP: tool = route REST, nhóm theo category, tool discovery tránh phình prompt → có thể làm tương tự trên
  FastAPI của fin-platform.
- "Inject, don't fetch" (pt-hieu/trade-bot): tầng dữ liệu batch trước, LLM không gọi API lẻ. "Mọi số liệu do code
  tính, LLM chỉ viết lời" (FinRobot V2).
- Point-in-time (TradingAgents v0.5.0): truy vấn lịch sử nhận `as_of_date`, không trả BCTC "hiện tại" cho ngày quá khứ.

---

## G. Danh sách repo đã mở (rút gọn)

Quốc tế: OpenBB (73.8k), yfinance (25.4k), akshare (22.8k), OpenStock (19.6k), tushare (15.4k), FinanceDatabase (9.4k),
FinanceToolkit (5.4k), zvt (4.3k), edgartools (2.8k), stocksight (2.5k), news-please (2.5k), FinRL-Meta (1.9k),
FinNLP (1.5k, commit cuối 07/2024), text-dedup (762), edgar-crawler (548), finance-news-aggregator (149),
TickerTick-API (141), akshare-one (74), qlib data_collector, TradingAgents (109.6k), ai-hedge-fund (63.8k),
TradingAgents-CN (32.1k), FinRobot (8.1k), financial-datasets/mcp-server (2.3k), Samarthpatel29/financial-news-sentiment,
NewsAgent, pulsarium, IbrahimKhan25/market-data-pipeline, abdallah-farahat, eikesf, indian-financial-news-aggregator.

Việt Nam: vnstock (1.410), vnquant (479), vnstock-agent (103), Scrape-Finance-Data-v2 (57), StockAndFinacial_VnData_Excel (52),
Real-time-data-vndirect (39), vn-stock-analyzer (21), ssi-stock-mcp-server (17), vietfin (15), vnstock-js (13),
Crawl_VietStock (10), qnaut (10), FiinQuant (8), streamflow (8), Crawl_NewsStock_From_Cafef (8), vnstock-api (7),
quant-data-layer (6), lotusmarket (4), vn-stock-api-mcp (4), ssi-fastconnect-v3-tutorials (4), viet-nam-stock (3),
vnstock-data-collector (3), vietnam-news-monitor, stock_news_crawl, pulsevn-market, vn-stock-analytics-platform,
smalldestocktotngiep, vnstock-hub, vn-stock-scanner, vn-market-data, API-Vietcap-Price, dnse-lightspeed-api, dnse-py,
tcbs-api, MiAI_Airflow, vn-annual-report-miner, pyvietstock, vietnam-stock-heatmap, VNStock-ELT-Pipeline,
Finance-ELT-pipeline, stock-data-pipeline, finance-news-scraper, financial_insight_agent, Bot-tradingCKVN, mmbbot-telegram,
TradeWatch, Vietnamese-stock-article-classification (21).

---

## H. Chưa xác minh được

- Mọi tính năng/giá nền tảng đóng (site bị chặn). Tài liệu vnstocks.com (đơn vị KBS, `unit_multiplier`) chỉ từ snippet.
- Host TCBS `apipubaws.tcbs.com.vn`, FireAnt `restv2.fireant.vn`: không mở được file nguồn nào chứa chuỗi này.
- Cách chọn provider mặc định/fallback của OpenBB (`user_settings.json defaults.routes`): chỉ từ snippet docs.
- FinRobot V2 "7 providers with failover": thư mục mã 404.
- Ngày commit cuối và số sao của nhiều repo nhỏ; một số truy vấn GitHub API bị 403 nên danh sách VN có thể thiếu.
- Không repo nào mô tả dedupe tin tiếng Việt theo ngữ nghĩa (embedding/simhash); không tích hợp Grafana/Home Assistant
  cho VN-Index; không repo backtest VN chuyên dụng được mở chi tiết.
