// ダッシュボードの組み立て。
//
// ファイル履歴の画面と同じく、データの取得は transport.js に任せる。
// Cockpit 版でもこのファイルはそのまま使える。
//
// **この画面は何も実行しない。** 状態を読み、root が要る操作については
// 「端末で実行すべきコマンド」を危険度とともに示すだけにしてある。
// サーバーは利用者の権限で動くので、ここから実行しても失敗するだけであり、
// 失敗するボタンは無いほうがよい。

import { fetchConfig, fetchDevices, fetchScrub } from './transport.js';
import { translate } from './i18n.js';

let catalog = {};
let config = {};

const el = (id) => document.getElementById(id);
const t = (key, params) => translate(catalog, key, params);

const setStatus = (message, isError) => {
  const node = el('status');
  node.textContent = message || '';
  node.classList.toggle('error', Boolean(isError));
};

const formatSize = (size) => {
  if (size === null || size === undefined) return '-';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let value = size;
  for (let i = 0; i < units.length; i += 1) {
    if (value < 1024 || i === units.length - 1) {
      return i === 0 ? `${value} B` : `${value.toFixed(1)} ${units[i]}`;
    }
    value /= 1024;
  }
  return `${size} B`;
};

const node = (tag, className, text) => {
  const made = document.createElement(tag);
  if (className) made.className = className;
  if (text !== undefined && text !== null) made.textContent = String(text);
  return made;
};

// 行は必ず {className, values} の形で渡す。
//
// 「配列でも {values} でも受ける」ようにしたところ、Array.prototype.values が
// **イテレータのメソッドとして存在する** ために `cells.values || cells` が
// 関数を返し、表が一切描けなくなった。曖昧に受けるより、形を 1 つに決める。
const row = (values, className) => ({ values, className: className || '' });

const table = (headerKeys, rows) => {
  const made = node('table');
  const head = node('thead');
  const headRow = node('tr');
  headerKeys.forEach((key) => headRow.append(node('th', null, t(key))));
  head.append(headRow);
  const body = node('tbody');
  rows.forEach((entry) => {
    const line = node('tr');
    if (entry.className) line.className = entry.className;
    entry.values.forEach((value) => line.append(node('td', null, value)));
    body.append(line);
  });
  made.append(head, body);
  return made;
};

// ---------------------------------------------------------------------------
// 実行するコマンドの提示
// ---------------------------------------------------------------------------

const riskLabel = (risk) => t({
  safe: 'risk.safe', caution: 'risk.caution', dangerous: 'risk.dangerous',
}[risk] || 'risk.safe');

const commandBlock = (command) => {
  const box = node('div', 'command');
  const line = node('code', 'path', command.command);
  const badge = node('span', `risk risk-${command.risk}`, riskLabel(command.risk));
  const summary = node('p', 'note', command.summary);
  box.append(badge, line, summary);
  return box;
};

// ---------------------------------------------------------------------------
// ファイルシステム 1 つ分
// ---------------------------------------------------------------------------

const deviceRows = (devices) => devices.map((device) => {
  let state = t('devices.device.ok');
  if (device.missing) state = t('devices.device.missing');
  else if (!device.writeable) state = t('devices.device.readonly');
  else if (device.replace_target) state = t('devices.device.replacing');

  const errors = Object.entries(device.errors || {})
    .filter(([, value]) => value)
    .map(([name, value]) => `${name}=${value}`)
    .join(' ');

  // エラーが記録されているデバイスは目立たせる。0 かどうかだけが重要
  return row(
    [device.devid, device.path || '-', formatSize(device.size), state, errors || '-'],
    device.missing || device.has_errors ? 'alarm' : '');
});

const allocationRows = (allocations) => allocations.map((allocation) => row([
  allocation.kind,
  allocation.profile || '-',
  formatSize(allocation.total_bytes),
  formatSize(allocation.used_bytes),
]));

const renderFilesystem = (filesystem, scrub) => {
  const section = node('section', 'filesystem');
  section.append(node('h2', null, filesystem.label || filesystem.uuid));

  const summary = node('p', 'note');
  summary.textContent = t('devices.mounted', {
    paths: filesystem.mount_points.join(' ') || '-',
  }).trim();
  section.append(summary);

  const state = node('p', filesystem.degraded ? 'warning' : 'note');
  state.textContent = t('devices.state', {
    state: t(filesystem.degraded ? 'devices.state.degraded' : 'devices.state.ok'),
    operation: filesystem.exclusive_operation || '-',
  }).trim();
  section.append(state);

  section.append(table(
    ['devices.column.devid', 'devices.column.device', 'devices.column.size',
     'devices.column.state', 'devices.column.errors'],
    deviceRows(filesystem.devices)));

  section.append(table(
    ['devices.column.allocation', 'devices.column.profile',
     'devices.column.total', 'devices.column.used'],
    allocationRows(filesystem.allocations)));

  if (scrub) {
    section.append(node('h3', null, t('web.scrub.title')));
    section.append(node('p', 'note', t('web.scrub.state', {
      state: scrub.state,
      errors: scrub.error_summary || '-',
    })));
    if (scrub.running && scrub.percent !== null) {
      section.append(node('p', 'note', `${scrub.percent}%`));
    }
  }

  if (filesystem.command) {
    section.append(node('h3', null, t('web.command.read')));
    section.append(commandBlock(filesystem.command));
  }

  return section;
};

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------

const applyLabels = () => {
  document.documentElement.lang = config.language;
  el('language-text').textContent = t('web.language');
  el('link-history').textContent = t('web.page.history');
  el('link-devices').textContent = t('web.page.devices');
};

const renderLanguageOptions = () => {
  const select = el('language');
  select.replaceChildren();
  (config.languages || []).forEach((entry) => {
    const option = node('option', null, entry.name);
    option.value = entry.code;
    select.append(option);
  });
  select.value = config.language;
};

const load = async () => {
  setStatus(t('web.loading'));
  const data = await fetchDevices();
  const dashboard = el('dashboard');
  dashboard.replaceChildren();

  for (const filesystem of data.filesystems) {
    // scrub の状態は非特権で読めるので、取れたら出す。取れなくても画面は成立する
    let scrub = null;
    const mount = filesystem.mount_points[0];
    if (mount) {
      try {
        scrub = await fetchScrub(mount);
      } catch (error) {
        scrub = null;
      }
    }
    dashboard.append(renderFilesystem(filesystem, scrub));
  }

  if ((data.suggestions || []).length) {
    const section = node('section', 'filesystem');
    section.append(node('h2', null, t('web.command.suggested')));
    // ここから実行はしない。端末で使う前提で、そのまま読める形で置く
    section.append(node('p', 'note', t('web.command.why')));
    data.suggestions.forEach((command) => section.append(commandBlock(command)));
    dashboard.append(section);
  }

  setStatus('');
};

const changeLanguage = async (code) => {
  config = await fetchConfig(code);
  catalog = config.catalog;
  try {
    localStorage.setItem('btrfs-timeline.language', config.language);
  } catch (error) {
    // 覚えられなくても操作自体は成立している
  }
  applyLabels();
  await load();
};

const start = async () => {
  let remembered = null;
  try {
    remembered = localStorage.getItem('btrfs-timeline.language');
  } catch (error) {
    remembered = null;
  }
  config = await fetchConfig(remembered);
  catalog = config.catalog;
  renderLanguageOptions();
  applyLabels();
  el('language').addEventListener('change', (event) => changeLanguage(event.target.value));
  await load();
};

start().catch((error) => setStatus(error.message, true));
