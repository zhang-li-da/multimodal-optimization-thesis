"""Exercise the offline demo in an isolated headless browser, then close it."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

import websocket

from chapter6_demo.v12_2.common import file_sha, save_json

HERE = Path(__file__).resolve().parent


def check(demo, output, edge, ffmpeg=None):
    demo, output = Path(demo).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Keep prior browser evidence; use a new directory.")
    output.mkdir(parents=True)
    temporary_root = HERE / "local-browser"
    temporary_root.mkdir(exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="isolated-", dir=temporary_root)).resolve()
    assert profile.is_relative_to(temporary_root.resolve())
    startup = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    process = subprocess.Popen([str(edge), "--headless=new", "--disable-gpu",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        "--disable-background-networking", "--remote-allow-origins=http://localhost",
        "--remote-debugging-port=0", "--user-data-dir=" + str(profile), "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup)
    ws = None
    events, seq, shots = [], 0, []
    try:
        deadline = time.monotonic() + 30
        portfile = profile / "DevToolsActivePort"
        while not portfile.exists():
            if time.monotonic() > deadline:
                raise RuntimeError("The isolated headless browser did not start.")
            time.sleep(.1)
        port = int(portfile.read_text().splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as response:
            targets = json.load(response)
        target = next(t for t in targets if t["type"] == "page")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], origin="http://localhost", timeout=15)

        def call(method, params=None):
            nonlocal seq
            seq += 1
            ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
            while True:
                result = json.loads(ws.recv())
                if result.get("id") == seq:
                    if "error" in result:
                        raise RuntimeError(result["error"])
                    return result.get("result", {})
                events.append(result)

        def evaluate(expression):
            result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
            if "exceptionDetails" in result:
                raise RuntimeError(result["exceptionDetails"])
            return result.get("result", {}).get("value")

        def shot(name):
            raw = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            path = output / name
            path.write_bytes(base64.b64decode(raw["data"]))
            shots.append({"file": name, "sha256": file_sha(path)})

        call("Page.enable"); call("Runtime.enable"); call("Network.enable")
        call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1080,
                                                     "deviceScaleFactor": 1, "mobile": False})
        call("Page.navigate", {"url": (demo / "index.html").as_uri()})
        deadline = time.monotonic() + 15
        while not evaluate("!!document.querySelector('#run-select')"):
            if time.monotonic() > deadline:
                raise RuntimeError("Offline demo did not initialize.")
            time.sleep(.05)
        checks = {"historical_options": evaluate("document.querySelector('#run-select').options.length")}
        assert checks["historical_options"] == 18
        checks["initial_step"] = evaluate("document.querySelector('#step-label').textContent")
        assert checks["initial_step"] == "共同初始化"
        # Exercise every historical run and every frame, rather than one favorable screenshot.
        checks["historical_frames"] = evaluate("(()=>{let count=0;for(let i=0;i<DATA.historical.runs.length;i++){runIndex=i;render();for(let j=0;j<run().frames.length;j++){frameIndex=j;renderFrame();if(document.querySelectorAll('#lineage [data-node]').length!==run().frames[j].node_ids.length)throw Error('lineage mismatch');count++;}}return count;})()")
        evaluate("setView('history');frameIndex=4;renderFrame()")
        shot("01_historical_replay.png")
        evaluate("document.querySelector('#prev').click()")
        assert evaluate("frameIndex") == 3
        evaluate("document.querySelector('#next').click()")
        assert evaluate("frameIndex") == 4
        evaluate("document.querySelector('#play').click()")
        assert evaluate("timer!==null")
        evaluate("document.querySelector('#play').click()")
        assert evaluate("timer===null")
        evaluate("document.querySelector('#tab-synthetic').click()")
        checks["synthetic_choices"] = []
        for index in range(3):
            evaluate(f"document.querySelector('[data-case=\"{index}\"]').click()")
            checks["synthetic_choices"].append(evaluate("Array.from(document.querySelectorAll('.metric b')).filter((_,i)=>i%2===0).map(x=>x.textContent)"))
        assert checks["synthetic_choices"] == [["p2", "p3"], ["p2", "p2"], ["p2", "p2"]]
        evaluate("caseIndex=0;renderSynthetic()")
        shot("02_synthetic_control.png")
        if evaluate("!!DATA.current"):
            evaluate("document.querySelector('#tab-current').click()")
            checks["current_options"] = evaluate("document.querySelector('#run-select').options.length")
            assert checks["current_options"] == 16
            checks["current_frames"] = evaluate("(()=>{let count=0;for(let i=0;i<DATA.current.runs.length;i++){runIndex=i;render();for(let j=0;j<run().frames.length;j++){frameIndex=j;renderFrame();count++;}}return count;})()")
            evaluate("setView('current');frameIndex=1;renderFrame()")
            shot("03_current_screening.png")
        call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844,
                                                     "deviceScaleFactor": 1, "mobile": True})
        evaluate("setView('history')")
        checks["mobile_page_overflow"] = evaluate("document.documentElement.scrollWidth>window.innerWidth")
        assert not checks["mobile_page_overflow"]
        shot("04_mobile_replay.png")
        exceptions = [e for e in events if e.get("method") == "Runtime.exceptionThrown"]
        requests = [e["params"]["request"]["url"] for e in events if e.get("method") == "Network.requestWillBeSent"]
        network = [url for url in requests if url.startswith(("https://", "http://"))]
        assert not exceptions and not network
        video = None
        if ffmpeg:
            from .video_story import export
            video = export(output / "video", evaluate, call, ffmpeg)
        report = {"passed": True, "demo_html_sha256": file_sha(demo / "index.html"),
                  "browser": call("Browser.getVersion"), "checks": checks,
                  "page_javascript_exceptions": exceptions, "page_network_requests": network,
                  "new_model_calls": 0, "screenshots": shots,
                  "video": {k: v for k, v in video.items() if k != "scenes"} if video else None}
        save_json(output / "browser_check.json", report, immutable=True)
        call("Browser.close")
        return report
    finally:
        if ws:
            ws.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
        # This verified, task-owned profile is the only recursive cleanup target.
        assert profile.is_relative_to(temporary_root.resolve())
        shutil.rmtree(profile, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--edge", type=Path,
                        default=Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"))
    parser.add_argument("--ffmpeg", type=Path, help="Optional binary for a 4m20s captioned walkthrough.")
    args = parser.parse_args()
    result = check(args.demo, args.output, args.edge, args.ffmpeg)
    print(json.dumps({k: v for k, v in result.items() if k != "screenshots"}))


if __name__ == "__main__":
    main()
