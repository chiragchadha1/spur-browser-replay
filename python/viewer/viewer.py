"""
Spur Browser Replay — Viewer service (runs on the host).

Receives rrweb events from the recorder container, stores an append-only history,
fans them out live over SSE, and replays them with rrweb's core Replayer plus a
small custom scrub bar.

Why the core Replayer instead of rrweb-player? The starter used
`new rrwebPlayer.Replayer(...)`, which is wrong twice over: `Replayer` lives on
`window.rrweb`, and the rrweb-player UMD global is a namespace object, not a bare
constructor. On top of that, the rrweb-player v2 Svelte UMD build did not mount its
Replayer in this setup (it rendered the shell but never created the iframe). So we
drive `window.rrweb.Replayer` directly — fewer moving parts, fully under our control,
and it makes the data model obvious: events in, scrubbable DOM replay out.
"""

import json
import queue
import threading
from flask import Flask, request, jsonify, Response

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Append-only history + live subscriber queues. "Store first, then broadcast."
events: list[dict] = []
events_lock = threading.Lock()
subscribers: set[queue.Queue] = set()
subscribers_lock = threading.Lock()


@app.route("/events", methods=["POST"])
def receive_event():
    """Receive a single rrweb event from the browser page (via sendBeacon)."""
    raw = request.get_data(as_text=True)
    if not raw:
        return "", 204
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        app.logger.warning("dropping non-JSON event: %s", exc)
        return "", 400

    with events_lock:
        events.append(event)
        count = len(events)
    app.logger.info("event #%d type=%s", count, event.get("type"))

    _publish(event)
    return "", 204


@app.route("/api/events", methods=["GET"])
def get_all_events():
    """Return the full event history so a late viewer can hydrate."""
    with events_lock:
        return jsonify(list(events))


