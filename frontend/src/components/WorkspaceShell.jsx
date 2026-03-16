import DesktopStatusBar from "./DesktopStatusBar";
import SupervisorView from "./SupervisorView";
import RunHistoryView from "./RunHistoryView";
import BackendControlPanel from "./BackendControlPanel";
import AutonomousDevPanel from "./AutonomousDevPanel";
import ProjectBrainPanel from "./ProjectBrainPanel";
import MultiAgentPanel from "./MultiAgentPanel";

export default function WorkspaceShell() {
  return (
    <div className="workspace-shell">
      <DesktopStatusBar />
      <div className="workspace-grid">
        <SupervisorView />
        <RunHistoryView />
        <BackendControlPanel />
        <AutonomousDevPanel />
        <ProjectBrainPanel />
        <MultiAgentPanel />
      </div>
    </div>
  );
}
