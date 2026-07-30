#!/usr/bin/env python3
"""shots.json -> output/final.mp4. Stages: concept -> tts -> images -> animate
-> overlay -> clips -> concat. --stages picks a subset, --only targets shot
ids, --force bypasses the on-disk cache."""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
OUTPUT = ROOT / "output"


def load_dotenv():
    # Minimal stdlib .env loader: KEY=VALUE per line, no quoting or
    # substitution needed here, so no reason to add a dependency for it.
    # Real exported env vars still win, so `export POLLINATIONS_KEY=...`
    # in the shell overrides whatever .env has.
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv()

POLLINATIONS_BASE = "https://gen.pollinations.ai"
POLLINATIONS_IMAGE_FALLBACK = "https://image.pollinations.ai/prompt"
NO_TEXT_SUFFIX = "no text, no letters, no numbers, no watermark"

OUT_W, OUT_H = 854, 480  # 480p: the free Pollinations fallback endpoint caps
                          # stills at 1024x576, so 720p output would upscale
                          # and soften; 480p keeps the source bigger than the
                          # output. Brief explicitly allows 480p.
STILL_W, STILL_H = OUT_W * 2, OUT_H * 2  # 2x so zoompan does not soften
FPS = 30
MIN_SHOT_SECONDS = 3.2
AUDIO_PAD_SECONDS = 0.35
STAGES = ["concept", "tts", "images", "animate", "overlay", "clips", "concat"]
MAX_ZOOM = 1.6  # safe up to 2.0: stills are generated at exactly 2x output res
PAN_ZOOM = 1.25  # zoom held during pans, gives room to travel across frame
VALID_MOTIONS = {"zoom_in", "zoom_out", "pan_right", "pan_left", "pan_up"}

def log(msg):
    print(f"[pipeline] {msg}", flush=True)

def load_shots(path):
    data = json.loads(Path(path).read_text())
    shots = data["shots"]
    for s in shots:
        if s["animate"] and s["overlay_text"]:
            raise AssertionError(f"{s['id']}: animate and overlay_text are mutually exclusive")
        if not s["overlay_text"].isascii():
            raise AssertionError(f"{s['id']}: overlay_text must be ASCII-only")
        if s["animate"]:
            if s.get("static_motion") not in VALID_MOTIONS:
                raise AssertionError(f"{s['id']}: animate shots need a valid static_motion fallback")
        elif s["motion"] not in VALID_MOTIONS:
            raise AssertionError(f"{s['id']}: motion must be one of {VALID_MOTIONS}")
    norm = lambda t: re.sub(r"\s+", " ", t).strip()
    joined = norm(" ".join(s["narration"] for s in shots))
    if joined != norm(data["full_script"]):
        raise AssertionError("concatenated shot narration does not reproduce the original script verbatim")
    return data

def load_run_log():
    p = ROOT / "run_log.json"
    return json.loads(p.read_text()) if p.exists() else {"shots": {}}

def save_run_log(run_log):
    (ROOT / "run_log.json").write_text(json.dumps(run_log, indent=2))

def shot_entry(run_log, shot_id):
    return run_log["shots"].setdefault(shot_id, {})

def request_with_backoff(url, headers=None, tries=4, timeout=120):
    """GET with exponential backoff. Returns (response, attempt_count) or raises."""
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r, attempt
            last_exc = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            last_exc = e
        if attempt < tries:
            time.sleep(2 ** attempt)
    raise last_exc

def strip_parens(text):
    # Some TTS voices read "(" and ")" aloud as "left paren" / "right paren".
    return text.replace("(", "").replace(")", "")

def pollinations_url(kind, prompt, params):
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
    return f"{POLLINATIONS_BASE}/{kind}/{quote(prompt)}?{query}"

def auth_headers(api_key):
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}

# ---------- concept: no LLM call, shot list is authored once against the
# dialogue and checked into shots.json so re-runs can't drift the approved
# narrative. This stage validates it and stamps it into run_log.json.

def stage_concept(data, shot_ids, force):
    run_log = load_run_log()
    run_log["base_seed"] = data["base_seed"]
    run_log["style_suffix"] = data["style_suffix"]
    for s in data["shots"]:
        if shot_ids and s["id"] not in shot_ids:
            continue
        entry = shot_entry(run_log, s["id"])
        entry["narration"] = s["narration"]
        entry["animate"] = s["animate"]
        entry["overlay_text"] = s["overlay_text"]
    save_run_log(run_log)
    log(f"concept: {len(data['shots'])} shots validated (mutual exclusivity, ASCII overlays, verbatim script)")

