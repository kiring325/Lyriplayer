# Lyriplayer（歌詞プレイヤー） — 作業メモ

- 本体は `lyrics_player.py` 1 ファイル（PySide6 + mutagen）。UI の文言・コメントは日本語で、利用者にわかりやすい言葉で書く。
- 対象は Windows のデスクトップアプリ。Windows 専用の処理（RegisterHotKey のホットキー、名前付きミューテックスの二重起動防止）は `IS_WIN` で分けてある。
- データ：歌詞・リストは AppData の `lyrics.db`（SQLite）、タイミングは `ranges.json`、設定は QSettings（Windows ではレジストリ）。
- タイミング計算は `ScrollModel`：歌い出し/歌い終わり・フレーズの印（`marks`）・区切り（`---` 行）から、再生時刻 ⇔ スクロール位置を求める。
- exe は Windows 上で `build_exe.ps1`（PyInstaller、onefile）。クラウドやスマホからの作業ではビルド・GUI 確認はできないので、
  変更後は `python -m py_compile lyrics_player.py` と、`QT_QPA_PLATFORM=offscreen` で画面を出さないテストで確かめる。
- アプリ内アップデート：`GitUpdater`（git fetch → ブランチを切り替え）→ `build_exe.ps1 -DistPath … -Install` → `launch_swap` で exe を入れ替え。
  版の情報は build_exe.ps1 が `build/version.json` に書いて exe に埋め込む（`app_version()`）。build_exe.ps1 は BOM 付き UTF-8 で保存する。
- アイコンは画像ファイルを使わず `icon_pixmap()` で描く（24×24 の升目で設計）。他人の素材画像はリポジトリに入れない。
- `dist/` `build/` はコミットしない。
