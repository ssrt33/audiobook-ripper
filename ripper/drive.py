"""Watches optical drives and rips each audio CD that goes in into the open book."""
import fcntl
import glob
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
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
BYTES_PER_SECTOR = 2352
# Seconds without any new audio before a track is shown as stuck (and a phone alert is sent).
STALL_WARN = int(os.environ.get("STALL_WARN", "120"))

# Discs the user removed from a book mid-rip; the ripper checks this between tracks.
cancelled = set()


class Watcher(threading.Thread):
    def __init__(self, dev, auto=False):
        super().__init__(daemon=True, name=f"watch-{dev}")
        self.dev = dev
        self.auto = auto          # found by auto-detect: forget it if it goes away for good
        self.stopped = False
        self.missing_since = None
        self.abort = None         # set to a reason to stop the current rip right away
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
        try:
            r = run(["cdparanoia", "-d", self.dev, "-Q"], timeout=180)
        except subprocess.TimeoutExpired:
            raise RuntimeError("The drive took more than 3 minutes to read the track list")
        tracks = []
        for line in (r.stderr + r.stdout).splitlines():
            m = re.match(r"^\s+(\d+)\.\s+(\d+)\s+\[[^\]]*\]\s+(\d+)", line)
            if m:
                tracks.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if not tracks:
            raise RuntimeError("Couldn't read the track list: " + (r.stderr.strip().splitlines() or ["no output"])[-1])
        return tracks

    def track_cmd(self, n, wav):
        return ["cdparanoia", "-d", self.dev, *config.PARANOIA_OPTS, "-q", str(n), wav]

    def rip_track(self, n, wav, sectors, status):
        """Rip one track while watching the file grow. `status(**fields)` updates the page.

        A track counts as stuck when no new audio has been written for STALL_WARN seconds,
        which on a scratched disc means cdparanoia is re-reading the same spot over and over.
        """
        expected = sectors * BYTES_PER_SECTOR
        errlog = wav + ".log"
        with open(errlog, "w") as ef:
            p = subprocess.Popen(self.track_cmd(n, wav), stdout=subprocess.DEVNULL, stderr=ef)
        start = last_growth = time.time()
        last_size, warned = 0, False
        try:
            while p.poll() is None:
                time.sleep(1)
                now = time.time()
                try:
                    size = max(0, os.path.getsize(wav) - 44)  # minus the WAV header
                except OSError:
                    size = 0
                if size > last_size:
                    last_size, last_growth = size, now
                stalled = now - last_growth
                speed = (size / BYTES_PER_SECTOR / SECTORS_PER_SEC) / max(now - start, 1)
                stuck = stalled >= STALL_WARN
                status(track_pct=min(size / expected, 1.0) if expected else 0, speed=round(speed, 1),
                       elapsed=int(now - start), stalled=int(stalled) if stuck else 0, stuck=stuck)
                if stuck and not warned:
                    warned = True
                    log.warning("track %d stuck: no progress for %ds at %d%%", n, stalled, size * 100 // max(expected, 1))
                    status(notify=True)
                if self.abort:
                    raise RuntimeError(self.abort)
                limit = config.STUCK_LIMIT * 60
                if limit and stalled >= limit:
                    raise RuntimeError(f"Track {n} was stuck for {config.STUCK_LIMIT} minutes, so the app gave up. "
                                       "Clean the disc and put it back in.")
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        try:
            with open(errlog) as ef:
                err = ef.read().strip().splitlines()
            os.remove(errlog)
        except OSError:
            err = []
        if p.returncode != 0 or not os.path.exists(wav):
            raise RuntimeError(f"Track {n} failed to rip: {(err or ['unknown error'])[-1]}")
        took = time.time() - start
        log.info("track %d ripped in %dm%02ds (%.1fx)%s", n, took // 60, took % 60,
                 sectors / SECTORS_PER_SEC / max(took, 1), " after being stuck" if warned else "")

    # ---- main loop ----
    def run(self):
        while not self.stopped:
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
            if self.missing_since is None:
                self.missing_since = time.time()
                log.warning("drive %s went away (%s)", self.dev, e.strerror)
            if self.auto and time.time() - self.missing_since > 20:
                # Unplugged for good, or it came back under another name; auto-detect re-adds it.
                log.info("forgetting %s", self.dev)
                self.stopped = True
                watchers.pop(self.dev, None)
                store.remove_drive(self.dev)
                return
            store.set_drive(self.dev, state="missing",
                            message="Drive disconnected — check the cable" if e.errno == 2
                            else f"Can't open drive ({e.strerror})")
            time.sleep(5)
            return
        if self.missing_since is not None:
            log.info("drive %s is back", self.dev)
            self.missing_since = None

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

        self.abort = None
        title = book["title"] or "your book"
        try:
            for i, (n, sectors, _) in enumerate(toc):
                if disc["id"] in cancelled:
                    raise RuntimeError("cancelled")
                label = f"Disc {num}: track {i + 1} of {len(toc)}"
                store.set_drive(self.dev, state="ripping", message=label, book=book["id"], disc=disc["id"],
                                track=i, tracks=len(toc), track_pct=0, speed=0, elapsed=0, stalled=0, stuck=False)

                def status(**kw):
                    if disc["id"] in cancelled:
                        self.abort = "cancelled"
                    if kw.pop("notify", False):
                        notify("Rip looks stuck",
                               f"Disc {num} of {title}, track {i + 1}: no progress for {STALL_WARN // 60}+ minutes. "
                               "The disc may be scratched. Open the ripper page to give up on it, or wait.")
                        return
                    stuck = kw.get("stuck")
                    store.set_drive(self.dev, message=(f"Disc {num}: stuck on track {i + 1} of {len(toc)}" if stuck else label), **kw)

                wav = os.path.join(ddir, f"t{n:02d}.wav")
                flac = os.path.join(ddir, f"t{n:02d}.flac")
                self.rip_track(n, wav, sectors, status)
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
                store.set_drive(self.dev, state="idle", message=f"Disc {num} failed — clean it and put it back in",
                                stuck=False, stalled=0)
                notify("Disc failed", f"Disc {num} of {title} failed: {e}")
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

    def track_cmd(self, n, wav):
        freq = 220 + 110 * ((self.pending or 1) % 4) + 20 * n
        secs = next(s for t, s, _ in self._toc if t == n) / SECTORS_PER_SEC
        tmp = wav + ".gen.wav"
        stuck = "1" if (os.environ.get("DEMO_STUCK") and n == 2) else "0"
        gen = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
               f"sine=frequency={freq}:duration={secs}", "-ac", "2", "-ar", "44100", tmp]
        write = [sys.executable, "-m", "ripper.demo_writer", tmp, wav, stuck]
        return ["sh", "-c", " ".join(map(shlex.quote, gen)) + " && exec " + " ".join(map(shlex.quote, write))]

    def rip(self, book, toc, fp):
        self._toc = toc
        super().rip(book, toc, fp)


watchers = {}


def _add(dev, auto=False):
    if dev in watchers:
        return
    w = DemoWatcher(dev) if dev == "demo" else Watcher(dev, auto=auto)
    watchers[dev] = w
    w.start()


def _autodetect():
    """DRIVES=auto: watch every /dev/srN, including ones plugged in (or renamed) later."""
    while True:
        for dev in sorted(glob.glob("/dev/sr[0-9]*")):
            if dev not in watchers:
                log.info("found drive %s", dev)
                _add(dev, auto=True)
        if watchers:
            store.remove_drive("none")
        else:
            store.set_drive("none", state="missing", message="No CD drive found — plug one in")
        time.sleep(5)


def start():
    for dev in config.DRIVES:
        if dev != "auto":
            _add(dev)
    if "auto" in config.DRIVES:
        threading.Thread(target=_autodetect, daemon=True, name="autodetect").start()
