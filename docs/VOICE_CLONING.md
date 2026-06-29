# Giving Elira a custom voice (e.g. a specific person)

Elira's TTS is **Piper** (CPU, ONNX). A Piper voice is a trained model — there's
no zero-shot "drop in 10 seconds". To make Elira speak with a specific person's
voice you **fine-tune a Piper voice on their recordings**, export to
`.onnx` + `.onnx.json`, and drop the files into the server's voices folder. The
app's voice picker (Settings → «Голос») then lists it — the slot is already
swappable.

> ⚠️ Only clone a real person's voice **with their consent**.

Quality is decided mostly by the **dataset**, not the engine. Aim for clean,
single-speaker, consistent recordings; ~30 min is a usable minimum, **1–3 h** for
professional results.

---

## 1. Record well (this matters most)
- One speaker, quiet room, **no** background noise / music / echo, no clipping.
- Same mic, consistent distance and volume. Mono is fine.
- Varied sentences and intonation (not the same phrase repeated).

## 2. Prepare the dataset (automated)
Needs `pip install pydub requests` + `ffmpeg` on PATH. The whisper STT service
(`:8006`) must be up (it drafts the transcripts).

```sh
python scripts/voice_dataset_prep.py \
    --input  ./her_recordings \
    --output ./her_voice_dataset \
    --stt-url http://192.168.88.15:8006 --lang ru
```

Produces `her_voice_dataset/wav/*.wav` (mono, 22050 Hz, 3–15 s clips),
`metadata.csv` (`id|text`), and `report.txt` (durations + clipping/low-volume
flags + clips to review).

## 3. Proofread (don't skip)
Open `metadata.csv` and fix every transcript to the **exact** words, with
punctuation — Piper learns prosody from it. Re-record or drop clips flagged
`CLIPPING` / `LOW_VOLUME` in `report.txt`.

## 4. Fine-tune a Piper voice (needs PyTorch + a GPU)
The server GPU is busy with the LLMs, so do this **offline / on another machine
or a cloud GPU** — it's a one-time step; the result (a small `.onnx`) runs
locally on CPU afterwards.

```sh
# env (one-time): clone Piper and install the trainer
git clone https://github.com/rhasspy/piper && cd piper/src/python
pip install -e .            # pulls torch

# preprocess the dataset (LJSpeech, single speaker, 22050 Hz)
python -m piper_train.preprocess \
    --language ru --dataset-format ljspeech --single-speaker \
    --sample-rate 22050 \
    --input-dir /path/to/her_voice_dataset \
    --output-dir /path/to/train_dir

# fine-tune FROM a Russian base checkpoint (far faster than from scratch).
# Grab a ru base .ckpt from huggingface rhasspy/piper-checkpoints (medium/high).
python -m piper_train \
    --dataset-dir /path/to/train_dir \
    --accelerator gpu --devices 1 --batch-size 16 \
    --validation-split 0.02 --num-test-examples 4 \
    --quality high \
    --resume_from_checkpoint /path/to/ru_base.ckpt \
    --checkpoint-epochs 1 --max_epochs 4000

# export the best checkpoint to ONNX + config
python -m piper_train.export_onnx /path/to/best.ckpt her_voice.onnx
cp /path/to/train_dir/config.json her_voice.onnx.json
```

Pick the checkpoint with the best validation loss / cleanest test samples.
`--quality high` = the high tier (22 kHz, larger model = cleaner audio).

## 5. Install the voice
Copy both files to the server and restart the TTS container:

```sh
scp her_voice.onnx her_voice.onnx.json \
    ai-server-codex:/home/aiadmin/elira-ai-server/docker/tts/voices/
ssh ai-server-codex 'cd /home/aiadmin/elira-ai-server/docker && \
    docker compose -f docker-compose.tts.yml restart'
```

Open Settings → «Голос», pick `her_voice`, press «Прослушать». Done — Elira
speaks with that voice, fully local on CPU.

---

## Alternative — maximum naturalness (XTTS / StyleTTS2, GPU)
For the most expressive, near-indistinguishable clone (and cloning from less
data), models like **XTTS v2** or **StyleTTS2** are stronger than Piper. The
cost: they need **torch + a GPU** at inference (a heavier, separate voice
service that competes with the LLMs for the GPU), versus Piper's light CPU
service. Worth it only if you want maximal expressiveness and can dedicate GPU.
The same dataset from steps 1–3 works for them too.
