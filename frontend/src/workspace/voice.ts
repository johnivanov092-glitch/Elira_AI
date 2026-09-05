// voice.ts — playback + preferences for Elira's voice (Living Persona step D).
//
// Preferences live in localStorage (selected voice + auto-speak). Playback is a
// single shared <Audio>; calling speak() again interrupts the previous clip.

import { synthesizeSpeech } from "../api/voice";

const VOICE_KEY = "elira.voice";
const AUTO_KEY = "elira.autospeak";
const MAX_TTS_CHARS = 2000; // keep CPU synthesis snappy

export function getSelectedVoice(): string {
  try {
    return localStorage.getItem(VOICE_KEY) || "";
  } catch {
    return "";
  }
}

export function setSelectedVoice(voice: string): void {
  try {
    localStorage.setItem(VOICE_KEY, voice);
  } catch {
    /* ignore */
  }
}

export function getAutoSpeak(): boolean {
  try {
    return localStorage.getItem(AUTO_KEY) === "1";
  } catch {
    return false;
  }
}

export function setAutoSpeak(on: boolean): void {
  try {
    localStorage.setItem(AUTO_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

/** Strip common markdown so the voice reads prose, not symbols. */
export function plainForSpeech(md: string): string {
  return (md || "")
    .replace(/```[\s\S]*?```/g, " ")          // code fences
    .replace(/`([^`]+)`/g, "$1")               // inline code
    .replace(/\[\[source:[a-zA-Z0-9_-]{1,80}\]\]/g, " ") // citation pointers are not spoken
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1") // links/images → text
    .replace(/[*_#>~|]/g, " ")                  // md punctuation
    .replace(/\s+/g, " ")
    .trim();
}

let _audio: HTMLAudioElement | null = null;
let _playbackId = 0;
let _finishClip: (() => void) | null = null;

/** Bounded requests, preserving the whole accepted prose in order. */
export function speechChunks(text: string): string[] {
  const chunks: string[] = [];
  let remaining = plainForSpeech(text);
  while (remaining.length > MAX_TTS_CHARS) {
    const space = remaining.lastIndexOf(" ", MAX_TTS_CHARS);
    const end = space > 0 ? space : MAX_TTS_CHARS;
    chunks.push(remaining.slice(0, end));
    remaining = remaining.slice(end).trimStart();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

/** Synthesize `text` with the selected voice and play it. Best-effort: any
 *  failure (TTS down, autoplay blocked) returns false. True means all clips ended. */
export async function speak(text: string): Promise<boolean> {
  stop();
  const playbackId = _playbackId;
  const chunks = speechChunks(text);
  if (!chunks.length) return false;
  const voice = getSelectedVoice() || undefined;
  try {
    for (const chunk of chunks) {
      const blob = await synthesizeSpeech(chunk, voice);
      if (playbackId !== _playbackId) return false;
      const url = URL.createObjectURL(blob);
      const clip = new Audio(url);
      _audio = clip;
      const ended = await new Promise<boolean>((resolve) => {
        const finish = (ok: boolean) => {
          clip.onended = null;
          clip.onerror = null;
          clip.pause();
          URL.revokeObjectURL(url);
          if (_audio === clip) { _audio = null; _finishClip = null; }
          resolve(ok);
        };
        _finishClip = () => finish(false);
        clip.onended = () => finish(true);
        clip.onerror = () => finish(false);
        void clip.play().catch(() => finish(false));
      });
      if (!ended || playbackId !== _playbackId) return false;
    }
    return true;
  } catch {
    return false;
  }
}

export function stop(): void {
  _playbackId += 1;
  _finishClip?.();
  try {
    if (_audio) {
      _audio.pause();
      _audio = null;
    }
  } catch {
    /* ignore */
  }
}
