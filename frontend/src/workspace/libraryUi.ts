import { useRef, useState } from "react";
import { uploadLibraryFile, type LibraryFile } from "../api/library";

export function formatLibrarySize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

export function libraryIndexLabel(file: LibraryFile): string {
  if (file.status === "ready") return "проиндексирован";
  if (file.status === "capped") return "индекс ограничен";
  if (file.status === "failed") return "ошибка индексации";
  return "только превью";
}

export function useLibraryUpload(onComplete: () => void) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function addFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setUploadError("");
    const failures: string[] = [];
    let uploaded = 0;
    try {
      for (const file of Array.from(files)) {
        try {
          await uploadLibraryFile(file, { useInContext: true });
          uploaded += 1;
        } catch {
          failures.push(file.name);
        }
      }
      if (uploaded > 0) onComplete();
      if (failures.length > 0) {
        setUploadError(`Не добавлены: ${failures.join(", ")}`);
      }
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return {
    uploading,
    uploadError,
    fileRef,
    addFiles,
    openPicker: () => fileRef.current?.click(),
  };
}
