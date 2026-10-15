import { useEffect, useState } from "react";
import idle from "./frames/idle.png";
import listening from "./frames/listening.png";
import looking from "./frames/looking.png";
import thinking from "./frames/thinking.png";
import speaking from "./frames/speaking.png";
import blink from "./frames/blink.png";
import type { CompanionState } from "./companionState";

export type Face = CompanionState | "blink";

/** Six full-body frames (512×768 copies of 1024×1536 originals, transparent background).
 *  The generated bodies differ slightly, so only the head changes between states. */
export const FRAMES: Record<Face, string> = { idle, listening, looking, thinking, speaking, blink };

/** Head box in original 1024×1536 coordinates, as a clip-path over the full frame. */
const HEAD = { x: 360, y: 20, w: 300, h: 360 };
export const HEAD_CLIP = `inset(${(HEAD.y / 1536) * 100}% ${((1024 - HEAD.x - HEAD.w) / 1024) * 100}% `
  + `${((1536 - HEAD.y - HEAD.h) / 1536) * 100}% ${(HEAD.x / 1024) * 100}%)`;

/** The figure spans x 244–782 of 1024: the part of the frame that takes the mouse. */
export const FIGURE_X = { from: 244 / 1024, to: 782 / 1024 };

/** The face to show now: the state's face, a blink every few seconds, lips moving while speaking. */
export function useLiveFace(state: CompanionState): Face {
  const [blinking, setBlinking] = useState(false);
  const [mouthOpen, setMouthOpen] = useState(true);

  useEffect(() => {
    let timer = 0;
    let open = 0;
    const schedule = () => {
      timer = window.setTimeout(() => {
        setBlinking(true);
        open = window.setTimeout(() => setBlinking(false), 140);
        schedule();
      }, 2800 + Math.random() * 3200);
    };
    schedule();
    return () => { window.clearTimeout(timer); window.clearTimeout(open); };
  }, []);

  useEffect(() => {
    if (state !== "speaking") return;
    const timer = window.setInterval(() => setMouthOpen(value => !value), 150);
    return () => window.clearInterval(timer);
  }, [state]);

  if (state === "speaking") return mouthOpen ? "speaking" : "idle";
  if (blinking && state !== "thinking") return "blink";
  return state;
}
