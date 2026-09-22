// 画面の組み立て。
//
// データの取得経路は transport.js に閉じ込めてあるので、このファイルは
// スタンドアロン版と Cockpit 版で共通に使える。ここに fetch や cockpit.spawn を
// 直接書いてはいけない。
//
// 画面の状態は 3 つしかない。
//   directory … 今どこを見ているか
//   moment    … いつの状態を見ているか (null なら現在)
//   target    … 右側に履歴を出している対象 (ファイルでもディレクトリでもよい)
// 「削除されたファイルを探す」も「ディレクトリの中身を遡る」も、この組み合わせで
// 表現できる。機能ごとにタブを増やすと、同じ操作を何通りも覚えることになる。
//
// ビルド手順を持たない方針のため、素の ES モジュールと DOM API だけで書く。

import {
  fetchConfig, fetchBrowse, fetchHistory, fetchPreview, fetchDiff, restore,
} from './transport.js';

let catalog = {};
let config = {};

let directory = null;
let moment = null;          // 表示中のスナップショット (null なら現在)
let target = null;          // 履歴を出している対象のライブパス
let targetIsDirectory = false;
let versions = [];
let selectedVersion = null;
let previewMode = 'content';
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
// 左ペイン: ある時点のファイル一覧
// ---------------------------------------------------------------------------

const renderTimeState = () => {
  const label = el('time-label');
  const reset = el('time-reset');
  if (moment) {
    label.textContent = t('web.time.at', { moment: formatMoment(moment.taken_at) });
    reset.textContent = t('web.time.reset');
    reset.hidden = false;
    document.body.classList.add('past');
  } else {
    label.textContent = t('web.live');
    reset.hidden = true;
    document.body.classList.remove('past');
  }
};

const renderBreadcrumbs = (parents) => {
  const nav = el('breadcrumbs');
  nav.replaceChildren();
  parents.forEach((path, position) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'crumb';
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
    if (entry.path === target) button.classList.add('selected');
    // 現在は存在しない項目。ここに現れることが、削除されたものへの唯一の入口になる
    if (entry.exists_now === false) button.classList.add('gone');

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = entry.name + (entry.is_directory ? '/' : '');
    if (entry.is_symlink) name.classList.add('symlink');

    const note = document.createElement('span');
    note.className = 'size';
    note.textContent = entry.exists_now === false
      ? t('web.deleted')
      : (entry.is_directory ? '' : formatSize(entry.size));
    if (entry.exists_now === false) note.classList.add('deleted');

    button.append(name, note);
    button.addEventListener('click', () => {
      if (entry.is_directory) openDirectory(entry.path);
      else openFile(entry.path);
    });
    item.append(button);
    list.append(item);
  });
};

const openDirectory = async (path) => {
  const data = await run(() => fetchBrowse(path, moment ? moment.id : null));
  if (!data) return;
  directory = data.path;
  moment = data.snapshot || null;
  el('path-input').value = data.path;
  renderTimeState();
  renderBreadcrumbs(data.parents);
  // ディレクトリに入ったら、そのディレクトリの履歴を右に出す。
  // btrfs はディレクトリの mtime も保存しており、項目の出入りで変化するため、
  // ファイルと同じ仕組みのまま「いつ何が増減したか」が版として出てくる。
  await loadHistory(data.path, true);
  renderEntries(data.entries);
};

const goToMoment = async (snapshotId) => {
  const data = await run(() => fetchBrowse(directory, snapshotId));
  if (!data) return;
  moment = data.snapshot || null;
  renderTimeState();
  renderEntries(data.entries);
};

const backToNow = async () => {
  moment = null;
  const data = await run(() => fetchBrowse(directory, null));
  if (!data) return;
  renderTimeState();
  renderEntries(data.entries);
};

// ---------------------------------------------------------------------------
// 右ペイン: 版の一覧
// ---------------------------------------------------------------------------

const stateLabel = (version) => {
  if (version.is_live) {
    return t(version.exists ? 'history.state.live' : 'history.state.live-missing');
  }
  return t(version.exists ? 'history.state.ok' : 'history.state.missing');
};

