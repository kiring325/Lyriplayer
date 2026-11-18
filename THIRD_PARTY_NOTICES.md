# ライセンスと使用している素材

## Lyriplayer 本体

Copyright (C) 2026 kiring325

Lyriplayer はフリーソフトウェアです。GNU General Public License（GPL）バージョン 3、
またはそれ以降のバージョンの条件のもとで、再配布・改変できます。全文は [LICENSE](LICENSE) を見てください。

このソフトウェアは役に立つことを願って配布していますが、**まったくの無保証**です。
商品性や特定の目的への適合性についての保証もありません。

> ソースコード（`lyrics_player.py`）と、exe の作り方（`build_exe.ps1`）はこのリポジトリで公開しています。
> 同梱しているフォントは、下の「フォント」にあるとおり SIL Open Font License に従います。

---

## 使用しているソフトウェア

配布している exe（`Lyriplayer.exe`）には、次のソフトウェアが入っています。

| 名前 | 作者 | ライセンス | 入手先 |
|---|---|---|---|
| Python | Python Software Foundation | PSF License | https://www.python.org/ |
| Qt 6 / PySide6（Qt for Python） | The Qt Company Ltd. ほか | GNU LGPL v3（[licenses/LGPL-3.0.txt](licenses/LGPL-3.0.txt)） | https://www.qt.io/ （ソースコード：https://code.qt.io/ ） |
| FFmpeg（Qt Multimedia が音声の再生に使用） | FFmpeg の開発者 | GNU LGPL v2.1 以降（[licenses/LGPL-2.1.txt](licenses/LGPL-2.1.txt)） | https://ffmpeg.org/ |
| mutagen（曲のタグの読み取り） | Joe Wreschnig, Michael Urman, Lukas Lalinsky, Christoph Reiter, Ben Ockmore ほか | GNU GPL v2 以降（[licenses/GPL-2.0.txt](licenses/GPL-2.0.txt)） | https://github.com/quodlibet/mutagen |
| PyInstaller（exe の起動部分） | PyInstaller Development Team | GNU GPL v2 以降（起動部分の例外条項付き） | https://pyinstaller.org/ |

Qt・PySide6・FFmpeg は LGPL で使っています。exe の中の Qt・PySide6 の部分は、利用者が別のバージョンに差し替えられます。
このリポジトリのソースコードと `build_exe.ps1` を使い、好きなバージョンの PySide6 を入れて exe を作り直してください。

---

## フォント（`fonts/`）

どちらも SIL Open Font License 1.1 で配布されています。フォントだけを販売することはできません。

| フォント | 作者 | ライセンス文書 |
|---|---|---|
| 源暎ラテゴ／源暎ラテミン v2（GenEiLatin） | おたもん（https://okoneya.jp/font/ ）。Adobe の「源ノ角ゴシック JP」などを改変したもの | [fonts/GenEiLatin-LICENSE.txt](fonts/GenEiLatin-LICENSE.txt)、[fonts/GenEiLatin-readme.txt](fonts/GenEiLatin-readme.txt) |
| しっぽり明朝 B1 | Copyright 2021 The Shippori Mincho Project Authors（https://github.com/fontdasu/ShipporiMincho ） | [fonts/ShipporiMincho-OFL.txt](fonts/ShipporiMincho-OFL.txt) |

---

## アイコン

ボタンなどのアイコンは、すべて Lyriplayer のプログラムの中で描いています（Lyriplayer 本体と同じ GPL v3）。
