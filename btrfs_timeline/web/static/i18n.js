// 訳文の引き方。
//
// **Python 側の i18n.translate() と同じ規則でなければならない。** CLI と Web UI が
// 同じカタログを共有している以上、引き方だけ違うと片方でだけ表示が壊れる。
// 実際、有無の判定に `||` を使っていたために、訳文が意図的に空文字のキー
// (操作列の見出しなど、ラベルを出さないのが正しい箇所) が「未翻訳」と見なされ、
// 画面にキー名がそのまま出ていた。
//
// app.js から切り出してあるのは、ここだけは副作用なしに import できるようにして
// テストを当てるため。画面そのものは DOM が無いと動かせないが、この規則は単体で守れる。

/**
 * カタログからキーに対応する訳文を取り出し、名前付きプレースホルダを埋める。
 *
 * 値が空文字のキーは「空文字を表示する」という指示であって、未翻訳ではない。
 * キーが存在しない場合だけ、キー自身を返す (開発中に気付けるように)。
 */
export const translate = (catalog, key, params) => {
  const template = Object.prototype.hasOwnProperty.call(catalog, key)
    ? catalog[key]
    : key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    (name in params ? String(params[name]) : match));
};
