// ダッシュボードの組み立て。
//
// ファイル履歴の画面と同じく、データの取得は transport.js に任せる。
// Cockpit 版でもこのファイルはそのまま使える。
//
// **この画面は何も実行しない。** 状態を読み、root が要る操作については
// 「端末で実行すべきコマンド」を危険度とともに示すだけにしてある。
// サーバーは利用者の権限で動くので、ここから実行しても失敗するだけであり、
// 失敗するボタンは無いほうがよい。

import {
  FLAVOUR, canElevate, fetchConfig, fetchDevices, fetchScrub, onPrivilegeChange,
} from './transport.js';
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

  const temperature = device.temperature === null || device.temperature === undefined
    ? '-'
    : `${device.temperature.toFixed(0)} °C`;

  // 壊れかけを探しに来る画面なので、そこが目立たないと意味が無い。
  // 温度の閾値はデバイス自身の申告 (too_hot) に従い、こちらでは決めない
  return row(
    [device.devid, device.path || '-', (device.model || '-').trim(),
     formatSize(device.size), temperature, state, errors || '-'],
    device.missing || device.has_errors || device.too_hot ? 'alarm' : '');
});

// マウント先ごとのデバイスは出さない。
//
// btrfs では **どのマウント先も、全デバイスに支えられている**。mountinfo に出る
// デバイスは「マウント時にどのデバイスノードを指定したか」でしかなく、
// そのマウントがそこに載っているという意味ではない。列にすると全行が同じ 1 台に
// なり、しかもそれが事実だと読めてしまう。関係は文章で 1 度述べるほうが正しい。
const mountRows = (mounts) => mounts.map((mount) => row([
  mount.path, mount.subvol || '-',
]));

const allocationRowsWithSpread = (allocations, deviceCount) =>
  allocations.map((allocation) => row([
    allocation.kind,
    allocation.profile || '-',
    deviceCount,
    formatSize(allocation.total_bytes),
    formatSize(allocation.used_bytes),
  ]));

// デバイスごとの割り当て内訳。root でしか読めないので、得られたときだけ出す。
// **空の表を出して「何も載っていない」と見せない。**
const usageRows = (devices) => {
  const rows = [];
  devices.forEach((device) => {
    const allocations = (device.usage && device.usage.allocations) || {};
    Object.entries(allocations).forEach(([name, size]) => {
      rows.push(row([device.path || '-', name, formatSize(size)]));
    });
    if (device.usage && device.usage.unallocated !== null
        && device.usage.unallocated !== undefined) {
      rows.push(row([device.path || '-', t('web.column.unallocated'),
                     formatSize(device.usage.unallocated)]));
    }
  });
  return rows;
};

const renderFilesystem = (filesystem, scrub) => {
  const section = node('section', 'filesystem');
  section.append(node('h2', null, filesystem.label || filesystem.uuid));

  const state = node('p', filesystem.degraded ? 'warning' : 'note');
  state.textContent = t('devices.state', {
    state: t(filesystem.degraded ? 'devices.state.degraded' : 'devices.state.ok'),
    operation: filesystem.exclusive_operation || '-',
  }).trim();
  section.append(state);

  // どのサブボリュームがどこに出ているか。btrfs では 1 つのファイルシステムが
  // 複数の場所に現れるので、これが見えないと一覧と実感が結び付かない
  section.append(table(
    ['web.column.mounted-at', 'web.column.subvolume'],
    mountRows(filesystem.mounts || [])));

  section.append(table(
    ['devices.column.devid', 'devices.column.device', 'devices.column.model',
     'devices.column.size', 'devices.column.temperature',
     'devices.column.state', 'devices.column.errors'],
    deviceRows(filesystem.devices)));

  section.append(table(
    ['devices.column.allocation', 'devices.column.profile',
     'devices.column.devices', 'devices.column.total', 'devices.column.used'],
    allocationRowsWithSpread(filesystem.allocations, filesystem.devices.length)));

  const breakdown = usageRows(filesystem.devices);
  if (breakdown.length) {
    section.append(node('h3', null, t('web.usage.title')));
    section.append(table(
      ['devices.column.device', 'devices.column.allocation', 'devices.column.size'],
      breakdown));
  }

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

// 内訳が出せない理由は経路によって違う。「権限が足りない」と「この経路では無理」は
// 利用者にとって別の話なので、同じ文言で済ませない。
const renderElevationHint = (dashboard) => {
  const section = node('section', 'filesystem hint');
  section.append(node('h2', null, t('web.elevate.title')));
  section.append(node('p', 'note', t(
    FLAVOUR === 'cockpit' ? 'web.elevate.cockpit' : 'web.elevate.standalone')));
  // ボタンは置かない。昇格の UI は Cockpit のヘッダーが持っており、
  // こちらから促しても応答が返らないまま待ち続けるだけだった。
  // 切り替えは監視しているので、有効にすれば勝手に読み直す
  dashboard.append(section);
};

const hasBreakdown = (filesystems) => filesystems.some(
  (filesystem) => filesystem.devices.some(
    (device) => device.usage && Object.keys(device.usage.allocations || {}).length));

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

  if (!hasBreakdown(data.filesystems)) {
    renderElevationHint(dashboard);
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

// **どの経路から呼ばれても、失敗が「読み込み中…」のまま残らないようにする。**
// 昇格を頼んで断られたときにここが無く、画面が固まったままになっていた。
const guarded = async (action) => {
  try {
    await action();
  } catch (error) {
    setStatus(error.message, true);
  }
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
  await guarded(load);
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
  el('language').addEventListener('change',
    (event) => guarded(() => changeLanguage(event.target.value)));

  // 管理アクセスを切り替えたら、その場で読み直す。利用者に「更新する」という
  // 手順を覚えさせない
  await onPrivilegeChange(() => guarded(load));

  await guarded(load);
};

start().catch((error) => setStatus(error.message, true));
