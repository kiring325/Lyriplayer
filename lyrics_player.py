"""
Lyriplayer（歌詞プレイヤー）
  音楽フォルダを開くと、曲の歌詞を表示します。
  自動スクロールは「歌い出し〜歌い終わり」に合わせて一定の速さで流れます。

  歌詞の読み込み順:
    1. アプリ内に保存した歌詞（［歌詞を編集］で作ったもの。音楽フォルダは汚しません）
    2. 曲と同じ名前の .txt（同じフォルダ / lyrics / 歌詞 フォルダ）
    3. 曲と同じ名前の .lrc（時刻は使わずテキストとして表示）
    4. アプリ内の歌詞のうち、曲名＋アーティスト名が一致するもの（ファイルを移動した場合）
    5. 曲ファイルに埋め込まれた歌詞

  歌詞の背景には、アルバムアート（埋め込み画像 or フォルダ内の cover.jpg 等）をぼかして表示します。

  そのほか:
    ・メディアキー／Ctrl+Alt のキーで、ほかのウィンドウを使っていても操作できます（設定で切り替え）
    ・起動したとき、前回聴いていた曲の続きの位置から聴けます
    ・曲ファイルを「プログラムから開く」で開けます。2 つ目は起動せず、開いているウィンドウで再生します
    ・歌詞・リスト・タイミング・設定を zip にバックアップ／復元できます（設定 → データ）

必要なもの:
  pip install PySide6 mutagen
  （mutagen は任意。入れると曲名・アーティスト名・埋め込み歌詞・アルバムアートを読み込みます）

起動:
  python lyrics_player.py
"""
import os
import re
import sys
import json
import math
import unicodedata
import time
import base64
import random
import sqlite3
import zipfile
import tempfile
import subprocess
import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import (
    Qt, QUrl, QTimer, QSettings, QFileSystemWatcher, QElapsedTimer, QStandardPaths, QRect, QRectF,
    QPointF, Signal, QSize, QByteArray, QObject, QEvent, QThread,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import (
    QFont, QKeySequence, QShortcut, QTextCursor, QTextBlockFormat, QColor, QImage, QPixmap, QPainter,
    QLinearGradient, QPainterPath, QFontDatabase, QTextCharFormat, QIcon, QDesktopServices, QPen,
    QFontMetrics, QTransform,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QLabel, QSlider, QTextBrowser, QFileDialog, QSplitter, QDialog,
    QPlainTextEdit, QMessageBox, QLineEdit, QStyle, QFrame,
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect, QGridLayout,
    QComboBox, QMenu, QInputDialog, QAbstractItemView, QCheckBox,
    QStyledItemDelegate, QStyleOptionViewItem, QScrollArea, QKeySequenceEdit,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaMetaData

try:
    import mutagen
except ImportError:
    mutagen = None

APP = "Lyriplayer"
# 保存場所（AppData）・設定・二重起動の判定に使う内部の名前。変えると今までのデータが見えなくなるのでそのまま
APP_ID = "LyricsPlayer"
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma"}
LYRIC_SUBDIRS = ("lyrics", "歌詞")
COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "jacket")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
ACCENT = "#107EAE"
TEXT_DEFAULT = QColor("#ececec")
# フォント：スクリプトと同じ場所の fonts フォルダ / スクリプトと同じ場所 / ダウンロードの順に探す
FONT_DIRS = (
    Path(__file__).resolve().parent / "fonts",
    Path(__file__).resolve().parent,
)
LYRIC_FONT_FILE = "ShipporiMinchoB1-Bold.ttf"  # 歌詞
LYRIC_FONT_DIRS = FONT_DIRS + (
    Path.home() / "Downloads" / "Shippori_Mincho,Shippori_Mincho_B1" / "Shippori_Mincho_B1",
)
# UI は源暎ラテン 4 書体を役割で使い分ける
UI_FONT_FILES = {
    "go": "GenEiLateGoN_v2.ttf",     # 源暎ラテゴ      ：ボタン・一覧などの基本
    "goP": "GenEiLateGoP_v2.ttf",    # 源暎ラテゴ 詰   ：小さい説明文・ステータス（字詰めで収まりよく）
    "min": "GenEiLateMinN_v2.ttf",   # 源暎ラテミン    ：見出し・大きい数字（縦太で力強い）
    "minP": "GenEiLateMinP_v2.ttf",  # 源暎ラテミン 詰 ：曲名
}
UI_FONT_DIRS = FONT_DIRS + (
    Path.home() / "Downloads" / "GenEiLatin-Separate_v2.1" / "GenEiLatin-Separate_v2.1",
)
UI_FONT_FALLBACK = ["Yu Gothic UI", "Meiryo UI", "Hiragino Sans", "Noto Sans CJK JP", "Noto Sans JP"]
FONTS = {}  # 役割 → 読み込めたファミリー名


# ---------------------------------------------------------------- helpers
def fmt(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_t(sec):
    """0:12.3 のように 0.1 秒まで"""
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    return f"{m}:{sec - m * 60:04.1f}"


def linear_progress(t, start, end):
    """start〜end の間で t がどこまで進んだか（0〜1）"""
    return min(1.0, max(0.0, (t - start) / max(0.5, end - start)))


# 歌詞の中の「区切り」の行（1番と2番の間などに空白を入れる）。--- / －－－ / ーーー / ＝＝＝ など 3 文字以上だけの行
SPACER_RE = re.compile(r"^\s*(?:-{3,}|－{3,}|ー{3,}|―{3,}|={3,}|＝{3,})\s*$")
SPACER_MARK = "---"
SPACER_STATE = 7  # 表示中の文書で、区切りの行に付ける印（QTextBlock.userState）


def is_spacer(line):
    return bool(SPACER_RE.match(line))


def lyric_line_texts(text):
    """フレーズ合わせに使う歌詞の行（空行と区切りを除いた順番）"""
    return [ln.strip() for ln in norm_newlines(text or "").split("\n") if ln.strip() and not is_spacer(ln)]


def strip_edge_spacers(text):
    """先頭・末尾の区切りと空行は意味がないので外す"""
    lines = norm_newlines(text or "").split("\n")
    while lines and (not lines[0].strip() or is_spacer(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or is_spacer(lines[-1])):
        lines.pop()
    return "\n".join(lines)


def resolve_marks(raw, texts):
    """保存したフレーズの印 [[行番号, 秒, 行の文字], ...] を今の歌詞に当てはめて [(行番号, 秒)] にする。
    歌詞を少し書き換えて行がずれても、同じ文字の行を近くから探して合わせる。"""
    used, out = set(), []
    for m in raw or []:
        try:
            idx, t = int(m[0]), float(m[1])
        except (TypeError, ValueError, IndexError):
            continue
        txt = m[2] if len(m) > 2 else None
        j = None
        if 0 <= idx < len(texts) and (txt is None or texts[idx] == txt) and idx not in used:
            j = idx
        elif txt is not None:
            cands = [i for i, s in enumerate(texts) if s == txt and i not in used]
            if cands:
                j = min(cands, key=lambda i: abs(i - idx))
        if j is not None:
            used.add(j)
            out.append((j, t))
    return sorted(out)


def _smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


class ScrollModel:
    """再生時刻 ⇔ スクロール位置。
    ・印を付けたフレーズ（行）は、その時刻にちょうど「今ここ」に来る。印と印の間は一定の速さ
    ・区切りの空白は「歌詞の量」に数えず、次の歌詞が「今ここ」に来る直前の短い時間に通り過ぎる
      （次の歌い出しに合わせて、下から上がってくる）
    位置はすべてスクロールバーの値（その位置が「今ここ」に来るスクロール量）で表す。"""

    SPACER_DEFAULT = 2.5     # 区切りの空白を通り過ぎる秒数

    def __init__(self, intro, maximum, spacers, line_pos, marks, start, end):
        self.max = maximum
        self.spacers = [(a, h) for a, h in spacers if h > 0]
        c_intro, c_end = self.to_c(intro), self.to_c(maximum)
        pts = []
        for idx, t in sorted(marks, key=lambda m: m[1]):
            if not (0 <= idx < len(line_pos)):
                continue
            c = self.to_c(line_pos[idx])
            if pts and (t <= pts[-1][0] + 0.05 or c <= pts[-1][1] + 0.5):
                continue  # 順番が前後している印は使わない
            pts.append((t, c))
        if not pts or (start < pts[0][0] and c_intro < pts[0][1] - 0.5):
            pts.insert(0, (start, c_intro))
        if end > pts[-1][0] + 0.05 and c_end > pts[-1][1] + 0.5:
            pts.append((end, c_end))
        self.pts = pts
        # 区切りごとに、空白を通り過ぎる時間帯を決める。区切りのすぐ後の行が「今ここ」に来る時刻 ts に
        # 通り過ぎ終わるようにする（印を付けた行なら、ちょうどその時刻に来る）。順番は守る
        self.windows = []
        last = -1e9
        for a, h in self.spacers:
            ts = self.t_at_c(self.to_c(a))
            ws = max(last, ts - self.SPACER_DEFAULT)
            we = max(ws, ts)
            self.windows.append((ws, we, h))
            last = we

    # 区切りの空白を除いた「歌詞の量」の位置
    def to_c(self, y):
        c = y
        for a, h in self.spacers:
            if y >= a + h:
                c -= h
            elif y > a:
                c -= y - a
        return c

    def c_at(self, t):
        pts = self.pts
        t0, c0 = pts[0]
        if t <= t0:
            return c0 * t / t0 if t0 > 0 else c0
        for (ta, ca), (tb, cb) in zip(pts, pts[1:]):
            if t <= tb:
                return ca + linear_progress(t, ta, tb) * (cb - ca)
        return pts[-1][1]

    def t_at_c(self, c):
        pts = self.pts
        t0, c0 = pts[0]
        if c <= c0:
            return t0 * c / c0 if c0 > 0 else t0
        for (ta, ca), (tb, cb) in zip(pts, pts[1:]):
            if c <= cb:
                return progress_to_time((c - ca) / (cb - ca), ta, tb)
        return pts[-1][0]

    def y_at(self, t):
        y = self.c_at(t)
        for ws, we, h in self.windows:
            y += h * _smooth((t - ws) / (we - ws) if we > ws else float(t >= we))
        return min(self.max, max(0.0, y))

    def time_of(self, y):
        """位置 y（行の位置）が「今ここ」に来る時刻"""
        return self.t_at_c(self.to_c(y))


SHUFFLE_MODES = ("off", "all", "album")
SHUFFLE_LABEL = {"off": "シャッフル：オフ", "all": "シャッフル：全曲", "album": "シャッフル：アルバム"}


def album_key(path, info):
    """アルバムの見分け方：アルバムアーティスト＋アルバム名。タグが無ければフォルダ単位。"""
    if info.get("album"):
        return "a:" + (info.get("albumartist") or "").strip().lower() + "|" + info["album"].strip().lower()
    return "d:" + os.path.normcase(os.path.dirname(os.path.abspath(path)))


def build_play_order(n, start, mode, album_keys=None, sort_keys=None, playable=None, rng=random,
                     played_albums=None):
    """再生順（tracks のインデックスのリスト）を作る。

    off   : 並び順のまま
    all   : start を先頭に、残りを全部シャッフル
    album : アルバムの順番だけをシャッフル。アルバムの中はディスク番号→トラック番号→並び順のまま。
            start のアルバムを最初に置く（start の位置から再生される）。
            played_albums（この周回ですでに再生したアルバムのキー）は、まだのアルバムをすべて流したあとに回す。
    """
    idx = [i for i in range(n) if playable is None or playable[i]]
    if start not in idx:
        start = None
    if mode == "all":
        rest = [i for i in idx if i != start]
        rng.shuffle(rest)
        return ([start] if start is not None else []) + rest
    if mode == "album" and album_keys is not None:
        groups = {}
        for i in idx:
            groups.setdefault(album_keys[i], []).append(i)
        for g in groups.values():
            if sort_keys is not None:
                g.sort(key=lambda i: (sort_keys[i], i))
        glist = list(groups.values())
        first = next((g for g in glist if start in g), None)
        others = [g for g in glist if g is not first]
        played = played_albums or set()
        fresh = [g for g in others if album_keys[g[0]] not in played]
        again = [g for g in others if album_keys[g[0]] in played]
        rng.shuffle(fresh)
        rng.shuffle(again)
        return (first or []) + [i for g in fresh + again for i in g]
    return idx


# 一覧の並び替え（値, 表示名）。並び替えは表示だけで、リストに保存した曲順は変えない
LIST_SORTS = (
    ("", "並び：標準"),
    ("album", "アルバム名（あ→ん）"),
    ("album_desc", "アルバム名（ん→あ）"),
    ("year", "リリース年（古い順）"),
    ("year_desc", "リリース年（新しい順）"),
    ("artist", "アーティスト（あ→ん）"),
    ("artist_desc", "アーティスト（ん→あ）"),
)


def kana_key(text):
    """あいうえお順の並び用のキー。全角／半角・カタカナ／ひらがな・大文字／小文字の違いをそろえ、
    数字は数の大きさで比べる（英数字 → かな → 漢字 の順になる）"""
    t = unicodedata.normalize("NFKC", text or "").strip().lower()
    t = "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in t)  # カタカナ → ひらがな
    return natkey(t)


def sort_track_order(items, mode):
    """並び替えた順番（items のインデックスのリスト）を返す。
    items は曲ごとの dict（album, artist, year, disc, track, name）。
    同じアルバムの曲はまとめて、アルバムの中はディスク番号→トラック番号→名前の順（降順でもここは昇順のまま）。
    アルバム名・アーティスト・年が分からない曲は、昇順でも降順でも最後に並べる。"""
    n = len(items)
    if not mode:
        return list(range(n))
    desc = mode.endswith("_desc")
    kind = mode[:-5] if desc else mode

    def within(i):
        it = items[i]
        return (it.get("disc") or 1, it["track"] if it.get("track") is not None else 10 ** 6, natkey(it.get("name", "")))

    def year_num(i):
        y = items[i].get("year") or ""
        return int(y) if y.isdigit() else 10 ** 4

    if kind == "album":
        primary = lambda i: kana_key(items[i].get("album"))
        tie = lambda i: (kana_key(items[i].get("artist")),)
        has = lambda i: bool((items[i].get("album") or "").strip())
    elif kind == "year":
        primary = year_num
        tie = lambda i: (kana_key(items[i].get("album")), kana_key(items[i].get("artist")))
        has = lambda i: (items[i].get("year") or "").isdigit()
    elif kind == "artist":
        primary = lambda i: kana_key(items[i].get("artist"))
        tie = lambda i: (year_num(i), kana_key(items[i].get("album")))  # 同じアーティストはリリース順
        has = lambda i: bool((items[i].get("artist") or "").strip())
    else:
        return list(range(n))
    idx = sorted(range(n), key=lambda i: (tie(i), within(i)))  # 先に 2 番目以降の並びを決めておく
    present = [i for i in idx if has(i)]
    missing = [i for i in idx if not has(i)]
    present.sort(key=primary, reverse=desc)  # 安定ソートなので、同じ値の中は上の並びのまま
    return present + missing


def progress_to_time(p, start, end):
    """linear_progress の逆：進み具合 p（0〜1）になる再生時刻（秒）"""
    return start + min(1.0, max(0.0, p)) * max(0.5, end - start)


def natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.lower())]


def norm_newlines(t):
    return t.replace("\r\n", "\n").replace("\r", "\n")


def path_key(p):
    return os.path.normcase(os.path.abspath(p))


def app_data_dir():
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    os.makedirs(d, exist_ok=True)
    return d


def read_text_file(path):
    b = Path(path).read_bytes()
    if b.startswith(b"\xef\xbb\xbf"):
        return b[3:].decode("utf-8", "replace")
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    for enc in ("utf-8", "cp932"):  # cp932 = 古いメモ帳の Shift_JIS
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("latin-1")


_TS = re.compile(r"\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]")
_WTS = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_META = re.compile(r"^\[[a-zA-Z#]+:.*\]$")


def strip_lrc(text):
    """LRC から時刻を取り除いてただのテキストにする"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if _META.match(s):
            continue
        out.append(_WTS.sub("", _TS.sub("", s)).strip())
    return "\n".join(out).strip("\n")


def read_meta(path):
    title = os.path.splitext(os.path.basename(path))[0]
    artist = ""
    if mutagen:
        try:
            f = mutagen.File(path, easy=True)
            if f is not None and f.tags:
                tags = f.tags
                if hasattr(tags, "getall"):  # easy 非対応の ID3（WAV / AIFF など）
                    t, a = tags.getall("TIT2"), tags.getall("TPE1")
                    title = str(t[0]) if t else title
                    artist = str(a[0]) if a else ""
                else:
                    title = (tags.get("title") or [title])[0]
                    artist = (tags.get("artist") or [""])[0]
        except Exception:
            pass
    return title, artist


def _num(v):
    """'3/12' → 3、読めなければ None"""
    try:
        return int(str(v).split("/")[0].strip())
    except (ValueError, TypeError):
        return None


def _year_of(values):
    """タグの日付の値（'2019-03-01' など）から 4 桁の年を取り出す"""
    for x in (values if isinstance(values, list) else [values]):
        m = re.search(r"(1[89]|20)\d\d", str(x))
        if m:
            return m.group(0)
    return ""


def read_album_info(path):
    info = {"album": "", "albumartist": "", "disc": None, "track": None,
            "year": "", "album_sort": "", "artist_sort": ""}
    if not mutagen:
        return info
    try:
        f = mutagen.File(path, easy=True)
        tags = f.tags if f is not None else None
        if not tags:
            return info
        if hasattr(tags, "getall"):  # easy 非対応の ID3（WAV など）
            def g(fid):
                v = tags.getall(fid)
                return str(v[0]) if v else ""
            info.update(album=g("TALB"), albumartist=g("TPE2") or g("TPE1"),
                        disc=_num(g("TPOS")), track=_num(g("TRCK")),
                        year=_year_of(g("TDRC") or g("TYER") or g("TDOR") or g("TORY")),
                        album_sort=g("TSOA"), artist_sort=g("TSO2") or g("TSOP"))
            return info

        def first(*keys):
            for k in keys:
                v = tags.get(k)
                if v:
                    return str(v[0])
            return ""
        info.update(album=first("album"), albumartist=first("albumartist", "album artist", "artist"),
                    disc=_num(first("discnumber")), track=_num(first("tracknumber")),
                    year=_year_of(first(*YEAR_KEYS)),
                    album_sort=first("albumsort", "ALBUMSORT"),
                    artist_sort=first("albumartistsort", "ALBUMARTISTSORT", "artistsort", "ARTISTSORT"))
    except Exception:
        pass
    return info


YEAR_KEYS = ("date", "originaldate", "year", "originalyear", "WM/Year", "WM/OriginalReleaseYear",
             "\xa9day", "TDRC", "TYER", "TDOR", "TORY")


def embedded_lyrics(path):
    if not mutagen:
        return None
    try:
        f = mutagen.File(path)
        if f is None or f.tags is None:
            return None
        tags = f.tags
        if hasattr(tags, "getall"):  # MP3 (ID3)
            for fr in tags.getall("USLT"):
                if str(fr.text).strip():
                    return str(fr.text)
            for fr in tags.getall("SYLT"):
                txt = "".join(t for t, _ in fr.text)
                if txt.strip():
                    return txt
        for key in ("lyrics", "unsyncedlyrics", "LYRICS", "UNSYNCEDLYRICS", "\xa9lyr"):
            try:
                if key in tags:
                    v = tags[key]
                    v = v[0] if isinstance(v, list) else v
                    if str(v).strip():
                        return str(v)
            except Exception:
                pass
    except Exception:
        pass
    return None


def find_lyric_file(path):
    """曲の隣にある歌詞ファイル → (テキスト, 出どころ, ファイルパス) / なければ None"""
    d = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    cands = [os.path.join(d, stem + ".txt")]
    cands += [os.path.join(d, sub, stem + ".txt") for sub in LYRIC_SUBDIRS]
    cands += [os.path.join(d, stem + ".lrc")]
    for c in cands:
        if os.path.isfile(c):
            try:
                t = read_text_file(c)
            except OSError:
                continue
            if c.lower().endswith(".lrc"):
                return strip_lrc(t), "LRCファイル（時刻は使わずテキストとして表示）", c
            return norm_newlines(t), "テキスト：" + os.path.basename(c), c
    return None


def scan_audio(d):
    """(フォルダ以下の音楽ファイル（自然な並び順）, 見たフォルダの一覧)"""
    d = os.path.abspath(d)
    files, dirs_seen = [], []
    for root, dirs, fs in os.walk(d):
        dirs.sort(key=natkey)
        dirs_seen.append(root)
        for f in fs:
            if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                files.append(os.path.join(root, f))
    files.sort(key=lambda p: natkey(os.path.relpath(p, d)))
    return files, dirs_seen


# ---------------------------------------------------------------- album art
def _pick_picture(pics):
    """APIC / FLAC Picture のリストから表紙（type 3）を優先して選ぶ"""
    pics = [p for p in pics if getattr(p, "data", None)]
    if not pics:
        return None
    for p in pics:
        if getattr(p, "type", None) == 3:
            return p.data
    return pics[0].data


def embedded_cover(path):
    if not mutagen:
        return None
    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    try:
        data = _pick_picture(getattr(f, "pictures", None) or [])  # FLAC
        if data:
            return data
        tags = f.tags
        if tags is None:
            return None
        if hasattr(tags, "getall"):  # MP3 (ID3)
            data = _pick_picture(tags.getall("APIC"))
            if data:
                return data
        try:  # MP4 / M4A
            if "covr" in tags and tags["covr"]:
                return bytes(tags["covr"][0])
        except Exception:
            pass
        try:  # Ogg Vorbis / Opus
            from mutagen.flac import Picture
            vals = tags.get("metadata_block_picture") or tags.get("METADATA_BLOCK_PICTURE") or []
            pics = []
            for v in vals:
                try:
                    pics.append(Picture(base64.b64decode(v)))
                except Exception:
                    pass
            data = _pick_picture(pics)
            if data:
                return data
        except Exception:
            pass
        try:  # WMA
            for v in tags.get("WM/Picture", []):
                raw = bytes(v.value)
                # WM/Picture: type(1) size(4) mime(utf16z) desc(utf16z) data
                i = 5
                for _ in range(2):
                    while i + 1 < len(raw) and raw[i:i + 2] != b"\x00\x00":
                        i += 2
                    i += 2
                if i < len(raw):
                    return raw[i:]
        except Exception:
            pass
    except Exception:
        pass
    return None


def folder_cover(path):
    d = os.path.dirname(path)
    try:
        names = os.listdir(d)
    except OSError:
        return None
    best = None
    for n in names:
        stem, ext = os.path.splitext(n)
        if ext.lower() not in IMAGE_EXT:
            continue
        s = stem.lower()
        if s in COVER_NAMES:
            return os.path.join(d, n)
        if best is None and (s.startswith("albumart") or s == os.path.splitext(os.path.basename(path))[0].lower()):
            best = os.path.join(d, n)
    return best


def load_cover(path):
    """QImage（見つからなければ None）"""
    data = embedded_cover(path)
    if data:
        img = QImage.fromData(data)
        if not img.isNull():
            return img
    fp = folder_cover(path)
    if fp:
        img = QImage(fp)
        if not img.isNull():
            return img
    return None


def make_blurred(img, size=120, radius=18):
    """小さく縮めてからぼかした画像を返す（あとで拡大して背景に使う）"""
    small = img.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    w, h = small.width(), small.height()
    pad = radius * 2
    cw, ch = w + pad * 2, h + pad * 2
    # 端が透明に溶けないよう、少し大きく引き伸ばしてからぼかし、中央を切り出す
    canvas = small.scaled(cw, ch, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, cw, ch)
    item = QGraphicsPixmapItem(QPixmap.fromImage(canvas))
    eff = QGraphicsBlurEffect()
    eff.setBlurRadius(radius)
    eff.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(eff)
    scene.addItem(item)
    out = QImage(cw, ch, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.black)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    scene.render(p, QRectF(0, 0, cw, ch), QRectF(0, 0, cw, ch))
    p.end()
    return out.copy(pad, pad, w, h)


def rounded_thumb(img, side, radius=2):
    src = img.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    src = src.copy((src.width() - side) // 2, (src.height() - side) // 2, side, side)
    out = QPixmap(side, side)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, side, side, radius, radius)
    p.setClipPath(path)
    p.drawImage(0, 0, src)
    p.end()
    return out


def load_lyric_font():
    """歌詞用フォントを読み込んでファミリー名を返す（見つからなければ None）"""
    return load_font_file(LYRIC_FONT_FILE, LYRIC_FONT_DIRS)



# ---------------------------------------------------------------- アイコン（すべてプログラムで描く。画像ファイルは使わない）
_FINE = 40.0  # 図形の合成は 40 倍の大きさで行う（小さいまま合成すると曲線がカクカクになるため）


def _combine(a, b, op):
    up, down = QTransform().scale(_FINE, _FINE), QTransform().scale(1 / _FINE, 1 / _FINE)
    a2, b2 = up.map(a), up.map(b)
    r = a2.united(b2) if op == "+" else a2.subtracted(b2)
    return down.map(r)


def _gear_path(cx, cy, r_out, r_body, teeth, tooth_w):
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r_body, r_body)
    for i in range(teeth):
        a = 360.0 / teeth * i
        t = QPainterPath()
        t.addRoundedRect(QRectF(-tooth_w / 2, -r_out, tooth_w, r_out - r_body + 0.8), 0.6, 0.6)
        tr = QTransform().translate(cx, cy).rotate(a)
        path = _combine(path, tr.map(t), "+")
    return path


def _arrowhead(p, tip, angle_deg, length=4.2, width=4.6):
    """tip の位置に、angle_deg の向き（0=右、90=下）の三角の矢じりを描く"""
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    bx, by = tip[0] - dx * length, tip[1] - dy * length
    nx, ny = -dy * width / 2, dx * width / 2
    path = QPainterPath()
    path.moveTo(*tip)
    path.lineTo(bx + nx, by + ny)
    path.lineTo(bx - nx, by - ny)
    path.closeSubpath()
    p.save()
    p.setPen(Qt.NoPen)
    p.drawPath(path)
    p.restore()


def icon_pixmap(name, color="#e4e4e4", size=22):
    """操作ボタンなどの記号を描く（24×24 の升目で設計し、size に合わせて高解像度で描く）"""
    px = size * 3
    img = QImage(px, px, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(px / 24.0, px / 24.0)
    c = QColor(color)
    thin = QPen(c, 1.1)
    thin.setJoinStyle(Qt.RoundJoin)
    thick = QPen(c, 2.8, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin)
    p.setBrush(Qt.NoBrush)
    if name == "settings":  # 大小 2 つの歯車（線画）
        big = QPen(c, 0.85)
        big.setJoinStyle(Qt.RoundJoin)
        p.setPen(big)
        p.drawPath(_gear_path(8.4, 10.6, 7.9, 6.1, 9, 2.0))
        p.drawEllipse(QPointF(8.4, 10.6), 3.0, 3.0)
        # 小さい歯車は線をさらに細くし、歯を少なめにして、線の太さでつぶれないようにする
        small = QPen(c, 0.7)
        small.setJoinStyle(Qt.RoundJoin)
        p.setPen(small)
        p.drawPath(_gear_path(19.3, 15.9, 4.7, 3.55, 8, 1.35))
        p.drawEllipse(QPointF(19.3, 15.9), 1.75, 1.75)
    elif name == "chevron":  # 細い ∨
        pen = QPen(c, 1.1)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawPolyline([QPointF(3, 8), QPointF(12, 16.5), QPointF(21, 8)])
    elif name == "game":  # コントローラー（塗り）。左右対称のなめらかな外形を 1 本の線で作る
        half = [  # 上の中央から、左側を反時計回りに下の中央まで（3 次曲線の制御点）
            ((10.0, 5.2), (8.0, 4.7), (6.3, 4.8)),
            ((3.3, 5.0), (1.7, 7.4), (1.2, 11.2)),
            ((0.8, 14.8), (1.3, 19.1), (4.3, 19.3)),
            ((6.3, 19.4), (7.5, 17.6), (8.7, 16.3)),
            ((9.7, 15.3), (10.7, 15.1), (12.0, 15.1)),
        ]
        body = QPainterPath()
        body.moveTo(12.0, 6.0)
        for c1, c2, end in half:
            body.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*end))
        # 右半分は、左半分を左右反転して逆向きにたどる（下の中央から上の中央へ戻る）
        starts = [(12.0, 6.0)] + [seg[2] for seg in half[:-1]]  # 各区間の始まりの点
        for (c1, c2, _end), start in zip(reversed(half), reversed(starts)):
            body.cubicTo(QPointF(24 - c2[0], c2[1]), QPointF(24 - c1[0], c1[1]), QPointF(24 - start[0], start[1]))
        body.closeSubpath()
        holes = QPainterPath()
        cross_h, cross_v = QPainterPath(), QPainterPath()
        cross_h.addRect(QRectF(4.3, 9.4, 5.8, 1.9))  # 十字キー（横と縦をつなげて 1 つの十字にする）
        cross_v.addRect(QRectF(6.25, 7.45, 1.9, 5.8))
        holes = _combine(cross_h, cross_v, "+")
        for bx, by in ((16.6, 8.4), (18.6, 10.35), (16.6, 12.3), (14.6, 10.35)):  # 4 つのボタン
            b = QPainterPath()
            b.addEllipse(QPointF(bx, by), 1.1, 1.1)
            holes = _combine(holes, b, "+")
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(_combine(body, holes, "-"))
    elif name in ("volume", "volume_mute"):  # スピーカー（＋音の波）
        sp = QPainterPath()
        sp.moveTo(2.5, 9.2)
        sp.lineTo(6.5, 9.2)
        sp.lineTo(11.5, 4.8)
        sp.lineTo(11.5, 19.2)
        sp.lineTo(6.5, 14.8)
        sp.lineTo(2.5, 14.8)
        sp.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(sp)
        if name == "volume":
            pen = QPen(c, 1.5)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for r in (3.6, 6.6, 9.6):
                p.drawArc(QRectF(11.5 - r, 12 - r, r * 2, r * 2), -50 * 16, 100 * 16)
    elif name == "repeat":  # ⇄ のループ
        p.setPen(thick)
        p.setBrush(c)
        p.drawPolyline([QPointF(4.5, 13.5), QPointF(4.5, 6.8), QPointF(16.0, 6.8)])
        _arrowhead(p, (22, 6.8), 0, 6.2, 7.6)
        p.drawPolyline([QPointF(19.5, 10.5), QPointF(19.5, 17.2), QPointF(8.0, 17.2)])
        _arrowhead(p, (2, 17.2), 180, 6.2, 7.6)
    elif name == "reload":  # くるっと回る矢印
        p.setPen(thick)
        # 左から下をまわって上まで来た円の線の先に、左向きの矢じり（↺）
        p.drawArc(QRectF(5.5, 7.0, 15, 15), 180 * 16, 270 * 16)
        p.setBrush(c)
        _arrowhead(p, (5.2, 7.0), 180, 7.0, 9.4)
    elif name == "shuffle":  # 交差する 2 本の矢印
        p.setPen(thick)
        p.setBrush(c)
        p.drawPolyline([QPointF(2.0, 6.8), QPointF(7.2, 6.8), QPointF(14.2, 17.2), QPointF(16.5, 17.2)])
        _arrowhead(p, (22.5, 17.2), 0, 6.2, 7.6)
        p.drawPolyline([QPointF(2.0, 17.2), QPointF(7.2, 17.2), QPointF(14.2, 6.8), QPointF(16.5, 6.8)])
        _arrowhead(p, (22.5, 6.8), 0, 6.2, 7.6)
    elif name in ("prev", "next"):  # ⏮ ⏭
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        if name == "next":
            p.translate(24, 0)
            p.scale(-1, 1)
        p.drawRect(QRectF(3.0, 2.5, 3.6, 19.0))
        tri = QPainterPath()
        tri.moveTo(21.5, 2.5)
        tri.lineTo(6.6, 12.0)
        tri.lineTo(21.5, 21.5)
        tri.closeSubpath()
        p.drawPath(tri)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(3)
    return pm


def media_icon(name, color="#e4e4e4", size=22):
    return QIcon(icon_pixmap(name, color, size))


def settings_icon(color="#e4e4e4", size=28):
    return QIcon(icon_pixmap("settings", color, size))


def load_font_file(name, dirs):
    for d in dirs:
        fp = d / name
        if fp.is_file():
            fid = QFontDatabase.addApplicationFont(str(fp))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                return fams[0]
    return None


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(r, g, b):
    """相対輝度（0〜1、引数は 0〜1）"""
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


SMOKE = 0.20  # 背景のスモークの強さ（0〜1）。0 = 元画像そのまま、1 = ぼかし最大＋完全に覆う。設定画面で変更
VEIL_DARK = (12, 12, 12)     # 明るい文字のときにかける暗いスモーク
VEIL_LIGHT = (245, 245, 245)  # 暗い文字のときにかける明るいスモーク


def smoke_image(img, strength):
    """スモークの強さに合わせてぼかした背景画像。0 なら元画像をそのまま返す"""
    if strength <= 0:
        return img
    size = int(360 - 270 * strength)       # 小さく縮めるほど強くぼける
    radius = 3 + 17 * strength
    return make_blurred(img, size=size, radius=radius)


def analyze_background(blurred, strength=None):
    """背景から (文字色, スモークの濃さ 0〜255, スモークの色) を決める。

    スモークの濃さは設定した強さそのまま。文字色は 1 曲 1 色で、アートの代表色を少しだけ混ぜた
    明るい色か暗い色のうち、そのスモークをかけた背景でより読みやすい方を選ぶ
    （明るい文字には暗いスモーク、暗い文字には明るいスモーク）。
    """
    if strength is None:
        strength = SMOKE
    s = blurred.scaled(40, 40, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    px = []
    wr = wg = wb = wsum = 0.0
    for y in range(s.height()):
        for x in range(s.width()):
            c = s.pixelColor(x, y)
            r, g, b = c.redF(), c.greenF(), c.blueF()
            px.append((r, g, b))
            # 鮮やかな色ほど重く数えて「アートの色味」を拾う
            sat = max(r, g, b) - min(r, g, b)
            w = sat * sat + 1e-4
            wr += r * w
            wg += g * w
            wb += b * w
            wsum += w
    tint = QColor.fromRgbF(wr / wsum, wg / wsum, wb / wsum)
    h, sat, _l, _a = tint.getHslF()
    if h < 0:  # 無彩色
        h, sat = 0.0, 0.0
    light = QColor.fromHslF(h, min(sat, 0.55) * 0.45, 0.93)
    dark = QColor.fromHslF(h, min(sat, 0.55) * 0.5, 0.11)

    def lums(alpha, veil):
        vr, vg, vb = (v / 255 for v in veil)
        return sorted(luminance(r * (1 - alpha) + vr * alpha, g * (1 - alpha) + vg * alpha, b * (1 - alpha) + vb * alpha)
                      for r, g, b in px)

    def score(text, alpha, veil):
        lt = luminance(text.redF(), text.greenF(), text.blueF())
        ls = lums(alpha, veil)
        # 明るい文字は背景の明るい側、暗い文字は背景の暗い側がいちばん読みにくい
        worst = ls[int(len(ls) * 0.95)] if veil == VEIL_DARK else ls[int(len(ls) * 0.05)]
        return contrast(lt, worst)

    a = max(0.0, min(1.0, strength))
    # 明るい文字を優先（同じくらいなら明るい文字）。暗い文字の方がはっきり読みやすいときだけ切り替える
    if score(dark, a, VEIL_LIGHT) > score(light, a, VEIL_DARK) * 1.15:
        return dark, int(a * 255), VEIL_LIGHT
    return light, int(a * 255), VEIL_DARK


# ---------------------------------------------------------------- lyrics store
# ---------------------------------------------------------------- どのウィンドウを使っていても効くキー（Windows）
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
CA = MOD_CONTROL | MOD_ALT
# (ID, 修飾キー, 仮想キーコード, 動作, 表示名)
MEDIA_HOTKEYS = (
    (1, MOD_NOREPEAT, 0xB3, "play", "再生／一時停止キー"),
    (2, MOD_NOREPEAT, 0xB0, "next", "次の曲キー"),
    (3, MOD_NOREPEAT, 0xB1, "prev", "前の曲キー"),
    (4, MOD_NOREPEAT, 0xB2, "stop", "停止キー"),
)
COMBO_HOTKEYS = (
    (11, CA | MOD_NOREPEAT, ord("P"), "play", "Ctrl+Alt+P"),
    (12, CA | MOD_NOREPEAT, 0x27, "next", "Ctrl+Alt+→"),
    (13, CA | MOD_NOREPEAT, 0x25, "prev", "Ctrl+Alt+←"),
    (16, CA | MOD_NOREPEAT, ord("M"), "mute", "Ctrl+Alt+M"),
)
COMBO_HELP = ("Ctrl+Alt+P：再生／一時停止　Ctrl+Alt+← →：前の曲／次の曲\n"
              "Ctrl+Alt+M：ミュート")
MOD_SHIFT, MOD_WIN = 0x0004, 0x0008
GAME_HOTKEY_ID = 21
GAME_HOTKEY_DEFAULT = "Ctrl+Alt+G"
# 文字入力の邪魔にならないよう、Ctrl・Alt・Win なしでも登録してよいキー
_LONE_OK_VK = set(range(0x70, 0x88)) | {0x13, 0x91}  # F1〜F24、Pause、ScrollLock


def _qt_key_to_vk(key, keypad=False):
    """Qt のキー → Windows の仮想キーコード。対応していないキーは None"""
    if keypad and Qt.Key_0 <= key <= Qt.Key_9:
        return 0x60 + (key - Qt.Key_0)
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        return int(key)  # 'A'〜'Z'、'0'〜'9' は文字コードと同じ
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return 0x70 + (key - Qt.Key_F1)
    table = {
        Qt.Key_Space: 0x20, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22, Qt.Key_End: 0x23, Qt.Key_Home: 0x24,
        Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
        Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E, Qt.Key_Pause: 0x13, Qt.Key_ScrollLock: 0x91,
        Qt.Key_Tab: 0x09, Qt.Key_Backspace: 0x08, Qt.Key_Return: 0x0D, Qt.Key_Enter: 0x0D,
        Qt.Key_Asterisk: 0x6A, Qt.Key_Plus: 0xBB, Qt.Key_Minus: 0xBD, Qt.Key_Comma: 0xBC, Qt.Key_Period: 0xBE,
        Qt.Key_Slash: 0xBF, Qt.Key_Semicolon: 0xBA, Qt.Key_BracketLeft: 0xDB, Qt.Key_BracketRight: 0xDD,
        Qt.Key_Backslash: 0xDC, Qt.Key_Apostrophe: 0xDE, Qt.Key_QuoteLeft: 0xC0, Qt.Key_Colon: 0xBA,
        Qt.Key_At: 0xC0, Qt.Key_AsciiCircum: 0xDE,
    }
    if keypad:
        table.update({Qt.Key_Plus: 0x6B, Qt.Key_Minus: 0x6D, Qt.Key_Slash: 0x6F, Qt.Key_Period: 0x6E})
    v = table.get(key)
    return v


def parse_hotkey(text):
    """'Ctrl+Alt+G' などの文字 → (修飾キー, 仮想キーコード, エラーの説明)。空なら (0, None, "")"""
    if not text:
        return 0, None, ""
    seq = QKeySequence(text, QKeySequence.PortableText)
    if seq.isEmpty():
        return 0, None, "このキーは読み取れませんでした。"
    comb = seq[0]
    key, mods = comb.key(), comb.keyboardModifiers()
    if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_unknown):
        return 0, None, "Ctrl や Alt だけでなく、文字などのキーと組み合わせてください。"
    vk = _qt_key_to_vk(key, bool(mods & Qt.KeypadModifier))
    if vk is None:
        return 0, None, "このキーはショートカットに使えません。ほかのキーを選んでください。"
    m = 0
    if mods & Qt.ControlModifier:
        m |= MOD_CONTROL
    if mods & Qt.AltModifier:
        m |= MOD_ALT
    if mods & Qt.ShiftModifier:
        m |= MOD_SHIFT
    if mods & Qt.MetaModifier:
        m |= MOD_WIN
    if not (m & (MOD_CONTROL | MOD_ALT | MOD_WIN)) and vk not in _LONE_OK_VK:
        return 0, None, "文字の入力などの邪魔になるので、Ctrl か Alt と組み合わせてください（F1〜F12 などは単独でも使えます）。"
    if m == MOD_CONTROL and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        return 0, None, "Ctrl＋文字（Ctrl+C のコピーなど）は、ほかのアプリでよく使うので選べません。Alt や Shift も一緒に押してください。"
    if (m, vk) in {(mm & ~MOD_NOREPEAT, v) for _, mm, v, _, _ in COMBO_HOTKEYS + MEDIA_HOTKEYS}:
        return m, vk, "このキーはほかの操作（再生や音量など）で使っています。ほかのキーを選んでください。"
    return m, vk, ""


def hotkey_label(text):
    """画面に出すキーの名前（例：Ctrl+Alt+G）。空なら「なし」"""
    if not text:
        return "なし"
    return QKeySequence(text, QKeySequence.PortableText).toString(QKeySequence.NativeText)
IS_WIN = sys.platform == "win32"
if IS_WIN:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


# ---------------------------------------------------------------- タイトルバーと縁の色（Windows）
# ふつうの Windows のウィンドウのまま（スナップ・サイズ変更・影・最小化はそのまま使える）、
# タイトルバーと縁をアプリと同じ暗い色に塗って、中身と一体に見せる
FRAME_COLOR = "#181818"       # 左の一覧・下のバーと同じ色
FRAME_TEXT_COLOR = "#9a9a9a"  # タイトルの文字は控えめに
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 の古いバージョン
DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 34, 35, 36
_dwmapi = None


def _colorref(hex_color):
    c = QColor(hex_color)
    return c.red() | (c.green() << 8) | (c.blue() << 16)


def style_title_bar(widget):
    """ウィンドウのタイトルバーと縁をアプリの色にする。
    Windows 11 は色をそのまま指定、Windows 10 は暗いタイトルバー（ダークモード）になる。Windows 以外は何もしない"""
    global _dwmapi
    if not IS_WIN:
        return
    try:
        if _dwmapi is None:
            _dwmapi = ctypes.WinDLL("dwmapi")
            _dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            _dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = wintypes.HWND(int(widget.winId()))

        def setattr_(attr, value):
            v = ctypes.c_int(value)
            return _dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0

        if not setattr_(DWMWA_USE_IMMERSIVE_DARK_MODE, 1):
            setattr_(DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1)
        # 以下は Windows 11 から。古い Windows では失敗するだけで害はない
        setattr_(DWMWA_CAPTION_COLOR, _colorref(FRAME_COLOR))
        setattr_(DWMWA_BORDER_COLOR, _colorref(FRAME_COLOR))
        setattr_(DWMWA_TEXT_COLOR, _colorref(FRAME_TEXT_COLOR))
    except (OSError, AttributeError, ValueError):
        pass


class TitleBarStyler(QObject):
    """アプリのウィンドウ（メイン・設定・歌詞の編集・確認のメッセージなど）が開くたびに、タイトルバーを塗る"""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and obj.windowType() in (Qt.Window, Qt.Dialog) and not obj.property("_lp_styled"):
            obj.setProperty("_lp_styled", True)
            style_title_bar(obj)
        return False


# ---------------------------------------------------------------- タスクバーのボタン（Windows）
# タスクバーのアイコンにマウスを乗せたときに出る小さな画面の下に、操作ボタンを並べる（ITaskbarList3 のサムネイル ツールバー）
WM_COMMAND = 0x0111
THBN_CLICKED = 0x1800
THB_ICON, THB_TOOLTIP, THB_FLAGS = 0x2, 0x4, 0x8
THBF_ENABLED, THBF_DISABLED = 0x0, 0x1
TASKBAR_BUTTONS = ("restart", "play", "next", "game")  # 左から並ぶ順
TASKBAR_BUTTON_BASE_ID = 4100  # ボタンの ID（4100〜4103）


class THUMBBUTTON(ctypes.Structure):
    _fields_ = [("dwMask", ctypes.c_uint32), ("iId", ctypes.c_uint), ("iBitmap", ctypes.c_uint),
                ("hIcon", ctypes.c_void_p), ("szTip", ctypes.c_wchar * 260), ("dwFlags", ctypes.c_uint32)]


def taskbar_glyph(name, color="#ffffff", size=16):
    """タスクバーのボタン用の記号を描く（size×size の透明な画像）"""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    if name in ("game_on", "game_off"):
        pm = icon_pixmap("game", color, size)
        if pm is not None:
            g = pm.toImage()
            g.setDevicePixelRatio(1)  # 高解像度用の倍率を外して、ちょうど size に収める
            g = g.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p = QPainter(img)
            p.drawImage((size - g.width()) // 2, (size - g.height()) // 2, g)
            p.end()
            return img
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    u = size / 16.0

    def tri(x1, y1, x2, y2, x3, y3):
        path = QPainterPath()
        path.moveTo(x1 * u, y1 * u)
        path.lineTo(x2 * u, y2 * u)
        path.lineTo(x3 * u, y3 * u)
        path.closeSubpath()
        p.drawPath(path)

    if name == "play":
        tri(4, 2.5, 13.5, 8, 4, 13.5)
    elif name == "pause":
        p.drawRect(QRectF(3.5 * u, 2.5 * u, 3 * u, 11 * u))
        p.drawRect(QRectF(9.5 * u, 2.5 * u, 3 * u, 11 * u))
    elif name == "restart":  # ⏮：縦の棒＋左向きの三角
        p.drawRect(QRectF(2.5 * u, 3 * u, 2 * u, 10 * u))
        tri(13.5, 3, 5, 8, 13.5, 13)
    elif name == "next":  # ⏭：右向きの三角＋縦の棒
        tri(2.5, 3, 11, 8, 2.5, 13)
        p.drawRect(QRectF(11.5 * u, 3 * u, 2 * u, 10 * u))
    else:  # ゲームの画像が無いとき：コントローラーの形を簡単に描く
        p.drawRoundedRect(QRectF(1 * u, 4.5 * u, 14 * u, 7 * u), 3.5 * u, 3.5 * u)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.drawRect(QRectF(3.5 * u, 7.5 * u, 4 * u, 1 * u))
        p.drawRect(QRectF(5 * u, 6 * u, 1 * u, 4 * u))
        p.drawEllipse(QPointF(11.5 * u, 8 * u), 1 * u, 1 * u)
    p.end()
    return img


class TaskbarButtons:
    """タスクバーの小さな画面の下のボタン。Windows 以外では何もしない。
    Windows がタスクバーのボタンを作ったという知らせ（TaskbarButtonCreated）が来てから登録する"""

    def __init__(self, widget):
        self.widget = widget
        self.tb = None       # ITaskbarList3
        self.added = False
        self.icons = {}
        self.state = {"playing": False, "game": False, "has_track": False}
        self.created_msg = None
        if IS_WIN:
            try:
                self.created_msg = _user32.RegisterWindowMessageW("TaskbarButtonCreated")
            except (OSError, AttributeError):
                self.created_msg = None

    # ---- COM（ctypes で ITaskbarList3 を直接呼ぶ）
    def _method(self, index, *argtypes):
        vtbl = ctypes.cast(ctypes.cast(self.tb, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])

    def _create(self):
        ole32 = ctypes.OleDLL("ole32")
        try:
            ole32.CoInitialize(None)  # Qt がすでに準備していれば何もしない
        except OSError:
            pass
        clsid = _guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")  # TaskbarList
        iid = _guid("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")    # ITaskbarList3
        ptr = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr))  # 1 = CLSCTX_INPROC_SERVER
        self.tb = ptr
        self._method(3)(self.tb)  # HrInit

    def _icon(self, name):
        if name not in self.icons:
            side = 16
            try:
                side = max(16, _user32.GetSystemMetrics(49))  # SM_CXSMICON（画面の拡大率を反映した小さいアイコンの大きさ）
            except (OSError, AttributeError):
                pass
            color = "#8a8a8a" if name == "game_off" else "#ffffff"
            self.icons[name] = hicon_from_image(taskbar_glyph(name, color, side))
        return self.icons[name]

    def buttons(self):
        """今の状態に合わせたボタンの中身（名前, アイコン名, 説明, 押せるか）"""
        st = self.state
        playing, game, has = st["playing"], st["game"], st["has_track"]
        return [
            ("restart", "restart", "曲の先頭から再生", has),
            ("play", "pause" if playing else "play", "一時停止" if playing else "再生", True),
            ("next", "next", "次の曲", has),
            ("game", "game_on" if game else "game_off",
             "ゲームモード：ON（クリックで OFF）" if game else "ゲームモード：OFF（クリックで ON）", True),
        ]

    def _array(self):
        items = self.buttons()
        arr = (THUMBBUTTON * len(items))()
        for i, (name, icon, tip, enabled) in enumerate(items):
            b = arr[i]
            b.dwMask = THB_ICON | THB_TOOLTIP | THB_FLAGS
            b.iId = TASKBAR_BUTTON_BASE_ID + TASKBAR_BUTTONS.index(name)
            b.hIcon = self._icon(icon)
            b.szTip = tip
            b.dwFlags = THBF_ENABLED if enabled else THBF_DISABLED
        return arr

    def on_taskbar_created(self):
        """タスクバーのボタンができた（エクスプローラーの再起動後にも来る）ので、ボタンを登録し直す"""
        if not IS_WIN:
            return
        try:
            self._create()
            arr = self._array()
            hwnd = wintypes.HWND(int(self.widget.winId()))
            hr = self._method(15, wintypes.HWND, ctypes.c_uint, ctypes.POINTER(THUMBBUTTON))(
                self.tb, hwnd, len(arr), arr)  # ThumbBarAddButtons
            self.added = hr >= 0
        except (OSError, AttributeError, ValueError):
            self.added = False

    def update(self, **state):
        self.state.update(state)
        if not (IS_WIN and self.added and self.tb):
            return
        try:
            arr = self._array()
            hwnd = wintypes.HWND(int(self.widget.winId()))
            self._method(16, wintypes.HWND, ctypes.c_uint, ctypes.POINTER(THUMBBUTTON))(
                self.tb, hwnd, len(arr), arr)  # ThumbBarUpdateButtons
        except (OSError, AttributeError, ValueError):
            pass

    @staticmethod
    def clicked_button(msg_id, wparam):
        """WM_COMMAND がこのボタンの押下なら、ボタンの名前を返す"""
        if msg_id != WM_COMMAND or (wparam >> 16) & 0xFFFF != THBN_CLICKED:
            return None
        i = (wparam & 0xFFFF) - TASKBAR_BUTTON_BASE_ID
        return TASKBAR_BUTTONS[i] if 0 <= i < len(TASKBAR_BUTTONS) else None


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    h = text.strip("{}").replace("-", "")
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16)
    for i in range(8):
        g.Data4[i] = int(h[16 + i * 2:18 + i * 2], 16)
    return g


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", ctypes.c_int), ("xHotspot", ctypes.c_uint32), ("yHotspot", ctypes.c_uint32),
                ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]


def hicon_from_image(img):
    """QImage → Windows のアイコン（HICON）。透明部分もそのまま使う"""
    gdi32 = ctypes.WinDLL("gdi32")
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.c_void_p, ctypes.c_uint32]
    gdi32.CreateBitmap.restype = ctypes.c_void_p
    gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    _user32.CreateIconIndirect.restype = ctypes.c_void_p
    _user32.CreateIconIndirect.argtypes = [ctypes.POINTER(_ICONINFO)]
    img = img.convertToFormat(QImage.Format_ARGB32)  # メモリ上は B,G,R,A の順＝Windows の 32 ビット画像と同じ
    w, h = img.width(), img.height()
    bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # 高さを負にすると上から下の順
    bits = ctypes.c_void_p()
    color = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    data = bytes(img.constBits())
    stride = img.bytesPerLine()
    for y in range(h):
        ctypes.memmove(bits.value + y * w * 4, data[y * stride:y * stride + w * 4], w * 4)
    mask_buf = (ctypes.c_ubyte * (((w + 15) // 16) * 2 * h))()  # すべて 0（色の画像の透明度を使う）
    mask = gdi32.CreateBitmap(w, h, 1, 1, mask_buf)
    hicon = _user32.CreateIconIndirect(ctypes.byref(_ICONINFO(1, 0, 0, mask, color)))
    gdi32.DeleteObject(color)
    gdi32.DeleteObject(mask)
    return hicon


# ---------------------------------------------------------------- 二重起動の防止（2 つ目は 1 つ目にファイルを渡して終わる）
INSTANCE_NAME = "LyricsPlayer-" + re.sub(r"[^0-9A-Za-z_-]", "_", os.environ.get("USERNAME", "user"))
_instance = {"mutex": None, "server": None}


def acquire_instance():
    """このユーザーで最初の起動なら True"""
    if not IS_WIN:
        return True
    h = _kernel32.CreateMutexW(None, False, INSTANCE_NAME)
    first = ctypes.get_last_error() != 183  # 183 = ERROR_ALREADY_EXISTS
    if first:
        _instance["mutex"] = h
    elif h:
        _kernel32.CloseHandle(h)
    return first


def release_instance():
    if _instance["server"] is not None:
        _instance["server"].close()
        _instance["server"] = None
    if _instance["mutex"]:
        _kernel32.CloseHandle(_instance["mutex"])
        _instance["mutex"] = None


def send_to_running(paths, wait_ms=6000):
    """起動中のアプリにファイルを渡す（起動直後でまだ受け付けていないときは少し待つ）"""
    if IS_WIN:
        _user32.AllowSetForegroundWindow(-1)  # 受け取った側が前面に出られるように
    data = json.dumps(paths).encode("utf-8")
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < wait_ms:
        s = QLocalSocket()
        s.connectToServer(INSTANCE_NAME)
        if s.waitForConnected(300):
            s.write(data)
            s.flush()
            s.waitForBytesWritten(1000)
            s.disconnectFromServer()
            if s.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                s.waitForDisconnected(1000)
            return True
        time.sleep(0.2)
    return False


def restart_app():
    """今のアプリを閉じて、もう一度起動する"""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, os.path.abspath(sys.argv[0])]
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # exe 版：新しいプロセスとして起動し直す
    release_instance()
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if IS_WIN else 0
    subprocess.Popen(cmd, env=env, creationflags=flags, close_fds=True)


# ---------------------------------------------------------------- アップデート
# GitHub の Releases に置いた最新の exe をダウンロードして、今の exe と入れ替える。
# exe は GitHub の自動ビルド（.github/workflows/release.yml）が main に変更が入るたびに作って置く
UPDATE_REPO = "kiring325/Lyriplayer"
RELEASES_URL = f"https://github.com/{UPDATE_REPO}/releases"
LATEST_API = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
UPDATE_ASSET = APP + ".exe"
CREATE_NO_WINDOW = 0x08000000  # 裏で動かすコマンドの黒い画面を出さない


def app_version():
    """exe に埋め込んだ版の情報 {"version", "commit", "date"}。無ければ {}（Python から起動したときなど）"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    try:
        return json.loads((base / "version.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def version_key(tag):
    """'v2026.11.18.5' → (2026, 11, 18, 5)。比べられる形にする（読めなければ空）"""
    return tuple(int(x) for x in re.findall(r"\d+", tag or ""))


class UpdateError(Exception):
    pass


def _http_get(url, timeout=30):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={"User-Agent": APP, "Accept": "application/vnd.github+json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("まだ公開されている版がありません。")
        raise UpdateError(f"GitHub につながりましたが、エラーになりました（{e.code}）。少したってから試してください。")
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"GitHub につながりませんでした。インターネットの接続を確かめてください。\n（{e}）")


def fetch_latest_release():
    """Releases の最新版 → {"tag", "name", "notes", "url", "size", "sha256", "page"}"""
    with _http_get(LATEST_API) as r:
        try:
            data = json.loads(r.read().decode("utf-8"))
        except ValueError:
            raise UpdateError("GitHub からの返事を読み取れませんでした。")
    asset = next((a for a in data.get("assets", []) if a.get("name") == UPDATE_ASSET), None)
    if asset is None:
        raise UpdateError(f"最新版（{data.get('tag_name', '')}）に {UPDATE_ASSET} が見つかりませんでした。")
    digest = asset.get("digest") or ""
    return {"tag": data.get("tag_name", ""), "name": data.get("name") or data.get("tag_name", ""),
            "notes": (data.get("body") or "").strip(), "url": asset.get("browser_download_url", ""),
            "size": int(asset.get("size") or 0), "sha256": digest[7:] if digest.startswith("sha256:") else "",
            "page": data.get("html_url") or RELEASES_URL}


def download_release(info, dest, progress=None):
    """exe をダウンロードして、大きさ（と、わかれば SHA-256）が合っているか確かめる"""
    import hashlib
    url = info["url"]
    if not url.startswith("https://github.com/"):
        raise UpdateError("ダウンロード先が GitHub ではないので中止しました。")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    h, got = hashlib.sha256(), 0
    with _http_get(url, timeout=60) as r, open(tmp, "wb") as f:
        total = info["size"] or int(r.headers.get("Content-Length") or 0)
        last = -1
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
            pct = got * 100 // total if total else 0
            if progress and pct != last:
                last = pct
                progress(f"ダウンロードしています… {pct}%")
    if info["size"] and got != info["size"]:
        os.remove(tmp)
        raise UpdateError("ダウンロードが途中で切れたようです。もう一度試してください。")
    if info["sha256"] and h.hexdigest() != info["sha256"].lower():
        os.remove(tmp)
        raise UpdateError("ダウンロードしたファイルが壊れているようです（中身の確認が合いません）。")
    os.replace(tmp, dest)
    return dest


def launch_swap(new_exe, cur_exe):
    """このアプリが終わるのを待ってから、新しい exe で置き換えて起動し直す（PowerShell を裏で動かす）"""
    q = lambda x: "'" + x.replace("'", "''") + "'"
    ps = f"""$ErrorActionPreference = "Continue"
Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue
$ok = $false
for ($i = 0; $i -lt 40 -and -not $ok; $i++) {{
    try {{ Copy-Item -LiteralPath {q(new_exe)} -Destination {q(cur_exe)} -Force -ErrorAction Stop; $ok = $true }}
    catch {{ Start-Sleep -Milliseconds 500 }}
}}
Start-Process -FilePath {q(cur_exe)}
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force
"""
    path = os.path.join(tempfile.gettempdir(), f"lyriplayer_update_{os.getpid()}.ps1")
    with open(path, "w", encoding="utf-8-sig") as f:  # PowerShell 5 が日本語のパスを正しく読めるよう BOM 付き
        f.write(ps)
    flags = (CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if IS_WIN else 0
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", path], creationflags=flags, close_fds=True)


class BackgroundTask(QThread):
    """時間のかかる処理を裏で動かす。progress(文字) で途中経過、finished_with(結果, エラー) で終わりを知らせる"""
    progress = Signal(str)
    finished_with = Signal(object, object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            res = self.fn(self.progress.emit)
            self.finished_with.emit(res, None)
        except UpdateError as e:
            self.finished_with.emit(None, str(e))
        except Exception as e:  # 想定外のエラーも画面に出す
            self.finished_with.emit(None, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- バックアップ
BACKUP_FORMAT = 1


def _settings_to_json(settings):
    out = {}
    for k in settings.allKeys():
        v = settings.value(k)
        if isinstance(v, (QByteArray, bytes, bytearray)):
            out[k] = {"__bytes__": base64.b64encode(bytes(v)).decode("ascii")}
        elif v is None or isinstance(v, (str, int, float, bool, list)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _settings_from_json(settings, data):
    settings.clear()
    for k, v in data.items():
        if isinstance(v, dict) and "__bytes__" in v:
            v = QByteArray(base64.b64decode(v["__bytes__"]))
        settings.setValue(k, v)
    settings.sync()


def write_backup(zip_path, db, ranges, settings):
    """歌詞・リスト（lyrics.db）、タイミング（ranges.json）、設定を 1 つの zip にまとめる"""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        db.backup(dst)  # 使用中でも壊れない形でコピー
        dst.close()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(
                {"app": "LyricsPlayer", "format": BACKUP_FORMAT, "created": time.strftime("%Y-%m-%d %H:%M:%S")},
                ensure_ascii=False, indent=1))
            z.write(tmp, "lyrics.db")
            z.writestr("ranges.json", json.dumps(ranges, ensure_ascii=False, indent=1))
            z.writestr("settings.json", json.dumps(_settings_to_json(settings), ensure_ascii=False, indent=1))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def read_backup(zip_path):
    """(lyrics.db の中身, ranges, settings)。このアプリのバックアップでなければ ValueError"""
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if not {"manifest.json", "lyrics.db"} <= names:
            raise ValueError("Lyriplayer（歌詞プレイヤー）のバックアップファイルではありません")
        man = json.loads(z.read("manifest.json").decode("utf-8"))
        if man.get("app") != "LyricsPlayer":
            raise ValueError("Lyriplayer（歌詞プレイヤー）のバックアップファイルではありません")
        db = z.read("lyrics.db")
        if not db.startswith(b"SQLite format 3"):
            raise ValueError("バックアップの中の歌詞データが壊れています")
        ranges = json.loads(z.read("ranges.json").decode("utf-8")) if "ranges.json" in names else {}
        st = json.loads(z.read("settings.json").decode("utf-8")) if "settings.json" in names else None
    return db, ranges, st


class LyricsStore:
    """アプリ内に保存した歌詞（SQLite）。曲ファイルのパスで引き、移動に備えて曲名＋アーティストでも引ける。"""

    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS lyrics ("
            " key TEXT PRIMARY KEY, path TEXT, title TEXT, artist TEXT,"
            " title_l TEXT, artist_l TEXT, text TEXT NOT NULL, updated REAL)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS lyrics_ta ON lyrics(title_l, artist_l)")
        self._init_meta()
        self._init_playlists()
        self._init_flags()
        self.db.commit()

    def get(self, path):
        r = self.db.execute("SELECT text FROM lyrics WHERE key=?", (path_key(path),)).fetchone()
        return r[0] if r else None

    def find_by_meta(self, title, artist):
        if not (title and artist):
            return None
        r = self.db.execute(
            "SELECT text FROM lyrics WHERE title_l=? AND artist_l=? ORDER BY updated DESC LIMIT 1",
            (title.strip().lower(), artist.strip().lower()),
        ).fetchone()
        return r[0] if r else None

    def put(self, path, title, artist, text):
        self.db.execute(
            "INSERT OR REPLACE INTO lyrics VALUES (?,?,?,?,?,?,?,?)",
            (path_key(path), os.path.abspath(path), title, artist,
             (title or "").strip().lower(), (artist or "").strip().lower(), text, time.time()),
        )
        self.db.commit()

    def delete(self, path):
        self.db.execute("DELETE FROM lyrics WHERE key=?", (path_key(path),))
        self.db.commit()

    def keys(self):
        return {r[0] for r in self.db.execute("SELECT key FROM lyrics")}

    # ---- インスト（歌のない曲）の印
    def _init_flags(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS instrumental (key TEXT PRIMARY KEY, path TEXT)")

    def is_instrumental(self, path):
        return self.db.execute("SELECT 1 FROM instrumental WHERE key=?", (path_key(path),)).fetchone() is not None

    def set_instrumental(self, path, on):
        if on:
            self.db.execute("INSERT OR REPLACE INTO instrumental VALUES (?,?)", (path_key(path), os.path.abspath(path)))
        else:
            self.db.execute("DELETE FROM instrumental WHERE key=?", (path_key(path),))
        self.db.commit()

    def instrumental_keys(self):
        return {r[0] for r in self.db.execute("SELECT key FROM instrumental")}

    # ---- 曲情報キャッシュ（アルバムシャッフル用。ファイルの更新日時が変わったら読み直す）
    META_VER = 2  # 読み取る項目を増やしたら上げる（古いキャッシュは読み直す）

    def _init_meta(self):
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS meta ("
            " key TEXT PRIMARY KEY, mtime REAL, album TEXT, albumartist TEXT, disc INTEGER, track INTEGER)"
        )
        # 並び替え用に増やした項目（前のバージョンのデータベースにも足す）
        have = {r[1] for r in self.db.execute("PRAGMA table_info(meta)")}
        for col, typ in (("year", "TEXT"), ("album_sort", "TEXT"), ("artist_sort", "TEXT"), ("ver", "INTEGER")):
            if col not in have:
                self.db.execute(f"ALTER TABLE meta ADD COLUMN {col} {typ}")
        self.db.commit()

    def track_info(self, path):
        k = path_key(path)
        try:
            mt = os.path.getmtime(path)
        except OSError:
            mt = -1
        r = self.db.execute("SELECT mtime, album, albumartist, disc, track, year, album_sort, artist_sort, ver"
                            " FROM meta WHERE key=?", (k,)).fetchone()
        if r and r[0] == mt and (r[8] or 0) >= self.META_VER:
            return {"album": r[1], "albumartist": r[2], "disc": r[3], "track": r[4],
                    "year": r[5] or "", "album_sort": r[6] or "", "artist_sort": r[7] or ""}
        info = read_album_info(path)
        self.db.execute("INSERT OR REPLACE INTO meta (key, mtime, album, albumartist, disc, track,"
                        " year, album_sort, artist_sort, ver) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (k, mt, info["album"], info["albumartist"], info["disc"], info["track"],
                         info["year"], info["album_sort"], info["artist_sort"], self.META_VER))
        self._meta_dirty = True
        return info

    def commit_meta(self):
        if getattr(self, "_meta_dirty", False):
            self.db.commit()
            self._meta_dirty = False

    # ---- 自分用のリスト
    def _init_playlists(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS playlists (id INTEGER PRIMARY KEY, name TEXT, created REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS playlist_items (playlist_id INTEGER, pos INTEGER, path TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS pl_items ON playlist_items(playlist_id, pos)")

    def playlists(self):
        return self.db.execute(
            "SELECT p.id, p.name, (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id=p.id)"
            " FROM playlists p ORDER BY p.created").fetchall()

    def playlist_name(self, pid):
        r = self.db.execute("SELECT name FROM playlists WHERE id=?", (pid,)).fetchone()
        return r[0] if r else None

    def create_playlist(self, name):
        cur = self.db.execute("INSERT INTO playlists(name, created) VALUES (?,?)", (name, time.time()))
        self.db.commit()
        return cur.lastrowid

    def rename_playlist(self, pid, name):
        self.db.execute("UPDATE playlists SET name=? WHERE id=?", (name, pid))
        self.db.commit()

    def delete_playlist(self, pid):
        self.db.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
        self.db.execute("DELETE FROM playlists WHERE id=?", (pid,))
        self.db.commit()

    def playlist_items(self, pid):
        return [r[0] for r in self.db.execute(
            "SELECT path FROM playlist_items WHERE playlist_id=? ORDER BY pos", (pid,))]

    def set_playlist_items(self, pid, paths):
        self.db.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
        self.db.executemany("INSERT INTO playlist_items VALUES (?,?,?)",
                            [(pid, i, os.path.abspath(p)) for i, p in enumerate(paths)])
        self.db.commit()

    def add_to_playlist(self, pid, paths):
        """末尾に追加（すでに入っている曲は追加しない）。追加した曲数を返す"""
        items = self.playlist_items(pid)
        have = {path_key(p) for p in items}
        new = []
        for p in paths:
            k = path_key(p)
            if k not in have:
                have.add(k)
                new.append(p)
        if new:
            self.set_playlist_items(pid, items + new)
        return len(new)


# ---------------------------------------------------------------- widgets
class ClickSlider(QSlider):
    """溝をクリックした位置へ直接ジャンプできるスライダー"""

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.maximum() > self.minimum():
            v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().x()), self.width())
            self.setValue(v)
            self.sliderMoved.emit(v)
        super().mousePressEvent(e)


EMPTY_HTML = """
<div align="center" style="color:#b0b0b0;">
<p style="font-size:28px;color:#ececec;__HEAD__"><b>この曲の歌詞がありません</b></p>
<p style="font-size:15px;">上の<b style="color:#ffffff;">［歌詞を編集］</b>（Ctrl+E）で貼り付けて保存すると、<br>
アプリ内に保存されて次回から自動で表示されます。</p>
<p style="font-size:15px;">曲と<b style="color:#ffffff;">同じ名前の .txt</b> を置いても表示されます。<br>
例：<span style="color:#ffffff;">夜の駅.flac</span> → <span style="color:#ffffff;">夜の駅.txt</span><br>
.txt をこのウィンドウにドロップして取り込むこともできます。</p>
</div>
"""

WELCOME_HTML = """
<div align="center" style="color:#b0b0b0;">
<p style="font-size:30px;color:#ececec;__HEAD__"><b>音楽フォルダを選んでください</b></p>
<p style="font-size:15px;">左上の［フォルダを追加］か、フォルダをこのウィンドウにドロップします。<br>
フォルダはいくつでも追加でき、サブフォルダの曲もまとめて一覧に入ります。次回からは自動で開きます。</p>
<p style="font-size:15px;">歌詞は［歌詞を編集］でアプリ内に保存するか、<br>
曲と<b style="color:#ffffff;">同じ名前の .txt</b>（例：夜の駅.flac → 夜の駅.txt）を置きます。</p>
</div>
"""


class LyricsView(QTextBrowser):
    READ_LINE = 0.40  # 画面の上から何割の位置を「今ここ」にするか

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setAcceptDrops(False)
        self.has_text = False
        self.font_px = 26
        self._hold = QElapsedTimer()
        self._hold_ms = 0
        self._bg_src = None    # ぼかし済みの小さい画像
        self._bg_cache = None  # ビューポートの大きさに合わせた完成品
        self._overlay = 165    # スモークの濃さ（0〜255）
        self._veil = VEIL_DARK  # スモークの色
        self.text_color = QColor(TEXT_DEFAULT)
        self.lyric_family = None
        self.instrumental = None  # インストの曲なら (曲名, アーティスト)
        self.intro_px = 0.0       # イントロの間に歌詞が下から上がってくる距離
        self.layout_rev = 0       # 配置が変わるたびに増やす（タイミング計算の作り直しの目印）
        self._press_pos = None
        self.viewport().setMouseTracking(True)
        sb = self.verticalScrollBar()
        sb.sliderPressed.connect(self.hold)
        sb.sliderMoved.connect(lambda _v: self.hold())

    # 手でスクロールしたら数秒だけ自動スクロールを止める
    hold_ms_default = 3500  # 設定画面で変えられる

    def hold(self, ms=None):
        self._hold.start()
        self._hold_ms = self.hold_ms_default if ms is None else ms

    def held(self):
        return self._hold.isValid() and self._hold.elapsed() < self._hold_ms

    def wheelEvent(self, e):
        self.hold()
        super().wheelEvent(e)

    # ---- 歌詞の行をクリック → その行が「今ここ」に来る時刻から再生
    lineClicked = Signal(float)  # 行の上端の位置（文書内の y）

    def line_rect_at(self, vpos):
        """画面上の位置にある歌詞の行（文字のある部分だけ）の範囲。文字の無いところなら None"""
        if not self.has_text:
            return None
        pt = QPointF(vpos.x() + self.horizontalScrollBar().value(), vpos.y() + self.verticalScrollBar().value())
        doc = self.document()
        lay = doc.documentLayout()
        pos = lay.hitTest(pt, Qt.HitTestAccuracy.FuzzyHit)
        if pos < 0:
            return None
        block = doc.findBlock(pos)
        if not block.text().strip():
            return None
        br = lay.blockBoundingRect(block)
        tl = block.layout()
        for i in range(tl.lineCount()):
            ln = tl.lineAt(i)
            r = ln.naturalTextRect().translated(br.topLeft())
            if ln.textLength() > 0 and r.adjusted(-8, -6, 8, 6).contains(pt):
                return r
        return None

    def mousePressEvent(self, e):
        self.hold()
        self._press_pos = e.position()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        pp = getattr(self, "_press_pos", None)
        if e.button() == Qt.LeftButton and pp is not None and (e.position() - pp).manhattanLength() < 5:
            r = self.line_rect_at(e.position())
            if r is not None and not self.textCursor().hasSelection():
                self.lineClicked.emit(r.top())
        self._press_pos = None

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        if not e.buttons():  # 文字の上では指カーソルにして、押せることが分かるようにする
            over = self.line_rect_at(e.position()) is not None
            self.viewport().setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)

    # ---- 背景（ぼかしたアルバムアート）
    def set_background(self, cover):
        """背景にするアルバムアート（元の画像）。スモークの強さに合わせてぼかし・暗さを決める"""
        self._cover = cover
        self._bg_cache = None
        if cover is None:
            self._bg_src = None
            color, self._overlay, self._veil = QColor(TEXT_DEFAULT), 165, VEIL_DARK
        else:
            self._bg_src = smoke_image(cover, SMOKE)
            # 文字色の判定は、表示と同じ強さのぼかし具合ではなく、全体の色の傾向で見る
            color, self._overlay, self._veil = analyze_background(make_blurred(cover), SMOKE)
        self.set_text_color(color)
        self.viewport().update()

    def set_text_color(self, color):
        self.text_color = QColor(color)
        if self.has_text:
            self._apply_char_format()

    def _apply_char_format(self):
        cf = QTextCharFormat()
        cf.setForeground(self.text_color)
        cur = QTextCursor(self.document())
        cur.select(QTextCursor.SelectionType.Document)
        cur.mergeCharFormat(cf)

    def _build_bg(self, size):
        w, h = size.width(), size.height()
        pm = QPixmap(w, h)
        pm.fill(QColor("#111111"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        src = self._bg_src
        scale = max(w / src.width(), h / src.height())
        sw, sh = src.width() * scale, src.height() * scale
        p.drawImage(QRectF((w - sw) / 2, (h - sh) / 2, sw, sh), src)
        # 文字が読めるよう暗くする＋上下をなじませる
        if self.instrumental:
            pass  # 文字を出さないので、スモークもかけずにアートをそのまま見せる
        elif self.has_text:
            p.fillRect(0, 0, w, h, QColor(*self._veil, self._overlay))
        else:
            # 「歌詞がありません」などの案内は明るい文字なので、暗めにして読めるようにする
            p.fillRect(0, 0, w, h, QColor(*VEIL_DARK, 150))
        # 上下の端を周りの UI になじませる（スモーク 0% のときは元画像のまま、何もかけない）
        k = min(1.0, SMOKE / 0.3)
        if k > 0:
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0.0, QColor(17, 17, 17, int(90 * k)))
            g.setColorAt(0.08, QColor(17, 17, 17, 0))
            g.setColorAt(0.92, QColor(17, 17, 17, 0))
            g.setColorAt(1.0, QColor(17, 17, 17, int(110 * k)))
            p.fillRect(0, 0, w, h, g)
        p.end()
        return pm

    def paintEvent(self, e):
        if self._bg_src is not None:
            vp = self.viewport()
            if self._bg_cache is None or self._bg_cache.size() != vp.size():
                self._bg_cache = self._build_bg(vp.size())
            p = QPainter(vp)
            p.drawPixmap(0, 0, self._bg_cache)
            p.end()
        super().paintEvent(e)
        # インストの曲は文字を出さず、アルバムアートだけを見せる

    def set_instrumental(self, title, artist):
        self.has_text = False
        self.instrumental = (title or "", artist or "")
        self._bg_cache = None
        self.setPlainText("")
        self.verticalScrollBar().setValue(0)
        self.viewport().update()

    def scrollContentsBy(self, dx, dy):
        # 既定は画面をずらしてコピーする（背景と「今ここ」の帯まで一緒に動く）ので、毎回描き直す
        self.viewport().update()

    # ---- 中身
    def set_message(self, html):
        self.has_text = False
        self.instrumental = None
        self._bg_cache = None  # 案内表示用の濃さに描き直す
        head = f"font-family:'{FONTS['min']}';" if "min" in FONTS else ""
        self.setHtml(html.replace("__HEAD__", head))
        self.update_padding()
        self.verticalScrollBar().setValue(0)

    def set_lyrics(self, text, font_px):
        self.font_px = font_px
        if not (text and text.strip()):
            self.set_message(EMPTY_HTML)
            return
        self.has_text = True
        self.instrumental = None
        self._bg_cache = None
        self.setPlainText(strip_edge_spacers(text))
        # 区切りの行（---）は文字を消して空白の行にする（高さは update_padding で画面に合わせる）
        block = self.document().begin()
        while block.isValid():
            if is_spacer(block.text()):
                c = QTextCursor(block)
                c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                c.removeSelectedText()
                block.setUserState(SPACER_STATE)
            else:
                block.setUserState(-1)
            block = block.next()
        f = QFont(self.font())
        if self.lyric_family:
            f.setFamilies([self.lyric_family] + self.font().families())
            f.setWeight(QFont.Weight.Bold)
        else:
            f.setWeight(QFont.Weight.Medium)
        f.setPixelSize(font_px)
        self.document().setDefaultFont(f)
        cur = QTextCursor(self.document())
        cur.select(QTextCursor.SelectionType.Document)
        bf = QTextBlockFormat()
        bf.setAlignment(Qt.AlignHCenter)
        bf.setLineHeight(165.0, 1)  # 1 = ProportionalHeight
        cur.mergeBlockFormat(bf)
        self._apply_char_format()
        self.update_padding()

    def update_padding(self):
        vh = self.viewport().height()
        fr = self.document().rootFrame()
        ff = fr.frameFormat()
        if self.has_text:
            # 上の余白 =「今ここ」までの 40% ＋ イントロ用（最初の行が画面の下に隠れるぶん）
            # スクロール 0 では歌詞は画面の外。イントロの間に上がってきて、
            # スクロール intro_px で最初の行がちょうど「今ここ」に来る。下の余白で最後の行も「今ここ」まで上がる
            self.intro_px = vh * (1 - self.READ_LINE) + self.font_px * 2
            ff.setTopMargin(vh * self.READ_LINE + self.intro_px)
            ff.setBottomMargin(vh * (1 - self.READ_LINE))
        else:
            ff.setTopMargin(vh * 0.22)
            ff.setBottomMargin(20)
        ff.setLeftMargin(36)
        ff.setRightMargin(36)
        fr.setFrameFormat(ff)
        if self.has_text:
            # 区切り：前の歌詞が「今ここ」にあるとき、次の歌詞が画面の下に隠れる高さ
            gap = vh * (1 - self.READ_LINE) + self.font_px
            block = self.document().begin()
            while block.isValid():
                if block.userState() == SPACER_STATE and abs(block.blockFormat().topMargin() - gap) > 0.5:
                    bf = QTextBlockFormat()
                    bf.setTopMargin(gap)
                    QTextCursor(block).mergeBlockFormat(bf)
                block = block.next()
        self.layout_rev += 1
        if self.has_text and vh > 0:
            # 一番下までスクロールしたとき、最後の行の上端がちょうど「今ここ」に来るよう下の余白を測って直す
            doc = self.document()
            last_top = doc.documentLayout().blockBoundingRect(doc.lastBlock()).top()
            err = (last_top - (doc.size().height() - vh)) - vh * self.READ_LINE
            ff.setBottomMargin(max(0.0, ff.bottomMargin() + err))
            fr.setFrameFormat(ff)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update_padding()

    # ---- タイミング計算用の位置（スクロール量。その位置が「今ここ」に来るときのスクロールバーの値）
    def _read_off(self):
        return self.viewport().height() * self.READ_LINE

    def spacer_spans(self):
        """区切りの空白 [(始まり, 高さ), ...]"""
        out = []
        if not self.has_text:
            return out
        lay = self.document().documentLayout()
        block = self.document().begin()
        while block.isValid():
            if block.userState() == SPACER_STATE:
                br = lay.blockBoundingRect(block)
                tm = block.blockFormat().topMargin()  # 空白の大部分は上の余白（blockBoundingRect に入らない）
                out.append((br.top() - tm - self._read_off(), br.height() + tm))
            block = block.next()
        return out

    def line_positions(self):
        """歌詞の行（空行と区切りを除く）の位置。順番は lyric_line_texts と同じ"""
        out = []
        if not self.has_text:
            return out
        lay = self.document().documentLayout()
        block = self.document().begin()
        while block.isValid():
            if block.userState() != SPACER_STATE and block.text().strip():
                br = lay.blockBoundingRect(block)
                tl = block.layout()
                top = br.top() + (tl.lineAt(0).naturalTextRect().top() if tl.lineCount() else 0)
                out.append(top - self._read_off())
            block = block.next()
        return out


class EditDialog(QDialog):
    """歌詞の作成・編集。保存先はアプリ内（音楽フォルダにはファイルを作らない）。"""

    def __init__(self, parent, title, text, embedded=None, instrumental=False):
        super().__init__(parent)
        self.setWindowTitle(f"歌詞を編集 — {title}")
        self.resize(660, 700)
        self.embedded = embedded
        lay = QVBoxLayout(self)
        self.instChk = QCheckBox("Instrumental（歌のない曲）— 歌詞を出さず、アルバムアートだけを表示します")
        self.instChk.setChecked(instrumental)
        lay.addWidget(self.instChk)
        info = QLabel("普通のテキストを貼り付けるだけでOKです（時刻は不要。LRC を貼っても時刻は自動で外します）。\n"
                      "「---」だけの行は区切りになり、そこに 1 画面ぶんの空白が入ります（1 番と 2 番の間など。"
                      "次の歌詞が早く見えないようにして、次の歌い出しの直前に下から上がってきます）。\n"
                      "保存した歌詞はアプリ内に入り、曲の隣の .txt より優先して表示されます。"
                      "空にして保存すると、アプリ内の歌詞を削除します。")
        info.setObjectName("dim")
        info.setWordWrap(True)
        lay.addWidget(info)

        tools = QHBoxLayout()
        openBtn = QPushButton("テキストファイルから読み込む…")
        openBtn.clicked.connect(self.load_file)
        tools.addWidget(openBtn)
        if embedded:
            embBtn = QPushButton("埋め込み歌詞を読み込む")
            embBtn.clicked.connect(lambda: self.edit.setPlainText(norm_newlines(self.embedded)))
            tools.addWidget(embBtn)
        spBtn = QPushButton("カーソルの位置に区切り（---）を入れる")
        spBtn.clicked.connect(self.insert_spacer)
        tools.addWidget(spBtn)
        tools.addStretch(1)
        lay.addLayout(tools)

        self.edit = QPlainTextEdit(text or "")
        f = self.edit.font()
        f.setPointSize(12)
        self.edit.setFont(f)
        lay.addWidget(self.edit, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("キャンセル")
        save = QPushButton("保存")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)
        QShortcut(QKeySequence("Ctrl+S"), self, self.accept)
        # インストにしたら歌詞欄は使わないので薄くする（入っている歌詞は消さずに残す）
        self._lyric_widgets = [self.edit, openBtn, spBtn, info] + ([embBtn] if embedded else [])
        self.instChk.toggled.connect(self._toggle_inst)
        self._toggle_inst(instrumental)

    def _toggle_inst(self, on):
        for w in self._lyric_widgets:
            w.setEnabled(not on)

    def instrumental(self):
        return self.instChk.isChecked()

    def insert_spacer(self):
        """カーソルのある行の前に「---」の行を入れる（行の途中なら、そこで改行して入れる）"""
        c = self.edit.textCursor()
        c.clearSelection()
        block_text = c.block().text()
        pos_in_block = c.positionInBlock()
        if is_spacer(block_text):
            return
        c.beginEditBlock()
        if pos_in_block == 0:
            c.insertText(SPACER_MARK + "\n")
        elif pos_in_block >= len(block_text):
            c.insertText("\n" + SPACER_MARK)
        else:
            c.insertText("\n" + SPACER_MARK + "\n")
        c.endEditBlock()
        self.edit.setTextCursor(c)
        self.edit.setFocus()

    def load_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "歌詞ファイルを選ぶ", "", "歌詞 (*.txt *.lrc);;すべて (*.*)")
        if p:
            try:
                self.edit.setPlainText(norm_newlines(read_text_file(p)))
            except OSError as e:
                QMessageBox.warning(self, APP, f"読み込めませんでした。\n{e}")

    def text(self):
        t = self.edit.toPlainText()
        if _TS.search(t):  # LRC が貼られたら時刻を外す
            t = strip_lrc(t)
        return t


class TrackDelegate(QStyledItemDelegate):
    """曲の一覧：曲名は左、アルバム名は右に寄せて小さめのグレーで表示する"""

    def paint(self, p, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name, album = opt.text, index.data(Qt.UserRole + 2) or ""
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, p, opt.widget)  # 背景（選択・ホバー）
        r = opt.rect.adjusted(10, 0, -10, 0)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        fg = index.data(Qt.ForegroundRole)
        name_col = QColor("#ffffff") if selected else (fg.color() if fg is not None else QColor("#ececec"))
        p.save()
        aw = 0
        if album:
            af = QFont(opt.font)
            af.setPixelSize(max(10, opt.font.pixelSize() - 2) if opt.font.pixelSize() > 0 else 12)
            afm = QFontMetrics(af)
            aw = min(afm.horizontalAdvance(album), int(r.width() * 0.45))
            p.setFont(af)
            p.setPen(QColor("#bbbbbb") if selected else QColor("#777777"))
            p.drawText(QRect(r.right() - aw, r.top(), aw, r.height()), Qt.AlignVCenter | Qt.AlignRight,
                       afm.elidedText(album, Qt.ElideMiddle, aw))
        p.setFont(opt.font)
        p.setPen(name_col)
        nw = r.width() - (aw + 14 if album else 0)
        p.drawText(QRect(r.left(), r.top(), nw, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(opt.font).elidedText(name, Qt.ElideRight, nw))
        p.restore()


class Timeline(QWidget):
    """曲全体の帯：歌う範囲（青）・再生位置（白）。クリック／ドラッグで移動。"""
    seekRequested = Signal(float)  # 秒

    PAD = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(64)
        self.setCursor(Qt.PointingHandCursor)
        self.dur = 0.0
        self.pos = 0.0
        self.start = None
        self.end = None

    def set_state(self, dur, pos, start, end):
        self.dur, self.pos, self.start, self.end = dur, pos, start, end
        self.update()

    def _x(self, sec):
        w = self.width() - self.PAD * 2
        return self.PAD + (w * sec / self.dur if self.dur > 0 else 0)

    def _sec(self, x):
        w = self.width() - self.PAD * 2
        return min(self.dur, max(0.0, (x - self.PAD) / w * self.dur)) if self.dur > 0 and w > 0 else 0.0

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#181818"))
        top, h = 14, 22
        x0, x1 = self._x(0), self._x(self.dur)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2a2a"))
        p.drawRect(QRectF(x0, top, x1 - x0, h))
        if self.dur > 0:
            s = self.start if self.start is not None else 0.0
            e = self.end if self.end is not None else self.dur
            p.setBrush(QColor(16, 126, 174, 200))  # #107EAE
            p.drawRect(QRectF(self._x(s), top, self._x(e) - self._x(s), h))
            # 目盛り（30 秒ごと）
            p.setPen(QColor("#666666"))
            f = p.font()
            f.setPointSize(8)
            p.setFont(f)
            step = 30 if self.dur <= 600 else 60
            t = 0
            while t <= self.dur:
                x = self._x(t)
                p.drawLine(QPointF(x, top + h + 2), QPointF(x, top + h + 6))
                p.drawText(QRectF(x - 30, top + h + 7, 60, 14), Qt.AlignHCenter, fmt(t))
                t += step
            # 再生位置
            x = self._x(self.pos)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawRect(QRectF(x - 1, top - 6, 2, h + 12))
            p.drawEllipse(QPointF(x, top - 6), 4, 4)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.dur > 0:
            self.seekRequested.emit(self._sec(e.position().x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self.dur > 0:
            self.seekRequested.emit(self._sec(e.position().x()))


class TimingDialog(QDialog):
    """曲を聴きながら、歌い出し・歌い終わり・フレーズを決める画面（開いたままでもメイン画面は動く）"""

    PREROLL = 3.0  # 「聴く」で何秒前から再生するか

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setModal(False)
        self.resize(720, 860)
        st = self.style()
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self.songLbl = QLabel("", objectName="title")
        lay.addWidget(self.songLbl)
        help_ = QLabel("曲を流しながらボタンを押して設定します。変更はすぐ保存され、メイン画面の歌詞の流れにも反映されます。",
                       objectName="dim")
        help_.setWordWrap(True)
        lay.addWidget(help_)

        # 再生まわり
        self.timeLbl = QLabel("0:00.0 / 0:00", objectName="bigtime")
        lay.addWidget(self.timeLbl)
        self.timeline = Timeline()
        self.timeline.seekRequested.connect(self.seek_to)
        lay.addWidget(self.timeline)
        tr = QHBoxLayout()
        self.playBtn = QPushButton()
        self.playBtn.setObjectName("play")
        self.playBtn.clicked.connect(self.main.toggle_play)
        tr.addStretch(1)
        for label, d in (("−5秒", -5), ("−1秒", -1)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, d=d: self.seek_by(d))
            tr.addWidget(b)
        tr.addWidget(self.playBtn)
        for label, d in (("＋1秒", 1), ("＋5秒", 5)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, d=d: self.seek_by(d))
            tr.addWidget(b)
        tr.addStretch(1)
        lay.addLayout(tr)

        # 歌い出し・歌い終わり
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(8)
        self.vals = {}
        for row, (name, label) in enumerate((("start", "歌い出し"), ("end", "歌い終わり"))):
            g.addWidget(QLabel(label, objectName="head"), row, 0)
            v = QLabel("", objectName="value")
            v.setMinimumWidth(110)
            self.vals[name] = v
            g.addWidget(v, row, 1)
            buttons = (
                ("今の位置にする", lambda _=False, n=name: self.set_here(n), "primary"),
                ("−0.5", lambda _=False, n=name: self.nudge(n, -0.5), None),
                ("＋0.5", lambda _=False, n=name: self.nudge(n, 0.5), None),
                ("▶ 聴いて確認", lambda _=False, n=name: self.listen(n), None),
                ("クリア", lambda _=False, n=name: self.clear(n), None),
            )
            for col, (txt, fn, obj) in enumerate(buttons, start=2):
                b = QPushButton(txt)
                if obj:
                    b.setObjectName(obj)
                b.clicked.connect(fn)
                g.addWidget(b, row, col)
        g.setColumnStretch(7, 1)
        lay.addLayout(g)

        # フレーズ（行ごとの歌い始め）
        lay.addWidget(QLabel("フレーズに合わせる（行ごとの歌い始め）", objectName="head"))
        pnote = QLabel("曲を流しながら、選んでいる行を歌い始めたところで Enter を押すと記録して次の行へ進みます。"
                       "印を付けた行はちょうどその時刻に「今ここ」に来ます（全部の行でなくても、合わせたい行だけでOK）。",
                       objectName="small")
        pnote.setWordWrap(True)
        lay.addWidget(pnote)
        self.lineList = QListWidget()
        self.lineList.setMinimumHeight(150)
        self.lineList.itemDoubleClicked.connect(lambda _it: self.listen_line())
        lay.addWidget(self.lineList, 1)
        pr = QHBoxLayout()
        markBtn = QPushButton("選んだ行の歌い始めをここに  Enter", objectName="primary")
        markBtn.clicked.connect(self.mark_here)
        undoBtn = QPushButton("1つ戻す  BS")
        undoBtn.clicked.connect(self.undo_mark)
        pr.addWidget(markBtn)
        pr.addWidget(undoBtn)
        for txt, d in (("−0.2", -0.2), ("＋0.2", 0.2)):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, d=d: self.nudge_mark(d))
            pr.addWidget(b)
        lListen = QPushButton("▶ 聴く")
        lListen.clicked.connect(self.listen_line)
        lClear = QPushButton("クリア")
        lClear.clicked.connect(self.clear_mark)
        lClearAll = QPushButton("全部クリア")
        lClearAll.clicked.connect(self.clear_all_marks)
        for b in (lListen, lClear, lClearAll):
            pr.addWidget(b)
        pr.addStretch(1)
        lay.addLayout(pr)
        self._undo = []  # 記録した行（1つ戻す用）

        self.msgLbl = QLabel("", objectName="small")
        lay.addWidget(self.msgLbl)
        keys = QLabel("キー：Space 再生/一時停止　← → 5秒　Shift+← → 1秒　S 歌い出し　E 歌い終わり　"
                      "Enter フレーズを記録　BackSpace 1つ戻す　↑ ↓ 行を選ぶ",
                      objectName="small")
        keys.setWordWrap(True)
        lay.addWidget(keys)

        bottom = QHBoxLayout()
        resetBtn = QPushButton("この曲の設定をすべてリセット")
        resetBtn.clicked.connect(self.reset_all)
        closeBtn = QPushButton("閉じる")
        closeBtn.clicked.connect(self.close)
        bottom.addWidget(resetBtn)
        bottom.addStretch(1)
        bottom.addWidget(closeBtn)
        lay.addLayout(bottom)

        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.NoFocus)
        self.lineList.setFocusPolicy(Qt.NoFocus)

        for key, fn in (
            (Qt.Key_Return, self.mark_here),
            (Qt.Key_Enter, self.mark_here),
            (Qt.Key_Backspace, self.undo_mark),
            (Qt.Key_Up, lambda: self.move_line(-1)),
            (Qt.Key_Down, lambda: self.move_line(1)),
            (Qt.Key_Space, self.main.toggle_play),
            (Qt.Key_Left, lambda: self.seek_by(-5)),
            (Qt.Key_Right, lambda: self.seek_by(5)),
            ("Shift+Left", lambda: self.seek_by(-1)),
            ("Shift+Right", lambda: self.seek_by(1)),
            ("S", lambda: self.set_here("start")),
            ("E", lambda: self.set_here("end")),
        ):
            QShortcut(QKeySequence(key), self, fn)

        self._icons = (st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
                       st.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self.update_live)

    # ---- 表示
    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def refresh(self):
        self._undo = []
        self.setWindowTitle(f"タイミング編集 — {self.main.titleLbl.text()}")
        self.songLbl.setText(self.main.titleLbl.text())
        self.msg("")
        self.lineList.setCurrentRow(-1)
        self.update_fields()
        # 最初の、まだ印の無い行を選んでおく
        marks = self._marks()
        texts = self._texts()
        first = next((i for i in range(len(texts)) if i not in marks), 0)
        if texts:
            self.lineList.setCurrentRow(first)

    def update_fields(self):
        r = self.main.timing_raw()
        self.vals["start"].setText(fmt_t(r["start"]) if "start" in r else "0:00.0（曲の頭）")
        self.vals["end"].setText(fmt_t(r["end"]) if "end" in r else "未設定（曲の最後）")
        # フレーズの一覧
        texts, marks = self._texts(), self._marks()
        row = self.lineList.currentRow()
        self.lineList.clear()
        if not texts:
            self.lineList.addItem("（この曲には歌詞がありません。先に［歌詞を編集］で歌詞を入れてください）")
            self.lineList.setEnabled(False)
        else:
            self.lineList.setEnabled(True)
            for i, s in enumerate(texts):
                t = marks.get(i)
                self.lineList.addItem(f"{fmt_t(t) if t is not None else '   ―   '}    {s}")
                if t is not None:
                    self.lineList.item(i).setForeground(QColor("#ffffff"))
                else:
                    self.lineList.item(i).setForeground(QColor("#8a8a8a"))
            if 0 <= row < len(texts):
                self.lineList.setCurrentRow(row)
        self.update_live()

    def update_live(self):
        pl = self.main.player
        dur, pos = pl.duration() / 1000, pl.position() / 1000
        self.timeLbl.setText(f"{fmt_t(pos)} / {fmt(dur)}")
        r = self.main.timing_raw()
        self.timeline.set_state(dur, pos, r.get("start"), r.get("end"))
        playing = pl.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.playBtn.setIcon(self._icons[1 if playing else 0])

    def msg(self, text):
        self.msgLbl.setText(text)

    # ---- 再生操作
    def now(self):
        return self.main.player.position() / 1000

    def seek_to(self, sec):
        self.main.player.setPosition(int(sec * 1000))
        self.update_live()

    def seek_by(self, d):
        dur = self.main.player.duration() / 1000
        self.seek_to(max(0.0, min(dur - 0.2, self.now() + d)))

    def play_from(self, sec):
        self.seek_to(max(0.0, sec))
        self.main.player.play()

    # ---- 歌い出し・歌い終わり
    def _store(self, name, value):
        r = self.main.timing_raw()
        if value is None:
            r.pop(name, None)
        else:
            dur = self.main.player.duration() / 1000
            value = max(0.0, min(value, dur)) if dur > 0 else max(0.0, value)
            other_s, other_e = r.get("start", 0.0), r.get("end")
            if name == "start" and other_e is not None and value >= other_e - 1:
                self.msg("歌い出しは歌い終わりより前にしてください")
                return False
            if name == "end" and value <= other_s + 1:
                self.msg("歌い終わりは歌い出しより後にしてください")
                return False
            r[name] = value
        self.main.set_timing(r)
        self.update_fields()
        return True

    def set_here(self, name):
        t = self.now()
        if self._store(name, t):
            self.msg(f"{'歌い出し' if name == 'start' else '歌い終わり'}を {fmt_t(t)} にしました")

    def nudge(self, name, d):
        r = self.main.timing_raw()
        base = r.get(name)
        if base is None:
            base = 0.0 if name == "start" else self.main.player.duration() / 1000
        self._store(name, base + d)

    def listen(self, name):
        r = self.main.timing_raw()
        t = r.get(name, 0.0 if name == "start" else self.main.player.duration() / 1000)
        self.play_from(t - self.PREROLL)

    def clear(self, name):
        self._store(name, None)

    # ---- フレーズ（行ごとの歌い始め）
    def _texts(self):
        m = self.main
        return lyric_line_texts(m.lyrics_text) if (m.view.has_text and m.lyrics_kind != "instrumental") else []

    def _marks(self):
        """{行番号: 秒}"""
        return dict(resolve_marks(self.main.timing_raw().get("marks"), self._texts()))

    def _save_marks(self, marks):
        texts = self._texts()
        r = self.main.timing_raw()
        r["marks"] = [[i, t, texts[i]] for i, t in sorted(marks.items()) if 0 <= i < len(texts)]
        self.main.set_timing(r)
        self.update_fields()

    def _row(self):
        row = self.lineList.currentRow()
        return row if 0 <= row < len(self._texts()) else None

    def move_line(self, d):
        n = len(self._texts())
        if n:
            row = self.lineList.currentRow()
            self.lineList.setCurrentRow(max(0, min(n - 1, (row if row >= 0 else 0) + d)))

    def mark_here(self):
        texts = self._texts()
        if not texts:
            self.msg("この曲には歌詞がありません")
            return
        row = self._row()
        if row is None:
            row = 0
        t = round(self.now(), 2)
        marks = self._marks()
        # 順番が合わなくなる印（前の行なのに後の時刻・後の行なのに前の時刻）は外す
        bad = [i for i, mt in marks.items() if (i < row and mt >= t - 0.05) or (i > row and mt <= t + 0.05)]
        for i in bad:
            del marks[i]
        marks[row] = t
        self._undo.append(row)
        self._save_marks(marks)
        note = f"（順番が合わない印 {len(bad)} 個を外しました）" if bad else ""
        self.msg(f"{row + 1} 行目「{texts[row][:20]}」を {fmt_t(t)} にしました{note}")
        if row + 1 < len(texts):
            self.lineList.setCurrentRow(row + 1)
        else:
            self.lineList.setCurrentRow(row)

    def undo_mark(self):
        marks = self._marks()
        while self._undo and self._undo[-1] not in marks:
            self._undo.pop()
        if self._undo:
            row = self._undo.pop()
        else:
            row = self._row()
            row = row - 1 if row else None
            if row is None or row not in marks:
                self.msg("戻す印がありません")
                return
        del marks[row]
        self._save_marks(marks)
        self.lineList.setCurrentRow(row)
        self.msg(f"{row + 1} 行目の印を外しました")

    def nudge_mark(self, d):
        row, marks = self._row(), self._marks()
        if row is None or row not in marks:
            self.msg("印の付いた行を選んでください")
            return
        t = round(max(0.0, marks[row] + d), 2)
        prev = max((mt for i, mt in marks.items() if i < row), default=None)
        nxt = min((mt for i, mt in marks.items() if i > row), default=None)
        if (prev is not None and t <= prev + 0.05) or (nxt is not None and t >= nxt - 0.05):
            self.msg("前後の行と順番が入れ替わるので、これ以上動かせません")
            return
        marks[row] = t
        self._save_marks(marks)
        self.lineList.setCurrentRow(row)
        self.msg(f"{row + 1} 行目を {fmt_t(t)} にしました")

    def listen_line(self):
        row = self._row()
        if row is None:
            self.msg("一覧から行を選んでください")
            return
        marks = self._marks()
        if row in marks:
            t = marks[row]
        else:
            pos = self.main.view.line_positions()
            t = self.main.scroll_model().time_of(pos[row]) if row < len(pos) else 0.0
        self.play_from(t - self.PREROLL)

    def clear_mark(self):
        row, marks = self._row(), self._marks()
        if row is None or row not in marks:
            return
        del marks[row]
        self._save_marks(marks)
        self.lineList.setCurrentRow(row)
        self.msg(f"{row + 1} 行目の印を外しました")

    def clear_all_marks(self):
        if not self._marks():
            return
        if QMessageBox.question(self, APP, "この曲のフレーズの印をすべて外しますか？") != QMessageBox.StandardButton.Yes:
            return
        self._undo = []
        self._save_marks({})
        self.msg("フレーズの印をすべて外しました")

    def reset_all(self):
        self._undo = []
        self.main.set_timing({})
        self.update_fields()
        self.msg("この曲のタイミング設定をリセットしました（曲全体に合わせて流れます）")


class Stepper(QWidget):
    """［−］ 値 ［＋］ の数値入力"""
    valueChanged = Signal(int)

    def __init__(self, lo, hi, step=1, suffix="", parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step, self.suffix, self._v = lo, hi, step, suffix, lo
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        minus, plus = QPushButton("−", objectName="step"), QPushButton("＋", objectName="step")
        self.lbl = QLabel("", objectName="value")
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setMinimumWidth(70)
        minus.clicked.connect(lambda: self.setValue(self._v - self.step))
        plus.clicked.connect(lambda: self.setValue(self._v + self.step))
        for w in (minus, self.lbl, plus):
            lay.addWidget(w)
        self._show()

    def _show(self):
        self.lbl.setText(f"{self._v}{self.suffix}")

    def value(self):
        return self._v

    def setValue(self, v):
        v = max(self.lo, min(self.hi, int(v)))
        if v != self._v:
            self._v = v
            self._show()
            self.valueChanged.emit(v)
        else:
            self._show()


class HotkeyEdit(QKeySequenceEdit):
    """キーの組み合わせを 1 つだけ受け付ける入力欄。
    入力中は、どのウィンドウでも効くキーを外しておく（押したキーがそちらに取られないように）"""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setMaximumSequenceLength(1)
        self.setClearButtonEnabled(False)

    def focusInEvent(self, e):
        self.main.pause_hotkeys(True)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.main.pause_hotkeys(False)


class SettingsDialog(QDialog):
    """設定画面。変更はその場で反映・保存される。"""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("設定")
        self.setModal(False)
        self.resize(580, 760)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:#181818; border:none; }"
                             " QScrollArea > QWidget > QWidget { background:#181818; }")
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(10)

        # ---- 表示
        lay.addWidget(QLabel("表示", objectName="head"))
        self.artChk = QCheckBox("歌詞の背景にアルバムアートを表示する（B キー）")
        self.artChk.toggled.connect(self.main.set_art_bg)
        lay.addWidget(self.artChk)

        row = QHBoxLayout()
        row.addWidget(QLabel("背景のスモーク"))
        self.smokeSlider = QSlider(Qt.Horizontal)
        self.smokeSlider.setRange(0, 100)
        self.smokeLbl = QLabel("", objectName="value")
        self.smokeLbl.setMinimumWidth(48)
        self.smokeSlider.valueChanged.connect(self._smoke_changed)
        self.smokeSlider.sliderReleased.connect(lambda: self.main.set_smoke(self.smokeSlider.value()))
        row.addWidget(self.smokeSlider, 1)
        row.addWidget(self.smokeLbl)
        lay.addLayout(row)
        note = QLabel("0% でアルバムアートをそのまま表示します（ぼかし・暗さなし）。大きくするほど強くぼかして暗く（明るく）します。文字の色はジャケットに合わせて自動で選びます。",
                      objectName="small")
        note.setWordWrap(True)
        lay.addWidget(note)

        row = QHBoxLayout()
        row.addWidget(QLabel("歌詞の文字の大きさ"))
        self.fontSpin = Stepper(14, 64, 2, " px")
        self.fontSpin.valueChanged.connect(self.main.set_font)
        row.addWidget(self.fontSpin)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("手でスクロールしたあと、自動スクロールに戻るまで"))
        self.holdSpin = Stepper(1, 30, 1, " 秒")
        self.holdSpin.valueChanged.connect(self.main.set_hold_seconds)
        row.addWidget(self.holdSpin)
        row.addStretch(1)
        lay.addLayout(row)

        # ---- 音量
        lay.addSpacing(6)
        lay.addWidget(QLabel("音量", objectName="head"))
        row = QHBoxLayout()
        row.addWidget(QLabel("ゲームモードの音量"))
        self.gameSlider = QSlider(Qt.Horizontal)
        self.gameSlider.setRange(0, 100)
        self.gameLbl = QLabel("", objectName="value")
        self.gameLbl.setMinimumWidth(48)
        self.gameSlider.valueChanged.connect(self._game_changed)
        row.addWidget(self.gameSlider, 1)
        row.addWidget(self.gameLbl)
        lay.addLayout(row)
        gnote = QLabel("下のバーのゲームモードを ON にすると、音量をこの大きさまで下げます。"
                       "普段の音量のほうが小さいときは、普段の音量のままです。", objectName="small")
        gnote.setWordWrap(True)
        lay.addWidget(gnote)

        row = QHBoxLayout()
        row.addWidget(QLabel("ゲームモードの ON／OFF のキー"))
        self.gameKeyEdit = HotkeyEdit(self.main)
        self.gameKeyEdit.setToolTip("ここをクリックしてから、使いたいキーの組み合わせを押してください")
        self.gameKeyEdit.editingFinished.connect(self._game_key_changed)
        row.addWidget(self.gameKeyEdit, 1)
        noneBtn = QPushButton("なしにする")
        noneBtn.clicked.connect(lambda: self._apply_game_key(""))
        resetBtn = QPushButton("元に戻す")
        resetBtn.setToolTip(f"初めの設定（{GAME_HOTKEY_DEFAULT}）に戻します")
        resetBtn.clicked.connect(lambda: self._apply_game_key(GAME_HOTKEY_DEFAULT))
        row.addWidget(noneBtn)
        row.addWidget(resetBtn)
        lay.addLayout(row)
        knote2 = QLabel("枠をクリックして、使いたいキー（例：Ctrl+Shift+G、F9）を押すと変わります。"
                        + ("ゲーム中などほかのウィンドウを使っているときも効きます。" if IS_WIN else "")
                        + "このウィンドウでは G キーでも切り替えられます。", objectName="small")
        knote2.setWordWrap(True)
        lay.addWidget(knote2)
        self.gameKeyMsg = QLabel("", objectName="small")
        self.gameKeyMsg.setWordWrap(True)
        self.gameKeyMsg.hide()
        lay.addWidget(self.gameKeyMsg)

        # ---- 再生
        lay.addSpacing(6)
        lay.addWidget(QLabel("再生", objectName="head"))
        self.resumeChk = QCheckBox("起動したとき、前回聴いていた曲の続きの位置から聴けるようにする")
        self.resumeChk.toggled.connect(self.main.set_resume)
        lay.addWidget(self.resumeChk)

        # ---- キー操作
        lay.addSpacing(6)
        lay.addWidget(QLabel("ほかのウィンドウを使っているときのキー操作", objectName="head"))
        self.mediaChk = QCheckBox("キーボードのメディアキー（▶⏸ ⏭ ⏮）で操作する")
        self.mediaChk.toggled.connect(lambda on: self.main.set_hotkeys(media=on))
        lay.addWidget(self.mediaChk)
        self.comboChk = QCheckBox("Ctrl+Alt のキーで操作する（ゲーム中なども使えます）")
        self.comboChk.toggled.connect(lambda on: self.main.set_hotkeys(combo=on))
        lay.addWidget(self.comboChk)
        knote = QLabel(COMBO_HELP + "\nほかのアプリ（ブラウザや別の音楽アプリ）で同じキーを使いたいときは、チェックを外してください。",
                       objectName="small")
        knote.setWordWrap(True)
        lay.addWidget(knote)
        self.hkStatus = QLabel("", objectName="small")
        self.hkStatus.setWordWrap(True)
        lay.addWidget(self.hkStatus)

        # ---- ライブラリ
        lay.addSpacing(6)
        lay.addWidget(QLabel("ライブラリのフォルダ", objectName="head"))
        info = QLabel("ここに入れたフォルダの曲がライブラリに並びます。フォルダの追加と読み直しは、"
                      "メイン画面の左上の［フォルダを追加］と再読込ボタンでできます。", objectName="small")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.folderList = QListWidget()
        self.folderList.setMinimumHeight(110)
        lay.addWidget(self.folderList, 1)
        row = QHBoxLayout()
        rmBtn = QPushButton("選んだフォルダを外す")
        rmBtn.clicked.connect(self.remove_folder)
        row.addWidget(rmBtn)
        row.addStretch(1)
        lay.addLayout(row)
        self.watchChk = QCheckBox("フォルダに曲やフォルダが追加・削除されたら、自動でライブラリに反映する")
        self.watchChk.toggled.connect(self.main.set_auto_watch)
        lay.addWidget(self.watchChk)

        # ---- アップデート
        lay.addSpacing(6)
        lay.addWidget(QLabel("アップデート", objectName="head"))
        self.verLbl = QLabel("", objectName="small")
        self.verLbl.setWordWrap(True)
        lay.addWidget(self.verLbl)
        row = QHBoxLayout()
        self.checkBtn = QPushButton("更新を確認")
        self.checkBtn.clicked.connect(lambda: self.main.check_update())
        self.updateBtn = QPushButton("アップデートする", objectName="primary")
        self.updateBtn.clicked.connect(self.main.run_update)
        pageBtn = QPushButton("公開ページを開く")
        pageBtn.setToolTip(RELEASES_URL)
        pageBtn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        row.addWidget(self.checkBtn)
        row.addWidget(self.updateBtn)
        row.addWidget(pageBtn)
        row.addStretch(1)
        lay.addLayout(row)
        self.updLbl = QLabel("", objectName="small")
        self.updLbl.setWordWrap(True)
        self.updLbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.updLbl)
        self.autoUpdChk = QCheckBox("起動したときに更新を確認する（新しい版があれば下の帯でお知らせします）")
        self.autoUpdChk.toggled.connect(lambda on: self.main.settings.setValue("update_auto_check", bool(on)))
        lay.addWidget(self.autoUpdChk)
        unote = QLabel("GitHub で公開している最新版をダウンロードして、今のアプリと入れ替えます。"
                       "終わると自動で起動し直します。", objectName="small")
        unote.setWordWrap(True)
        lay.addWidget(unote)

        # ---- データ
        lay.addSpacing(6)
        lay.addWidget(QLabel("データ", objectName="head"))
        row = QHBoxLayout()
        openBtn = QPushButton("歌詞・リスト・設定の保存場所を開く")
        openBtn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(app_data_dir())))
        row.addWidget(openBtn)
        licBtn = QPushButton("ライセンスと使用している素材…")
        licBtn.clicked.connect(self.show_licenses)
        row.addWidget(licBtn)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        expBtn = QPushButton("バックアップを書き出す…")
        impBtn = QPushButton("バックアップから戻す…")
        expBtn.clicked.connect(self.main.export_backup)
        impBtn.clicked.connect(self.main.import_backup)
        row.addWidget(expBtn)
        row.addWidget(impBtn)
        row.addStretch(1)
        lay.addLayout(row)
        bnote = QLabel("アプリ内に保存した歌詞・自分用のリスト・歌い出しなどのタイミング・設定を 1 つの zip ファイルにまとめます。"
                       "PC を買い替えたときなどに「戻す」で読み込めます（曲ファイルそのものは入りません）。", objectName="small")
        bnote.setWordWrap(True)
        lay.addWidget(bnote)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        closeBtn = QPushButton("閉じる")
        closeBtn.clicked.connect(self.close)
        bottom.addWidget(closeBtn)
        outer.addLayout(bottom)
        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.NoFocus)

    def hideEvent(self, e):
        self.gameKeyMsg.hide()
        if self.main._hk_paused:
            self.main.pause_hotkeys(False)
        super().hideEvent(e)

    def _game_key_changed(self):
        seq = self.gameKeyEdit.keySequence()
        self._apply_game_key(seq.toString(QKeySequence.PortableText) if not seq.isEmpty() else "")

    def _apply_game_key(self, text):
        err = self.main.set_game_hotkey(text)
        self.gameKeyMsg.setText(err or "")
        self.gameKeyMsg.setVisible(bool(err))
        self.gameKeyEdit.blockSignals(True)
        self.gameKeyEdit.setKeySequence(QKeySequence(self.main.game_key, QKeySequence.PortableText))
        self.gameKeyEdit.blockSignals(False)
        self.refresh_hotkey_status()

    def refresh_hotkey_status(self):
        m = self.main
        self.hkStatus.setText(m.hotkey_problem)
        self.hkStatus.setVisible(bool(m.hotkey_problem))

    def _game_changed(self, v):
        self.gameLbl.setText(f"{v}%")
        self.main.set_game_volume(v)

    def _smoke_changed(self, v):
        self.smokeLbl.setText(f"{v}%")
        if not self.smokeSlider.isSliderDown():  # クリックやキー操作のときはすぐ反映
            self.main.set_smoke(v)

    def refresh(self):
        m = self.main
        self.refresh_hotkey_status()
        self.refresh_update()
        self.gameKeyEdit.blockSignals(True)
        self.gameKeyEdit.setKeySequence(QKeySequence(m.game_key, QKeySequence.PortableText))
        self.gameKeyEdit.blockSignals(False)
        for w, val in ((self.artChk, m.art_bg), (self.watchChk, m.auto_watch), (self.resumeChk, m.resume),
                       (self.mediaChk, m.hk_media), (self.comboChk, m.hk_combo)):
            w.blockSignals(True)
            w.setChecked(val)
            w.blockSignals(False)
        for w, val in ((self.smokeSlider, round(SMOKE * 100)), (self.fontSpin, m.font_px), (self.gameSlider, m.game_volume),
                       (self.holdSpin, round(LyricsView.hold_ms_default / 1000))):
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)
        self.smokeLbl.setText(f"{self.smokeSlider.value()}%")
        self.gameLbl.setText(f"{self.gameSlider.value()}%")
        self.folderList.clear()
        for f in m.folders:
            self.folderList.addItem(f)

    def show_licenses(self):
        """THIRD_PARTY_NOTICES.md（exe の中、またはソースのフォルダ）を表示する"""
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        try:
            text = (base / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        except OSError:
            text = "ライセンスの説明が見つかりませんでした。\n\nhttps://github.com/kiring325/Lyriplayer を見てください。"
        dlg = QDialog(self)
        dlg.setWindowTitle("ライセンスと使用している素材")
        dlg.resize(760, 640)
        v = QVBoxLayout(dlg)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setStyleSheet("QTextBrowser { background:#181818; padding:8px; }")
        tb.setMarkdown(text)
        # リンクの色を、暗い背景でも読みやすい明るい青にする（アプリ全体の配色が優先されるので、文字に直接付ける）
        doc = tb.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isAnchor():
                    c = QTextCursor(doc)
                    c.setPosition(frag.position())
                    c.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                    f = QTextCharFormat()
                    f.setForeground(QColor("#7cc4f0"))
                    c.mergeCharFormat(f)
                it += 1
            block = block.next()
        v.addWidget(tb, 1)
        close = QPushButton("閉じる")
        close.clicked.connect(dlg.accept)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(close)
        v.addLayout(h)
        dlg.exec()

    def refresh_update(self):
        m = self.main
        v = app_version()
        if not getattr(sys, "frozen", False):
            self.verLbl.setText("今のバージョン：Python から起動しています（アップデートは exe 版で使えます）")
        elif v.get("version"):
            self.verLbl.setText(f"今のバージョン：{v['version']}（{v.get('date', '')}）")
        else:
            self.verLbl.setText("今のバージョン：不明（自分で作った exe です。アップデートすると公開版に置き換わります）")
        busy = m._update_task is not None
        info = m.update_info
        self.checkBtn.setEnabled(not busy)
        self.updateBtn.setEnabled(not busy and bool(info and info.get("newer")) and getattr(sys, "frozen", False))
        self.updLbl.setText(m.update_msg)
        self.autoUpdChk.blockSignals(True)
        self.autoUpdChk.setChecked(m.settings.value("update_auto_check", True, type=bool))
        self.autoUpdChk.blockSignals(False)

    def remove_folder(self):
        it = self.folderList.currentItem()
        if it:
            self.main.remove_folder(it.text())
            self.refresh()


# ---------------------------------------------------------------- main window
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setAcceptDrops(True)
        self.settings = QSettings()
        self.ranges = self._load_ranges()
        self.store = LyricsStore(os.path.join(app_data_dir(), "lyrics.db"))
        # 再生中の並び（再生キュー）と、左の一覧に表示中の並びは別に持つ
        self.tracks = []        # 再生キュー（再生を始めた一覧のコピー）
        self.ctx_source = None  # 再生キューの出どころ：None=ライブラリ / 数値=リストID
        self.order = []         # 再生順（tracks のインデックス）
        self.opos = 0           # order の中の今の位置
        self.lib_tracks = []    # フォルダ内の全曲
        self.view_tracks = []   # 左の一覧に表示中の曲（並び替え後）
        self.view_orig = []     # view_tracks の各曲が、元の並び（フォルダ順／リストの順）で何番目か
        self.list_sort = self.settings.value("list_sort", "", type=str) or ""
        if self.list_sort not in dict(LIST_SORTS):
            self.list_sort = ""
        self.view_source = None
        self.folder = ""        # 最初のフォルダ（ダイアログの開始位置などに使う）
        self.folders = []       # ライブラリに入れているフォルダ（複数可）
        self.cur = -1
        self.cur_meta = ("", "")
        self.lyrics_text = None
        self.lyrics_file = None
        self.lyrics_kind = None  # "app" / "file" / "meta" / "embedded" / None
        self.cover = None
        self.timingDlg = None
        self.scroll_f = 0.0
        self.seeking = False
        self.repeat = self.settings.value("repeat", "all")
        self.shuffle = self.settings.value("shuffle_mode", "")
        if self.shuffle not in SHUFFLE_MODES:  # 旧バージョンの ON/OFF 設定から引き継ぐ
            self.shuffle = "all" if self.settings.value("shuffle", False, type=bool) else "off"
        self.autoscroll = self.settings.value("autoscroll", True, type=bool)
        self.art_bg = self.settings.value("art_bg", True, type=bool)
        self.font_px = self.settings.value("font_px", 26, type=int)
        self.auto_watch = self.settings.value("auto_watch", True, type=bool)
        self.game_mode = self.settings.value("game_mode", False, type=bool)
        self.game_volume = self.settings.value("game_volume", 30, type=int)
        self.muted = False
        self.resume = self.settings.value("resume", True, type=bool)
        self._resume = None      # (曲のパス, 位置ms)：読み込みが終わったらその位置へ
        self.hk_media = self.settings.value("hotkey_media", True, type=bool)
        self.hk_combo = self.settings.value("hotkey_combo", True, type=bool)
        self.game_key = self.settings.value("game_hotkey", GAME_HOTKEY_DEFAULT, type=str) or ""
        if parse_hotkey(self.game_key)[2]:
            self.game_key = GAME_HOTKEY_DEFAULT  # 読めない値が入っていたら初めの設定に戻す
        self._hk_paused = False  # 設定画面でキーを入力しているあいだは止める
        try:
            self.album_played = set(json.loads(self.settings.value("album_played", "[]", type=str) or "[]"))
        except (ValueError, TypeError):
            self.album_played = set()
        self._queue_album_keys = None
        self._last_album = None  # 直前に再生したアルバム（同じアルバムの続きの曲かどうかの判定用）
        self.taskbar = TaskbarButtons(self)  # タスクバーの小さな画面の下のボタン
        self.update_info = None    # 更新の確認の結果
        self.update_msg = ""       # 設定画面に出す、アップデートの状況
        self._update_task = None
        if self.settings.value("update_auto_check", True, type=bool):
            QTimer.singleShot(8000, lambda: self.check_update(silent=True))
        self._hotkeys = {}       # 登録できたキーの ID → 動作
        self.hotkey_problem = ""
        self._no_save = False    # バックアップから戻した直後は、閉じるときに設定を書き戻さない
        self._ext_paths = []     # 外から渡されたファイル（まとめて開く）
        self._ext_timer = QTimer(self, singleShot=True, interval=300)
        self._ext_timer.timeout.connect(self._open_external_now)
        self._apply_smoke(self.settings.value("smoke", round(SMOKE * 100), type=int))
        LyricsView.hold_ms_default = self.settings.value("hold_sec", 3.5, type=float) * 1000
        self.settingsDlg = None
        self.lib_dirs = []
        self.album_names = {}   # 一覧に出すアルバム名のキャッシュ

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)

        self._build_ui()
        self._connect()

        self.watcher = QFileSystemWatcher(self)
        self.reload_timer = QTimer(self, singleShot=True, interval=400)
        self.reload_timer.timeout.connect(lambda: self.load_lyrics(keep_scroll=True))
        self.watcher.fileChanged.connect(lambda _p: self.reload_timer.start())
        self.watcher.directoryChanged.connect(lambda _p: self.reload_timer.start())

        # ライブラリのフォルダを見張って、曲やフォルダが増えたら自動で読み直す
        self.libWatcher = QFileSystemWatcher(self)
        self.libTimer = QTimer(self, singleShot=True, interval=2000)
        self.libTimer.timeout.connect(self.auto_rescan)
        self.libWatcher.directoryChanged.connect(lambda _p: self.libTimer.start())

        self.timer = QTimer(self, interval=30)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        # 再生位置をときどき保存（強制終了しても、だいたいの続きから聴ける）
        self.posTimer = QTimer(self, interval=5000)
        self.posTimer.timeout.connect(self.save_position)
        self.posTimer.start()

        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1120, 740)

        self.reload_sources()
        last_view = self.settings.value("view_source", 0, type=int)  # ライブラリを読むと上書きされるので先に読む
        folders = self.settings.value("folders", [])
        if isinstance(folders, str):  # 1 件だけだと文字列で返ってくることがある
            folders = [folders]
        if not folders and self.settings.value("folder", ""):  # 旧バージョン（フォルダ 1 つ）から引き継ぐ
            folders = [self.settings.value("folder", "")]
        folders = [f for f in (folders or []) if f and os.path.isdir(f)]
        if folders:
            self.set_folders(folders, restore=True)
        else:
            self.view.set_message(WELCOME_HTML)
        if last_view and self.store.playlist_name(last_view) is not None:
            self.show_source(last_view)
        self.update_modes()

    # ---------------- UI
    def _build_ui(self):
        st = self.style()
        # 左：プレイリスト
        side = QWidget(objectName="side")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 8, 12)
        sl.setSpacing(8)
        srow = QHBoxLayout()
        srow.setSpacing(4)
        self.sourceBox = QComboBox()
        self.sourceBox.setToolTip("ライブラリ（フォルダ全体）か、自分用のリストを選びます")
        self.newListBtn = QPushButton("＋")
        self.newListBtn.setToolTip("新しいリストを作る")
        self.listMenuBtn = QPushButton("…")
        self.listMenuBtn.setToolTip("リストの名前変更・削除")
        srow.addWidget(self.sourceBox, 1)
        srow.addWidget(self.newListBtn)
        srow.addWidget(self.listMenuBtn)
        sl.addLayout(srow)
        self.folderLbl = QLabel("フォルダ未選択", objectName="dim")
        self.folderLbl.setWordWrap(True)
        sl.addWidget(self.folderLbl)
        row = QHBoxLayout()
        self.folderBtn = QPushButton("フォルダを追加", objectName="primary")
        self.folderBtn.setToolTip("音楽フォルダをライブラリに追加します（いくつでも追加できます）")
        self.rescanBtn = QPushButton("再読込")
        self.rescanBtn.setToolTip("再読み込み：フォルダの中身を読み直します（曲や歌詞を追加したとき）")
        self.rescanBtn.setText("")
        self.rescanBtn.setIcon(media_icon("reload", "#e4e4e4", 18))
        self.rescanBtn.setIconSize(QSize(18, 18))
        self.rescanBtn.setObjectName("mode")
        row.addWidget(self.folderBtn, 1)
        row.addWidget(self.rescanBtn)
        sl.addLayout(row)
        self.search = QLineEdit(placeholderText="曲名で絞り込み")
        self.search.setClearButtonEnabled(True)
        self.sortBox = QComboBox(objectName="sort")
        for val, label in LIST_SORTS:
            self.sortBox.addItem(label, val)
        self.sortBox.setToolTip("一覧の並び替え（表示だけで、リストに保存した曲順は変わりません）。\n"
                                "同じアルバムの曲はまとめて、トラック番号の順に並びます")
        self.sortBox.setCurrentIndex(max(0, self.sortBox.findData(self.list_sort)))
        sl.addWidget(self.search)
        sl.addWidget(self.sortBox)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setItemDelegate(TrackDelegate(self.list))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        sl.addWidget(self.list, 1)

        # 右：曲情報＋歌詞
        # 上の段：アルバムアート｜曲名・アーティスト名｜操作ボタン（2 段）／下：歌詞（横いっぱい）
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 14, 20, 10)
        rl.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(14)
        self.thumb_side = 76
        self.thumb = QLabel()
        self.thumb.setFixedSize(self.thumb_side, self.thumb_side)
        self.thumb.hide()
        head.addWidget(self.thumb, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.titleLbl = QLabel("曲が選択されていません", objectName="title")
        self.artistLbl = QLabel("", objectName="dim")
        self.srcLbl = QLabel("", objectName="small")
        for w in (self.titleLbl, self.artistLbl, self.srcLbl):
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            texts.addWidget(w)
        texts.addStretch(1)
        head.addLayout(texts, 1)

        self.autoBtn = QPushButton(checkable=True)
        self.autoBtn.setChecked(self.autoscroll)
        self.autoBtn.setToolTip("歌い出し〜歌い終わりに合わせて歌詞を一定の速さで流します（A キー）")
        self.timingBtn = QPushButton("タイミング編集")
        self.timingBtn.setToolTip("曲を聴きながら、歌い出し・歌い終わり・フレーズを設定します（Ctrl+T）")
        self.fontMinus = QPushButton("A−")
        self.fontPlus = QPushButton("A＋")
        self.editBtn = QPushButton("歌詞を編集")
        self.editBtn.setToolTip("歌詞を作成・編集してアプリ内または .txt に保存します（Ctrl+E）")
        # 操作ボタンは右端に 2 段：上［自動スクロール］［タイミング編集］／下［A−］［A＋］［歌詞を編集］
        btns = QGridLayout()
        btns.setHorizontalSpacing(6)
        btns.setVerticalSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.autoBtn, 1)
        top.addWidget(self.timingBtn, 1)
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        for w in (self.fontMinus, self.fontPlus, self.editBtn):
            bottom_row.addWidget(w)
        btns.addLayout(top, 0, 0)
        btns.addLayout(bottom_row, 1, 0)
        head.addLayout(btns)
        rl.addLayout(head)

        self.view = LyricsView()
        self.view.setObjectName("lyrics")
        self.view.lyric_family = load_lyric_font()
        if not self.view.lyric_family:
            QTimer.singleShot(500, lambda: self.statusBar().showMessage(
                f"歌詞フォント {LYRIC_FONT_FILE} が見つからないので標準フォントで表示します"
                "（スクリプトと同じ場所の fonts フォルダに置いてください）", 8000))
        rl.addWidget(self.view, 1)

        split = QSplitter()
        split.addWidget(side)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 820])
        split.setHandleWidth(1)

        # 下：操作バー
        bottom = QWidget(objectName="bottom")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(16, 8, 16, 8)
        bl.setSpacing(6)
        self.shufBtn = QPushButton("シャッフル", checkable=True)
        self.shufBtn.setToolTip("オフ → 全曲 → アルバム（アルバムの順番だけシャッフル）の順に切り替えます")
        self.prevBtn = QPushButton(icon=media_icon("prev", "#e4e4e4", 18))
        self.prevBtn.setToolTip("前の曲（Shift+←）。再生が 3 秒を過ぎていたら曲の頭に戻ります")
        self.playBtn = QPushButton(icon=st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), objectName="play")
        self.nextBtn = QPushButton(icon=media_icon("next", "#e4e4e4", 18))
        self.nextBtn.setToolTip("次の曲（Shift+→）")
        for b in (self.prevBtn, self.nextBtn):
            b.setObjectName("mode")
            b.setIconSize(QSize(18, 18))
        self.repBtn = QPushButton(checkable=True)
        # シャッフル・リピートは記号で表示（オン/オフは色、モードは横の短い文字）
        self.mode_icons = {name: (media_icon(name, "#ffffff"), media_icon(name, "#8a8a8a"))
                           for name in ("shuffle", "repeat")}
        for b in (self.shufBtn, self.repBtn):
            b.setIconSize(QSize(22, 22))
            b.setObjectName("mode")
        self.tCur = QLabel("0:00", objectName="time")
        self.seek = ClickSlider(Qt.Horizontal)
        self.tDur = QLabel("0:00", objectName="time")
        # ゲームモード：押すと音量を設定した大きさまで下げる
        self.gameBtn = QPushButton("ゲーム", checkable=True, objectName="mode")
        self.game_icons = (media_icon("game", "#ffffff", 22), media_icon("game", "#bdbdbd", 22))
        self.gameBtn.setText("")
        self.gameBtn.setIconSize(QSize(22, 22))
        # 音量アイコン：押すとミュート。ミュート中は音の波を消したスピーカーだけを表示
        self.volBtn = QPushButton("音量", objectName="iconbtn")
        self.vol_icons = (media_icon("volume", "#bdbdbd", 22), media_icon("volume_mute", "#bdbdbd", 22))
        self.volBtn.setText("")
        self.volBtn.setIconSize(QSize(22, 22))
        self.vol = ClickSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(120)
        for w in (self.shufBtn, self.prevBtn, self.playBtn, self.nextBtn, self.repBtn):
            bl.addWidget(w)
        bl.addSpacing(12)
        bl.addWidget(self.tCur)
        bl.addWidget(self.seek, 1)
        bl.addWidget(self.tDur)
        bl.addSpacing(12)
        bl.addWidget(self.gameBtn)
        bl.addSpacing(4)
        bl.addWidget(self.volBtn)
        bl.addWidget(self.vol)
        bl.addSpacing(8)
        self.settingsBtn = QPushButton(objectName="iconbtn")
        self.settingsBtn.setIcon(settings_icon(size=32))
        self.settingsBtn.setIconSize(QSize(32, 32))
        self.settingsBtn.setToolTip("設定（Ctrl+,）")
        bl.addWidget(self.settingsBtn)

        # Space などでボタンが押されないようにフォーカスを取らせない
        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.NoFocus)
        for s in (self.seek, self.vol):
            s.setFocusPolicy(Qt.NoFocus)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(split, 1)
        cl.addWidget(bottom)
        self.setCentralWidget(central)

        v = self.settings.value("volume", 80, type=int)
        self.vol.setValue(v)
        self.apply_volume()
        self.update_auto_btn()

    def _connect(self):
        self.folderBtn.clicked.connect(self.choose_folder)
        self.rescanBtn.clicked.connect(lambda: self.folders and self.set_folders(self.folders, keep_current=True))
        self.search.textChanged.connect(self.filter_list)
        self.sortBox.activated.connect(lambda i: self.set_list_sort(self.sortBox.itemData(i)))
        # ダブルクリック／Enter（両方つなぐと二重に再生されるので itemActivated だけ）
        self.list.itemActivated.connect(lambda it: self.play_from_view(self.list.row(it)))
        self.list.customContextMenuRequested.connect(self.list_menu)
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self.on_rows_moved))
        self.sourceBox.activated.connect(lambda i: self.show_source(self.sourceBox.itemData(i)))
        self.newListBtn.clicked.connect(lambda: self.new_playlist())
        self.listMenuBtn.clicked.connect(self.playlist_menu)
        QShortcut(QKeySequence(Qt.Key_Delete), self.list, self.remove_selected_from_playlist,
                  context=Qt.WidgetShortcut)

        self.autoBtn.toggled.connect(self.set_autoscroll)
        self.timingBtn.clicked.connect(self.open_timing)
        self.settingsBtn.clicked.connect(self.open_settings)
        self.fontMinus.clicked.connect(lambda: self.set_font(self.font_px - 2))
        self.fontPlus.clicked.connect(lambda: self.set_font(self.font_px + 2))
        self.editBtn.clicked.connect(lambda: self.edit_lyrics())

        self.playBtn.clicked.connect(self.toggle_play)
        self.prevBtn.clicked.connect(self.prev)
        self.nextBtn.clicked.connect(self.next)
        self.shufBtn.clicked.connect(self.cycle_shuffle)
        self.repBtn.clicked.connect(self.cycle_repeat)
        self.seek.sliderPressed.connect(lambda: setattr(self, "seeking", True))
        self.seek.sliderMoved.connect(lambda v: self.tCur.setText(fmt(v / 1000)))
        self.seek.sliderReleased.connect(self.seek_released)
        self.vol.valueChanged.connect(self.set_volume)
        self.gameBtn.clicked.connect(lambda on: self.set_game_mode(on))
        self.volBtn.clicked.connect(self.toggle_mute)

        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.playbackStateChanged.connect(self.on_state)
        self.player.mediaStatusChanged.connect(self.on_status)
        self.player.metaDataChanged.connect(self.on_metadata)
        self.view.lineClicked.connect(self.seek_to_line)
        self.player.errorOccurred.connect(lambda _e, msg: self.statusBar().showMessage("再生エラー：" + msg, 6000))

        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.seek_rel(-5000))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.seek_rel(5000))
        QShortcut(QKeySequence("Shift+Left"), self, self.prev)
        QShortcut(QKeySequence("Shift+Right"), self, self.next)
        QShortcut(QKeySequence("A"), self, self.autoBtn.toggle)
        QShortcut(QKeySequence("B"), self, lambda: self.set_art_bg(not self.art_bg))
        QShortcut(QKeySequence("M"), self, self.toggle_mute)
        QShortcut(QKeySequence("G"), self, lambda: self.set_game_mode(not self.game_mode))
        # 設定で決めたゲームモードのキー。Windows ではどのウィンドウでも効くキーとして登録し、
        # 登録できなかったとき（ほかのアプリが使用中など）や Windows 以外では、このウィンドウの中だけで効く
        self.gameKeyShortcut = QShortcut(QKeySequence(self.game_key, QKeySequence.PortableText), self,
                                         lambda: self.set_game_mode(not self.game_mode))
        QShortcut(QKeySequence("Ctrl+,"), self, self.open_settings)
        QShortcut(QKeySequence("Ctrl+O"), self, self.choose_folder)
        QShortcut(QKeySequence("Ctrl+F"), self, self.search.setFocus)
        QShortcut(QKeySequence("Ctrl+E"), self, self.edit_lyrics)
        QShortcut(QKeySequence("Ctrl+T"), self, self.open_timing)

    # ---------------- folder / list
    def _dialog_start(self):
        return self.folder or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MusicLocation)

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "ライブラリに追加する音楽フォルダ", self._dialog_start())
        if d:
            self.add_folder(d)

    def add_folder(self, d, play_path=None):
        d = os.path.abspath(d)
        if self.root_of(d) is not None:
            self.statusBar().showMessage("このフォルダはもうライブラリに入っています", 4000)
            if play_path:
                self.play_path_in_library(play_path)
            return
        first = not self.folders
        self.set_folders(self.folders + [d], keep_current=not first and not play_path, play_path=play_path)
        if not first:
            self.statusBar().showMessage(f"フォルダを追加しました（ライブラリ {len(self.lib_tracks)} 曲）", 5000)

    def remove_folder(self, d):
        rest = [f for f in self.folders if path_key(f) != path_key(d)]
        if rest:
            self.set_folders(rest, keep_current=True)
        else:
            self.folders, self.lib_tracks, self.folder, self.lib_dirs = [], [], "", []
            self.settings.setValue("folders", [])
            self.update_folder_label()
            self.update_lib_watch()
            self.show_source(self.view_source)

    def root_of(self, p):
        """p が入っているライブラリのフォルダ（なければ None）"""
        k = path_key(p)
        for f in self.folders:
            fk = path_key(f)
            if k == fk or k.startswith(fk.rstrip(os.sep) + os.sep):
                return f
        return None

    def play_path_in_library(self, p):
        keys = [path_key(x) for x in self.lib_tracks]
        if path_key(p) in keys:
            self.show_source(None)
            self.play_from_view(keys.index(path_key(p)))

    def update_folder_label(self):
        if not self.folders:
            self.folderLbl.setText("フォルダ未選択")
            self.folderLbl.setToolTip("")
        elif len(self.folders) == 1:
            self.folderLbl.setText(self.folders[0])
            self.folderLbl.setToolTip(self.folders[0])
        else:
            names = "、".join(os.path.basename(f.rstrip(os.sep)) or f for f in self.folders)
            self.folderLbl.setText(f"{len(self.folders)} フォルダ：{names}")
            self.folderLbl.setToolTip("\n".join(self.folders))

    def set_folders(self, folders, restore=False, play_path=None, keep_current=False):
        # 重複や、別のフォルダの中にあるフォルダはまとめる
        norm = []
        for f in sorted({os.path.abspath(f) for f in folders if os.path.isdir(f)}, key=lambda f: len(f)):
            fk = path_key(f)
            if not any(fk == path_key(n) or fk.startswith(path_key(n).rstrip(os.sep) + os.sep) for n in norm):
                norm.append(f)
        order = {path_key(os.path.abspath(f)): i for i, f in enumerate(folders)}
        norm.sort(key=lambda f: order.get(path_key(f), 0))
        cur_path = self.cur_path() if keep_current else None
        self.album_names = {}  # タグの書き換えやフォルダ数の変化に合わせて作り直す
        files, dirs = [], []
        for d in norm:
            fs, ds = scan_audio(d)
            files += fs
            dirs += ds
        self.lib_tracks, self.folders, self.lib_dirs = files, norm, dirs
        self.folder = norm[0] if norm else ""
        self.settings.setValue("folders", norm)
        self.update_folder_label()
        self.update_lib_watch()
        if self.settingsDlg is not None and self.settingsDlg.isVisible():
            self.settingsDlg.refresh()
        self.statusBar().showMessage(f"{len(files)} 曲を読み込みました", 4000)

        if keep_current:  # 再読込：再生は止めない
            self.show_source(self.view_source)
            if self.ctx_source is None and cur_path in files:
                self.tracks, self.cur = list(files), files.index(cur_path)
                self.rebuild_order(self.cur)
            self.highlight_current()
            self.load_lyrics(keep_scroll=True)
            return
        self.show_source(None)
        self.player.stop()
        self.cur = -1
        if not files:
            self.tracks, self.order = [], []
            self.set_cover(None)
            self.view.set_message(WELCOME_HTML.replace("音楽フォルダを選んでください", "このフォルダには音楽ファイルがありません"))
            return
        last = self.settings.value("last_track", "") if restore else None
        last_pos = self.settings.value("last_pos", 0, type=int) if restore else 0
        target = play_path or last
        idx = files.index(target) if target in files else 0
        self.start_queue(files, None, idx, autoplay=bool(play_path))
        if restore and not play_path and self.resume and last_pos > 3000 and files[idx] == last:
            self._resume = (last, last_pos)
            self.settings.setValue("last_pos", last_pos)  # play_index で 0 にしたのを戻す

    # ---------------- 一覧（ライブラリ / 自分用のリスト）
    def reload_sources(self):
        self.sourceBox.blockSignals(True)
        self.sourceBox.clear()
        self.sourceBox.addItem("ライブラリ（フォルダ全体）", 0)
        for pid, name, n in self.store.playlists():
            self.sourceBox.addItem(f"♪ {name}（{n}曲）", pid)
        self.sourceBox.setCurrentIndex(max(0, self.sourceBox.findData(self.view_source or 0)))
        self.sourceBox.blockSignals(False)
        self.listMenuBtn.setEnabled(bool(self.view_source))

    def show_source(self, src):
        src = src or None
        if src is not None and self.store.playlist_name(src) is None:
            src = None
        self.view_source = src
        base = self.lib_tracks if src is None else self.store.playlist_items(src)
        self.view_orig = self.sorted_order(base)
        self.view_tracks = [base[i] for i in self.view_orig]
        self.settings.setValue("view_source", src or 0)
        # リストの中だけドラッグで並べ替えできる（並び替えて表示しているときはできない）
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove if src and not self.list_sort
                                  else QAbstractItemView.DragDropMode.NoDragDrop)
        self.reload_sources()
        self.populate_list()

    def sorted_order(self, paths):
        """今の並び替えの設定での順番（paths のインデックスのリスト）"""
        if not self.list_sort:
            return list(range(len(paths)))
        busy = len(paths) > 300
        if busy:  # 初回だけ曲情報を読むので少し待つことがある
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            items = []
            for p in paths:
                info = self.store.track_info(p) if os.path.isfile(p) else {}
                album = info.get("album_sort") or self.album_label(p)
                artist = info.get("artist_sort") or info.get("albumartist") or ""
                items.append({"album": album, "artist": artist, "year": info.get("year") or "",
                              "disc": info.get("disc"), "track": info.get("track"),
                              "name": os.path.basename(p)})
            self.store.commit_meta()
        finally:
            if busy:
                QApplication.restoreOverrideCursor()
        return sort_track_order(items, self.list_sort)

    def set_list_sort(self, mode):
        self.list_sort = mode or ""
        self.settings.setValue("list_sort", self.list_sort)
        self.sortBox.setCurrentIndex(max(0, self.sortBox.findData(self.list_sort)))
        self.show_source(self.view_source)
        label = dict(LIST_SORTS).get(self.list_sort, "")
        if self.list_sort:
            self.statusBar().showMessage(f"{label}で並べました" + (
                "（リストに保存した曲順はそのままです。ドラッグで並べ替えるときは「並び：標準」に戻してください）"
                if self.view_source else ""), 5000)

    def album_label(self, p):
        """一覧の右側に出すアルバム名。タグが無ければフォルダ名。"""
        k = path_key(p)
        if k not in self.album_names:
            album = self.store.track_info(p)["album"] if os.path.isfile(p) else ""
            if not album:
                root = self.root_of(p)
                if root is None:
                    album = os.path.basename(os.path.dirname(p))
                else:
                    album = os.path.dirname(os.path.relpath(p, root))
                    if len(self.folders) > 1:  # フォルダが複数なら、どのフォルダの曲かも出す
                        rn = os.path.basename(root.rstrip(os.sep))
                        album = os.path.join(rn, album) if album else rn
            self.album_names[k] = album
        return self.album_names[k]

    def populate_list(self):
        self.list.clear()
        busy = len(self.view_tracks) > 300 and any(path_key(p) not in self.album_names for p in self.view_tracks)
        if busy:  # 初回だけ曲情報を読むので少し待つことがある
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for p in self.view_tracks:
                name = os.path.splitext(os.path.basename(p))[0]
                exists = os.path.isfile(p)
                it = QListWidgetItem(name + ("" if exists else "  ［見つかりません］"))
                it.setToolTip(p if exists else f"ファイルが見つかりません：{p}")
                it.setData(Qt.UserRole, p)
                it.setData(Qt.UserRole + 1, path_key(p))
                it.setData(Qt.UserRole + 2, self.album_label(p))
                self.list.addItem(it)
                if not exists:  # 見つからない曲だけ薄く（歌詞の有無で色は変えない）
                    it.setForeground(QColor("#666666"))
            self.store.commit_meta()
        finally:
            if busy:
                QApplication.restoreOverrideCursor()
        self.filter_list(self.search.text())
        self.highlight_current()

    def on_rows_moved(self):
        if not self.view_source:
            return
        if self.list_sort:
            return  # 並び替えて表示しているときはドラッグできない（念のため）
        self.view_tracks = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        self.view_orig = list(range(len(self.view_tracks)))
        self.store.set_playlist_items(self.view_source, self.view_tracks)
        self._sync_ctx(self.view_source)

    def _sync_ctx(self, pid):
        """再生中のリストが編集されたら、再生キューもそれに合わせる（今の曲は止めない）"""
        if self.ctx_source != pid:
            return
        cp = self.cur_path()
        new = self.store.playlist_items(pid)
        keys = [path_key(p) for p in new]
        if cp and path_key(cp) in keys:
            self.tracks, self.cur = new, keys.index(path_key(cp))
            self.rebuild_order(self.cur)

    def selected_rows(self):
        return sorted({self.list.row(it) for it in self.list.selectedItems()})

    def list_menu(self, pos):
        rows = self.selected_rows()
        if not rows:
            it = self.list.itemAt(pos)
            rows = [self.list.row(it)] if it else []
        if not rows:
            return
        paths = [self.view_tracks[r] for r in rows]
        m = QMenu(self)
        m.addAction("再生", lambda: self.play_from_view(rows[0]))
        self._fill_add_menu(m.addMenu(f"リストに追加（{len(paths)}曲）"), lambda: paths)
        self._fill_add_menu(m.addMenu("アルバムごとリストに追加"), lambda: self.album_tracks_of(paths))
        if self.view_source:
            m.addSeparator()
            m.addAction(f"このリストから外す（{len(paths)}曲）", self.remove_selected_from_playlist)
        m.exec(self.list.viewport().mapToGlobal(pos))

    def _fill_add_menu(self, menu, get_paths):
        for pid, name, _n in self.store.playlists():
            if pid != self.view_source:
                menu.addAction(name, lambda pid=pid: self.add_paths_to(pid, get_paths()))
        menu.addSeparator()
        menu.addAction("新しいリストを作って追加…", lambda: self.new_playlist(get_paths()))

    def album_tracks_of(self, paths):
        """選んだ曲と同じアルバムの曲を、ライブラリからアルバムの曲順で集める"""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            want = []
            for p in paths:
                k = album_key(p, self.store.track_info(p))
                if k not in want:
                    want.append(k)
            # 同じフォルダと、その兄弟フォルダ（Disc1 / Disc2 など）だけ調べる
            parents = {os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(p)))) for p in paths}
            pool = [p for p in self.lib_tracks
                    if os.path.normcase(os.path.dirname(os.path.dirname(p))) in parents] + list(paths)
            found, seen = [], set()
            for i, p in enumerate(pool):
                pk = path_key(p)
                if pk in seen:
                    continue
                info = self.store.track_info(p)
                k = album_key(p, info)
                if k in want:
                    seen.add(pk)
                    found.append((want.index(k), info["disc"] or 1,
                                  info["track"] if info["track"] is not None else 10 ** 6, i, p))
            self.store.commit_meta()
        finally:
            QApplication.restoreOverrideCursor()
        return [f[-1] for f in sorted(found)]

    def add_paths_to(self, pid, paths):
        n = self.store.add_to_playlist(pid, paths)
        dup = len(paths) - n
        name = self.store.playlist_name(pid)
        self.statusBar().showMessage(f"「{name}」に {n} 曲追加しました" + (f"（{dup} 曲はすでに入っていました）" if dup else ""), 5000)
        if self.view_source == pid:
            self.show_source(pid)
        else:
            self.reload_sources()
        self._sync_ctx(pid)

    def new_playlist(self, paths=None):
        n = len(self.store.playlists())
        name, ok = QInputDialog.getText(self, APP, "新しいリストの名前：", text=f"マイリスト {n + 1}")
        if not (ok and name.strip()):
            return
        pid = self.store.create_playlist(name.strip())
        if paths:
            self.add_paths_to(pid, paths)
        else:
            self.show_source(pid)
            self.statusBar().showMessage("ライブラリの曲を右クリック →「リストに追加」で曲を入れられます", 6000)

    def playlist_menu(self):
        if not self.view_source:
            return
        m = QMenu(self)
        m.addAction("フォルダの曲をこのリストに追加…", self.add_folder_to_playlist)
        m.addAction("曲ファイルを選んでこのリストに追加…", self.add_files_to_playlist)
        m.addSeparator()
        m.addAction("名前を変更…", self.rename_playlist)
        m.addAction("このリストを削除…", self.delete_playlist)
        m.exec(self.listMenuBtn.mapToGlobal(self.listMenuBtn.rect().bottomLeft()))

    def add_folder_to_playlist(self):
        """ライブラリに入れていないフォルダの曲も、リストに直接追加できる"""
        pid = self.view_source
        d = QFileDialog.getExistingDirectory(self, "このリストに曲を追加するフォルダ", self._dialog_start())
        if pid and d:
            files = scan_audio(d)[0]
            if files:
                self.add_paths_to(pid, files)
            else:
                self.statusBar().showMessage("音楽ファイルが見つかりませんでした", 4000)

    def add_files_to_playlist(self):
        pid = self.view_source
        exts = " ".join("*" + e for e in sorted(AUDIO_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "このリストに追加する曲", self._dialog_start(), f"音楽 ({exts})")
        if pid and files:
            self.add_paths_to(pid, [os.path.abspath(f) for f in files])

    def rename_playlist(self):
        pid = self.view_source
        name, ok = QInputDialog.getText(self, APP, "リストの名前：", text=self.store.playlist_name(pid) or "")
        if ok and name.strip():
            self.store.rename_playlist(pid, name.strip())
            self.reload_sources()

    def delete_playlist(self):
        pid = self.view_source
        name = self.store.playlist_name(pid)
        if QMessageBox.question(self, APP, f"リスト「{name}」を削除しますか？\n（曲のファイルは消えません）") != QMessageBox.Yes:
            return
        self.store.delete_playlist(pid)
        if self.ctx_source == pid:
            self.ctx_source = None  # 再生中の曲はそのまま続ける
        self.show_source(None)

    def remove_selected_from_playlist(self):
        pid = self.view_source
        rows = set(self.selected_rows())
        if not (pid and rows):
            return
        # 並び替えて表示していても、リストに保存した曲順の中から外す
        drop = {self.view_orig[r] for r in rows if r < len(self.view_orig)}
        self.store.set_playlist_items(pid, [p for i, p in enumerate(self.store.playlist_items(pid)) if i not in drop])
        self.show_source(pid)
        self._sync_ctx(pid)
        self.statusBar().showMessage(f"{len(rows)} 曲をリストから外しました", 4000)


    def filter_list(self, q):
        q = q.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            hay = it.text().lower() + " " + (it.data(Qt.UserRole + 2) or "").lower()  # アルバム名でも絞り込める
            it.setHidden(bool(q) and q not in hay)

    def highlight_current(self):
        cp = self.cur_path()
        k = path_key(cp) if cp else None
        hit = -1
        for i in range(self.list.count()):
            it = self.list.item(i)
            on = k is not None and it.data(Qt.UserRole + 1) == k
            if on and hit < 0:
                hit = i
            # 太字にすると字がつぶれるので、再生中の曲は背景を少し明るくして示す
            it.setBackground(QColor("#262626") if on else QColor(0, 0, 0, 0))
        if hit >= 0:
            self.list.setCurrentRow(hit)
            self.list.scrollToItem(self.list.item(hit))

    # ---------------- playback
    def cur_path(self):
        return self.tracks[self.cur] if 0 <= self.cur < len(self.tracks) else None

    def album_keys_for(self, paths):
        """(アルバムの見分け用キー, アルバム内の並び用キー) のリスト。曲情報はキャッシュから。"""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ak, sk = [], []
            for p in paths:
                info = self.store.track_info(p)
                ak.append(album_key(p, info))
                sk.append((info["disc"] or 1, info["track"] if info["track"] is not None else 10 ** 6))
            self.store.commit_meta()
        finally:
            QApplication.restoreOverrideCursor()
        return ak, sk

    def rebuild_order(self, start=None, finished=None):
        """今のシャッフル設定で再生順を作り直す（start の曲から続ける）。
        finished は一周して作り直すときの、最後に聴いた曲（そのアルバムを次の周回の最後に回す）"""
        ak = sk = None
        if self.shuffle == "album":
            ak, sk = self.album_keys_for(self.tracks)
        self._queue_album_keys = ak
        playable = [os.path.isfile(p) for p in self.tracks]
        played = None
        if ak is not None:
            # 1 周したら（start のアルバム以外がすべて再生済みなら）、新しい周回を始める
            keys = {ak[i] for i in range(len(ak)) if playable[i]}
            if start is not None and 0 <= start < len(ak):
                keys.discard(ak[start])
            if keys and keys <= self.album_played:
                keep = start if start is not None else finished
                self._reset_album_cycle(ak, keep=ak[keep] if keep is not None and 0 <= keep < len(ak) else None)
            played = self.album_played
        self.order = build_play_order(len(self.tracks), start, self.shuffle, ak, sk, playable,
                                      played_albums=played)
        self.opos = self.order.index(start) if start in self.order else 0
        if ak is not None and start is not None:
            self.mark_album_played(start)  # 今のアルバムも、この周回で再生したことにする

    # ---- アルバムシャッフル：1 度再生したアルバムは、ほかのアルバムを一通り再生するまで選ばない
    def _reset_album_cycle(self, ak, keep=None):
        self.album_played -= set(ak)
        if keep is not None:
            self.album_played.add(keep)
        self._save_album_played()

    def _save_album_played(self):
        self.settings.setValue("album_played", json.dumps(sorted(self.album_played), ensure_ascii=False))

    def mark_album_played(self, i):
        """アルバムシャッフル中に曲を再生したら、そのアルバムを「この周回で再生済み」にする"""
        ak = self._queue_album_keys
        if self.shuffle != "album" or ak is None or len(ak) != len(self.tracks) or not (0 <= i < len(ak)):
            return
        k = ak[i]
        if k == self._last_album:
            return  # 同じアルバムの続きの曲
        self._last_album = k
        if k in self.album_played:
            queue_keys = {ak[j] for j in range(len(ak)) if os.path.isfile(self.tracks[j])}
            if not queue_keys <= self.album_played:
                return  # 自分で選んで聴き直しただけ（周回の途中）
            # すべてのアルバムを一通り再生し終えて、次の周回の最初のアルバムに入った
            self._reset_album_cycle(ak)
        self.album_played.add(k)
        self._save_album_played()

    def start_queue(self, paths, source, start, autoplay=True):
        self.tracks, self.ctx_source = list(paths), source
        self.rebuild_order(start)
        self.play_index(start, autoplay)

    def play_from_view(self, row):
        if not (0 <= row < len(self.view_tracks)):
            return
        if not os.path.isfile(self.view_tracks[row]):
            self.statusBar().showMessage("この曲のファイルが見つかりません", 4000)
            return
        self.start_queue(self.view_tracks, self.view_source, row)

    def play_index(self, i, autoplay=True):
        if not (0 <= i < len(self.tracks)):
            return
        self.cur = i
        self.update_taskbar()
        self.mark_album_played(i)
        self._resume = None
        if i in self.order:
            self.opos = self.order.index(i)
        p = self.tracks[i]
        self.player.setSource(QUrl.fromLocalFile(p))
        title, artist = read_meta(p)
        self.cur_meta = (title, artist)
        self.titleLbl.setText(title)
        self.artistLbl.setText(artist or os.path.basename(p))
        self.setWindowTitle(f"{title} — {APP}")
        self.set_cover(load_cover(p))
        self.lyrics_text = None
        self.load_lyrics()
        self.update_range_label()
        self.highlight_current()
        self.settings.setValue("last_track", p)
        self.settings.setValue("last_pos", 0)
        if self.timingDlg is not None and self.timingDlg.isVisible():
            self.timingDlg.refresh()  # 曲が変わったら編集画面も切り替える
        if autoplay:
            self.player.play()

    def toggle_play(self):
        if self.cur < 0:
            if self.view_tracks:
                self.play_from_view(0)
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def next_index(self, manual):
        if not self.order:
            return None
        if self.opos + 1 < len(self.order):
            return self.order[self.opos + 1]
        if not (self.repeat == "all" or manual):
            return None
        if self.shuffle != "off":  # 一周したら新しい順番でシャッフルし直す
            last = self.cur
            self.rebuild_order(None, finished=last)
            if len(self.order) > 1 and self.order[0] == last:
                self.order.append(self.order.pop(0))
        return self.order[0]

    def next(self):
        n = self.next_index(True)
        if n is not None:
            self.play_index(n)

    def prev(self):
        if self.player.position() > 3000 or len(self.order) < 2:
            self.player.setPosition(0)
            return
        self.play_index(self.order[self.opos - 1] if self.opos > 0 else self.order[-1])

    def seek_rel(self, ms):
        d = self.player.duration()
        if d > 0:
            self.player.setPosition(max(0, min(d - 200, self.player.position() + ms)))

    def seek_released(self):
        self.player.setPosition(self.seek.value())
        self.seeking = False

    def set_volume(self, v):
        self.settings.setValue("volume", v)
        if self.muted:  # つまみを動かしたらミュートは解除
            self.muted = False
        self.apply_volume()

    def effective_volume(self):
        """実際に出す音量（0〜100）。ゲームモード中は設定した音量より大きくしない"""
        v = self.vol.value()
        if self.game_mode:
            v = min(v, self.game_volume)
        return v

    def apply_volume(self):
        self.audio.setVolume(self.effective_volume() / 100)
        self.audio.setMuted(self.muted)
        self.update_volume_buttons()

    def update_volume_buttons(self):
        self.gameBtn.setChecked(self.game_mode)
        self.update_taskbar()
        if self.game_icons:
            self.gameBtn.setIcon(self.game_icons[0 if self.game_mode else 1])
        self.gameBtn.setToolTip(
            f"ゲームモード：{'ON' if self.game_mode else 'OFF'}\n"
            f"ON のあいだは音量を {self.game_volume}% までに下げます（大きさは設定で変えられます）\n"
            f"切り替えのキー：G" + (f"、{hotkey_label(self.game_key)}" if self.game_key else "") + "（設定で変えられます）")
        if self.vol_icons:
            self.volBtn.setIcon(self.vol_icons[1 if self.muted else 0])
        else:
            self.volBtn.setText("ミュート中" if self.muted else "音量")
        self.volBtn.setToolTip(("ミュート中" if self.muted else f"音量 {self.effective_volume()}%") + "\nクリックでミュートの切り替え（M キー）")

    def toggle_mute(self):
        self.muted = not self.muted
        self.apply_volume()
        self.statusBar().showMessage("ミュートしました" if self.muted else "ミュートを解除しました", 2000)

    def set_game_mode(self, on):
        self.game_mode = bool(on)
        self.settings.setValue("game_mode", self.game_mode)
        self.apply_volume()
        self.statusBar().showMessage(
            f"ゲームモード ON：音量を {self.game_volume}% までに下げました" if self.game_mode else "ゲームモード OFF", 3000)

    def set_game_volume(self, v):
        self.game_volume = max(0, min(100, int(v)))
        self.settings.setValue("game_volume", self.game_volume)
        self.apply_volume()

    def on_position(self, ms):
        if not self.seeking:
            self.seek.setValue(ms)
            self.tCur.setText(fmt(ms / 1000))

    def on_duration(self, ms):
        self.seek.setRange(0, max(0, ms))
        self.tDur.setText(fmt(ms / 1000))
        self.update_range_label()

    def on_state(self, state):
        sp = QStyle.StandardPixmap.SP_MediaPause if state == QMediaPlayer.PlaybackState.PlayingState else QStyle.StandardPixmap.SP_MediaPlay
        self.playBtn.setIcon(self.style().standardIcon(sp))
        self.update_taskbar()

    def on_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.repeat == "one":
                self.player.setPosition(0)
                self.player.play()
            else:
                n = self.next_index(False)
                if n is not None:
                    self.play_index(n)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._resume = None
            self.statusBar().showMessage("このファイルは再生できませんでした", 6000)
        elif status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self._resume:
            p, pos = self._resume
            self._resume = None
            dur = self.player.duration()
            if p == self.cur_path() and (dur <= 0 or pos < dur - 5000):
                self.player.setPosition(pos)
                self.on_position(pos)
                self.statusBar().showMessage(f"前回の続き（{fmt(pos / 1000)}）から再生できます", 5000)

    def save_position(self):
        if self._no_save or self.cur < 0:
            return
        # 起動してまだ一度も再生していない（止まっている）ときは、前回の位置をそのまま残す
        if self._resume or self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            return
        self.settings.setValue("last_pos", int(self.player.position()))

    def set_resume(self, on):
        self.resume = bool(on)
        self.settings.setValue("resume", self.resume)

    # ---------------- どのウィンドウを使っていても効くキー
    def register_hotkeys(self):
        if not IS_WIN:
            return
        hwnd = int(self.winId())
        for hid in self._hotkeys:
            _user32.UnregisterHotKey(hwnd, hid)
        self._hotkeys = {}
        failed = []
        if self._hk_paused:
            return
        groups = (MEDIA_HOTKEYS if self.hk_media else ()) + (COMBO_HOTKEYS if self.hk_combo else ())
        gm, gvk, gerr = parse_hotkey(self.game_key)
        if gvk is not None and not gerr:
            groups += ((GAME_HOTKEY_ID, gm | MOD_NOREPEAT, gvk, "game", hotkey_label(self.game_key) + "（ゲームモード）"),)
        for hid, mods, vk, action, label in groups:
            if _user32.RegisterHotKey(hwnd, hid, mods, vk):
                self._hotkeys[hid] = action
            else:
                failed.append(label)
        self.hotkey_problem = ("次のキーはほかのアプリが使っているため使えません：" + "、".join(failed)) if failed else ""
        self._sync_settings()

    def pause_hotkeys(self, paused):
        """設定画面でキーを入力しているあいだ、どのウィンドウでも効くキーを一時的に外す"""
        self._hk_paused = bool(paused)
        self.register_hotkeys()

    def set_game_hotkey(self, text):
        """ゲームモードのキーを変える。使えないキーならその理由を返す（変更はしない）"""
        text = text or ""
        err = parse_hotkey(text)[2]
        if err:
            return err
        self.game_key = text
        self.settings.setValue("game_hotkey", text)
        self.gameKeyShortcut.setKey(QKeySequence(text, QKeySequence.PortableText))
        self.register_hotkeys()
        self.update_volume_buttons()
        return ""

    def set_hotkeys(self, media=None, combo=None):
        if media is not None:
            self.hk_media = bool(media)
            self.settings.setValue("hotkey_media", self.hk_media)
        if combo is not None:
            self.hk_combo = bool(combo)
            self.settings.setValue("hotkey_combo", self.hk_combo)
        self.register_hotkeys()

    def nativeEvent(self, eventType, message):
        if IS_WIN:
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam in self._hotkeys:
                    self.hotkey_action(self._hotkeys[msg.wParam])
                    return True, 0
                if self.taskbar.created_msg and msg.message == self.taskbar.created_msg:
                    self.taskbar.on_taskbar_created()
                    self.update_taskbar()
                else:
                    name = TaskbarButtons.clicked_button(msg.message, msg.wParam or 0)
                    if name:
                        self.taskbar_action(name)
                        return True, 0
            except (TypeError, ValueError):
                pass
        return super().nativeEvent(eventType, message)

    def taskbar_action(self, name):
        """タスクバーの小さな画面の下のボタンが押された"""
        if name == "restart":
            if self.cur >= 0:
                self.player.setPosition(0)
                self.player.play()
        elif name == "play":
            self.toggle_play()
        elif name == "next":
            self.next()
        elif name == "game":
            self.set_game_mode(not self.game_mode)

    def update_taskbar(self):
        if getattr(self, "player", None) is None:  # 起動の途中（まだ再生の準備前）
            return
        self.taskbar.update(playing=self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                            game=self.game_mode, has_track=self.cur >= 0)

    def hotkey_action(self, a):
        if a == "play":
            self.toggle_play()
        elif a == "next":
            self.next()
        elif a == "prev":
            self.prev()
        elif a == "stop":
            self.player.pause()
        elif a == "mute":
            self.toggle_mute()
        elif a == "game":
            self.set_game_mode(not self.game_mode)

    # ---------------- 外からファイルを渡されたとき（「プログラムから開く」・exe へのドロップ・2 つ目の起動）
    def open_external(self, paths):
        self.bring_to_front()
        if paths:
            self._ext_paths += [p for p in paths if p not in self._ext_paths]
            self._ext_timer.start()  # 複数のファイルを選んで開くと 1 つずつ届くので、少し待ってまとめる

    def _open_external_now(self):
        paths, self._ext_paths = self._ext_paths, []
        if paths:
            self.open_paths(paths, external=True)

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # ---------------- バックアップ
    def export_backup(self):
        name = f"Lyriplayer_バックアップ_{time.strftime('%Y%m%d')}.zip"
        start = os.path.join(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation), name)
        parent = self.settingsDlg if (self.settingsDlg and self.settingsDlg.isVisible()) else self
        path, _ = QFileDialog.getSaveFileName(parent, "バックアップを書き出す", start, "バックアップ (*.zip)")
        if not path:
            return
        self.save_position()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()
        try:
            write_backup(path, self.store.db, self.ranges, self.settings)
        except (OSError, sqlite3.Error) as e:
            QMessageBox.warning(parent, APP, f"書き出せませんでした。\n{e}")
            return
        n = len(self.store.keys())
        QMessageBox.information(parent, APP, f"バックアップを書き出しました（保存した歌詞 {n} 曲）。\n{path}")

    def import_backup(self):
        parent = self.settingsDlg if (self.settingsDlg and self.settingsDlg.isVisible()) else self
        start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _ = QFileDialog.getOpenFileName(parent, "バックアップから戻す", start, "バックアップ (*.zip)")
        if not path:
            return
        try:
            db_bytes, ranges, st = read_backup(path)
        except (OSError, ValueError, zipfile.BadZipFile, KeyError) as e:
            QMessageBox.warning(parent, APP, f"読み込めませんでした。\n{e}")
            return
        r = QMessageBox.question(
            parent, APP,
            "今の歌詞・リスト・タイミング・設定を、このバックアップの内容に置き換えます。\n"
            "（念のため、今のデータもデータの保存場所に自動でバックアップしておきます）\n\n"
            "置き換えたあと、アプリを再起動します。よろしいですか？")
        if r != QMessageBox.StandardButton.Yes:
            return
        # 今のデータを退避
        auto = os.path.join(app_data_dir(), f"戻す前の自動バックアップ_{time.strftime('%Y%m%d_%H%M%S')}.zip")
        try:
            self.save_position()
            self.settings.sync()
            write_backup(auto, self.store.db, self.ranges, self.settings)
        except (OSError, sqlite3.Error) as e:
            QMessageBox.warning(parent, APP, f"今のデータを退避できなかったため、中止しました。\n{e}")
            return
        # 置き換え
        self._no_save = True
        self.player.stop()
        for t in (self.timer, self.posTimer, self.libTimer, self.reload_timer):
            t.stop()
        db_path = os.path.join(app_data_dir(), "lyrics.db")
        try:
            self.store.db.close()
            with open(db_path, "wb") as f:
                f.write(db_bytes)
            with open(self._ranges_path(), "w", encoding="utf-8") as f:
                json.dump(ranges, f, ensure_ascii=False, indent=1)
            if st is not None:
                _settings_from_json(self.settings, st)
        except OSError as e:
            QMessageBox.critical(parent, APP, f"置き換えの途中で失敗しました。\n{e}\n\n"
                                              f"元のデータは次の場所にあります。\n{auto}")
        restart_app()
        QApplication.quit()

    def on_metadata(self):
        # mutagen で画像が取れなかったときの予備：再生エンジンが読んだ画像を使う
        if self.cover is not None:
            return
        md = self.player.metaData()
        for key in (QMediaMetaData.Key.CoverArtImage, QMediaMetaData.Key.ThumbnailImage):
            img = md.value(key)
            if isinstance(img, QImage) and not img.isNull():
                self.set_cover(img)
                return

    def set_shuffle_mode(self, mode, rebuild=True):
        self.shuffle = mode
        self.settings.setValue("shuffle_mode", mode)
        if rebuild and self.tracks:
            self.rebuild_order(self.cur if self.cur >= 0 else None)
        self.update_modes()

    def cycle_shuffle(self):
        i = SHUFFLE_MODES.index(self.shuffle)
        self.set_shuffle_mode(SHUFFLE_MODES[(i + 1) % len(SHUFFLE_MODES)])
        if self.shuffle == "album":
            self.statusBar().showMessage("アルバムシャッフル：今のアルバムを最後まで聴いたら、別のアルバムを最初の曲から再生します", 6000)

    def cycle_repeat(self):
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        self.settings.setValue("repeat", self.repeat)
        self.update_modes()

    def update_modes(self):
        rep_label = {"off": "リピート：オフ", "all": "リピート：全曲", "one": "リピート：1曲"}[self.repeat]
        shuf_on, rep_on = self.shuffle != "off", self.repeat != "off"
        self.shufBtn.setChecked(shuf_on)
        self.repBtn.setChecked(rep_on)
        if "shuffle" in self.mode_icons:
            self.shufBtn.setIcon(self.mode_icons["shuffle"][0 if shuf_on else 1])
            self.shufBtn.setText(" アルバム" if self.shuffle == "album" else "")
        else:
            self.shufBtn.setText(SHUFFLE_LABEL[self.shuffle])
        if "repeat" in self.mode_icons:
            self.repBtn.setIcon(self.mode_icons["repeat"][0 if rep_on else 1])
            self.repBtn.setText(" 1曲" if self.repeat == "one" else "")
        else:
            self.repBtn.setText(rep_label)
        self.shufBtn.setToolTip(SHUFFLE_LABEL[self.shuffle] + "\nクリックで オフ → 全曲 → アルバム（アルバムの順番だけシャッフル）")
        self.repBtn.setToolTip(rep_label + "\nクリックで 全曲 → 1曲 → オフ")

    # ---------------- album art
    def set_cover(self, img):
        self.cover = img
        if img is None:
            self.thumb.hide()
            self.view.set_background(None)
            return
        self.thumb.setPixmap(rounded_thumb(img, self.thumb_side))
        self.thumb.show()
        self.view.set_background(img if self.art_bg else None)

    def set_art_bg(self, on):
        self.art_bg = on
        self.settings.setValue("art_bg", on)
        self.view.set_background(self.cover if (on and self.cover is not None) else None)
        self._sync_settings()

    # ---------------- アップデート
    def _set_update_msg(self, text):
        self.update_msg = text
        if self.settingsDlg is not None and self.settingsDlg.isVisible():
            self.settingsDlg.refresh_update()

    def _start_update_task(self, work, done):
        task = BackgroundTask(work, self)
        task.progress.connect(self._set_update_msg)

        def finished(res, err):
            self._update_task = None
            done(res, err)
        task.finished_with.connect(finished)
        task.finished.connect(task.deleteLater)  # 片付けは、裏の処理が完全に終わってから
        self._update_task = task
        task.start()
        self._set_update_msg(self.update_msg)

    def check_update(self, silent=False):
        """GitHub に新しい版があるか確かめる（裏で動かす）"""
        if self._update_task is not None:
            return
        current = app_version().get("version", "")

        def work(progress):
            progress("GitHub で最新版を確かめています…")
            info = fetch_latest_release()
            info["current"] = current
            # 版の番号が分からない exe（自分で作ったもの）は、公開版があれば新しいものとして扱う
            info["newer"] = (not current) or version_key(info["tag"]) > version_key(current)
            return info

        self._start_update_task(work, lambda res, err: self._check_done(res, err, silent))

    def _check_done(self, res, err, silent):
        if err:
            self.update_info = None
            self._set_update_msg("" if silent else "更新を確認できませんでした：" + err)
            return
        self.update_info = res
        if res["newer"]:
            notes = "\n".join(ln for ln in res["notes"].splitlines() if ln.strip())[:600]
            self._set_update_msg(f"新しい版があります：{res['tag']}" + (f"\n{notes}" if notes else ""))
            if silent:
                self.statusBar().showMessage("Lyriplayer の新しい版があります — 設定 →「アップデート」から更新できます", 20000)
        else:
            self._set_update_msg(f"最新の版です（{res['tag']}）")

    def run_update(self):
        info = self.update_info
        if self._update_task is not None or not (info and info.get("newer")):
            return
        if not getattr(sys, "frozen", False):
            QDesktopServices.openUrl(QUrl(info.get("page") or RELEASES_URL))
            return
        parent = self.settingsDlg if (self.settingsDlg and self.settingsDlg.isVisible()) else self
        r = QMessageBox.question(parent, APP, f"最新版（{info['tag']}）をダウンロードして入れ替えます。\n"
                                              "終わると自動で起動し直します。よろしいですか？")
        if r != QMessageBox.StandardButton.Yes:
            return
        dest = os.path.join(tempfile.gettempdir(), "Lyriplayer_update", UPDATE_ASSET)
        self._start_update_task(lambda progress: download_release(info, dest, progress), self._update_done)

    def _update_done(self, exe, err):
        if err:
            self._set_update_msg("アップデートできませんでした：" + err)
            QMessageBox.warning(self, APP, "アップデートできませんでした。\n\n" + err)
            return
        self.update_info = None
        self._set_update_msg("ダウンロードできました。起動し直します…")
        launch_swap(exe, sys.executable)
        self.close()
        QApplication.quit()

    # ---------------- 設定
    def open_settings(self):
        if self.settingsDlg is None:
            self.settingsDlg = SettingsDialog(self)
        self.settingsDlg.refresh()
        self.settingsDlg.show()
        self.settingsDlg.raise_()
        self.settingsDlg.activateWindow()

    def _sync_settings(self):
        if self.settingsDlg is not None and self.settingsDlg.isVisible():
            self.settingsDlg.refresh()

    @staticmethod
    def _apply_smoke(pct):
        global SMOKE
        SMOKE = max(0, min(100, int(pct))) / 100

    def set_smoke(self, pct):
        if round(SMOKE * 100) == int(pct):
            return
        self._apply_smoke(pct)
        self.settings.setValue("smoke", int(pct))
        if self.art_bg and self.cover is not None:
            self.view.set_background(self.cover)

    def set_hold_seconds(self, sec):
        LyricsView.hold_ms_default = float(sec) * 1000
        self.settings.setValue("hold_sec", float(sec))

    def set_auto_watch(self, on):
        self.auto_watch = on
        self.settings.setValue("auto_watch", on)
        self.update_lib_watch()

    def update_lib_watch(self):
        old = self.libWatcher.directories()
        if old:
            self.libWatcher.removePaths(old)
        if self.auto_watch and self.lib_dirs:
            self.libWatcher.addPaths(self.lib_dirs[:5000])  # 極端に多いときは上限まで

    def auto_rescan(self):
        """見張っているフォルダに変化があったとき：曲の増減があればライブラリを読み直す"""
        if not self.folders:
            return
        now = []
        for d in self.folders:
            now += scan_audio(d)[0]
        if [path_key(p) for p in now] == [path_key(p) for p in self.lib_tracks]:
            # 曲は同じ（歌詞ファイルが増えた等）。新しいサブフォルダだけ見張りに足す
            dirs = []
            for d in self.folders:
                dirs += scan_audio(d)[1]
            if len(dirs) != len(self.lib_dirs):
                self.lib_dirs = dirs
                self.update_lib_watch()
            return
        before = len(self.lib_tracks)
        self.set_folders(self.folders, keep_current=True)
        diff = len(self.lib_tracks) - before
        msg = f"新しい曲を {diff} 曲読み込みました" if diff > 0 else (
            f"{-diff} 曲がフォルダから無くなりました" if diff < 0 else "フォルダの変更を反映しました")
        self.statusBar().showMessage(msg, 5000)

    # ---------------- lyrics
    def resolve_lyrics(self, p):
        """(テキスト, 出どころ表示, 読んだファイル, 種類)。インストの曲は種類が "instrumental"
        （テキストは編集画面で外したときのために、見つかった歌詞をそのまま返す）"""
        text, src, lfile, kind = self._resolve_lyrics_text(p)
        if self.store.is_instrumental(p):
            return text, "Instrumental", lfile, "instrumental"
        return text, src, lfile, kind

    def _resolve_lyrics_text(self, p):
        t = self.store.get(p)
        if t:
            return t, "アプリ内に保存した歌詞", None, "app"
        r = find_lyric_file(p)
        if r:
            return r[0], r[1], r[2], "file"
        t = self.store.find_by_meta(*self.cur_meta)
        if t:
            return t, "アプリ内に保存した歌詞（曲名・アーティスト名で一致）", None, "meta"
        t = embedded_lyrics(p)
        if t:
            return norm_newlines(t), "曲ファイルに埋め込まれた歌詞", None, "embedded"
        return None, "", None, None

    def load_lyrics(self, keep_scroll=False):
        if not (0 <= self.cur < len(self.tracks)):
            return
        p = self.tracks[self.cur]
        text, src, lfile, kind = self.resolve_lyrics(p)
        same = keep_scroll and text == self.lyrics_text and kind == self.lyrics_kind
        self.lyrics_file, self.lyrics_kind = lfile, kind
        self._watch(p, lfile)
        if kind in ("app", "meta", None):
            # アプリ内の歌詞・歌詞なしのときは、出どころの代わりにアルバムのリリース年を出す（無ければ空欄）
            year = self.store.track_info(p)["year"] if os.path.isfile(p) else ""
            self.store.commit_meta()  # 初めて読んだ曲の情報はすぐ保存（データベースを開きっぱなしにしない）
            src = f"{year}年" if year else ""
        self.srcLbl.setText(src)
        if same:
            return  # 中身が変わっていなければ表示はそのまま
        self.lyrics_text = text
        self._render_lyrics(keep_scroll)

    def _render_lyrics(self, keep_scroll):
        if self.lyrics_kind == "instrumental":
            title, artist = self.cur_meta
            self.view.set_instrumental(title, artist)
            return
        sb = self.view.verticalScrollBar()
        frac = sb.value() / sb.maximum() if (keep_scroll and sb.maximum() > 0) else None
        self.view.set_lyrics(self.lyrics_text, self.font_px)
        if frac is not None:
            sb.setValue(round(frac * sb.maximum()))
        elif self.autoscroll and self.view.has_text:
            sb.setValue(round(self.scroll_target(self.player.position() / 1000)))  # 今の再生位置に合った場所から
        else:
            sb.setValue(round(min(self.view.intro_px, sb.maximum())))  # 手動のときは最初の行を「今ここ」に
        self.scroll_f = float(sb.value())
        if self.timingDlg is not None and self.timingDlg.isVisible():
            self.timingDlg.update_fields()  # 歌詞が変わったらフレーズの一覧も作り直す

    def _watch(self, p, lfile):
        old = self.watcher.files() + self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        paths = [os.path.dirname(p)]
        for sub in LYRIC_SUBDIRS:
            sd = os.path.join(os.path.dirname(p), sub)
            if os.path.isdir(sd):
                paths.append(sd)
        if lfile:
            paths.append(lfile)
        self.watcher.addPaths(paths)

    def set_font(self, px):
        px = max(14, min(64, px))
        if px == self.font_px and self.view.font_px == px:
            return
        self.font_px = px
        self.settings.setValue("font_px", self.font_px)
        if self.view.has_text:
            self._render_lyrics(keep_scroll=True)
        self._sync_settings()

    def edit_lyrics(self, initial_text=None):
        if not (0 <= self.cur < len(self.tracks)):
            return
        p = self.tracks[self.cur]
        text = initial_text if initial_text is not None else self.lyrics_text
        was_inst = self.store.is_instrumental(p)
        dlg = EditDialog(self, self.titleLbl.text(), text, embedded=embedded_lyrics(p), instrumental=was_inst)
        if not dlg.exec():
            return
        text = dlg.text()
        title, artist = self.cur_meta
        try:
            if dlg.instrumental():
                # インストの印だけ付ける（歌詞が入っていても消さない。外せばまた表示される）
                self.store.set_instrumental(p, True)
                self.statusBar().showMessage("Instrumental にしました", 4000)
                self.lyrics_text = None
                self.load_lyrics()
                return
            if was_inst:
                self.store.set_instrumental(p, False)
            if text.strip():
                self.store.put(p, title, artist, text)
                msg = "アプリ内に保存しました"
            else:
                self.store.delete(p)
                msg = "アプリ内の歌詞を削除しました"
                lf = find_lyric_file(p)
                if lf:  # 曲の隣の歌詞ファイルは消さないので、そちらが表示される
                    msg += f"（{os.path.basename(lf[2])} があるので、そちらを表示します）"
        except (OSError, sqlite3.Error) as e:
            QMessageBox.warning(self, APP, f"保存できませんでした。\n{e}")
            return
        self.statusBar().showMessage(msg, 4000)
        self.lyrics_text = None
        self.load_lyrics()

    def import_txt(self, src):
        if not (0 <= self.cur < len(self.tracks)):
            return
        try:
            text = read_text_file(src)
        except OSError as e:
            QMessageBox.warning(self, APP, f"読み込めませんでした。\n{e}")
            return
        if src.lower().endswith(".lrc"):
            text = strip_lrc(text)
        # 中身を確認してから保存できるよう、編集画面で開く
        self.edit_lyrics(initial_text=norm_newlines(text))

    # ---------------- auto scroll
    def set_autoscroll(self, on):
        self.autoscroll = on
        self.settings.setValue("autoscroll", on)
        self.update_auto_btn()
        self.scroll_f = float(self.view.verticalScrollBar().value())

    def update_auto_btn(self):
        self.autoBtn.setText("自動スクロール")  # ON / OFF はボタンの色で表す

    def _key(self, p):
        return path_key(p)

    # ---- タイミング（歌い出し・歌い終わり・フレーズの印）
    def timing_raw(self):
        """今の曲の保存済み設定 {"start","end","marks":[[行, 秒, 行の文字],...]}（未設定の項目は無し）"""
        if not (0 <= self.cur < len(self.tracks)):
            return {}
        r = self.ranges.get(self._key(self.tracks[self.cur]), {})
        d = {k: r[k] for k in ("start", "end") if r.get(k) is not None}
        d["marks"] = [list(m) for m in r.get("marks", [])]
        return d

    def set_timing(self, d):
        if not (0 <= self.cur < len(self.tracks)):
            return
        k = self._key(self.tracks[self.cur])
        r = {}
        for name in ("start", "end"):
            if d.get(name) is not None:
                r[name] = round(float(d[name]), 2)
        if d.get("marks"):
            r["marks"] = sorted([int(m[0]), round(float(m[1]), 2), m[2] if len(m) > 2 else None] for m in d["marks"])
        if r:
            self.ranges[k] = r
        else:
            self.ranges.pop(k, None)
        self._save_ranges()
        self.update_range_label()

    def get_timing(self):
        """(歌い出し, 歌い終わり) — 未設定なら曲全体"""
        dur = self.player.duration() / 1000
        r = self.timing_raw()
        start = float(r.get("start", 0.0))
        end = float(r.get("end") or dur or 0)
        if end <= start + 1:
            end = max(dur, start + 1)
        return start, end

    def update_range_label(self):
        """今の設定は［タイミング編集］ボタンのツールチップで見られるようにする"""
        tip = "曲を聴きながら、歌い出し・歌い終わり・フレーズを設定します（Ctrl+T）"
        if self.cur >= 0:
            s, e = self.get_timing()
            custom = self._key(self.tracks[self.cur]) in self.ranges
            nm = len(self.timing_raw().get("marks", []))
            tip += (f"\n今の設定：{fmt(s)} → {fmt(e)}"
                    + (f"・フレーズ {nm}" if nm else "") + ("" if custom else "（曲全体）"))
        self.timingBtn.setToolTip(tip)

    def open_timing(self):
        if not (0 <= self.cur < len(self.tracks)):
            return
        if self.timingDlg is None:
            self.timingDlg = TimingDialog(self)
        self.timingDlg.refresh()
        self.timingDlg.show()
        self.timingDlg.raise_()
        self.timingDlg.activateWindow()

    def scroll_model(self):
        """今の曲・歌詞・画面の大きさに合わせた ScrollModel（変わったときだけ作り直す）"""
        m = self.view.verticalScrollBar().maximum()
        raw = self.timing_raw()
        timing = self.get_timing()
        key = (self.view.layout_rev, m, self.cur, json.dumps(raw, sort_keys=True), timing)
        if getattr(self, "_model_key", None) != key:
            texts = lyric_line_texts(self.lyrics_text) if self.view.has_text else []
            marks = resolve_marks(raw.get("marks"), texts)
            start, end = timing
            self._model = ScrollModel(min(self.view.intro_px, m), m, self.view.spacer_spans(),
                                      self.view.line_positions(), marks, start, end)
            self._model_key = key
        return self._model

    def scroll_target(self, t):
        """再生時刻 t（秒）のときのスクロール位置。
        イントロ（0 〜 歌い出し）：歌詞が画面の下から一定の速さで上がってきて、歌い出しで最初の行が「今ここ」に。
        歌い出し以降：歌い終わりまで一定の速さで流れる。
        フレーズの印があれば、その行がちょうどその時刻に「今ここ」に来るように合わせる。"""
        return self.scroll_model().y_at(t)

    def line_time(self, line_top):
        """歌詞の行（文書内の上端 y）が、自動スクロールで「今ここ」に来る時刻（秒）"""
        return self.scroll_model().time_of(line_top - self.view.viewport().height() * LyricsView.READ_LINE)

    def seek_to_line(self, line_top):
        if self.cur < 0 or self.player.duration() <= 0:
            return
        t = self.line_time(line_top)
        self.player.setPosition(int(t * 1000))
        self.view._hold_ms = 0  # すぐ自動スクロールに戻して、その行を「今ここ」に合わせる
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.player.play()
        self.statusBar().showMessage(f"{fmt(t)} から再生", 2000)

    def tick(self):
        if not (self.autoscroll and self.view.has_text and self.cur >= 0):
            return
        sb = self.view.verticalScrollBar()
        if self.view.held() or self.player.duration() <= 0:
            self.scroll_f = float(sb.value())
            return
        target = self.scroll_target(self.player.position() / 1000)
        if abs(sb.value() - round(self.scroll_f)) > 1:
            self.scroll_f = float(sb.value())
        diff = target - self.scroll_f
        self.scroll_f += diff * 0.18 if abs(diff) > 0.5 else diff
        sb.setValue(round(self.scroll_f))

    # ---------------- persistence
    def _ranges_path(self):
        return os.path.join(app_data_dir(), "ranges.json")

    def _load_ranges(self):
        try:
            with open(self._ranges_path(), encoding="utf-8") as f:
                ranges = json.load(f)
        except (OSError, ValueError):
            return {}
        # 「間奏」の機能は廃止したので、前のバージョンで保存した間奏のデータは消しておく
        changed = False
        for k in list(ranges):
            r = ranges[k]
            if isinstance(r, dict) and "breaks" in r:
                del r["breaks"]
                changed = True
                if not r:
                    del ranges[k]
        if changed:
            self.ranges = ranges
            self._save_ranges()
        return ranges

    def _save_ranges(self):
        try:
            with open(self._ranges_path(), "w", encoding="utf-8") as f:
                json.dump(self.ranges, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ---------------- drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths, external=False):
        """ドロップされた／外から渡されたファイル・フォルダを開く。
        external=True（「プログラムから開く」など）のときは、リストを表示中でも追加せずに再生する"""
        paths = [os.path.abspath(p) for p in paths]
        dirs = [p for p in paths if os.path.isdir(p)]
        audio = [p for p in paths if os.path.splitext(p)[1].lower() in AUDIO_EXT]
        lyric = [p for p in paths if os.path.splitext(p)[1].lower() in (".txt", ".lrc")]
        if external:
            audio.sort(key=natkey)
        if (dirs or audio) and self.view_source and not external:
            # リストを表示中なら、落としたフォルダ・曲をそのリストに追加（別々のフォルダでも OK）
            files = []
            for p in paths:
                if p in dirs:
                    files += scan_audio(p)[0]
                elif p in audio:
                    files.append(p)
            if files:
                self.add_paths_to(self.view_source, files)
            else:
                self.statusBar().showMessage("音楽ファイルが見つかりませんでした", 4000)
        elif dirs:
            new = [d for d in dirs if self.root_of(d) is None]
            if new:
                had = bool(self.folders)
                self.set_folders(self.folders + new, keep_current=had)
                self.statusBar().showMessage(f"{len(new)} フォルダを追加しました（ライブラリ {len(self.lib_tracks)} 曲）", 5000)
        elif audio:
            p = audio[0]
            if self.root_of(p) is not None:
                self.play_path_in_library(p)  # ライブラリ内の曲ならそのまま再生
            else:
                self.add_folder(os.path.dirname(p), play_path=p)
        elif lyric:
            self.import_txt(lyric[0])

    def closeEvent(self, e):
        if not self._no_save:
            self.settings.setValue("geometry", self.saveGeometry())
            self.save_position()
        if IS_WIN:
            hwnd = int(self.winId())
            for hid in self._hotkeys:
                _user32.UnregisterHotKey(hwnd, hid)
            self._hotkeys = {}
        super().closeEvent(e)


