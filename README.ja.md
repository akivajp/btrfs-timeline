# btrfs-timeline

btrfs のスナップショットから、ファイルの過去の版をブラウザで辿って復元する —
Mac の Time Machine のような感覚で。

[English README is here](README.md)

> **状態: 開発初期。** コアと CLI は動作し、テストも通っています。
> Web UI と復元コマンドはこれから実装します。[ロードマップ](#ロードマップ)を参照してください。

## なぜ作るのか

snapper や btrbk、Timeshift で自動スナップショットを取っていれば、
**すべてのファイルのすべての過去版は既に手元にあります**。
しかし実際に1つ取り戻そうとすると、スナップショットの置き場所を知っていて、
どれが十分古いかを当てて、コマンドラインでファイルを見比べる必要があります。
できることはできますが、玄人向けの作業です。そしてこれこそが、
そもそもスナップショットを取っている理由そのものです。

既存のツールは、それぞれどこかで手前に止まっています。

| ツール | できること | 合わない点 |
| --- | --- | --- |
| [httm](https://github.com/kimono-koans/httm) | ZFS/btrfs の優れた対話的ファイル履歴 | CLI / TUI のみ |
| Btrfs Assistant, Snapper GUI | スナップショット管理 | デスクトップアプリ。ファイル単位の履歴ではない |
| Samba `vfs_shadow_copy2` | Windows エクスプローラの「以前のバージョン」 | Windows クライアントが前提 |
| Rockstor, OpenMediaVault | btrfs 対応の本格的な NAS Web UI | NAS 専用ディストリビューション。共有単位のロールバックでファイル単位ではない |
| Cockpit `storaged` | btrfs のファイルシステム/サブボリューム作成 | スナップショット閲覧は無し。マルチデバイス btrfs も非対応 |

**ブラウザを開いてパスを指定し、時間を遡る**、というものが存在しません。それを作ります。

## 設計

このプロジェクトは3つの形態での提供を想定しており、それらは1つのものを共有します。

```
btrfs_timeline/core/   ライブラリ: スナップショット検出・版の履歴・復元
btrfs_timeline/cli.py  全サブコマンドに --json を持つ CLI  <- 共有される契約
   |- スタンドアロン Web   core を直接 import (同一プロセス)
   |- Cockpit モジュール   cockpit.spawn([... , "--json"], {superuser: "require"})
   `- デスクトップ GUI     core を import、または CLI 呼び出し
```

Cockpit モジュールには **サーバーサイドが存在せず**、実体は静的ファイルと
`manifest.json` だけで、ブラウザからプロセスを起動してシステムに触れます。
そのため、共有できるコアの形は「JSON を吐く CLI」しかありません。
全サブコマンドが `--json` を持つのはこのためで、その出力は公開 API として扱います。

好みではなく実測から決まった判断が2つあります。

- **`btrfs subvolume list` を使わない。** これは root を要求します
  (非特権では `Operation not permitted`)。一方でスナップショット内のファイル自体は
  通常のパーミッションで読めます。検出を `/proc/self/mountinfo` とディレクトリ走査で
  行うことで、**履歴の閲覧に root が不要**になります。
- **重複排除は必須。** btrfs は CoW なので、変更していないファイルも
  スナップショットの数だけ現れます。作者の環境では 35 個のスナップショットが
  1 つの版に畳まれました。版は mtime とサイズで統合し、
  **ファイルが存在しなかった期間も独立した項目として残します** —
  そうしないと、削除されてから再作成されたファイルの履歴が繋がって見えてしまいます。

## インストール

```shell
pipx install btrfs-timeline          # CLI のみ。依存パッケージ無し
pipx install 'btrfs-timeline[web]'   # スタンドアロン Web UI 付き
```

Python 3.9 以降と Linux が必要です。履歴の閲覧に `btrfs-progs` は**不要**です。

## 使い方

```shell
# このファイルにはどんな版があるか
btrfs-timeline history ~/notes.md

# 機械可読出力 (スクリプトや Cockpit モジュール向け)
btrfs-timeline history ~/notes.md --json

# このパスを含むスナップショットと、そのレイアウト
btrfs-timeline snapshots ~/

# このシステムの btrfs マウント一覧
btrfs-timeline mounts
```

出力例:

```
/home/akiva/.gitconfig
FIRST SEEN           LAST SEEN                  SIZE  SNAPS  STATE
2024-10-02 02:00:08  2025-01-01 00:00:00           -      3  (存在しない)
2025-12-01 00:00:08  2026-03-01 00:00:00       268 B      4  ok
2026-04-01 00:00:00  2026-09-23 01:00:00       297 B     28  ok
-                    -                         297 B      -  live
```

35 個のスナップショットが、意味のある3つの版と、
ファイルが作られる前の空白期間になっています。これが要点です。

## 対応するスナップショットのレイアウト

| レイアウト | パスの形 | 主な出所 |
| --- | --- | --- |
| snapper | `<マウント>/.snapshots/<番号>/snapshot` + `info.xml` | snapper |
| flat | `<マウント>/.snapshots/<名前>` | btrbk, Timeshift, 手動運用 |

取得日時は、snapper の `info.xml` (タイムゾーン表記が無いが **UTC**)、
ディレクトリ名に埋め込まれた日時、ディレクトリの mtime の順で判定します。

## ロードマップ

1. **ファイル履歴ブラウザ / スタンドアロン Web UI** — 現在の主眼
2. **Cockpit モジュール** — 同じ CLI を叩き、フロントエンドのコードを共有
3. **ダッシュボードとデバイス管理** — デバイス一覧、RAID 構成、scrub/balance の進捗
4. **デスクトップ GUI**

デバイス管理 (3) は失敗するとファイルシステムを壊しうるもので、
履歴を読むのとは risk の種類が違います。モジュールも権限も分離したままにします。

## 注意点と制限

- シンボリックリンクは**現在の**ファイルシステム上で解決するため、履歴はリンク先の
  実体を辿ります (dotfiles 運用で有用)。一方、スナップショット内のリンクは決して
  辿らないので、スナップショットがライブの内容を過去の版として報告することはありません。
- 版の比較は mtime とサイズで行い、内容のハッシュは取りません
  (全ファイルの全版を読み込むことになるため)。
- btrfs の RAID 5/6 は上流で依然として本番向けとは見なされていません。
  このツールはその事情を変えるものではありません。

## 代替となるツール

ブラウザではなくターミナルで済ませたい場合は
[httm](https://github.com/kimono-koans/httm) が成熟していて高速で、ZFS にも対応しています。
本格的な NAS アプライアンスが欲しい場合は [Rockstor](https://rockstor.com/) や
[OpenMediaVault](https://www.openmediavault.org/) を使ってください。

## ライセンス

MIT — [LICENSE](LICENSE) を参照してください。
