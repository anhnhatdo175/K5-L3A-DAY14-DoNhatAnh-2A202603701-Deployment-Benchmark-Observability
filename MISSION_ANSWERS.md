# Day 14 Lab — Mission Answers

> Mọi output dưới đây lấy từ lệnh đã chạy trên máy làm bài (Windows 11, Docker Desktop engine 29.3.1, Python 3.11.4) ngày 2026-11-25. Token và key đã được che.
> URL deploy (Part 3 + Part 6): https://agent-production-0a677981.up.railway.app

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

1. **Hardcode secret** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-..."`, `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ key và mật khẩu DB; muốn đổi key phải sửa code và deploy lại.
2. **Log ra secret** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Ai đọc được log (log aggregator, người vận hành) là có key.
3. **`print()` thay cho logging có cấu trúc** (dòng 33, 38): không có level, không có timestamp, không parse được bằng Loki/Datadog. Khi chạy trên Windows, `print()` câu trả lời tiếng Việt còn làm **request trả 500** (`UnicodeEncodeError: 'charmap' codec can't encode characters`) vì console dùng cp1252. Đây đúng là lỗi "chạy được trên máy tôi" (xem Exercise 1.2); trong container Linux (Part 2) lỗi này không xảy ra.
4. **Config cố định trong code** (dòng 21–22): `DEBUG = True`, `MAX_TOKENS = 500`. Không đổi được giữa dev/staging/prod nếu không sửa code.
5. **Không có health check** (`GET /health` → 404): platform không biết agent đã chết để restart, load balancer không biết để bỏ instance.
6. **Bind `host="localhost"`** (dòng 51): trong container chỉ nhận kết nối từ chính container, từ ngoài vào không được.
7. **Port cố định 8000** (dòng 52): Railway/Render cấp port qua biến `PORT`, app không đọc nên không nhận traffic.
8. **`reload=True`** (dòng 53): chế độ dev, tốn CPU vì phải theo dõi file, không dùng trong production.
9. **Không xử lý shutdown**: không có lifespan hay SIGTERM handler, nên không dọn dẹp kết nối khi platform tắt container.
10. **`question` nhận qua query string**: câu hỏi dài hoặc nhạy cảm lộ trong URL và access log; gửi JSON body thì bị 422.

### Exercise 1.2: Chạy bản develop

Lần đầu (Windows, console cp1252):

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 500 Internal Server Error
Internal Server Error
# log: UnicodeEncodeError: 'charmap' codec can't encode characters in position 34-36
#      (ở dòng print(f"[DEBUG] Response: {response}"))
```

Chạy lại với `PYTHONIOENCODING=utf-8 python app.py`:

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found

# log in ra secret:
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-...   (key giả có sẵn trong code mẫu, rút gọn ở đây)
```

Nhận xét: app chạy được, nhưng hành vi phụ thuộc vào môi trường máy (encoding console), lộ secret trong log và không có health check, nên **chưa sẵn sàng cho production**.

### Exercise 1.3: So sánh develop và production

Bản production (`production/app.py` + `config.py`) chạy thử:

```text
GET /health → 200 {"status":"ok","uptime_seconds":4.9,"version":"1.0.0","environment":"development",...}
GET /ready  → 200 {"ready":true}
POST /ask (JSON {"question":"Hello"}) → 200 {"question":"Hello","answer":"...","model":"gpt-4o-mini"}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`DEBUG`, `MAX_TOKENS`, key, DB URL) | `config.py` đọc env (`os.getenv`) có default, `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một artifact chạy được ở mọi môi trường; đổi config không cần build lại; secret không nằm trong git |
| Secrets | `sk-...` và `password123` nằm trong code, còn in ra log | Lấy từ env / `.env` (đã gitignore), log không chứa secret | Lộ key thì mất tiền và mất dữ liệu; key trong lịch sử git không xoá được |
| Health check | Không có (`/health` 404) | `/health` (liveness) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform tự restart khi chết; load balancer chỉ gửi traffic vào instance đã sẵn sàng |
| Logging | `print()` text, in cả secret | `logging` với format JSON, có level, không log secret | Parse/tìm kiếm được trong log aggregator; không lộ dữ liệu nhạy cảm |
| Shutdown | Đột ngột, không có hook | `lifespan` (startup/shutdown) + handler SIGTERM, uvicorn chờ request đang chạy | Deploy hoặc scale down không cắt ngang request của user |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Chạy được trong container và trên platform cấp port động |
| Reload / Debug | `reload=True` luôn bật | `reload` chỉ bật khi `DEBUG=true` | Tránh overhead và hành vi dev trong production |
| Input | Query param `?question=` | JSON body, validate rỗng → 422 | API rõ ràng, không lộ dữ liệu trong URL/access log |

