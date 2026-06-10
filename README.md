
# Spur Browser Replay

A working version of the Spur browser-replay exercise. It captures a live Chrome
session as [rrweb](https://github.com/rrweb-io/rrweb) events over CDP, streams them
to a viewer, and replays the session with a scrub bar.

The recorder runs in a container. The viewer runs on your host. They talk over
`host.docker.internal`.

## Demo
<p align="center" width="100%">
<video src="https://github.com/user-attachments/assets/846a48de-2fa9-4f87-b3db-f94453452c4e" controls width="80%"></video>
</p>

## How I built this

I didn't get this working during the live interview. Afterward I took the Granola
transcript of our session and used it to find where my understanding actually broke
down: the CDP/rrweb boundary, the Docker networking, and how the viewer should be
separated from browser control.

I used Claude Code on a high-reasoning model (Opus 4.8), plus a
"learn" skill that quizzes you on a change until you can explain the why, the what,
and the how (see [LEARN_SKILL.md](LEARN_SKILL.md)). The loop was: form a hypothesis
about why something was blank or failing, instrument the boundary, read the evidence,
then fix it. The "What was broken" section below is the real output of that. Every row
is something I got wrong and then understood.

I'm being upfront that I worked through this with Claude Code (which is how I typically work). What
I want to demonstrate is that I can learn from my knowledge gaps.

## Run it

Viewer on your host:

```bash
pip install -r viewer/requirements.txt
python viewer/viewer.py            # http://localhost:8000
```

Recorder in a container (separate terminal):

```bash
docker compose up --build
```

Open <http://localhost:8000>. The replay builds up live as the recorder types
"hello Spur", clicks the button (the box slides), and scrolls. Use the Pause button
and the slider to scrub. Stop with `docker compose down`.

Start the viewer before the recorder so the first full snapshot lands in a fresh store.

## Architecture

```
   ┌─────────────────────────────────────┐         rrweb events          ┌────────────────────────────┐
   │  recorder  (Docker container)       │   navigator.sendBeacon  →     │  viewer  (host: Flask)     │
   │  • Playwright launches headless     │ ────────────────────────────▶ │  • POST /events  (store)   │
   │    Chrome over CDP                  │                               │  • GET  /api/events        │
   │  • injects rrweb (vendored)         │   GET demo page + rrweb js    │  • GET  /stream  (live SSE)│
   │  • scripted driver: type/click/scroll│ ◀──────────────────────────── │  • GET  /  (rrweb Replayer)│
   └─────────────────────────────────────┘     http://host.docker.internal:8000
```

Two planes. Playwright drives Chrome over CDP, which is the control plane. rrweb runs
inside the page and serializes DOM, input, and scroll mutations, which is the capture
plane. rrweb hands each event to the page, the page beacons it to the viewer, the
viewer stores it first and then fans it out live over SSE, and the browser builds a
scrubbable replay once a full snapshot arrives.

rrweb event types you'll see: `4` Meta, `2` FullSnapshot (the serialized DOM, nothing
replays without it), `3` IncrementalSnapshot (the mutations, input, mouse, scroll).

## What was broken, and how I found and fixed it

| # | What was wrong | How I found it / fixed it |
|---|----------------|---------------------------|
| 1 | Blank replay, no error. | rrweb can't render without a `type-2` full snapshot. I'd been feeding it metadata events, so there was nothing to paint. |
| 2 | `rrwebPlayer is not a constructor`. | The player's UMD global is a namespace object, not a bare constructor, and `Replayer` lives on `window.rrweb`. I tried `rrwebPlayer.default`, but the v2 Svelte build rendered its shell and never mounted the Replayer (no iframe, silent). I proved that in isolation, then drove `new rrweb.Replayer(events, { root })` directly with a small scrub bar I wrote. |
| 3 | The script never loaded: `rrweb is not defined`. | The server logs showed the browser fetched `rrweb.css` but never even requested `rrweb.min.js`. A privacy/ad blocker was blocking it by name, because rrweb is a session-recording library. I serve the bundle under a neutral filename (`viewer-core.min.js`) so the filter rules don't match. |
| 4 | Events never reached the viewer. | The page beaconed to `localhost:8000`, but inside the container that's the container. It has to reach the host at `host.docker.internal`. |
| 5 | An idle snapshot with nothing to replay. | I added a scripted Playwright driver that types, clicks, and scrolls, so the replay shows real behavior. |
| 6 | Chrome never launched at all. | Playwright wanted a Chromium revision that wasn't installed. One line to fix (`playwright install chromium`), but a good reminder to check the boundary before assuming the code is wrong. |

## If this were going to production

In-memory storage would become compressed rrweb chunks in object storage with metadata
in a database. The in-process SSE fan-out would become Redis or a managed pub/sub for
multiple instances. I'd add per-session Chrome limits, backpressure and sampling on
high-volume events, input masking on by default (for sensitive info), and reconnect-from-last-index so a
viewer can rejoin mid-session. rrweb has blind spots (canvas, WebGL, cross-origin
iframes) that I'd cover with a screenshot or video fallback. For mostly-idle agent
sessions, rrweb is far cheaper than video, which is why it's the right default.
