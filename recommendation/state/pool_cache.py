"""Exploration-pool cache (spec section 5, R4, R13): SQLite tables plus a per-process LRU.

Two SQLite tables in the run state file:

- ``pool_cache``: pool CONTENT by (conversation_id, pool_version), with the pool_key and
  data_version it was built for. Retention: the ``cache.pool_retention_per_conversation`` most
  recently USED contents per conversation (a hit counts as a use, so the pools of the latest
  runs stay inspectable), never one an index entry still points to. Contents serve
  ``<trace>/pool`` while retained (afterwards STALE_REFERENCE "pool evicted").
- ``pool_cache_index``: lookup (conversation_id, pool_key, data_version) -> pool_version, the
  ``cache.max_entries_per_conversation`` NEWEST (most recently built) per conversation, as
  spec section 5 says. A hit does not refresh an index entry; its ``used_seq`` column holds
  the build sequence.

The two bounds stay separate (P1 open item, decided in P3): the index bounds which pools a new
request may REUSE, retention bounds how long older runs' pools stay INSPECTABLE. The loader
requires retention >= max entries, so an index entry never outlives its content.

TTL: ``cache.ttl_seconds`` 0 means no TTL (entries are invalidated only by pool_key and
data_version); a positive value makes an index entry older than that (since it was built) a
miss, and the rebuild replaces it.

The tables are created by ``StateStore.__init__`` (``ensure_schema``). ``PoolCache`` never runs
DDL: ``executescript`` commits implicitly, and a ``PoolCache`` is built inside the run's
``BEGIN IMMEDIATE`` transaction, so DDL there would release the cross-process write lock.

Rows are written inside the caller's run transaction, so a failed run rolls them back. The
per-process LRU (bounded by ``cache.memory_max_entries``) is content-addressed by
(conversation_id, pool_version) and consulted only after the SQLite index confirmed the entry,
so it can never serve a pool the index does not hold; it is filled only after a commit.
Different conversations never share entries (the conversation is part of every key).
"""

import json, threading, time
from collections import OrderedDict

SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_cache(conversation_id TEXT NOT NULL, pool_version TEXT NOT NULL, pool_key TEXT NOT NULL, data_version TEXT NOT NULL, created_at REAL NOT NULL, used_seq INTEGER NOT NULL, content TEXT NOT NULL, PRIMARY KEY(conversation_id, pool_version));
CREATE TABLE IF NOT EXISTS pool_cache_index(conversation_id TEXT NOT NULL, pool_key TEXT NOT NULL, data_version TEXT NOT NULL, pool_version TEXT NOT NULL, created_at REAL NOT NULL, used_seq INTEGER NOT NULL, PRIMARY KEY(conversation_id, pool_key, data_version));
"""


def ensure_schema(db):
    db.executescript(SCHEMA)


def retained(db, conversation_id, pool_version):
    """Stored pool content of this conversation, or None when evicted/never persisted."""
    row = db.execute(
        "SELECT content FROM pool_cache WHERE conversation_id=? AND pool_version=?",
        (conversation_id, pool_version),
    ).fetchone()
    return json.loads(row[0]) if row else None


class PoolMemory:
    """Bounded LRU of parsed pool contents, keyed by (conversation_id, pool_version)."""

    def __init__(self):
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            value = self.entries.get(key)
            if value is not None:
                self.entries.move_to_end(key)
            return value

    def put(self, key, value, limit):
        with self.lock:
            self.entries[key] = value
            self.entries.move_to_end(key)
            while len(self.entries) > limit:
                self.entries.popitem(last=False)

    def clear(self):
        with self.lock:
            self.entries.clear()

    def __len__(self):
        return len(self.entries)


MEMORY = PoolMemory()  # module level: survives the per-request repositories/stores of app.py


class PoolCache:
    def __init__(self, store, settings, memory=MEMORY, clock=None, parse=None):
        self.store = store
        self.db = store.db
        self.settings = settings.cache
        self.memory = memory
        self.clock = clock or (lambda: time.time())
        self.parse = parse or (lambda content: None)
        # No ensure_schema here: StateStore created the tables, and executescript() would COMMIT
        # the caller's BEGIN IMMEDIATE run transaction (releasing the cross-process lock).

    @property
    def enabled(self):
        return self.settings.enabled

    def _next_seq(self, conversation_id):
        row = self.db.execute(
            "SELECT max(s) FROM (SELECT max(used_seq) AS s FROM pool_cache WHERE conversation_id=? "
            "UNION ALL SELECT max(used_seq) FROM pool_cache_index WHERE conversation_id=?)",
            (conversation_id, conversation_id),
        ).fetchone()
        return (row[0] or 0) + 1

    def load(self, conversation_id, pool_version):
        """(content, parsed rows, source) of a retained pool, or None."""
        key = (conversation_id, pool_version)
        cached = self.memory.get(key)
        if cached is not None:
            return cached[0], cached[1], "memory"
        content = retained(self.db, conversation_id, pool_version)
        if content is None:
            return None
        parsed = self.parse(content)
        self._remember(key, (content, parsed))
        return content, parsed, "sqlite"

    def lookup(self, conversation_id, pool_key, data_version):
        """{"pool_version", "created_at", "content", "rows", "source"} on a hit, plus the outcome."""
        row = self.db.execute(
            "SELECT pool_version, created_at FROM pool_cache_index WHERE conversation_id=? AND pool_key=? AND data_version=?",
            (conversation_id, pool_key, data_version),
        ).fetchone()
        if row is None:
            return None, "miss"
        pool_version, created_at = row
        ttl = self.settings.ttl_seconds
        if ttl and self.clock() - created_at >= ttl:
            return None, "expired"
        loaded = self.load(conversation_id, pool_version)
        if loaded is None:
            return None, "content_missing"
        # A hit refreshes content retention only; the index keeps the newest BUILT entries.
        seq = self._next_seq(conversation_id)
        self.db.execute(
            "UPDATE pool_cache SET used_seq=? WHERE conversation_id=? AND pool_version=?",
            (seq, conversation_id, pool_version),
        )
        content, rows, source = loaded
        return {
            "pool_version": pool_version, "created_at": created_at, "content": content,
            "rows": rows, "source": source,
        }, "hit"

    def save(self, conversation_id, pool_key, data_version, pool_version, content, rows=None, index=True):
        """Store pool content (always) and its index entry (when ``index``); returns created_at."""
        created_at = self.clock()
        seq = self._next_seq(conversation_id)
        self.db.execute(
            "INSERT OR REPLACE INTO pool_cache VALUES(?,?,?,?,?,?,?)",
            (conversation_id, pool_version, pool_key, data_version, created_at, seq,
             json.dumps(content, sort_keys=True, allow_nan=False)),
        )
        if index:
            self.db.execute(
                "INSERT OR REPLACE INTO pool_cache_index VALUES(?,?,?,?,?,?)",
                (conversation_id, pool_key, data_version, pool_version, created_at, seq),
            )
        self.evict(conversation_id)
        self._remember((conversation_id, pool_version), (content, rows if rows is not None else self.parse(content)))
        return created_at

    def _remember(self, key, value):
        """Fill the LRU only once the run transaction commits (never after a rollback)."""
        limit = self.settings.memory_max_entries
        after_commit = getattr(self.store, "after_commit", None)
        if after_commit:
            after_commit(lambda: self.memory.put(key, value, limit))
        else:
            self.memory.put(key, value, limit)

    def evict(self, conversation_id):
        # Index: keep the newest built entries (used_seq = build sequence, never bumped by a hit).
        self.db.execute(
            "DELETE FROM pool_cache_index WHERE conversation_id=? AND rowid NOT IN "
            "(SELECT rowid FROM pool_cache_index WHERE conversation_id=? ORDER BY used_seq DESC LIMIT ?)",
            (conversation_id, conversation_id, self.settings.max_entries_per_conversation),
        )
        self.db.execute(
            "DELETE FROM pool_cache WHERE conversation_id=? AND rowid NOT IN "
            "(SELECT rowid FROM pool_cache WHERE conversation_id=? ORDER BY used_seq DESC LIMIT ?) "
            "AND pool_version NOT IN (SELECT pool_version FROM pool_cache_index WHERE conversation_id=?)",
            (conversation_id, conversation_id, self.settings.pool_retention_per_conversation, conversation_id),
        )

    # Inspection helpers (terminal / tests).
    def index_entries(self, conversation_id):
        return [
            dict(zip(("pool_key", "data_version", "pool_version", "created_at"), row))
            for row in self.db.execute(
                "SELECT pool_key, data_version, pool_version, created_at FROM pool_cache_index "
                "WHERE conversation_id=? ORDER BY used_seq DESC",
                (conversation_id,),
            )
        ]

    def retained_versions(self, conversation_id):
        return [
            row[0]
            for row in self.db.execute(
                "SELECT pool_version FROM pool_cache WHERE conversation_id=? ORDER BY used_seq DESC",
                (conversation_id,),
            )
        ]