---

## Part 2: Docker

### Exercise 2.1: Đọc `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Debian đầy đủ (có gcc, header, nhiều tool), nên rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`).
3. **Vì sao COPY requirements.txt trước:** Docker cache theo layer. Khi chỉ sửa code, layer `COPY requirements.txt` + `RUN pip install` không đổi nên dùng lại cache, build lại trong vài giây. Nếu `COPY . .` trước thì mỗi lần sửa code đều cài lại toàn bộ dependencies.
4. **CMD và ENTRYPOINT:** `ENTRYPOINT` là chương trình cố định của container; `CMD` là lệnh hoặc tham số mặc định, bị thay thế khi `docker run image <lệnh khác>`. Có cả hai thì CMD trở thành tham số của ENTRYPOINT. Cả hai nên viết dạng exec (`["python","app.py"]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và chạy bản develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":4.3,"container":true}
```

Trong container (Linux, UTF-8) câu trả lời tiếng Việt không còn gây lỗi encoding như khi chạy thẳng trên Windows ở Part 1: môi trường được đóng gói nên chạy giống nhau ở mọi máy.

### Exercise 2.3: Multi-stage (`02-docker/production/Dockerfile`) và so sánh kích thước

- **Stage 1 (builder):** `python:3.11-slim` + `gcc`, `libpq-dev`; `pip install --user` vào `/root/.local`.
- **Stage 2 (runtime):** `python:3.11-slim` sạch, tạo user `appuser`, chỉ copy `/root/.local` (packages đã cài) và source code, có `HEALTHCHECK`, chạy uvicorn 2 worker.
- **Vì sao nhỏ hơn:** image cuối không chứa gcc, header, apt cache hay pip cache của stage builder; base `slim` thay cho base đầy đủ (`python:3.11` kéo theo cả bộ build tool của Debian).

```text
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   78e45161b791        240MB         57.4MB
my-agent:develop    10385edca6cf       1.67GB          426MB
```

- Develop: **1.67 GB** (1670 MB)
- Production (advanced): **240 MB**
- Chênh lệch: (1670 − 240) / 1670 = **85.6 % nhỏ hơn** (theo CONTENT SIZE nén: 426 MB → 57.4 MB, nhỏ hơn 86.5 %)

(Docker Desktop bản này dùng containerd image store nên `docker images` báo 2 cột: DISK USAGE là dung lượng giải nén trên đĩa, CONTENT SIZE là kích thước nén của các layer.)

### Exercise 2.4: Docker Compose stack (`02-docker/production/`)

Chạy: `touch .env.local && docker compose -p k5-day14-docker up -d --build`.

```text
                 :80 / :443
   client ───────────▶ ┌───────────┐
                       │   nginx   │  reverse proxy, rate limit 10 r/s/IP,
                       │ (alpine)  │  security headers
                       └─────┬─────┘
                             │ proxy_pass http://agent:8000   (network "internal")
                       ┌─────▼─────┐
                       │   agent   │  FastAPI + uvicorn 2 workers, không expose port ra host
                       └──┬─────┬──┘
          redis://redis:6379│     │http://qdrant:6333
                   ┌────────▼┐   ┌▼─────────┐
                   │  redis  │   │  qdrant  │
                   │ (cache, │   │ (vector  │
                   │  rate   │   │   DB)    │
                   │  limit) │   └──────────┘
                   └─────────┘
   volumes: redis_data, qdrant_data   ·   depends_on: agent chờ redis + qdrant "healthy"
```

Services được start: `agent`, `redis`, `qdrant`, `nginx`. Chỉ nginx mở cổng ra host (80/443). Các service nói chuyện với nhau qua bridge network `internal` bằng tên service (Docker DNS): nginx → `agent:8000`, agent → `redis:6379` và `qdrant:6333`.

```text
$ docker compose -p k5-day14-docker ps
SERVICE   STATUS                    PORTS
agent     Up 16 seconds (healthy)   8000/tcp
nginx     Up 15 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    Up 27 seconds (healthy)   6333-6334/tcp
redis     Up 27 seconds (healthy)   6379/tcp

$ curl -i http://localhost/health
HTTP/1.1 200 OK
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
{"status":"ok","uptime_seconds":20.2,"version":"2.0.0","timestamp":"2026-11-25T08:36:48.795615"}

$ curl -i http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Header bảo mật do nginx thêm vào cho thấy request thật sự đi qua proxy. Một điểm quan sát được trong log agent: dòng log "JSON" của app mẫu **không phải JSON hợp lệ**, vì format string bọc message JSON trong một chuỗi mà không escape: `{"time":"...","level":"INFO","msg":"{"event": "request", "q_len": 21}"}`. Part 6 tránh lỗi này bằng formatter tự `json.dumps` cả dòng.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Deploy agent Part 6 lên Railway (nền tảng chính)

- URL: **https://agent-production-0a677981.up.railway.app**
- Project `k5-day14-agent` gồm service `agent` (build từ `06-lab-complete/Dockerfile` theo `railway.toml`) và service `Redis`; `REDIS_URL=${{Redis.REDIS_URL}}`, `AGENT_API_KEY` đặt bằng biến môi trường trên Railway. Chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md).
- 4 lệnh tự kiểm CP6: `/health` **200**, `/ready` **200**, `/ask` không key **401**, `/ask` có key **200**. Rate limit trên cloud: `200 ×8 → 429 ×4` (10 req/phút).
- Screenshot: [`screenshots/dashboard.png`](screenshots/dashboard.png), [`screenshots/running.png`](screenshots/running.png), [`screenshots/test.png`](screenshots/test.png)

### So sánh `railway.toml` với `render.yaml` (`06-lab-complete/`)

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Chỉ cấu hình **build + deploy cho 1 service** (`[build] builder = "DOCKERFILE"`, `[deploy] healthcheckPath`, restart policy) | **Blueprint (IaC) cho cả hạ tầng**: web service + Key Value (Redis) cùng trong 1 file | Railway tạo project/service/Redis qua CLI hoặc dashboard rồi gắn file vào service; Render đọc blueprint để tạo tất cả từ repo |
| Thư mục code | Chạy `railway up` ngay trong `06-lab-complete/` nên không cần khai báo | Phải có `rootDir: 06-lab-complete` vì Render đọc blueprint từ gốc repo GitHub | Railway upload thư mục hiện tại; Render build từ git repo |
| Biến môi trường | Không ghi trong file; đặt bằng `railway variables set AGENT_API_KEY=...`, `REDIS_URL` tham chiếu biến của service Redis | Khai báo ngay trong `envVars`: giá trị thường (`RATE_LIMIT_PER_MINUTE`), secret `sync: false` (nhập trên dashboard), `REDIS_URL` lấy bằng `fromService ... property: connectionString` | Render muốn blueprint mô tả đủ biến nhưng không chứa giá trị secret; Railway tách biến ra khỏi file |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 60` | `healthCheckPath: /health` | Cùng ý nghĩa: platform chỉ chuyển traffic sang bản mới khi `/health` trả 200 (zero-downtime deploy) |
| Port | Railway inject `PORT`; Dockerfile dùng `${PORT:-8000}` | Render cũng inject `PORT`, cùng Dockerfile | Cả hai cấp port động, nên app phải đọc `PORT` |
| Gói | Dùng credit trial | `plan: free` cho cả web và Key Value | Mô hình giá khác nhau |

### Exercise 3.3: Pipeline Cloud Run (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` chạy 4 bước tuần tự (mỗi bước `waitFor` bước trước), tự kích hoạt khi push lên repo:
1. **test**: trong `python:3.11-slim`, cài `requirements.txt` + pytest, chạy `pytest tests/`. Test fail thì dừng pipeline, không build.
2. **build**: `docker build` với 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tái dùng layer.
3. **push**: đẩy cả 2 tag lên Container Registry.
4. **deploy**: `gcloud run deploy ai-agent` bằng image đúng `$COMMIT_SHA` (truy vết được, rollback được) ở `asia-southeast1`, min 1 / max 10 instance, 512Mi/1 CPU, timeout 60s, secret `OPENAI_API_KEY` lấy từ Secret Manager.

`service.yaml` là khai báo Knative tương ứng: autoscale theo `containerConcurrency: 80`, `minScale: 1` để tránh cold start, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, `AGENT_API_KEY`/`OPENAI_API_KEY` lấy bằng `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop`, chạy `AGENT_API_KEY=secret-key-123 python app.py`)

