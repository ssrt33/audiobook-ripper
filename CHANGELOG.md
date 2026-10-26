# Changelog

## 1.6.0 — 2026-10-25

### Added
- **Bad-spot report.** When a book finishes building and any disc had skipped or stuck spots,
  the app writes a .txt report listing each one with its position in the finished book (and
  chapter), so you can jump to it in Audiobookshelf. Clean books don't get one.
- The report is **attached to the "Audiobook ready" phone alert** (ntfy), falling back to the
  report as the message text for servers that don't take attachments.
- **Email reports**: set the address on the page and `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` in
  the stack (Gmail app passwords work). **Test** button next to the address.
- **Report** button on finished books in the Books list; reports are also kept in `config/reports`.
- Tracks that got stuck for 2+ minutes but recovered are now remembered, so the report can
  point them out.

## 1.5.0 — 2026-10-23

### Added
- **Skip the bad spot** button on the stuck banner. Instead of losing the whole disc to one
  unreadable patch, the app keeps the audio ripped so far, fills the bad stretch with silence
  (2 seconds, doubling up to 30 if it's stuck again right after) and carries on ripping the rest
  of the track and the disc.
- **Skips are flagged.** The disc row shows each gap, like `Track 9: 2s skipped at 14:05`, the
  "disc done" alert says how much was skipped and on which track, and the log has a line per skip.
- **"What to do then" setting** next to the stuck time limit: give up on the disc (as before) or
  skip the bad spot and keep going, for unattended rips. Also settable with `STUCK_ACTION=skip`.

### Changed
- The stuck setting is now labelled **When a track is stuck** (just warn me, or act after 5–30
  minutes), since the action can now be either giving up or skipping.

## 1.4.0 — 2026-10-22

### Added
- **See when a rip is stuck.** The banner now shows progress inside the current track, how long
  it's been on that track and the ripping speed (for example `This track: 63% · 2m 10s · 4.1× speed`),
  and the progress bar moves smoothly instead of jumping a track at a time.
- **Stuck warning.** If a track writes no new audio for 2 minutes, the banner turns amber with
  "Disc 9: stuck on track 9 of 17", how long it's been stuck, and what that usually means
  (a scratched or dirty disc being re-read). A phone alert goes out too, if alerts are on.
- **Give up on this disc** button on the stuck banner. It stops the rip straight away, marks the
  disc as failed and ejects it, so you can clean it and put it back in (it re-rips into the same slot).
- **"Give up on a stuck track" setting**: never (just warn, the default), or after 5, 10, 20 or
  30 minutes with no progress.
- **Per-track log lines**, like `track 9 ripped in 4m12s (3.1x)`, and a warning line when a track
  gets stuck, so the log shows exactly where time went.

### Fixed
- Removing a disc while it's ripping now stops it immediately instead of after the current track.
- Reading the track list gives up after 3 minutes instead of waiting forever on a bad disc.

## 1.3.0 — 2026-10-21

### Fixed
- **Stopping or updating the app no longer force-kills it** (exit code 137). It now saves and
  exits straight away when Docker asks it to stop, instead of being killed 10 seconds later.

### Added
- **`DRIVES=auto`** (now the default) watches every CD drive it can see, including one plugged in
  later or one that comes back under a new name like `/dev/sr1` after a USB drop-out.
- **Clear "Drive disconnected" message** on the page when a drive drops off, plus a log line when
  it goes away and when it's back.
- **Optional hot-plug mode** in the example YAML, so the container can start without a drive
  attached and keep going if a USB drive drops out.

### Changed
- The app can read a drive plugged in after start-up (adds the usual `cdrom` group).

## 1.2.0 — 2026-10-21

### Changed
- **Refreshed web page, dark theme only.** New look throughout: a sticky header with drive status,
  a larger "insert disc / ripping" banner with an animated disc and progress bar, a bigger cover,
  cleaner inputs and dropdowns, numbered disc rows, status pills in the Books list, and tidier
  Settings. The output path and **Finish book** button now sit on one line. The page no longer
  switches to a light theme when your computer is in light mode.

## 1.1.2 — 2026-10-21

### Fixed
- **README no longer says the work folder can live inside the Audiobookshelf library.** The
  `.ignore` file only stops Audiobookshelf's full scans, not its live folder watcher, so rips
  showed up as untitled books with ID names and then as "missing" issues. Keep the work folder
  outside the library; the README now explains how to move it and clean up.

### Added
- **Startup warning** in the log when the work folder is inside the library folder.

## 1.1.1 — 2026-10-21

### Changed
- New books default to **128 kbps** (was 64 kbps) for better sound, especially in stereo.
  A finished book is about 58 MB per hour. Pick a lower quality in Settings to save space.

## 1.1.0 — 2026-10-21

### Added
- **Settings panel in the web page.** Auto-eject, auto-start books, keep rips after build,
  new-book defaults (sound, quality, chapters), rip mode, Audible lookup region and phone alerts
  are now changed from the page. Changes save instantly to `/config/settings.json` and survive
  restarts and updates. Paths, drives and file ownership stay in the YAML.
- **Test button for phone alerts**, so you can check your ntfy (or other) address works.
- **Version and "finished" lines in the log.** The app prints its version when it starts and a
  `finished … -> path` line when a book is done building.
- **Prebuilt image on GitHub Container Registry** (`ghcr.io/ssrt33/audiobook-ripper`, amd64 and
  arm64). No build step needed, and the Update button in TrueNAS Apps or Dockge now works.

### Changed
- New books default to **stereo** instead of mono.
- Example YAML files use the published image instead of building locally.

### Fixed
- Finished books show their cover in the Books list after the rips are deleted.
- The image sets file permissions itself, so it no longer fails with "Permission denied" when the
  project was copied onto a dataset that uses ACLs.

## 1.0.0 — 2026-10-21

First release.

- Watches the optical drive and rips each audio CD with cdparanoia as soon as it goes in.
- Groups discs into a book in the order you insert them; spots a disc you've already ripped and
  ejects it; a failed disc re-rips into the same slot.
- Looks up title, author, narrator, series, description and cover from Audible and Apple Books,
  and compares the running time with your discs to catch abridged editions.
- Builds one M4B per book with chapters (per disc or per track), cover art and tags, in an
  `Author/Series/# - Title` layout for Audiobookshelf, Plex and Jellyfin.
- Web page with live progress, disc reordering and a chime when it's time to swap discs.
- TrueNAS Custom App YAML and Dockge/compose example.
