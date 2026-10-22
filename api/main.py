import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


app = FastAPI(title="Multi-Agent Assistance API")
GRAPH_SERVICE_URL = os.getenv("GRAPH_SERVICE_URL", "http://127.0.0.1:3001").rstrip("/")


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, max_length=100)
    source_request_id: str | None = Field(default=None, max_length=100)


class RuntimeEventRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1)
    payload: Any


def get_graph_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
    return httpx.AsyncClient(base_url=GRAPH_SERVICE_URL, timeout=timeout)


@app.get("/health")
async def health(client: httpx.AsyncClient = Depends(get_graph_client)):
    try:
        upstream = await client.get("/health", timeout=5)
        upstream.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="LangGraph service unavailable")
    finally:
        await client.aclose()
    return {"status": "ok"}


async def stream_graph(graph_request: httpx.Request, client: httpx.AsyncClient):
    upstream = None
    try:
        upstream = await client.send(graph_request, stream=True)
        upstream.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"LangGraph service returned {exc.response.status_code}")
    except httpx.HTTPError:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=503, detail="LangGraph service unavailable")

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(relay(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-store, no-transform",
        "X-Accel-Buffering": "no",
    })


@app.post("/api/query")
async def query(request: QueryRequest, authorization: str | None = Header(default=None), client: httpx.AsyncClient = Depends(get_graph_client)):
    if not request.query.strip():
        await client.aclose()
        raise HTTPException(status_code=422, detail="query must not be blank")
    if request.session_id is not None and not request.session_id.strip():
        await client.aclose()
        raise HTTPException(status_code=422, detail="session_id must not be blank")
    graph_request = client.build_request("POST", "/run", json=request.model_dump(exclude_none=True), headers={"Authorization": authorization} if authorization else None)
    return await stream_graph(graph_request, client)


@app.get("/api/events")
async def events(
    session_id: str = Query(min_length=1, max_length=128),
    after: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    authorization: str | None = Header(default=None),
    client: httpx.AsyncClient = Depends(get_graph_client),
):
    params = {"session_id": session_id}
    if after is not None:
        params["after"] = str(after)
    headers = {}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    if authorization:
        headers["Authorization"] = authorization
    graph_request = client.build_request("GET", "/events", params=params, headers=headers)
    return await stream_graph(graph_request, client)


@app.post("/api/runtime/events", status_code=202)
async def runtime_event(request: RuntimeEventRequest, client: httpx.AsyncClient = Depends(get_graph_client)):
    if not request.session_id.strip() or not request.name.strip():
        await client.aclose()
        raise HTTPException(status_code=422, detail="session_id and name must not be blank")
    try:
        response = await client.post("/runtime/events", json=request.model_dump())
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="LangGraph service unavailable")
    finally:
        await client.aclose()


class LocalBoundaries:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 65537
        if length > 65536:
            return await JSONResponse({"detail": "请求内容过大"}, status_code=413)(scope, receive, send)

        async def bounded_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
                ]
            await send(message)
        await self.app(scope, receive, bounded_send)

app.add_middleware(LocalBoundaries)

@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def business_proxy(path: str, request: Request):
    allowed = path in {"config", "sessions", "state", "feedback", "evidence", "calendar", "followups", "summaries/daily", "integrations/templates", "webhooks/whatsapp", "webhooks/telegram"} or (request.method == "POST" and path == "behavior/views") or (request.method == "GET" and path == "behavior/summary") or path.startswith("actions/") or path.startswith("artifacts/") or (request.method == "GET" and (path.startswith("locations/") or path.startswith("listings/")))
    if not allowed:
        raise HTTPException(404, "Not found")
    headers = {"Authorization": request.headers["authorization"]} if "authorization" in request.headers else {}
    for name in ("x-hub-signature-256", "x-telegram-bot-api-secret-token"):
        if name in request.headers:
            headers[name] = request.headers[name]
    data = await request.body()
    if len(data) > 65536:
        raise HTTPException(413, "请求内容过大")
    async with httpx.AsyncClient(base_url=GRAPH_SERVICE_URL, timeout=30) as client:
        try:
            result = await client.request(request.method, "/" + path, params=request.query_params, content=data, headers={**headers, "Content-Type": "application/json"})
        except httpx.HTTPError:
            raise HTTPException(503, "业务服务暂不可用")
        forwarded = {name: value for name, value in result.headers.items() if name.lower() in {"content-type", "content-disposition", "cache-control"}}
        if path == "state":
            forwarded["cache-control"] = "no-store"
        return Response(result.content, status_code=result.status_code, headers=forwarded)


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
if FRONTEND.is_dir():
    @app.get("/assets/followup-message.js", include_in_schema=False)
    def followup_message_module():
        # One pure renderer is compiled for both Node and the browser; no duplicate message templates.
        return FileResponse(FRONTEND.parent / "dist" / "shared" / "followup-message.js", media_type="application/javascript")

    app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend():
        return FileResponse(FRONTEND / "index.html")
