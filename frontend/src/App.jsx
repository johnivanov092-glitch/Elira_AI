import WorkspaceShell from "./components/WorkspaceShell";
import "./styles.css";

export default function App() {
  return (
    <main className="app-root">
      <header className="app-header">
        <div>
          <h1>Jarvis Work</h1>
          <p>AI Workspace / Desktop Control Center</p>
        </div>
      </header>
      <WorkspaceShell />
    </main>
  );
}
