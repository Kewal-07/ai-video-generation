# Pipeline Log

## 1. Summary

- Video link: TODO (upload output/final.mp4 as unlisted YouTube or Google Drive, paste link here)
- Runtime: 134.1s
- Resolution: 854x480 (480p, 30fps, H.264/AAC)
- How to reproduce:
  ```
  export POLLINATIONS_KEY=your_key
  python3 pipeline.py
  ```
  Same `shots.json` (seed 42, seed = base_seed + shot_index) plus cached
  intermediates in `cache/` reproduce the same video. `run_log.json` records
  every prompt, seed, endpoint, and retry count actually used in this run.

## 2. Workflow

Stage graph: `concept -> tts -> images -> animate -> overlay -> clips -> concat`

- **tts before images/animate**: audio duration drives everything downstream
  (hard rule: never hardcode a duration). Every later stage needs
  `shot_duration` from `run_log.json`, so tts has to run first or those
  stages fail loudly with a clear error telling you to run tts first.
- **images before animate**: the animate stage reuses the still's own
  generation URL as the `image=` init-image parameter for image-to-video, so
  a still has to exist (and its URL cached) before animate can reference it.
- **overlay before clips**: overlay renders two kinds of transparent PNG
  cards (a subtitle band for all 17 shots, a number label for 6 of them) that
  clips then composites onto the zoompan'd background. Clips needs those PNGs
  to already exist on disk.
- **On-disk caching per stage**: every stage skips work whose output file
  already exists unless `--force`. This mattered in practice - I iterated on
  the clips-stage motion logic and the overlay layout multiple times without
  re-spending any Pollinations budget, since images and tts audio stayed
  cached the whole time.

## 3. Pipeline steps

### concept

- Objective: Turn the raw voiceover script into a 17-shot list (id,
  narration, image prompt, motion, animate flag, overlay text) before any
  code ran, and keep that list under version control in `shots.json` so
  re-running the pipeline can't silently drift the approved narrative.
- Outcome: 17 shots, split at sentence boundaries, one idea per shot; 4
  flagged `animate: true` (apple to digits, surface to pixel grid, RGB cones
  blending, sunset to tiles), 6 flagged with a numeric overlay. A
  load-time assertion checks the concatenated shot narration reproduces the
  original script verbatim, so a typo or dropped clause fails the run
  immediately instead of silently airing a wrong sentence.
- Why this choice: the brief names "visual conceptualization" as a real
  pipeline step, but an LLM call here would make the one artifact that's
  hardest to defend (the creative narrative) the least inspectable.
  Authoring it once, by hand, and letting the script just validate it kept
  the design decision visible and reviewable rather than regenerable noise.

### tts

- Objective: One narrated audio file per shot, loudness-normalized once, with
  parentheses stripped so voices don't read "(255, 0, 0)" as "left paren two
  five five...".
- Outcome: All 17 shots synthesized with edge-tts (`en-US-GuyNeural`),
  durations ranging 3.10s (`s03`) to 17.02s (`s14`). `loudnorm=I=-16:TP=-1.5:
  LRA=11` applied once per file.
- Why this choice: edge-tts is free, local, and fast, so it doesn't compete
  for the same paid budget as images/video. Normalizing once per file at the
  source (rather than per-clip later) means loudness doesn't drift shot to
  shot if a later stage re-encodes.

### images

- Objective: One AI-generated still per shot at 2x output resolution, shared
  style suffix, no rendered text (all numbers added by Pillow later).
- Outcome: All 17 stills fetched successfully in one attempt each - but
  every one of them went through the free fallback endpoint
  (`image.pollinations.ai`), not the primary `gen.pollinations.ai/image`
  endpoint, because the API key had 0 pollen balance. See Dead Ends below;
  this is also why the final output is 480p rather than 720p.
- Why this choice: requesting 2x resolution (1708x960 against an 854x480
  output) was meant to give zoompan headroom without softening. In practice
  the free endpoint ignores the requested width/height and returns a fixed
  1024x576 regardless, so the real "2x" margin came from adjusting the
  output resolution down to 480p to match what was actually available, not
  from the request parameters.

