import hashlib
from math import ceil
from typing import Callable, Optional, Union

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.status import HTTP_429_TOO_MANY_REQUESTS
from starlette.websockets import WebSocket


_refund_script_sha = None
_refund_script = """
local key = KEYS[1]
local current = tonumber(redis.call('get', key) or "0")
if current > 0 then
    redis.call("DECR", key)
end
return current
"""


def iter_routes(routes):
    """Aplatit récursivement les routers inclus.

    FastAPI >= 0.116 imbrique les routers via des `_IncludedRouter` présents dans
    `app.routes` SANS attribut `path`/`methods`. Itérer `app.routes` brut et lire
    `route.path` lève alors `AttributeError`. On descend récursivement et on ne yield
    que les vraies routes (avec `path` + `methods`), pour garder un index par-route.
    """
    for route in routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            yield route
        sub = getattr(route, "routes", None)
        if sub:
            yield from iter_routes(sub)

async def mark_request_ignored(request: Request, response: Response) -> None:
    """
    Marque une requête comme devant être ignorée du rate limiting principal.
    Rembourse automatiquement le crédit de rate limiting et applique les ConditionalRateLimiter.
    
    :param request: L'objet Request FastAPI/Starlette
    :param response: L'objet Response FastAPI/Starlette
    """
    # 1. Rembourser le rate limiting principal
    await refund_rate_limit_for_request(request)
    
    # 2. Appliquer les ConditionalRateLimiter s'il y en a
    if hasattr(request.state, 'conditional_limiters'):
        for limiter in request.state.conditional_limiters:
            await limiter.apply_for_ignored_request(request, response)
    
    # 3. Marquer comme ignorée
    setattr(response, '_rate_limit_ignored', True)


async def refund_rate_limit_for_request(request: Request) -> None:
    """
    Rembourse automatiquement le rate limiting si la requête est marquée comme ignorée.
    À appeler dans votre endpoint après avoir potentiellement marqué la requête.
    """
    global _refund_script_sha
    
    if not FastAPILimiter.redis:
        return
    
    # Trouver tous les RateLimiter appliqués à cette route
    route_index = 0
    for i, route in enumerate(iter_routes(request.app.routes)):
        if route.path == request.scope["path"] and request.method in route.methods:
            route_index = i
            for j, dependency in enumerate(route.dependencies):
                # Vérifier si c'est un RateLimiter (pas ConditionalRateLimiter)
                dep_class = dependency.dependency.__class__.__name__
                if dep_class == "RateLimiter":
                    # Construire la clé Redis identique à celle du RateLimiter
                    identifier = FastAPILimiter.identifier
                    rate_key = await identifier(request)
                    key = f"{FastAPILimiter.prefix}:{rate_key}:{route_index}:{j}"
                    
                    # Charger le script de remboursement si nécessaire
                    if _refund_script_sha is None:
                        _refund_script_sha = await FastAPILimiter.redis.script_load(_refund_script)
                    
                    # Exécuter le remboursement
                    try:
                        await FastAPILimiter.redis.evalsha(_refund_script_sha, 1, key)
                    except:
                        # Recharger le script si nécessaire et réessayer
                        _refund_script_sha = await FastAPILimiter.redis.script_load(_refund_script)
                        await FastAPILimiter.redis.evalsha(_refund_script_sha, 1, key)
            break



async def default_identifier(request: Union[Request, WebSocket]):
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0]
    else:
        ip = request.client.host
    return ip + ":" + request.scope["path"]


async def http_default_callback(request: Request, response: Response, pexpire: int):
    """
    default callback when too many requests
    :param request:
    :param pexpire: The remaining milliseconds
    :param response:
    :return:
    """
    expire = ceil(pexpire / 1000)
    raise HTTPException(
        HTTP_429_TOO_MANY_REQUESTS, "Too Many Requests", headers={"Retry-After": str(expire)}
    )


async def ws_default_callback(ws: WebSocket, pexpire: int):
    """
    default callback when too many requests
    :param ws:
    :param pexpire: The remaining milliseconds
    :return:
    """
    expire = ceil(pexpire / 1000)
    raise HTTPException(
        HTTP_429_TOO_MANY_REQUESTS, "Too Many Requests", headers={"Retry-After": str(expire)}
    )

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

class FastAPILimiter:
    redis = None
    prefix: Optional[str] = None
    lua_sha: Optional[str] = None
    identifier: Optional[Callable] = None
    http_callback: Optional[Callable] = None
    ws_callback: Optional[Callable] = None
    lua_script = """local key = KEYS[1]
local limit = tonumber(ARGV[1])
local expire_time = ARGV[2]

local current = tonumber(redis.call('get', key) or "0")
if current > 0 then
 if current + 1 > limit then
 return redis.call("PTTL",key)
 else
        redis.call("INCR", key)
 return 0
 end
else
    redis.call("SET", key, 1,"px",expire_time)
 return 0
end"""

    @classmethod
    async def init(
            cls,
            redis,
            prefix: str = "fastapi-limiter",
            identifier: Callable = default_identifier,
            http_callback: Callable = http_default_callback,
            ws_callback: Callable = ws_default_callback,
            authorized_passwords: list[str] = None,
            query_param_names: list[str] = None,
            bearer_token_headers: list[str] = None,
            api_key_headers: list[str] = None
    ) -> None:
        cls.redis = redis
        cls.prefix = prefix
        cls.identifier = identifier
        cls.http_callback = http_callback
        cls.ws_callback = ws_callback
        cls.authorized_passwords = [hash_password(pw) for pw in (authorized_passwords or [])]
        cls.query_param_names = query_param_names or []
        cls.bearer_token_headers = bearer_token_headers or []
        cls.api_key_headers = api_key_headers or []
        cls.lua_sha = await redis.script_load(cls.lua_script)

    @classmethod
    async def close(cls) -> None:
        await cls.redis.close()
