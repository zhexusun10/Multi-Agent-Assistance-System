import json, sqlite3
from pathlib import Path
from contextlib import contextmanager
from recommendation.contracts.errors import RecommendationError


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
""")
        self.db.commit()

    @contextmanager
    def transaction(self):
        # Serialize read/modify/commit across processes; failed runs roll back event effects.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def reserve(self, request, payload_hash):
        row = self.db.execute(
            "SELECT payload_hash FROM request_identities WHERE conversation_id=? AND request_id=?",
            (request.conversation_id, request.request_id),
        ).fetchone()
        if row and row[0] != payload_hash:
            raise RecommendationError(
                "IDEMPOTENCY_CONFLICT", "Request ID already used with another payload"
            )
        self.db.execute(
            "INSERT OR IGNORE INTO request_identities VALUES(?,?,?)",
            (request.conversation_id, request.request_id, payload_hash),
        )

    def cached(self, conversation, request):
        row = self.db.execute(
            "SELECT payload_hash,response,snapshot FROM runs WHERE conversation_id=? AND request_id=?",
            (conversation, request),
        ).fetchone()
        return (row[0], json.loads(row[1]), json.loads(row[2])) if row else None

    def current(self, conversation):
        row = self.db.execute(
            "SELECT state_id,sequence FROM conversations WHERE id=?", (conversation,)
        ).fetchone()
        if not row:
            return None
        for (snapshot,) in self.db.execute(
            "SELECT snapshot FROM runs WHERE conversation_id=?", (conversation,)
        ):
            s = json.loads(snapshot)
            if s["state_id"] == row[0]:
                return s
        return None

    def commit(self, request, payload_hash, response, snapshot):
        self.db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?)",
            (
                request.conversation_id,
                request.request_id,
                payload_hash,
                response.model_dump_json(),
                json.dumps(snapshot),
            ),
        )
        self.db.execute(
            "INSERT INTO conversations VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state_id=excluded.state_id,sequence=excluded.sequence",
            (request.conversation_id, snapshot["state_id"], snapshot["sequence"]),
        )

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
        if reference == trace_id:
            return trace
        if reference == f"{trace_id}/pool" and "pool" in trace:
            return trace["pool"]
        for step in trace["steps"]:
            if step.get("snapshot_ref") == reference:
                return step["snapshot"]
            if step.get("context_ref") == reference:
                return {
                    "messages": step["messages"],
                    "tool_schemas": step["tool_schemas"],
                }
        raise RecommendationError(
            "STALE_REFERENCE", "Unknown immutable snapshot reference"
        )
