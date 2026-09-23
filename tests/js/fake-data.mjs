// テスト用のデータ。本物の CLI / API と同じ形の JSON を組み立てるだけで、
// 通信もプロセス起動もしない。
//
// スタンドアロン版の transport と Cockpit 版の cockpit.spawn スタブが、
// **同じここを参照する**。画面に届くデータが同じであることを前提に、
// 経路だけが違う 2 通りで同じ操作を走らせて比べられるようにするため。

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

export const fakeConfig = async (lang) => ({
  version: '0.0.0',
  language: lang || 'en',
  languages: [{ code: 'en', name: 'English' }, { code: 'ja', name: '日本語' }],
  catalog: catalogs[lang] || catalogs.en,
  read_only: false,
  root: '/root',
  start_path: '/root',
});

export const fakeBrowse = async (path, snapshot, showHidden) => ({
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

export const fakeHistory = async (path) => ({
  target: path,
  versions: [
    version(1),
    version(2, {
      is_live: true, path, first_snapshot_id: null, last_snapshot_id: null,
      first_seen: null, last_seen: null, snapshot_count: 0,
    }),
  ],
});

export const fakePreview = async (path, index) => ({
  path, index: Number(index), size: 5, binary: false, truncated: false,
  text: 'first\n',
});

export const fakeDiff = async (path, from, to) => ({
  path, from: Number(from), to: Number(to || 2),
  text: '--- #1\n+++ #2\n-first\n+live\n', identical: false,
  binary: false, truncated: false,
});

export const fakeRestore = async (payload) => ({
  target: payload.path, index: payload.index,
  source: '/root/.snapshots/1/snapshot/notes.txt',
  destination: '/root/notes.20260101T000000.txt',
  in_place: Boolean(payload.in_place), backup: null, is_symlink: false, size: 5,
  method: payload.dry_run ? 'dry-run' : 'reflink', dry_run: Boolean(payload.dry_run),
});

// --- ダッシュボード -------------------------------------------------------

const device = (devid, name, overrides) => ({
  devid, name, path: `/dev/${name}`, size: 1024 ** 4,
  missing: false, writeable: true, replace_target: false,
  errors: {
    write_io_errs: 0, read_io_errs: 0, flush_io_errs: 0,
    corruption_errs: 0, generation_errs: 0,
  },
  has_errors: false, ...overrides,
});

export const fakeDevices = async () => ({
  filesystems: [{
    uuid: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    label: 'tank',
    mount_points: ['/mnt/tank'],
    degraded: true,
    exclusive_operation: 'none',
    command: {
      argv: ['btrfs', '--format', 'json', 'device', 'stats', '/mnt/tank'],
      command: 'btrfs --format json device stats /mnt/tank',
      risk: 'safe', needs_root: false, summary: 'read the error counters',
    },
    devices: [
      device(1, 'sdd1'),
      // 欠損と、エラーを抱えたデバイス。画面が目立たせられるかを見る
      device(2, 'sdc1', { missing: true }),
      device(3, 'sda1', {
        errors: { corruption_errs: 7 }, has_errors: true,
      }),
    ],
    allocations: [
      { kind: 'data', profile: 'raid1', total_bytes: 1000, used_bytes: 500 },
      { kind: 'metadata', profile: 'raid1', total_bytes: 200, used_bytes: 100 },
    ],
  }],
  suggestions: [{
    argv: ['sudo', 'btrfs', 'scrub', 'start', '/mnt/tank'],
    command: 'sudo btrfs scrub start /mnt/tank',
    risk: 'caution', needs_root: true,
    summary: 'read every block and repair what it can',
  }],
});

export const fakeScrub = async (path) => ({
  path, state: 'running', running: true, percent: 48.59,
  total_bytes: 100, scrubbed_bytes: 48,
  duration: '0:00:05', time_left: '0:00:05', eta: '',
  error_summary: 'no errors found', has_errors: false, counters: {},
  raw: 'Status: running\n', ok: true,
  command: {
    argv: ['btrfs', 'scrub', 'status', '-R', '--raw', path],
    command: `btrfs scrub status -R --raw ${path}`,
    risk: 'safe', needs_root: false, summary: 'read the scrub state',
  },
});
