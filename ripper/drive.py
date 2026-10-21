"""Watches optical drives and rips each audio CD that goes in into the open book."""
import fcntl
import hashlib
import os
import re
import shutil
import threading
import time

from . import config, store
from .util import log, makedirs, chown, notify, run

# linux/cdrom.h
CDROM_DRIVE_STATUS = 0x5326
CDROM_DISC_STATUS = 0x5327
CDROMEJECT = 0x5309
CDROM_LOCKDOOR = 0x5329
CDS_NO_INFO, CDS_NO_DISC, CDS_TRAY_OPEN, CDS_DRIVE_NOT_READY, CDS_DISC_OK = 0, 1, 2, 3, 4
CDS_AUDIO, CDS_MIXED = 100, 105

SECTORS_PER_SEC = 75.0

# Discs the user removed from a book mid-rip; the ripper checks this between tracks.
cancelled = set()


class Watcher(threading.Thread):
    def __init__(self, dev):
        super().__init__(daemon=True, name=f"watch-{dev}")
        self.dev = dev
        self.handled = False  # we've already dealt with the disc currently in the tray
        store.set_drive(dev, state="starting", message="")

    # ---- low level ----
    def _ioctl(self, req, arg=0):
        fd = os.open(self.dev, os.O_RDONLY | os.O_NONBLOCK)
        try:
            return fcntl.ioctl(fd, req, arg)
        finally:
            os.close(fd)

    def drive_status(self):
        return self._ioctl(CDROM_DRIVE_STATUS, 0)

    def is_audio(self):
        try:
            return self._ioctl(CDROM_DISC_STATUS, 0) in (CDS_AUDIO, CDS_MIXED)
        except OSError:
            return True  # some drives don't answer; let cdparanoia decide

    def eject(self):
        if not config.AUTO_EJECT:
            return
        try:
            try:
                self._ioctl(CDROM_LOCKDOOR, 0)
            except OSError:
                pass
            self._ioctl(CDROMEJECT, 0)
        except OSError:
            run(["eject", self.dev])

    def read_toc(self):
        """Return list of (track_no, sectors) for the audio tracks on the disc."""
        r = run(["cdparanoia", "-d", self.dev, "-Q"])
        tracks = []
        for line in (r.stderr + r.stdout).splitlines():
            m = re.match(r"^\s+(\d+)\.\s+(\d+)\s+\[[^\]]*\]\s+(\d+)", line)
            if m:
                tracks.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if not tracks:
            raise RuntimeError("Couldn't read the track list: " + (r.stderr.strip().splitlines() or ["no output"])[-1])
        return tracks

    def rip_track(self, n, wav):
        r = run(["cdparanoia", "-d", self.dev, *config.PARANOIA_OPTS, "-q", str(n), wav])
        if r.returncode != 0 or not os.path.exists(wav):
            raise RuntimeError(f"Track {n} failed to rip: {(r.stderr.strip().splitlines() or ['unknown error'])[-1]}")

    # ---- main loop ----
    def run(self):
        while True:
            try:
                self.tick()
            except Exception as e:  # keep watching no matter what
                log.exception("watcher %s", self.dev)
                store.set_drive(self.dev, state="error", message=str(e))
                time.sleep(5)
            time.sleep(2)

    def tick(self):
        try:
            st = self.drive_status()
        except OSError as e:
            store.set_drive(self.dev, state="missing", message=f"Can't open drive ({e.strerror})")
            time.sleep(8)
            return

        if st != CDS_DISC_OK:
            self.handled = False
            label = {CDS_TRAY_OPEN: "open", CDS_NO_DISC: "empty", CDS_DRIVE_NOT_READY: "reading"}.get(st, "empty")
            msg = {"open": "Tray open", "empty": "No disc", "reading": "Reading disc…"}[label]
            store.set_drive(self.dev, state=label, message=msg, book=None, disc=None, track=0, tracks=0)
            return
        if self.handled:
            return

        if not self.is_audio():
            self.handled = True
            store.set_drive(self.dev, state="idle", message="Not an audio CD — ejected")
            self.eject()
            return

        with store.lock():
            book = store.active_book()
            if not book or book["status"] != "collecting":
                if not config.AUTO_START_BOOK:
                    store.set_drive(self.dev, state="waiting", message="Disc ready — start a book in the web page")
                    return  # not handled; we'll rip once a book is opened
                book = store.new_book()

        self.handle_disc(book)

    def handle_disc(self, book):
        self.handled = True
        store.set_drive(self.dev, state="ripping", message="Reading track list…", book=book["id"])
        toc = self.read_toc()
        fp = hashlib.sha1(repr([(n, s, b) for n, s, b in toc]).encode()).hexdigest()[:16]
        self.rip(book, toc, fp)


    def rip(self, book, toc, fp):
        with store.lock():
            existing = next((d for d in book["discs"] if d["fingerprint"] == fp), None)
            if existing and existing["status"] in ("ready", "ripping"):
                num = book["discs"].index(existing) + 1
                store.set_drive(self.dev, state="idle", message=f"Already have this disc (disc {num}) — ejected")
                self.eject()
                return
            if existing:  # a failed earlier attempt: rip again in the same slot
                disc = existing
                disc.update(status="ripping", error="", tracks=[], done_tracks=0, seconds=0.0, drive=self.dev)
            else:
                disc = store.add_disc(book["id"], fp, self.dev)
            disc["total_tracks"] = len(toc)
            num = book["discs"].index(disc) + 1
            store.save()

        ddir = os.path.join(store.book_dir(book["id"]), disc["id"])
        if os.path.isdir(ddir):
            shutil.rmtree(ddir)
        makedirs(ddir)
        log.info("ripping disc %d of %s (%d tracks) on %s", num, book["id"], len(toc), self.dev)

        try:
            for i, (n, sectors, _) in enumerate(toc):
                if disc["id"] in cancelled:
                    raise RuntimeError("cancelled")
                store.set_drive(self.dev, state="ripping", message=f"Disc {num}: track {i + 1} of {len(toc)}",
                                book=book["id"], disc=disc["id"], track=i, tracks=len(toc))
                wav = os.path.join(ddir, f"t{n:02d}.wav")
                flac = os.path.join(ddir, f"t{n:02d}.flac")
                self.rip_track(n, wav)
                to_flac(wav, flac)
                with store.lock():
                    disc["tracks"].append({"n": n, "file": os.path.basename(flac), "seconds": sectors / SECTORS_PER_SEC})
                    disc["done_tracks"] = i + 1
                    disc["seconds"] += sectors / SECTORS_PER_SEC
                    store.save()
        except Exception as e:
            if str(e) == "cancelled":
                cancelled.discard(disc["id"])
                shutil.rmtree(ddir, ignore_errors=True)
                store.set_drive(self.dev, state="idle", message="Rip cancelled — ejected")
            else:
                log.exception("rip failed")
                with store.lock():
                    disc["status"] = "error"
                    disc["error"] = str(e)
                    store.save()
                store.set_drive(self.dev, state="idle", message=f"Disc {num} failed — clean it and put it back in")
                notify("Disc failed", f"Disc {num} of {book['title'] or 'your book'} failed: {e}")
            self.eject()
            return

        with store.lock():
            disc["status"] = "ready"
            disc["ripped_at"] = time.time()
            store.save()
        store.set_drive(self.dev, state="idle", message=f"Disc {num} done — insert disc {num + 1}",
                        book=book["id"], disc=None, track=0, tracks=0)
        notify("Disc done", f"Disc {num} of {book['title'] or 'your book'} is ripped. Insert disc {num + 1}.")
        self.eject()


