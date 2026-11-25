# Deployment Information

## Public URL

https://agent-production-0a677981.up.railway.app

## Platform

**Railway**: project `k5-day14-agent`, environment `production`, gồm 2 service:

| Service | Nguồn | Ghi chú |
|---|---|---|
| `agent` | `06-lab-complete/` upload bằng `railway up`, build từ `Dockerfile` (`railway.toml`: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`) | Lắng nghe theo `PORT` do Railway cấp (`${PORT:-8000}` trong CMD) |
| `Redis` | `railway add --database redis` (image `redis:8.2`, có volume) | Lưu state rate limit + cost guard |

## Cách deploy đã dùng

```bash
cd 06-lab-complete
railway login --browserless
railway init --name k5-day14-agent
railway add --database redis
railway add --service agent \
  --variables "AGENT_API_KEY=<key ngẫu nhiên, không ghi vào repo>" \
  --variables 'REDIS_URL=${{Redis.REDIS_URL}}' \
  --variables "ENVIRONMENT=production" --variables "RATE_LIMIT_PER_MINUTE=10" \
  --variables "DAILY_BUDGET_USD=1.0" --variables "MONTHLY_BUDGET_USD=10.0"
railway link --project <project-id> --environment production --service agent
railway up --service agent --ci        # build Docker trên Railway → "Deploy complete"
railway domain --service agent         # → https://agent-production-0a677981.up.railway.app
```

## Test Commands

```bash
BASE=https://agent-production-0a677981.up.railway.app
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả (2026-11-25, xem `screenshots/test.png`):

```text
200
200
401
200
```

Response mẫu:

```text
GET  /health → {"status":"ok"}
GET  /ready  → {"status":"ready"}           ← Redis trên Railway kết nối được
POST /ask (có key) → {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được."}
```

Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`, đã có 2 request hợp lệ trước đó trong cùng phút), 12 request liên tiếp:

```text
200 200 200 200 200 200 200 200 429 429 429 429
```

Script chấm: `python check_production_ready.py --static-only --public-url https://agent-production-0a677981.up.railway.app`
→ `✅ Public URL: GET https://agent-production-0a677981.up.railway.app/health → 200`

## Environment Variables Set

Đặt trên Railway (service `agent`), **không có trong code hay git**:

- `AGENT_API_KEY`: secret ngẫu nhiên (`secrets.token_urlsafe`)
- `REDIS_URL` = `${{Redis.REDIS_URL}}` (tham chiếu tới service Redis trong project)
- `ENVIRONMENT=production`
- `RATE_LIMIT_PER_MINUTE=10`
- `DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0`
- `PORT`: Railway tự cấp
- `LOG_LEVEL`: không đặt, app mặc định `INFO`

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png): trang project trên railway.com
- [Service running](screenshots/running.png): `GET /health` và `GET /ready` của URL public
- [Test results](screenshots/test.png): 4 lệnh tự kiểm CP6
