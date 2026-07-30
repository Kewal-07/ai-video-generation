# Pipeline Log

## 1. Summary

Video link: TODO, will add once uploaded.

Runtime: 134.1 seconds. Resolution: 854x480 (480p), 30fps.

To reproduce it, put your Pollinations API key in a `.env` file like this,
`POLLINATIONS_KEY=your_key`, then run `python3 pipeline.py`. The whole
video is defined by `shots.json`, and every shot uses a fixed seed based
on its position in the list, so running it again produces the same
result. `run_log.json` records every prompt, seed, and API call I
actually made while building this, so anyone can check my claims against
real data instead of my word for it.

## 2. Workflow

The pipeline runs in seven steps: concept, then tts, then images, then
animate, then overlay, then clips, then concat.

They have to run in that order because each step needs something the one
before it made. The narration audio has to exist before anything else can
know how long a shot should be, since nothing about the video's timing is
hardcoded. The still images have to exist before the animate step can
turn one into a video clip, since the still is used as the starting
frame. And the text overlays have to be drawn before the clips step can
combine them with the background and audio into one file.

Every step also saves its output to disk and skips work that is already
done, unless I explicitly ask it to redo it. That mattered while I was
working on this. When I was adjusting how the camera motion looked, for
example, I could rerun that part as many times as I wanted without
spending any more of the image and video generation budget.

## 3. What each step does

**Concept.** This step turns the voiceover script into a shot by shot
plan, one entry per shot with its own narration, image description, and
camera motion. I wrote this by hand, once, and saved it in `shots.json`
rather than generating it with an AI model every run. The result is 17
shots. Four of them were chosen for real AI video motion: the apple
turning into digits, a surface cracking into a pixel grid, three colored
light beams blending together, and a sunset dissolving into tiles. Six of
them needed an exact number on screen, like `(255, 0, 0)` or
`256 x 256 x 256 = 16,777,216`. I chose to write the shot list by hand
because it is the part of this project that is hardest to check
automatically, the creative direction, and I wanted that decision to
stay something I actually made and can explain, not something regenerated
on every run. The pipeline does check, every time it runs, that all 17
narration lines glued together exactly match the original script word for
word, so a typo would get caught immediately.

**Text to speech.** This step turns each shot's narration into spoken
audio. I used edge-tts, a free tool that runs locally, with one voice
throughout. The shortest line is about 3 seconds, the longest about 17.
Each file's loudness is normalized once, right when it is created, so
volume does not drift up or down as more shots get added later. I picked
edge-tts specifically because it does not compete with the paid image and
video budget, and normalizing early instead of at the very end keeps
every shot sounding equally loud.

**Images.** This step generates one AI background image per shot, all
sharing one visual style so the video reads as one film instead of 17
unrelated pictures. No text or numbers are generated inside the image
itself, those are added separately in the overlay step. All 17 images
generated successfully, but every one of them came through a free backup
image service instead of the main one, because the account I was using
had no paid balance. That also ended up affecting the final resolution of
the video, which I explain in the dead ends section below. I originally
asked for images at twice the final video's resolution so that the
camera motion added later would not make anything look soft.

**Animate.** This step was meant to take the 4 flagged shots and turn
their still image into a few seconds of real AI generated video, using
the still as the starting frame so the visual style would not change.
All 4 attempts failed. Three were rejected immediately for insufficient
account balance, and one did not respond at all and eventually timed out
after five minutes. In every case the pipeline caught the failure, logged
it, and automatically used the plain still image with camera motion
instead, so the video still finished without stopping. I built it this
way because free AI video generation is unreliable, sometimes it costs
money I do not have and sometimes it just hangs, and I wanted the
pipeline to expect that instead of crashing over one bad shot.

**Overlay.** This step draws all the on screen text: a caption of the
narration on every one of the 17 shots, and an exact number label on the
6 shots that need one. Both are drawn with a plain text drawing tool
instead of an image model, and both are saved as separate transparent
images that get placed on top of the background afterward rather than
baked into it. I did it this way because AI image generators are
unreliable at drawing legible text, and a wrong digit in something like
`256 x 256 x 256 = 16,777,216` would be a real, visible mistake. A text
drawing tool gets it exactly right every time. Keeping the text as a
separate layer on top also solved a real problem: the background moves
and zooms in the next step, and text drawn directly onto a background
that later gets zoomed into can end up cropped out of frame. A separate
layer on top stays exactly where it is no matter what the background
underneath is doing.

**Clips.** This step turns each shot's background, audio, and text into
one short video file, all in the exact same format, so they can be joined
at the end without needing to re-encode anything. All 17 shots ended up
as still images with a slow pan and zoom, since none of the AI motion
attempts succeeded. The speed of that camera movement is calculated from
each shot's own length, so a 17 second shot keeps moving the whole way
through instead of stopping early. I built it this way after an earlier
version moved the camera at the same fixed speed on every shot
regardless of length, which meant the longer shots, and several are 12
seconds or more, would finish moving in the first couple of seconds and
then sit still for the rest. That is what made the first version of the
video feel static, and scaling the speed to each shot's actual length
fixed it.