def to_flac(wav, flac):
    r = run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-c:a", "flac", "-compression_level", "5", flac])
    if r.returncode != 0:
        raise RuntimeError("FLAC encode failed: " + r.stderr.strip()[-200:])
    os.remove(wav)
    chown(flac)


class DemoWatcher(Watcher):
    """A pretend drive for trying the app without a CD. Use the 'Insert test disc' button."""

    def __init__(self, dev="demo"):
        super().__init__(dev)
        self.pending = None
        self.counter = 0

    def insert(self):
        self.counter += 1
        self.pending = self.counter

    def eject(self):
        self.pending = None

    def tick(self):
        if self.pending is None:
            self.handled = False
            store.set_drive(self.dev, state="empty", message="No disc (demo drive)", book=None, disc=None)
            return
        if self.handled:
            return
        with store.lock():
            book = store.active_book()
            if not book or book["status"] != "collecting":
                book = store.new_book()
        self.handled = True
        k = self.pending
        # 3–4 short tracks per fake disc; the TOC differs per disc so each one is unique
        toc = [(i + 1, int(75 * (4 + i + k % 3)), 0) for i in range(3 + k % 2)]
        fp = hashlib.sha1(f"demo{k}".encode()).hexdigest()[:16]
        self.rip(book, toc, fp)

    def rip_track(self, n, wav):
        freq = 220 + 110 * ((self.pending or 1) % 4) + 20 * n
        secs = next(s for t, s, _ in self._toc if t == n) / SECTORS_PER_SEC
        r = run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                 f"sine=frequency={freq}:duration={secs}", "-ac", "2", "-ar", "44100", wav])
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        time.sleep(0.3)

    def rip(self, book, toc, fp):
        self._toc = toc
        super().rip(book, toc, fp)


watchers = {}


def start():
    for dev in config.DRIVES:
        w = DemoWatcher(dev) if dev == "demo" else Watcher(dev)
        watchers[dev] = w
        w.start()
