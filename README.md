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

**Keep the work folder outside your Audiobookshelf library.** Audiobookshelf's live folder
watcher picks up any new audio file under the library, so rips in a work folder inside it show
up as untitled books (named after random IDs) that turn into "missing" issues once the book is
built. An `.ignore` file doesn't stop the watcher. A sibling folder such as
`/mnt/tank/audiobooks_work` next to `/mnt/tank/audiobooks` is fine. The app logs a warning at
startup if it finds the work folder inside the library.

If you already ripped into a folder inside the library: stop the app, move the folder out (if it's
its own dataset, use `zfs rename`, and stop Audiobookshelf and any SMB share on it first), point
the `/work` volume at the new spot, then in Audiobookshelf run **Scan** and **Issues > Remove All**.

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
| `DRIVES` | `auto` | `auto` watches every CD drive it can see, including one plugged in or renamed later. Or list them: `/dev/sr0,/dev/sr1`. Add `demo` for a fake drive to try the app without CDs. |
| `FOLDER_TEMPLATE` | `{author}/{series}/{series_seq_title}` | Library layout. Fields: `author series series_seq title series_seq_title year narrator`. Empty parts are dropped. |
| `PUID` / `PGID` | `568` | Owner of files it writes. |

The page settings can also be given starting values in the YAML (`AUTO_EJECT`, `AUTO_START_BOOK`,
`KEEP_RIPS`, `DEFAULT_CHANNELS` (default `2`, stereo), `DEFAULT_BITRATE`, `DEFAULT_CHAPTERS`,
`PARANOIA_OPTS`, `AUDIBLE_REGION`, `NOTIFY_URL`). Once something is saved from the page, the page wins.

## Phone alerts

