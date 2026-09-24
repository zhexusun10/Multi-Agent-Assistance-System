import os
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


app = FastAPI(title="Multi-Agent Assistance API")
GRAPH_SERVICE_URL = os.getenv("GRAPH_SERVICE_URL", "http://127.0.0.1:3001").rstrip("/")


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


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

    return StreamingResponse(relay(), media_type="text/event-stream")


@app.post("/api/query")
async def query(request: QueryRequest, client: httpx.AsyncClient = Depends(get_graph_client)):
    if not request.query.strip():
        await client.aclose()
        raise HTTPException(status_code=422, detail="query must not be blank")
    if request.session_id is not None and not request.session_id.strip():
        await client.aclose()
        raise HTTPException(status_code=422, detail="session_id must not be blank")
    graph_request = client.build_request("POST", "/run", json=request.model_dump(exclude_none=True))
    return await stream_graph(graph_request, client)


@app.get("/api/events")
async def events(
    session_id: str = Query(min_length=1, max_length=128),
    after: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    client: httpx.AsyncClient = Depends(get_graph_client),
):
    params = {"session_id": session_id}
    if after is not None:
        params["after"] = str(after)
    headers = {"Last-Event-ID": last_event_id} if last_event_id is not None else None
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
