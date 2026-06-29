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
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1") // links/images → text
    .replace(/[*_#>~|]/g, " ")                  // md punctuation
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, MAX_TTS_CHARS);
}

let _audio: HTMLAudioElement | null = null;

/** Synthesize `text` with the selected voice and play it. Best-effort: any
 *  failure (TTS down, autoplay blocked) is swallowed. Returns true if it played. */
export async function speak(text: string): Promise<boolean> {
  const clean = plainForSpeech(text);
  if (!clean) return false;
  try {
    const blob = await synthesizeSpeech(clean, getSelectedVoice() || undefined);
    const url = URL.createObjectURL(blob);
    stop();
    _audio = new Audio(url);
    _audio.onended = () => URL.revokeObjectURL(url);
    await _audio.play();
    return true;
  } catch {
    return false;
  }
}

export function stop(): void {
  try {
    if (_audio) {
      _audio.pause();
      _audio = null;
    }
  } catch {
    /* ignore */
  }
}
