import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { AgentId } from "../types.js";
import type { AgentRunner } from "./worker.js";
import { createAgentA } from "./a.js";
import { createAgentB } from "./b.js";
import { createAgentC } from "./c.js";
import { createAgentD } from "./d.js";

export function createAgentRegistry(model: BaseChatModel) {
  return {
    a: createAgentA(model),
    b: createAgentB(model),
    c: createAgentC(model),
    d: createAgentD(model),
  } satisfies Record<AgentId, AgentRunner>;
}

export type AgentRegistry = Record<AgentId, AgentRunner>;
