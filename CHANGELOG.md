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
