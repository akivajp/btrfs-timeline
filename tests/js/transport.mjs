// テスト用の transport。本物と同じ形の JSON を返すだけで、通信はしない。
//
// 差し替えられるのがこのファイルだけ、という設計がそのまま役に立っている。
// Cockpit 版が cockpit.spawn に差し替えるのと同じ場所を、テストが差し替える。

import { catalogs } from './fixture.mjs';

const version = (index, overrides) => ({
  index, path: `/root/.snapshots/${index}/snapshot/notes.txt`,
  size: 5, mtime: '2026-01-01T00:00:00+00:00', exists: true, is_live: false,
  first_snapshot_id: String(index), last_snapshot_id: String(index),
  first_seen: '2026-01-01T00:00:00+00:00', last_seen: '2026-01-01T00:00:00+00:00',
  snapshot_count: 1, ...overrides,
});

const entry = (name, isDirectory, overrides) => ({
  name, path: `/root/${name}`, is_directory: isDirectory, is_symlink: false,
  size: isDirectory ? 0 : 5, mtime: '2026-01-01T00:00:00+00:00',
  exists_now: true, ...overrides,
});

export const fetchConfig = async (lang) => ({
  version: '0.0.0',
  language: lang || 'en',
  languages: [{ code: 'en', name: 'English' }, { code: 'ja', name: '日本語' }],
  catalog: catalogs[lang] || catalogs.en,
  read_only: false,
  root: '/root',
  start_path: '/root',
});

export const fetchBrowse = async (path, snapshot, showHidden) => ({
  path,
  parents: ['/root', path].filter((value, index, all) => all.indexOf(value) === index),
  snapshot: snapshot ? { id: snapshot, taken_at: '2026-01-01T00:00:00+00:00' } : null,
  entries: [
    entry('sub', true),
    entry('notes.txt', false),
    ...(showHidden ? [entry('.hidden', false)] : []),
    ...(snapshot ? [entry('gone.txt', false, { exists_now: false })] : []),
  ],
});

export const fetchHistory = async (path) => ({
  target: path,
  versions: [
    version(1),
    version(2, {
      is_live: true, path, first_snapshot_id: null, last_snapshot_id: null,
      first_seen: null, last_seen: null, snapshot_count: 0,
    }),
  ],
});

export const fetchPreview = async (path, index) => ({
  path, index: Number(index), size: 5, binary: false, truncated: false,
  text: 'first\n',
});

export const fetchDiff = async (path, from, to) => ({
  path, from: Number(from), to: Number(to || 2),
  text: '--- #1\n+++ #2\n-first\n+live\n', identical: false,
  binary: false, truncated: false,
});

export const restore = async (payload) => ({
  target: payload.path, index: payload.index,
  source: '/root/.snapshots/1/snapshot/notes.txt',
  destination: '/root/notes.20260101T000000.txt',
  in_place: Boolean(payload.in_place), backup: null, is_symlink: false, size: 5,
  method: payload.dry_run ? 'dry-run' : 'reflink', dry_run: Boolean(payload.dry_run),
});
