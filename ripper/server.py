"""Web page + JSON API."""
import logging
import os
import shutil
import threading

from flask import Flask, jsonify, request, send_file, send_from_directory

from . import builder, config, drive, metadata, settings, store
from .util import notify

app = Flask(__name__, static_folder=None)
STATIC = os.path.join(os.path.dirname(__file__), "static")

EDITABLE = {"title", "subtitle", "author", "narrator", "series", "series_seq", "year", "publisher",
            "description", "genre", "chapters", "bitrate", "channels"}


def err(msg, code=400):
    return jsonify({"error": msg}), code


def get_book(bid):
    b = store.book(bid)
    if not b:
        raise LookupError
    return b


@app.errorhandler(LookupError)
def _missing(_):
    return err("Not found", 404)


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


@app.get("/api/state")
def state():
    s = store.snapshot()
    s["settings"] = dict(settings.current(), library=config.LIBRARY_DIR, demo="demo" in config.DRIVES)
    for b in s["books"]:
        b["output_preview"] = builder.output_path(b)[1]
        b["seconds"] = sum(d["seconds"] for d in b["discs"] if d["status"] == "ready")
    return jsonify(s)


@app.post("/api/books")
def new_book():
    with store.lock():
        a = store.active_book()
        if a and a["status"] == "collecting" and not a["discs"]:
            return jsonify(a)  # reuse the empty one instead of piling up blanks
        return jsonify(store.new_book((request.json or {}).get("title", "")))


@app.patch("/api/books/<bid>")
def update_book(bid):
    b = get_book(bid)
    if b["status"] == "building":
        return err("This book is being built right now")
    data = request.json or {}
    with store.lock():
        for k, v in data.items():
            if k not in EDITABLE:
                continue
            if k == "channels":
                v = 1 if int(v) == 1 else 2
            elif k == "chapters" and v not in ("disc", "track", "none"):
                continue
            elif k == "bitrate" and v not in ("32k", "48k", "64k", "96k", "128k"):
                continue
            else:
                v = str(v).strip()
            b[k] = v
        store.save()
    return jsonify(b)


@app.post("/api/books/<bid>/activate")
def activate(bid):
    b = get_book(bid)
    with store.lock():
        if b["status"] in ("queued", "failed"):
            b["status"] = "collecting"
            b["error"] = ""
        if b["status"] != "collecting":
            return err("Only unfinished books can take more discs")
        store.set_active(bid)
    return jsonify(b)


@app.post("/api/books/<bid>/finish")
def finish(bid):
    b = get_book(bid)
    with store.lock():
        if any(d["status"] == "ripping" for d in b["discs"]):
            return err("Wait for the current disc to finish ripping")
        if not any(d["status"] == "ready" for d in b["discs"]):
            return err("Rip at least one disc first")
        if not b["title"].strip():
            return err("Give the book a title first")
        if b["status"] not in ("collecting", "failed", "done"):
            return err("Already queued")
        if b["status"] == "done" and b.get("rips_deleted"):
            return err("The rips were deleted after the last build, so this book can't be rebuilt")
        b["status"] = "queued"
        b["error"] = ""
        if store.active_book() is b:
            store.set_active(None)
        store.save()
    builder.worker.wake.set()
    return jsonify(b)


@app.delete("/api/books/<bid>")
def delete_book(bid):
    b = get_book(bid)
    if b["status"] == "building":
        return err("Wait for the build to finish")
    if any(d["status"] == "ripping" for d in b["discs"]):
        return err("A disc from this book is still ripping")
    shutil.rmtree(store.book_dir(bid), ignore_errors=True)
    store.delete_book(bid)
    return jsonify({"ok": True})


@app.post("/api/books/<bid>/discs/<did>/move")
def move_disc(bid, did):
    b = get_book(bid)
    step = int((request.json or {}).get("step", 0))
    with store.lock():
        d = store.find_disc(bid, did)
        if not d:
            raise LookupError
        i = b["discs"].index(d)
        j = max(0, min(len(b["discs"]) - 1, i + step))
        b["discs"].insert(j, b["discs"].pop(i))
        store.save()
    return jsonify(b)


@app.delete("/api/books/<bid>/discs/<did>")
def delete_disc(bid, did):
    b = get_book(bid)
    with store.lock():
        d = store.find_disc(bid, did)
        if not d:
            raise LookupError
        if d["status"] == "ripping":
            drive.cancelled.add(did)
        b["discs"].remove(d)
        store.save()
    if d["status"] != "ripping":
        shutil.rmtree(os.path.join(store.book_dir(bid), did), ignore_errors=True)
    return jsonify(b)


