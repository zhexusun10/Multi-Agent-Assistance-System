import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import type { AgentId, Assignment } from "./types.js";

export type AgentTask = {
  id: string;
  sessionId: string;
  agent: AgentId;
  prompt: string;
  status: "queued" | "running" | "completed" | "failed";
  output: string | null;
  error: string | null;
};

export interface AgentTaskStore {
  enqueue(sessionId: string, assignments: Assignment[]): Promise<AgentTask[]>;
  requeueRunning(sessionId?: string, agent?: AgentId): Promise<void>;
  pendingPairs(): Promise<Array<{ sessionId: string; agent: AgentId }>>;
  activeAgents(sessionId: string): Promise<AgentId[]>;
  nextUndelivered(sessionId: string, agent: AgentId): Promise<AgentTask | null>;
  claimNext(sessionId: string, agent: AgentId): Promise<AgentTask | null>;
  finish(taskId: string, output: string | null, error: string | null): Promise<void>;
  markDelivered(taskId: string): Promise<void>;
  hasWork(sessionId: string, agent: AgentId): Promise<boolean>;
}

export class MemoryAgentTaskStore implements AgentTaskStore {
  private readonly tasks: Array<AgentTask & { delivered: boolean }> = [];

  async enqueue(sessionId: string, assignments: Assignment[]): Promise<AgentTask[]> {
    const tasks = assignments.map(({ agent, prompt }) => ({
      id: randomUUID(), sessionId, agent, prompt, status: "queued" as const,
      output: null, error: null, delivered: false,
    }));
    this.tasks.push(...tasks);
    return tasks;
  }

  async requeueRunning(sessionId?: string, agent?: AgentId): Promise<void> {
    for (const task of this.tasks) {
      if (task.status === "running" && (!sessionId || task.sessionId === sessionId) &&
        (!agent || task.agent === agent)) task.status = "queued";
    }
  }

  async pendingPairs(): Promise<Array<{ sessionId: string; agent: AgentId }>> {
    return [...new Map(this.tasks.filter((task) =>
      task.status === "queued" || (task.status !== "running" && !task.delivered),
    ).map((task) => [`${task.sessionId}\0${task.agent}`, {
      sessionId: task.sessionId, agent: task.agent,
    }])).values()];
  }

  async activeAgents(sessionId: string): Promise<AgentId[]> {
    return [...new Set(this.tasks.filter((task) =>
      task.sessionId === sessionId && (task.status === "queued" || task.status === "running"),
    ).map((task) => task.agent))];
  }

  async nextUndelivered(sessionId: string, agent: AgentId): Promise<AgentTask | null> {
    return this.tasks.find((task) => task.sessionId === sessionId && task.agent === agent &&
      (task.status === "completed" || task.status === "failed") && !task.delivered) ?? null;
  }

  async claimNext(sessionId: string, agent: AgentId): Promise<AgentTask | null> {
    const task = this.tasks.find((item) => item.sessionId === sessionId && item.agent === agent &&
      item.status === "queued");
    if (task) task.status = "running";
    return task ?? null;
  }

  async finish(taskId: string, output: string | null, error: string | null): Promise<void> {
    const task = this.tasks.find(({ id }) => id === taskId);
    if (!task) throw new Error("Agent task not found");
    task.status = error === null ? "completed" : "failed";
    task.output = output;
    task.error = error;
  }

  async markDelivered(taskId: string): Promise<void> {
    const task = this.tasks.find(({ id }) => id === taskId);
    if (!task) throw new Error("Agent task not found");
    task.delivered = true;
  }

  async hasWork(sessionId: string, agent: AgentId): Promise<boolean> {
    return this.tasks.some((task) => task.sessionId === sessionId && task.agent === agent &&
      (task.status === "queued" || (task.status !== "running" && !task.delivered)));
  }
}

type TaskRow = {
  id: string; session_id: string; agent: AgentId; prompt: string;
  status: AgentTask["status"]; output: string | null; error: string | null;
};

function toTask(row: TaskRow): AgentTask {
  return {
    id: row.id, sessionId: row.session_id, agent: row.agent, prompt: row.prompt,
    status: row.status, output: row.output, error: row.error,
  };
}

export class PostgresAgentTaskStore implements AgentTaskStore {
  constructor(private readonly pool: Pool) {}

