import { useEffect, useState } from "react";
import {
  getPhase12Status,
  listExecutions,
  getExecution,
  startExecution,
} from "../api/phase12";

export default function Phase12Panel() {
  const [status, setStatus] = useState(null);
  const [executions, setExecutions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState("autonomous_dev");
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [s, e] = await Promise.all([
        getPhase12Status(),
        listExecutions(20),
      ]);
      setStatus(s);
      setExecutions(e);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleStart() {
    if (!goal.trim()) return;
    try {
      const data = await startExecution(goal.trim(), mode, {});
      setSelected(data);
      await refresh();
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleSelect(executionId) {
    try {
      const data = await getExecution(executionId);
      setSelected(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <section className="workspace-card">
      <div className="section-header">
        <h2>Phase 12 — Execution History / Trace Viewer</h2>
        <button onClick={refresh}>Refresh</button>
      </div>

      <div className="goal-box">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Например: Усиль patch pipeline и проверь execution trace"
        />
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          style={{ padding: 10, borderRadius: 10, background: "#0f1115", color: "#e8ecf1", border: "1px solid #394356" }}
        >
          <option value="autonomous_dev">autonomous_dev</option>
          <option value="multi_agent">multi_agent</option>
          <option value="project_brain">project_brain</option>
        </select>
        <button onClick={handleStart}>Start Execution</button>
      </div>

      {status ? (
        <div className="json-block">
          <h3>Status</h3>
          <pre>{JSON.stringify(status, null, 2)}</pre>
        </div>
      ) : null}

      <div className="run-history-layout">
        <div className="run-list">
          {executions.map((item) => (
            <div key={item.id} className="run-item">
              <button className="run-item-main" onClick={() => handleSelect(item.id)}>
                <strong>{item.goal}</strong>
                <span>{item.status}</span>
              </button>
            </div>
          ))}
        </div>
        <div className="run-details">
          {selected ? <pre>{JSON.stringify(selected, null, 2)}</pre> : <div>Select an execution</div>}
        </div>
      </div>

      {error ? <div className="panel-error">{error}</div> : null}
    </section>
  );
}
