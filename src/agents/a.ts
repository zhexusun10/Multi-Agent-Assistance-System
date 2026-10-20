import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import { createWorker, createRoleRunner } from "./worker.js";
import type { WorkflowService } from '../domain/workflow.js';

export const createAgentA = (model: BaseChatModel|undefined, checkpointer: BaseCheckpointSaver, service?:WorkflowService) =>
  service?createRoleRunner('a',service,checkpointer):createWorker(model!, "a", checkpointer);
