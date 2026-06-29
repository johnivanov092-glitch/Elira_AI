// voice.ts — TTS API (Living Persona step D, slice 1).

import { request, safeRequest } from "./client";

export type VoiceStatus = {
  ok: boolean;
  configured?: boolean;
  url?: string;
  voices?: string[];
  error?: string;
};

/** Health + available voices of the self-hosted Piper service. Never throws. */
export async function getVoiceStatus(): Promise<VoiceStatus> {
  return safeRequest<VoiceStatus>("/api/voice/status", {}, { ok: false, voices: [] });
}

/** Available voice names (e.g. "ru_RU-irina-medium"). [] on any error. */
export async function listVoices(): Promise<string[]> {
  const r = await safeRequest<{ voices?: string[] }>("/api/voice/voices", {}, { voices: [] });
  return Array.isArray(r.voices) ? r.voices : [];
}

/** Synthesize speech → WAV blob. Throws on failure (caller decides what to do). */
export async function synthesizeSpeech(text: string, voice?: string): Promise<Blob> {
  return request<Blob>("/api/voice/tts", {
    method: "POST",
    body: voice ? { text, voice } : { text },
    responseType: "blob",
  });
}
