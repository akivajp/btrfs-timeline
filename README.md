# btrfs-timeline

Browse and restore previous versions of your files from btrfs snapshots — the way
Time Machine does it, from a browser.

[日本語版 README はこちら](README.ja.md)

> **Status: early development.** Browsing history, previewing versions and restoring
> files all work — from the CLI, from a standalone web UI, and as a Cockpit module.
> See [Roadmap](#roadmap).

![The web UI: a file browser on the left, and on the right the versions of the selected file, each with a preview and a restore button](https://raw.githubusercontent.com/akivajp/btrfs-timeline/main/docs/screenshot-en.png)

One file, four rows: a stretch when it did not exist yet, two contents it has held since,
and the file as it stands now. Forty-eight snapshots went into that — the ones that
changed nothing are not worth a row.

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
btrfs_timeline/core/          Library: snapshot discovery, version history, restore
btrfs_timeline/cli.py         CLI with --json on every subcommand  <- the shared contract
btrfs_timeline/web/static/    The screen: index.html + app.js + style.css
                 transport.js   <- the only file a front-end replaces
   |- standalone web   imports core directly (single process)
   |- Cockpit module   cockpit.spawn([... , "--json"], {superuser: "require"})
   `- desktop GUI      imports core, or calls the CLI
```

A Cockpit module has **no server side** — it is static files plus a `manifest.json`, and
it reaches the system by spawning processes from the browser. So the only shape a shared
core can take is *a CLI that emits JSON*. Every subcommand here has `--json` for that
reason; treat its output as a public API.

The screen is built in the browser, not on the server. A Cockpit module has no server
to render with, so anything baked into the HTML could not be reused there. Instead
`app.js` imports its data from `transport.js`, and that one file is what each front-end
swaps: `fetch('./api/history?…')` here, `cockpit.spawn` there. The rest of the UI — and
the translations, which come from the same JSON catalogs the CLI uses — is shared.

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

# What is in this directory? (the web UI uses the same call)
btrfs-timeline browse ~/Documents --json

# What was in it back then — including what has been deleted since
btrfs-timeline browse ~/Documents --snapshot 1729

# What changed between a version and the file as it is now
btrfs-timeline diff ~/notes.md --from 2
btrfs-timeline diff ~/notes.md --from 2 --to 3

# Show a past version, or what changed
btrfs-timeline preview ~/notes.md --index 2
btrfs-timeline diff ~/notes.md --from 2

# Open the web UI
btrfs-timeline serve
```

Example output:

```
/home/akiva/.gitconfig
  # FIRST SEEN           LAST SEEN                  SIZE   SNAPS STATE
  1 2024-10-02 02:00:08  2025-01-01 00:00:00           -       3 (does not exist)
  2 2025-12-01 00:00:08  2026-03-01 00:00:00       268 B       4 ok
  3 2026-04-01 00:00:00  2026-09-23 01:00:00       297 B      28 ok
  4 -                    -                         297 B       - live
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

## Web UI

```shell
pipx install 'btrfs-timeline[web]'
btrfs-timeline serve                       # http://127.0.0.1:8088/
btrfs-timeline serve --root ~/Documents    # only allow reading below this directory
btrfs-timeline serve --read-only           # history only, no restoring
```

Walk the filesystem on the left, click a file, and its versions appear on the right.
Each version can be previewed before you decide, and restored with one button. Restoring
always shows a dry run of exactly what it will write — and where the current content will
be kept — before asking you to confirm.

There is **one time axis, not a set of modes**. Everything the screen does is a
combination of two things: a path, and a point in time.

- Click a **directory** and you get its history too. Pick one of its versions and the
  listing on the left becomes what that directory held at that moment.
- Files that were **deleted since** appear there, struck through. They are invisible in
  the current filesystem, so this is the only way to reach them — and from there their
  history and restore work exactly as they do for any other file.
- The preview pane doubles as a **diff**: compare a version against the current file, or
  against any other version, with the selector next to the toggle.

No tabs were added for any of this. Adding one screen per feature would mean learning the
same "look at the past" gesture three times over.

Two settings sit in the page and are remembered per browser: the **language**, picked
from the same catalogs the CLI uses, and whether to **show hidden files** — off by
default, because a home directory is mostly dotfiles and the things you came for get
buried in them.

It listens on loopback only by default, and it deliberately refuses to listen on any
other address without `--auth USER:PASSWORD` (also read from `BTRFS_TIMELINE_AUTH`),
because anyone who can reach it can read your files and write over them. `--allow-no-auth`
overrides that if you really mean it. Restoring is a `POST` and is rejected when the
request carries a foreign `Origin`: basic auth alone would not stop another site from
making your browser send the request for you.

The server runs as you, so it can only read what you can read. It does not need root —
that is the point of not using `btrfs subvolume list`.

## Cockpit module

If you already run [Cockpit](https://cockpit-project.org/), the same screen is available
inside it:

```shell
btrfs-timeline cockpit install     # into ~/.local/share/cockpit
btrfs-timeline cockpit install --system   # into /usr/share/cockpit, for every user
btrfs-timeline cockpit uninstall
```

Reload Cockpit and look under Tools for **File history**. No restart, no service.

It is the same `index.html`, `app.js`, `i18n.js` and `style.css` — copied, not rewritten.
The only file that differs is `transport.js`, which calls the CLI instead of an HTTP API:

```js
cockpit.spawn([...COMMAND, 'history', path, '--json'])
```

**It asks for no privilege escalation.** Browsing snapshots does not need root, and
restoring writes as the logged-in user, so there is no reason to demand more. The tests
run the real `app.js` through both transports and assert the two end up with the same
screen.

Because `cockpit.spawn` does not inherit your shell's `PATH`, `install` records the
absolute path of the installation you ran it from. Run it again after you change how
`btrfs-timeline` itself is installed.

## Translations

Messages are translated at runtime from JSON catalogs in
[`btrfs_timeline/locales/`](btrfs_timeline/locales/). `en.json` is the reference, and any
key a catalog is missing falls back to English — so a partial translation is useful from
its very first line.

```shell
btrfs-timeline history ~/notes.md --lang ja   # this invocation only
BTRFS_TIMELINE_LANG=ja btrfs-timeline ...     # this shell
```

With neither, the language comes from `LC_ALL`, `LC_MESSAGES` or `LANG`, and falls back
to English; `LANG=C` means English. Only human-readable output is translated. `--json` is
byte-identical in every language, because front-ends and scripts consume it.

### Adding a language

Copy `en.json` to `<code>.json` — an ISO 639-1 code such as `de`, or a regional variant
such as `pt_br`, which falls back to `pt` if that catalog exists — and translate the
values, starting with `language.name`, which is how your language names *itself* (`日本語`,
not `Japanese`): that string is what the web UI's language picker shows, so someone who
reads only your language has to be able to find it. **No code changes are needed.** The
new language is picked up automatically, both in the values `--lang` accepts and in the
picker.

The test suite (`pytest tests/test_i18n.py`) enforces two rules:

- Every key from `en.json` is present, and nothing extra is.
- Placeholder *names* match (`{path}`, `{index}`). Their order within a sentence is yours
  to change — that is exactly why they are named rather than positional.

Tables are aligned by terminal display width rather than character count, so East Asian
full-width characters line up correctly.

Why JSON and not gettext: `.po` files need a compilation step to `.mo`, and the web UI
and Cockpit module render in the browser, where `.mo` is unusable. A single catalog
format that both Python and JavaScript can read keeps the translations from being
maintained twice.

## Supported snapshot layouts

| Layout | Path shape | Typical source |
| --- | --- | --- |
| snapper | `<mount>/.snapshots/<N>/snapshot` + `info.xml` | snapper |
| flat | `<mount>/.snapshots/<name>` | btrbk, Timeshift, manual |

Snapshot timestamps come from snapper's `info.xml` (which records **UTC**, despite
carrying no timezone marker), from a timestamp embedded in the directory name, or from
the directory mtime, in that order.

## Roadmap

1. **File history browser, standalone web UI** — done
2. **Cockpit module** — done: same CLI, same screen, only `transport.js` differs
3. **Dashboard and device management** — the current focus: devices, RAID profile,
   scrub/balance progress
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
- Restoring a whole directory is not supported yet — only individual files. You can see
  what a directory held at any point and restore the files out of it one by one, but
  there is no single "put this directory back". Restoring part of a tree silently would
  be worse than refusing.
- A directory's history comes from the directory's own mtime, which btrfs preserves. That
  changes when entries are added, removed or renamed — not when a file inside is edited.
  So directory versions mark *what came and went*, which is the granularity you want for
  finding something deleted; use the file's own history for edits.
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
