# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.1] - 2026-09-23

### Fixed

- **The history screen had no visible link to the dashboard.** The anchors were there
  with no text set, so they rendered as empty and unclickable — only the dashboard side
  had been given its labels. The drive tests now assert both links carry text.
- **The mount table no longer shows a device per mount.** In btrfs every mount is served
  by every device; the device in `mountinfo` only records which node was named at mount
  time. As a column it made all six rows show the same disk and read as though each mount
  lived there. The relationship is stated once instead, and the allocation table says how
  many devices each profile spreads across.

### Added

- `btrfs device usage` is offered as a command. Which allocation sits on which device in
  what quantity is the one part of this that sysfs does not expose — it needs the chunk
  information, which needs root — so it is shown rather than run.

## [0.7.0] - 2026-09-23

### Added

- **Which subvolume is mounted where.** A btrfs filesystem appears at several paths at
  once, and a flat list of them does not say that `/home` is `subvol=/@home`. The
  relationship is now shown, in the terminal and on the dashboard.
- **Device model and temperature, without root.** Both come from sysfs — hwmon, which the
  NVMe driver populates and `drivetemp` provides for SATA — so the dashboard can show
  them while still running as an ordinary user.
- A device is flagged as too hot against **the limit it reports itself** (`temp1_crit`),
  never a threshold chosen here. A number invented by this tool would eventually be wrong
  for somebody's disk, and a warning that is wrong stops being read.
- `smartctl` is offered as a command for devices that have recorded errors. Wear,
  reallocated sectors and power-on hours are past what sysfs exposes, and that is the one
  thing here that genuinely needs root — so it is shown rather than run.

## [0.6.0] - 2026-09-23

### Added

- **`device add`, `remove` and `replace`.** These can lose a pool, so they are the only
  operations marked `dangerous`, and **`--yes` does not satisfy them**: you confirm by
  typing the device itself. That guards against naming the wrong disk and not noticing,
  which is the failure that actually happens.
- For `replace`, the name to type is the **target** — the device being overwritten. The
  source is retired and its contents move; the target loses everything. Confirming the
  thing at risk is the only version of this that means anything.
- `add` stays `caution`: btrfs refuses a device that already holds a filesystem, so a
  typo is caught there. `--force` removes that check and the operation becomes dangerous.
- A `dangerous` operation cannot be built without a confirmation token. Forgetting one is
  a `ValueError` rather than a quietly weaker prompt.
- `scripts/loopback-lab.sh` builds a throwaway multi-device btrfs on loop devices, and
  `tests/test_lab.py` runs against it — really removing and re-adding a device — skipping
  entirely when it is absent. Device operations cannot be tested on a machine you care
  about, and they should not ship untested either.

### Fixed

- **`scrub status` was documented as working without root. It does not, in the case that
  matters.** btrfs writes `/var/lib/btrfs/scrub.status.<uuid>` as root-only, so the
  command succeeds until the first scrub has run and fails with `Permission denied`
  afterwards. The earlier check passed only because that filesystem had never been
  scrubbed. It is still attempted unprivileged, since the case where it works is real,
  but a failure now suggests the `sudo` command instead of being reported as success.
- Empty output is no longer parsed as "never scrubbed". btrfs saying `no stats available`
  and receiving nothing at all are different things, and reporting the second as the
  first made the screen state the opposite of the truth.

## [0.5.0] - 2026-09-23

### Added

- **A dashboard in the browser**, on its own page and linked from the history screen:
  what each filesystem is made of, allocation by profile, the scrub state, and devices
  with errors or missing devices marked so they are not buried. `/api/devices` and
  `/api/scrub` back it.
- The same page is installed into the Cockpit module as a second entry, sharing every
  file with the standalone one as the history screen already does.

### Notes

- **The dashboard is read-only on purpose.** Scrub and balance need root and the server
  runs as the invoking user, so a button there would always fail. The page prints the
  command to run in a terminal instead — `sudo` prefix and risk included, the same string
  the CLI would show. Privileged actions belong in the Cockpit module, which has a
  channel for them. A test asserts no endpoint exists that starts maintenance, so growing
  one by accident fails the suite.

## [0.4.0] - 2026-09-23

### Added

- **`scrub`** and **`balance`** — status, start, cancel, resume, and pause for balance.
  `balance start --usage N` rewrites only the chunks less than N%% full, which is the
  usual answer to fragmented free space and far lighter than a full balance. Both apply
  the filter to data and metadata, since `-dusage` alone leaves metadata behind.
