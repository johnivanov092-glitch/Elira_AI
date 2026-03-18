import { useEffect, useState } from "react";
import { api } from "../api/ide";

export default function SettingsPanelHotfix() {
  const [contexts, setContexts] = useState([]);
  const [selectedContext, setSelectedContext] = useState("32768");

  useEffect(() => {
    let mounted = true;
    api.listContextWindows().then((items) => {
      if (mounted) setContexts(items || []);
    });
    return () => {
      mounted = false
    };
  }, []);

  return (
    <div className="phase21-panel">
      <div className="pane-title">Настройки</div>
      <div className="phase21-body">
        <label className="task-field">
          <span>Контекст модели</span>
          <select
            value={selectedContext}
            onChange={(e) => setSelectedContext(e.target.value)}
            style={{ minWidth: 180 }}
          >
            {(contexts.length ? contexts : [4096, 8192, 16384, 32768, 65536, 131072, 262144]).map((item) => (
              <option key={item} value={String(item)}>
                {item / 1024 >= 1 ? `${Math.round(item / 1024)}k` : item}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}
