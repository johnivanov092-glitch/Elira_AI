import { useEffect, useState } from "react";
import {
  getMultiAgentStatus,
  listMultiAgents,
  bootstrapMultiAgents,
  runMultiAgent,
  listMultiAgentRuns,
} from "../api/multi_agent";

export default function MultiAgentPanel() {
  const [goal, setGoal] = useState("");
  const [status, setStatus] = useState(null);
  const [agents, setAgents] = useState([]);
  const [runs, setRuns] = useState([]);
  const [result, setResult] = useState(null);
  const [autoApply, setAutoApply] = useState(false);
  const [runChecks, setRunChecks] = useState(true);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [s, a, r] = await Promise.all([
        getMultiAgentStatus(),
        listMultiAgents(),
        listMultiAgentRuns(10),
      ]);
      setStatus(s);
      setAgents(a);
      setRuns(r);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleBootstrap() {
    try {
      await bootstrapMultiAgents();
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleRun() {
    if (!goal.trim()) return;
    try {
      const data = await runMultiAgent(goal.trim(), {
        auto_apply: autoApply,
        run_checks: runChecks,
      });
      setResult(data);
      await refresh();
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <section className="workspace-card">
      <div className="section-header">
        <h2>Multi-Agent Dev System</h2>
        <div className="actions-row">
          <button onClick={handleBootstrap}>Bootstrap</button>
          <button onClick={refresh}>Refresh</button>
        </div>
      </div>

      <div className="goal-box">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Например: Раздели patch систему на preview/apply/rollback pipeline"
        />
        <button onClick={handleRun}>Run Multi-Agent</button>
      </div>

      <div className="actions-row" style={{ justifyContent: "flex-start", marginBottom: 12 }}>
        <label><input type="checkbox" checked={autoApply} onChange={(e) => setAutoApply(e.target.checked)} /> auto apply</label>
        <label><input type="checkbox" checked={runChecks} onChange={(e) => setRunChecks(e.target.checked)} /> run checks</label>
      </div>

      {status ? (
        <div className="json-block">
          <h3>Status</h3>
          <pre>{JSON.stringify(status, null, 2)}</pre>
        </div>
      ) : null}

      <div className="json-block">
        <h3>Agents</h3>
        <pre>{JSON.stringify(agents, null, 2)}</pre>
      </div>

      <div className="json-block">
        <h3>Recent Runs</h3>
        <pre>{JSON.stringify(runs, null, 2)}</pre>
      </div>

      {result ? (
        <div className="json-block">
          <h3>Last Result</h3>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </div>
      ) : null}

      {error ? <div className="panel-error">{error}</div> : null}
    </section>
  );
}