STYLE = f"""
QWidget {{ background:#111111; color:#ececec; }}
QToolTip {{ background:#222222; color:#ececec; border:1px solid #333333; padding:4px; }}
#side, #side QWidget {{ background:#181818; }}
#head {{ font-size:15px; font-weight:bold; }}
#title {{ font-size:20px; font-weight:bold; }}
#dim {{ color:#999999; }}
#small {{ color:#999999; font-size:12px; }}
#time {{ color:#999999; min-width:40px; qproperty-alignment:AlignCenter; }}
QListWidget {{ border:none; outline:0; }}
QListWidget::item {{ padding:6px 8px; border-radius:1px; }}
QListWidget::item:hover {{ background:#1e1e1e; }}
QListWidget::item:selected {{ background:#2a2a2a; color:#ffffff; }}
QPushButton {{ background:#222222; border:1px solid transparent; border-radius:2px; padding:6px 12px; }}
QPushButton:hover {{ border-color:{ACCENT}; }}
QPushButton:checked {{ background:{ACCENT}; color:#ffffff; border-color:{ACCENT}; }}
QPushButton#primary {{ background:{ACCENT}; color:#ffffff; border-color:{ACCENT}; font-weight:bold; }}
QPushButton#play {{ background:#ececec; border-color:#ececec; border-radius:2px; min-width:36px; min-height:24px; }}
QLineEdit, QPlainTextEdit {{ background:#111111; border:1px solid #333333; border-radius:2px; padding:6px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color:{ACCENT}; }}
QCheckBox {{ background:transparent; padding:3px 0; spacing:8px; }}
QCheckBox::indicator {{ width:14px; height:14px; border:1px solid #666666; background:#111111; border-radius:1px; }}
QCheckBox::indicator:checked {{ background:{ACCENT}; border-color:{ACCENT}; }}
QPushButton#step {{ padding:4px 0; min-width:30px; }}
QPushButton#mode {{ padding:5px 10px; min-width:26px; }}
QPushButton#iconbtn {{ padding:4px 8px; background:transparent; border-color:transparent; }}
QPushButton#iconbtn:hover {{ background:#222222; border-color:{ACCENT}; }}
QComboBox {{ background:#222222; border:1px solid #333333; border-radius:2px; padding:5px 10px; font-weight:bold; }}
QComboBox:hover {{ border-color:{ACCENT}; }}
QComboBox::drop-down {{ border:none; background:transparent; width:20px; }}
QComboBox QAbstractItemView {{ background:#222222; border:1px solid #333333; selection-background-color:#2a2a2a;
  selection-color:#ffffff; outline:0; }}
QMenu {{ background:#222222; border:1px solid #333333; padding:4px; }}
QMenu::item {{ padding:6px 18px; border-radius:0px; }}
QMenu::item:selected {{ background:#2e2e2e; color:#ffffff; }}
QMenu::item:disabled {{ color:#666666; }}
QMenu::separator {{ height:1px; background:#333333; margin:4px 6px; }}
QSlider::groove:horizontal {{ height:4px; background:#333333; border-radius:2px; }}
QSlider::sub-page:horizontal {{ background:{ACCENT}; border-radius:2px; }}
QSlider::handle:horizontal {{ background:#ffffff; width:12px; height:12px; margin:-4px 0; border-radius:6px; }}
#bottom {{ background:#181818; border-top:1px solid #333333; }}
#bottom QLabel {{ background:#181818; }}
QTextBrowser {{ background:#111111; color:#ececec; border:none; }}
QScrollBar:vertical {{ background:transparent; width:8px; }}
QScrollBar::handle:vertical {{ background:#333333; border-radius:1px; min-height:30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
QSplitter::handle {{ background:#333333; }}
QStatusBar {{ background:#181818; color:#999999; }}
QMessageBox, QDialog {{ background:#181818; }}
QDialog QLabel {{ background:transparent; }}
QDialog QListWidget {{ background:#111111; border:1px solid #333333; border-radius:2px; }}
#bigtime {{ font-size:26px; font-weight:bold; }}
#value {{ font-size:15px; color:#ffffff; }}
"""


