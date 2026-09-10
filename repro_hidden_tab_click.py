#!/usr/bin/env python3
"""repro_hidden_tab_click.py — PROOF HARNESS for the 2026-09-09 harvester root cause.

Launches a THROWAWAY Chrome (temp profile, no proxy) on two `data:` pages and measures
how long a CDP `Input.dispatchMouseEvent(mouseMoved)` takes to be acknowledged on a
foreground tab vs. a background tab, then re-activates the tab. It NEVER contacts
target.com and NEVER touches an account profile. Safe to run any time:

    python repro_hidden_tab_click.py

Expected (measured 2026-09-09 on this box, Chrome 152):
  foreground mouseMoved            0-16 ms
  after Target.createTarget        first tab visibilityState=hidden, evaluate 0 ms,
                                   mouseMoved ~5,015-5,032 ms  (Chromium kMaxRafDelay)
  press/release on the hidden tab  0 ms (blocking events, not rAF-aligned)
  Target.activateTarget (own session) ~63 ms -> visible, mouseMoved 0 ms again
  human_click on a hidden tab       HarvestTabNotPainting at move #1 (~5,014 ms)
"""
sys.path.insert(0, r"C:\Users\elric\Desktop\testScraper")
import zendriver as uc
from zendriver import cdp
from src.session import shape_harvest as h

PAGE1 = 'data:text/html,<html><body style="height:3000px"><h1>tab1</h1><button id=b style="width:200px;height:60px">B</button><script>let n=0;document.addEventListener("mousemove",()=>n++);</script></body></html>'
PAGE2 = 'data:text/html,<html><body><h1>tab2</h1></body></html>'

async def timed(coro, budget=15.0):
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(coro, budget)
        return int((time.monotonic() - t0) * 1000)
    except asyncio.TimeoutError:
        return -1

def move(tab, x=100, y=100):
    return tab.send(cdp.input_.dispatch_mouse_event(type_="mouseMoved", x=x, y=y, pointer_type="mouse"))

async def probe(tab):
    info = await asyncio.wait_for(tab.evaluate(h.VISIBILITY_PROBE_JS, await_promise=True), 5)
    return h.visibility_verdict(info)

async def main():
    browser = await uc.start(headless=False, browser_args=[
        '--window-size=900,700', '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding', '--disable-background-timer-throttling'])
    try:
        tab1 = await browser.get(PAGE1)
        await asyncio.sleep(1.0)
        print("A. tab1 (only tab) probe:", await probe(tab1))
        print("A. tab1 foreground mouseMoved ms:", [await timed(move(tab1, 100 + i, 100)) for i in range(3)])
        tab2 = await browser.get(PAGE2, new_tab=True)      # Target.createTarget = new FOREGROUND tab
        await asyncio.sleep(1.0)
        print("B. after createTarget: tab1 probe:", await probe(tab1), "| tab2 probe:", await probe(tab2))
        print("B. tab1 HIDDEN evaluate ms:", await timed(tab1.evaluate("1+1")))
        print("B. tab1 HIDDEN mouseMoved ms (budget 15 s):", [await timed(move(tab1, 120 + i, 100)) for i in range(2)])
        t0 = time.monotonic()
        await asyncio.wait_for(tab1.send(cdp.input_.dispatch_mouse_event(
            type_="mousePressed", x=100, y=100, button=cdp.input_.MouseButton.LEFT, buttons=1, click_count=1, pointer_type="mouse")), 15)
        await asyncio.wait_for(tab1.send(cdp.input_.dispatch_mouse_event(
            type_="mouseReleased", x=100, y=100, button=cdp.input_.MouseButton.LEFT, buttons=0, click_count=1, pointer_type="mouse")), 15)
        print("B. tab1 HIDDEN press+release ms:", int((time.monotonic() - t0) * 1000))
        print("C. Target.activateTarget from tab1's OWN page session ->", await timed(tab1.activate(), 5), "ms")
        await asyncio.sleep(0.5)
        print("C. tab1 probe after activate:", await probe(tab1), "| tab2 probe:", await probe(tab2))
        print("C. tab1 re-activated mouseMoved ms:", [await timed(move(tab1, 140 + i, 100)) for i in range(3)])
        stats = {}
        try:
            await asyncio.wait_for(h.human_click(tab1, 150, 130, (60, 60), stats=stats, move_abort_ms=2500), 20)
            print("D. human_click on VISIBLE tab:", h.click_stats_summary(stats))
        except Exception as e:
            print("D. human_click on VISIBLE tab raised", type(e).__name__, e, h.click_stats_summary(stats))
        await tab2.activate(); await asyncio.sleep(0.5)
        stats = {}
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(h.human_click(tab1, 150, 130, (60, 60), stats=stats, move_abort_ms=2500), 20)
            print("E. human_click on HIDDEN tab completed:", h.click_stats_summary(stats))
        except Exception as e:
            print(f"E. human_click on HIDDEN tab -> {type(e).__name__}: {e} | {h.click_stats_summary(stats)} | {int((time.monotonic()-t0)*1000)} ms")
        print("F. mousemove events seen by tab1 page:", await tab1.evaluate("n"))
    finally:
        await browser.stop()

asyncio.run(main())
