import os
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError


app = FastAPI(title="Multi-Agent Assistance API")
GRAPH_SERVICE_URL = os.getenv("GRAPH_SERVICE_URL", "http://127.0.0.1:3001").rstrip("/")


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)


class AgentResult(BaseModel):
    agent: Literal["a", "b", "c", "d"]
    prompt: str
    output: str


class QueryResponse(BaseModel):
    answer: str
    agents: list[AgentResult]


async def get_graph_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=GRAPH_SERVICE_URL, timeout=120) as client:
        yield client


@app.get("/health")
async def health(client: httpx.AsyncClient = Depends(get_graph_client)):
    try:
        upstream = await client.get("/health", timeout=5)
        upstream.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="LangGraph service unavailable")
    return {"status": "ok"}


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest, client: httpx.AsyncClient = Depends(get_graph_client)):
    if not request.query.strip():
        raise HTTPException(status_code=422, detail="query must not be blank")
    try:
        upstream = await client.post("/run", json=request.model_dump())
        upstream.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"LangGraph service returned {exc.response.status_code}")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="LangGraph service unavailable")
    try:
        return QueryResponse.model_validate(upstream.json())
    except (ValueError, ValidationError):
        raise HTTPException(status_code=502, detail="Invalid LangGraph service response")