- **Check ở đâu:** dependency `verify_api_key()` dùng `APIKeyHeader(name="X-API-Key", auto_error=False)` và được inject vào `/ask` bằng `Depends(verify_api_key)`.
- **Sai key thì sao:** thiếu header → **401** "Missing API key"; có header nhưng sai → **403** "Invalid API key". (Part 6 trả 401 cho cả hai trường hợp, theo yêu cầu của bảng tiêu chí.)
- **Rotate key:** key đọc từ biến môi trường `AGENT_API_KEY`, nên đổi biến trên platform rồi restart/redeploy, không sửa code. Muốn không downtime thì cho phép đồng thời 2 key (cũ + mới) trong một thời gian, báo client chuyển sang key mới rồi bỏ key cũ.

```text
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`)

Flow: `POST /auth/token` (username/password) → server ký JWT HS256 chứa `sub`, `role`, `iat`, `exp` (60 phút) → client gửi `Authorization: Bearer <token>` → `verify_token()` kiểm tra chữ ký và hạn, lấy `username`/`role` mà không cần tra DB (stateless).

```text
$ curl -i http://localhost:8000/auth/token -X POST -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJhbGciOiJI...<masked>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."}

$ curl -i http://localhost:8000/auth/token -X POST -H "Content-Type: application/json" -d '{"username": "student", "password": "x"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i http://localhost:8000/ask -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```

