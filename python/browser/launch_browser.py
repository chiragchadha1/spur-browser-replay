"""
Spur Browser Replay — Browser + driver service.

Evolved from the starter `launch_browser.py`. Changes that make it actually work
end-to-end in a two-container setup:

  * EVENT_STREAM_URL / TARGET_URL / RRWEB_URL come from the environment and point
    at the *viewer service name* (http://viewer:8000/...), not localhost. This is
    the Docker networking gotcha: the page runs in the browser container, so
    "localhost" there is the browser container, not the viewer.
  * rrweb is injected from the viewer's vendored copy, so the demo never depends
    on a CDN being reachable at record time.
  * We wait for the viewer to be reachable before navigating (compose start order
    is not readiness).
  * A scripted Playwright driver performs real interactions (type, click, scroll)
    so the replay shows actual behavior instead of an idle snapshot.

Playwright drives Chrome over the Chrome DevTools Protocol, so this is the CDP
control plane; rrweb is the in-page capture plane that ships events back out.
"""

import asyncio
import os
import urllib.request

from playwright.async_api import async_playwright

TARGET_URL = os.getenv("TARGET_URL", "http://localhost:8000/demo-target")
EVENT_STREAM_URL = os.getenv("EVENT_STREAM_URL", "http://localhost:8000/events")
RRWEB_URL = os.getenv("RRWEB_URL", "http://localhost:8000/static/rrweb.min.js")
CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
HEALTH_URL = os.getenv("HEALTH_URL", "http://localhost:8000/healthz")
LOOP_FOREVER = os.getenv("LOOP_FOREVER", "1") == "1"


def wait_for_viewer(url: str, attempts: int = 60, delay: float = 1.0) -> None:
    """Block until the viewer service answers, so we don't navigate too early."""
    import time
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    print(f"[browser] viewer reachable at {url}")
                    return
        except Exception as exc:  # noqa: BLE001 - readiness probe
            print(f"[browser] waiting for viewer ({i + 1}/{attempts}): {exc}")
        time.sleep(delay)
    raise RuntimeError(f"viewer never became reachable at {url}")


async def inject_rrweb(page) -> None:
    """Load rrweb into the page and start recording, shipping events to the viewer."""
    await page.add_script_tag(url=RRWEB_URL)
    await page.wait_for_function("() => !!(window.rrweb && window.rrweb.record)", timeout=10000)
    await page.evaluate(
        """
        (eventUrl) => {
            if (window.__spurRecording) return;
            window.__spurRecording = window.rrweb.record({
                emit: (event) => {
                    // sendBeacon is fire-and-forget and survives page unload.
                    navigator.sendBeacon(eventUrl, JSON.stringify(event));
                },
                maskAllInputs: false,   // demo page has no secrets; show typed text
                recordCanvas: false,
                sampling: { mousemove: 50, scroll: 150 },
            });
        }
        """,
        EVENT_STREAM_URL,
    )
    print("[browser] rrweb recording started")


async def run_demo_interactions(page) -> None:
    """Scripted driver: real interactions that produce a compelling replay."""
    await asyncio.sleep(0.6)
    await page.fill("#name", "")
    await page.type("#name", "hello Spur", delay=90)   # per-keystroke input events
    await asyncio.sleep(0.8)
    await page.click("#move")                            # box slides (DOM mutation)
    await asyncio.sleep(1.0)
    await page.mouse.wheel(0, 400)                       # scroll events
    await asyncio.sleep(0.8)
    await page.evaluate(
        "document.body.insertAdjacentHTML('beforeend', '<p id=\\'marker\\'>captured by rrweb</p>')"
    )
    await asyncio.sleep(0.8)
    await page.mouse.wheel(0, -400)
    await page.click("#move")                            # box slides back
    print("[browser] demo interaction sequence complete")


async def main() -> None:
    wait_for_viewer(HEALTH_URL)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            chromium_sandbox=False,
            args=[
                f"--remote-debugging-port={CDP_PORT}",
                "--remote-debugging-address=0.0.0.0",
                "--remote-allow-origins=*",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        print(f"[browser] navigating to {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="load", timeout=60000)
        await inject_rrweb(page)

        # Drive interactions. Optionally loop so a live viewer keeps seeing motion.
        while True:
            await run_demo_interactions(page)
            if not LOOP_FOREVER:
                break
            await asyncio.sleep(3)

        print("[browser] idling; replay available in the viewer")
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
