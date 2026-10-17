"""
Renders the README images and the promo frames from the real app pages, in headless Chrome at 2x.

Needs, from the project folder:
  python -m http.server 5174 --bind 127.0.0.1
  APPDATA=<a demo folder> python -m fawa2ly.server --idle --port 8799 --allow-origin http://127.0.0.1:5174
(the engine fills the setup screen and Settings; use a demo APPDATA so no real CV or key shows).

  python docs/media/source/capture.py shots            # docs/media/*.png
  python docs/media/source/capture.py promo OUT_DIR             # MP4 frames, 30 fps
  python docs/media/source/capture.py promo OUT_DIR 15 "&gif"   # GIF frames, still background
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from websockets.sync.client import connect

BASE = "http://127.0.0.1:5174"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333


class Page:
    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="fawa2ly-capture-")
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
                                      f"--user-data-dir={self.profile}", "--hide-scrollbars",
                                      "--no-first-run", "--no-default-browser-check", "about:blank"])
        for _ in range(50):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list"))
                ws = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        self.ws = connect(ws, max_size=None)
        self.n = 0
        self.lang_script = None
        self.call("Page.enable")             # scripts added for new documents need it

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r))
        return r.get("result", {}).get("value")

    def open(self, url, w, h, scale=2, transparent=True, wait=1.2, lang="en"):
        # i18n.js translates while the page parses, from the language saved last; a cloned panel
        # cannot be turned back, so each page is told its language before any script runs
        if self.lang_script:
            self.call("Page.removeScriptToEvaluateOnNewDocument", identifier=self.lang_script)
        self.lang_script = self.call("Page.addScriptToEvaluateOnNewDocument",
                                     source=f"try {{ localStorage.setItem('ui-lang', '{lang}') }} catch (e) {{}}")["identifier"]
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=scale,
                  mobile=False)
        self.call("Emulation.setDefaultBackgroundColorOverride",
                  color={"r": 0, "g": 0, "b": 0, "a": 0 if transparent else 1})
        self.call("Page.navigate", url=url)
        time.sleep(wait)
        self.eval("document.fonts.ready.then(() => true)")

    def demo(self, state):
        self.eval(open(os.path.join(HERE, "demo-state.js"), encoding="utf-8").read())
        self.eval(f"fawaDemo(document, {json.dumps(state, ensure_ascii=False)})")

    def save(self, path):
        time.sleep(0.25)
        data = self.call("Page.captureScreenshot", format="png")["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        print("wrote", os.path.relpath(path))

    def close(self):
        self.ws.close()
        self.proc.terminate()


def examples(page):
    page.open(f"{BASE}/app/src/index.html?port=1", 460, 400)
    page.eval(open(os.path.join(HERE, "demo-state.js"), encoding="utf-8").read())
    return page.eval("fawaExamples")


def shots():
    page = Page()
    ex = examples(page)
    overlay = f"{BASE}/app/src/index.html"
    try:
        page.open(f"{overlay}?port=8799", 460, 880, wait=2.5)
        page.save(os.path.join(OUT, "overlay-setup.png"))
        for name, h in (("live", 760), ("thinking", 470), ("arabic", 640), ("ended", 640)):
            page.open(f"{overlay}?port=1", 460, h, lang=ex[name].get("lang", "en"))
            page.demo(ex[name])
            page.save(os.path.join(OUT, f"overlay-{name}.png"))
        for group in ("call", "you", "calls", "model", "app"):
            page.open(f"{BASE}/app/src/settings.html?port=8799&section={group}", 1100, 760,
                      transparent=False, wait=2.5)
            page.save(os.path.join(OUT, f"settings-{group}.png"))
        for scene, (w, h) in (("hero", (1280, 640)), ("themes", (1280, 760)), ("how", (1280, 560))):
            page.open(f"{BASE}/docs/media/source/stage.html?scene={scene}", w, h, transparent=False, wait=2.5)
            page.eval("window.stageReady")
            page.save(os.path.join(OUT, f"{scene}.png"))
    finally:
        page.close()


def promo(out_dir, fps=30, scale=2, query=""):
    os.makedirs(out_dir, exist_ok=True)
    page = Page()
    try:
        page.open(f"{BASE}/docs/media/source/promo.html?still{query}", 960, 540, scale=scale, transparent=False, wait=3)
        page.eval("window.promoReady")
        total = page.eval("PROMO_SECONDS")
        frames = int(total * fps)
        for i in range(frames):
            page.eval(f"seek({i / fps})")
            data = page.call("Page.captureScreenshot", format="png")["data"]
            with open(os.path.join(out_dir, f"f{i:04d}.png"), "wb") as f:
                f.write(base64.b64decode(data))
        print(f"{frames} frames in {out_dir}")
    finally:
        page.close()


if __name__ == "__main__":
    if sys.argv[1] == "shots":
        shots()
    elif sys.argv[1] == "promo":
        # promo OUT_DIR [fps] [query]: the GIF uses 15 fps and "&gif" (still background)
        promo(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 30, query=sys.argv[4] if len(sys.argv) > 4 else "")
