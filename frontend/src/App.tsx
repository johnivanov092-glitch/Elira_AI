import WorkspaceShell from "./workspace/WorkspaceShell";
import ToastHost from "./components/ToastHost";
import ProactiveSuggestions from "./workspace/ProactiveSuggestions";
import "./styles.css";

export default function App() {
  return (
    <>
      <WorkspaceShell />
      <ToastHost />
      <ProactiveSuggestions />
    </>
  );
}

