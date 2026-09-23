// スタンドアロン Web UI 版の transport のスタブ。
//
// 本物は fetch で ``/api/...`` を叩く。ここではデータを直接返すだけで、
// app.js から見える関数の形は同じにしてある。

// スタンドアロン版を名乗る。Cockpit 版のテストでは本物の transport を使うので、
// こちらは常に standalone。
export const FLAVOUR = 'standalone';
export const canElevate = false;

export {
  fakeConfig as fetchConfig,
  fakeBrowse as fetchBrowse,
  fakeHistory as fetchHistory,
  fakePreview as fetchPreview,
  fakeDiff as fetchDiff,
  fakeRestore as restore,
  fakeDevices as fetchDevices,
  fakeScrub as fetchScrub,
} from './fake-data.mjs';
