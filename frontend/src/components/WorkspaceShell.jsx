import DesktopStatusBar from "./DesktopStatusBar";
import SupervisorView from "./SupervisorView";
import RunHistoryView from "./RunHistoryView";
import BackendControlPanel from "./BackendControlPanel";
import AutonomousDevPanel from "./AutonomousDevPanel";
import ProjectBrainPanel from "./ProjectBrainPanel";
import MultiAgentPanel from "./MultiAgentPanel";
import Phase10Panel from "./Phase10Panel";
import Phase11Panel from "./Phase11Panel";
import Phase12Panel from "./Phase12Panel";

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
        <Phase10Panel />
        <Phase11Panel />
        <Phase12Panel />
      </div>
    </div>
  );
}
