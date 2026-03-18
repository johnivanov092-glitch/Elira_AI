export default function ChatInputHotfix({
  value,
  onChange,
  onSubmit,
  placeholder = "Напиши задачу... Jarvis сам выберет режим",
}) {
  return (
    <div
      style={{
        marginTop: 16,
        border: "1px solid rgba(255,255,255,0.10)",
        borderRadius: 22,
        padding: 12,
        minHeight: 150, // hotfix: smaller by ~25%
      }}
    >
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
        <button className="soft-btn" type="button">+</button>

        <div style={{ flex: 1 }}>
          <textarea
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            placeholder={placeholder}
            style={{
              width: "100%",
              minHeight: 92,
              resize: "vertical",
              background: "transparent",
              border: "none",
              outline: "none",
            }}
          />
        </div>

        <button className="soft-btn" type="button" onClick={onSubmit}>
          ➤
        </button>
      </div>
    </div>
  );
}