Get a notification when a disc is done and it's time to put in the next one, when a disc fails,
and when a book is ready. It uses [ntfy](https://ntfy.sh), a free notification app with no account.

1. **Install the ntfy app** from the App Store (iPhone) or Play Store (Android) and allow notifications.
2. **Pick a channel name nobody will guess.** Anyone who knows the name can read the messages, so
   use something random like `abr-7f3k9q2m8x1d` rather than `audiobooks`. To make one in the
   TrueNAS shell: `python3 -c "import secrets;print('abr-'+secrets.token_hex(6))"`
3. **Subscribe in the app:** tap **+**, enter your channel name, leave the server as `ntfy.sh`,
   and tap **Subscribe**.
4. **Add it to the ripper:** in the web page's **Settings** panel, paste
   `https://ntfy.sh/your-channel-name` into **Alert address**, click outside the box to save, then
   press **Test**. Your phone should buzz within a few seconds.

What you'll get:

- **Disc done**: "Disc 3 of *Book* is ripped. Insert disc 4."
- **Disc failed**: which disc and why, so you can clean it and put it back in.
- **Audiobook ready**: the M4B is in your library. If it had bad spots, this alert carries the
  [bad-spot report](#bad-spot-reports) as an attached .txt instead.
- **Build failed**: if a book couldn't be built.

If the test doesn't arrive, check the app's notification permission, then look at the app's logs for
a `notify failed` line (the NAS may not be able to reach ntfy.sh). Any service that accepts a
plain-text POST works too, including a self-hosted ntfy server.

## Bad-spot reports

When a book finishes building and any of its discs had trouble, you get a short .txt report:

```
SKIPPED (replaced with silence – audio is missing here)
  Book time   Chapter            Disc / track   In track   Length
    5:12:40   Disc 9             Disc  9 /  4       2:05   2s

STUCK BUT RECOVERED (nothing skipped – worth a listen, may have clicks)
  Track starts  Chapter            Disc / track   Stuck for
    6:01:15     Disc 11            Disc 11 /  2   6:50
```

**Book time** is where the spot is in the finished book, so you can jump straight to it in
Audiobookshelf and hear whether it matters. Books that ripped cleanly don't get a report.

Where it goes:

- **Your phone**, attached to the "Audiobook ready" alert, if phone alerts are on. (A server that
  doesn't take attachments gets the report as the message text instead.)
- **Email**, if you set it up (below).
- The **Report** button on the book in the Books list, and the `reports` folder in the app's config folder.

### Email

Put the address in **Settings → Email reports to**, and add your mail server to the stack. For Gmail,
make an [app password](https://myaccount.google.com/apppasswords) (needs 2-step verification on), then:

```yaml
      REPORT_EMAIL: you@gmail.com
      SMTP_HOST: smtp.gmail.com
      SMTP_PORT: "587"
      SMTP_USER: you@gmail.com
      SMTP_PASSWORD: "abcd efgh ijkl mnop"   # the app password, not your normal one
```

Redeploy, then press **Test** next to the email address. If the server hangs up because the port and encryption
don't match, the ripper tries the other combination (587 with STARTTLS, 465 with SSL) on its own,
and if every try fails the message lists what it tried. Other providers work the same way with
their own server and port (`465` for SSL, `587` for STARTTLS). `SMTP_FROM` sets a different sender.

## Is it stuck?

While a disc rips, the banner shows how far into the current track it is, how long it's been on
that track and the ripping speed. A clean disc usually rips at 4–10× speed, so a track takes a
minute or two.

If a track writes no new audio for 2 minutes, the banner turns amber and says which track is
stuck and for how long, and you get a phone alert if alerts are on. That almost always means a
scratch or smudge that the drive keeps re-reading. Your options:

- **Wait.** Many discs get past the bad spot on their own after a few minutes.
- **Press "Skip the bad spot".** Everything ripped so far is kept, the unreadable stretch becomes
  silence (2 seconds to start with) and the rip carries on with the rest of the track and disc.
  If it gets stuck again right after, press it again; each repeat skips twice as much, up to 30
  seconds. The disc row then shows which track has a gap and where (for example
  `Track 9: 2s skipped at 14:05`), so you can listen there, and the "disc done" alert mentions it.
- **Press "Give up on this disc".** The rip stops, the disc is marked failed and ejected. Clean it
  (wipe from the centre outwards) and put it back in; it re-rips into the same slot.
- **Let the app decide.** In Settings, set **When a track is stuck** to act after 3–30 minutes,
  and **What to do then** to either *Give up on the disc* or *Skip the bad spot and keep going*.
  Skipping is the better choice for unattended rips of audiobooks, where a second or two of
  silence usually costs a word or two.
- **Limit how many spots get skipped.** **Give up on a disc after** (default *5 bad spots*) stops a
  badly damaged disc from turning into a patchwork: when automatic skipping reaches one more spot
  than the limit, the disc is marked failed and ejected, with a phone alert, so you can clean it
  and re-rip it into the same slot. A long scratch that needs several jumps in a row counts as one
  spot, and pressing **Skip the bad spot** yourself is never blocked.
- **For badly scratched discs**, set **Rip mode** to *Thorough* before re-inserting. It's slower but
  tries harder. *Fast* is the opposite: quicker on clean discs, less careful on damaged ones.

The log also shows each track's time and speed (`track 9 ripped in 4m12s (3.1x)`), a warning
when one gets stuck, and a line for each skip (`track 9: skipped 2.0s of unreadable audio at 14:05`).

## Disk space

Rips are kept as FLAC until the book is built: roughly 300–400 MB per disc. A 15-disc
book needs about 6 GB in the work dataset while you're ripping. The finished M4B at the default 128 kbps
stereo is about 58 MB per hour, so a 17-hour book is around 1 GB. Pick 64 kbps in Settings to halve that.

## Troubleshooting

- **Drive shows "Drive disconnected"** – the drive dropped off the NAS. USB drives do this
  after a power dip or with a loose cable, often during long rips. Unplug it, wait a few seconds,
  plug it back in, then run `ls -l /dev/sr*` to see its name. The disc that was ripping shows as
  failed; put it back in and it re-rips into the same slot. Earlier discs are kept.
- **App won't start: "error gathering device information … /dev/sr0"** – Docker won't start a
  container whose `devices:` entry is missing. Reconnect the drive (above), or switch to hot-plug
  mode (the commented block in the example YAML) so the app starts without the drive and picks it
  up when it appears.
- **Drive keeps disconnecting** – try a different USB port (rear ports on the board are better
  than front or hub ports), a shorter cable, or a drive with its own power supply.
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
