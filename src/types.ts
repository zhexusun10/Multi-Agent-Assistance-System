import { z } from "zod";

export const agentIdSchema = z.enum(["a", "b", "c", "d"]);
export type AgentId = z.infer<typeof agentIdSchema>;

export const assignmentSchema = z.object({
  agent: agentIdSchema,
  prompt: z.string().trim().min(1),
});
export type Assignment = z.infer<typeof assignmentSchema>;

export const spawnInputSchema = z.object({
  assignments: z.array(assignmentSchema).min(1).max(4).refine(
    (items) => new Set(items.map((item) => item.agent)).size === items.length,
    "Each agent can only be selected once per spawn call",
  ),
});

export const querySchema = z.object({ query: z.string().trim().min(1) });

export interface AgentResult {
  agent: AgentId;
  prompt: string;
  output: string;
}

export const agentResultsSchema = z.array(z.object({
  agent: agentIdSchema,
  prompt: z.string(),
  output: z.string(),
}));

export interface QueryResult {
  answer: string;
  agents: AgentResult[];
}
