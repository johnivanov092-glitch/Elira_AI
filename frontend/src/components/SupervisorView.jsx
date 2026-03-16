import { useEffect, useState } from "react";
import { bootstrapAgents, getSupervisorStatus, listAgents, runGoal, scheduleGoal } from "../api/supervisor";

export default function SupervisorView() {
  const [goal, setGoal] = useState("");
  const [status, setStatus] = useState(null);
  const [agents, setAgents] = useState([]);
  const [lastRun, setLastRun] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [statusData, agentsData] = await Promise.all([getSupervisorStatus(), listAgents()]);
      setStatus(statusData);
      setAgents(agentsData);
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
      await bootstrapAgents();
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleRunNow() {
    if (!goal.trim()) return;
    try {
      const result = await runGoal(goal.trim());
      setLastRun(result);
      await refresh();
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleSchedule() {
    if (!goal.trim()) return;
    try {
      await scheduleGoal(goal.trim(), 5);
      await refresh();
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <section className="workspace-card">
      <div className="section-header">
        <h2>Agent Supervisor</h2>
        <div className="actions-row">
          <button onClick={handleBootstrap}>Bootstrap Agents</button>
          <button onClick={refresh}>Refresh</button>
        </div>
      </div>

      <div className="goal-box">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Например: Проанализируй backend и предложи patch plan"
        />
        <button onClick={handleRunNow}>Run Goal</button>
        <button onClick={handleSchedule}>Schedule +5s</button>
      </div>

      {status ? (
        <div className="stats-grid">
          <div className="stat-card"><span>Agents</span><strong>{status.agents_count}</strong></div>
          <div className="stat-card"><span>Runs</span><strong>{status.runs_count}</strong></div>
          <div className="stat-card"><span>Jobs</span><strong>{status.jobs_count}</strong></div>
        </div>
      ) : null}

      <div className="list-block">
        <h3>Registered Agents</h3>
        <ul>
          {agents.map((agent) => (
            <li key={agent.id}>
              <strong>{agent.name}</strong> — {agent.role}
            </li>
          ))}
        </ul>
      </div>

      {lastRun ? (
        <div className="json-block">
          <h3>Last Run</h3>
          <pre>{JSON.stringify(lastRun, null, 2)}</pre>
        </div>
      ) : null}

      {error ? <div className="panel-error">{error}</div> : null}
    </section>
  );
}
