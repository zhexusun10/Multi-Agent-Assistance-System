import json, sqlite3
from pathlib import Path
from contextlib import contextmanager
from recommendation.contracts.errors import RecommendationError
from .pool_cache import ensure_schema as ensure_pool_schema, retained as retained_pool
from ..tracing import slim

# Contract 2.0 runs live in the *_v2 tables. The legacy (contract 1.0) tables stay untouched
# and readable (legacy_* methods); v2 ignores legacy heads, so a v2 request in a conversation
# with only legacy runs starts a fresh v2 chain. Traces are shared: v2 traces carry
# "format": 2, legacy rows lack it. Pool contents live in pool_cache / pool_cache_index
# (state/pool_cache.py); run snapshots only reference them by pool_version.
TRACE_FORMAT = 2
SNAPSHOT_FORMAT = 2


class StateStore:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
CREATE TABLE IF NOT EXISTS request_identities(conversation_id TEXT, request_id TEXT, payload_hash TEXT, PRIMARY KEY(conversation_id,request_id));
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, state_id TEXT, sequence INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs(conversation_id TEXT,request_id TEXT,payload_hash TEXT,response TEXT,snapshot TEXT,PRIMARY KEY(conversation_id,request_id));
CREATE TABLE IF NOT EXISTS traces(conversation_id TEXT,trace_id TEXT,request_id TEXT,content TEXT,PRIMARY KEY(conversation_id,trace_id));
CREATE TABLE IF NOT EXISTS request_identities_v2(conversation_id TEXT, request_id TEXT, payload_hash TEXT NOT NULL, PRIMARY KEY(conversation_id,request_id));
CREATE TABLE IF NOT EXISTS conversations_v2(id TEXT PRIMARY KEY, state_id TEXT NOT NULL, sequence INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs_v2(conversation_id TEXT, request_id TEXT, state_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload_hash TEXT NOT NULL, response TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(conversation_id,request_id));
CREATE UNIQUE INDEX IF NOT EXISTS runs_v2_state ON runs_v2(conversation_id, state_id);
""")
        ensure_pool_schema(self.db)
        self.db.commit()
        self._after_commit = None  # callbacks of the open transaction (None: no transaction)

    @contextmanager
    def transaction(self):
        # Serialize read/modify/commit across processes; failed runs roll back their effects.
        self.db.execute("BEGIN IMMEDIATE")
        self._after_commit = []
        try:
            yield
            self.db.commit()
        except BaseException:
            self._after_commit = None
            self.db.rollback()
            raise
        callbacks, self._after_commit = self._after_commit, None
        for callback in callbacks:
            callback()

    def after_commit(self, callback):
        """Run ``callback`` once the open transaction commits (never after a rollback)."""
        if self._after_commit is None:
            callback()
        else:
            self._after_commit.append(callback)

    def reserve(self, request, payload_hash):
        row = self.db.execute(
            "SELECT payload_hash FROM request_identities_v2 WHERE conversation_id=? AND request_id=?",
            (request.conversation_id, request.request_id),
        ).fetchone()
        if row and row[0] != payload_hash:
            raise RecommendationError(
                "IDEMPOTENCY_CONFLICT", "Request ID already used with another payload"
            )
        self.db.execute(
            "INSERT OR IGNORE INTO request_identities_v2 VALUES(?,?,?)",
            (request.conversation_id, request.request_id, payload_hash),
        )

    def cached(self, conversation, request):
        row = self.db.execute(
            "SELECT payload_hash,response,snapshot FROM runs_v2 WHERE conversation_id=? AND request_id=?",
            (conversation, request),
        ).fetchone()
        return (row[0], json.loads(row[1]), json.loads(row[2])) if row else None

    def current(self, conversation):
        """Snapshot of the conversation's v2 head, by direct state-id lookup."""
        row = self.db.execute(
            "SELECT r.snapshot FROM conversations_v2 c JOIN runs_v2 r "
            "ON r.conversation_id=c.id AND r.state_id=c.state_id WHERE c.id=?",
            (conversation,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def commit(self, request, payload_hash, response, snapshot):
        self.db.execute(
            "INSERT INTO runs_v2 VALUES(?,?,?,?,?,?,?)",
            (
                request.conversation_id,
                request.request_id,
                snapshot["state_id"],
                snapshot["sequence"],
                payload_hash,
                response.model_dump_json(),
                json.dumps(snapshot),
            ),
        )
        self.db.execute(
            "INSERT INTO conversations_v2 VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state_id=excluded.state_id,sequence=excluded.sequence",
            (request.conversation_id, snapshot["state_id"], snapshot["sequence"]),
        )

    # --- legacy (contract 1.0) readers; never written by v2 ---------------------------------
    def legacy_cached(self, conversation, request):
        row = self.db.execute(
            "SELECT payload_hash,response,snapshot FROM runs WHERE conversation_id=? AND request_id=?",
            (conversation, request),
        ).fetchone()
        return (row[0], json.loads(row[1]), json.loads(row[2])) if row else None

    def legacy_head(self, conversation):
        """(state_id, sequence) of a legacy v1 conversation head, or None."""
        row = self.db.execute(
            "SELECT state_id,sequence FROM conversations WHERE id=?", (conversation,)
        ).fetchone()
        return tuple(row) if row else None

    def save_trace(self, conversation, trace_id, request_id, trace):
        self.db.execute(
            "INSERT INTO traces VALUES(?,?,?,?)",
            (conversation, trace_id, request_id, json.dumps(trace)),
        )

    def trace(self, conversation, request_id=None):
        row = self.db.execute(
            "SELECT content FROM traces WHERE conversation_id=? "
            + ("AND request_id=? " if request_id else "")
            + "ORDER BY rowid DESC LIMIT 1",
            (conversation, request_id) if request_id else (conversation,),
        ).fetchone()
        if not row:
            raise RecommendationError(
                "STALE_REFERENCE", "Trace not found in this conversation"
            )
        return json.loads(row[0])

    def close(self):
        self.db.close()

    def resolve_reference(self, conversation, reference):
        """Resolve only immutable content belonging to the supplied conversation."""
        reference = reference.removeprefix("sqlite:")
        trace_id = reference.split("/")[0]
        row = self.db.execute(
            "SELECT content FROM traces WHERE conversation_id=? AND trace_id=?",
            (conversation, trace_id),
        ).fetchone()
        if not row:
            raise RecommendationError(
                "STALE_REFERENCE", "Reference not found in this conversation"
            )
        trace = json.loads(row[0])
        legacy = is_legacy_trace(trace)
        if reference == trace_id:
            found = trace
        elif reference == f"{trace_id}/pool":
            found = self.resolve_pool(conversation, trace)
        else:
            # Slim v2 traces (P6): snapshot_ref -> the compact tool step record, context_ref -> the
            # reconstructed model messages, '<trace>/messages' -> the whole transcript. Older v2
            # and legacy traces return their stored step snapshot / messages (tracing/slim.py).
            found = slim.resolve_step(trace, reference)
        if found is None:
            raise RecommendationError(
                "STALE_REFERENCE", "Unknown immutable snapshot reference"
            )
        if legacy and isinstance(found, dict):
            return {**found, "legacy_note": slim.LEGACY_NOTE}
        return found

    def resolve_pool(self, conversation, trace):
        """Pool content of a trace: from pool_cache by pool_version while retained (spec R13)."""
        ref = trace.get("pool_ref")
        if ref is None:
            if "pool" in trace:  # P2-era and legacy traces embedded the pool snapshot
                return trace["pool"]
            raise RecommendationError("STALE_REFERENCE", "This trace has no exploration pool")
        if not ref.get("persisted", True):
            raise RecommendationError(
                "STALE_REFERENCE", "Pool was not persisted (the run failed and rolled back)"
            )
        content = retained_pool(self.db, conversation, ref["pool_version"])
        if content is None:
            raise RecommendationError(
                "STALE_REFERENCE", "Pool evicted (cache.pool_retention_per_conversation)"
            )
        return {**content, "pool_ref": ref}


def is_legacy_trace(trace):
    return trace.get("format") != TRACE_FORMAT
