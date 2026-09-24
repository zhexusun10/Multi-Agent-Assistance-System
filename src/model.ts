import { initChatModel } from "langchain/chat_models/universal";

const DEFAULT_MODEL = "openai:gpt-4.1-mini";

export async function createModel(modelId = process.env.CHAT_MODEL?.trim() || DEFAULT_MODEL) {
  const baseURL = process.env.OPENAI_BASE_URL?.trim();
  const options = modelId.startsWith("openai:") && baseURL
    ? { configuration: { baseURL } }
    : {};

  return initChatModel(modelId, options);
}
