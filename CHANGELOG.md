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
