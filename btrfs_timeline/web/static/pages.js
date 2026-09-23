// ページ間の移動。
//
// スタンドアロンにはシェルが無いので、画面の中にリンクが要る。
// Cockpit には左のツールメニューがあり、同じ 2 画面が既にそこに並んでいる。
// 中にもう一組置くと、同じ移動手段が 2 つになるだけで、**どちらを使えばよいのかを
// 利用者に考えさせる** ことになる。経路が自分を名乗れるようにしてあるので、
// 画面はそれを見て出し分ける。
//
// app.js からも devices.js からも使うため、独立したモジュールにしてある
// (app.js は import しただけで起動するので、そこから借りることはできない)。

import { FLAVOUR } from './transport.js';

export const applyPageLinks = (translate) => {
  const nav = document.getElementById('pages');
  if (!nav) return;
  if (FLAVOUR === 'cockpit') {
    nav.hidden = true;
    return;
  }
  nav.hidden = false;
  document.getElementById('link-history').textContent = translate('web.page.history');
  document.getElementById('link-devices').textContent = translate('web.page.devices');
};
