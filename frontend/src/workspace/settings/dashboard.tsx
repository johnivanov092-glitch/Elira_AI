import { useEffect, useState } from "react";
import { Loading, Note, Wrap } from "./_shared";

export function Lazy({ load, title }: { load: () => Promise<unknown>; title: string }) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let alive = true;
    load().then((r) => { if (alive) setData((r ?? {}) as Record<string, unknown>); }).catch(() => { if (alive) setData({}); });
    return () => { alive = false; };
  }, [load]);
  return (
    <Wrap title={title}>
      {data === null ? <Loading /> : <KV data={data} />}
    </Wrap>
  );
}

// Human labels for the top-level dashboard groups; anything unmapped falls back
// to its raw key so a new backend section still shows up.
const DASH_GROUP_LABELS: Record<string, string> = {
  stats: "Статистика",
  projectBrainStatus: "Память проекта",
  personaStatus: "Персона",
  runtimeStatus: "Среда выполнения",
  agentOsHealth: "Agent OS · здоровье",
  agentOsDashboard: "Agent OS · прогоны",
  agentOsLimits: "Agent OS · лимиты",
  agentOsEvents: "Agent OS · события",
};

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (Array.isArray(v)) return v.length ? `${v.length} шт.` : "—";
  return String(v);
}

/** One label/value row in a dashboard card. */
function Row({ k, v }: { k: string; v: unknown }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-1.5 text-[12.5px]">
      <span className="text-mut">{k}</span>
      <span className="max-w-[55%] truncate font-mono text-t2">{fmtVal(v)}</span>
    </div>
  );
}

const isScalar = (v: unknown) =>
  v !== null && (["string", "number", "boolean"].includes(typeof v) || Array.isArray(v));

/** Flatten one group's fields into rows, descending one level into nested
 *  status maps (e.g. runtimeStatus.api_keys_present.tavily) so their flags —
 *  Tavily and the like — still surface instead of being silently dropped. */
function groupRows(data: Record<string, unknown>): { k: string; v: unknown }[] {
  const rows: { k: string; v: unknown }[] = [];
  for (const [k, v] of Object.entries(data)) {
    if (isScalar(v)) {
      rows.push({ k, v });
    } else if (v && typeof v === "object") {
      for (const [ck, cv] of Object.entries(v as Record<string, unknown>)) {
        if (isScalar(cv)) rows.push({ k: `${k} · ${ck}`, v: cv });
      }
    }
  }
  return rows;
}

/** A titled card listing the (flattened) fields of one nested group. */
function Group({ title, data }: { title: string; data: Record<string, unknown> }) {
  const rows = groupRows(data);
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="px-0.5 text-[11.5px] font-medium uppercase tracking-wide text-mut">{title}</div>
      {rows.map(({ k, v }) => <Row key={k} k={k} v={v} />)}
    </div>
  );
}

function KV({ data }: { data: Record<string, unknown> }) {
  const errors = Array.isArray(data.errors) ? (data.errors as string[]) : [];
  // Top-level dashboard payload is a map of nested status objects — render each
  // as its own card. Scalar top-level fields (if any) collapse into one group.
  const groups: { key: string; obj: Record<string, unknown> }[] = [];
  const flat: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(data)) {
    if (key === "errors") continue;
    if (val && typeof val === "object" && !Array.isArray(val)) {
      groups.push({ key, obj: val as Record<string, unknown> });
    } else {
      flat[key] = val;
    }
  }

  const cards = groups
    .map(({ key, obj }) => <Group key={key} title={DASH_GROUP_LABELS[key] ?? key} data={obj} />)
    .filter(Boolean);
  if (Object.keys(flat).length) cards.unshift(<Group key="__flat" title="Общее" data={flat} />);

  if (cards.length === 0 && errors.length === 0) return <Note>Нет данных для отображения.</Note>;
  return (
    <div className="flex flex-col gap-4">
      {cards}
      {errors.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="px-0.5 text-[11.5px] font-medium uppercase tracking-wide text-mut">Недоступно</div>
          {errors.map((e, i) => (
            <div key={i} className="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mut">{e}</div>
          ))}
        </div>
      )}
    </div>
  );
}
