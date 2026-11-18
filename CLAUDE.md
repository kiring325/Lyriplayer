# Lyriplayer（歌詞プレイヤー） — 作業メモ

- 本体は `lyrics_player.py` 1 ファイル（PySide6 + mutagen）。UI の文言・コメントは日本語で、利用者にわかりやすい言葉で書く。
- 対象は Windows のデスクトップアプリ。Windows 専用の処理（RegisterHotKey のホットキー、名前付きミューテックスの二重起動防止）は `IS_WIN` で分けてある。
- データ：歌詞・リストは AppData の `lyrics.db`（SQLite）、タイミングは `ranges.json`、設定は QSettings（Windows ではレジストリ）。
- タイミング計算は `ScrollModel`：歌い出し/歌い終わり・フレーズの印（`marks`）・区切り（`---` 行）から、再生時刻 ⇔ スクロール位置を求める。
- exe は Windows 上で `build_exe.ps1`（PyInstaller、onefile）。クラウドやスマホからの作業ではビルド・GUI 確認はできないので、
  変更後は `python -m py_compile lyrics_player.py` と、`QT_QPA_PLATFORM=offscreen` で画面を出さないテストで確かめる。
- 公開：`main` に入ると `.github/workflows/release.yml` が exe を作って Releases に公開（タグ `v年.月.日.通し番号`）。
  build_exe.ps1 は環境変数 `LYRIPLAYER_VERSION` を `build/version.json` に書いて exe に埋め込む（`app_version()`）。build_exe.ps1 は BOM 付き UTF-8 で保存する。
- アプリ内アップデート：`fetch_latest_release()`（GitHub API の releases/latest）→ `download_release()`（大きさと SHA-256 を確認）→ `launch_swap()` で exe を入れ替え。
- 作業はブランチで行い、プルリクエストで `main` に入れる（`main` に入ると利用者に配られる）。
- アイコンは画像ファイルを使わず `icon_pixmap()` で描く（24×24 の升目で設計）。他人の素材画像はリポジトリに入れない。
- README.md は使う人向け（ダウンロード・使い方）。ビルドや公開の手順など開発者向けのことは BUILDING.md に書く。
- `dist/` `build/` はコミットしない。