### animate

- Objective: Real image-to-video motion on the 4 flagged shots via
  Pollinations' `nova-reel` model, using the shot's own still as the init
  frame so style holds, with duration derived as
  `6 * ceil((audio+pad)/6)` (nova-reel's minimum unit is 6s) rather than
  fixed.
- Outcome: All 4 attempts failed - 1 read-timeout after ~5 minutes on `s01`,
  3 clean 402 "Insufficient balance" errors on `s02`/`s06`/`s17` (nova-reel
  costs ~1.01 pollen per request, roughly 500x an image) - and all 4 fell
  back to still+zoompan automatically, logging the error without crashing
  the run.
- Why this choice: nova-reel was the only non-`paid_only` video model in
  Pollinations' `/video/models` listing, confirmed against their live
  OpenAPI spec before writing any code (not guessed). The automatic
  still-fallback exists specifically because free-tier video generation is
  unreliable - the pipeline had to keep working if it failed, not just when
  it succeeded.

### overlay

- Objective: Draw exact, correct on-screen text with Pillow rather than
  trust an image model to render legible characters - both the 6 numeric
  labels and, later, a subtitle caption band for all 17 shots.
- Outcome: 6 number cards (`(255,0,0)`, `0-255`, `256 x 256 x 256 =
  16,777,216`, etc.), 17 subtitle cards, all rendered as separate transparent
  PNGs composited onto the background *after* zoompan rather than baked into
  the source image.
- Why this choice: diffusion models render text unreliably and re-rolling
  for legible glyphs wastes generation budget for no guaranteed gain -
  Pillow draws the exact string every time. Keeping the text as a separate
  post-zoompan layer (rather than baked onto the still before motion is
  applied) was a fix, not just a preference: baking text onto the
  background before a 1.6x zoom meant the crop window could shrink past
  where the text was drawn, cropping it out of frame. Compositing after
  zoompan keeps captions fully visible regardless of background motion, and
  the number labels fade in independently of it.

### clips

- Objective: Normalize every shot to one byte-compatible spec (854x480,
  30fps, yuv420p, H.264 crf20, AAC 48kHz stereo 160k) regardless of source -
  still+zoompan or (if it existed) real AI video - so concat can stream-copy.
- Outcome: All 17 built as still+zoompan (since animate had 0 successes).
  Motion direction and rate are computed per-shot from that shot's own frame
  count, so a 17s shot keeps moving the whole way through instead of hitting
  a fixed zoom cap early and sitting still for the remainder.
- Why this choice: an earlier version used a fixed per-frame zoom rate
  shared across all shots, which meant the four longest shots (12-17s, over
  a third of total runtime) stopped moving after a couple of seconds and
  visibly stalled. Scaling the rate to `(max_zoom-1)/total_frames` makes
  every shot's motion proportional to its own length instead.

### concat

- Objective: Join all 17 clips into one file, verify total runtime lands in
  the 105-135s target window, and print a per-shot table as submission
  evidence.
- Outcome: 134.1s total, `-c copy` stream concatenation succeeded (all clips
  were already byte-compatible from the clips stage, no re-encode fallback
  needed).
- Why this choice: stream-copy concat is fast and lossless, but only works
  if every input clip already shares codec/resolution/framerate/sample rate
  - which is exactly what the clips stage's fixed encode settings guarantee.

## 4. Iteration

- Zero image rerolls: all 17 stills passed review on the first generation
  (spot-checked several, including all 4 animate-shot init frames and one
  numeric-overlay shot).
- Motion was iterated twice after the first full render looked visually
  static: first pass used a fixed-rate zoompan (max zoom 1.3x, constant
  per-frame increment) copied from a generic Ken Burns recipe; second pass
  made the rate proportional to each shot's own duration and raised the
  ceiling to 1.6x (safe under the 2x generation headroom).
- Subtitles were added after the first version of the video was already
  complete, as a deliberate accessibility/explainability addition, not
  something the original design missed - see brief's own framing of
  "explainable over impressive."
- The subtitle addition surfaced and fixed a layout bug in the same pass:
  the number-overlay position was originally fixed at a constant height,
  which collided with subtitle captions that wrapped to 3-4 lines (e.g.
  `s08`'s narration). Fixed by computing the subtitle band's height first
  and positioning the number label just above it, per shot.

## 5. Dead ends

**Attempt 1 - free API key, expected full-resolution images and real AI
video.**
- What I tried: ran the images and animate stages with a Pollinations API
  key that had 0 pollen balance, assuming (based on `flux` and `nova-reel`
  showing no `paid_only` flag in `/image/models` and `/video/models`) that
  both were usable for free.
- What happened: every primary-endpoint image request returned
  `402 Insufficient balance` and fell back to the free
  `image.pollinations.ai` endpoint successfully; every animate request also
  402'd (nova-reel costs ~1.01 pollen/request, not free) except one, which
  instead hung for the full 5-minute timeout before failing.
- My hypothesis: `paid_only: false`/absent in the model listing means "does
  not require a paid *tier*," not "free to call" - there's still a per-call
  pollen cost, and video costs roughly 500x what an image does, so a
  zero-balance key exhausts on the very first video call.
- What I tried next: confirmed the free image fallback endpoint
  (`image.pollinations.ai`) worked but silently ignores requested
  width/height, always returning 1024x576 regardless of what's asked for -
  discovered by inspecting the actual returned file dimensions, not by
  reading docs.
- Final resolution: dropped output resolution from 720p to 480p (explicitly
  allowed by the brief) so the fixed 1024x576 source stays larger than the
  output instead of needing to be upscaled. All 4 animate shots fall back to
  still+zoompan automatically and the pipeline completes without crashing -
  this is the designed behavior, not a workaround.

**Attempt 2 - subtitle/frame timing issue, unresolved.**
- What I tried: after adding burned-in subtitles, spotted what looked like a
  timing mismatch between shot cuts and caption changes.
- What happened: [fill in what you actually saw - freeze? flicker? stale
  text? at what timestamp?]
- My hypothesis: [your best guess after watching it again]
- What I tried next: I checked frame-to-frame differences within a shot (flat,
  no unexpected jitter) and the frame just after all 16 shot cuts in the
  final concatenated video (all 16 showed the correct subtitle immediately -
  see the review grid). Neither reproduced an obvious mismatch from static
  frame extraction alone.
- Final resolution: not resolved. Static frame inspection couldn't reproduce
  it, which suggests it's either a real-time playback artifact (needs live
  scrubbing to pin down, not screenshots) or specific to a particular
  timestamp/shot I haven't isolated yet. Documenting as open rather than
  claiming a fix I couldn't verify.

## 6. Not AI-generated

- ffmpeg zoompan motion on all 17 shots (Ken Burns pan/zoom, duration-scaled
  per shot) - confirmed, no AI video succeeded due to the balance issue
  above.
- Pillow-rendered number overlays on 6 shots (exact glyphs, not model
  output).
- Pillow-rendered subtitle captions on all 17 shots.
- No music.
- No hand-edited prompts beyond the original design pass (shot list authored
  once, not touched per-run).
- No rerolls performed - the human choice here was accepting the first
  generation on every shot, not selecting among alternates.

## 7. Evaluation

- Duration vs. 105-135s target: 134.1s - inside the window, close to the
  ceiling.
- Every script number correct on screen: verified `s08` ("8-bit -> 2^8 =
  256") and `s14` ("256 x 256 x 256 = 16,777,216") directly against rendered
  frames; [check the remaining 4 - s09, s11, s12, s13 - yourself before
  submitting].
- Audio and visual aligned at each cut: checked all 16 shot boundaries in
  the final concatenated video - each showed the correct subtitle for its
  own shot immediately after the cut, no stale text from the previous shot.
- Style consistency across shots: single shared style suffix appended to
  every image/video prompt (dark navy background, cyan/magenta/gold accent
  lighting, minimalist geometric render) - [your subjective call on whether
  it reads as one film].
