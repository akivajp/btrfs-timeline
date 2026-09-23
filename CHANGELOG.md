# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-23

First public release. Nothing here is a change from an earlier version; this is what the
release contains.

### Browsing history

- Snapshot discovery for the snapper layout (`<mount>/.snapshots/<N>/snapshot` with
  `info.xml`) and for the flat layouts used by btrbk, Timeshift and manual setups. It
  reads `/proc/self/mountinfo` and walks directories rather than calling
  `btrfs subvolume list`, which requires root — so **browsing history does not**.
- File version history, deduplicated by mtime and size, keeping the periods when a file
  did not exist as entries of their own. Copy-on-write means an unchanged file otherwise
  appears once per snapshot.
- Directory history, and listing a directory as it stood at a point in time. Entries
  deleted since then show up there, which is the only way to reach them — the current
  filesystem no longer has them — and their own history and restore work as usual from
  that point. Directory versions come from the directory's mtime, so they mark when
  entries came and went, not when a file inside was edited.
- Diffs between a version and the current file, or between any two versions.

### Restoring

- `restore` brings a version back with the `FICLONE` ioctl, sharing extents with the
  snapshot: instant regardless of size, no extra space, falling back to a plain copy
  elsewhere and reporting which happened.
- Non-destructive by default: the recovered version is written beside the original as
  `notes.<timestamp>.md`. `--in-place` overwrites, and even then the current content is
  kept as a backup unless `--no-backup --force`.
- Writes go through a temporary file in the destination directory and `rename(2)`, so an
  interrupted restore leaves no partial file. Symlinks are recreated as symlinks; the
  target is resolved with realpath, so restoring a symlinked dotfile in place does not
  replace the link itself.

### Command line

- `history`, `restore`, `diff`, `browse`, `snapshots`, `mounts` and `serve`, every one of
  them with `--json`. That output is the contract the other front-ends share: a Cockpit
  module has no server side and can only spawn a process, so a JSON-emitting CLI is the
  one shape a shared core can take.

### Web UI

- `serve` runs a standalone web UI with no build step and no JavaScript toolchain: walk
  the filesystem, see a file's versions, preview one, compare it, restore it.
- One time axis rather than a mode per feature. Picking a directory version re-lists the
  left pane at that moment; the preview pane doubles as the diff view.
- The screen is rendered in the browser and gets its data through `transport.js`, so the
  Cockpit module can reuse all of it by replacing that one file.
- Loopback only by default; it refuses any other address without `--auth` (or
  `--allow-no-auth` said explicitly). Restoring is a `POST` and is rejected on a foreign
  `Origin`. `--root` confines reading to a subtree, compared with realpath so a symlink
  inside cannot escape, and `--read-only` disables restoring. The server runs
  unprivileged, as the user.

### Translations

- Messages come from JSON catalogs under `btrfs_timeline/locales/`, with English as the
  reference and fallback. English and Japanese ship with the package. The language comes
  from `--lang`, `BTRFS_TIMELINE_LANG` or the usual locale variables, and the web UI has
  a picker that lists each language by the name it gives itself.
- Adding a language means adding one JSON file and touching no code. Tests check that
  every catalog carries exactly the keys of `en.json` with matching placeholder names,
  and that every key the code looks up exists.
- JSON rather than gettext, because `.po` needs a compilation step and the web UI and
  Cockpit module render in the browser, where `.mo` is unusable. One catalog format both
  Python and JavaScript can read keeps the translations from being maintained twice.
- Terminal tables are padded by display width, so East Asian full-width characters line
  up.

[0.1.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.1.0
