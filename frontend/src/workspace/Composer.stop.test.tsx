import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Composer } from "./Composer";

describe("Composer Stop confirmation", () => {
  it("shows cleanup failure and an enabled retry while blocking a new send", () => {
    const html = renderToStaticMarkup(<Composer value="следующая задача" onChange={() => {}} sessionId="test"
      onPlus={() => {}} onPlugins={() => {}} onSend={async () => true} onSendMultiAgent={() => {}}
      running={false} runControlState="cancel_failed" cancelError="process 7312 is still alive" onStop={() => {}} />);
    expect(html).toContain("Остановка не подтверждена");
    expect(html).toContain("process 7312 is still alive");
    expect(html).toMatch(/<button(?=[^>]*aria-label="Повторить остановку")(?=[^>]*>)(?![^>]*disabled)[^>]*>/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*aria-label="Отправить"/);
  });
});
