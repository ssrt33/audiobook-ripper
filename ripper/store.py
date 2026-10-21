"""Books, discs and drive status, persisted to a JSON file so a restart loses nothing."""
import json
import os
import threading
import time
import uuid

from . import config

_lock = threading.RLock()
_state = {"books": {}, "order": [], "active": None}
_drives = {}  # live drive status, not persisted
_listeners = []
_path = None


def _new_id():
    return uuid.uuid4().hex[:10]


def init():
    global _path
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    _path = os.path.join(config.CONFIG_DIR, "state.json")
    if os.path.exists(_path):
        with open(_path) as f:
            _state.update(json.load(f))
    # Anything that was mid-rip or mid-build when we stopped can't be trusted.
    for b in _state["books"].values():
        if b["status"] == "building":
            b["status"] = "queued"
            b["progress"] = 0
        for d in b["discs"]:
            if d["status"] == "ripping":
                d["status"] = "error"
                d["error"] = "Interrupted by a restart. Remove this disc and rip it again."
    save()


def save():
    with _lock:
        tmp = _path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f, indent=1)
        os.replace(tmp, _path)


def lock():
    return _lock


def book(book_id):
    return _state["books"].get(book_id)


def books():
    return [_state["books"][i] for i in _state["order"] if i in _state["books"]]


def active_book():
    return _state["books"].get(_state["active"]) if _state["active"] else None


def new_book(title=""):
    with _lock:
        bid = _new_id()
        b = {
            "id": bid,
            "title": title,
            "subtitle": "",
            "author": "",
            "narrator": "",
            "series": "",
            "series_seq": "",
            "year": "",
            "publisher": "",
            "description": "",
            "genre": "Audiobook",
            "has_cover": False,
            "chapters": config.DEFAULT_CHAPTERS,
            "bitrate": config.DEFAULT_BITRATE,
            "channels": config.DEFAULT_CHANNELS,
            "status": "collecting",  # collecting -> queued -> building -> done | failed
            "progress": 0,
            "error": "",
            "output": "",
            "discs": [],
            "created": time.time(),
            "finished": None,
        }
        _state["books"][bid] = b
        _state["order"].insert(0, bid)
        _state["active"] = bid
        save()
        return b


def set_active(book_id):
    with _lock:
        _state["active"] = book_id
        save()


def book_dir(book_id):
    return os.path.join(config.WORK_DIR, book_id)


def add_disc(book_id, fingerprint, drive):
    with _lock:
        b = book(book_id)
        d = {
            "id": _new_id(),
            "fingerprint": fingerprint,
            "drive": drive,
            "status": "ripping",  # ripping -> ready | error
            "tracks": [],
            "total_tracks": 0,
            "done_tracks": 0,
            "seconds": 0.0,
            "error": "",
            "ripped_at": None,
        }
        b["discs"].append(d)
        save()
        return d


def find_disc(book_id, disc_id):
    b = book(book_id)
    if not b:
        return None
    return next((d for d in b["discs"] if d["id"] == disc_id), None)


def queued_book():
    with _lock:
        # oldest queued first
        for bid in reversed(_state["order"]):
            b = _state["books"].get(bid)
            if b and b["status"] == "queued":
                return b
    return None


def delete_book(book_id):
    with _lock:
        _state["books"].pop(book_id, None)
        if book_id in _state["order"]:
            _state["order"].remove(book_id)
        if _state["active"] == book_id:
            _state["active"] = None
        save()


# ---- live drive status ----

def set_drive(dev, **kw):
    with _lock:
        cur = _drives.setdefault(dev, {"device": dev, "state": "unknown", "message": "", "book": None,
                                       "disc": None, "track": 0, "tracks": 0})
        cur.update(kw)


def remove_drive(dev):
    with _lock:
        _drives.pop(dev, None)


def drives():
    with _lock:
        return [dict(v) for v in _drives.values()]


def snapshot():
    with _lock:
        return json.loads(json.dumps({
            "books": books(),
            "active": _state["active"],
            "drives": drives(),
        }))
