"""HTTP transport and app presentation proofs. Native service/algorithms stay intact."""
import asyncio
import hmac
import os
import json

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware

from .agent.controller import ScriptedController
from .app_controller import ApplicationLiveController
from .config import RecommendationConfig
from .contracts.request import RecommendationRequest
from .app_contract import ApplicationRecommendationRequest
from .models.candidate import Response
from .repositories.connections import ROOT, repositories
from .service import RecommendationService
from .state.store import StateStore
from .tracing.trace import digest

# Runtime selection chosen by the caller (state file, data mode, controller, settings file, trace
# mode) wins over the project file, so a process that set them before importing the app (tests,
# terminal subprocesses, ad-hoc scripts) never silently switches to the live state/database/model.
# The service token is a credential, not a runtime selection.
RUNTIME_SELECTION_PREFIX = "RECOMMENDATION_"
CREDENTIAL_KEYS = frozenset({"RECOMMENDATION_SERVICE_TOKEN"})


def load_project_environment(path=ROOT / ".env"):
    """The project file stays authoritative for provider/database credentials, as in src/server.ts."""
    for key, value in dotenv_values(path).items():
        if value is None:
            continue
        if key.startswith(RUNTIME_SELECTION_PREFIX) and key not in CREDENTIAL_KEYS and key in os.environ:
            continue
        os.environ[key] = value


load_project_environment()
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
        # behavior_context (main contract) is part of the request, so the transport body, its
        # fingerprint and the idempotency binding all cover it: a changed snapshot under the same
        # request ID is a 409.
        transport_request = request.model_dump(mode="json")
        if app_view:
            # App operations can be superseded after the native run commits but
            # before its response is adopted by the UI. Resolve the native v2 head at
            # the transport boundary, once per request ID, without changing native
            # optimistic checks, requirements, feedback or immutable replay. A state ID
            # that resolves only to a legacy contract-1.0 run (or to nothing) becomes
            # null: the request starts a fresh v2 chain instead of failing STALE_REFERENCE.
            store.db.execute("CREATE TABLE IF NOT EXISTS app_transport_requests_v2(conversation_id TEXT,request_id TEXT,input_hash TEXT,request TEXT,PRIMARY KEY(conversation_id,request_id))")
            store.db.commit()
            with store.transaction():
                binding = store.db.execute("SELECT input_hash,request FROM app_transport_requests_v2 WHERE conversation_id=? AND request_id=?", (request.conversation_id,request.request_id)).fetchone()
                fingerprint = digest(transport_request)
                if binding:
                    if binding[0] != fingerprint:
                        raise HTTPException(409, "IDEMPOTENCY_CONFLICT: app request ID is bound to another body")
                    request = RecommendationRequest.model_validate_json(binding[1])
                else:
                    # A pre-existing native cached response must retain its original
                    # request identity. Let native reserve/replay reject changed bodies.
                    cached = store.cached(request.conversation_id, request.request_id)
                    if not cached:
                        head = store.current(request.conversation_id)
                        request = request.model_copy(update={"previous_state_id": head["state_id"] if head else None})
                    store.db.execute("INSERT INTO app_transport_requests_v2 VALUES(?,?,?,?)", (request.conversation_id,request.request_id,fingerprint,request.model_dump_json()))
        controller_mode = os.getenv("RECOMMENDATION_CONTROLLER", "live")
        if controller_mode not in {"live", "scripted"}:
            raise ValueError("RECOMMENDATION_CONTROLLER must be live or scripted")
        # Committed recommendation/settings/recommendation.yaml unless RECOMMENDATION_CONFIG names an alternative.
        config = RecommendationConfig.from_file(os.getenv("RECOMMENDATION_CONFIG"))
        controller = ApplicationLiveController() if controller_mode == "live" else ScriptedController()
        service = RecommendationService(listing, geographic, store, controller, config=config)
        response = Response.model_validate(service.recommend(request).model_dump())
        native = response.model_dump(mode="json")
        if not app_view:
            return native
        if response.status == "error" or response.pool is None:
            return {"request":request.model_dump(mode="json"),"transport_request":transport_request,"response": native, "presentation": {"listings": [], "counts": None}}
        # Proofs come from the SAME immutable response, never a fresh or differently ranked search.
        pool = response.pool
        return {"request":request.model_dump(mode="json"),"transport_request":transport_request,"response": native, "presentation": {
            "listings": [item.listing.model_dump(mode="json") for item in response.recommendations],
            "counts": {"assessed": pool.source_count, "eligible": pool.size, "unverified": pool.excluded_hard_unknown, "excluded": pool.excluded_hard_fail},
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
    return {"status": "ok", "provider": "jiangshan-native", "upstream": "907cfa8", "contract": "2.0", "controller": os.getenv("RECOMMENDATION_CONTROLLER", "live")}


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
async def app_recommend(request: ApplicationRecommendationRequest):
    return await run(request.native_request(), True)