# ---------- tts ----------

def ffprobe_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    return float(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip())

def stage_tts(data, shot_ids, force):
    voice = data["voice"]
    tts_dir = CACHE / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    run_log = load_run_log()
    for s in data["shots"]:
        if shot_ids and s["id"] not in shot_ids:
            continue
        out_path = tts_dir / f"{s['id']}.mp3"
        entry = shot_entry(run_log, s["id"])
        if out_path.exists() and not force:
            log(f"tts {s['id']}: cached")
        else:
            narration = strip_parens(s["narration"])
            tmp_path = tts_dir / f"{s['id']}.raw.mp3"
            log(f"tts {s['id']}: generating with {voice}")
            subprocess.run(["edge-tts", "--voice", voice, "--text", narration,
                             "--write-media", str(tmp_path)], check=True)
            # Loudness normalized once per file here, not per clip later,
            # so shots don't drift in loudness relative to each other.
            subprocess.run(["ffmpeg", "-y", "-i", str(tmp_path), "-af",
                             "loudnorm=I=-16:TP=-1.5:LRA=11", str(out_path)],
                            check=True, capture_output=True)
            tmp_path.unlink()
        duration = ffprobe_duration(out_path)
        entry["audio_duration"] = duration
        entry["shot_duration"] = max(duration + AUDIO_PAD_SECONDS, MIN_SHOT_SECONDS)
        entry["voice"] = voice
        log(f"tts {s['id']}: audio={duration:.2f}s shot={entry['shot_duration']:.2f}s")
    save_run_log(run_log)

# ---------- images ----------

def fetch_image(prompt, model, seed, width, height, api_key):
    url = pollinations_url("image", prompt, {"model": model, "width": width, "height": height, "seed": seed})
    try:
        r, attempts = request_with_backoff(url, headers=auth_headers(api_key))
        return r.content, url, "gen.pollinations.ai", attempts, None
    except Exception as primary_exc:
        log(f"images: primary endpoint failed ({primary_exc}), trying fallback")
        fb_url = f"{POLLINATIONS_IMAGE_FALLBACK}/{quote(prompt)}?width={width}&height={height}&seed={seed}&nologo=true"
        try:
            r, attempts = request_with_backoff(fb_url)
            return r.content, fb_url, "image.pollinations.ai", attempts, str(primary_exc)
        except Exception as fallback_exc:
            return None, url, None, 0, f"primary: {primary_exc} | fallback: {fallback_exc}"

def stage_images(data, shot_ids, force, api_key):
    img_dir = CACHE / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    run_log = load_run_log()
    model, style = data["image_model"], data["style_suffix"]
    for i, s in enumerate(data["shots"]):
        if shot_ids and s["id"] not in shot_ids:
            continue
        out_path = img_dir / f"{s['id']}.png"
        entry = shot_entry(run_log, s["id"])
        seed = data["base_seed"] + i
        prompt = f"{s['image_prompt']}, {style}, {NO_TEXT_SUFFIX}"
        if out_path.exists() and not force:
            log(f"images {s['id']}: cached")
        else:
            content, used_url, endpoint, attempts, error = fetch_image(prompt, model, seed, STILL_W, STILL_H, api_key)
            if content is None:
                raise RuntimeError(f"images {s['id']}: both endpoints failed: {error}")
            out_path.write_bytes(content)
            entry.update(image_url=used_url, image_endpoint=endpoint, image_attempts=attempts,
                          image_seed=seed, image_prompt=prompt)
            log(f"images {s['id']}: fetched via {endpoint} in {attempts} attempt(s)")
        save_run_log(run_log)

# ---------- animate ----------

