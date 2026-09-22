// データの取得経路。
//
// **Cockpit モジュールで差し替えるのはこのファイルだけ。** あちらにはサーバーが
// 無いので、fetch ではなく cockpit.spawn(["btrfs-timeline", ..., "--json"],
// {superuser: "require"}) でプロセスを起動して同じ JSON を得る。
// app.js からはどちらも同じ関数名で見えるため、画面のコードは 1 つで済む。
//
// ここで返す JSON の形は CLI の --json 出力と同じ。形が食い違うと、
// 差し替えた瞬間に画面が動かなくなる。

async function request(url, options) {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch (error) {
    data = { error: response.statusText };
  }
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

const query = (path, extra) => {
  const params = new URLSearchParams({ path, ...(extra || {}) });
  return params.toString();
};

export const fetchConfig = () => request('./api/config');

export const fetchBrowse = (path) => request(`./api/browse?${query(path)}`);

export const fetchHistory = (path) => request(`./api/history?${query(path)}`);

export const fetchPreview = (path, index) =>
  request(`./api/preview?${query(path, { index })}`);

// 復元は書き込みなので POST で送る。GET にすると、リンクを踏ませるだけで
// 実行させられてしまう。サーバー側は Origin ヘッダも検証している。
export const restore = (payload) =>
  request('./api/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
