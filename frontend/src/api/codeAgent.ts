import { request } from "./client";

export const DEFAULT_CODE_AGENT_MODEL = "qwen2.5-coder:7b";
export const DEFAULT_CODE_AGENT_MAX_STEPS = 20;

export type CodeAgentToolCall = {
  step: number;
  tool: string;
  arguments: Record<string, unknown>;
  result: string;
};

export type CodeAgentResponse = {
  ok: boolean;
  response: string;
  steps: number;
  tool_calls: CodeAgentToolCall[];
  stop_reason: "answer" | "max_steps" | "error";
  error: string | null;
};

export type CodeAgentRunArgs = {
  message: string;
  projectRoot: string;
  model?: string;
  maxSteps?: number;
};

export async function runCodeAgent({
  message,
  projectRoot,
  model = DEFAULT_CODE_AGENT_MODEL,
  maxSteps = DEFAULT_CODE_AGENT_MAX_STEPS,
}: CodeAgentRunArgs): Promise<CodeAgentResponse> {
  return request<CodeAgentResponse>("/api/code-agent/run", {
    method: "POST",
    body: {
      message,
      project_root: projectRoot,
      model,
      max_steps: maxSteps,
    },
  });
}
