# Benchmark & Observability Report — Day 14

**Họ tên:** Do Nhat Anh (2A202603701)
**Ngày chạy:** 2026-11-25
**Môi trường:** Laptop Windows 11 Home, Python 3.11.4; Docker Desktop (engine 29.3.1, WSL2 backend, 12 CPU, ~3.9 GB RAM cấp cho Docker). Load test develop chạy bằng Python Windows gọi agent trên cùng máy (client và server dùng chung CPU).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~120–180 ms/call, retrieval ~20 ms; 500 ms đủ chỗ cho jitter và hàng đợi nhẹ nhưng vẫn "tức thì" với người dùng chat. Đây cũng là mặc định của `load_test.py`. Alert production đặt ở 1 s (mức "đang hỏng rõ") để tránh báo động giả |
| Error rate `/ask` | < 2 % | Mock có ~1% lỗi upstream tự nhiên; 2% chừa error budget gấp đôi mức nền. Alert production bật ở 5% kéo dài 1 phút |
| Accuracy trên golden dataset | ≥ 85 % (gate CI), mục tiêu 100 % | Dưới 85% là regression rõ ràng, chặn merge; sau khi sửa KB, v2 đạt 23/23 nên bản chính thức yêu cầu `--min-accuracy 1.0` và không có câu regression |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

300 request mỗi mức, `MOCK_SLOW_RATE=0.03` (mặc định). Nguồn: `reports/load_v2_c{1,10,50,100}.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.44 | 197.1 | 230.4 | 1697.8 | 0.67 % | ✅ PASS |
| 10 | 36.17 | 188.2 | 434.5 | 1707.5 | 0.33 % | ✅ PASS |
| 50 | 90.39 | 215.5 | 529.3 | 1737.7 | 0.33 % | ❌ FAIL (p95) |
| 100 | 47.29 | 789.5 | 4951.3 | 5481.9 | 0.67 % | ❌ FAIL (p95) |

**Nhận xét:** RPS tăng gần tuyến tính từ c=1 đến c=10 (4.44 → 36.17, ×8.1) vì agent dùng `async` và mock LLM chỉ `await asyncio.sleep`: thời gian chủ yếu là "chờ upstream", một process phục vụ được nhiều request song song. Từ c=10 lên c=50 RPS còn tăng (×2.5) nhưng p95 đã vượt SLO (529 ms) vì bắt đầu xếp hàng. Ở c=100 RPS **giảm** còn 47 req/s và p50 nhảy lên 790 ms, p95 ~5 s: process uvicorn đơn (1 event loop, 1 CPU core, phải serialize JSON, ghi log, cập nhật metrics) và client load test chạy trên cùng máy Windows đã bão hoà, nên request chủ yếu đứng chờ trong hàng đợi. Điểm "gãy" nằm giữa c=10 và c=50; muốn chịu c≥50 trong SLO cần thêm worker/instance.

### 2.2 Tail latency

`--requests 300 --concurrency 20`, khởi động lại v2 cho mỗi giá trị. Nguồn: `reports/load_v2_slow_{0,0.03,0.1}.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 200.2 | 437.6 | 477.4 |
| 0.03 (mặc định) | 252.6 | 474.5 | 1708.1 |
| 0.1 | 358.4 | 1687.1 | 1716.6 |

