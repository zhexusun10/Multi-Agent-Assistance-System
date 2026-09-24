import type { Pool } from "pg";
import type { MasterEvent } from "./types.js";

export type SequencedEvent = { id: number; event: MasterEvent };

export interface SessionEventStore {
  append(sessionId: string, event: MasterEvent, key?: string): Promise<SequencedEvent>;
  list(sessionId: string, after: number): Promise<SequencedEvent[]>;
}

export class MemorySessionEventStore implements SessionEventStore {
  private readonly events = new Map<string, SequencedEvent[]>();
  private readonly keyed = new Map<string, SequencedEvent>();

  async append(sessionId: string, event: MasterEvent, key?: string): Promise<SequencedEvent> {
    const eventKey = key === undefined ? undefined : JSON.stringify([sessionId, key]);
    if (eventKey && this.keyed.has(eventKey)) return this.keyed.get(eventKey)!;
    const entries = this.events.get(sessionId) ?? [];
    const entry = { id: entries.length + 1, event };
    entries.push(entry);
    this.events.set(sessionId, entries);
    if (eventKey) this.keyed.set(eventKey, entry);
    return entry;
  }

  async list(sessionId: string, after: number): Promise<SequencedEvent[]> {
    return (this.events.get(sessionId) ?? []).filter(({ id }) => id > after);
  }
}

export class PostgresSessionEventStore implements SessionEventStore {
  constructor(private readonly pool: Pool) {}

  async setup(): Promise<void> {
    await this.pool.query(`
      CREATE TABLE IF NOT EXISTS session_event_counters (
        session_id TEXT PRIMARY KEY,
        last_event_id BIGINT NOT NULL
      )
    `);
    await this.pool.query(`
      CREATE TABLE IF NOT EXISTS session_events (
        session_id TEXT NOT NULL,
        event_id BIGINT NOT NULL,
        event JSONB NOT NULL,
        event_key TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (session_id, event_id)
      )
    `);
    await this.pool.query("ALTER TABLE session_events ADD COLUMN IF NOT EXISTS event_key TEXT");
    await this.pool.query(`
      CREATE UNIQUE INDEX IF NOT EXISTS session_events_key_idx
      ON session_events (session_id, event_key) WHERE event_key IS NOT NULL
    `);
  }

  async append(sessionId: string, event: MasterEvent, key?: string): Promise<SequencedEvent> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const counter = await client.query<{ last_event_id: string }>(`
        INSERT INTO session_event_counters (session_id, last_event_id)
        VALUES ($1, 1)
        ON CONFLICT (session_id) DO UPDATE
        SET last_event_id = session_event_counters.last_event_id + 1
        RETURNING last_event_id
      `, [sessionId]);
      const id = Number(counter.rows[0].last_event_id);
      if (!Number.isSafeInteger(id)) throw new Error("Session event cursor exceeds JavaScript's safe integer range");
      if (key !== undefined) {
        const existing = await client.query<{ event_id: string; event: MasterEvent }>(
          "SELECT event_id, event FROM session_events WHERE session_id = $1 AND event_key = $2",
          [sessionId, key],
        );
        if (existing.rows[0]) {
          await client.query("ROLLBACK");
          return { id: Number(existing.rows[0].event_id), event: existing.rows[0].event };
        }
      }
      await client.query(
        "INSERT INTO session_events (session_id, event_id, event, event_key) VALUES ($1, $2, $3::jsonb, $4)",
        [sessionId, id, JSON.stringify(event), key ?? null],
      );
      await client.query("COMMIT");
      return { id, event };
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async list(sessionId: string, after: number): Promise<SequencedEvent[]> {
    const result = await this.pool.query<{ event_id: string; event: MasterEvent }>(
      "SELECT event_id, event FROM session_events WHERE session_id = $1 AND event_id > $2 ORDER BY event_id",
      [sessionId, after],
    );
    return result.rows.map(({ event_id, event }) => ({ id: Number(event_id), event }));
  }
}
