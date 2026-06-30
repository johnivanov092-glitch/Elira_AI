import { Brain, Code2, FileText, GitBranch, Globe, ListChecks, Monitor, Search, Server, TerminalSquare, Wrench, type LucideIcon } from "lucide-react";

/** Map a tool name to an existing lucide icon (no new icon set). */
export function toolIcon(tool: string): LucideIcon {
  if (tool === "web_search" || tool === "web_fetch" || tool === "browser") return Globe;
  if (tool === "sandbox_run") return Code2;
  if (tool === "run_bash") return TerminalSquare;
  if (tool === "read_file" || tool === "write_file" || tool === "edit_file") return FileText;
  if (tool === "glob" || tool === "grep" || tool === "tool_search") return Search;
  if (tool === "recall") return Brain;
  if (tool === "todo_update") return ListChecks;
  if (tool === "delegate_task") return GitBranch;
  if (tool === "ssh_run" || tool === "ssh_read" || tool === "ssh_write" || tool === "ssh_list_hosts") return Server;
  if (tool === "computer") return Monitor;
  return Wrench;
}