def apply_ui_font(app):
    """源暎ラテン 4 書体を読み込み、基本フォント（源暎ラテゴ）を設定する。
    見つからない書体は Windows 標準の游ゴシック UI などで代わりに表示する。"""
    for role, name in UI_FONT_FILES.items():
        fam = load_font_file(name, UI_FONT_DIRS)
        if fam:
            FONTS[role] = fam
    f = QFont()
    f.setFamilies(([FONTS["go"]] if "go" in FONTS else []) + UI_FONT_FALLBACK)
    f.setPointSizeF(10.5)
    app.setFont(f)
    return FONTS.get("go")


def _ff(role):
    fam = FONTS.get(role)
    return f'font-family:"{fam}";' if fam else ""


def _chevron_rule():
    """リスト選択の右端に出す「∨」。描いた記号を画像にして保存し、スタイルから参照する（飾りなので押せる範囲は箱全体のまま）"""
    pm = icon_pixmap("chevron", "#e0e0e0", 14)
    path = os.path.join(app_data_dir(), "chevron_ui.png")
    if not pm.toImage().save(path):
        return ""
    url = path.replace("\\", "/")
    return (f'QComboBox::drop-down {{ border:none; background:transparent; width:30px; '
            f'subcontrol-origin:padding; subcontrol-position:center right; }}\n'
            f'QComboBox::down-arrow {{ image:url("{url}"); width:14px; height:14px; }}\n')


