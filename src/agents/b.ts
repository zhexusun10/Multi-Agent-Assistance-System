import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import { createWorker, createRoleRunner } from "./worker.js";
import type { WorkflowService } from '../domain/workflow.js';

// Business mode delegates to the Recommendation Contract gateway, never a second free-form ranker.
export const createAgentB = (model: BaseChatModel|undefined, checkpointer: BaseCheckpointSaver, service?:WorkflowService) =>
  service?createRoleRunner('b',service,checkpointer):createWorker(model!, "b", checkpointer);
