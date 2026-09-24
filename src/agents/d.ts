import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { createWorker } from "./worker.js";

export const createAgentD = (model: BaseChatModel) => createWorker(model);
