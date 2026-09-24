import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import { createWorker } from "./worker.js";

export const createAgentB = (model: BaseChatModel, checkpointer: BaseCheckpointSaver) =>
  createWorker(model, "b", checkpointer);