@app.put("/api/settings")
def put_settings():
    try:
        return jsonify(settings.update(request.json or {}))
    except ValueError as e:
        return err(str(e))


@app.post("/api/settings/test-alert")
def test_alert():
    if not config.NOTIFY_URL:
        return err("Enter an alert address first")
    notify("Audiobook Ripper", "Test alert: phone alerts are working.")
    return jsonify({"ok": True})


@app.get("/api/books/<bid>/cover")
def get_cover(bid):
    b = get_book(bid)
    p = builder.cover_path(bid)
    if not os.path.exists(p) and b.get("output"):
        # rips (and the working cover) are deleted after a build; use the copy next to the book
        p = os.path.join(os.path.dirname(b["output"]), "cover.jpg")
    if not os.path.exists(p):
        raise LookupError
    resp = send_file(p, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/books/<bid>/cover")
def set_cover(bid):
    b = get_book(bid)
    os.makedirs(store.book_dir(bid), exist_ok=True)
    dest = builder.cover_path(bid)
    try:
        if "file" in request.files:
            metadata.save_cover_bytes(request.files["file"].read(), dest)
        else:
            metadata.fetch_cover((request.json or {}).get("url", ""), dest)
    except Exception as e:
        return err(f"Couldn't use that cover: {e}")
    with store.lock():
        b["has_cover"] = True
        store.save()
    return jsonify(b)


@app.delete("/api/books/<bid>/cover")
def clear_cover(bid):
    b = get_book(bid)
    try:
        os.remove(builder.cover_path(bid))
    except FileNotFoundError:
        pass
    with store.lock():
        b["has_cover"] = False
        store.save()
    return jsonify(b)


@app.get("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": [], "errors": []})
    results, errors = metadata.search(q)
    return jsonify({"results": results, "errors": errors})


@app.post("/api/drives/eject")
def eject():
    dev = (request.json or {}).get("device")
    w = drive.watchers.get(dev)
    if not w:
        raise LookupError
    if isinstance(w, drive.DemoWatcher):
        w.pending = None
    else:
        try:
            w._ioctl(drive.CDROMEJECT, 0)
        except OSError as e:
            return err(f"Couldn't eject: {e.strerror}")
    return jsonify({"ok": True})


@app.post("/api/demo/insert")
def demo_insert():
    w = drive.watchers.get("demo")
    if not w:
        return err("Demo drive is off (add 'demo' to DRIVES)")
    w.insert()
    return jsonify({"ok": True})


def warn_if_work_in_library():
    """Warn if the work dir is visible inside the library (same folder via another mount)."""
    log = logging.getLogger("ripper")
    try:
        w = os.stat(config.WORK_DIR)
        target = (w.st_dev, w.st_ino)
        base = os.path.abspath(config.LIBRARY_DIR)
        for root, dirs, _ in os.walk(base):
            depth = root[len(base):].count(os.sep)
            for d in dirs:
                try:
                    st = os.stat(os.path.join(root, d))
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) == target:
                    log.warning(
                        "The work folder is inside your library (%s). Audiobookshelf will import "
                        "half-finished rips as books. Move the work folder outside the library: "
                        "https://github.com/ssrt33/audiobook-ripper#or-install-with-dockge",
                        os.path.join(root, d))
                    return
            if depth >= 2:
                dirs[:] = []
    except Exception:
        log.debug("work folder check skipped", exc_info=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for d in (config.WORK_DIR, config.LIBRARY_DIR, config.CONFIG_DIR):
        os.makedirs(d, exist_ok=True)
    store.init()
    settings.init()

    # As PID 1 in a container, Python ignores SIGTERM unless told otherwise, so `docker stop`
    # used to wait 10 s and then kill the app (exit code 137). Save state and exit cleanly instead.
    import signal

    def _stop(signum, _frame):
        logging.getLogger("ripper").info("stopping (%s)", signal.Signals(signum).name)
        try:
            store.save()
        finally:
            os._exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    drive.start()
    builder.worker.start()
    from waitress import serve
    from . import __version__
    log = logging.getLogger("ripper")
    log.info("Audiobook Ripper %s (changes: https://github.com/ssrt33/audiobook-ripper/blob/main/CHANGELOG.md)", __version__)
    log.info("listening on :%d, drives: %s", config.PORT, ", ".join(config.DRIVES))
    threading.Thread(target=warn_if_work_in_library, daemon=True).start()
    serve(app, host="0.0.0.0", port=config.PORT, threads=8)


if __name__ == "__main__":
    main()
