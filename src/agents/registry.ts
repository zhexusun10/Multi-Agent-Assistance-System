import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import type { AgentId } from "../types.js";
import type { AgentRunner } from "./worker.js";
import { createAgentA } from "./a.js";
import { createAgentB } from "./b.js";
import { createAgentC } from "./c.js";
import { createAgentD } from "./d.js";

export function createAgentRegistry(model: BaseChatModel, checkpointer: BaseCheckpointSaver) {
  return {
    a: createAgentA(model, checkpointer),
    b: createAgentB(model, checkpointer),
    c: createAgentC(model, checkpointer),
    d: createAgentD(model, checkpointer),
  } satisfies Record<AgentId, AgentRunner>;
}

export type AgentRegistry = Record<AgentId, AgentRunner>;