def build_style():
    """基本の配色（STYLE）に、役割ごとのフォントと大きさを足したもの"""
    return STYLE + _chevron_rule() + f"""
#head {{ {_ff('min')} font-size:17px; font-weight:bold; }}
QComboBox {{ {_ff('min')} font-size:15px; font-weight:normal; }}
QComboBox#sort {{ {_ff('goP')} font-size:13px; padding:5px 8px; }}
QListWidget {{ font-size:14px; }}
QPushButton#primary {{ font-size:14px; font-weight:normal; }}
#time {{ {_ff('min')} font-size:15px; }}
#title {{ {_ff('minP')} font-weight:normal; }}
#small, #dim, QStatusBar, QToolTip {{ {_ff('goP')} }}
#bigtime {{ {_ff('min')} font-size:32px; font-weight:bold; }}
#value {{ {_ff('min')} font-size:18px; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setOrganizationName(APP_ID)
    app.setApplicationDisplayName(APP)
    # 起動時に渡されたファイル（「プログラムから開く」や exe へのドロップ）
    args = [os.path.abspath(a) for a in sys.argv[1:] if os.path.exists(a)]

    # すでに起動しているなら、そちらにファイルを渡して終わる
    if not acquire_instance():
        if send_to_running(args):
            return
        # 渡せなかった（前のアプリが固まっている等）ときは、このまま起動する
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE_NAME)
    server.listen(INSTANCE_NAME)
    _instance["server"] = server  # 届いた分は、ウィンドウができてから受け取る

    apply_ui_font(app)
    styler = TitleBarStyler(app)
    app.installEventFilter(styler)
    app.setStyleSheet(build_style())
    w = Main()
    w.show()
    w.register_hotkeys()

    def on_connection():
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))

            def done(sock=sock, buf=buf):
                read()
                sock.deleteLater()
                try:
                    paths = [p for p in json.loads(buf.decode("utf-8")) if isinstance(p, str)]
                except ValueError:
                    paths = []
                w.open_external(paths)

            sock.readyRead.connect(read)
            sock.disconnected.connect(done)
            read()

    server.newConnection.connect(on_connection)
    if args:
        QTimer.singleShot(0, lambda: w.open_external(args))
    code = app.exec()
    release_instance()
    sys.exit(code)


if __name__ == "__main__":
    main()