**Nhận xét:** Từ 0 → 0.03, avg chỉ tăng 26% (200 → 253 ms) nhưng **p99 tăng 3.6 lần** (477 → 1708 ms): 3% request chậm thêm 1.5 s nằm trọn trong 1% đuôi. Lên 0.1, p95 cũng bị kéo theo (437 → 1687 ms) vì 10% request chậm đã vượt qua ngưỡng 5% đuôi, trong khi avg vẫn chỉ ~1.8 lần. Chỉ số nhạy nhất là **p99** (rồi tới p95). SLO không nên đặt theo average vì average "pha loãng" một nhóm nhỏ người dùng bị chờ 1.7 s: ở `MOCK_SLOW_RATE=0.1` avg 358 ms trông vẫn ổn nhưng 1/10 người dùng chờ gần 2 s. SLO đặt theo p95/p99 đo đúng trải nghiệm của những người tệ nhất.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json` (golden dataset 23 câu, không sửa).

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 56.8 | 108.7 | 113.7 |
| Tổng cost (USD) | 0.000400 | 0.000756 | 0.000790 |
| Cost / câu đúng (USD) | 0.00040035 | 0.00003599 | 0.00003436 |
| Quality gate | FAIL (gate ≥ 80%) | PASS (gate ≥ 80%, 0 regression vs v1) | PASS (gate ≥ 100%, 0 regression vs v2) |

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| scaling-03 ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm tài liệu `(["hpa", "kubernetes", "autoscal"], "Kubernetes HPA ... scale số pod dựa trên CPU, memory hoặc custom metrics như RPS hay độ dài hàng đợi.")` vào cuối `KNOWLEDGE_BASE` |
| security-02 ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Thêm `(["jwt"], "API key là chuỗi bí mật cố định, không tự hết hạn. JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn; ...")` |

Trước khi thêm, kiểm tra 4 từ khoá mới (`hpa`, `kubernetes`, `autoscal`, `jwt`) chỉ xuất hiện trong đúng 2 câu này của dataset, nên retrieval của 21 câu còn lại không đổi → eval cuối báo "Câu bị regression: 0". Phần sửa cũng được chép sang `production/utils/mock_llm.py`.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

v2 tốn gần gấp đôi token mỗi câu (56.8 → 108.7) vì prompt có thêm đoạn tài liệu retrieve được, nên tổng cost tăng 89% và mỗi request thêm ~20 ms retrieval. Nhưng accuracy tăng từ 4.3% lên 91.3%, nên **cost trên mỗi câu đúng giảm 11 lần** (0.000400 → 0.000036 USD). So "cost / câu" thì v1 rẻ hơn, nhưng v1 gần như luôn trả lời sai, tiền đó là tiền mất trắng; "cost / câu đúng" mới phản ánh giá trị nhận được. (p95 trong eval_v2 = 1556 ms là do vài request dính `MOCK_SLOW_RATE` ngẫu nhiên trên 23 mẫu, không phải do retrieval; lần chạy `eval_v2_fixed` p95 = 236 ms.)

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability ...`. Hai chỗ phải xử lý khi chạy trên máy này:

- `production/Dockerfile` của đề tạo user `agent` với HOME=`/app` nhưng copy packages vào `/home/agent/.local` → agent crash `ModuleNotFoundError: No module named 'uvicorn'`. Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local`.
- `docker compose run locust ...` tự "reconcile" service phụ thuộc (nginx → agent) về `deploy.replicas: 2`, làm **mất agent-3 ngay khi bắt đầu chạy**. Lần đo scale đầu tiên vì vậy không hợp lệ và đã bị xoá; số trong mục 5 là lần chạy lại với `run --no-deps`, có kiểm tra số instance trước và sau khi chạy.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). Khung 30 phút: 2 đợt benchmark scale (~60 req/s), giai đoạn thí nghiệm A+B (p95 ~1.9 s vượt đường 1 s, có req/s 503) rồi hồi phục; lúc chụp Locust 30 users: 18.4 req/s, error 1.09%, p95 295 ms, 2 instance.
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png), trace `382f4b81eec0a602f6204306e3198815`, span `chat mock-llm-small` 1.7 s / 1.8 s
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png), trace `1cbfc90b517992bc2f175a0d242b8b24`, HTTP 503, event `exception` (`LLMError`)
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). `AgentHighErrorRate` và `AgentHighLatencyP95` cùng FIRING (2)

(Ảnh chụp tự động bằng Playwright + Microsoft Edge headless từ các UI đang chạy trên máy: Prometheus `:9090`, Jaeger `:16686`, Grafana `:3000`.)

**Exercise 7.4** (2 instance, Locust 30 users / ramp 5, đọc từ Prometheus sau ~100 s có tải, cùng PromQL với dashboard):

1. **p95 = 612 ms, avg = 261 ms** (p99 = 1893 ms). p95 gấp ~2.3 lần avg: phần lớn request ~200 ms nhưng có một đuôi dài (3% request chậm thêm 1.5 s). Khoảng cách avg ↔ p95/p99 chính là tail latency mà average che mất.
2. **Requests/s theo instance:** 9.3099 và 9.3098 req/s → Nginx chia đều (round-robin). Lúc benchmark 3 instance cũng đều: 2344 / 2361 / 2323 LLM call mỗi instance.
3. **Input tokens/s = 1431, output tokens/s = 749.** Input lớn hơn vì mỗi prompt gồm system prompt + đoạn tài liệu retrieve (RAG) + câu hỏi, còn output chỉ là câu trả lời ngắn. Với RAG, input thường chiếm phần lớn token (dù giá output đắt hơn 4 lần/token).
4. `curl http://localhost:8080/metrics` → **HTTP 404**. Nginx có `location = /metrics { return 404; }`: metrics chỉ cho Prometheus scrape trực tiếp từng agent trong mạng nội bộ Docker, không public ra internet vì lộ thông tin nội bộ (route, version, cost, lưu lượng) và có thể bị gọi dồn dập làm tốn tài nguyên.

