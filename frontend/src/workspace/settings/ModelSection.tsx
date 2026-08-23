import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { listLocalModels } from "../../api/models";
import { cn } from "../../ui/cn";
import { Note, Wrap } from "./_shared";
import { modelName } from "./util";

export function ModelSection({ model, onModel }: { model: string; onModel: (m: string) => void }) {
  const [models, setModels] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    listLocalModels()
      .then((r) => { if (alive) setModels(Array.from(new Set((r.models ?? []).map(modelName).filter(Boolean)))); })
      .catch(() => { /* offline */ });
    return () => { alive = false; };
  }, []);
  const options = ["auto", ...models.filter((m) => m !== "auto")];
  return (
    <Wrap title="Модель и провайдер">
      <div className="flex flex-col gap-1">
        {options.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onModel(m)}
            className={cn(
              "flex items-center gap-2.5 rounded-lg border px-3 py-2 text-left text-[13px] transition-colors",
              m === model ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx",
            )}
          >
            <span className="flex-1 font-mono">{m === "auto" ? "auto (оркестрация)" : m}</span>
            {m === model && <Check size={15} />}
          </button>
        ))}
        {options.length === 1 && <Note>Сервер недоступен — список моделей пуст.</Note>}
      </div>
    </Wrap>
  );
}
