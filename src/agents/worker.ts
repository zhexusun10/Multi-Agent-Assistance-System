import { AIMessage, HumanMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { Annotation, END, START, StateGraph } from "@langchain/langgraph";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import type { AgentId } from "../types.js";

export type AgentRunner = (prompt: string, sessionId: string, taskId: string) => Promise<string>;

const WorkerState = Annotation.Root({
  prompt: Annotation<string>(),
  taskId: Annotation<string>(),
  output: Annotation<string>(),
  lastTaskId: Annotation<string>(),
  messages: Annotation<Array<HumanMessage | AIMessage>>({
    reducer: (left, right) => left.concat(right),
    default: () => [],
  }),
});

// Shared mechanics only. Domain-specific tools and instructions belong in each agent module later.
export function createWorker(
  model: BaseChatModel, agent: AgentId, checkpointer: BaseCheckpointSaver,
): AgentRunner {
  const graph = new StateGraph(WorkerState)
    .addNode("run", async (state) => {
      // Retrying a task after a crash must not append its prompt twice.
      if (state.lastTaskId === state.taskId) return { output: state.output };
      const prompt = new HumanMessage(state.prompt);
      const response = await model.invoke([...state.messages, prompt]);
      if (!(response instanceof AIMessage)) throw new Error("Sub agent returned an invalid response");
      return {
        output: response.text,
        lastTaskId: state.taskId,
        messages: [prompt, response],
      };
    })
    .addEdge(START, "run")
    .addEdge("run", END)
    .compile({ checkpointer });

  return async (prompt, sessionId, taskId) => (await graph.invoke(
    { prompt, taskId },
    { configurable: { thread_id: JSON.stringify(["worker", sessionId, agent]) }, durability: "sync" },
  )).output;
}
