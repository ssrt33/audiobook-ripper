# Audiobook Ripper for TrueNAS

Rips audiobook CDs on your TrueNAS box and turns each multi-disc set into a single
M4B file with chapters, cover art and proper tags, in a folder layout that
Audiobookshelf, Plex and Jellyfin all pick up.

How it works day to day:

1. Put disc 1 in the drive. A new book starts and the disc rips (the page shows progress).
2. When it ejects, put in disc 2, then 3, and so on. Each disc is added to the book in order.
   Inserting a disc that's already ripped is detected and it just ejects.
3. Open the web page, press **Look up book** to fill in title, author, narrator, series,
   description and cover from Audible or Apple Books. The running time is compared with what
   you ripped so you can spot abridged editions.
4. Press **Finish book**. It builds the M4B in the background while you start the next book.

Finished books go to `Author/Series/# - Title/Title.m4b` (or `Author/Title/Title.m4b`
without a series), plus `cover.jpg`, `desc.txt` and `reader.txt` for Audiobookshelf.

## Install on TrueNAS 25.10 (Goldeye)

You need an optical drive plugged into the NAS. USB drives work fine.

### 1. Check the drive shows up

In **System > Shell**:

```sh
ls -l /dev/sr*
```

You should see `/dev/sr0`. If not, try another USB port or cable.

### 2. Make datasets

Create (or pick) a dataset for finished books, e.g. `tank/audiobooks`, and one for the
app, e.g. `tank/apps/audiobook-ripper`. Give the `apps` user (568) read/write on both.
If Audiobookshelf already has a library dataset, point the ripper straight at it.

### 3. Install the app

The image is published at `ghcr.io/ssrt33/audiobook-ripper` (amd64 and arm64), so there's
nothing to build.

**Apps > Discover Apps > Custom App > Install via YAML**. Name it `audiobook-ripper`, paste
`truenas-app.yaml`, change the three `/mnt/tank/...` paths to yours, and Save.

Open `http://<your-nas-ip>:8686`.

### Or install with Dockge

Create a stack named `audiobook-ripper` in Dockge, paste `docker-compose.example.yaml` into
the compose editor with your paths filled in, and press Deploy.

The work folder can live inside your Audiobookshelf library: the app drops an `.ignore` file
in it so half-finished rips aren't scanned as books.

### Updating

Use the **Update** button in TrueNAS Apps or Dockge, or run `sudo docker compose pull && sudo docker compose up -d`
in the stack folder. `latest` follows the main branch; pin a version tag such as
`ghcr.io/ssrt33/audiobook-ripper:1.0.0` if you'd rather update on your own schedule.

### Building it yourself

Clone the repo, replace `image:` with `build: .` in your compose file, and run
`sudo docker compose up -d --build`.

## Settings

### In the web page

The **Settings** panel has switches and menus for everything you'd change day to day. Changes
save straight away to `/config/settings.json` and survive restarts and updates.

- **Auto-eject**: open the tray when a disc finishes.
- **Auto-start books**: start a new book when a disc goes in and none is open.
- **Keep rips after build**: keep the FLAC rips so you can edit details and rebuild (about 350 MB a disc).
- **New book defaults**: sound (stereo or mono), quality (48–128 kbps, default 128), chapters (per disc, per track
  or none), rip mode (normal, fast for clean discs, thorough for scratched ones) and the Audible
  lookup region. Each book can still be changed on its own before you press Finish.
- **Phone alerts**: an address that gets a plain-text message on "disc done, insert the next one" and
  "book ready". An [ntfy](https://ntfy.sh) topic link works out of the box. Use **Test** to check it.

### In the YAML

Paths, drives and file ownership stay in the YAML.

| Variable | Default | What it does |
|---|---|---|
| `DRIVES` | `/dev/sr0` | Drives to watch, comma separated. Add `demo` for a fake drive to try the app without CDs. |
| `FOLDER_TEMPLATE` | `{author}/{series}/{series_seq_title}` | Library layout. Fields: `author series series_seq title series_seq_title year narrator`. Empty parts are dropped. |
| `PUID` / `PGID` | `568` | Owner of files it writes. |

The page settings can also be given starting values in the YAML (`AUTO_EJECT`, `AUTO_START_BOOK`,
`KEEP_RIPS`, `DEFAULT_CHANNELS` (default `2`, stereo), `DEFAULT_BITRATE`, `DEFAULT_CHAPTERS`,
`PARANOIA_OPTS`, `AUDIBLE_REGION`, `NOTIFY_URL`). Once something is saved from the page, the page wins.

## Disk space

Rips are kept as FLAC until the book is built: roughly 300–400 MB per disc. A 15-disc
book needs about 6 GB in the work dataset while you're ripping. The finished M4B at the default 128 kbps
stereo is about 58 MB per hour, so a 17-hour book is around 1 GB. Pick 64 kbps in Settings to halve that.

## Troubleshooting

- **Drive shows "Can't open drive"** – the `devices:` line in the YAML doesn't match your
  drive, or the drive was unplugged after the app started. Fix and restart the app.
- **A disc fails** – clean it and put it back in. It re-rips into the same slot.
  Badly scratched library discs may need `PARANOIA_OPTS: "-z"`.
- **Discs in the wrong order** – use the ↑ ↓ buttons before pressing Finish.
- **Logs** – Apps > audiobook-ripper > Logs.

## Trying it without a CD

```sh
pip install flask waitress
DRIVES=demo WORK_DIR=./work LIBRARY_DIR=./library CONFIG_DIR=./config python -m ripper
```

Open http://localhost:8686 and press **Insert test disc** a few times (needs `ffmpeg`).

Only rip CDs you own; this is for making a personal copy of your own collection.
