import { initChatModel } from "langchain/chat_models/universal";

const DEFAULT_MODEL = "openai:gpt-4.1-mini";

export async function createModel(modelId = process.env.CHAT_MODEL?.trim() || DEFAULT_MODEL, taskOptions: {maxTokens?:number;temperature?:number} = {}) {
  const baseURL = process.env.OPENAI_BASE_URL?.trim();
  const options = modelId.startsWith("openai:") && baseURL
    ? { configuration: { baseURL }, ...(new URL(baseURL).hostname==='api.deepseek.com'?{temperature:0,maxTokens:4096,timeout:30000,maxRetries:1,modelKwargs:{thinking:{type:'disabled'}}}:{}) }
    : {};

  return initChatModel(modelId, {...options,...taskOptions});
}
