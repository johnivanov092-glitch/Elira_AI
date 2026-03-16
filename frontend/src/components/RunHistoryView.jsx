import { useEffect, useState } from "react";
import { listRuns, getRun, deleteRun } from "../api/run_history";

export default function RunHistoryView() {
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const data = await listRuns(30);
      setRuns(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleSelect(runId) {
    try {
      const data = await getRun(runId);
      setSelectedRun(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleDelete(runId) {
    try {
      await deleteRun(runId);
      if (selectedRun?.id === runId) setSelectedRun(null);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <section className="workspace-card">
      <div className="section-header">
        <h2>Run History</h2>
        <button onClick={refresh}>Refresh</button>
      </div>

      <div className="run-history-layout">
        <div className="run-list">
          {runs.map((run) => (
            <div key={run.id} className="run-item">
              <button className="run-item-main" onClick={() => handleSelect(run.id)}>
                <strong>{run.goal}</strong>
                <span>{run.status}</span>
              </button>
              <button className="danger" onClick={() => handleDelete(run.id)}>Delete</button>
            </div>
          ))}
        </div>

        <div className="run-details">
          {selectedRun ? <pre>{JSON.stringify(selectedRun, null, 2)}</pre> : <div>Select a run</div>}
        </div>
      </div>

      {error ? <div className="panel-error">{error}</div> : null}
    </section>
  );
}
