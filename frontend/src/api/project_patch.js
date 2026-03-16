export async function previewProjectPatch(path, newContent, maxChars = 20000) {
  const response = await fetch("/api/project/patch/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path,
      new_content: newContent,
      max_chars: maxChars,
    }),
  });
  return await response.json();
}

export async function applyProjectPatch(path, newContent) {
  const response = await fetch("/api/project/patch/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path,
      new_content: newContent,
    }),
  });
  return await response.json();
}

export async function replaceInFile(path, oldText, newText, maxChars = 20000) {
  const response = await fetch("/api/project/patch/replace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path,
      old_text: oldText,
      new_text: newText,
      max_chars: maxChars,
    }),
  });
  return await response.json();
}
