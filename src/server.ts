import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { PostgresSaver } from "@langchain/langgraph-checkpoint-postgres";
import { PostgresAgentTaskStore } from "./agent-task-store.js";
import { createAgentRegistry } from "./agents/registry.js";
import { createMaster } from "./agents/master.js";
import { createModel } from "./model.js";
import { PostgresSessionEventStore } from "./session-store.js";
import { querySchema, runtimeEventSchema } from "./types.js";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required for PostgreSQL session storage");
const pool = new pg.Pool({ connectionString: databaseUrl });
const checkpointer = new PostgresSaver(pool);
const events = new PostgresSessionEventStore(pool);
const tasks = new PostgresAgentTaskStore(pool);
await checkpointer.setup();
await events.setup();
await tasks.setup();
const model = await createModel();
// Reuse one compiled master graph while the service is running.
const master = createMaster(model, createAgentRegistry(model, checkpointer), { checkpointer, events, tasks });
await master.resumePendingTasks();
const host = process.env.GRAPH_HOST || "127.0.0.1";
const port = Number(process.env.GRAPH_PORT || 3001);

const server = createServer(async (request, response) => {
  const send = (status: number, body: unknown) => {
    response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
    response.end(JSON.stringify(body));
  };

  if (request.method === "GET" && request.url === "/health") {
    try {
      await pool.query("SELECT 1");
      return send(200, { status: "ok" });
    } catch {
      return send(503, { detail: "PostgreSQL unavailable" });
    }
  }
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
  if (request.method === "GET" && url.pathname === "/events") {
    const sessionId = url.searchParams.get("session_id")?.trim();
    const lastId = request.headers["last-event-id"];
    const cursor = url.searchParams.get("after") ?? (Array.isArray(lastId) ? lastId[0] : lastId) ?? "0";
    const after = Number(cursor);
    if (!sessionId || sessionId.length > 128 || !Number.isSafeInteger(after) || after < 0) {
      return send(422, { detail: "Invalid session_id or event cursor" });
    }
    response.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    });
    const controller = new AbortController();
    response.on("close", () => controller.abort());
    try {
      for await (const { id, event } of master.subscribe(sessionId, after, controller.signal)) {
        if (!response.destroyed) response.write(`id: ${id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
      }
    } catch (error) {
      console.error("Session event subscription failed", error);
      if (!response.destroyed) {
        const event = { type: "error", detail: "Session event subscription failed" };
        response.write(`event: error\ndata: ${JSON.stringify(event)}\n\n`);
      }
    }
    if (!response.destroyed) response.end();
    return;
  }
  if (request.method !== "POST" || (url.pathname !== "/run" && url.pathname !== "/runtime/events")) {
    return send(404, { detail: "Not found" });
  }

  try {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    let body: unknown;
    try {
      body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch (error) {
      if (error instanceof SyntaxError) return send(400, { detail: "Invalid JSON" });
      throw error;
    }
    if (url.pathname === "/runtime/events") {
      const parsedEvent = runtimeEventSchema.safeParse(body);
      if (!parsedEvent.success) return send(422, { detail: parsedEvent.error.flatten() });
      const { session_id, name, payload } = parsedEvent.data;
      await master.acceptRuntimeEvent(session_id, { type: "runtime_event", name, payload });
      return send(202, { status: "accepted", session_id });
    }
    const parsed = querySchema.safeParse(body);
    if (!parsed.success) return send(422, { detail: parsed.error.flatten() });
    const sessionId = parsed.data.session_id ?? randomUUID();
    response.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    });
    for await (const event of master.stream(parsed.data.query, sessionId)) {
      if (!response.destroyed) {
        response.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
      }
    }
    if (!response.destroyed) response.end();
  } catch (error) {
    console.error(error);
    if (response.headersSent) {
      if (!response.destroyed) response.end();
      return;
    }
    return send(500, { detail: "Graph execution failed" });
  }
});

server.listen(port, host, () => console.log(`LangGraph service listening on ${host}:${port}`));
