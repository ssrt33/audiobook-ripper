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
import wave

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

# How much audio to jump over the first time a bad spot is skipped. If the drive is stuck again
# right after the jump, the next skip doubles, up to SKIP_MAX.
SKIP_START = 2 * 75   # sectors (2 seconds)
SKIP_MAX = 30 * 75    # sectors (30 seconds)
# A new piece that gets stuck with less than this much audio counts as "still in the bad spot".
SKIP_NEAR = 5 * 75    # sectors

# Discs the user removed from a book mid-rip; the ripper checks this between tracks.
cancelled = set()


def _pcm_start(path):
    """Byte offset where the audio starts in a WAV file (after the header)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return 44
    i = head.find(b"data", 12)
    return i + 8 if i >= 0 else 44


def _sector_span(n, offset):
    """cdparanoia span for track n starting `offset` sectors in, to the end of the track."""
    secs, frames = divmod(offset, 75)
    h, rest = divmod(secs, 3600)
    m, sec = divmod(rest, 60)
    return f"{n}[{h}:{m}:{sec}.{frames}]-{n}"


def join_pieces(pieces, out):
    """Write one 44.1 kHz stereo WAV from [(kind, value)] pieces.

    ("file", (path, max_bytes or None)) copies audio from a ripped piece;
    ("silence", sectors) fills a skipped stretch with silence.
    """
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        for kind, val in pieces:
            if kind == "silence":
                chunk = bytes(BYTES_PER_SECTOR * 75)
                left = val * BYTES_PER_SECTOR
                while left > 0:
                    w.writeframesraw(chunk[:min(left, len(chunk))])
                    left -= len(chunk)
            else:
                path, limit = val
                with open(path, "rb") as f:
                    f.seek(_pcm_start(path))
                    data = f.read() if limit is None else f.read(limit)
                w.writeframesraw(data[:len(data) - len(data) % 4])


class Watcher(threading.Thread):
    def __init__(self, dev, auto=False):
        super().__init__(daemon=True, name=f"watch-{dev}")
        self.dev = dev
        self.auto = auto          # found by auto-detect: forget it if it goes away for good
        self.stopped = False
        self.missing_since = None
        self.abort = None         # set to a reason to stop the current rip right away
        self.skip = False         # set to jump over the bad spot in the current track
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

    def track_cmd(self, n, wav, offset=0):
        span = _sector_span(n, offset) if offset else str(n)
        return ["cdparanoia", "-d", self.dev, *config.PARANOIA_OPTS, "-q", "--", span, wav]

    def rip_track(self, n, wav, sectors, status):
        """Rip one track while watching the file grow. `status(**fields)` updates the page.

        A track counts as stuck when no new audio has been written for STALL_WARN seconds,
        which on a scratched disc means cdparanoia is re-reading the same spot over and over.
        A stuck track can be skipped past (button on the page, or the "when stuck" setting):
        the audio ripped so far is kept, the bad stretch becomes silence and the rip carries on
        just after it. Returns the skipped stretches as [{"at": seconds, "len": seconds}].
        """
        start = time.time()
        self.skip = False
        self.max_stall = 0    # longest stretch with no new audio, for the bad-spot report
        offset = 0            # sector the current piece started at
        pieces, gaps = [], []
        skip_len = SKIP_START
        warned = False
        while True:
            piece = wav if offset == 0 else f"{wav}.{offset}.part"
            outcome, good, warned = self._rip_piece(n, piece, offset, sectors, start, status, warned)
            if outcome == "done":
                pieces.append(("file", (piece, None)))
                break
            # outcome == "skip": keep what we have, jump over the bad spot, rip the rest.
            if good < SKIP_NEAR and gaps and gaps[-1]["end"] == offset:
                skip_len = min(skip_len * 2, SKIP_MAX)   # stuck again right after the last jump
            else:
                skip_len = SKIP_START
            bad = offset + good
            pieces.append(("file", (piece, good * BYTES_PER_SECTOR)))
            jump = min(skip_len, sectors - bad)
            pieces.append(("silence", jump))
            if gaps and gaps[-1]["end"] == bad:
                gaps[-1]["end"] = bad + jump          # merge with the previous skip
            else:
                gaps.append({"start": bad, "end": bad + jump})
            log.warning("track %d: skipped %.1fs of unreadable audio at %d:%02d", n, jump / SECTORS_PER_SEC,
                        bad / SECTORS_PER_SEC // 60, bad / SECTORS_PER_SEC % 60)
            offset = bad + jump
            if offset >= sectors:
                break
        if gaps:
            tmp = wav + ".joined"
            join_pieces(pieces, tmp)
            for kind, val in pieces:
                if kind == "file" and val[0] != wav:
                    try:
                        os.remove(val[0])
                    except OSError:
                        pass
            os.replace(tmp, wav)
        took = time.time() - start
        skipped = sum(g["end"] - g["start"] for g in gaps) / SECTORS_PER_SEC
        log.info("track %d ripped in %dm%02ds (%.1fx)%s", n, took // 60, took % 60,
                 sectors / SECTORS_PER_SEC / max(took, 1),
                 f", {skipped:.1f}s skipped" if gaps else (" after being stuck" if warned else ""))
        return [{"at": round(g["start"] / SECTORS_PER_SEC, 1), "len": round((g["end"] - g["start"]) / SECTORS_PER_SEC, 1)}
                for g in gaps]

    def _rip_piece(self, n, wav, offset, sectors, start, status, warned):
        """Run cdparanoia from `offset` to the end of the track.

        Returns (outcome, sectors, warned): ("done", sectors written) or
        ("skip", whole sectors written before the bad spot), plus whether a stuck alert went out.
        """
        expected = sectors * BYTES_PER_SECTOR
        errlog = wav + ".log"
        with open(errlog, "w") as ef:
            p = subprocess.Popen(self.track_cmd(n, wav, offset), stdout=subprocess.DEVNULL, stderr=ef)
        piece_start = last_growth = time.time()
        last_size = 0
        outcome = None
        try:
            while p.poll() is None:
                time.sleep(1)
                now = time.time()
                try:
                    size = max(0, os.path.getsize(wav) - _pcm_start(wav))
                except OSError:
                    size = 0
                if size > last_size:
                    last_size, last_growth = size, now
                stalled = now - last_growth
                self.max_stall = max(getattr(self, "max_stall", 0), stalled)
                done = offset * BYTES_PER_SECTOR + size
                speed = (done / BYTES_PER_SECTOR / SECTORS_PER_SEC) / max(now - start, 1)
                stuck = stalled >= STALL_WARN
                status(track_pct=min(done / expected, 1.0) if expected else 0, speed=round(speed, 1),
                       elapsed=int(now - start), stalled=int(stalled) if stuck else 0, stuck=stuck)
                if stuck and not warned:
                    warned = True
                    log.warning("track %d stuck: no progress for %ds at %d%%", n, stalled, done * 100 // max(expected, 1))
                    status(notify=True)
                if self.abort:
                    raise RuntimeError(self.abort)
                if self.skip:
                    self.skip = False
                    outcome = "skip"
                    break
                limit = config.STUCK_LIMIT * 60
                if limit and stalled >= limit:
                    if config.STUCK_ACTION == "skip":
                        log.info("track %d stuck for %d minutes: skipping the bad spot", n, config.STUCK_LIMIT)
                        outcome = "skip"
                        break
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
        if outcome == "skip":
            status(message_override="skipping", stuck=False, stalled=0)
            try:
                size = max(0, os.path.getsize(wav) - _pcm_start(wav))
            except OSError:
                size = 0
            return "skip", min(size // BYTES_PER_SECTOR, sectors - offset), warned
        if p.returncode != 0 or not os.path.exists(wav):
            raise RuntimeError(f"Track {n} failed to rip: {(err or ['unknown error'])[-1]}")
        return "done", sectors - offset, warned

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
                disc.update(status="ripping", error="", tracks=[], done_tracks=0, seconds=0.0, drive=self.dev, skipped=0)
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
        self.skip = False
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
                    if kw.pop("message_override", None) == "skipping":
                        store.set_drive(self.dev, message=f"Disc {num}: skipping the bad spot in track {i + 1}", **kw)
                        return
                    stuck = kw.get("stuck")
                    store.set_drive(self.dev, message=(f"Disc {num}: stuck on track {i + 1} of {len(toc)}" if stuck else label), **kw)

                wav = os.path.join(ddir, f"t{n:02d}.wav")
                flac = os.path.join(ddir, f"t{n:02d}.flac")
                gaps = self.rip_track(n, wav, sectors, status)
                to_flac(wav, flac)
                with store.lock():
                    t = {"n": n, "file": os.path.basename(flac), "seconds": sectors / SECTORS_PER_SEC}
                    if self.max_stall >= STALL_WARN:
                        t["stuck_secs"] = int(self.max_stall)
                    if gaps:
                        t["gaps"] = gaps
                        disc["skipped"] = round(disc.get("skipped", 0) + sum(g["len"] for g in gaps), 1)
                    disc["tracks"].append(t)
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
        if disc.get("skipped"):
            bad = ", ".join(str(t["n"]) for t in disc["tracks"] if t.get("gaps"))
            notify("Disc done (with skips)",
                   f"Disc {num} of {title} is ripped, but {disc['skipped']:g}s of unreadable audio was skipped "
                   f"(track {bad}). Insert disc {num + 1}.")
        else:
            notify("Disc done", f"Disc {num} of {title} is ripped. Insert disc {num + 1}.")
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

    def track_cmd(self, n, wav, offset=0):
        freq = 220 + 110 * ((self.pending or 1) % 4) + 20 * n
        secs = (next(s for t, s, _ in self._toc if t == n) - offset) / SECTORS_PER_SEC
        tmp = wav + ".gen.wav"
        stuck = "1" if (os.environ.get("DEMO_STUCK") and n == 2 and offset == 0) else "0"
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
