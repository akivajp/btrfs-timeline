// スタンドアロン Web UI 版の transport のスタブ。
//
// 本物は fetch で ``/api/...`` を叩く。ここではデータを直接返すだけで、
// app.js から見える関数の形は同じにしてある。

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