const renderVersions = () => {
  el('timeline-target').textContent = target;
  el('timeline-hint').hidden = !targetIsDirectory;
  el('timeline-hint').textContent = t('web.directory.hint');

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
  versions.forEach((version) => {
    const row = document.createElement('tr');
    if (version.is_live) row.classList.add('live');
    if (moment && version.first_snapshot_id === moment.id) row.classList.add('current');
    [
      version.index,
      formatMoment(version.first_seen),
      formatMoment(version.last_seen),
      targetIsDirectory ? '' : formatSize(version.size),
      version.snapshot_count || '-',
      stateLabel(version),
    ].forEach((value) => {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    });

    const actions = document.createElement('td');
    if (version.exists) {
      if (targetIsDirectory) {
        // ディレクトリの版に対してできるのは「その時点を開く」こと。
        // 版ごとに別の意味のボタンを並べるより、1 つに絞った方が迷わない。
        if (!version.is_live) {
          const open = document.createElement('button');
          open.type = 'button';
          open.textContent = t('web.open-at');
          open.addEventListener('click', () => goToMoment(version.first_snapshot_id));
          actions.append(open);
        }
      } else {
        const preview = document.createElement('button');
        preview.type = 'button';
        preview.textContent = t('web.preview');
        preview.addEventListener('click', () => showVersion(version.index));
        actions.append(preview);

        // ライブ版を「復元」しても意味が無いので、ボタンを出さない
        if (!version.is_live && !config.read_only) {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'danger';
          button.textContent = t('web.restore');
          button.addEventListener('click', () => askRestore(target, version.index));
          actions.append(button);
        }
      }
    }
    row.append(actions);
    body.append(row);
  });
};

const loadHistory = async (path, isDirectory) => {
  const data = await run(() => fetchHistory(path));
  if (!data) return;
  target = data.target;
  targetIsDirectory = Boolean(isDirectory);
  versions = data.versions;
  selectedVersion = null;
  el('preview-box').hidden = true;
  renderVersions();
};

const openFile = async (path) => {
  await loadHistory(path, false);
  // 選択状態を反映するため一覧を描き直す (時点は変えない)
  const listing = await run(() => fetchBrowse(directory, moment ? moment.id : null));
  if (listing) renderEntries(listing.entries);
};

// ---------------------------------------------------------------------------
// プレビューと差分 (同じ場所の表示モード切り替え)
// ---------------------------------------------------------------------------

const renderCompareOptions = () => {
  const select = el('diff-against');
  select.replaceChildren();
  versions.forEach((version) => {
    if (version.index === selectedVersion) return;
    const option = document.createElement('option');
    option.value = String(version.index);
    option.textContent = version.is_live
      ? t('web.live')
      : `#${version.index} ${formatMoment(version.first_seen)}`;
    select.append(option);
  });
  const live = versions.find((v) => v.is_live);
  if (live && live.index !== selectedVersion) select.value = String(live.index);
};

const renderModeButtons = () => {
  el('mode-content').textContent = t('web.mode.content');
  el('mode-diff').textContent = t('web.mode.diff');
  el('mode-content').classList.toggle('active', previewMode === 'content');
  el('mode-diff').classList.toggle('active', previewMode === 'diff');
  el('diff-against-label').hidden = previewMode !== 'diff';
};

const showVersion = async (index) => {
  selectedVersion = index;
  el('preview-box').hidden = false;
  el('preview-title').textContent = t('web.preview.title', { index });
  renderCompareOptions();
  renderModeButtons();
  await refreshPreview();
};

const refreshPreview = async () => {
  if (selectedVersion === null) return;
  if (previewMode === 'diff') {
    const against = el('diff-against').value;
    const data = await run(() => fetchDiff(target, selectedVersion, against));
    if (!data) return;
    const notes = [];
    if (data.identical) notes.push(t('web.diff.identical'));
    if (data.binary) notes.push(t('web.diff.binary'));
    if (data.truncated) notes.push(t('web.diff.truncated'));
    el('preview-note').textContent = notes.join(' ');
    el('preview').textContent = data.text;
    el('preview').classList.add('diff');
    return;
  }

  const data = await run(() => fetchPreview(target, selectedVersion));
  if (!data) return;
  const notes = [];
  if (data.binary) notes.push(t('web.preview.binary'));
  if (data.truncated) notes.push(t('web.preview.truncated'));
  el('preview-note').textContent = notes.join(' ');
  el('preview').textContent = data.text;
  el('preview').classList.remove('diff');
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
  el('diff-against-text').textContent = t('web.diff.against');
  el('in-place-text').textContent = t('web.restore.in-place');
  el('restore-cancel').textContent = t('web.restore.cancel');
  el('restore-confirm').textContent = t('web.restore.confirm');
  renderModeButtons();
  if (config.read_only) {
    el('in-place-label').hidden = true;
    setStatus(t('web.read-only'));
  }
};

const setMode = (mode) => {
  previewMode = mode;
  renderModeButtons();
  refreshPreview();
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
    fetchBrowse(value, moment ? moment.id : null)
      .then(() => openDirectory(value))
      .catch(() => {
        directory = value.replace(/\/[^/]*$/, '') || '/';
        openFile(value);
      });
  });

  el('time-reset').addEventListener('click', backToNow);
  el('mode-content').addEventListener('click', () => setMode('content'));
  el('mode-diff').addEventListener('click', () => setMode('diff'));
  el('diff-against').addEventListener('change', refreshPreview);

  el('restore-dialog').addEventListener('close', (event) => {
    if (event.target.returnValue === 'confirm') confirmRestore();
    else pendingRestore = null;
  });

  openDirectory(config.start_path);
};

start().catch((error) => setStatus(error.message, true));
