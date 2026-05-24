// StatusPanels.tsx — status & diagnostic sub-components for EliraChatShell

import React, { CSSProperties } from "react";
import { LucideIcon, BarChart3, Bot, BrainCircuit, Square, Workflow } from "lucide-react";
import {
  capabilityLabel,
  capabilityStateText,
  capabilityModeText,
  formatDurationMs,
  humanizeValue,
  runtimeStorageModeText,
  engineListText,
  yesNoText,
} from "../chatUtils";

// ── Primitive UI helpers ────────────────────────────────────────────────────

interface UiIconProps {
  icon: LucideIcon;
  size?: number;
  strokeWidth?: number;
  style?: CSSProperties;
}

export function UiIcon({ icon: Icon, size = 14, strokeWidth = 2, style }: UiIconProps) {
  return (
    <Icon
      size={size}
      strokeWidth={strokeWidth}
      style={{ display: "block", flexShrink: 0, ...style }}
      aria-hidden="true"
    />
  );
}

interface IconTextProps {
  icon: LucideIcon;
  children: React.ReactNode;
  size?: number;
  gap?: number;
  style?: CSSProperties;
  textStyle?: CSSProperties;
}

export function IconText({ icon, children, size = 14, gap = 6, style, textStyle }: IconTextProps) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap, ...style }}>
      <UiIcon icon={icon} size={size} />
      <span style={textStyle}>{children}</span>
    </span>
  );
}

// ── PanelNotice ─────────────────────────────────────────────────────────────

interface PanelNoticeProps {
  title: string;
  message: string;
  onRetry?: () => void;
  tone?: "error" | "warning" | "info";
}

export function PanelNotice({ title, message, onRetry, tone = "error" }: PanelNoticeProps) {
  if (!message) return null;

  const palette = ({
    error: { border: "rgba(244,67,54,0.45)", background: "rgba(244,67,54,0.08)", title: "#f44336" },
    warning: { border: "rgba(245,166,35,0.45)", background: "rgba(245,166,35,0.08)", title: "#f5a623" },
    info: { border: "rgba(99,102,241,0.35)", background: "rgba(99,102,241,0.08)", title: "var(--accent)" },
  } as Record<string, { border: string; background: string; title: string }>)[tone] ||
    { border: "rgba(244,67,54,0.45)", background: "rgba(244,67,54,0.08)", title: "#f44336" };

  return (
    <div style={{ marginBottom: 12, padding: "10px 12px", borderRadius: 10, border: `1px solid ${palette.border}`, background: palette.background }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: palette.title, marginBottom: 4 }}>{title}</div>
          <div style={{ fontSize: 11, color: "var(--text)", wordBreak: "break-word", whiteSpace: "pre-wrap" }}>{message}</div>
        </div>
        {onRetry && (
          <button
            className="soft-btn"
            style={{ fontSize: 10, padding: "3px 10px", border: "1px solid var(--border)", borderRadius: 6, flexShrink: 0 }}
            onClick={onRetry}
          >
            Повторить
          </button>
        )}
      </div>
    </div>
  );
}

// ── CapabilityStatusSection ─────────────────────────────────────────────────

interface CapabilityItem {
  available?: boolean;
  reason?: string;
  mode?: string;
  missing_packages?: string[];
  hint?: string;
  [key: string]: unknown;
}

interface CapabilityStatusSectionProps {
  status: { capabilities?: Record<string, CapabilityItem> } | null | undefined;
}

