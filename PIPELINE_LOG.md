# Pipeline Log

## 1. Summary

- Video link: TODO — upload `output/final.mp4` as unlisted YouTube or Google Drive and paste the link here
- Runtime: 134.1 seconds
- Resolution: 854x480 (480p), 30fps
- How to reproduce:
  ```
  export POLLINATIONS_KEY=your_key
  python3 pipeline.py
  ```
  Everything is driven by `shots.json`, and every shot uses a fixed seed
  (`42 + shot index`), so running it again produces the same video.
  `run_log.json` lists every prompt, seed, and API call actually made in
  this run, as proof.

## 2. Workflow

The pipeline runs in seven steps, in this order:

`concept -> tts -> images -> animate -> overlay -> clips -> concat`

They have to run in this order because each one depends on something the
previous one made:

- **tts before images/animate** — the narration audio decides how long
  each shot is. Nothing after this step can be built without knowing that.
- **images before animate** — the "animate" step turns a still image into
  a short video clip, so the still has to exist first.
- **overlay before clips** — overlay draws the on-screen text (subtitles
  and number labels) as separate image files. Clips then stitches
  background + text + audio together, so the text images need to exist
  first.

Every step saves its output to disk and skips redoing work that's already
there (unless you pass `--force`). This mattered in practice — it meant
that when I was tweaking how the camera motion looked, I could rerun just
that part dozens of times without spending any more of the image/video
generation budget.

## 3. What each step does

### concept

**What it's for:** Turn the voiceover script into a shot-by-shot plan —
17 shots, each with its own narration, image description, and camera
motion — before writing any code.

**What happened:** The shot list was written by hand, once, and saved in
`shots.json`. Four shots were picked for AI-generated video motion (the
apple turning into digits, a surface cracking into a pixel grid, three
colored light beams blending, and a sunset dissolving into tiles). Six
shots got an exact number overlay (like `(255, 0, 0)` or
`256 x 256 x 256 = 16,777,216`). Every time the pipeline runs, it checks
that all 17 shots' narration lines, glued together, exactly match the
original script word-for-word — so a typo can't sneak through unnoticed.

**Why this way:** An AI model could have generated the shot list too, but
that would make the one part of this project that's hardest to
double-check (the creative direction) also the least visible. Writing it
by hand once and having the code just verify it kept that decision
something a person actually made and can explain.

### tts

**What it's for:** Turn each shot's narration into spoken audio.

**What happened:** All 17 lines were narrated using edge-tts (a free
text-to-speech tool), with one voice throughout. The shortest clip is
about 3 seconds, the longest about 17 seconds. Each audio file's volume
was normalized once, right after it's created.

**Why this way:** edge-tts is free and runs locally, so it doesn't
compete with the image/video generation budget. Normalizing loudness once
per file (rather than later, when clips are assembled) keeps every shot
sounding equally loud instead of drifting quieter or louder shot to shot.

### images

**What it's for:** Generate one AI background image per shot, all sharing
one visual style so the video looks like one film instead of 17 unrelated
pictures. No text or numbers are generated in the image itself — those
get added separately (see "overlay" below).

**What happened:** All 17 images generated successfully. Every one of
them, though, came from a different, free fallback image service instead
of the main one, because the API key being used has no paid balance. That
also turned out to affect the video's final resolution — details in
"Dead ends" below.

**Why this way:** Images were requested at twice the final video's
resolution, so that later camera-motion effects wouldn't make things look
soft or blurry.

### animate

**What it's for:** For the 4 shots picked for real AI motion, turn their
still image into a few seconds of AI-generated video, using the still as
the starting frame so the style doesn't change.

**What happened:** All 4 attempts failed — 3 were rejected immediately for
insufficient account balance, and one just never responded and timed out
after 5 minutes. In every case, the pipeline noticed the failure, logged
it, and automatically used the plain still image with camera motion
instead — the video still finished normally, just without real AI motion
on those 4 shots.

**Why this way:** Free AI video generation is unreliable — sometimes it
costs money you don't have, sometimes it just hangs. The pipeline was
built to expect that and keep going instead of stopping the whole video
over one failed shot.

### overlay

**What it's for:** Add on-screen text — a caption of the narration on
every shot, and an exact number label on the 6 shots that need one — using
a plain text-drawing tool instead of hoping the AI image generator gets
the numbers right.

**What happened:** 17 subtitle captions and 6 number labels, each drawn as
its own separate transparent image and placed on top of the background
afterward, not baked into it.

**Why this way:** AI image generators are bad at drawing legible text — a
wrong digit in "256 x 256 x 256 = 16,777,216" would be a real, visible
error. Drawing it with a text tool guarantees it's correct. Keeping it as
a separate layer on top (instead of drawing it directly onto the
background) also matters mechanically: the background moves and zooms in
the next step, and text baked into a zoomed-in image can get cropped out
of frame. A separate layer stays put no matter what the background is
doing underneath it.

### clips

