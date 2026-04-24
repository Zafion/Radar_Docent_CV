# API rate limiting

This project keeps FastAPI docs disabled in the app itself:

- `/docs` disabled
- `/redoc` disabled
- `/openapi.json` disabled

Rate limiting is applied only to `/api/*` routes through `ApiRateLimitMiddleware`.

## Defaults

- General API: `20 requests / 10 seconds / IP`
- Heavy search endpoints:
  - `/api/search/persons`
  - `/api/persons/profile`
  - `10 requests / 10 seconds / IP`

## Proxy-aware client IP order

1. `CF-Connecting-IP`
2. `X-Forwarded-For`
3. `request.client.host`

## Optional environment overrides

```bash
RADAR_API_RATE_LIMIT_MAX_REQUESTS=20
RADAR_API_RATE_LIMIT_WINDOW_SECONDS=10
RADAR_API_RATE_LIMIT_SEARCH_MAX_REQUESTS=10
RADAR_API_RATE_LIMIT_SEARCH_WINDOW_SECONDS=10
```
