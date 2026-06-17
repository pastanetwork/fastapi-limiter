# ChangeLog

## 0.1

### 0.1.10

- **Fix**: compat FastAPI >= 0.116. `router.routes` est désormais un arbre (objets
  intermédiaires `_IncludedRouter` sans `path`/`methods`) au lieu d'une liste plate
  d'`APIRoute`. L'itération de `request.app.routes` levait `AttributeError: '_IncludedRouter'
  object has no attribute 'path'` (HTTP 500 sur tout endpoint rate-limité). Ajout d'un
  helper `iter_routes()` qui aplatit récursivement l'arbre et ne retient que les routes
  réelles ; utilisé dans `RateLimiter`, `ConditionalRateLimiter` et `refund_rate_limit_for_request`.

### 0.1.5

- Replace aioredis to redis.

### 0.1.4

- Now use `lua` script.
- **Break change**: You should call `FastAPILimiter.init` with `await`.

```python
    await FastAPILimiter.init(redis)
```

### 0.1.3

- Support multiple rate strategy for one route. (#3)

### 0.1.2

- Use milliseconds instead of seconds as default unit of expiration.
- Update default_callback, round milliseconds up to nearest second for `Retry-After` value.
- Access response in the callback.
- Replace transaction with pipeline.

### 0.1.1

- Configuring the global default through the FastAPILimiter.init method.
- Update status to 429 when too many requests.
- Update default_callback params and add `Retry-After` response header.
