// 画面を実際に動かして、操作のたびにエラーが出ていないかを見る。
//
// app.js のエラーは全て run() が拾って status 行に出す設計なので、
// 「操作したあと status がエラーになっていないか」を見れば、
// 未定義の参照・変数名の衝突・API の呼び違いはひととおり捕まえられる。
// 実際、ローカル変数がヘルパーと同じ名前になっていた事故
// (Cannot access 'listing' before initialization) は、
// 構文チェックでは通ってしまい、画面を開くまで分からなかった。
//
// DOM は本物を用意せず、app.js が使う分だけを埋める。ブラウザを立ち上げずに
// ロジックだけ確かめたいので、これで足りる。

// 経路ごとの下ごしらえ (スタンドアロン版では何もしない)。
// import は本文より先に評価されるので、app.js が読み込まれる前に整う。
import './prelude.mjs';

import { byId, settle } from './dom.mjs';

const steps = [];
const failures = [];

const check = async (label) => {
  await settle();
  const status = byId('status');
  const failed = status.classList.contains('error');
  steps.push({ step: label, status: status.textContent, error: failed });
  if (failed) failures.push(`${label}: ${status.textContent}`);
};

const entryButton = (name) => {
  for (const item of byId('entries').children) {
    const button = item.children[0];
    if (button && button.children[0] && button.children[0].textContent === name) {
      return button;
    }
  }
  throw new Error(`一覧に ${name} が見つかりません`);
};

try {
  await import('./app.mjs');
  await check('起動');

  entryButton('notes.txt').listeners.click();
  await check('ファイルを選ぶ');

  // 行の最後のセルが操作列。その先頭がプレビュー
  const actions = byId('versions-body').children[0].children[6];
  actions.children[0].listeners.click();
  await check('プレビュー');

  byId('mode-diff').listeners.click();
  await check('差分に切り替え');

  // ブラウザでは値が変わってから change が飛ぶ。同じ順序にしておく
  byId('language').value = 'ja';
  byId('language').listeners.change({ target: { value: 'ja' } });
  await check('言語を切り替え');

  byId('show-hidden').listeners.change({ target: { checked: true } });
  await check('隠しファイルを表示');

  entryButton('sub/').listeners.click();
  await check('ディレクトリへ移動');

  // ディレクトリの版は「この時点を開く」だけを持つ
  byId('versions-body').children[0].children[6].children[0].listeners.click();
  await check('過去の時点を開く');

  byId('time-reset').listeners.click();
  await check('現在に戻る');
} catch (error) {
  failures.push(`想定外の例外: ${error.message}`);
}

// 途中で落ちた場合でも、何が起きたかを必ず出す。ここで例外にすると
// failures の中身が見えなくなり、原因が分からなくなる。
const cell = (id, position) => {
  const found = byId(id).children[position];
  return found === undefined ? null : found.textContent;
};

console.log(JSON.stringify({
  steps,
  failures,
  entries: byId('entries').children.length,
  versions: byId('versions-body').children.length,
  preview: byId('preview').textContent,
  language: byId('language').value,
  hiddenLabel: byId('hidden-text').textContent,
  actionsHeader: cell('versions-head', 6),
  historyLink: byId('link-history').textContent,
  devicesLink: byId('link-devices').textContent,
  pagesHidden: byId('pages').hidden,
}));
