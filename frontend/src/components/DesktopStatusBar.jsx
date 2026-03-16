import { useEffect, useState } from "react";
import { getDesktopHandshake, getDesktopInfo, getDesktopStatus } from "../api/desktop";

export default function DesktopStatusBar() {
  const [state, setState] = useState({
    status: "loading",
    info: null,
    handshake: null,
    error: null,
  });

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [status, info, handshake] = await Promise.all([
          getDesktopStatus().catch(() => null),
          getDesktopInfo().catch(() => null),
          getDesktopHandshake().catch(() => null),
        ]);
        if (!active) return;
        setState({ status: "ready", info, handshake, error: null });
      } catch (error) {
        if (!active) return;
        setState({ status: "error", info: null, handshake: null, error: String(error) });
      }
    }
    load();
    const timer = setInterval(load, 8000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="desktop-status-bar">
      <div className="desktop-pill">
        <strong>Desktop</strong>
        <span>{state.status}</span>
      </div>
      <div className="desktop-pill">
        <strong>Backend</strong>
        <span>{state.info?.status || "unknown"}</span>
      </div>
      <div className="desktop-pill">
        <strong>Mode</strong>
        <span>{state.handshake?.mode || "workspace"}</span>
      </div>
      {state.error ? <div className="desktop-error">{state.error}</div> : null}
    </div>
  );
}
