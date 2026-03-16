import { useEffect, useState } from "react";
import { getPhase10Status, runResearchPipeline, runBrowserRuntime } from "../api/phase10";

export default function Phase10Panel() {
  const [status, setStatus] = useState(null);
  const [query, setQuery] = useState("");
  const [url, setUrl] = useState("");
  const [docMode, setDocMode] = useState(false);
  const [researchResult, setResearchResult] = useState(null);
  const [browserResult, setBrowserResult] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const data = await getPhase10Status();
      setStatus(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleResearch() {
    if (!query.trim()) return;
    try {
      const data = await runResearchPipeline(query.trim(), {
        documentation_mode: docMode,
      });
      setResearchResult(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleBrowser() {
    if (!url.trim()) return;
    try {
      const data = await runBrowserRuntime(url.trim(), []);
      setBrowserResult(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <section className="workspace-card">
      <div className="section-header">
        <h2>Phase 10 — Research + Browser Runtime</h2>
        <button onClick={refresh}>Refresh</button>
      </div>

      <div className="goal-box">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Например: Playwright safe browser runtime architecture"
        />
        <button onClick={handleResearch}>Run Research</button>
      </div>

      <div className="actions-row" style={{ justifyContent: "flex-start", marginBottom: 12 }}>
        <label>
          <input type="checkbox" checked={docMode} onChange={(e) => setDocMode(e.target.checked)} /> documentation mode
        </label>
      </div>

      <div className="goal-box">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com"
        />
        <button onClick={handleBrowser}>Run Browser</button>
      </div>

      {status ? (
        <div className="json-block">
          <h3>Status</h3>
          <pre>{JSON.stringify(status, null, 2)}</pre>
        </div>
      ) : null}

      {researchResult ? (
        <div className="json-block">
          <h3>Research Result</h3>
          <pre>{JSON.stringify(researchResult, null, 2)}</pre>
        </div>
      ) : null}

      {browserResult ? (
        <div className="json-block">
          <h3>Browser Result</h3>
          <pre>{JSON.stringify(browserResult, null, 2)}</pre>
        </div>
      ) : null}

      {error ? <div className="panel-error">{error}</div> : null}
    </section>
  );
}
