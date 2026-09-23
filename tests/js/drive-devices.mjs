// ダッシュボードを実際に描かせて、エラーが出ていないかを見る。
//
// この画面には操作が無いので、確かめるのは「最後まで描けたか」と
// 「危険なものが目立つ形で出ているか」の 2 つ。
// 欠損デバイスやエラーカウンタが静かに埋もれたら、この画面の意味が無い。

import './prelude.mjs';
import { byId, nodes, settle } from './dom.mjs';

const failures = [];

const text = (node) => {
  if (!node) return '';
  const own = node.textContent || '';
  return own + (node.children || []).map(text).join(' ');
};

try {
  await import('./devices.mjs');
  await settle(12);
} catch (error) {
  failures.push(`想定外の例外: ${error.message}`);
}

const status = byId('status');
if (status.classList.contains('error')) {
  failures.push(`status: ${status.textContent}`);
}

const dashboard = byId('dashboard');
const rendered = text(dashboard);

// 危険度の札が付いた要素を数える
let riskBadges = 0;
let alarmRows = 0;
const walk = (node) => {
  if (!node || !node.children) return;
  if ((node.className || '').startsWith('risk risk-')) riskBadges += 1;
  if (node.className === 'alarm') alarmRows += 1;
  node.children.forEach(walk);
};
walk(dashboard);

console.log(JSON.stringify({
  failures,
  sections: dashboard.children.length,
  rendered,
  riskBadges,
  alarmRows,
  languageOptions: (byId('language').children || []).length,
}));
