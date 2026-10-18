"""Stands in for a Chromium browser: answers on the debugging pipe and records what it is sent.

`FAKE_BROWSER_LOG` is where the record goes, one JSON document per line. `FAKE_BROWSER_COOKIES`
holds the cookies of each `Network.getCookies` answer in turn, the last one repeating.
`FAKE_BROWSER_MODE` changes one behaviour: `no-page`, `closed`, `hangs`, `helper` or `elsewhere`.
"""

import json
import os
import sys
import time
from pathlib import Path


LOG = Path(os.environ["FAKE_BROWSER_LOG"])
COOKIES = json.loads(os.environ.get("FAKE_BROWSER_COOKIES", "[[]]"))
MODE = os.environ.get("FAKE_BROWSER_MODE", "")

TARGETS = [{"targetId": "browser-1", "type": "browser"}, "not a target", {"targetId": "page-1", "type": "page"}]


def record(entry):
    with LOG.open("a") as log:
        log.write(json.dumps(entry) + "\n")


def write(message):
    os.write(4, (message if isinstance(message, bytes) else json.dumps(message).encode()) + b"\0")


def answer(message):
    method, reply = message["method"], {"id": message["id"], "result": {}}
    if method == "Target.getTargets":
        reply["result"] = {"targetInfos": TARGETS[:2] if MODE == "no-page" else TARGETS}
    elif method == "Target.attachToTarget":
        # What a browser sends unasked, and what it never should.
        write({"method": "Target.attachedToTarget", "params": {"sessionId": "page-session-1"}})
        write(b"not json")
        write([message["id"]])
        reply["result"] = {"sessionId": "page-session-1"}
    elif method == "Network.getCookies":
        if MODE == "closed":
            sys.exit(0)
        reply["result"] = {"cookies": COOKIES.pop(0) if len(COOKIES) > 1 else COOKIES[0]}
    elif method == "Browser.close":
        if MODE == "hangs":
            return
        write(reply)
        sys.exit(0)
    else:
        reply = {"id": message["id"], "error": {"code": -32601, "message": "not found"}}
    write(reply)


def main():
    profile = next(arg.partition("=")[2] for arg in sys.argv if arg.startswith("--user-data-dir="))
    if MODE != "elsewhere":
        Path(profile, "Cookies").write_text("what the profile holds of the GitHub sign-in")
    helper = os.fork() if MODE == "helper" else None
    if helper == 0:
        os.close(3)
        os.close(4)
        for beat in range(600):
            LOG.with_suffix(".helper").write_text(str(beat))
            time.sleep(0.05)
        return
    launch = {"argv": sys.argv[1:], "pid": os.getpid(), "helper": helper}
    record({**launch, "stdio": [os.fstat(fd).st_rdev for fd in (0, 1, 2)]})
    buffer = b""
    while chunk := os.read(3, 65536):
        buffer += chunk
        while b"\0" in buffer:
            raw, _, buffer = buffer.partition(b"\0")
            message = json.loads(raw)
            record(message)
            answer(message)


main()
