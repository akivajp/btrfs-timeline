# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Core snapshot discovery for the snapper layout (`<mount>/.snapshots/<N>/snapshot`)
  and for flat layouts used by btrbk, Timeshift and manual setups.
- File version history with deduplication by mtime and size, including gaps where
  the file did not exist.
- `btrfs-timeline history|snapshots|mounts` CLI, each with `--json` output.
- `btrfs-timeline restore`, which brings a previous version back with the `FICLONE`
  ioctl (reflink) and falls back to a plain copy when reflink is unavailable. It writes
  to a sibling file by default and never overwrites unless `--in-place` is given, in
  which case the current content is kept as a backup. Writes are atomic, and symlinks
  are recreated as symlinks rather than dereferenced.
- Version numbers in `history` output (the `#` column, and `index` in the JSON), which
  `restore --index` accepts.
- Localized messages, loaded at runtime from JSON catalogs under `btrfs_timeline/locales/`
  with English as the reference and fallback. English and Japanese ship with the package.
  The language comes from `--lang`, `BTRFS_TIMELINE_LANG`, or the usual locale variables.
  Adding a language means adding one JSON file and no code. `--json` output is unaffected.
- Table columns are aligned by terminal display width, so East Asian full-width
  characters no longer break the layout.
- `btrfs-timeline serve`, a standalone web UI: browse the filesystem, see a file's
  versions, preview one, and restore it. It listens on loopback by default, refuses any
  other address without `--auth`, rejects cross-origin restore requests, and can be
  confined with `--root` or made `--read-only`.
- `btrfs-timeline browse`, the directory listing the web UI navigates with, with `--json`
  so the Cockpit module can use the same call.
- The screen is rendered in the browser and gets its data through `transport.js`, so the
  Cockpit module can reuse it by replacing that one file.

- Directory history, and browsing a directory as it was at a point in time. Entries
  deleted since then appear in that listing, which is the only way to reach them — the
  current filesystem no longer has them. Their history and restore work as usual from
  there. Directory versions come from the directory's own mtime, so they mark when
  entries came and went rather than when a file inside changed.
- `btrfs-timeline diff` and `/api/diff`: what changed between a version and the current
  file, or between any two versions.
- `btrfs-timeline browse --snapshot ID` and `/api/browse?snapshot=ID` for the listing at
  a point in time, with `exists_now` on every entry.
- The web UI now has a single time axis rather than a mode per feature: picking a
  directory version re-lists the left pane at that moment, and the preview pane doubles
  as the diff view.

- A language picker in the web UI, listing each language by the name it gives itself in
  `language.name`, and a toggle for hidden files which is off by default. Both are
  remembered in the browser.

### Changed

- `/api/config` reports `languages` as `{code, name}` objects rather than bare codes.
- The `web` extra no longer needs Jinja2: the HTML carries no server-rendered data, which
  is what lets the Cockpit module serve the same file.

### Fixed

- The web UI printed a message key (`web.actions`) instead of nothing where a catalog
  entry is deliberately empty. Presence was tested with `||`, so an empty translation
  counted as missing; it is now tested for the key itself, matching the Python side.
- Static assets are served with `Cache-Control: no-cache`, so a browser revalidates them
  instead of pairing a freshly fetched page with a stale `app.js`. Revalidation still
  answers 304 when nothing changed.
- Selecting a file in the web UI failed with "Cannot access 'listing' before
  initialization": a local variable shadowed the helper it was assigned from.
- The browser-side translation lookup moved to `static/i18n.js`, which can be imported
  without touching the DOM, and is now covered by tests that run under Node when it is
  available — including a check that it agrees with the Python implementation.
- The web UI is now driven end to end under Node against a stubbed transport, with a
  stubbed DOM, so a mistake that only shows up when the page is used fails the suite
  instead of the screen. Skipped where Node is absent.
