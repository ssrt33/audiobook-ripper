"""Bad-spot report for a finished book: where audio was skipped or a track got stuck.

Made after the M4B is built, so every spot can be given as a position in the finished book
(and its chapter) that you can jump to in Audiobookshelf. Only books with something to report
get one. It's kept in CONFIG_DIR/reports, sent to the phone (ntfy, with the .txt attached)
and emailed, whichever of those are set up.
"""
import os
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from email.message import EmailMessage

from . import builder, config
from .util import chown, log, makedirs, safe_name

STUCK_REPORT = 120  # a track that stalled this long (seconds) is worth mentioning even if it recovered


def hms(sec):
    sec = int(sec)  # round down, so jumping to it lands just before the spot
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def spots(book):
    """Every bad spot in play order: skipped stretches, and stuck tracks that recovered."""
    chapters = builder.chapter_list(book)

    def chapter_at(pos):
        for title, start, end in chapters:
            if start <= pos < end:
                return title
        return None

    out, pos = [], 0.0
    for dn, _d, t in builder.all_tracks(book):
        for g in t.get("gaps") or []:
            at = pos + g["at"]
            out.append({"kind": "skip", "disc": dn, "track": t["n"], "in_track": g["at"], "len": g["len"],
                        "book_pos": at, "chapter": chapter_at(at)})
        if not t.get("gaps") and (t.get("stuck_secs") or 0) >= STUCK_REPORT:
            out.append({"kind": "stuck", "disc": dn, "track": t["n"], "stuck_secs": t["stuck_secs"],
                        "book_pos": pos, "chapter": chapter_at(pos)})
        pos += t["seconds"]
    return out


def make(book):
    """(subject, short summary, full text) or None when the rip was clean."""
    found = spots(book)
    if not found:
        return None
    skips = [s for s in found if s["kind"] == "skip"]
    stuck = [s for s in found if s["kind"] == "stuck"]
    total = sum(s["len"] for s in skips)
    title = book.get("title") or "Untitled"
    bits = []
    if skips:
        bits.append(f"{len(skips)} bad spot{'s' if len(skips) != 1 else ''} skipped ({total:g}s of silence)")
    if stuck:
        bits.append(f"{len(stuck)} track{'s' if len(stuck) != 1 else ''} stuck but recovered")
    summary = f"{title}: " + ", ".join(bits) + "."

    lines = [f"Rip report – {title}"]
    if book.get("author"):
        lines.append(f"by {book['author']}")
    lines += [f"Built {time.strftime('%Y-%m-%d %H:%M', time.localtime(book.get('finished') or time.time()))}",
              f"Saved as {book.get('output') or '?'}", ""]
    if skips:
        lines += ["SKIPPED (replaced with silence – audio is missing here)",
                  "  Book time   Chapter            Disc / track   In track   Length"]
        for s in skips:
            lines.append(f"  {hms(s['book_pos']):>9}   {(s['chapter'] or '-')[:17]:<17}  "
                         f"Disc {s['disc']:>2} / {s['track']:>2}   {hms(s['in_track']):>8}   {s['len']:g}s")
        lines += [f"  Total: {total:g}s in {len(skips)} spot{'s' if len(skips) != 1 else ''}.", ""]
    if stuck:
        lines += ["STUCK BUT RECOVERED (nothing skipped – worth a listen, may have clicks)",
                  "  Track starts  Chapter            Disc / track   Stuck for"]
        for s in stuck:
            lines.append(f"  {hms(s['book_pos']):>12}  {(s['chapter'] or '-')[:17]:<17}  "
                         f"Disc {s['disc']:>2} / {s['track']:>2}   {hms(s['stuck_secs'])}")
        lines.append("")
    lines += ["To check a spot, open the book in Audiobookshelf and jump to the book time.",
              "If it matters, clean that disc and re-rip it (keep rips on to rebuild without re-ripping the rest)."]
    text = "\n".join(lines) + "\n"
    return f"Audiobook ready, with bad spots: {title}", summary, text


def save(book, text):
    folder = os.path.join(config.CONFIG_DIR, "reports")
    makedirs(folder)
    name = f"{time.strftime('%Y-%m-%d')} {safe_name(book.get('title'), 'Untitled')} rip report.txt"
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write(text)
    chown(path)
    return path


# ---------------------------------------------------------------- delivery

def send_phone(subject, summary, text, filename):
    """ntfy with the report attached; falls back to the report as the message text."""
    if not config.NOTIFY_URL:
        return None
    headers = {"Title": subject.encode("ascii", "ignore").decode(), "Tags": "cd,warning",
               "Filename": filename.encode("ascii", "ignore").decode() or "rip-report.txt",
               "Message": summary.encode("ascii", "ignore").decode()}
    try:
        req = urllib.request.Request(config.NOTIFY_URL, data=text.encode(), headers=headers, method="PUT")
        urllib.request.urlopen(req, timeout=20).read()
        return "phone (file attached)"
    except Exception as e:
        log.info("report attachment not accepted (%s); sending it as text", e)
    try:  # not ntfy, or attachments are off on a self-hosted server
        body = text if len(text) < 3800 else text[:3700] + "\n… (full report in the ripper's config/reports folder)"
        req = urllib.request.Request(config.NOTIFY_URL, data=body.encode(), method="POST",
                                     headers={"Title": headers["Title"], "Tags": "cd,warning"})
        urllib.request.urlopen(req, timeout=20).read()
        return "phone (as text)"
    except Exception as e:
        log.warning("report alert failed: %s", e)
        return None


def email_ready():
    return bool(config.REPORT_EMAIL and config.SMTP_HOST)


def send_email(subject, summary, text, filename, to=None):
    to = to or config.REPORT_EMAIL
    if not (to and config.SMTP_HOST):
        return None
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM or config.SMTP_USER or to
    msg["To"] = to
    msg.set_content(summary + "\n\n" + text)
    msg.add_attachment(text.encode(), maintype="text", subtype="plain", filename=filename)
    ctx = ssl.create_default_context()
    if config.SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30, context=ctx)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
    with server:
        if config.SMTP_PORT != 465:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=ctx)
                server.ehlo()
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
    return f"email to {to}"


def deliver(book):
    """Make, save and send the report. Returns what happened, for the page; None if clean."""
    made = make(book)
    if not made:
        return None
    subject, summary, text = made
    path = save(book, text)
    filename = os.path.basename(path)
    sent = []
    r = send_phone(subject, summary, text, filename)
    if r:
        sent.append(r)
    if email_ready():
        try:
            sent.append(send_email(subject, summary, text, filename))
        except Exception as e:
            log.warning("report email failed: %s", e)
            sent.append(f"email failed: {e}")
    log.info("rip report for %s: %s (%s)", book.get("title"), summary, ", ".join(sent) or "saved only")
    return {"path": path, "summary": summary, "sent": sent, "at": time.time()}
