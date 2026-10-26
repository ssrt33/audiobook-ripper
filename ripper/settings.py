"""Settings changeable from the web page. Saved to CONFIG_DIR/settings.json.

Environment variables give the starting values; anything saved from the page wins over them.
Paths, drives and file ownership stay in the YAML only.
"""
import json
import os
import re
import threading

from . import config

RIP_MODES = {"normal": [], "fast": ["-Y"], "thorough": ["-z"]}
BITRATES = ["48k", "64k", "96k", "128k"]
CHAPTERS = ["disc", "track", "none"]
STUCK_LIMITS = [0, 5, 10, 20, 30]
STUCK_ACTIONS = ["give_up", "skip"]
REGIONS = ["us", "uk", "ca", "au", "de", "fr", "it", "es", "in", "jp"]

_lock = threading.Lock()
_path = None


def _rip_mode_from_opts(opts):
    for name, flags in RIP_MODES.items():
        if flags == opts:
            return name
    return "normal"


def current():
    return {
        "auto_eject": config.AUTO_EJECT,
        "auto_start": config.AUTO_START_BOOK,
        "keep_rips": config.KEEP_RIPS,
        "bitrate": config.DEFAULT_BITRATE,
        "channels": config.DEFAULT_CHANNELS,
        "chapters": config.DEFAULT_CHAPTERS,
        "rip_mode": _rip_mode_from_opts(config.PARANOIA_OPTS),
        "stuck_limit": config.STUCK_LIMIT,
        "stuck_action": config.STUCK_ACTION,
        "audible_region": config.AUDIBLE_REGION,
        "notify_url": config.NOTIFY_URL,
        "report_email": config.REPORT_EMAIL,
        "smtp_ready": bool(config.SMTP_HOST),
    }


def _apply(data):
    """Validate and apply. Returns the keys that were changed."""
    changed = []
    if "auto_eject" in data:
        config.AUTO_EJECT = bool(data["auto_eject"]); changed.append("auto_eject")
    if "auto_start" in data:
        config.AUTO_START_BOOK = bool(data["auto_start"]); changed.append("auto_start")
    if "keep_rips" in data:
        config.KEEP_RIPS = bool(data["keep_rips"]); changed.append("keep_rips")
    if data.get("bitrate") in BITRATES:
        config.DEFAULT_BITRATE = data["bitrate"]; changed.append("bitrate")
    if "channels" in data and str(data["channels"]) in ("1", "2"):
        config.DEFAULT_CHANNELS = int(data["channels"]); changed.append("channels")
    if data.get("chapters") in CHAPTERS:
        config.DEFAULT_CHAPTERS = data["chapters"]; changed.append("chapters")
    if data.get("rip_mode") in RIP_MODES:
        config.PARANOIA_OPTS = list(RIP_MODES[data["rip_mode"]]); changed.append("rip_mode")
    if "stuck_limit" in data and str(data["stuck_limit"]).isdigit() and int(data["stuck_limit"]) in STUCK_LIMITS:
        config.STUCK_LIMIT = int(data["stuck_limit"]); changed.append("stuck_limit")
    if data.get("stuck_action") in STUCK_ACTIONS:
        config.STUCK_ACTION = data["stuck_action"]; changed.append("stuck_action")
    if data.get("audible_region") in REGIONS:
        config.AUDIBLE_REGION = data["audible_region"]; changed.append("audible_region")
    if "notify_url" in data:
        url = str(data["notify_url"] or "").strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("The alert address must start with http:// or https://")
        config.NOTIFY_URL = url; changed.append("notify_url")
    if "report_email" in data:
        addr = str(data["report_email"] or "").strip()
        if addr and not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", addr):
            raise ValueError("That doesn't look like an email address")
        config.REPORT_EMAIL = addr; changed.append("report_email")
    return changed


def init():
    global _path
    _path = os.path.join(config.CONFIG_DIR, "settings.json")
    if os.path.exists(_path):
        try:
            with open(_path) as f:
                _apply(json.load(f))
        except (ValueError, OSError):
            pass  # a bad file shouldn't stop the app; the page can rewrite it


def update(data):
    with _lock:
        _apply(data)
        tmp = _path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(current(), f, indent=1)
        os.replace(tmp, _path)
    return current()
