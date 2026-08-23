import WorkspaceShell from "./workspace/WorkspaceShell";
import ToastHost from "./components/ToastHost";
import "./styles.css";

export default function App() {
  return (
    <>
      <WorkspaceShell />
      <ToastHost />
    </>
  );
}
