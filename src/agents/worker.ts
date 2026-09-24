import { HumanMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { Annotation, END, START, StateGraph } from "@langchain/langgraph";

export type AgentRunner = (prompt: string) => Promise<string>;

const WorkerState = Annotation.Root({
  prompt: Annotation<string>(),
  output: Annotation<string>(),
});

// Shared mechanics only. Domain-specific tools and instructions belong in each agent module later.
export function createWorker(model: BaseChatModel) {
  const graph = new StateGraph(WorkerState)
    .addNode("run", async (state) => {
      const response = await model.invoke([new HumanMessage(state.prompt)]);
      return { output: response.text };
    })
    .addEdge(START, "run")
    .addEdge("run", END)
    .compile();

  return async (prompt: string) => (await graph.invoke({ prompt })).output;
}
