"""Settings, all overridable with environment variables."""
import os


def _bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Optical drives to watch, comma separated. "auto" watches every /dev/srN, including drives
# plugged in or renamed later. "demo" adds a simulated drive for testing.
DRIVES = [d.strip() for d in os.environ.get("DRIVES", "auto").split(",") if d.strip()]

# Where in-progress rips live (needs ~350 MB per disc as FLAC until the book is built).
WORK_DIR = os.environ.get("WORK_DIR", "/work")
# Where finished books are written.
LIBRARY_DIR = os.environ.get("LIBRARY_DIR", "/audiobooks")
# Where the app keeps its own state file.
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")

PORT = int(os.environ.get("PORT", "8686"))

# Eject automatically when a disc finishes so you can drop in the next one.
AUTO_EJECT = _bool("AUTO_EJECT", True)
# Start a new untitled book on its own when a disc goes in and no book is open.
AUTO_START_BOOK = _bool("AUTO_START_BOOK", True)
# Keep the FLAC rips after a book is built (handy if you want to rebuild with other settings).
KEEP_RIPS = _bool("KEEP_RIPS", False)

# Extra cdparanoia flags. "" = normal paranoia, "-Y" = faster/less checking,
# "-z" = never give up on scratches (can be very slow).
PARANOIA_OPTS = os.environ.get("PARANOIA_OPTS", "").split()

# Give up on a track that has made no progress for this many minutes (0 = never give up).
STUCK_LIMIT = int(os.environ.get("STUCK_LIMIT", "0"))
# What to do when that limit is reached: "give_up" fails the disc; "skip" jumps over the bad spot,
# fills it with silence and keeps ripping the rest of the disc.
STUCK_ACTION = os.environ.get("STUCK_ACTION", "give_up")

# Defaults for new books (changeable per book in the web page).
DEFAULT_BITRATE = os.environ.get("DEFAULT_BITRATE", "128k")
DEFAULT_CHANNELS = int(os.environ.get("DEFAULT_CHANNELS", "2"))
DEFAULT_CHAPTERS = os.environ.get("DEFAULT_CHAPTERS", "disc")  # disc | track | none

# Folder layout under LIBRARY_DIR. Fields: {author} {series} {series_seq} {title} {year} {narrator}
# Empty path parts (e.g. no series) are dropped. This layout is what Audiobookshelf expects.
FOLDER_TEMPLATE = os.environ.get("FOLDER_TEMPLATE", "{author}/{series}/{series_seq_title}")

# Metadata lookup region for Audible: us, uk, ca, au, de, fr, it, es, in, jp
AUDIBLE_REGION = os.environ.get("AUDIBLE_REGION", "us")
ITUNES_COUNTRY = os.environ.get("ITUNES_COUNTRY", "us")

# Optional push notifications. Any URL that accepts a plain-text POST, e.g. an ntfy topic:
# https://ntfy.sh/my-secret-topic
NOTIFY_URL = os.environ.get("NOTIFY_URL", "").strip()

# Ownership for files written to the library (TrueNAS "apps" user is 568).
PUID = int(os.environ.get("PUID", "568"))
PGID = int(os.environ.get("PGID", "568"))
