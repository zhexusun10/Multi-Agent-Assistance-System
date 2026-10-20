import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import { createWorker, createRoleRunner } from "./worker.js";
import type { WorkflowService } from '../domain/workflow.js';

export const createAgentD = (model: BaseChatModel|undefined, checkpointer: BaseCheckpointSaver, service?:WorkflowService) =>
  service?createRoleRunner('d',service,checkpointer):createWorker(model!, "d", checkpointer);
