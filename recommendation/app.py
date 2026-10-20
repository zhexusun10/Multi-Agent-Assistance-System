"""HTTP transport and app presentation proofs. Native service/algorithms stay intact."""
import asyncio
import hmac
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware

from .agent.controller import LiveController, ScriptedController
from .config import RecommendationConfig
from .contracts.request import RecommendationRequest
from .models.candidate import Response
from .repositories.connections import ROOT, repositories
from .service import RecommendationService
from .state.store import StateStore
from .validation.rules import eligible
from .tracing.trace import digest

# The project file is authoritative for the provider credentials, as in src/server.ts.
load_dotenv(ROOT / ".env", override=True)
_lock = asyncio.Lock()


def state_path():
    return os.getenv("RECOMMENDATION_STATE_PATH", str(ROOT / "data/.runtime/recommendation/state.sqlite"))


def authorize(x_recommendation_token: str = Header(default="")):
    expected = os.getenv("RECOMMENDATION_SERVICE_TOKEN", "")
    if not expected or not hmac.compare_digest(expected, x_recommendation_token):
        raise HTTPException(401, "Internal recommendation service credential required")


def execute(request: RecommendationRequest, app_view=False):
    """Construct SQLite connection on the executing thread; never share it across threads."""
    listing, geographic = repositories(os.getenv("RECOMMENDATION_DATA_MODE", "database"))
    store = None
    try:
        store = StateStore(state_path())
        transport_request = request.model_dump(mode="json")
        if app_view:
            # App operations can be superseded after the native run commits but
            # before its response is adopted by the UI. Resolve the native head at
            # the transport boundary, once per request ID, without changing native
            # optimistic checks, requirements, feedback or immutable replay.
            store.db.execute("CREATE TABLE IF NOT EXISTS app_transport_requests(conversation_id TEXT,request_id TEXT,input_hash TEXT,request TEXT,PRIMARY KEY(conversation_id,request_id))")
            store.db.commit()
            with store.transaction():
                binding = store.db.execute("SELECT input_hash,request FROM app_transport_requests WHERE conversation_id=? AND request_id=?", (request.conversation_id,request.request_id)).fetchone()
                fingerprint = digest(transport_request)
                if binding:
                    if binding[0] != fingerprint:
                        raise HTTPException(409, "IDEMPOTENCY_CONFLICT: app request ID is bound to another body")
                    request = RecommendationRequest.model_validate_json(binding[1])
                else:
                    # A pre-existing native cached response must retain its original
                    # request identity. Let native reserve/replay reject changed bodies.
                    cached = store.cached(request.conversation_id, request.request_id)
                    previous = store.current(request.conversation_id)
                    if not cached and previous:
                        request = request.model_copy(update={"previous_state_id":previous["state_id"]})
                    store.db.execute("INSERT INTO app_transport_requests VALUES(?,?,?,?)", (request.conversation_id,request.request_id,fingerprint,request.model_dump_json()))
        controller_mode = os.getenv("RECOMMENDATION_CONTROLLER", "live")
        if controller_mode not in {"live", "scripted"}:
            raise ValueError("RECOMMENDATION_CONTROLLER must be live or scripted")
        controller = LiveController() if controller_mode == "live" else ScriptedController()
        service = RecommendationService(listing, geographic, store, controller, config=RecommendationConfig.from_file(os.getenv("RECOMMENDATION_CONFIG")))
        response = Response.model_validate(service.recommend(request).model_dump())
        native = response.model_dump(mode="json")
        if not app_view:
            return native
        cached = store.cached(request.conversation_id, request.request_id)
        if response.status == "error" or not cached:
            return {"request":request.model_dump(mode="json"),"transport_request":transport_request,"response": native, "presentation": {"listings": [], "counts": None}}
        # Proofs come from the SAME immutable run, never a fresh or differently ranked search.
        pool = cached[2]["pool"]
        ids = {item.listing.listing_id for item in response.recommendations} | {item.listing_id for item in response.unverified_candidates}
        criteria = request.requirements.criteria
        hard = {c.criterion_id for c in criteria if c.strength == "hard"}
        from .contracts.evidence import CriterionResult
        verified = pending = 0
        for raw in pool["validation"].values():
            results = [CriterionResult.model_validate(v) for v in raw]
            if eligible(results, criteria):
                verified += 1
            elif not any(v.outcome == "fail" and v.criterion_id in hard for v in results):
                pending += 1
        return {"request":request.model_dump(mode="json"),"transport_request":transport_request,"response": native, "presentation": {
            "listings": [row for row in pool["rows"] if row["listing_id"] in ids],
            "counts": {"assessed": response.state.source_count, "eligible": verified, "unverified": pending, "excluded": response.state.source_count - verified - pending},
        }}
    finally:
        listing.close()
        geographic.close()
        if store:
            store.close()


app = FastAPI(title="Native Recommendation Agent", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/health")
def health():
    return {"status": "ok", "provider": "jiangshan-native", "upstream": "907cfa8", "controller": os.getenv("RECOMMENDATION_CONTROLLER", "live")}


async def run(request, app_view):
    # Native SQLite already serializes commits. Queue outside its transaction to avoid
    # spending the 30-second SQLite busy timeout while another model call is running.
    try:
        await asyncio.wait_for(_lock.acquire(), timeout=30)
    except TimeoutError:
        raise HTTPException(503, "Recommendation worker busy; retry the same request ID") from None
    try:
        # Shield the worker: losing an HTTP client must not release the lock mid-commit.
        task = asyncio.create_task(run_in_threadpool(execute, request, app_view))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    finally:
        _lock.release()


@app.post("/recommend", dependencies=[Depends(authorize)])
async def recommend(request: RecommendationRequest):
    return await run(request, False)


@app.post("/app/recommend", dependencies=[Depends(authorize)])
async def app_recommend(request: RecommendationRequest):
    return await run(request, True)