- The disclosure from 0.3.0 now guards real writes. Before anything that changes state,
  the exact command and its declared risk are printed and a confirmation is asked for.
  Only `safe` operations skip the question, so the question keeps its meaning.
- **With no terminal to ask at, it refuses rather than assuming yes.** `--yes` is how you
  say yes in a script.
- **It does not quietly escalate.** When an operation needs root, it prints the exact
  command to run including the `sudo` prefix and stops; `--sudo` is how you ask it to add
  that itself. What it prints and what it runs are the same string, so a confirmation
  cannot be about a different command than the one that executes.
- `scrub status` parses `-R --raw` output — `--format json` does not cover it — and keeps
  the original text alongside the parsed fields, so nothing is hidden behind a possibly
  imperfect parser.

### Notes

- `corrected_errors` and `uncorrectable_errors` are not the same thing and are not
  reported as one. The first means a bad block was found and rebuilt from another copy;
  the second means it could not be, and that data is gone. Only the second is flagged.

## [0.3.0] - 2026-09-23

### Added

- **`devices`** — what each filesystem is made of, the profile of every allocation kind,
  whether a device is missing, whether an exclusive operation is running, and the
  per-device error counters. `--json` as usual.
- **It needs no root.** `btrfs filesystem show` does — it opens the raw block devices —
  so it is not used. The same information comes from `/sys/fs/btrfs/` plus
  `btrfs device stats --format json`, both readable by an ordinary user.
- **Commands are disclosed with their risk.** Every command run on the user's behalf is
  reported, in the terminal and in the JSON, together with a declared risk level: `safe`
  changes nothing, `caution` changes state but can be interrupted or undone, `dangerous`
  can lose data. A `dangerous` operation **cannot be executed until it is confirmed** —
  the refusal lives in the runner, not in each caller, so a call site cannot forget it.

### Notes

- `devid` is taken from `btrfs device stats`, not from the order of
  `/sys/fs/btrfs/<uuid>/devices/`. That directory is sorted by name, and on the author's
  machine `devid=1` is the *fourth* name — matching by order silently attributes every
  device's error counters to the wrong disk. A test fixes a layout where the two orders
  disagree.

## [0.2.1] - 2026-09-23

### Fixed

- The Cockpit menu entry stayed English in an otherwise translated interface. Cockpit
  does not translate `manifest.json` itself; it looks for a sibling
  `po.manifest.<language>.js` that maps the English string to a translation. `cockpit
  install` now generates those **from the same catalogs the rest of the tool uses**, so a
  language added as one JSON file gets its menu entry translated too, with no second
  place to keep in sync. A test pins the manifest's label to the English catalog entry,
  since that string is the key the translation is looked up by.

## [0.2.0] - 2026-09-23

### Added

- **Cockpit module.** `btrfs-timeline cockpit install` places it where Cockpit looks;
  it then appears under Tools as "File history". It is the same `index.html`, `app.js`,
  `i18n.js` and `style.css` the standalone UI uses, copied rather than rewritten — the
  only file that differs is `transport.js`, which calls the CLI through `cockpit.spawn`
  instead of an HTTP API. It requests **no privilege escalation**: browsing snapshots
  does not need root and restoring writes as the logged-in user. `install` records the
  absolute path of the installation it was run from, because `cockpit.spawn` does not
  inherit a shell `PATH`.
- `preview` shows a past version's contents — on its own, and as the call the Cockpit
  module makes where the web UI has `/api/preview`.
- `config` reports what a front-end needs to start: version, language, the available
  languages and the translation catalog. The web UI's `/api/config` is now built from
  the same function, so the two cannot drift.
- `restore --json` includes the version number it acted on, matching the web API.

### Testing

- The real `app.js` is driven through **both** transports and the two are asserted to end
  up with the same screen — the claim the architecture rests on.
- The CLI's `--json` and the HTTP API are asserted to return identical payloads for the
  same question, which is what lets the Cockpit module exist at all.

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

[0.7.1]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.7.1
[0.7.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.7.0
[0.6.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.6.0
[0.5.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.5.0
[0.4.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.4.0
[0.3.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.3.0
[0.2.1]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.2.1
[0.2.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.2.0
[0.1.0]: https://github.com/akivajp/btrfs-timeline/releases/tag/v0.1.0