  async setup(): Promise<void> {
    await this.pool.query(`
      CREATE TABLE IF NOT EXISTS agent_tasks (
        id UUID PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent TEXT NOT NULL CHECK (agent IN ('a', 'b', 'c', 'd')),
        prompt TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
        output TEXT,
        error TEXT,
        delivered_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    `);
    await this.pool.query(`
      CREATE INDEX IF NOT EXISTS agent_tasks_work_idx
      ON agent_tasks (session_id, agent, created_at, id)
      WHERE status = 'queued' OR delivered_at IS NULL
    `);
  }

  async enqueue(sessionId: string, assignments: Assignment[]): Promise<AgentTask[]> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const tasks: AgentTask[] = [];
      for (const { agent, prompt } of assignments) {
        const result = await client.query<TaskRow>(`
          INSERT INTO agent_tasks (id, session_id, agent, prompt, status)
          VALUES ($1, $2, $3, $4, 'queued') RETURNING *
        `, [randomUUID(), sessionId, agent, prompt]);
        tasks.push(toTask(result.rows[0]));
      }
      await client.query("COMMIT");
      return tasks;
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async requeueRunning(sessionId?: string, agent?: AgentId): Promise<void> {
    await this.pool.query(`
      UPDATE agent_tasks SET status = 'queued', updated_at = now()
      WHERE status = 'running' AND ($1::text IS NULL OR session_id = $1)
      AND ($2::text IS NULL OR agent = $2)
    `, [sessionId ?? null, agent ?? null]);
  }

  async pendingPairs(): Promise<Array<{ sessionId: string; agent: AgentId }>> {
    const result = await this.pool.query<{ session_id: string; agent: AgentId }>(`
      SELECT DISTINCT session_id, agent FROM agent_tasks
      WHERE status = 'queued' OR (status IN ('completed', 'failed') AND delivered_at IS NULL)
    `);
    return result.rows.map(({ session_id, agent }) => ({ sessionId: session_id, agent }));
  }

  async activeAgents(sessionId: string): Promise<AgentId[]> {
    const result = await this.pool.query<{ agent: AgentId }>(`
      SELECT DISTINCT agent FROM agent_tasks
      WHERE session_id = $1 AND status IN ('queued', 'running')
    `, [sessionId]);
    return result.rows.map(({ agent }) => agent);
  }

  async nextUndelivered(sessionId: string, agent: AgentId): Promise<AgentTask | null> {
    const result = await this.pool.query<TaskRow>(`
      SELECT * FROM agent_tasks WHERE session_id = $1 AND agent = $2
      AND status IN ('completed', 'failed') AND delivered_at IS NULL
      ORDER BY created_at, id LIMIT 1
    `, [sessionId, agent]);
    return result.rows[0] ? toTask(result.rows[0]) : null;
  }

  async claimNext(sessionId: string, agent: AgentId): Promise<AgentTask | null> {
    const result = await this.pool.query<TaskRow>(`
      UPDATE agent_tasks SET status = 'running', updated_at = now()
      WHERE id = (
        SELECT id FROM agent_tasks WHERE session_id = $1 AND agent = $2 AND status = 'queued'
        ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
      ) RETURNING *
    `, [sessionId, agent]);
    return result.rows[0] ? toTask(result.rows[0]) : null;
  }

  async finish(taskId: string, output: string | null, error: string | null): Promise<void> {
    await this.pool.query(`
      UPDATE agent_tasks SET status = $2, output = $3, error = $4, updated_at = now()
      WHERE id = $1
    `, [taskId, error === null ? "completed" : "failed", output, error]);
  }

  async markDelivered(taskId: string): Promise<void> {
    await this.pool.query("UPDATE agent_tasks SET delivered_at = now(), updated_at = now() WHERE id = $1", [taskId]);
  }

  async hasWork(sessionId: string, agent: AgentId): Promise<boolean> {
    const result = await this.pool.query(`
      SELECT 1 FROM agent_tasks WHERE session_id = $1 AND agent = $2
      AND (status = 'queued' OR (status IN ('completed', 'failed') AND delivered_at IS NULL)) LIMIT 1
    `, [sessionId, agent]);
    return (result.rowCount ?? 0) > 0;
  }
}