Ngoài ra `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.2 Thí nghiệm "phá hệ thống"

Locust 30 users chạy nền suốt thí nghiệm. Đổi cấu hình bằng `MOCK_...=... docker compose -p k5-day14-observability up -d --no-deps --scale agent=2 agent`; thời gian đo bằng script poll `GET /api/v1/alerts` mỗi 5 s, tính từ lúc lệnh đổi cấu hình xong.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` (đổi lúc 09:06:24 UTC) | `AgentHighErrorRate` (giá trị 21.6%) | pending sau 36 s, **firing sau 96 s** | Golden Signals → Error rate (đỏ); LLM calls ok/error |
| B | `MOCK_SLOW_RATE=0.3` (giữ A, đổi lúc 09:08:12 UTC) | `AgentHighLatencyP95` (p95 = 1.89 s) | pending sau 26 s, **firing sau 146 s** | Latency p50/p95/p99/avg: p95 1.89 s, p99 1.98 s, avg chỉ 0.57 s |
| C | `LLM_MODEL=mock-llm-large` | Không chạy riêng: `LLMCostBurnRateHigh` **đã firing từ 08:53:32 UTC khi chưa đổi gì** (cost ước tính 4.1–4.2 USD/giờ > ngưỡng 2 USD/giờ, do tải benchmark 100 user rồi 30 user với model small) | n/a | Cost USD/giờ. Ngưỡng $2/giờ quá thấp so với lưu lượng lab nên alert này không phân biệt được "đổi model" với tải bình thường; khi B làm throughput giảm thì cost/h còn 1.59 và alert tự hết |
| D | `docker pause k5-day14-observability-agent-1` (09:11:51 UTC) | `AgentInstanceDown` (`up{instance="172.21.0.9:8000"} == 0`) | pending sau 15 s, **firing sau 46 s** | Instances up giảm; Locust thấy 504 ở Nginx nhưng Error rate của agent không phản ánh |
| E | `AGENT_VERSION=v1` | Không chạy trên stack (eval develop đã cho thấy accuracy 4.3%) | Không alert | Retrieval hit ratio, bảng version |

Lúc 09:11:38 UTC trang Alerts có **2 alert cùng firing**: `AgentHighErrorRate` (since 09:06:52) và `AgentHighLatencyP95` (since 09:08:32).

Thời gian firing ≈ thời gian để `rate(...[1m])` vượt ngưỡng (pending) + `for` của rule (A: 1 m, B: 2 m, D: 30 s) + chu kỳ scrape/evaluate. B lâu hơn A vì `for: 2m`.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Lỗi chất lượng không làm latency, error rate hay `up` thay đổi: agent vẫn trả 200 nhanh, chỉ là trả lời sai. Phải đo **chất lượng** trực tiếp: (1) quality gate trong CI/CD (`run_eval.py` với `--baseline`, exit code 1 chặn merge/deploy, như `.github/workflows/eval.yml`); (2) chạy eval định kỳ (synthetic) trên golden dataset vào bản đang chạy production và alert khi accuracy giảm; (3) metric proxy như retrieval hit ratio, tỷ lệ câu trả lời generic, độ dài câu trả lời, có alert khi lệch khỏi baseline; (4) phản hồi người dùng (thumbs down) và online eval bằng LLM-as-judge trên một mẫu traffic; (5) canary: cho bản mới nhận một phần nhỏ traffic, so sánh metric chất lượng với bản cũ trước khi rollout hết.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất trong ~2000 trace `POST /ask` của 12 phút (Jaeger API v3):

