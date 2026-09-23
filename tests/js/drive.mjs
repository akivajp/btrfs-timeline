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

const makeNode = (name) => {
  const classes = new Set();
  return {
    id: name, textContent: '', value: '', placeholder: '', lang: '',
    hidden: false, checked: false, className: '', type: '', returnValue: '',
    children: [], listeners: {},
    classList: {
      add: (...names) => names.forEach((item) => classes.add(item)),
      remove: (...names) => names.forEach((item) => classes.delete(item)),
      toggle: (item, force) => {
        const on = force === undefined ? !classes.has(item) : Boolean(force);
        if (on) classes.add(item); else classes.delete(item);
      },
      contains: (item) => classes.has(item),
    },
    replaceChildren(...items) { this.children = items; },
    append(...items) { this.children.push(...items); },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    showModal() { this.open = true; },
    close() { this.open = false; },
  };
};

const nodes = new Map();
const byId = (id) => {
  if (!nodes.has(id)) nodes.set(id, makeNode(id));
  return nodes.get(id);
};

globalThis.document = {
  documentElement: makeNode('html'),
  body: makeNode('body'),
  getElementById: byId,
  createElement: (tag) => makeNode(tag),
};
// localStorage が使えない環境の再現も兼ねている (読み書きが例外になっても動くこと)
globalThis.localStorage = {
  getItem() { throw new Error('storage is unavailable'); },
  setItem() { throw new Error('storage is unavailable'); },
};

const steps = [];
const failures = [];

const settle = async () => {
  for (let i = 0; i < 8; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
};

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

console.log(JSON.stringify({
  steps,
  failures,
  entries: byId('entries').children.length,
  versions: byId('versions-body').children.length,
  preview: byId('preview').textContent,
  language: byId('language').value,
  hiddenLabel: byId('hidden-text').textContent,
  actionsHeader: byId('versions-head').children[6].textContent,
}));
