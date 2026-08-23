import { ExternalLink } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fetchCodeAgentImage, type AnswerMediaItem } from "../api/codeAgent";
import { ExternalBrowserLink } from "../components/ExternalLink";

const EMPTY_PIXEL = "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=";

function ProxiedAnswerImage({ item, onError }: { item: AnswerMediaItem; onError: () => void }) {
  const [src, setSrc] = useState(EMPTY_PIXEL);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    const timeout = setTimeout(() => {
      onErrorRef.current();
      controller.abort();
    }, 15_000);
    void fetchCodeAgentImage(item.url, controller.signal)
      .then((blob) => {
        if (!blob.type.startsWith("image/")) throw new Error("answer image is not an image");
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) onErrorRef.current();
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      clearTimeout(timeout);
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.url]);

  return (
    <img
      src={src}
      alt={item.title}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02]"
      onError={onError}
    />
  );
}

export function AnswerMediaGallery({ media }: { media: AnswerMediaItem[] }) {
  const [failed, setFailed] = useState<Set<string>>(() => new Set());
  const visible = media.filter((item) => item.type === "image" && !failed.has(item.url));
  if (!visible.length) return null;

  return (
    <div
      className="answer-media-gallery my-3 flex gap-2 overflow-x-auto pb-1"
      aria-label="Изображения к ответу"
    >
      {visible.map((item) => (
        <ExternalBrowserLink
          key={`${item.url}|${item.source_url}`}
          href={item.source_url}
          title={`Открыть источник: ${item.title}`}
          className="group relative min-w-[165px] max-w-[250px] flex-1 basis-[30%] overflow-hidden rounded-xl border border-line bg-surface"
        >
          <div className="aspect-[4/3] overflow-hidden bg-hover">
            <ProxiedAnswerImage
              item={item}
              onError={() => setFailed((current) => new Set(current).add(item.url))}
            />
          </div>
          <div className="flex items-center gap-1.5 px-2 py-1.5 text-[10.5px] text-mut">
            <span className="min-w-0 flex-1 truncate">{item.source || item.title}</span>
            <ExternalLink size={11} className="shrink-0" aria-hidden="true" />
          </div>
        </ExternalBrowserLink>
      ))}
    </div>
  );
}