### Exercise 4.3: Rate limiting

- **Algorithm:** sliding window log: mỗi user có một `deque` timestamp, loại các timestamp cũ hơn 60 s rồi đếm.
- **Limit:** `rate_limiter_user` = **10 request / 60 s**; `rate_limiter_admin` = 100 request / 60 s.
- **Bypass cho admin:** `/ask` chọn limiter theo role trong JWT: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`. Tài khoản `teacher` (role `admin`) dùng ngưỡng 100/phút.
- Hạn chế: state nằm trong RAM của process, nên nhiều instance/worker mỗi cái đếm riêng và restart là mất. Part 6 chuyển sang Redis sorted set.

Vòng lặp 20 request bằng token `student` (request thứ 1 trong phút là lệnh gọi ở 4.2, nên vòng lặp bị chặn ở request thứ 10):

```text
req 1 -> 200
req 2 -> 200
...
req 9 -> 200
req 10 -> 429
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795595938
retry-after: 56
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":56}}
req 11 -> 429
...
req 20 -> 429
```

### Exercise 4.4: Cost guard

`04-api-gateway/production/cost_guard.py` (bản mẫu): đếm token input/output theo user/ngày trong RAM, `check_budget()` gọi trước LLM, vượt $1/ngày/user → **402**, vượt $10/ngày toàn hệ thống → 503; cảnh báo khi dùng 80%. `GET /me/usage` sau vòng lặp trên: `{"requests":10,"input_tokens":40,"output_tokens":312,"cost_usd":0.000193,"budget_usd":1.0,...}`.

Cách mình làm ở Part 6 (`06-lab-complete/app/cost_guard.py`), theo yêu cầu "track trong Redis, reset theo kỳ":
- Giá: input $0.00015/1K token, output $0.0006/1K token; token ước lượng ~4 ký tự/token.
- `check_budget()` (gọi **trước** LLM): `MGET cost:daily:<YYYY-MM-DD> cost:monthly:<YYYY-MM>`; tổng ngày ≥ `DAILY_BUDGET_USD` hoặc tổng tháng ≥ `MONTHLY_BUDGET_USD` → `HTTPException(402)`.
- `record_cost()` (gọi **sau** LLM): `INCRBYFLOAT` cả 2 key trong một pipeline, `EXPIRE` 2 ngày / 32 ngày. Key gắn theo ngày/tháng (UTC) nên tự "reset" khi sang kỳ mới, TTL dọn key cũ.
- Lưu trong Redis nên mọi worker/instance cộng chung một ngân sách và không mất khi restart. Script chấm xác nhận: budget 0.000001 USD → 402.
- Rate limit bảo vệ **tốc độ** (chống spam/DoS, chia đều tài nguyên); cost guard bảo vệ **tiền** (nhiều client hợp lệ, hoặc prompt dài, vẫn có thể đốt hết ngân sách dù không ai vượt rate limit). Cần cả hai.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- `/health` (liveness): process còn sống thì trả 200 kèm uptime, version, kiểm tra memory (psutil); `status` là `ok`/`degraded`. Platform restart container khi endpoint này fail.
- `/ready` (readiness): trả 503 khi đang khởi động hoặc đang shutdown (`_is_ready = False`). Load balancer ngừng gửi traffic nhưng không restart.
- Khác nhau: liveness fail → **restart**; readiness fail → **ngừng route traffic** (instance vẫn chạy, có thể tự hồi phục, ví dụ Redis kết nối lại). Vì vậy `/health` không nên phụ thuộc Redis (Redis chết mà restart agent thì vô ích), còn `/ready` phải kiểm tra Redis.

### Exercise 5.2: Graceful shutdown

App dùng `lifespan`: khi shutdown đặt `_is_ready=False`, chờ `_in_flight_requests` về 0 (tối đa 30 s); `uvicorn.run(..., timeout_graceful_shutdown=30)`; handler SIGTERM tự đăng ký chỉ log, để uvicorn xử lý. Test trong container Linux (Windows không gửi được SIGTERM): gửi request rồi `kill -TERM` sau 0.05 s:

```text
GET /health -> 200
GET /ready  -> 200
sent SIGTERM to 11
in-flight request -> 200 {"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi củ (3.00s)
app exit code: 0
--- app log
INFO:     Shutting down
INFO:     127.0.0.1:33806 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 08:42:38,697 INFO 🔄 Graceful shutdown initiated...
2026-11-25 08:42:38,697 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [11]
```

Request đang chạy vẫn hoàn thành (200), sau đó process thoát với exit code 0. Uvicorn ngừng nhận kết nối mới ngay khi nhận SIGTERM ("Shutting down") nhưng chờ request đang xử lý xong.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/history trong Redis (`session:<id>` với `SETEX` TTL), chỉ fallback sang dict trong RAM khi không có Redis. Lý do: khi chạy nhiều instance sau load balancer, mỗi instance có RAM riêng. Request 1 vào instance A, request 2 vào instance B thì B không thấy history; instance restart hoặc scale down là mất state. Đưa state ra Redis thì instance nào cũng phục vụ được bất kỳ request nào.

### Exercise 5.4: Load balancing (`docker compose -p k5-day14-scaling up -d --build --scale agent=3`, cổng 8080, `POST /chat`)

```text
NAME                       STATUS
k5-day14-scaling-agent-1   Up 14 seconds (healthy)
k5-day14-scaling-agent-2   Up 13 seconds (healthy)
k5-day14-scaling-agent-3   Up 13 seconds (healthy)
k5-day14-scaling-nginx-1   Up 12 seconds
k5-day14-scaling-redis-1   Up 25 seconds (healthy)

### 10 requests
req 1 -> instance-097578 redis
req 2 -> instance-09d72f redis
req 3 -> instance-80cd26 redis
req 4 -> instance-097578 redis
...
req 10 -> instance-097578 redis

$ docker compose -p k5-day14-scaling logs agent | grep "POST /chat"   (đếm theo container)
      6 agent-1
      5 agent-2
      5 agent-3
```

Nginx round-robin xoay vòng đều 3 instance (16 request `/chat`: 6/5/5). Thử `docker stop k5-day14-scaling-agent-2` rồi gửi tiếp 6 request cùng session: **cả 6 đều 200**, history tăng từ 2 lên 14 message. Traffic tự chuyển sang 2 instance còn lại (`proxy_next_upstream error timeout http_503`) và session không mất vì nằm trong Redis.

### Exercise 5.5: `python test_stateless.py`

```text
Session ID: 23149771-c07d-409d-98bd-fedbe2cc81a1

Request 1: [instance-097578]  Q: What is Docker?
Request 2: [instance-09d72f]  Q: Why do we need containers?
Request 3: [instance-80cd26]  Q: What is Kubernetes?
Request 4: [instance-097578]  Q: How does load balancing work?
Request 5: [instance-09d72f]  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-097578', 'instance-80cd26', 'instance-09d72f'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

Giải thích: 5 câu hỏi trong cùng một session đi qua cả 3 instance khác nhau, nhưng lịch sử đủ 10 message (5 user + 5 assistant) theo đúng thứ tự. Mỗi instance đọc và ghi history ở Redis chứ không ở RAM của mình. Script không tự tắt instance; thí nghiệm tắt instance ở 5.4 bổ sung cho phần này. (Trên Windows phải chạy với `PYTHONIOENCODING=utf-8`, nếu không `print()` tiếng Việt lỗi `UnicodeEncodeError`, giống Part 1.)

---

## Part 6: Final Project — output `check_production_ready.py`

Code nằm trong `06-lab-complete/`: `app/main.py`, `config.py`, `auth.py`, `rate_limiter.py`, `cost_guard.py`, `redis_client.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml`.

Cách chạy: máy làm bài là Windows và chưa có distro WSL Ubuntu, nên script chấm (không sửa) được chạy trong container Linux `docker:27-dind` (có Docker riêng bên trong, `os.name == "posix"`), với lệnh `python check_production_ready.py` trong `06-lab-complete/`. Chạy trực tiếp bằng Python Windows thì ra 11/12, 42/42 checks (chỉ mục SIGTERM runtime ⚠️ vì Windows không gửi được SIGTERM; `docker stop` vẫn tắt êm với exit code 0 trong 0,7 giây).

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết từ cùng lần chạy:

```text
✅ Docker: build image thành công — 38s
✅ Docker: image < 500 MB — 183 MB
✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ Khi chạy: Redis mất kết nối → /ready 503 (còn /health vẫn 200)
✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 1.0s
```

(Trên Docker Desktop của Windows, cùng image được báo 63 MB vì Docker Desktop dùng containerd image store và tính kích thước khác; trong dind với storage driver cổ điển là 183 MB. Cả hai đều < 500 MB.)

### Giải thích thiết kế

- **`/health` và `/ready`:** `/health` chỉ báo process còn sống, không chạm Redis, nên Redis chết thì vẫn 200 và platform không restart vô ích. `/ready` ping Redis; mất Redis → 503 để load balancer ngừng gửi traffic tới instance chưa phục vụ được (rate limit và cost guard cần Redis).
- **Auth:** `APIKeyHeader("X-API-Key", auto_error=False)`, so sánh bằng `hmac.compare_digest`; thiếu, sai, hoặc server chưa đặt `AGENT_API_KEY` → 401.
- **Rate limit:** sliding window bằng Redis sorted set theo hash của API key: `ZREMRANGEBYSCORE` → `ZCARD` → (vượt → 429 + `Retry-After`) → `ZADD` + `EXPIRE`. State nằm trong Redis nên dùng chung giữa 2 worker và còn sau khi restart.
- **Cost guard:** `INCRBYFLOAT` vào `cost:daily:YYYY-MM-DD` và `cost:monthly:YYYY-MM` (có TTL); kiểm tra trước khi gọi LLM, ≥ `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` → 402.
- **Log JSON:** `JsonFormatter` tự viết (`json.dumps`), middleware ghi một dòng mỗi request (`method, path, status, ms`).
- **Graceful shutdown:** uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong → chạy phần shutdown của `lifespan` → exit 0. Không tự đè signal handler của uvicorn. Trong Docker, `CMD ["sh","-c","exec uvicorn ..."]` để uvicorn là PID 1.
- **Lỗi:** 422 có `details` gọn, Redis lỗi → 503, lỗi khác → 500 `{"error":"internal_error"}`, không trả stack trace.

---

## Part 7: Benchmark & Observability

Chi tiết và số liệu: [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md), file gốc trong `reports/` và `benchmark-results/`.

### Exercise 7.1: Metrics

Sau 1 request `POST /ask` vào v2 (`curl http://localhost:8002/metrics | grep -v "^#"`):

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0                     ← Counter
agent_in_flight_requests 0.0                                                          ← Gauge
llm_request_duration_seconds_bucket{le="0.2",model="mock-llm-small"} 1.0              ← Histogram (bucket)
llm_request_duration_seconds_count{model="mock-llm-small"} 1.0
llm_request_duration_seconds_sum{model="mock-llm-small"} 0.17975262211598855
llm_tokens_total{model="mock-llm-small",type="input"} 91.0
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05
agent_question_length_chars_count 1.0                                                 ← metric mới thêm (Histogram)
agent_question_length_chars_sum 40.0
agent_info{model="mock-llm-small",version="v2"} 1.0
```

1. Counter: `http_requests_total`, `llm_tokens_total`, `llm_cost_usd_total` (chỉ tăng). Gauge: `agent_in_flight_requests`, `agent_info` (lên xuống / giá trị hiện tại). Histogram: `http_request_duration_seconds_*`, `llm_request_duration_seconds_*` (`_bucket`/`_count`/`_sum`).
2. Nếu dùng `request.url.path` làm label: mỗi URL lạ (`/wp-admin`, `/.env`, `/a1b2c3`…) tạo một time series mới cho **mỗi** tổ hợp method × status × bucket histogram. Bot quét vài nghìn URL là sinh ra hàng chục nghìn series (cardinality bùng nổ), Prometheus tốn RAM/đĩa, query chậm, có thể OOM. Kẻ tấn công còn chủ động làm sập được hệ thống giám sát. Dùng route template và gom URL không khớp thành `unmatched` thì số series bị chặn trên.
3. Đã thêm `agent_question_length_chars` (Histogram, buckets 20/50/100/200/500/1000/2000) vào `06-lab-complete/benchmark-observability/develop/app.py`, gọi `QUESTION_LENGTH.observe(len(body.question))` trong `/ask`. Câu hỏi 40 ký tự → `_count 1`, `_sum 40`.

### Exercise 7.2 – 7.3

Xem mục 2 và 3 của `BENCHMARK_REPORT.md`. Tóm tắt: v1 4.3% → v2 91.3% → v2 sửa KB **100% (23/23), 0 regression, gate PASS**.

**Câu hỏi 7.2: vì sao avg tăng nhẹ còn p99 tăng vọt?** Request chậm chỉ chiếm 3–10% nên khi chia trung bình bị "pha loãng" (avg 200 → 253 ms ở 0.03) nhưng chúng nằm trọn trong 1% đuôi (p99 477 → 1708 ms). SLO nên đặt theo **p95/p99** vì phản ánh trải nghiệm của nhóm người dùng tệ nhất; avg có thể trông ổn trong khi 1/10 người dùng chờ gần 2 s.

**Job CI (Exercise 7.3):** `.github/workflows/eval.yml`. Trên mỗi PR đụng `06-lab-complete/benchmark-observability/**`: cài deps → bật agent v2 (`MOCK_ERROR_RATE=0`) → chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2.json`. `run_eval.py` trả **exit code 1** khi accuracy < ngưỡng hoặc có câu regression so với baseline, nên step fail → job fail. Bật branch protection "Require status checks to pass" cho check `quality-gate / eval` thì PR không merge được. Thử ở local: v1 gate FAIL (exit 1), v2 PASS (exit 0).

### Exercise 7.4 – 7.6

Xem mục 4 và 5 của `BENCHMARK_REPORT.md`: 2 instance chia đều 9.31/9.31 req/s, p95 612 ms vs avg 261 ms, input 1431 tokens/s vs output 749 tokens/s, `/metrics` qua Nginx → 404; trace chậm `382f4b81…` (95% thời gian ở span LLM), trace lỗi `1cbfc90b…` có event `exception`; alert A firing sau 96 s, B sau 146 s, D sau 46 s.

**Câu hỏi 7.6 (`AGENT_VERSION=v1` không làm alert nào firing):** lỗi chất lượng không làm đổi latency/error rate/`up`. Phát hiện bằng eval: quality gate trong CI/CD trước khi deploy, eval định kỳ (synthetic) trên production, metric proxy (retrieval hit ratio, tỷ lệ câu trả lời generic), feedback người dùng, canary so sánh chất lượng bản mới với bản cũ.

---

## Ghi chú môi trường và các chỗ phải xử lý

- **Ổ C: đầy (0 GB)** giữa Part 2 làm Docker treo. Đã chuyển dữ liệu Docker Desktop sang `D:\DockerDesktop` (setting `CustomWslDistroDir`) và dọn cache, rồi build lại.
- **Windows không gửi được SIGTERM:** script chấm Part 6 và test graceful shutdown Part 5 chạy trong container Linux.
- **Encoding Windows (cp1252):** `print()`/`write_text()` tiếng Việt lỗi trên Python Windows → chạy với `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` (không sửa script đề). Body tiếng Việt cho curl gửi từ file UTF-8.
- **`06-lab-complete/benchmark-observability/production/Dockerfile`:** bản đề crash `ModuleNotFoundError: uvicorn` (user `agent` có HOME=`/app`, packages ở `/home/agent/.local`). Sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local`.
- **`docker compose run locust`** tự đưa số agent về `replicas: 2` → đo scale 3 instance bị sai; đo lại bằng `run --no-deps`.

---

## AI log

| # | Hỏi / giao việc | Nhận được | Mình tự kiểm tra thế nào |
|---|---|---|---|
| 1 | Nhờ Claude Code (Claude Opus 5.5, trong VS Code) đọc đề và tóm tắt yêu cầu lab | Bảng tóm tắt Part 1–7, điểm số, các lưu ý K5 | Đối chiếu với `README.md`, `CHECKPOINTS.md`, `RUBRIC.md` |
| 2 | Nhờ Claude Code viết code Part 6 (các TODO trong `06-lab-complete/`) | Code `app/*.py`, `Dockerfile`, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Chạy `check_production_ready.py` bản đầy đủ trong Linux: PASS 12/12. _(Cần tự đọc lại và giải thích được từng đoạn code)_ |
| 3 | Nhờ Claude Code chạy Part 1, 2, 4, 5 và ghi kết quả | Output curl/docker ở trên; phát hiện lỗi `UnicodeEncodeError` trên Windows và log JSON không hợp lệ ở app mẫu Part 2 | Output lấy trực tiếp từ lệnh đã chạy |
| 5 | Nhờ Claude Code cài Railway CLI, deploy `06-lab-complete/` lên Railway (sau khi mình tự đăng nhập bằng device code) và chụp ảnh các UI | Project `k5-day14-agent` (agent + Redis), URL public; ảnh Prometheus/Jaeger/Grafana và `running.png`/`test.png` chụp bằng Playwright + Edge headless | Chạy 4 lệnh CP6 trên URL public (200/200/401/200) và `check_production_ready.py --public-url`; xem lại từng ảnh |
| 4 | Nhờ Claude Code chạy Part 7 (load test, eval, sửa `KNOWLEDGE_BASE`, CI job, stack Prometheus/Grafana/Jaeger, thí nghiệm alert) | File `reports/*.json`, `benchmark-results/*.csv`, `.github/workflows/eval.yml`, `BENCHMARK_REPORT.md`; sửa `production/Dockerfile` (PYTHONUSERBASE); phát hiện `compose run` làm sai benchmark scale | Mọi số trong báo cáo lấy từ JSON/CSV và từ API Prometheus/Jaeger đã gọi; đo lại scale sau khi kiểm tra số instance |
