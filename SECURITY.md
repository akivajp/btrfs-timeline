# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.3.x | Yes |
| 0.2.x | No — upgrade |
| 0.1.x | No — upgrade |

## Reporting a vulnerability

Please report security issues privately via
[GitHub Security Advisories](https://github.com/akivajp/btrfs-timeline/security/advisories/new)
rather than opening a public issue.

## Threat model

Two things about this tool decide its risk: it **serves the contents of files over
HTTP**, and it **writes files** on request. The Cockpit module carries the same risk
through a different door: it starts the CLI as the logged-in user, without asking for
privilege escalation, so it can reach exactly what that user can reach.

Design assumptions:

- **It runs unprivileged, as the person who started it**, and it deliberately avoids
  `btrfs subvolume list` so that browsing history never needs root. It can therefore read
  exactly what that user can read — and, through `restore`, write what that user can
  write. Running it as root hands that reach to whoever can talk to it.
- **Loopback is the default, and non-loopback without `--auth` is refused**, because
  anyone who can reach the port can read your files and overwrite them. `--allow-no-auth`
  exists for people who mean it.
- **HTTP Basic auth provides no confidentiality.** Deploy behind TLS or a VPN when the
  network is not trusted. There is a single set of credentials, no per-user roles and no
  audit log.
- **Basic auth alone does not stop another site** from making your browser send a
  request, because the browser attaches the credentials itself. Restoring is a `POST`
  and is rejected when the request carries a foreign `Origin`.
- **`--root` is a confinement, not a sandbox.** It is compared with `realpath`, so a
  symlink inside it cannot be used to read outside it, but it does not defend against a
  local user who can already read those files by other means. `--read-only` disables
  writing entirely.
- **Snapshot contents are not trusted.** Paths inside snapshots are inspected with
  `lstat` and symlinks are never followed out of a snapshot, so a link planted in one
  cannot make the tool report — or restore — live content as if it were old.
- File names and contents are rendered as text by the browser front-end (`textContent`,
  never `innerHTML`), so a crafted file name is not markup.
- **External commands are never run through a shell.** They are argument vectors passed
  to `subprocess` directly, so a path containing shell metacharacters is an argument and
  nothing else. Commands that can lose data declare themselves as such and are refused
  by the runner until a caller confirms them explicitly.
