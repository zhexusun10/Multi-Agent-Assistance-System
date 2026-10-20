r"""Optional local tunnel origin. Exposes provider webhooks only; verification stays in graph service.

Run manually: .venv\Scripts\python.exe -m uvicorn api.webhook_gateway:app --host 127.0.0.1 --port 8011 --no-access-log
"""
import os

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response

app = FastAPI(title="Messaging webhook gateway", docs_url=None, redoc_url=None, openapi_url=None)
GRAPH_SERVICE_URL = os.getenv("GRAPH_SERVICE_URL", "http://127.0.0.1:3001").rstrip("/")
MAX_BODY = 65536


def graph_client():
    return httpx.AsyncClient(base_url=GRAPH_SERVICE_URL, timeout=20, follow_redirects=False)


@app.api_route("/api/webhooks/{provider}", methods=["GET", "POST"])
async def webhook(provider: str, request: Request, client: httpx.AsyncClient = Depends(graph_client)):
    try:
        if provider not in {"whatsapp", "telegram"} or (request.method == "GET" and provider != "whatsapp"):
            raise HTTPException(404, "Not found")
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "Invalid content length")
        if length < 0 or length > MAX_BODY:
            raise HTTPException(413, "Request too large")
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                raise HTTPException(413, "Request too large")
            chunks.append(chunk)
        headers = {name: request.headers[name] for name in
                   ("content-type", "x-hub-signature-256", "x-telegram-bot-api-secret-token")
                   if name in request.headers}
        try:
            result = await client.request(request.method, f"/webhooks/{provider}",
                                          params=request.query_params, content=b"".join(chunks), headers=headers)
        except httpx.HTTPError:
            raise HTTPException(503, "Local graph service unavailable")
        return Response(result.content, status_code=result.status_code,
                        headers={"content-type": result.headers.get("content-type", "application/json"),
                                 "cache-control": "no-store", "x-content-type-options": "nosniff"})
    finally:
        await client.aclose()
