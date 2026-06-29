#!/usr/bin/env python3
"""Voice-cloning dataset prep (Living Persona step D — custom voices).

Turns a folder of raw recordings of ONE speaker into a clean, segmented,
transcribed dataset ready for Piper fine-tuning (LJSpeech layout) — and just as
usable for XTTS/StyleTTS2. Engine-agnostic: this is the common first step.

Audio backend = soundfile + numpy (pip-only, no system ffmpeg). soundfile's
bundled libsndfile decodes wav/flac/ogg/opus — which covers WhatsApp voice
notes (.ogg/opus). Pipeline per the "professional" recipe (dataset quality is
what matters most):
  1. load each recording, downmix to mono, resample to --target-sr
  2. trim + split long files on silence into ~3-15s clips
  3. transcribe each clip via the self-hosted whisper STT (DRAFT — PROOFREAD!)
  4. write wav/<id>.wav + metadata.csv ("<id>|<text>")
  5. write report.txt: durations, clipping/low-volume flags, clips to review

Requirements (run on your machine, one-off):  pip install soundfile numpy requests

Usage:
  python scripts/voice_dataset_prep.py \
      --input  "C:/Users/Root/Desktop/Голос Лолита" \
      --output ./lolita_voice_dataset \
      --stt-url http://192.168.88.15:8006 --lang ru
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
        import numpy as np  # noqa
        import soundfile as sf  # noqa
    except Exception:
        _fail("need soundfile + numpy: pip install soundfile numpy")
    try:
        import requests  # noqa
    except Exception:
        _fail("requests is required: pip install requests")
    import numpy as np
    import soundfile as sf
    import requests
    return np, sf, requests


def find_audio(input_dir: Path) -> list[Path]:
    return sorted(p for p in input_dir.rglob("*") if p.suffix.lower() in AUDIO_EXTS)


def _resample(np, x, sr: int, target: int):
    if sr == target:
        return x
    n_out = int(round(len(x) * target / sr))
    if n_out <= 1:
        return x
    xp = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    fp = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(fp, xp, x).astype("float32")


def _voiced_mask(np, x, sr: int, thresh_db: float):
    fl = max(1, int(0.02 * sr))  # 20 ms frames
    nf = len(x) // fl
    if nf < 1:
        return np.array([True]), fl
    fr = x[: nf * fl].reshape(nf, fl)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-9)
    ref = np.percentile(rms, 95) + 1e-9
    db = 20.0 * np.log10(rms / ref + 1e-9)
    return db > thresh_db, fl


def _trim(np, x, sr: int, thresh_db: float = -34.0):
    voiced, fl = _voiced_mask(np, x, sr, thresh_db)
    idx = np.where(voiced)[0]
    if len(idx) == 0:
        return x[:0]
    return x[idx[0] * fl: min(len(x), (idx[-1] + 1) * fl)]


def _split_on_silence(np, x, sr: int, min_sil_ms: int, thresh_db: float, keep_ms: int):
    voiced, fl = _voiced_mask(np, x, sr, thresh_db)
    keep = int(keep_ms / 1000 * sr)
    sil_frames = max(1, int(min_sil_ms / 20))
    spans = []
    start = None
    gap = 0
    for j, v in enumerate(voiced):
        if v:
            if start is None:
                start = j
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= sil_frames:
                spans.append((start, j - gap + 1))
                start = None
                gap = 0
    if start is not None:
        spans.append((start, len(voiced)))
    out = []
    for a, b in spans:
        s0 = max(0, a * fl - keep)
        s1 = min(len(x), b * fl + keep)
        if s1 > s0:
            out.append(x[s0:s1])
    return out or [x]


def segment(np, x, sr: int, *, target_sr: int, min_ms: int, max_ms: int):
    x = x.astype("float32")
    x = _resample(np, x, sr, target_sr)
    sr = target_sr
    x = _trim(np, x, sr)
    if len(x) < int(0.8 * sr):
        return []
    if len(x) / sr * 1000 <= max_ms:
        return [x]
    pieces = _split_on_silence(np, x, sr, min_sil_ms=300, thresh_db=-34.0, keep_ms=120)
    merged = []
    buf = None
    for p in pieces:
        buf = p if buf is None else np.concatenate([buf, p])
        if len(buf) / sr * 1000 >= min_ms:
            merged.append(buf)
            buf = None
    if buf is not None and len(buf) / sr * 1000 >= 800:
        merged.append(buf)
    final = []
    step = int(max_ms / 1000 * sr)
    for p in merged:
        if len(p) / sr * 1000 <= max_ms:
            final.append(p)
        else:
            for i in range(0, len(p), step):
                c = p[i:i + step]
                if len(c) / sr * 1000 >= 800:
                    final.append(c)
    return final


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

    np, sf, requests = _import_deps()

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
    total_samples = 0
    idx = 0
    for src in sources:
        try:
            data, sr = sf.read(str(src), dtype="float32", always_2d=False)
        except Exception as exc:
            report.append(f"SKIP {src.name}: cannot read ({exc})")
            continue
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1).astype("float32")
        clips = segment(np, data, sr, target_sr=args.target_sr,
                        min_ms=int(args.min_sec * 1000), max_ms=int(args.max_sec * 1000))
        for clip in clips:
            idx += 1
            cid = f"clip_{idx:05d}"
            wav_path = wav_dir / f"{cid}.wav"
            sf.write(str(wav_path), clip, args.target_sr, subtype="PCM_16")
            total_samples += len(clip)

            peak = float(np.max(np.abs(clip))) if len(clip) else 0.0
            rms = float(np.sqrt((clip ** 2).mean() + 1e-9)) if len(clip) else 0.0
            dbfs = 20.0 * float(np.log10(rms + 1e-9))
            flags = []
            if peak >= 0.99:
                flags.append("CLIPPING")
            if dbfs < -33.0:
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
                report.append(f"{cid}.wav  {len(clip)/args.target_sr:.1f}s  dBFS={dbfs:.1f}  -> {', '.join(flags)}")

    meta = out_dir / "metadata.csv"
    meta.write_text("\n".join(f"{cid}|{text}" for cid, text in rows) + "\n", encoding="utf-8")

    mins = total_samples / args.target_sr / 60.0
    header = [
        f"Dataset: {out_dir}",
        f"Clips: {len(rows)}   Total speech: {mins:.1f} min   Target SR: {args.target_sr} Hz",
        "Guideline: >=30 min usable, 1-3 h for professional. Clean, one speaker, no noise.",
        "NEXT: proofread metadata.csv (exact words + punctuation), fix flagged clips below.",
        "-" * 60,
    ]
    (out_dir / "report.txt").write_text("\n".join(header + (report or ["No quality flags."])) + "\n", encoding="utf-8")

    print(f"\nDone. {len(rows)} clips, {mins:.1f} min of speech.")
    print(f"  wav/         -> {wav_dir}")
    print(f"  metadata.csv -> {meta}   (PROOFREAD the transcripts!)")
    print(f"  report.txt   -> {out_dir / 'report.txt'}   ({len(report)} flag(s))")
    if mins < 30:
        print("  NOTE: <30 min — fine for a first test, but record more for professional quality.")


if __name__ == "__main__":
    main()
