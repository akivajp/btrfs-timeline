// Cockpit 版を動かすための下ごしらえ。
//
// Cockpit モジュールは cockpit.spawn で CLI を起動し、`--json` の出力を読む。
// ここでは spawn を **argv を実際に解釈するスタブ** に置き換える。
// transport が組み立てる引数が違っていれば、ここで見つからずに失敗するので、
// 「どんなコマンドを呼ぶか」もこれで検証されていることになる。

import {
  fakeConfig, fakeBrowse, fakeHistory, fakePreview, fakeDiff, fakeRestore,
} from './fake-data.mjs';

const SUBCOMMANDS = ['config', 'browse', 'history', 'preview', 'diff', 'restore'];

const spawn = async (argv) => {
  const start = argv.findIndex((item) => SUBCOMMANDS.includes(item));
  if (start === -1) {
    throw new Error(`知らないサブコマンドです: ${argv.join(' ')}`);
  }
  const args = argv.slice(start);
  if (!args.includes('--json')) {
    throw new Error(`--json が付いていません: ${args.join(' ')}`);
  }

  const value = (name) => {
    const at = args.indexOf(name);
    return at === -1 ? null : args[at + 1];
  };
  const has = (name) => args.includes(name);
  const [subcommand, path] = args;

  switch (subcommand) {
    case 'config':
      return JSON.stringify(await fakeConfig(value('--lang')));
    case 'browse':
      return JSON.stringify(await fakeBrowse(path, value('--snapshot'), !has('--no-hidden')));
    case 'history':
      return JSON.stringify(await fakeHistory(path));
    case 'preview':
      return JSON.stringify(await fakePreview(path, value('--index')));
    case 'diff':
      return JSON.stringify(await fakeDiff(path, value('--from'), value('--to')));
    case 'restore':
      return JSON.stringify(await fakeRestore({
        path,
        index: value('--index'),
        in_place: has('--in-place'),
        dry_run: has('--dry-run'),
      }));
    default:
      throw new Error(`扱えないサブコマンドです: ${subcommand}`);
  }
};

globalThis.window = globalThis;
globalThis.window.cockpit = { spawn };
