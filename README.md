# btrfs-timeline

Browse and restore previous versions of your files from btrfs snapshots — the way
Time Machine does it, from a browser.

[日本語版 README はこちら](README.ja.md)

> **Status: early development.** The core and the CLI work and are tested — browsing
> history and restoring files are both usable today. The web UI is being built next.
> See [Roadmap](#roadmap).

## Why this exists

If you run btrfs with automatic snapshots (snapper, btrbk, Timeshift), you already have
every previous version of every file. Getting one back, however, means knowing where the
snapshots live, guessing which one is old enough, and comparing files by hand on the
command line. It works, but it is expert-only — and it is the one thing people actually
need snapshots for.

The tools that exist today each stop somewhere short of this:

| Tool | What it does | Why it may not fit |
| --- | --- | --- |
| [httm](https://github.com/kimono-koans/httm) | Excellent interactive file history for ZFS/btrfs | CLI/TUI only |
| Btrfs Assistant, Snapper GUI | Snapshot management | Desktop app; not per-file history |
| Samba `vfs_shadow_copy2` | "Previous Versions" in Windows Explorer | Requires a Windows client |
| Rockstor, OpenMediaVault | Full NAS web UI with btrfs support | NAS-only distributions; share-level rollback, not per-file |
| Cockpit `storaged` | btrfs filesystem/subvolume creation | No snapshot browsing; multi-device btrfs unsupported |

Nothing lets you open a browser, point at a path, and walk back through time.
That is what this is.

## Design

The project is meant to ship in three forms, and they share one thing:

```
btrfs_timeline/core/   Library: snapshot discovery, version history, restore
btrfs_timeline/cli.py  CLI with --json on every subcommand  <- the shared contract
   |- standalone web   imports core directly (single process)
   |- Cockpit module   cockpit.spawn([... , "--json"], {superuser: "require"})
   `- desktop GUI      imports core, or calls the CLI
```

A Cockpit module has **no server side** — it is static files plus a `manifest.json`, and
it reaches the system by spawning processes from the browser. So the only shape a shared
core can take is *a CLI that emits JSON*. Every subcommand here has `--json` for that
reason; treat its output as a public API.

Two decisions follow from measurements rather than taste:

- **No `btrfs subvolume list`.** It requires root (`Operation not permitted` otherwise),
  while the files inside snapshots are readable under ordinary permissions. Discovery is
  done with `/proc/self/mountinfo` plus directory traversal, so **browsing history does
  not need root**.
- **Deduplication is mandatory.** btrfs is copy-on-write, so an unchanged file appears
  once per snapshot. On the author's machine, 35 snapshots collapse to a single version.
  Versions are merged by mtime and size, and the periods when a file did *not* exist are
  preserved as their own entries — otherwise a deleted-then-recreated file looks
  continuous.

## Installation

```shell
pipx install btrfs-timeline          # CLI only, no dependencies
pipx install 'btrfs-timeline[web]'   # with the standalone web UI
```

Requires Python 3.9+ and Linux. `btrfs-progs` is *not* required for browsing history.

## Usage

```shell
# What versions of this file exist?
btrfs-timeline history ~/notes.md

# Machine-readable, for scripts and for the Cockpit module
btrfs-timeline history ~/notes.md --json

# Which snapshots cover this path, and what layout are they in?
btrfs-timeline snapshots ~/

# Which btrfs mounts does this system have?
btrfs-timeline mounts
```

Example output:

```
/home/akiva/.gitconfig
  # FIRST SEEN           LAST SEEN                  SIZE  SNAPS  STATE
  1 2024-10-02 02:00:08  2025-01-01 00:00:00           -      3  (does not exist)
  2 2025-12-01 00:00:08  2026-03-01 00:00:00       268 B      4  ok
  3 2026-04-01 00:00:00  2026-09-23 01:00:00       297 B     28  ok
  4 -                    -                         297 B      -  live
```

Thirty-five snapshots, three meaningful versions, and the gap before the file was
created — which is the point.

## Restoring

```shell
# Bring back the newest version found in snapshots, next to the original file
btrfs-timeline restore ~/notes.md

# Pick a version by the number shown in the "#" column of `history`
btrfs-timeline restore ~/notes.md --index 2

# Or by snapshot id
btrfs-timeline restore ~/notes.md --snapshot 10129

# Show what would happen and change nothing
btrfs-timeline restore ~/notes.md --dry-run

# Write somewhere else, or overwrite the original (a backup is kept)
btrfs-timeline restore ~/notes.md --to /tmp/notes.old.md
btrfs-timeline restore ~/notes.md --in-place
```

Three properties matter here:

- **Nothing is overwritten by default.** The restored file is written next to the
  original as `notes.20260401T000008.md`. The worst failure mode for a history tool is
  destroying the current content of a file while trying to get an old one back, so it is
  excluded by default rather than guarded by a prompt. `--in-place` overwrites, and even
  then the current content is kept as `notes.before-restore.<timestamp>.md` unless you
  pass both `--no-backup` and `--force`.
- **It is a reflink, not a copy.** Restoring uses the `FICLONE` ioctl, so it shares
  extents with the snapshot: instant regardless of file size, and no extra space used.
  If the destination is on another filesystem it falls back to a plain copy, and the
  output says which one happened.
- **Writes are atomic.** Content goes to a temporary file in the destination directory
  and is moved into place with `rename(2)`, so an interrupted restore never leaves a
  half-written file.

## Supported snapshot layouts

| Layout | Path shape | Typical source |
| --- | --- | --- |
| snapper | `<mount>/.snapshots/<N>/snapshot` + `info.xml` | snapper |
| flat | `<mount>/.snapshots/<name>` | btrbk, Timeshift, manual |

Snapshot timestamps come from snapper's `info.xml` (which records **UTC**, despite
carrying no timezone marker), from a timestamp embedded in the directory name, or from
the directory mtime, in that order.

## Roadmap

1. **File history browser, standalone web UI** — the current focus
   (core, CLI and restore are done)
2. **Cockpit module** — same CLI, shared front-end code, for people who already run Cockpit
3. **Dashboard and device management** — devices, RAID profile, scrub/balance progress
4. **Desktop GUI**

Device management (3) can destroy a filesystem when it goes wrong, which is a different
class of risk from reading history. It will stay a separate module with separate
privileges.

## Notes and limitations

- Symlinks are resolved against the *live* filesystem, so history follows the target of a
  link (useful for dotfiles). Links inside snapshots are never followed, so a snapshot
  never reports live content as if it were old.
- Versions are compared by mtime and size, not by content hash. Hashing would mean
  reading every version of every file.
- Restoring a whole directory is not supported yet — only individual files. Restoring
  part of a tree silently would be worse than refusing.
- A restored file keeps the original's permissions and mtime, but not its owner:
  `chown` requires root, and this tool is meant to run unprivileged.
- btrfs RAID 5/6 is still not considered production-ready upstream; this tool does not
  change that.

## Alternatives

If you want a terminal tool rather than a browser, use
[httm](https://github.com/kimono-koans/httm) — it is mature, fast, and covers ZFS too.
If you want a full NAS appliance, use [Rockstor](https://rockstor.com/) or
[OpenMediaVault](https://www.openmediavault.org/).

## License

MIT — see [LICENSE](LICENSE).
