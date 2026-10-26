"""Turns a book's ripped discs into one tagged M4B with chapters and cover art."""
import os
import shutil
import subprocess
import threading
import time

from . import config, store
from .util import chown, log, makedirs, notify, safe_name


def cover_path(book_id):
    return os.path.join(store.book_dir(book_id), "cover.jpg")


def all_tracks(book):
    """(disc_number, disc, track) in play order, ready discs only."""
    out = []
    for i, d in enumerate(book["discs"]):
        if d["status"] != "ready":
            continue
        for t in sorted(d["tracks"], key=lambda t: t["n"]):
            out.append((i + 1, d, t))
    return out


def chapter_list(book):
    """List of (title, start_sec, end_sec)."""
    tracks = all_tracks(book)
    chapters, pos = [], 0.0
    if book["chapters"] == "track":
        for k, (dn, d, t) in enumerate(tracks):
            chapters.append((f"Disc {dn} - Track {t['n']:02d}", pos, pos + t["seconds"]))
            pos += t["seconds"]
    elif book["chapters"] == "disc":
        ready = [(i + 1, d) for i, d in enumerate(book["discs"]) if d["status"] == "ready"]
        for dn, d in ready:
            secs = sum(t["seconds"] for t in d["tracks"])
            chapters.append((f"Disc {dn}", pos, pos + secs))
            pos += secs
    return chapters


def _ffmeta_escape(s):
    for ch in ("\\", "=", ";", "#", "\n"):
        s = s.replace(ch, "\\" + ch)
    return s


def output_path(book):
    seq = str(book.get("series_seq") or "").strip()
    title = safe_name(book["title"], "Untitled")
    fields = {
        "author": safe_name(book["author"], "Unknown Author"),
        "series": safe_name(book["series"]),
        "series_seq": safe_name(seq),
        "title": title,
        "series_seq_title": safe_name(f"{seq} - {book['title']}" if (seq and book["series"]) else book["title"], "Untitled"),
        "year": safe_name(book["year"]),
        "narrator": safe_name(book["narrator"]),
    }
    parts = [p.strip() for p in config.FOLDER_TEMPLATE.format(**fields).split("/")]
    folder = os.path.join(config.LIBRARY_DIR, *[p for p in parts if p])
    return folder, os.path.join(folder, title + ".m4b")


def build(book):
    tracks = all_tracks(book)
    if not tracks:
        raise RuntimeError("No ripped discs in this book")
    total = sum(t["seconds"] for _, _, t in tracks)
    work = store.book_dir(book["id"])

    concat = os.path.join(work, "concat.txt")
    with open(concat, "w") as f:
        for _, d, t in tracks:
            p = os.path.join(work, d["id"], t["file"]).replace("'", "'\\''")
            f.write(f"file '{p}'\n")

    meta = os.path.join(work, "chapters.txt")
    with open(meta, "w") as f:
        f.write(";FFMETADATA1\n")
        for title, start, end in chapter_list(book):
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(start * 1000)}\nEND={int(end * 1000)}\n"
                    f"title={_ffmeta_escape(title)}\n")

    folder, final = output_path(book)
    makedirs(folder)
    tmp = os.path.join(work, "book.m4b.part")

    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y",
           "-f", "concat", "-safe", "0", "-i", concat,
           "-i", meta]
    cover = cover_path(book["id"])
    has_cover = book["has_cover"] and os.path.exists(cover)
    if has_cover:
        cmd += ["-i", cover]
    cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
    if has_cover:
        cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += ["-c:a", "aac", "-b:a", book["bitrate"], "-ac", str(book["channels"]), "-ar", "44100"]

    tags = {
        "title": book["title"], "album": book["title"], "artist": book["author"],
        "album_artist": book["author"], "composer": book["narrator"], "genre": book["genre"] or "Audiobook",
        "date": book["year"], "publisher": book["publisher"], "description": book["description"],
        "comment": book["description"][:250], "grouping": book["series"],
    }
    if book["series"]:
        tags["show"] = book["series"]
        if book["series_seq"]:
            tags["episode_id"] = str(book["series_seq"])
    for k, v in tags.items():
        if v:
            cmd += ["-metadata", f"{k}={v}"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-nostats", tmp]

    log.info("building %s -> %s", book["id"], final)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    last = 0
    for line in p.stdout:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            pct = min(99, int(us / 1e6 / total * 100)) if total else 0
            if pct != last:
                last = pct
                with store.lock():
                    book["progress"] = pct
                    store.save()
    err = p.stderr.read()
    if p.wait() != 0:
        raise RuntimeError("Encoding failed: " + err.strip()[-300:])

    shutil.move(tmp, final)
    chown(final)

    # Sidecar files Audiobookshelf (and people) can read.
    if has_cover:
        shutil.copy(cover, os.path.join(folder, "cover.jpg"))
        chown(os.path.join(folder, "cover.jpg"))
    for name, text in (("desc.txt", book["description"]), ("reader.txt", book["narrator"])):
        if text:
            with open(os.path.join(folder, name), "w") as f:
                f.write(text + "\n")
            chown(os.path.join(folder, name))
    return final


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="builder")
        self.wake = threading.Event()

    def run(self):
        while True:
            book = store.queued_book()
            if not book:
                self.wake.wait(10)
                self.wake.clear()
                continue
            with store.lock():
                book["status"] = "building"
                book["progress"] = 0
                book["error"] = ""
                store.save()
            try:
                out = build(book)
                with store.lock():
                    book.update(status="done", progress=100, output=out, finished=time.time())
                    store.save()
                log.info("finished %s -> %s", book["id"], out)
                rep = None
                try:
                    from . import report
                    rep = report.deliver(book)
                except Exception as e:  # a report problem must never fail the book
                    log.warning("rip report failed: %s", e)
                with store.lock():
                    book["report"] = rep
                    store.save()
                if not (rep and any(s.startswith("phone") for s in rep["sent"])):
                    notify("Audiobook ready", f"{book['title']} is in your library."
                           + (f" {rep['summary'].split(': ', 1)[-1]}" if rep else ""))
                if not config.KEEP_RIPS:
                    shutil.rmtree(store.book_dir(book["id"]), ignore_errors=True)
                    with store.lock():
                        book["rips_deleted"] = True
                        store.save()
            except Exception as e:
                log.exception("build failed")
                with store.lock():
                    book.update(status="failed", error=str(e))
                    store.save()
                notify("Audiobook build failed", f"{book['title']}: {e}")


worker = Worker()