def stage_animate(data, shot_ids, force, api_key):
    video_dir = CACHE / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    run_log = load_run_log()
    model, step, style = data["video_model"], data["video_duration_step"], data["style_suffix"]
    for i, s in enumerate(data["shots"]):
        if not s["animate"] or (shot_ids and s["id"] not in shot_ids):
            continue
        out_path = video_dir / f"{s['id']}.mp4"
        entry = shot_entry(run_log, s["id"])
        seed = data["base_seed"] + i
        shot_duration = entry.get("shot_duration")
        if shot_duration is None:
            raise RuntimeError(f"animate {s['id']}: run tts stage first, shot_duration unknown")
        # nova-reel only accepts durations in multiples of video_duration_step
        # (floor 6s), so round the audio-driven duration up instead of fixing it.
        video_duration = step * math.ceil(shot_duration / step)
        if out_path.exists() and not force:
            log(f"animate {s['id']}: cached (duration={entry.get('video_duration', '?')})")
            continue
        image_ref = entry.get("image_url")
        if not image_ref:
            raise RuntimeError(f"animate {s['id']}: run images stage first, no image_url cached")
        prompt = f"{s['motion']}, {style}, {NO_TEXT_SUFFIX}"
        url = pollinations_url("video", prompt, {"model": model, "image": image_ref, "seed": seed,
                                                   "duration": video_duration, "width": OUT_W, "height": OUT_H})
        log(f"animate {s['id']}: requesting {model} duration={video_duration}s")
        try:
            r, attempts = request_with_backoff(url, headers=auth_headers(api_key), tries=4, timeout=300)
            out_path.write_bytes(r.content)
            entry.update(source="ai_video", video_url=url, video_attempts=attempts,
                          video_duration=video_duration, video_error=None)
            log(f"animate {s['id']}: succeeded in {attempts} attempt(s)")
        except Exception as e:
            # Best-effort: fall back to still + zoompan instead of failing
            # the whole pipeline over one flaky shot.
            entry.update(source="still", video_error=str(e))
            log(f"animate {s['id']}: FAILED after retries ({e}), falling back to still+zoompan")
        save_run_log(run_log)

# ---------- overlay ----------

def find_font(size):
    candidates = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                  "/System/Library/Fonts/Helvetica.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    path = next((c for c in candidates if Path(c).exists()), None)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

def subtitle_band_top(draw, text):
    # Subtitles wrap to 1-4 lines depending on narration length, so the band's
    # top edge moves shot to shot. Shared by draw_subtitle and draw_overlay so
    # the number label (only 6 of 17 shots) always clears it, however tall it is.
    font = find_font(int(OUT_H * 0.055))
    lines = wrap_text(draw, text, font, OUT_W * 0.86)
    line_h = int(OUT_H * 0.075)
    pad = int(OUT_H * 0.025)
    band_bottom = OUT_H - int(OUT_H * 0.04)
    return font, lines, line_h, pad, band_bottom - len(lines) * line_h - pad * 2

