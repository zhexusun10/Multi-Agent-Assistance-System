import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import type { AgentId } from "../types.js";
import type { AgentRunner } from "./worker.js";
import { createRoleRunner } from './worker.js';
import type { WorkflowService } from '../domain/workflow.js';
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

export function createDomainRegistry(service:WorkflowService,checkpointer:BaseCheckpointSaver):AgentRegistry{
  return {a:createAgentA(undefined,checkpointer,service),b:createAgentB(undefined,checkpointer,service),c:createAgentC(undefined,checkpointer,service),d:createAgentD(undefined,checkpointer,service)};
}