**Concat.** This step joins all 17 clips into the final video, checks
that the total length lands close to the two minute target, and prints a
summary table. The final video came out to 134.1 seconds, inside the 105
to 135 second range I was aiming for. All 17 clips joined without
needing to re-encode anything, since they were already saved in identical
formats. Joining pre-matched clips this way is fast and does not lose any
quality, but it only works if every clip already shares the exact same
resolution, frame rate, and audio format, which is what the clips step
guarantees.

## 4. Iteration

All 17 background images were accepted on the first generation. I did
not need to reroll any of them.

The camera motion went through two versions. The first moved every shot
at the same fixed speed, which looked static on the longer shots for the
reason explained above. The second version scales the speed to each
shot's own length and allows a slightly wider zoom range, which reads as
noticeably more alive across shots of very different lengths.

Subtitles were added after the video was already working from start to
finish, as a deliberate readability improvement rather than something the
original plan was missing. Adding them also exposed a real layout bug.
The number label used on 6 shots was pinned to a fixed height on screen,
and it could overlap a subtitle that wrapped onto three or four lines. I
fixed that by having the number label always position itself just above
wherever that particular shot's subtitle actually ends, instead of at a
fixed spot.

## 5. Dead ends

I assumed the API key I was using could generate both images and video
for free, based on how the models were listed in Pollinations' own
catalog. Neither model was flagged as requiring a paid account, so I
expected both to work. What actually happened was that every image
request got rejected for insufficient balance on the main service, but
then succeeded instantly through a free backup service instead. Every
video request also got rejected for insufficient balance, except for one,
which did not error out at all and simply hung until it timed out five
minutes later. My hypothesis is that not being marked as paid only in the
catalog means a model does not require a subscription, not that it is
free to call. Each call still has a small cost attached, and video costs
roughly 500 times what an image does, so an account with a zero balance
can afford some images through the free fallback but cannot afford even
one video clip. I checked exactly what that free backup image service
was actually returning and found that it ignores the resolution I
requested and always returns a smaller, fixed size instead. I resolved
this by lowering the final video's resolution from 720p to 480p, which
the assignment explicitly allows, so that smaller image would not need
to be stretched larger than it actually is. All 4 video generation
attempts now fall back to a still image automatically, and the pipeline
finishes cleanly either way.

The second dead end I have not been able to fix. After adding subtitles,
I watched the finished video and noticed that at some of the cuts, the
new shot's picture and subtitle arrive a little after that shot's audio
has already started. It is subtle, not a full freeze and not a wrong
caption, but the narration for the next line begins just slightly before
you see the slide that goes with it. My hypothesis is that each shot's
video and text overlay are combined using an ffmpeg filter that lays the
caption image on top of the moving background, and that filter step can
add a small delay to the video stream that the audio track, which passes
through unfiltered, does not get. In theory both start together, but in
practice they may drift by a frame or two. I checked whether the picture
itself was jittering from frame to frame within a single shot, and it was
not, that part is smooth. I also checked the frame right after every one
of the 16 cuts in the finished video to confirm the correct subtitle text
was showing each time, and it was, in all 16 cases. Neither check was
precise enough to catch a lag of a fraction of a second, since that kind
of thing only shows up while the video is actually playing, not in a
still frame. I decided not to spend more time chasing a fix I had not
confirmed, and I am documenting it honestly here instead of claiming it
is resolved.

## 6. Not AI-generated

The slow pan and zoom camera motion on all 17 shots was built with
ffmpeg, a video editing tool, not AI. The 6 number labels and 17
subtitle captions were drawn with a plain text drawing tool, not AI. No
music was added. No prompts were hand edited after the original design
pass, the shot list was written once and left alone. No image or video
generations were rerolled, the first result for every shot was used as
is.

## 7. Evaluation

The final video is 134.1 seconds, inside the 105 to 135 second target.

I checked two of the six on screen numbers directly against the
rendered video, `8-bit -> 2^8 = 256` and `256 x 256 x 256 = 16,777,216`,
and both render correctly. I still need to check the remaining four,
`0 - 255`, `(255, 0, 0)`, `(0, 0, 0)`, and `(255, 255, 255)`, myself
before submitting.

I checked all 16 cut points in the finished video, and every one showed
the correct subtitle for its own shot right after the cut, with nothing
left over from the previous shot.

Whether all 17 shots genuinely look like one consistent film is my own
call to make. Every image prompt shares one style description, but I am
the one who watched the whole thing start to finish, so that judgment is
mine.
