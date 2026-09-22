// 画面の組み立て。
//
// データの取得経路は transport.js に閉じ込めてあるので、このファイルは
// スタンドアロン版と Cockpit 版で共通に使える。ここに fetch や cockpit.spawn を
// 直接書いてはいけない。
//
// ビルド手順を持たない方針のため、素の ES モジュールと DOM API だけで書く。

import {
  fetchConfig, fetchBrowse, fetchHistory, fetchPreview, restore,
} from './transport.js';

let catalog = {};
let config = {};
let currentTarget = null;
let pendingRestore = null;

const el = (id) => document.getElementById(id);

// CLI と同じ JSON カタログを使う。プレースホルダも同じ名前付き形式。
const t = (key, params) => {
  const template = catalog[key] || key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    (name in params ? String(params[name]) : match));
};

const setStatus = (message, isError) => {
  const node = el('status');
  node.textContent = message || '';
  node.classList.toggle('error', Boolean(isError));
};

const run = async (action) => {
  try {
    setStatus(t('web.loading'));
    const result = await action();
    setStatus('');
    return result;
  } catch (error) {
    setStatus(error.message, true);
    return null;
  }
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

const formatMoment = (iso) => (iso ? new Date(iso).toLocaleString() : '-');

// ---------------------------------------------------------------------------
// ディレクトリの閲覧
// ---------------------------------------------------------------------------

const renderBreadcrumbs = (parents) => {
  const nav = el('breadcrumbs');
  nav.replaceChildren();
  parents.forEach((path, position) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'crumb';
    // 先頭はルート、それ以外は末尾の名前だけを出す
    button.textContent = position === 0 ? (path === '/' ? '/' : path) : path.split('/').pop();
    button.addEventListener('click', () => openDirectory(path));
    nav.append(button);
  });
};

const renderEntries = (entries) => {
  const list = el('entries');
  list.replaceChildren();
  entries.forEach((entry) => {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = entry.is_directory ? 'entry directory' : 'entry file';
    if (entry.path === currentTarget) button.classList.add('selected');

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = entry.name + (entry.is_directory ? '/' : '');
    if (entry.is_symlink) name.classList.add('symlink');

    const size = document.createElement('span');
    size.className = 'size';
    size.textContent = entry.is_directory ? '' : formatSize(entry.size);

    button.append(name, size);
    button.addEventListener('click', () => {
      if (entry.is_directory) openDirectory(entry.path);
      else openFile(entry.path);
    });
    item.append(button);
    list.append(item);
  });
};

const openDirectory = async (path) => {
  const data = await run(() => fetchBrowse(path));
  if (!data) return;
  el('path-input').value = data.path;
  renderBreadcrumbs(data.parents);
  renderEntries(data.entries);
};

// ---------------------------------------------------------------------------
// 版の一覧
// ---------------------------------------------------------------------------

const stateLabel = (version) => {
  if (version.is_live) {
    return t(version.exists ? 'history.state.live' : 'history.state.live-missing');
  }
  return t(version.exists ? 'history.state.ok' : 'history.state.missing');
};

const renderVersions = (data) => {
  el('timeline-target').textContent = data.target;

  const head = el('versions-head');
  head.replaceChildren();
  [
    'history.column.index', 'history.column.first-seen', 'history.column.last-seen',
    'history.column.size', 'history.column.snapshots', 'history.column.state',
    'web.actions',
  ].forEach((key) => {
    const cell = document.createElement('th');
    cell.textContent = t(key);
    head.append(cell);
  });

  const body = el('versions-body');
  body.replaceChildren();
  data.versions.forEach((version) => {
    const row = document.createElement('tr');
    if (version.is_live) row.classList.add('live');
    [
      version.index,
      formatMoment(version.first_seen),
      formatMoment(version.last_seen),
      formatSize(version.size),
      version.snapshot_count || '-',
      stateLabel(version),
    ].forEach((value) => {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    });

    const actions = document.createElement('td');
    if (version.exists) {
      const preview = document.createElement('button');
      preview.type = 'button';
      preview.textContent = t('web.preview');
      preview.addEventListener('click', () => showPreview(data.target, version.index));
      actions.append(preview);

      // ライブ版を「復元」しても意味が無いので、ボタンを出さない
      if (!version.is_live && !config.read_only) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'danger';
        button.textContent = t('web.restore');
        button.addEventListener('click', () => askRestore(data.target, version.index));
        actions.append(button);
      }
    }
    row.append(actions);
    body.append(row);
  });
};

