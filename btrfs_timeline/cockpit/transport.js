// データの取得経路 — Cockpit モジュール版。
//
// **画面のコードはスタンドアロン版と 1 バイトも違わない。** 差し替えるのはこの
// ファイルだけ、という前提でここまで作ってきた。index.html も app.js も i18n.js も
// style.css も、そのまま同じものを使う。
//
// Cockpit モジュールにはサーバーが存在しない。実体は静的ファイルと manifest.json
// だけで、システムに触る手段はブラウザ上の JavaScript からプロセスを起動すること
// しかない。したがって fetch の代わりに cockpit.spawn で CLI を呼び、
// `--json` の出力を読む。CLI の JSON を公開契約として扱ってきた理由がこれである。
//
// **特権昇格は要求しない。** スナップショット内のファイルは通常のパーミッションで
// 読めるため履歴の閲覧に root は要らず、復元はログイン中の利用者の権限で書く。
// superuser を要求すると、必要のない権限を毎回求めることになる。

import { COMMAND } from './command.js';

// cockpit.js は素のスクリプトで window.cockpit を定義する (ES モジュールではない)。
// index.html に script タグを足せば済むが、それをすると HTML が
// スタンドアロン版と別物になってしまうので、ここで読み込む。
let loading = null;
const loadCockpit = () => {
  if (window.cockpit) return Promise.resolve(window.cockpit);
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      tag.src = '../base1/cockpit.js';
      tag.onload = () => resolve(window.cockpit);
      tag.onerror = () => reject(new Error('cockpit.js を読み込めませんでした'));
      document.head.append(tag);
    });
  }
  return loading;
};

const run = async (args) => {
  const cockpit = await loadCockpit();
  let output;
  try {
    // err: 'message' で、失敗時の例外に標準エラー出力の内容が入る。
    // CLI は翻訳済みのメッセージをそこに書くので、そのまま画面に出せる。
    output = await cockpit.spawn([...COMMAND, ...args, '--json'], { err: 'message' });
  } catch (error) {
    throw new Error((error && error.message) || String(error));
  }
  return JSON.parse(output);
};

const flag = (condition, ...args) => (condition ? args : []);

export const fetchConfig = (lang) => run(['config', ...flag(lang, '--lang', lang)]);

export const fetchBrowse = (path, snapshot, showHidden) => run([
  'browse', path,
  ...flag(snapshot, '--snapshot', String(snapshot)),
  ...flag(!showHidden, '--no-hidden'),
]);

export const fetchHistory = (path) => run(['history', path]);

export const fetchPreview = (path, index) =>
  run(['preview', path, '--index', String(index)]);

export const fetchDiff = (path, from, to) => run([
  'diff', path, '--from', String(from),
  ...flag(to !== '' && to !== null && to !== undefined, '--to', String(to)),
]);

export const restore = (payload) => run([
  'restore', payload.path,
  ...flag(payload.index !== null && payload.index !== undefined,
          '--index', String(payload.index)),
  ...flag(payload.in_place, '--in-place'),
  ...flag(payload.dry_run, '--dry-run'),
]);
