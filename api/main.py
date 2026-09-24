import os

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


app = FastAPI(title="Multi-Agent Assistance API")
GRAPH_SERVICE_URL = os.getenv("GRAPH_SERVICE_URL", "http://127.0.0.1:3001").rstrip("/")


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


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


@app.post("/api/query")
async def query(request: QueryRequest, client: httpx.AsyncClient = Depends(get_graph_client)):
    if not request.query.strip():
        await client.aclose()
        raise HTTPException(status_code=422, detail="query must not be blank")
    if request.session_id is not None and not request.session_id.strip():
        await client.aclose()
        raise HTTPException(status_code=422, detail="session_id must not be blank")

    upstream = None
    try:
        graph_request = client.build_request("POST", "/run", json=request.model_dump(exclude_none=True))
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
