import logging
import os
import re
import subprocess
import threading
import urllib.request

from . import config

log = logging.getLogger("ripper")


def run(cmd, **kw):
    """Run a command, return CompletedProcess with text output. Never raises on exit code."""
    log.debug("run: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def chown(path):
    try:
        os.chown(path, config.PUID, config.PGID)
    except (PermissionError, OSError):
        pass


def makedirs(path):
    """mkdir -p, chowning each directory we create to PUID:PGID."""
    path = os.path.abspath(path)
    missing = []
    p = path
    while not os.path.exists(p):
        missing.append(p)
        p = os.path.dirname(p)
    for p in reversed(missing):
        os.mkdir(p)
        chown(p)


_bad = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(s, fallback=""):
    s = _bad.sub(" ", (s or "")).strip().strip(".")
    s = re.sub(r"\s+", " ", s)
    return s[:120] or fallback


def notify(title, message):
    if not config.NOTIFY_URL:
        return

    def _send():
        try:
            req = urllib.request.Request(
                config.NOTIFY_URL,
                data=message.encode(),
                headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": "cd"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # notifications are best effort
            log.warning("notify failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()
