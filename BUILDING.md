# 開発する人向けのメモ

ふつうに使うだけなら、このページは読まなくて大丈夫です。[README](README.md) を見てください。

## ソースコードから動かす

Python 3.10 以降が必要です。

```
pip install -r requirements.txt
python lyrics_player.py
```

## exe を作る（Windows）

PowerShell でこのフォルダを開いて、次を実行します。

```
.\build_exe.ps1 -Install
```

- `-Install` を付けると、必要な部品（PySide6・mutagen・PyInstaller）も自動で入れます。
- `dist\Lyriplayer.exe` ができます（フォントとライセンスの文書は exe の中に入ります）。
- スクリプトの実行が止められるときは、先に `Set-ExecutionPolicy -Scope Process Bypass` を実行してください。
- 自分で作った exe は版の番号が空になります。アプリの「アップデート」を使うと、公開している版に置き換わります。

## 新しい版の公開

`main` に変更が入ると、GitHub Actions（`.github/workflows/release.yml`）が Windows で exe を作り、
[Releases](https://github.com/kiring325/Lyriplayer/releases) に新しい版として公開します。

- 版の番号は `v年.月.日.通し番号`（日付は日本時間）です。
- リリースの説明には、前の版からのコミットの一覧が自動で入ります。
- アプリは GitHub の API で最新版を確認し、`Lyriplayer.exe` をダウンロードして入れ替えます。
  ダウンロードしたファイルは、大きさと SHA-256 が GitHub の値と合うときだけ使います。

## 素材について

アイコンは画像ファイルを使わず、プログラムの中で描いています（`icon_pixmap()`）。
ほかの人が作った素材画像は、リポジトリに入れないでください。
