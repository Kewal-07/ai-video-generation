# AI Video Production Pipeline

Generates a 2-minute explainer video, "How Computers See Color", from
`shots.json` through a fully automated pipeline: TTS, AI images, AI
image-to-video on flagged shots, Pillow text overlays, and ffmpeg assembly.

## Setup

```bash
brew install ffmpeg
pip3 install requests pillow edge-tts
export POLLINATIONS_KEY=your_key_from_enter.pollinations.ai
```

## Run

```bash
python3 pipeline.py
```

Use `--stages tts,images` to run a subset, `--only s01,s06` to target
specific shots, `--force` to bypass the cache. Output lands in
`output/final.mp4`; per-shot evidence (prompts, seeds, endpoints, retries) is
written to `run_log.json`.

## Video link

See `PIPELINE_LOG.md`, section 1.