const openFile = async (path) => {
  const data = await run(() => fetchHistory(path));
  if (!data) return;
  currentTarget = path;
  el('preview-box').hidden = true;
  renderVersions(data);
  // 選択状態を反映するため、一覧を描き直す
  const directory = path.replace(/\/[^/]*$/, '') || '/';
  const listing = await run(() => fetchBrowse(directory));
  if (listing) renderEntries(listing.entries);
};

const showPreview = async (path, index) => {
  const data = await run(() => fetchPreview(path, index));
  if (!data) return;
  el('preview-box').hidden = false;
  el('preview-title').textContent = t('web.preview.title', { index: data.index });
  const notes = [];
  if (data.binary) notes.push(t('web.preview.binary'));
  if (data.truncated) notes.push(t('web.preview.truncated'));
  el('preview-note').textContent = notes.join(' ');
  el('preview').textContent = data.text;
};

// ---------------------------------------------------------------------------
// 復元
// ---------------------------------------------------------------------------

const describePlan = (plan) => {
  const details = el('restore-details');
  details.replaceChildren();
  [
    ['web.restore.source', plan.source],
    ['web.restore.destination', plan.destination],
    ['web.restore.backup', plan.backup || t('restore.backup.none')],
  ].forEach(([key, value]) => {
    const term = document.createElement('dt');
    term.textContent = t(key);
    const description = document.createElement('dd');
    description.textContent = value;
    description.className = 'path';
    details.append(term, description);
  });
};

// 実行前に必ず dry-run で計画を取り、どこに何を書くのかを見せてから確認を取る。
// 「押したら終わっていた」を避けるための一手間。
const askRestore = async (path, index) => {
  const inPlace = el('in-place').checked;
  const plan = await run(() => restore({ path, index, in_place: inPlace, dry_run: true }));
  if (!plan) return;
  pendingRestore = { path, index };
  el('restore-title').textContent = t('web.restore.title', { index });
  describePlan(plan);
  const warning = el('restore-warning');
  warning.hidden = !inPlace;
  warning.textContent = t('web.restore.in-place-warning');
  el('restore-dialog').showModal();
};

const confirmRestore = async () => {
  if (!pendingRestore) return;
  const { path, index } = pendingRestore;
  pendingRestore = null;
  const result = await run(() => restore({
    path, index, in_place: el('in-place').checked, dry_run: false,
  }));
  if (!result) return;
  setStatus(t('web.restore.done', {
    destination: result.destination, method: result.method,
  }));
  openFile(path);
};

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------

const applyLabels = () => {
  document.documentElement.lang = config.language;
  el('path-input').placeholder = t('web.path-placeholder');
  el('path-go').textContent = t('web.go');
  el('browser-title').textContent = t('web.browser.title');
  el('timeline-title').textContent = t('web.timeline.title');
  el('in-place-text').textContent = t('web.restore.in-place');
  el('restore-cancel').textContent = t('web.restore.cancel');
  el('restore-confirm').textContent = t('web.restore.confirm');
  if (config.read_only) {
    el('in-place-label').hidden = true;
    setStatus(t('web.read-only'));
  }
};

const start = async () => {
  config = await fetchConfig();
  catalog = config.catalog;
  applyLabels();

  el('path-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const value = el('path-input').value.trim();
    if (!value) return;
    // ディレクトリなら移動、ファイルなら履歴を出す。
    // 利用者にどちらかを選ばせる必要は無いので、開いてみて判断する。
    fetchBrowse(value)
      .then((data) => {
        renderBreadcrumbs(data.parents);
        renderEntries(data.entries);
        setStatus('');
      })
      .catch(() => openFile(value));
  });

  el('restore-dialog').addEventListener('close', (event) => {
    if (event.target.returnValue === 'confirm') confirmRestore();
    else pendingRestore = null;
  });

  openDirectory(config.start_path);
};

start().catch((error) => setStatus(error.message, true));
