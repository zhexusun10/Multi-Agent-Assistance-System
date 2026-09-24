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

export const querySchema = z.object({
  query: z.string().trim().min(1),
  session_id: z.string().trim().min(1).max(128).optional(),
});

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

export type MasterEvent =
  | { type: "session"; session_id: string }
  | { type: "master_answer"; answer: string }
  | { type: "agent_result"; result: AgentResult }
  | { type: "done"; session_id: string; answer: string; agents: AgentResult[] }
  | { type: "error"; detail: string };