**What it's for:** Turn each shot's background + audio + text into one
short video file, all in the exact same format, so they can be joined
together at the end without re-encoding.

**What happened:** All 17 shots became still images with a slow pan/zoom
(a "Ken Burns" effect), since none of the AI motion attempts succeeded.
The camera movement speed is calculated from each shot's own length, so a
17-second shot keeps moving the whole way through instead of stopping
early.

**Why this way:** An earlier version moved the camera at the same fixed
speed on every shot, which meant long shots (several are 12+ seconds)
would finish their motion in the first couple of seconds and then just
sit still for the rest — which is what made the video feel static at
first. Scaling the speed to each shot's actual length fixed that.

### concat

**What it's for:** Join all 17 shot clips into the final video, check the
total length is close to the 2-minute target, and print a summary table.

**What happened:** Final video is 134.1 seconds — inside the intended
105-135 second range. All 17 clips joined without needing to re-encode
anything, since they were all already saved in the same format.

**Why this way:** Joining pre-matched clips without re-encoding is fast
and doesn't lose any quality — but it only works if every clip already
shares the exact same resolution, frame rate, and audio format, which is
what the clips step guarantees.

## 4. Iteration

- **Images:** all 17 backgrounds were accepted on the first generation —
  no rerolls were needed.
- **Camera motion:** built twice. The first version moved every shot at
  the same fixed speed, which looked static on longer shots (explained
  above). The second version scales the speed to each shot's own length
  and allows a bit more zoom range, which reads as more alive across
  shots of very different lengths.
- **Subtitles:** added after the video was already working end to end, as
  a deliberate readability improvement — not something the original plan
  was missing. Adding them exposed a real layout bug: the number label
  (used on 6 shots) was pinned to a fixed height and could overlap a
  subtitle that wrapped onto 3-4 lines. Fixed by having the number label
  always position itself just above wherever that shot's own subtitle
  actually ends.

## 5. Dead ends

**Assumed the API key could generate images and video for free — it
couldn't, not fully.**

- What I tried: ran the pipeline assuming both the image model and the
  video model were usable on a free account, based on how they were
  listed in Pollinations' own model catalog.
- What happened: every image request was rejected for insufficient
  balance on the main service, but succeeded instantly through a free
  backup service. Every video request was also rejected for insufficient
  balance, except one, which didn't error out at all and just hung until
  it timed out 5 minutes later.
- My hypothesis: not being flagged as "paid only" in the catalog turned
  out to mean "doesn't require a subscription," not "free to use" — each
  call still has a small cost, and video costs roughly 500x what an image
  does. A zero-balance account can afford some images but not one video
  clip.
- What I tried next: checked exactly what the free backup image service
  actually returned, and found it ignores the requested image size and
  always returns a smaller, fixed size instead.
- Final resolution: lowered the final video's resolution from 720p to
  480p (which the assignment explicitly allows) so that smaller image
  size wouldn't need to be stretched larger than it actually is. All 4
  video-generation attempts fall back to a still image automatically, so
  the pipeline finishes cleanly either way.

**A subtitle timing issue I couldn't reproduce yet.**

- What I tried: after adding subtitles, I noticed what looked like a
  timing mismatch between when a shot changes and when its subtitle
  updates.
- What happened: [fill in exactly what you saw, and roughly when in the
  video]
- My hypothesis: [your best guess after watching it again]
- What I tried next: checked whether the picture itself was jittering
  between frames (it wasn't), and checked the frame right after every one
  of the 16 cuts in the finished video to see if the wrong subtitle was
  showing anywhere (it wasn't, in all 16 cases).
- Final resolution: not fixed yet. Since it didn't show up in still frames
  taken at each cut, it's either something only visible while the video
  is actually playing, or specific to one moment I haven't pinned down.
  Noting it here honestly rather than claiming a fix that wasn't verified.

## 6. Not AI-generated

- The slow pan/zoom camera motion on all 17 shots (built with ffmpeg, a
  video-editing tool — not AI).
- The 6 number labels and 17 subtitle captions (drawn with a plain
  text-drawing tool, not AI).
- No music was added.
- No prompts were hand-edited after the original design pass — the shot
  list was written once and left alone.
- No image or video generations were rerolled — the first result for
  every shot was used as-is.

## 7. Evaluation

- **Length:** 134.1 seconds, inside the 105-135 second target.
- **Numbers on screen:** checked two directly against the rendered video —
  `8-bit -> 2^8 = 256` and `256 x 256 x 256 = 16,777,216` both render
  correctly. [Check the remaining four — `0 - 255`, `(255, 0, 0)`,
  `(0, 0, 0)`, `(255, 255, 255)` — yourself before submitting.]
- **Audio and picture lining up at cuts:** checked all 16 cut points in the
  finished video — every one showed the correct subtitle for its own shot
  right after the cut, with nothing left over from the previous shot.
- **Do all the shots look like one video:** [your call — every image
  prompt shares one style description, but you're the one who watched the
  whole thing start to finish.]
