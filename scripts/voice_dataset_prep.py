#!/usr/bin/env python3
"""Voice-cloning dataset prep (Living Persona step D — custom voices).

Turns a folder of raw recordings of ONE speaker into a clean, segmented,
transcribed dataset ready for Piper fine-tuning (LJSpeech layout) — and just as
usable for XTTS/StyleTTS2. Engine-agnostic: this is the common first step.

Pipeline per the "professional" recipe (dataset quality is what matters most):
  1. load each recording, downmix to mono, resample to --target-sr
  2. split on silence into 3-15s clips (merge tiny, hard-split long)
  3. transcribe each clip via the self-hosted whisper STT (draft — PROOFREAD!)
  4. write wav/<id>.wav + metadata.csv ("<id>|<text>")
  5. write report.txt: durations, clipping/low-volume flags, clips to review

Requirements (run on your machine, one-off):  pip install pydub requests
plus ffmpeg on PATH (pydub uses it to read mp3/m4a/etc and resample).

Usage:
  python scripts/voice_dataset_prep.py \
      --input  ./wife_recordings \
      --output ./wife_voice_dataset \
      --stt-url http://192.168.88.15:8006 --lang ru

Then: proofread metadata.csv (exact text + punctuation = better prosody), check
report.txt, and feed the dataset to training (see docs/VOICE_CLONING.md).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"}


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _import_deps():
    try:
        from pydub import AudioSegment  # noqa
        from pydub.silence import split_on_silence  # noqa
    except Exception:
        _fail("pydub is required: pip install pydub  (and install ffmpeg on PATH)")
    try:
        import requests  # noqa
    except Exception:
        _fail("requests is required: pip install requests")
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    import requests
    return AudioSegment, split_on_silence, requests


def find_audio(input_dir: Path) -> list[Path]:
    return sorted(p for p in input_dir.rglob("*") if p.suffix.lower() in AUDIO_EXTS)


def segment(seg, split_on_silence, *, target_sr: int, min_ms: int, max_ms: int):
    """Mono + resample, split on silence, then merge tiny / hard-split long."""
    seg = seg.set_channels(1).set_frame_rate(target_sr)
    # silence_thresh relative to the clip's own loudness so it adapts per file.
    thresh = (seg.dBFS if seg.dBFS != float("-inf") else -30) - 16
    raw = split_on_silence(seg, min_silence_len=400, silence_thresh=thresh, keep_silence=200)
    if not raw:
        raw = [seg]

    # Merge consecutive short pieces up to >= min_ms; hard-split anything > max_ms.
    merged = []
    buf = None
    for piece in raw:
        buf = piece if buf is None else buf + piece
        if len(buf) >= min_ms:
            merged.append(buf)
            buf = None
    if buf is not None and len(buf) > 800:  # keep a trailing piece if not trivially short
        merged.append(buf)

    clips = []
    for piece in merged:
        if len(piece) <= max_ms:
            clips.append(piece)
        else:
            for start in range(0, len(piece), max_ms):
                chunk = piece[start:start + max_ms]
                if len(chunk) >= 800:
                    clips.append(chunk)
    return clips


def transcribe(requests, stt_url: str, wav_path: Path, lang: str | None) -> str:
    with open(wav_path, "rb") as fh:
        files = {"file": (wav_path.name, fh, "audio/wav")}
        data = {"language": lang} if lang else {}
        resp = requests.post(f"{stt_url.rstrip('/')}/stt", files=files, data=data, timeout=180)
    resp.raise_for_status()
    return str(resp.json().get("text", "")).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare a single-speaker TTS dataset.")
    ap.add_argument("--input", required=True, help="folder with raw recordings of ONE speaker")
    ap.add_argument("--output", required=True, help="dataset output folder")
    ap.add_argument("--stt-url", default=os.environ.get("ELIRA_STT_URL", "http://192.168.88.15:8006"))
    ap.add_argument("--lang", default="ru", help="STT language hint ('' = auto)")
    ap.add_argument("--target-sr", type=int, default=22050, help="Piper trains at 22050")
    ap.add_argument("--min-sec", type=float, default=3.0)
    ap.add_argument("--max-sec", type=float, default=15.0)
    ap.add_argument("--no-transcribe", action="store_true", help="skip STT (clips only)")
    args = ap.parse_args()

    AudioSegment, split_on_silence, requests = _import_deps()

    in_dir = Path(args.input).expanduser().resolve()
    out_dir = Path(args.output).expanduser().resolve()
    if not in_dir.is_dir():
        _fail(f"input folder not found: {in_dir}")
    wav_dir = out_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    sources = find_audio(in_dir)
    if not sources:
        _fail(f"no audio files found under {in_dir} (exts: {sorted(AUDIO_EXTS)})")
    print(f"Found {len(sources)} source recording(s). Segmenting…")

    rows: list[tuple[str, str]] = []
    report: list[str] = []
    total_ms = 0
    idx = 0
    for src in sources:
        try:
            seg = AudioSegment.from_file(src)
        except Exception as exc:
            report.append(f"SKIP {src.name}: cannot read ({exc})")
            continue
        clips = segment(seg, split_on_silence, target_sr=args.target_sr,
                        min_ms=int(args.min_sec * 1000), max_ms=int(args.max_sec * 1000))
        for clip in clips:
            idx += 1
            cid = f"clip_{idx:05d}"
            wav_path = wav_dir / f"{cid}.wav"
            clip.export(wav_path, format="wav")
            total_ms += len(clip)

            flags = []
            if clip.max_dBFS > -1.0:
                flags.append("CLIPPING")
            if clip.dBFS < -33.0:
                flags.append("LOW_VOLUME")

            text = ""
            if not args.no_transcribe:
                try:
                    text = transcribe(requests, args.stt_url, wav_path, args.lang or None)
                except Exception as exc:
                    flags.append(f"STT_FAIL({exc})")
            if not text:
                flags.append("EMPTY_TEXT_REVIEW")
            rows.append((cid, text))
            if flags:
                report.append(f"{cid}.wav  {len(clip)/1000:.1f}s  dBFS={clip.dBFS:.1f}  -> {', '.join(flags)}")

    # metadata.csv (LJSpeech style: id|text)
    meta = out_dir / "metadata.csv"
    meta.write_text("\n".join(f"{cid}|{text}" for cid, text in rows) + "\n", encoding="utf-8")

    mins = total_ms / 60000.0
    header = [
        f"Dataset: {out_dir}",
        f"Clips: {len(rows)}   Total speech: {mins:.1f} min   Target SR: {args.target_sr} Hz",
        "Guideline: >=30 min usable, 1-3 h for professional. Clean, one speaker, no noise.",
        "NEXT: proofread metadata.csv (exact words + punctuation), fix flagged clips below.",
        "-" * 60,
    ]
    (out_dir / "report.txt").write_text("\n".join(header + (report or ["No quality flags."])) + "\n", encoding="utf-8")

    print(f"\nDone. {len(rows)} clips, {mins:.1f} min of speech.")
    print(f"  wav/        -> {wav_dir}")
    print(f"  metadata.csv-> {meta}   (PROOFREAD the transcripts!)")
    print(f"  report.txt  -> {out_dir / 'report.txt'}   ({len(report)} flag(s))")
    if mins < 30:
        print("  NOTE: <30 min — fine for a first test, but record more for professional quality.")


if __name__ == "__main__":
    main()