export function CapabilityStatusSection({ status }: CapabilityStatusSectionProps) {
  const entries = Object.entries(status?.capabilities || {});
  if (!entries.length) return null;

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>Возможности Project Brain</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 8 }}>
        {entries.map(([key, capability]) => {
          const packages = Array.isArray(capability?.missing_packages) ? capability.missing_packages.filter(Boolean) : [];
          const available = Boolean(capability?.available);
          const tone = available ? "#4caf50" : "#f5a623";
          return (
            <div key={key} style={{ padding: 12, borderRadius: 10, border: `1px solid ${available ? "rgba(76,175,80,0.28)" : "rgba(245,166,35,0.32)"}`, background: "var(--bg-surface)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>{capabilityLabel(key)}</div>
                <div style={{ fontSize: 10, fontWeight: 700, color: tone }}>{capabilityStateText(capability as Record<string, unknown>)}</div>
              </div>
              {capability?.mode && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                  Режим: {capabilityModeText(capability.mode)}
                </div>
              )}
              {!available && capability?.reason && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: packages.length || capability?.hint ? 4 : 0 }}>
                  Причина: {capabilityStateText(capability as Record<string, unknown>)}
                </div>
              )}
              {packages.length > 0 && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: capability?.hint ? 4 : 0 }}>
                  Не хватает: <code style={{ fontSize: 10 }}>{packages.join(", ")}</code>
                </div>
              )}
              {capability?.hint && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", wordBreak: "break-word" }}>
                  Подсказка: <code style={{ fontSize: 10 }}>{capability.hint}</code>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── PersonaStatusSection ────────────────────────────────────────────────────

interface PersonaStatusSectionProps {
  status: Record<string, unknown> | null | undefined;
  busy?: boolean;
  onRollback?: (version: unknown) => void;
}

export function PersonaStatusSection({ status, busy = false, onRollback }: PersonaStatusSectionProps) {
  if (!status?.active_version) return null;

  const traits = Array.isArray(status?.latest_traits) ? status.latest_traits as Record<string, unknown>[] : [];
  const models = Array.isArray(status?.model_consistency) ? status.model_consistency as Record<string, unknown>[] : [];

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>Личность Elira</div>
      <div style={{ padding: 12, borderRadius: 10, border: "1px solid rgba(99,102,241,0.28)", background: "var(--bg-surface)" }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 4 }}>
              Версия v{status.active_version as string}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
              Последняя эволюция: {status.last_evolution_at ? new Date(status.last_evolution_at as string).toLocaleString("ru-RU") : "—"}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
              Кандидатов в карантине: {(status.quarantine_candidates as number) ?? 0}
            </div>
          </div>
          {status.previous_version ? (
            <button
              className="soft-btn"
              style={{ fontSize: 10, padding: "4px 10px", border: "1px solid var(--border)", borderRadius: 6, flexShrink: 0 }}
              onClick={() => onRollback?.(status.previous_version)}
              disabled={busy}
            >
              {busy ? "Откат..." : `Откат к v${status.previous_version as string}`}
            </button>
          ) : null}
        </div>

        {traits.length ? (
          <div style={{ marginBottom: models.length ? 10 : 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>Последние принятые черты</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {traits.map((trait) => (
                <span
                  key={`${trait.trait_key as string}-${(trait.promoted_version || trait.last_seen) as string}`}
                  style={{ fontSize: 10, color: "var(--text)", padding: "4px 8px", borderRadius: 999, border: "1px solid var(--border)", background: "rgba(99,102,241,0.08)" }}
                >
                  {(trait.summary || trait.trait_key) as string}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {models.length ? (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>Согласованность по моделям</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 8 }}>
              {models.map((item) => (
                <div key={`${item.model as string}-${item.version_id as string}`} style={{ padding: 10, borderRadius: 8, border: "1px solid var(--border)", background: "rgba(255,255,255,0.01)" }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>{item.model as string}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Consistency: {item.consistency_score as number}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                    Обновлено: {item.updated_at ? new Date(item.updated_at as string).toLocaleString("ru-RU") : "—"}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ── RuntimeStatusSection ────────────────────────────────────────────────────

interface RuntimeStatusSectionProps {
  status: Record<string, unknown> | null | undefined;
}

export function RuntimeStatusSection({ status }: RuntimeStatusSectionProps) {
  if (!status?.ok) return null;

  const warning = (status?.warning as string) || "";
  const webWarnings = Array.isArray(status?.web_warnings) ? (status.web_warnings as string[]).filter(Boolean) : [];
  const rows = [
    { label: "Data dir", value: (status.data_dir as string) || "—" },
    { label: "Режим хранения", value: runtimeStorageModeText((status.storage_mode as string) || "") },
    { label: "Активных чатов", value: (status.active_chat_count as number) ?? 0 },
    { label: "Persona v", value: (status.persona_version as string) ?? "—" },
    { label: "Web primary", value: humanizeValue(status.primary_engine || "") || "—" },
    { label: "Web fallback", value: engineListText(status.fallback_engines) },
    { label: "Available engines", value: engineListText(status.available_engines) },
    { label: "Tavily key", value: yesNoText(Boolean((status.api_keys_present as Record<string, unknown>)?.tavily)) },
    { label: "Degraded mode", value: yesNoText(Boolean(status.degraded_mode)) },
    { label: "Python", value: (status.python_executable as string) || "—" },
    { label: "Backend origin", value: ((status.backend_origin || status.cwd) as string) || "—" },
  ];

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>Runtime</div>
      <div style={{ padding: 12, borderRadius: 10, border: `1px solid ${warning ? "rgba(245,166,35,0.35)" : "rgba(99,102,241,0.28)"}`, background: "var(--bg-surface)" }}>
        {warning ? (
          <div style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(245,166,35,0.35)", background: "rgba(245,166,35,0.08)", fontSize: 10, color: "var(--text)" }}>
            {warning}
          </div>
        ) : null}
        {webWarnings.length ? (
          <div style={{ marginBottom: 10, display: "grid", gap: 6 }}>
            {webWarnings.map((item, index) => (
              <div key={`${item}-${index}`} style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(99,102,241,0.25)", background: "rgba(99,102,241,0.08)", fontSize: 10, color: "var(--text)" }}>
                {item}
              </div>
            ))}
          </div>
        ) : null}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 8 }}>
          {rows.map((row) => (
            <div key={row.label} style={{ padding: 10, borderRadius: 8, border: "1px solid var(--border)", background: "rgba(255,255,255,0.01)" }}>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>{row.label}</div>
              <div style={{ fontSize: 11, color: "var(--text)", wordBreak: "break-word" }}>{String(row.value)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── AgentOsStatusSection ────────────────────────────────────────────────────

interface AgentOsStatusSectionProps {
  health: Record<string, unknown> | null | undefined;
  dashboard: Record<string, unknown> | null | undefined;
  limits: Record<string, unknown> | null | undefined;
}

export function AgentOsStatusSection({ health, dashboard, limits }: AgentOsStatusSectionProps) {
  const hasHealth = Boolean(health && (Array.isArray(health.components) || Array.isArray(health.warnings)));
  const hasDashboard = Boolean(
    dashboard && (
      dashboard.ok ||
      dashboard.total_agent_runs ||
      dashboard.workflow_runs ||
      dashboard.blocked_runs ||
      (dashboard.top_agents as unknown[] || []).length ||
      (dashboard.limits_summary as unknown[] || []).length
    )
  );
  const limitItems = Array.isArray(limits?.items)
    ? (limits!.items as Record<string, unknown>[])
    : Array.isArray(dashboard?.limits_summary)
    ? (dashboard!.limits_summary as Record<string, unknown>[])
    : [];
  if (!hasHealth && !hasDashboard && !limitItems.length) return null;

  const healthComponents = Array.isArray(health?.components) ? (health!.components as Record<string, unknown>[]) : [];
  const topAgents = Array.isArray(dashboard?.top_agents) ? (dashboard!.top_agents as Record<string, unknown>[]) : [];
  const recentViolations = Array.isArray(dashboard?.recent_violations) ? (dashboard!.recent_violations as Record<string, unknown>[]) : [];
  const warnings = [
    ...(Array.isArray(health?.warnings) ? (health!.warnings as string[]) : []),
    ...(Array.isArray(dashboard?.warnings) ? (dashboard!.warnings as string[]) : []),
  ].filter(Boolean);
  const keyLimits = limitItems
    .filter((item) =>
      ["builtin-universal", "builtin-researcher", "builtin-programmer", "builtin-analyst", "builtin-orchestrator", "workflow-engine"].includes(item?.agent_id as string)
    )
    .slice(0, 6);

  const summaryItems: Array<{ label: string; value: string | number; icon: LucideIcon }> = [
    { label: "Health", value: health?.ok ? "OK" : "Check", icon: Bot },
    { label: "Agent runs / 24ч", value: (dashboard?.total_agent_runs as number) ?? 0, icon: BrainCircuit },
    { label: "Workflow runs / 24ч", value: (dashboard?.workflow_runs as number) ?? 0, icon: Workflow },
    { label: "Blocked / 24ч", value: (dashboard?.blocked_runs as number) ?? 0, icon: Square },
    { label: "Avg duration", value: formatDurationMs((dashboard?.avg_duration_ms as number) ?? 0), icon: BarChart3 },
  ];

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>Agent OS</div>
      <div style={{ padding: 12, borderRadius: 10, border: `1px solid ${warnings.length ? "rgba(245,166,35,0.35)" : "rgba(16,185,129,0.28)"}`, background: "var(--bg-surface)" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 8, marginBottom: warnings.length || topAgents.length || recentViolations.length || keyLimits.length ? 12 : 0 }}>
          {summaryItems.map((item) => (
            <div key={item.label} style={{ padding: 10, borderRadius: 8, border: "1px solid var(--border)", background: "rgba(255,255,255,0.01)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                <UiIcon icon={item.icon} size={12} />
                <span>{item.label}</span>
              </div>
              <div style={{ fontSize: 12, color: "var(--text)", fontWeight: 600 }}>{item.value}</div>
            </div>
          ))}
        </div>

        {warnings.length ? (
          <div style={{ display: "grid", gap: 6, marginBottom: 12 }}>
            {warnings.map((warning, index) => (
              <div key={`${warning}-${index}`} style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(245,166,35,0.35)", background: "rgba(245,166,35,0.08)", fontSize: 10, color: "var(--text)" }}>
                {warning}
              </div>
            ))}
          </div>
        ) : null}

        {healthComponents.length ? (
          <div style={{ marginBottom: topAgents.length || recentViolations.length || keyLimits.length ? 12 : 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>Components</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 8 }}>
              {healthComponents.map((item) => (
                <div key={item.component as string} style={{ padding: 10, borderRadius: 8, border: `1px solid ${item.ok ? "rgba(16,185,129,0.26)" : "rgba(245,166,35,0.30)"}`, background: "rgba(255,255,255,0.01)" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 4 }}>
                    <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 600 }}>{humanizeValue(item.component)}</div>
                    <div style={{ fontSize: 10, color: item.ok ? "#10b981" : "#f5a623" }}>{item.ok ? "OK" : "Warn"}</div>
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", wordBreak: "break-word" }}>{(item.detail as string) || "—"}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {topAgents.length ? (
          <div style={{ marginBottom: recentViolations.length || keyLimits.length ? 12 : 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>Top agents</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 8 }}>
              {topAgents.map((item) => (
                <div key={item.agent_id as string} style={{ padding: 10, borderRadius: 8, border: "1px solid var(--border)", background: "rgba(255,255,255,0.01)" }}>
                  <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 600 }}>{item.agent_id as string}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>Запусков: {item.run_count as number}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {recentViolations.length ? (
          <div style={{ marginBottom: keyLimits.length ? 12 : 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>Recent violations</div>
            <div style={{ display: "grid", gap: 6 }}>
              {recentViolations.slice(0, 5).map((item, index) => {
                const details = item.details as Record<string, unknown> | undefined;
                return (
                  <div key={`${(item.id || item.created_at || index) as string}`} style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(244,67,54,0.28)", background: "rgba(244,67,54,0.08)" }}>
                    <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 600 }}>{(item.agent_id as string) || "unknown-agent"}</div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>{(details?.reason || details?.error || "policy_blocked") as string}</div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

        {keyLimits.length ? (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>Key limits</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: 8 }}>
              {keyLimits.map((item) => (
                <div key={item.agent_id as string} style={{ padding: 10, borderRadius: 8, border: "1px solid var(--border)", background: "rgba(255,255,255,0.01)" }}>
                  <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 600, marginBottom: 4 }}>{item.agent_id as string}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Runs/hour: {item.max_runs_per_hour as number}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>Max exec: {item.max_execution_seconds as number}s</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>Context: {item.max_context_tokens as number}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
