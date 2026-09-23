// 最小限の DOM スタブ。
//
// 画面のコードは DOM が無いと動かないが、ブラウザを立ち上げずにロジックだけ
// 確かめたい。app.js と devices.js が使う分だけを埋めてある。
// 両方の画面で共有するので、片方だけ通る状態にならない。

export const makeNode = (name) => {
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

export const nodes = new Map();
export const byId = (id) => {
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


export const settle = async (rounds = 8) => {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
};