def draw_subtitle(dst_path, text):
    img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font, lines, line_h, pad, band_top = subtitle_band_top(draw, text)
    band_bottom = OUT_H - int(OUT_H * 0.04)
    draw.rectangle([0, band_top, OUT_W, band_bottom], fill=(0, 0, 0, 190))
    y = band_top + pad
    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text(((OUT_W - tw) / 2, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.save(dst_path)

def draw_overlay(dst_path, text, ceiling):
    # Drawn as its own transparent, fixed-position card at final output size
    # rather than baked onto the background: the background gets zoompan
    # motion (up to 1.6x), and text baked into a panned/zoomed frame can
    # crop out of view as the crop window shrinks. Compositing it after
    # zoompan keeps it always fully on screen, and lets it fade in as its
    # own layer independent of the background's motion. `ceiling` is this
    # shot's own subtitle band top, so the number sits just above it instead
    # of at a fixed height that could collide with a long, multi-line caption.
    img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = find_font(int(OUT_H * 0.09))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = int(OUT_H * 0.02)
    margin = int(OUT_H * 0.03)
    y = min(OUT_H * 0.55, ceiling - margin - th - pad * 2)
    x = (OUT_W - tw) / 2
    draw.rectangle([x - pad * 2, y - pad, x + tw + pad * 2, y + th + pad * 2], fill=(0, 0, 0, 220))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    img.save(dst_path)

def stage_overlay(data, shot_ids, force):
    overlay_dir = CACHE / "images_overlaid"
    subtitle_dir = CACHE / "subtitles"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    subtitle_dir.mkdir(parents=True, exist_ok=True)
    for s in data["shots"]:
        if shot_ids and s["id"] not in shot_ids:
            continue
        sub_dst = subtitle_dir / f"{s['id']}.png"
        if sub_dst.exists() and not force:
            log(f"overlay {s['id']}: subtitle cached")
        else:
            draw_subtitle(sub_dst, s["narration"])
            log(f"overlay {s['id']}: drew subtitle")
        if not s["overlay_text"]:
            continue
        dst = overlay_dir / f"{s['id']}.png"
        if dst.exists() and not force:
            log(f"overlay {s['id']}: number cached")
            continue
        # The image model never renders text: diffusion glyphs are unreliable
        # and re-rolling for legible text wastes budget for no gain. Pillow
        # draws exact characters every time, so every number is guaranteed
        # right instead of hoped for.
        probe = ImageDraw.Draw(Image.new("RGBA", (OUT_W, OUT_H)))
        _, _, _, _, ceiling = subtitle_band_top(probe, s["narration"])
        draw_overlay(dst, s["overlay_text"], ceiling)
        log(f"overlay {s['id']}: drew number '{s['overlay_text']}'")

# ---------- clips ----------

CLIP_ENCODE = ["-c:v", "libx264", "-crf", "20", "-preset", "medium",
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]

def motion_filter(motion, total_frames):
    # Rate is derived from this shot's own frame count so the move completes
    # by its last frame, whatever the shot's duration is - a fixed per-frame
    # rate made long shots (12-17s) hit the cap early and sit still for the
    # remainder, which is what made the video feel static.
    if motion == "zoom_in":
        rate = (MAX_ZOOM - 1.0) / total_frames
        return f"z='min(zoom+{rate:.6f},{MAX_ZOOM})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if motion == "zoom_out":
        rate = (MAX_ZOOM - 1.0) / total_frames
        return f"z='if(eq(on,0),{MAX_ZOOM},max(zoom-{rate:.6f},1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if motion in ("pan_right", "pan_left"):
        max_x = STILL_W - STILL_W / PAN_ZOOM
        rate = max_x / total_frames
        x = f"min({rate:.4f}*on,{max_x:.4f})" if motion == "pan_right" else f"max({max_x:.4f}-{rate:.4f}*on,0)"
        return f"z={PAN_ZOOM}:x='{x}':y='ih/2-(ih/zoom/2)'"
    max_y = STILL_H - STILL_H / PAN_ZOOM
    rate = max_y / total_frames
    return f"z={PAN_ZOOM}:x='iw/2-(iw/zoom/2)':y='max({max_y:.4f}-{rate:.4f}*on,0)'"

def build_animated_clip(video_path, audio_path, subtitle_path, duration, out_path):
    bg = (f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
          f"crop={OUT_W}:{OUT_H},fps={FPS},format=yuv420p[bg]")
    filter_complex = f"{bg};[1:v]format=rgba[sub];[bg][sub]overlay=0:0,format=yuv420p[v]"
    inputs = ["-stream_loop", "-1", "-i", str(video_path), "-loop", "1", "-i", str(subtitle_path), "-i", str(audio_path)]
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
           "-map", "[v]", "-map", "2:a", *CLIP_ENCODE, "-t", str(duration), str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)

def build_still_clip(image_path, audio_path, subtitle_path, duration, motion, out_path, overlay_path=None):
    total_frames = int(duration * FPS)
    zoom_expr = motion_filter(motion, total_frames)
    bg = (f"[0:v]scale={STILL_W}:{STILL_H},"
          f"zoompan={zoom_expr}:d={total_frames}:s={OUT_W}x{OUT_H}:fps={FPS},format=yuv420p[bg]")
    inputs = ["-loop", "1", "-i", str(image_path), "-loop", "1", "-i", str(subtitle_path)]
    if overlay_path:
        filter_complex = (f"{bg};[1:v]format=rgba[sub];[2:v]format=rgba,fade=t=in:st=0.3:d=0.6:alpha=1[num];"
                           f"[bg][sub]overlay=0:0[bgsub];[bgsub][num]overlay=0:0,format=yuv420p[v]")
        inputs += ["-loop", "1", "-i", str(overlay_path), "-i", str(audio_path)]
        maps = ["-map", "[v]", "-map", "3:a"]
    else:
        filter_complex = f"{bg};[1:v]format=rgba[sub];[bg][sub]overlay=0:0,format=yuv420p[v]"
        inputs += ["-i", str(audio_path)]
        maps = ["-map", "[v]", "-map", "2:a"]
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
           *maps, *CLIP_ENCODE, "-t", str(duration), str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)

