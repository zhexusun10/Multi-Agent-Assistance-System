import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { createAgentRegistry } from "./agents/registry.js";
import { createMaster } from "./agents/master.js";
import { createModel } from "./model.js";
import { querySchema } from "./types.js";

const model = await createModel();
// Reuse one compiled master graph while the service is running.
const master = createMaster(model, createAgentRegistry(model));
const host = process.env.GRAPH_HOST || "127.0.0.1";
const port = Number(process.env.GRAPH_PORT || 3001);

const server = createServer(async (request, response) => {
  const send = (status: number, body: unknown) => {
    response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
    response.end(JSON.stringify(body));
  };

  if (request.method === "GET" && request.url === "/health") {
    return send(200, { status: "ok" });
  }
  if (request.method !== "POST" || request.url !== "/run") {
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