- `trace_id`: `382f4b81eec0a602f6204306e3198815`
- Tổng thời gian: 1778.6 ms
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1691.5 ms, 95.1%). `agent.retrieve_context` chỉ 21.9 ms (1.2%). Nút thắt là LLM upstream, không phải code của agent.
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-25T09:04:03.134622+00:00", "level": "INFO", "service": "observable-agent", "instance": "0f4b1a2fe7bb", "event": "llm_call", "trace_id": "382f4b81eec0a602f6204306e3198815", "span_id": "ec10e72e8fe07247", "request_id": "ddcfb278ae8a", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1691.3}
{"ts": "2026-11-25T09:04:03.135028+00:00", "level": "INFO", "service": "observable-agent", "instance": "0f4b1a2fe7bb", "event": "http_request", "trace_id": "382f4b81eec0a602f6204306e3198815", "span_id": "ec10e72e8fe07247", "request_id": "ddcfb278ae8a", "method": "POST", "route": "/ask", "status": 200, "ms": 1777.9}
```

Trace lỗi: `1cbfc90b517992bc2f175a0d242b8b24` (121.1 ms). Span `agent.run` và `chat mock-llm-small` có status ERROR, event `exception` với `exception.type = utils.mock_llm.LLMError`, `exception.message = "Upstream LLM error: 503 Service Unavailable (mock)"`, `error.type = upstream_unavailable`. Log tương ứng:

```json
{"ts": "2026-11-25T09:05:32.656417+00:00", "level": "INFO", "service": "observable-agent", "instance": "6cb2a7fc2f72", "event": "llm_error", "trace_id": "1cbfc90b517992bc2f175a0d242b8b24", "span_id": "460b7498c4381096", "request_id": "5bb216a8bbe6", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Nguồn: `benchmark-results/scale_1_stats.csv`, `benchmark-results/scale_3_stats.csv` (dòng `POST /ask`).

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.00 | 200 | 310 | 1700 | 62 / 7157 (0.87 %) |
| 3 | 59.72 | 190 | 270 | 1700 | 75 / 7073 (1.06 %) |

(Aggregated cả `/ask` + `/health`: 1 instance 66.1 req/s; 3 instance 65.5 req/s.)

**Giải thích kết quả:** Scale 1 → 3 chỉ cải thiện p95 một chút (310 → 270 ms), RPS không đổi, p99 vẫn 1700 ms. Có hai lý do:

1. **RPS bị giới hạn bởi bộ tạo tải, không phải server.** Locust là closed-loop: 100 user, mỗi user `wait_time = between(0.5, 2)` (trung bình 1.25 s) + ~0.24 s chờ response → trần ≈ 100 / 1.49 ≈ 67 req/s, đúng bằng 65–66 req/s đo được. Một instance async ở mức này chưa bão hoà, nên thêm instance không có "việc" để chia.
2. **Nút thắt latency là LLM upstream.** Panel LLM latency và trace (95% thời gian nằm ở span `chat mock-llm-small`) cho thấy latency do upstream quyết định: base 120 ms + jitter + 3% request chậm thêm 1.5 s. Thêm instance không làm LLM nhanh hơn, nên p99 ≈ 1700 ms giữ nguyên. Scale ngang chỉ giúp khi CPU/event loop của agent là nút thắt (như c=100 ở mục 2.1); muốn giảm tail latency phải xử lý ở phía LLM: timeout + retry/hedging, cache, model nhanh hơn, streaming.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. Average nói dối: `MOCK_SLOW_RATE` 0 → 0.03 làm avg tăng 26% nhưng p99 tăng 3.6 lần. SLO và alert phải đặt theo percentile.
  2. Metric hạ tầng không bắt được lỗi chất lượng: v1 trả 200 rất nhanh nhưng chỉ đúng 4.3%. Cần eval + quality gate dựa trên exit code trong CI, và nên so "cost / câu đúng" chứ không phải "cost / câu".
  3. Kết quả benchmark chỉ đúng khi kiểm chứng được setup: lần đo scale đầu cho kết quả "3 instance = 1 instance" nhưng thực ra `docker compose run` đã âm thầm xoá agent-3; và RPS của Locust closed-loop bị giới hạn bởi số user và `wait_time`. Luôn kiểm tra số instance thực tế và phân bố traffic (metric theo instance) trước khi kết luận.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - Synthetic check từ bên ngoài và metric/alert ở tầng Nginx (tỷ lệ 502/504, upstream response time), vì thí nghiệm D cho thấy lỗi 504 không hiện trên metric của agent.
  - Alert theo **error-budget burn rate** nhiều cửa sổ (vd 1h và 6h) thay vì ngưỡng cố định, để giảm báo động giả và bắt cả lỗi âm ỉ.
  - Chỉnh `LLMCostBurnRateHigh` theo ngân sách thật (ở lab nó firing ngay ở tải bình thường), thêm cost theo user/tenant và alert khi chi phí ngày vượt % budget.
  - Metric chất lượng online: retrieval hit ratio, tỷ lệ câu trả lời generic, feedback người dùng, accuracy của eval định kỳ, có alert khi giảm.
  - Saturation: CPU/memory container, độ dài hàng đợi/in-flight theo instance, LLM 429 (rate limit của provider) và số lần retry.