@app.route("/stream", methods=["GET"])
def stream():
    """Server-Sent Events: replay history, then push every new event live."""
    def gen():
        with events_lock:
            backlog = list(events)
        for event in backlog:
            yield f"data: {json.dumps(event)}\n\n"

        q: queue.Queue = queue.Queue()
        with subscribers_lock:
            subscribers.add(q)
        try:
            while True:
                yield f"data: {json.dumps(q.get())}\n\n"
        finally:
            with subscribers_lock:
                subscribers.discard(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _publish(event: dict) -> None:
    with subscribers_lock:
        targets = list(subscribers)
    for q in targets:
        q.put(event)


@app.route("/healthz")
def healthz():
    return jsonify(ok=True)


@app.route("/demo-target")
def demo_target():
    """A controlled page for the recorder to capture. The scripted driver types,
    clicks (the box slides via CSS), and scrolls — producing rich rrweb mutations."""
    return DEMO_TARGET_HTML


@app.route("/")
def viewer():
    return VIEWER_HTML


DEMO_TARGET_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Demo Target Page</title>
    <style>
      body { font-family: system-ui; margin: 40px; color: #0f172a; }
      input, button { padding: 10px; margin: 8px 0; display: block; font-size: 15px; }
      #box { width: 120px; height: 120px; background: #38bdf8; border-radius: 16px;
             transition: transform 250ms ease, background 250ms ease; }
      #box.moved { transform: translateX(220px) rotate(8deg); background: #34d399; }
      .filler { margin-top: 40px; height: 700px; background:
        linear-gradient(180deg,#e0f2fe,#ecfccb); border-radius: 16px; padding: 20px; }
    </style>
  </head>
  <body>
    <h1>Demo Target</h1>
    <p>The recorder captures DOM/input/scroll mutations on this page as rrweb events.</p>
    <input id="name" placeholder="Type here to create input events" />
    <button id="move" onclick="document.getElementById('box').classList.toggle('moved')">Move box</button>
    <div id="box"></div>
    <div class="filler"><h2>Scroll region</h2><p>The driver scrolls here so the replay has vertical movement.</p></div>
  </body>
</html>"""


VIEWER_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Spur Browser Replay Viewer</title>
    <!-- Served under a neutral name: privacy/ad blockers ship rules that match
         "rrweb.min.js" (rrweb is a session-recording lib), which silently blocks the
         script and leaves the global undefined. -->
    <link rel="stylesheet" href="/static/viewer-core.css" />
    <style>
      body { margin: 0; font-family: Inter, system-ui; background: #020617; color: #e2e8f0; }
      header { padding: 16px 24px; border-bottom: 1px solid #1e293b; display: flex; gap: 18px; align-items: center; }
      .pill { font-size: 13px; padding: 4px 10px; border-radius: 999px; background: #1e293b; }
      main { display: grid; grid-template-columns: 300px 1fr; min-height: calc(100vh - 70px); }
      aside { border-right: 1px solid #1e293b; padding: 20px; }
      #player { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
      #screen { position: relative; width: 100%; height: 600px; background: #fff;
                border-radius: 10px; overflow: hidden; }
      #screen > p { color: #64748b; padding: 20px; }
      #controls { display: none; align-items: center; gap: 12px; }
      #seek { flex: 1; accent-color: #2563eb; }
      .metric { margin: 12px 0; padding: 12px; background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; }
      code { color: #67e8f9; }
      pre { white-space: pre-wrap; background: #0f172a; padding: 12px; border-radius: 10px; max-height: 240px; overflow: auto; font-size: 12px; }
      button, select { background: #2563eb; color: #fff; border: 0; border-radius: 8px; padding: 8px 12px; cursor: pointer; }
      select { background: #334155; }
    </style>
  </head>
  <body>
    <header>
      <strong>Spur Browser Replay Viewer</strong>
      <span class="pill" id="status">connecting…</span>
      <button id="rebuild">Rebuild from history</button>
    </header>
    <main>
      <aside>
        <div class="metric"><strong>Events received</strong><br /><span id="count">0</span></div>
        <div class="metric"><strong>Full snapshot</strong><br /><span id="snap">waiting…</span></div>
        <div class="metric"><strong>How it works</strong><br />Hydrate history, subscribe to live SSE, build rrweb's Replayer once a full snapshot (type 2) arrives, then append live events.</div>
        <h3>Debug log</h3>
        <pre id="log"></pre>
      </aside>
      <section id="player">
        <div id="screen"><p>Waiting for a full snapshot (rrweb event type 2)…</p></div>
        <div id="controls">
          <button id="playpause">Pause</button>
          <input id="seek" type="range" min="0" max="1000" value="0" step="1" />
          <span class="pill" id="time">0.0s / 0.0s</span>
          <select id="speed"><option value="1">1x</option><option value="2">2x</option><option value="4">4x</option></select>
        </div>
      </section>
    </main>

    <script src="/static/viewer-core.min.js"></script>
    <script>
      const events = [];
      let replayer = null, playing = false, raf = null;
      const $ = (id) => document.getElementById(id);

      function log(msg) {
        $('log').textContent = `[${new Date().toLocaleTimeString()}] ${msg}\\n` + $('log').textContent;
      }
      const hasFullSnapshot = () => events.some((e) => e.type === 2);
      const fmt = (ms) => `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
      const total = () => (replayer ? replayer.getMetaData().totalTime : 0);

      function fitScreen() {
        const wrap = $('screen').querySelector('.replayer-wrapper');
        const iframe = $('screen').querySelector('iframe');
        if (!wrap || !iframe) return;
        const sw = $('screen').clientWidth, sh = $('screen').clientHeight;
        const iw = iframe.offsetWidth || 1280, ih = iframe.offsetHeight || 800;
        wrap.style.transformOrigin = 'top left';
        wrap.style.transform = `scale(${Math.min(sw / iw, sh / ih)})`;
      }

      function buildPlayer() {
        // rrweb's Replayer needs >= 2 events and a type-2 full snapshot to paint.
        if (events.length < 2 || !hasFullSnapshot()) return;
        if (typeof rrweb === 'undefined') {
          $('snap').textContent = 'replay engine blocked';
          log('replay engine did not load — disable any content/ad blocker for localhost, then hard refresh');
          return;
        }
        $('screen').innerHTML = '';
        replayer = new rrweb.Replayer(events.slice(), {
          root: $('screen'), skipInactive: true, mouseTail: true, speed: Number($('speed').value) || 1,
        });
        $('controls').style.display = 'flex';
        $('snap').textContent = 'rendered ✓';
        fitScreen();
        startPlayback(0);
        log(`built replayer from ${events.length} events`);
      }

      function tick() {
        if (!replayer) return;
        const t = replayer.getCurrentTime(), tt = total();
        if (tt > 0) $('seek').value = String(Math.min(1000, (t / tt) * 1000));
        $('time').textContent = `${fmt(t)} / ${fmt(tt)}`;
        if (t >= tt) { pausePlayback(); return; }
        if (playing) raf = requestAnimationFrame(tick);
      }
      function startPlayback(offset) {
        replayer.play(offset);
        playing = true; $('playpause').textContent = 'Pause';
        if (raf) cancelAnimationFrame(raf);
        tick();
      }
      function pausePlayback() {
        if (replayer) replayer.pause();
        playing = false; $('playpause').textContent = 'Play';
        if (raf) cancelAnimationFrame(raf);
      }

      $('playpause').onclick = () => {
        if (!replayer) return;
        playing ? pausePlayback() : startPlayback(replayer.getCurrentTime());
      };
      $('seek').oninput = () => {
        if (!replayer) return;
        startPlayback((Number($('seek').value) / 1000) * total());
      };
      $('speed').onchange = () => { if (replayer) replayer.setConfig({ speed: Number($('speed').value) }); };
      $('rebuild').onclick = () => { if (raf) cancelAnimationFrame(raf); replayer = null; buildPlayer(); };
      window.addEventListener('resize', fitScreen);

      function pushEvent(event) { events.push(event); $('count').textContent = String(events.length); }
      function liveEvent(event) {
        pushEvent(event);
        log(`event type=${event.type}`);
        if (!replayer) buildPlayer();
        else replayer.addEvent(event); // extend the timeline live
      }

      async function hydrate() {
        const res = await fetch('/api/events');
        (await res.json()).forEach(pushEvent);
        log(`hydrated ${events.length} historical events`);
        buildPlayer();
      }

      function connectStream() {
        const es = new EventSource('/stream');
        const seen = events.length; let i = 0;
        es.onopen = () => { $('status').textContent = 'live (SSE connected)'; log('SSE connected'); };
        es.onmessage = (m) => { if (i++ < seen) return; liveEvent(JSON.parse(m.data)); };
        es.onerror = () => { $('status').textContent = 'SSE reconnecting…'; };
      }

      hydrate().then(connectStream).catch((e) => { $('status').textContent = 'viewer error'; log(e.message); });
    </script>
  </body>
</html>"""


if __name__ == "__main__":
    print("Viewer on http://0.0.0.0:8000  (events: POST /events, replay: GET /)")
    app.run(host="0.0.0.0", port=8000, threaded=True, debug=False)
