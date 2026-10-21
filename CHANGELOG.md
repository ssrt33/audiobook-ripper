# Changelog

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
