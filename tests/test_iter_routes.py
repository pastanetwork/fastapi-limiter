"""`iter_routes` retrouve chaque route, imbriquée ou non, sous un index qui lui est propre — sans Redis."""

from fastapi import APIRouter, Depends, FastAPI

from fastapi_limiter import iter_routes
from fastapi_limiter.depends import RateLimiter

first_limit = RateLimiter(times=1, seconds=5)
second_limit = RateLimiter(times=2, seconds=5)
nested_limit = RateLimiter(times=3, seconds=5)


def _app() -> FastAPI:
    inner = APIRouter()

    @inner.get("/deep", dependencies=[Depends(nested_limit)])
    async def deep():
        return {}

    router = APIRouter()

    @router.get("/first", dependencies=[Depends(first_limit)])
    async def first():
        return {}

    @router.post("/first", dependencies=[Depends(second_limit)])
    async def first_post():
        return {}

    router.include_router(inner, prefix="/inner")
    app = FastAPI()

    @app.get("/")
    async def root():
        return {}

    app.include_router(router, prefix="/api")
    return app


def _slot(app: FastAPI, path: str, method: str, limiter: RateLimiter) -> tuple:
    """Rend (index de route, index de dépendance) comme `RateLimiter.__call__` les calcule."""
    for index, route in enumerate(iter_routes(app.routes)):
        if route.path == path and method in route.methods:
            for dep_index, dependency in enumerate(route.dependencies):
                if limiter is dependency.dependency:
                    return index, dep_index
            return index, None
    return None


def test_every_route_is_found_with_its_full_path():
    paths = {(route.path, method) for route in iter_routes(_app().routes) for method in route.methods}

    assert {("/", "GET"), ("/api/first", "GET"), ("/api/first", "POST"), ("/api/inner/deep", "GET")} <= paths


def test_each_limited_route_has_its_own_slot_and_finds_its_limiter():
    app = _app()

    slots = [_slot(app, "/api/first", "GET", first_limit), _slot(app, "/api/first", "POST", second_limit), _slot(app, "/api/inner/deep", "GET", nested_limit)]

    assert None not in slots and all(dep_index is not None for _, dep_index in slots)
    assert len({index for index, _ in slots}) == 3


def test_the_order_is_stable_between_two_walks():
    app = _app()

    assert [(route.path, sorted(route.methods)) for route in iter_routes(app.routes)] == [(route.path, sorted(route.methods)) for route in iter_routes(app.routes)]
