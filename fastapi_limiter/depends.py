import secrets
from typing import Annotated, Callable, Optional

import hashlib

import redis as pyredis
from pydantic import Field
from starlette.requests import Request
from starlette.responses import Response
from starlette.websockets import WebSocket

# Import moved to avoid circular dependency
# FastAPILimiter will be imported locally where needed

def hash_input(value):
    return hashlib.sha256(value.encode()).hexdigest()


class RateLimiter:
    def __init__(
            self,
            times: Annotated[int, Field(ge=0)] = 1,
            milliseconds: Annotated[int, Field(ge=-1)] = 0,
            seconds: Annotated[int, Field(ge=-1)] = 0,
            minutes: Annotated[int, Field(ge=-1)] = 0,
            hours: Annotated[int, Field(ge=-1)] = 0,
            identifier: Optional[Callable] = None,
            callback: Optional[Callable] = None,
            enable_bypass: bool = False
    ):
        self.times = times
        self.milliseconds = milliseconds + 1000 * seconds + 60000 * minutes + 3600000 * hours
        self.identifier = identifier
        self.callback = callback
        self.enable_bypass = enable_bypass

    async def _check(self, key):
        from fastapi_limiter import FastAPILimiter
        redis = FastAPILimiter.redis
        pexpire = await redis.evalsha(
            FastAPILimiter.lua_sha, 1, key, str(self.times), str(self.milliseconds)
        )
        return pexpire

    async def __call__(self, request: Request, response: Response):
        from fastapi_limiter import FastAPILimiter, iter_routes
        
        if self.enable_bypass:
            for param in FastAPILimiter.query_param_names:
                raw_value = request.query_params.get(param, "")
                hashed_value = hash_input(raw_value)
                for password in FastAPILimiter.authorized_passwords:
                    if secrets.compare_digest(hashed_value, password):
                        return
            for header in FastAPILimiter.bearer_token_headers:
                raw_bearer_token = request.headers.get(header + " ", "")
                hashed_bearer_token = hash_input(raw_bearer_token)
                for password in FastAPILimiter.authorized_passwords:
                    if secrets.compare_digest(hashed_bearer_token, password):
                        return
            for header in FastAPILimiter.api_key_headers:
                raw_api_key = request.headers.get(header, "")
                hashed_api_key = hash_input(raw_api_key)
                for password in FastAPILimiter.authorized_passwords:
                    if secrets.compare_digest(hashed_api_key, password):
                        return

        if not FastAPILimiter.redis:
            raise Exception("You must call FastAPILimiter.init in startup event of fastapi!")
        route_index = 0
        dep_index = 0
        for i, route in enumerate(iter_routes(request.app.routes)):
            if route.path == request.scope["path"] and request.method in route.methods:
                route_index = i
                for j, dependency in enumerate(route.dependencies):
                    if self is dependency.dependency:
                        dep_index = j
                        break

        # moved here because constructor run before app startup
        identifier = self.identifier or FastAPILimiter.identifier
        callback = self.callback or FastAPILimiter.http_callback
        rate_key = await identifier(request)
        key = f"{FastAPILimiter.prefix}:{rate_key}:{route_index}:{dep_index}"
        try:
            pexpire = await self._check(key)
        except pyredis.exceptions.NoScriptError:
            FastAPILimiter.lua_sha = await FastAPILimiter.redis.script_load(
                FastAPILimiter.lua_script
            )
            pexpire = await self._check(key)
        if pexpire != 0:
            return await callback(request, response, pexpire)


class ConditionalRateLimiter(RateLimiter):
    """
    Rate limiter qui s'applique seulement aux requêtes ignorées.
    Utilisé comme rate limiting de secours pour éviter le spam.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def __call__(self, request: Request, response: Response):
        # Ne pas appliquer maintenant - sera appliqué seulement pour les requêtes ignorées
        # Stocker les infos pour une application conditionnelle plus tard
        if not hasattr(request.state, 'conditional_limiters'):
            request.state.conditional_limiters = []
        request.state.conditional_limiters.append(self)

    async def apply_for_ignored_request(self, request: Request, response: Response):
        """
        Applique le rate limiting conditionnel pour une requête marquée comme ignorée.
        """
        from fastapi_limiter import FastAPILimiter, iter_routes
        
        if not FastAPILimiter.redis:
            raise Exception("You must call FastAPILimiter.init in startup event of fastapi!")
        
        # Trouver l'index de cette route et de cette dépendance
        route_index = 0
        dep_index = 0
        for i, route in enumerate(iter_routes(request.app.routes)):
            if route.path == request.scope["path"] and request.method in route.methods:
                route_index = i
                for j, dependency in enumerate(route.dependencies):
                    if self is dependency.dependency:
                        dep_index = j
                        break
                break

        identifier = self.identifier or FastAPILimiter.identifier
        callback = self.callback or FastAPILimiter.http_callback
        rate_key = await identifier(request)
        key = f"{FastAPILimiter.prefix}:conditional:{rate_key}:{route_index}:{dep_index}"
        
        try:
            pexpire = await self._check(key)
        except pyredis.exceptions.NoScriptError:
            FastAPILimiter.lua_sha = await FastAPILimiter.redis.script_load(
                FastAPILimiter.lua_script
            )
            pexpire = await self._check(key)
        
        if pexpire != 0:
            return await callback(request, response, pexpire)


class WebSocketRateLimiter(RateLimiter):
    async def __call__(self, ws: WebSocket, context_key=""):
        from fastapi_limiter import FastAPILimiter
        
        if not FastAPILimiter.redis:
            raise Exception("You must call FastAPILimiter.init in startup event of fastapi!")
        identifier = self.identifier or FastAPILimiter.identifier
        rate_key = await identifier(ws)
        key = f"{FastAPILimiter.prefix}:ws:{rate_key}:{context_key}"
        pexpire = await self._check(key)
        callback = self.callback or FastAPILimiter.ws_callback
        if pexpire != 0:
            return await callback(ws, pexpire)