def stage_clips(data, shot_ids, force):
    clips_dir = CACHE / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    run_log = load_run_log()
    for s in data["shots"]:
        if shot_ids and s["id"] not in shot_ids:
            continue
        out_path = clips_dir / f"{s['id']}.mp4"
        entry = shot_entry(run_log, s["id"])
        if out_path.exists() and not force:
            log(f"clips {s['id']}: cached")
            continue
        audio_path = CACHE / "tts" / f"{s['id']}.mp3"
        subtitle_path = CACHE / "subtitles" / f"{s['id']}.png"
        duration = entry.get("shot_duration")
        if duration is None:
            raise RuntimeError(f"clips {s['id']}: run tts stage first")
        if not subtitle_path.exists():
            raise RuntimeError(f"clips {s['id']}: run overlay stage first, no subtitle cached")
        used_ai_video = s["animate"] and entry.get("source") == "ai_video"
        if used_ai_video:
            build_animated_clip(CACHE / "video" / f"{s['id']}.mp4", audio_path, subtitle_path, duration, out_path)
        else:
            still_path = CACHE / "images" / f"{s['id']}.png"
            overlay_path = CACHE / "images_overlaid" / f"{s['id']}.png" if s["overlay_text"] else None
            still_motion = s["static_motion"] if s["animate"] else s["motion"]
            build_still_clip(still_path, audio_path, subtitle_path, duration, still_motion, out_path, overlay_path)
        entry["clip_source"] = "ai_video" if used_ai_video else "still"
        save_run_log(run_log)
        log(f"clips {s['id']}: built ({entry['clip_source']}, {duration:.2f}s)")

# ---------- concat ----------

def print_shot_table(data, run_log):
    print("\nid    source    duration  overlay")
    print("-" * 40)
    for s in data["shots"]:
        entry = run_log["shots"].get(s["id"], {})
        overlay = "yes" if s["overlay_text"] else "no"
        print(f"{s['id']:<6}{entry.get('clip_source', '?'):<10}{entry.get('shot_duration', 0.0):<10.2f}{overlay}")

def stage_concat(data, shot_ids, force):
    clips_dir = CACHE / "clips"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT / "final.mp4"
    concat_list = CACHE / "concat_list.txt"
    run_log = load_run_log()
    lines, total_duration = [], 0.0
    for s in data["shots"]:
        clip_path = clips_dir / f"{s['id']}.mp4"
        if not clip_path.exists():
            raise RuntimeError(f"concat: missing clip for {s['id']}, run clips stage first")
        lines.append(f"file '{clip_path.resolve()}'")
        total_duration += run_log["shots"][s["id"]]["shot_duration"]
    concat_list.write_text("\n".join(lines))
    try:
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                         "-c", "copy", str(out_path)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        log("concat: stream copy failed, re-encoding instead")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                         *CLIP_ENCODE, str(out_path)], check=True, capture_output=True)
    log(f"concat: wrote {out_path}, total duration {total_duration:.1f}s")
    if not (105 <= total_duration <= 135):
        log(f"WARNING: total duration {total_duration:.1f}s is outside the 105-135s target window")
    print_shot_table(data, run_log)

# ---------- driver ----------

def main():
    parser = argparse.ArgumentParser(description="Build the explainer video from shots.json")
    parser.add_argument("--shots", default=str(ROOT / "shots.json"))
    parser.add_argument("--stages", default=",".join(STAGES), help="comma-separated subset of stages")
    parser.add_argument("--only", default="", help="comma-separated shot ids to target")
    parser.add_argument("--force", action="store_true", help="ignore cache, regenerate everything selected")
    args = parser.parse_args()
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    shot_ids = {s.strip() for s in args.only.split(",") if s.strip()}
    for s in stages:
        if s not in STAGES:
            sys.exit(f"unknown stage: {s}")
    api_key = os.environ.get("POLLINATIONS_KEY", "")
    data = load_shots(args.shots)
    if "concept" in stages:
        stage_concept(data, shot_ids, args.force)
    if "tts" in stages:
        stage_tts(data, shot_ids, args.force)
    if "images" in stages:
        if not api_key:
            sys.exit("POLLINATIONS_KEY is not set")
        stage_images(data, shot_ids, args.force, api_key)
    if "animate" in stages:
        if not api_key:
            sys.exit("POLLINATIONS_KEY is not set")
        stage_animate(data, shot_ids, args.force, api_key)
    if "overlay" in stages:
        stage_overlay(data, shot_ids, args.force)
    if "clips" in stages:
        stage_clips(data, shot_ids, args.force)
    if "concat" in stages:
        stage_concat(data, shot_ids, args.force)

if __name__ == "__main__":
    main()
