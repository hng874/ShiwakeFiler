# -*- coding: utf-8 -*-
"""
ShiwakeFiler（シワケファイラー） v1.2 — 動画・画像・PSD・PDFプレビュー付き2ペインファイラー
  [ペインA] [ペインB] [プレビュー]   ※アクティブなペインの選択がプレビューされる

  移動
    ↑↓ : 選択      Enter/ダブルクリック : フォルダに入る（ファイルはリネーム欄へ）
    Backspace / Alt+↑ : 上へ     Alt+← / Alt+→ : 戻る/進む
    ← → / Tab : 左右ペインへ    Ctrl+L : アドレス欄    F5 : 再読み込み
  ファイル操作（マークがあればマーク分、なければカーソル位置）
    Space : マーク（次の行へ）   Ctrl+A : 全マーク/全解除
    M : 反対ペインへ移動    C : 反対ペインへコピー
    K : フォルダ作成        D / Delete : ごみ箱へ
    L : ドライブ変更        R / F2 : リネーム
    O : 反対ペインを同じフォルダにする
  一覧の先頭には常に「デスクトップ」へのショートカット
  プレビュー
    P : 再生/一時停止    Ctrl+← → : 5秒移動（Ctrl+Shift で1秒）／PDFはページ送り
必要: pip install PySide6 av send2trash pillow
"""
import os
import shutil
import sys
import time
from pathlib import Path

import av
from PySide6.QtCore import (Qt, QUrl, QThread, Signal, QSize, QEvent, QSettings, QDir, QPoint,
                            QStandardPaths, QTimer)
from PySide6.QtGui import (QImage, QPixmap, QIcon, QShortcut, QKeySequence, QColor, QBrush, QFont,
                           QImageReader, QImageIOHandler, QMovie)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QListWidget, QListWidgetItem, QSplitter,
    QVBoxLayout, QHBoxLayout, QSlider, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QCheckBox, QListView, QAbstractItemView,
    QTreeWidget, QTreeWidgetItem, QFileIconProvider, QStyle, QToolButton, QHeaderView,
    QInputDialog, QMenu, QProgressDialog, QStyleOptionSlider, QStackedWidget,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtPdf import QPdfDocument

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".mpg", ".mpeg",
              ".ts", ".m2ts", ".mts", ".webm", ".flv", ".3gp", ".vob"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
              ".ico", ".jfif", ".tga", ".svg", ".psd"}
PDF_EXTS = {".pdf"}
PDF_THUMB_H = 90
THUMB_COUNT = 12
THUMB_W = 160
PLAY_KEY = Qt.Key_P          # 再生/一時停止キー（好みで変更可）
MARK_BG = QColor(255, 214, 102)
MARK_FG = QColor(0, 0, 0)
BAD_CHARS = set('<>:"/\\|?*')


def fmt_time(ms: int) -> str:
    s = max(0, ms // 1000)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


class ThumbWorker(QThread):
    """PyAVでサムネイルを等間隔に切り出す（日本語パスOK）"""
    thumb = Signal(int, int, float, QImage)  # gen, index, sec, image

    def __init__(self, gen: int, path: str, count: int):
        super().__init__()
        self.gen, self.path, self.count = gen, path, count

    def run(self):
        try:
            with av.open(self.path) as c:
                st = c.streams.video[0]
                st.thread_type = "AUTO"
                if c.duration:
                    dur = c.duration / av.time_base
                elif st.duration:
                    dur = float(st.duration * st.time_base)
                else:
                    return
                for i in range(self.count):
                    if self.isInterruptionRequested():
                        return
                    t = dur * (i + 0.5) / self.count
                    try:
                        c.seek(int(t / st.time_base), stream=st, backward=True, any_frame=False)
                        for n, frame in enumerate(c.decode(st)):
                            # キーフレームから目標時刻まで送る（最大300フレーム）
                            if frame.time is not None and frame.time < t - 0.05 and n < 300:
                                continue
                            w = THUMB_W
                            h = max(1, int(frame.height * w / frame.width))
                            f = frame.reformat(width=w, height=h, format="rgb24")
                            p = f.planes[0]
                            img = QImage(bytes(p), w, h, p.line_size, QImage.Format_RGB888).copy()
                            sec = frame.time if frame.time is not None else t
                            self.thumb.emit(self.gen, i, float(sec), img)
                            break
                    except Exception:
                        continue
        except Exception:
            pass




class ClickSlider(QSlider):
    """クリックした位置へ即ジャンプ（そのままドラッグも可）"""

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.maximum() > self.minimum():
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            st = self.style()
            groove = st.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
            handle = st.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
            pt = e.position().toPoint()
            if not handle.contains(pt):
                span = max(1, groove.width() - handle.width())
                x = pt.x() - groove.x() - handle.width() // 2
                v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span)
                self.setValue(v)
                self.sliderMoved.emit(v)
        super().mousePressEvent(e)


class Cancelled(Exception):
    pass


def tree_size(p: Path) -> int:
    try:
        if p.is_dir() and not p.is_symlink():
            total = 0
            for root, _dirs, files in os.walk(p):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
        return p.stat().st_size
    except OSError:
        return 0


class FileOpWorker(QThread):
    """コピー/移動をバックグラウンドで実行（進捗つき・中止可）"""
    progress = Signal(int, str)   # 0-1000, ラベル
    done = Signal(list, bool)     # エラー一覧, 中止されたか

    CHUNK = 8 * 1024 * 1024

    def __init__(self, mode: str, sources: list, dst_dir: Path):
        super().__init__()
        self.mode, self.sources, self.dst_dir = mode, sources, dst_dir
        self.total = 1
        self.copied = 0
        self.cur_name = ""
        self._last_emit = 0.0

    def _emit(self, force=False):
        now = time.monotonic()
        if force or now - self._last_emit > 0.1:
            self._last_emit = now
            self.progress.emit(int(self.copied * 1000 / self.total), self.cur_name)

    def run(self):
        errors, cancelled = [], False
        self.cur_name = "サイズ計算中…"
        self._emit(True)
        sizes = {s: tree_size(s) for s in self.sources}
        self.total = max(1, sum(sizes.values()))
        for src in self.sources:
            if self.isInterruptionRequested():
                cancelled = True
                break
            dst = self.dst_dir / src.name
            self.cur_name = src.name
            self._emit(True)
            try:
                if self.mode == "move":
                    if not dst.exists():
                        try:
                            os.rename(src, dst)          # 同一ドライブなら一瞬
                            self.copied += sizes[src]
                            continue
                        except OSError:
                            pass                          # 別ドライブ → コピー＋削除
                    self._copy_any(src, dst)
                    if src.is_dir() and not src.is_symlink():
                        shutil.rmtree(src)
                    else:
                        os.remove(src)
                else:
                    self._copy_any(src, dst)
            except Cancelled:
                cancelled = True
                break
            except Exception as e:
                errors.append(f"{src.name}: {e}")
        self._emit(True)
        self.done.emit(errors, cancelled)

    def _copy_any(self, src: Path, dst: Path):
        if src.is_dir() and not src.is_symlink():
            dst.mkdir(exist_ok=True)
            with os.scandir(src) as it:
                for e in it:
                    self._copy_any(Path(e.path), dst / e.name)
            try:
                shutil.copystat(src, dst)
            except OSError:
                pass
        else:
            self._copy_file(src, dst)

    def _copy_file(self, src: Path, dst: Path):
        self.cur_name = src.name
        try:
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                while True:
                    if self.isInterruptionRequested():
                        raise Cancelled()
                    buf = fi.read(self.CHUNK)
                    if not buf:
                        break
                    fo.write(buf)
                    self.copied += len(buf)
                    self._emit()
        except Cancelled:
            try:
                os.remove(dst)   # 途中までのファイルは消す
            except OSError:
                pass
            raise
        shutil.copystat(src, dst)


# ファイラーの行種別
K_UP, K_DRIVE, K_DIR, K_VIDEO, K_FILE, K_IMAGE, K_DESK, K_PDF = range(8)
NAV_KINDS = (K_UP, K_DESK)   # 移動用の特別な行


def _group(kind):
    """並び順のグループ：デスクトップ → .. → フォルダ → ファイル"""
    return {K_DESK: -1, K_UP: 0, K_DRIVE: 1, K_DIR: 1}.get(kind, 2)


class FileItem(QTreeWidgetItem):
    """フォルダを常に上に置いたまま列ソートする行"""

    def __init__(self, kind, path, name, size=-1, mtime=0.0):
        super().__init__()
        self.kind, self.path, self.name = kind, path, name
        self.size, self.mtime = size, mtime
        self.marked = False
        self.ext = "" if kind in (K_UP, K_DRIVE, K_DIR, K_DESK) else Path(name).suffix.lower()
        self.setText(0, name)
        if size >= 0 and kind in (K_VIDEO, K_FILE, K_IMAGE, K_PDF):
            self.setText(1, fmt_size(size))
            self.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        if mtime:
            self.setText(2, time.strftime("%Y/%m/%d %H:%M", time.localtime(mtime)))
        self.setText(3, {K_UP: "", K_DRIVE: "ドライブ", K_DIR: "フォルダ", K_DESK: "ショートカット"}.get(
            kind, self.ext[1:].upper() if self.ext else "ファイル"))

    @property
    def markable(self):
        return self.kind in (K_DIR, K_VIDEO, K_FILE, K_IMAGE, K_PDF)

    def set_marked(self, on: bool):
        self.marked = on and self.markable
        f = QFont(self.font(0))
        f.setBold(self.marked)
        for c in range(4):
            self.setBackground(c, QBrush(MARK_BG) if self.marked else QBrush())
            self.setForeground(c, QBrush(MARK_FG) if self.marked else QBrush())
            self.setFont(c, f)

    def __lt__(self, other):
        tw = self.treeWidget()
        desc = tw is not None and tw.header().sortIndicatorOrder() == Qt.DescendingOrder
        ga, gb = _group(self.kind), _group(other.kind)
        if ga != gb:
            return (ga < gb) != desc
        col = tw.sortColumn() if tw else 0
        if col == 1:
            ka, kb = self.size, other.size
        elif col == 2:
            ka, kb = self.mtime, other.mtime
        elif col == 3:
            ka, kb = (self.ext, self.name.lower()), (other.ext, other.name.lower())
        else:
            ka, kb = self.name.lower(), other.name.lower()
        return ka < kb


class FilePane(QWidget):
    """1ペイン分のファイラー"""
    currentChanged = Signal(object, object)   # pane, item

    def __init__(self, win, key: str):
        super().__init__()
        self.win, self.key = win, key
        self.folder: Path | None = None
        self.back_stack: list = []
        self.fwd_stack: list = []
        self.marks: set[str] = set()
        st = self.style()

        def tbtn(icon, tip, slot):
            b = QToolButton()
            b.setIcon(st.standardIcon(icon))
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            return b
        self.btn_back = tbtn(QStyle.SP_ArrowBack, "戻る (Alt+←)", self.go_back)
        self.btn_fwd = tbtn(QStyle.SP_ArrowForward, "進む (Alt+→)", self.go_forward)
        btn_up = tbtn(QStyle.SP_ArrowUp, "上へ (Backspace)", self.go_up)
        btn_browse = tbtn(QStyle.SP_DirOpenIcon, "フォルダを選ぶ…", self.choose_folder)
        self.addr = QLineEdit()
        self.addr.setPlaceholderText("パスを入力してEnter（Ctrl+L）")
        self.addr.returnPressed.connect(self.on_addr_enter)
        self.addr.installEventFilter(win)
        bar = QHBoxLayout()
        for w in (self.btn_back, self.btn_fwd, btn_up):
            bar.addWidget(w)
        bar.addWidget(self.addr, 1)
        bar.addWidget(btn_browse)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名前", "サイズ", "更新日時", "種類"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(lambda cur, _p: self.currentChanged.emit(self, cur))
        self.tree.itemActivated.connect(lambda it, _c: win.on_activate(self, it))
        self.tree.installEventFilter(win)
        self.lbl_count = QLabel("")

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.addLayout(bar)
        v.addWidget(self.tree, 1)
        v.addWidget(self.lbl_count)
        self.set_active(False)

    # ---- 状態
    def set_active(self, on: bool):
        self.tree.setStyleSheet(
            "QTreeWidget { border: 2px solid #3a8ee6; }" if on else
            "QTreeWidget { border: 2px solid transparent; }")

    def current_item(self):
        return self.tree.currentItem()

    def items(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def find(self, name):
        for it in self.items():
            if it.name == name or str(it.path) == name:
                return it
        return None

    def neighbor_name(self, removed: list):
        """removed が消えた後にカーソルを置く行（直後→なければ直前）"""
        cur = self.current_item()
        if cur is None:
            return None
        gone = {str(p) for p in removed}
        if str(cur.path) not in gone:
            return cur.name
        rows = self.items()
        i = self.tree.indexOfTopLevelItem(cur)
        for it in rows[i + 1:]:
            if str(it.path) not in gone:
                return it.name
        for it in reversed(rows[:i]):
            if str(it.path) not in gone and it.kind not in NAV_KINDS:
                return it.name
        return None

    def targets(self) -> list:
        """マークがあればマーク分、なければカーソル位置"""
        marked = [it.path for it in self.items() if it.marked]
        if marked:
            return marked
        it = self.current_item()
        return [it.path] if it is not None and it.markable else []

    # ---- マーク
    def toggle_mark(self):
        it = self.current_item()
        if it is None:
            return
        if it.markable:
            it.set_marked(not it.marked)
            (self.marks.add if it.marked else self.marks.discard)(it.name)
        idx = self.tree.indexOfTopLevelItem(it)
        if idx + 1 < self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(idx + 1))
        self.update_count()

    def mark_all(self):
        cand = [it for it in self.items() if it.markable]
        on = not all(it.marked for it in cand)
        for it in cand:
            it.set_marked(on)
        self.marks = {it.name for it in cand} if on else set()
        self.update_count()

    def update_count(self):
        n_dir = n_vid = n_img = n_file = 0
        for it in self.items():
            n_dir += it.kind in (K_DIR, K_DRIVE)
            n_vid += it.kind == K_VIDEO
            n_img += it.kind in (K_IMAGE, K_PDF)
            n_file += it.kind == K_FILE
        s = f"フォルダ {n_dir}  動画 {n_vid}  画像・PDF {n_img}  その他 {n_file}"
        m = [it for it in self.items() if it.marked]
        if m:
            sz = sum(max(0, it.size) for it in m)
            s += f"   ★マーク {len(m)}件 {fmt_size(sz)}"
        self.lbl_count.setText(s)

    # ---- 移動
    def choose_folder(self):
        start = str(self.folder) if self.folder else ""
        d = QFileDialog.getExistingDirectory(self, "フォルダを選択", start)
        if d:
            self.navigate(Path(d))

    def on_addr_enter(self):
        t = self.addr.text().strip().strip('"')
        if not t:
            self.navigate(None)
            return
        p = Path(os.path.expandvars(os.path.expanduser(t)))
        if p.is_dir():
            self.navigate(p)
        elif p.is_file():
            self.navigate(p.parent, select_name=p.name)
        else:
            QMessageBox.warning(self, "移動", f"見つかりません:\n{t}")

    def _cur_name(self):
        it = self.current_item()
        return it.name if it is not None else None

    def navigate(self, folder, select_name=None, record=True):
        if record and folder != self.folder:
            self.back_stack.append((self.folder, self._cur_name()))
            self.fwd_stack.clear()
        self.load_folder(folder, select_name)

    def go_back(self):
        if self.back_stack:
            self.fwd_stack.append((self.folder, self._cur_name()))
            self.load_folder(*self.back_stack.pop())

    def go_forward(self):
        if self.fwd_stack:
            self.back_stack.append((self.folder, self._cur_name()))
            self.load_folder(*self.fwd_stack.pop())

    def go_up(self):
        if self.folder is None:
            return
        parent = self.folder.parent
        if parent == self.folder:
            self.navigate(None, select_name=str(self.folder))
        else:
            self.navigate(parent, select_name=self.folder.name)

    def reload(self):
        self.load_folder(self.folder, self._cur_name())

    def load_folder(self, folder, select_name=None):
        if folder is not None and not folder.is_dir():
            folder = None
        if folder != self.folder:
            self.marks.clear()
        self.folder = folder
        self.btn_back.setEnabled(bool(self.back_stack))
        self.btn_fwd.setEnabled(bool(self.fwd_stack))
        w = self.win
        tree = self.tree
        had_focus = tree.hasFocus()
        tree.blockSignals(True)
        tree.setSortingEnabled(False)
        tree.clear()
        items = []
        desk = w.desktop_path()
        if desk is not None:
            it = FileItem(K_DESK, desk, "デスクトップ")
            it.setIcon(0, w.ic_desk)
            items.append(it)
        if folder is None:
            self.addr.setText("")
            for d in QDir.drives():
                p = d.absoluteFilePath()
                it = FileItem(K_DRIVE, Path(p), p)
                it.setIcon(0, w.ic_drive)
                items.append(it)
        else:
            w.settings.setValue(f"last_folder_{self.key}", str(folder))
            self.addr.setText(str(folder))
            up = FileItem(K_UP, folder.parent if folder.parent != folder else None, "..")
            up.setIcon(0, w.ic_up)
            items.append(up)
            media_only = w.chk_video_only.isChecked()
            show_hidden = w.chk_hidden.isChecked()
            try:
                with os.scandir(folder) as sd:
                    for e in sd:
                        if not show_hidden and (e.name.startswith(".") or _is_hidden(e)):
                            continue
                        try:
                            is_dir = e.is_dir()
                            stt = e.stat()
                        except OSError:
                            continue
                        if is_dir:
                            it = FileItem(K_DIR, Path(e.path), e.name, mtime=stt.st_mtime)
                            it.setIcon(0, w.ic_dir)
                        else:
                            ext = Path(e.name).suffix.lower()
                            kind = (K_VIDEO if ext in VIDEO_EXTS else K_IMAGE if ext in IMAGE_EXTS
                                    else K_PDF if ext in PDF_EXTS else K_FILE)
                            if media_only and kind == K_FILE:
                                continue
                            it = FileItem(kind, Path(e.path), e.name, stt.st_size, stt.st_mtime)
                            it.setIcon(0, {K_VIDEO: w.ic_video, K_IMAGE: w.ic_image,
                                           K_PDF: w.ic_pdf}.get(kind, w.ic_file))
                        if e.name in self.marks:
                            it.set_marked(True)
                        items.append(it)
            except OSError as ex:
                QMessageBox.warning(self, "フォルダ", f"開けません:\n{ex}")
        tree.addTopLevelItems(items)
        tree.setSortingEnabled(True)
        self.marks = {it.name for it in items if it.marked}
        target = self.find(select_name) if select_name else None
        if target is None and items:
            # 「デスクトップ」「..」ではなく最初の中身を選ぶ
            rows = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
            target = next((r for r in rows if r.kind not in NAV_KINDS), rows[0])
        if target is not None:
            tree.setCurrentItem(target)
            tree.scrollToItem(target)
        tree.blockSignals(False)
        self.update_count()
        if had_focus or w.active is self:
            tree.setFocus()
        self.currentChanged.emit(self, tree.currentItem())


def _is_hidden(entry) -> bool:
    try:
        return bool(entry.stat().st_file_attributes & 2)  # Windows FILE_ATTRIBUTE_HIDDEN
    except (AttributeError, OSError):
        return False


class PsdWorker(QThread):
    """PSDの統合画像をPillowで読む（重いので別スレッド）"""
    loaded = Signal(int, QImage, int, int)   # gen, 画像, 元の幅, 元の高さ
    failed = Signal(int, str)

    def __init__(self, gen, path: str, box: QSize):
        super().__init__()
        self.gen, self.path, self.box = gen, path, box

    def run(self):
        try:
            from PIL import Image
        except ImportError:
            self.failed.emit(self.gen, "PSDの表示には pip install pillow が必要です")
            return
        try:
            with Image.open(self.path) as im:
                w, h = im.size
                im.load()
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                if im.width > self.box.width() or im.height > self.box.height():
                    im.thumbnail((max(1, self.box.width()), max(1, self.box.height())))
                im = im.convert("RGBA")
                data = im.tobytes("raw", "RGBA")
                img = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()
            self.loaded.emit(self.gen, img, w, h)
        except Exception as e:
            self.failed.emit(self.gen, f"このPSDは表示できません（{e}）")


class ImageView(QLabel):
    """静止画プレビュー（縦横比を保って枠に収める／GIFはアニメ再生）"""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(400, 225)
        self.setStyleSheet("background:#111; color:#888;")
        self._pix = None
        self._movie = None
        self._full = None   # 元画像サイズ

    def clear_image(self):
        if self._movie:
            self._movie.stop()
            self.setMovie(None)
            self._movie.deleteLater()
            self._movie = None
        self._pix = None
        self.clear()

    def load(self, path: Path):
        """表示して (幅, 高さ) を返す。失敗なら None"""
        self.clear_image()
        p = str(path)
        if path.suffix.lower() == ".gif":
            mv = QMovie(p)
            if mv.isValid() and mv.frameCount() > 1:
                self._movie = mv
                mv.jumpToFrame(0)
                self._full = mv.currentImage().size()
                self.setMovie(mv)
                self._fit_movie()
                mv.start()
                return self._full.width(), self._full.height()
            mv.deleteLater()
        r = QImageReader(p)
        r.setAutoTransform(True)          # EXIFの回転を反映
        size = r.size()
        if size.isValid():
            # 枠より大きければ縮小しながら読む（大きな写真でも軽い）
            box = self.size() * self.devicePixelRatioF() * 1.5
            rot = bool(r.transformation() & QImageIOHandler.Transformation.TransformationRotate90)
            src = size.transposed() if rot else size
            if src.width() > box.width() or src.height() > box.height():
                fit = src.scaled(box, Qt.KeepAspectRatio)
                r.setScaledSize(fit.transposed() if rot else fit)
            self._full = src
        img = r.read()
        if img.isNull():
            self.setText("この画像は表示できません")
            return None
        if not size.isValid():
            self._full = img.size()
        self._pix = QPixmap.fromImage(img)
        self._fit()
        return self._full.width(), self._full.height()

    def set_image(self, img: QImage, full=None):
        self.clear_image()
        self._full = full or img.size()
        self._pix = QPixmap.fromImage(img)
        self._fit()

    def set_message(self, text: str):
        self.clear_image()
        self.setText(text)

    def _fit(self):
        if self._pix is None:
            return
        dpr = self.devicePixelRatioF()
        target = self.size() * dpr
        pm = self._pix
        if pm.width() > target.width() or pm.height() > target.height():
            pm = pm.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.setPixmap(pm)

    def _fit_movie(self):
        if self._movie and self._full:
            s = self._full
            if s.width() > self.width() or s.height() > self.height():
                s = s.scaled(self.size(), Qt.KeepAspectRatio)
            self._movie.setScaledSize(s)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._movie:
            self._fit_movie()
        else:
            self._fit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShiwakeFiler")
        self.resize(1700, 900)
        self.settings = QSettings("ShiwakeFiler", "ShiwakeFiler")
        self.current: Path | None = None
        self.current_kind = None
        self.gen = 0
        self.worker: ThumbWorker | None = None
        self.op_worker: FileOpWorker | None = None
        self.thumb_cache: dict[str, list] = {}
        self.active = None

        st = self.style()
        prov = QFileIconProvider()
        self.ic_dir = prov.icon(QFileIconProvider.Folder)
        self.ic_drive = prov.icon(QFileIconProvider.Drive)
        self.ic_file = prov.icon(QFileIconProvider.File)
        self.ic_video = st.standardIcon(QStyle.SP_MediaPlay)
        self.ic_up = st.standardIcon(QStyle.SP_FileDialogToParent)
        self.ic_desk = st.standardIcon(QStyle.SP_DesktopIcon)
        self.ic_image = st.standardIcon(QStyle.SP_FileDialogContentsView)
        self.ic_pdf = st.standardIcon(QStyle.SP_FileDialogDetailedView)
        self.pdf = QPdfDocument(self)
        self.pdf_page = 0
        self.psd_worker = None
        self._desktop = None

        # --- プレイヤー
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setMinimumSize(400, 225)
        self.player.setVideoOutput(self.video)
        self.image_view = ImageView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.video)
        self.stack.addWidget(self.image_view)

        # --- 上部オプション
        self.chk_video_only = QCheckBox("対応ファイルのみ")
        self.chk_hidden = QCheckBox("隠しファイル")
        self.chk_auto = QCheckBox("選択で自動再生")
        self.chk_auto.setChecked(True)
        self.chk_mute = QCheckBox("ミュート")
        self.chk_mute.toggled.connect(self.audio.setMuted)
        top = QHBoxLayout()
        top.addWidget(QLabel("Space:マーク  M:移動  C:コピー  K:フォルダ作成  D:削除  L:ドライブ  R:リネーム  "
                             "←→:ペイン切替  P:再生/停止  Ctrl+←→:シーク"))
        top.addStretch(1)
        for w in (self.chk_video_only, self.chk_hidden, self.chk_auto, self.chk_mute):
            w.setFocusPolicy(Qt.NoFocus)
            top.addWidget(w)

        # --- 2ペイン
        self.pane_a = FilePane(self, "a")
        self.pane_b = FilePane(self, "b")
        self.panes = [self.pane_a, self.pane_b]
        for p in self.panes:
            p.currentChanged.connect(self.on_pane_current)
        self.chk_video_only.toggled.connect(lambda _: self.reload_all())
        self.chk_hidden.toggled.connect(lambda _: self.reload_all())

        # --- シークバー
        self.slider = ClickSlider(Qt.Horizontal)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.lbl_time = QLabel("00:00 / 00:00")
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedWidth(40)
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_play.clicked.connect(self.toggle_play)
        seek = QHBoxLayout()
        seek.addWidget(self.btn_play)
        seek.addWidget(self.slider, 1)
        seek.addWidget(self.lbl_time)

        # --- サムネイル帯
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListView.IconMode)
        self.thumbs.setFlow(QListView.LeftToRight)
        self.thumbs.setWrapping(False)
        self.thumbs.setMovement(QListView.Static)
        self.thumbs.setIconSize(QSize(THUMB_W, int(THUMB_W * 9 / 16)))
        self.thumbs.setFixedHeight(int(THUMB_W * 9 / 16) + 50)
        self.thumbs.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.thumbs.setSpacing(4)
        self.thumbs.setFocusPolicy(Qt.NoFocus)
        self.thumbs.itemClicked.connect(self.on_thumb_click)

        # --- リネーム欄
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("R/F2で編集 → Enterで確定 / Escで戻る")
        self.edit_name.returnPressed.connect(self.do_rename)
        self.edit_name.installEventFilter(self)
        self.lbl_ext = QLabel("")
        self.lbl_info = QLabel("")
        self.lbl_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ren = QHBoxLayout()
        ren.addWidget(QLabel("名前:"))
        ren.addWidget(self.edit_name, 1)
        ren.addWidget(self.lbl_ext)
        btn_ren = QPushButton("リネーム")
        btn_ren.setFocusPolicy(Qt.NoFocus)
        btn_ren.clicked.connect(self.do_rename)
        ren.addWidget(btn_ren)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(2, 2, 2, 2)
        rv.addWidget(self.stack, 1)
        self.seek_w = QWidget()
        seek.setContentsMargins(0, 0, 0, 0)
        self.seek_w.setLayout(seek)
        rv.addWidget(self.seek_w)
        rv.addWidget(self.thumbs)
        rv.addLayout(ren)
        rv.addWidget(self.lbl_info)

        split = QSplitter()
        split.addWidget(self.pane_a)
        split.addWidget(self.pane_b)
        split.addWidget(right)
        split.setSizes([480, 480, 740])

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.addLayout(top)
        cv.addWidget(split, 1)
        self.setCentralWidget(central)

        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.playbackStateChanged.connect(
            lambda s: self.btn_play.setText("❚❚" if s == QMediaPlayer.PlayingState else "▶"))

        QShortcut(QKeySequence("F2"), self, self.focus_rename)
        QShortcut(QKeySequence("F5"), self, self.reload_all)
        QShortcut(QKeySequence("Ctrl+L"), self,
                  lambda: (self.active.addr.setFocus(), self.active.addr.selectAll()))
        QShortcut(QKeySequence("Alt+Left"), self, lambda: self.active.go_back())
        QShortcut(QKeySequence("Alt+Right"), self, lambda: self.active.go_forward())
        QShortcut(QKeySequence("Alt+Up"), self, lambda: self.active.go_up())
        self.setAcceptDrops(True)
        self.set_active(self.pane_a)

    # ================= ペイン =================
    def other(self, pane=None):
        pane = pane or self.active
        return self.pane_b if pane is self.pane_a else self.pane_a

    def set_active(self, pane):
        if pane is self.active:
            return
        self.active = pane
        for p in self.panes:
            p.set_active(p is pane)
        self.on_select(pane.current_item())

    def on_pane_current(self, pane, item):
        if pane is self.active:
            self.on_select(item)

    def reload_all(self):
        for p in self.panes:
            p.reload()

    def desktop_path(self):
        if self._desktop is None:
            d = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
            self._desktop = Path(d) if d and Path(d).is_dir() else False
        return self._desktop or None

    def on_activate(self, pane, item):
        if item is None:
            return
        if item.kind == K_UP:
            pane.go_up()
        elif item.kind in (K_DIR, K_DRIVE, K_DESK):
            pane.navigate(item.path)
        else:
            self.focus_rename()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        p = Path(e.mimeData().urls()[0].toLocalFile())
        if p.is_dir():
            self.active.navigate(p)
        elif p.is_file():
            self.active.navigate(p.parent, select_name=p.name)

    def drive_menu(self):
        pane = self.active
        menu = QMenu(self)
        for d in QDir.drives():
            p = d.absoluteFilePath()
            act = menu.addAction(self.ic_drive, p)
            act.setData(p)
        menu.addSeparator()
        menu.addAction("PC（ドライブ一覧）").setData("")
        pos = pane.tree.mapToGlobal(QPoint(30, 30))
        act = menu.exec(pos)
        if act is not None:
            d = act.data()
            pane.navigate(Path(d) if d else None)

    # ================= 選択・再生 =================
    def clear_preview(self):
        self.stop_worker()
        self.stop_psd()
        self.gen += 1
        self.player.stop()
        self.player.setSource(QUrl())
        self.pdf.close()
        self.thumbs.clear()
        self.slider.setRange(0, 0)
        self.lbl_time.setText("00:00 / 00:00")
        self.image_view.clear_image()
        self.show_video_ui(True)

    def show_video_ui(self, video: bool, strip: bool | None = None):
        self.stack.setCurrentWidget(self.video if video else self.image_view)
        self.seek_w.setVisible(video)
        self.thumbs.setVisible(video if strip is None else strip)

    def preview_box(self) -> QSize:
        """プレビュー枠の実ピクセル×1.5（拡大しても粗くならない程度）"""
        s = self.image_view.size() * self.image_view.devicePixelRatioF() * 1.5
        return QSize(max(400, s.width()), max(300, s.height()))

    # ---------- PSD
    def stop_psd(self):
        if self.psd_worker is not None:
            self.psd_worker.wait()      # 読み込み中のファイルを離すまで待つ
            self.psd_worker = None

    def show_psd(self, path: Path, size: int):
        self.image_view.set_message("PSD 読み込み中…")
        gen = self.gen
        w = PsdWorker(gen, str(path), self.preview_box())

        def ok(g, img, fw, fh):
            if g == self.gen:
                self.image_view.set_image(img, QSize(fw, fh))
                self.lbl_info.setText(f"{fw}×{fh}   {fmt_size(size)}   {path}")

        def ng(g, msg):
            if g == self.gen:
                self.image_view.set_message(msg)
        w.loaded.connect(ok)
        w.failed.connect(ng)
        self.psd_worker = w
        w.start()

    # ---------- PDF
    def show_pdf(self, path: Path, size: int):
        self.pdf.load(str(path))
        n = self.pdf.pageCount()
        if self.pdf.status() != QPdfDocument.Status.Ready or n <= 0:
            self.image_view.set_message("このPDFは表示できません（パスワード付き・破損など）")
            return
        self.pdf_info = f"{fmt_size(size)}   {path}"
        self.thumbs.setIconSize(QSize(PDF_THUMB_H, PDF_THUMB_H))
        for i in range(n):
            it = QListWidgetItem(f"p.{i + 1}")
            it.setData(Qt.UserRole, i)
            it.setSizeHint(QSize(PDF_THUMB_H + 10, PDF_THUMB_H + 30))
            self.thumbs.addItem(it)
        self.show_pdf_page(0)
        self._pdf_thumb_step(self.gen, 0)

    def show_pdf_page(self, i: int):
        n = self.pdf.pageCount()
        if n <= 0:
            return
        i = max(0, min(i, n - 1))
        self.pdf_page = i
        ps = self.pdf.pagePointSize(i)
        box = self.preview_box()
        sz = QSize(max(1, int(ps.width())), max(1, int(ps.height()))).scaled(box, Qt.KeepAspectRatio)
        img = self.pdf.render(i, sz)
        self.image_view.set_image(img, sz)
        self.lbl_info.setText(f"p.{i + 1} / {n}   {self.pdf_info}")
        self.thumbs.blockSignals(True)
        self.thumbs.setCurrentRow(i)
        self.thumbs.scrollToItem(self.thumbs.item(i))
        self.thumbs.blockSignals(False)

    def _pdf_thumb_step(self, gen: int, i: int):
        """ページの縮小画像を1枚ずつ作る（操作を止めないように少しずつ）"""
        if gen != self.gen or i >= self.pdf.pageCount():
            return
        ps = self.pdf.pagePointSize(i)
        dpr = self.thumbs.devicePixelRatioF()
        box = QSize(int(PDF_THUMB_H * dpr), int(PDF_THUMB_H * dpr))
        sz = QSize(max(1, int(ps.width())), max(1, int(ps.height()))).scaled(box, Qt.KeepAspectRatio)
        img = self.pdf.render(i, sz)
        it = self.thumbs.item(i)
        if it is not None and not img.isNull():
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)
            it.setIcon(QIcon(pm))
        QTimer.singleShot(0, lambda: self._pdf_thumb_step(gen, i + 1))

    def on_select(self, item):
        if item is None or item.kind in (K_UP, K_DRIVE, K_DESK):
            self.current = None
            self.current_kind = item.kind if item is not None else None
            self.clear_preview()
            self.edit_name.setText("")
            self.lbl_ext.setText("")
            self.lbl_info.setText(str(item.path) if item is not None and item.path else "")
            return
        path = item.path
        if path == self.current and item.kind == K_VIDEO and self.current_kind == K_VIDEO \
                and self.player.source().isValid():
            return  # 同じ動画を選び直しただけ（再読み込み時など）
        self.current, self.current_kind = path, item.kind
        if item.kind == K_DIR:
            self.edit_name.setText(path.name)
            self.lbl_ext.setText("")
            self.lbl_info.setText(f"フォルダ   {path}")
            self.clear_preview()
            return
        self.edit_name.setText(path.stem)
        self.lbl_ext.setText(path.suffix)
        self.lbl_info.setText(f"{fmt_size(item.size)}   {path}")
        if item.kind == K_VIDEO:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            if self.chk_auto.isChecked():
                self.player.play()
            else:
                self.player.pause()
            self.start_thumbs(path)
        elif item.kind == K_PDF:
            self.clear_preview()
            self.show_video_ui(False, strip=True)
            self.show_pdf(path, item.size)
        elif item.kind == K_IMAGE and path.suffix.lower() == ".psd":
            self.clear_preview()
            self.show_video_ui(False)
            self.show_psd(path, item.size)
        elif item.kind == K_IMAGE:
            self.clear_preview()
            self.show_video_ui(False)
            wh = self.image_view.load(path)
            if wh:
                self.lbl_info.setText(f"{wh[0]}×{wh[1]}   {fmt_size(item.size)}   {path}")
            else:
                self.lbl_info.setText(f"（表示できない画像）   {fmt_size(item.size)}   {path}")
        else:
            self.clear_preview()

    def release_file(self):
        """リネーム・移動・削除前にファイルを掴んでいるものを全部離す"""
        self.stop_worker()
        self.stop_psd()
        self.gen += 1
        self.player.stop()
        self.player.setSource(QUrl())
        self.pdf.close()
        self.image_view.clear_image()
        self.current = None
        QApplication.processEvents()

    def toggle_play(self):
        if self.current_kind != K_VIDEO:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_rel(self, ms: int):
        if self.current_kind == K_PDF:
            self.show_pdf_page(self.pdf_page + (1 if ms > 0 else -1))
            return
        if self.current_kind != K_VIDEO:
            return
        dur = self.player.duration()
        pos = self.player.position() + ms
        self.player.setPosition(max(0, min(pos, dur - 100 if dur else pos)))

    def on_duration(self, d):
        self.slider.setRange(0, d)

    def on_position(self, pos):
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        self.lbl_time.setText(f"{fmt_time(pos)} / {fmt_time(self.player.duration())}")
        if self.current_kind != K_VIDEO:
            return
        # 現在位置に対応するサムネイルをハイライト
        best = -1
        for i in range(self.thumbs.count()):
            t = self.thumbs.item(i).data(Qt.UserRole)
            if t is not None and t * 1000 <= pos + 50:
                best = i
        if best >= 0 and best != self.thumbs.currentRow():
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(best)
            self.thumbs.scrollToItem(self.thumbs.item(best))
            self.thumbs.blockSignals(False)

    # ---------- サムネイル
    def stop_worker(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(3000)
        self.worker = None

    def start_thumbs(self, path: Path):
        self.stop_worker()
        self.gen += 1
        self.thumbs.clear()
        self.thumbs.setIconSize(QSize(THUMB_W, int(THUMB_W * 9 / 16)))
        for i in range(THUMB_COUNT):
            it = QListWidgetItem("…")
            it.setSizeHint(QSize(THUMB_W + 8, int(THUMB_W * 9 / 16) + 30))
            self.thumbs.addItem(it)
        key = str(path)
        if key in self.thumb_cache:
            for i, sec, img in self.thumb_cache[key]:
                self.set_thumb(i, sec, img)
            return
        self.thumb_cache[key] = []
        w = ThumbWorker(self.gen, key, THUMB_COUNT)
        w.thumb.connect(self.on_thumb)
        self.worker = w
        w.start()

    def on_thumb(self, gen, i, sec, img):
        if gen != self.gen or not self.current:
            return
        self.thumb_cache.setdefault(str(self.current), []).append((i, sec, img))
        self.set_thumb(i, sec, img)

    def set_thumb(self, i, sec, img):
        it = self.thumbs.item(i)
        if it:
            it.setIcon(QIcon(QPixmap.fromImage(img)))
            it.setText(fmt_time(int(sec * 1000)))
            it.setData(Qt.UserRole, sec)

    def on_thumb_click(self, item):
        if self.current_kind == K_PDF:
            self.show_pdf_page(item.data(Qt.UserRole))
            return
        sec = item.data(Qt.UserRole)
        if sec is not None:
            self.player.setPosition(int(sec * 1000))


    # ================= ファイル操作 =================
    def focus_rename(self):
        if self.current:
            self.edit_name.setFocus()
            self.edit_name.selectAll()

    def do_rename(self):
        pane = self.active
        if not self.current:
            return
        is_dir = self.current_kind == K_DIR
        old = self.current
        new_text = self.edit_name.text().strip()
        cur_text = old.name if is_dir else old.stem
        if not new_text or new_text == cur_text:
            pane.tree.setFocus()
            return
        if any(c in BAD_CHARS for c in new_text):
            QMessageBox.warning(self, "リネーム", '使えない文字が含まれています: \\ / : * ? " < > |')
            return
        new_path = old.with_name(new_text if is_dir else new_text + old.suffix)
        if new_path.exists() and new_path.name.lower() != old.name.lower():
            QMessageBox.warning(self, "リネーム", f"同名のものがあります:\n{new_path.name}")
            return
        was_video = self.current_kind == K_VIDEO
        pos = self.player.position()
        self.release_file()
        try:
            os.rename(old, new_path)
        except OSError as e:
            QMessageBox.critical(self, "リネーム失敗", str(e))
            self.reload_all()
            return
        if str(old) in self.thumb_cache:
            self.thumb_cache[str(new_path)] = self.thumb_cache.pop(str(old))
        if old.name in pane.marks:
            pane.marks.discard(old.name)
            pane.marks.add(new_path.name)
        self.statusBar().showMessage(f"リネーム: {old.name} → {new_path.name}", 5000)
        pane.load_folder(pane.folder, new_path.name)
        if self.other(pane).folder == pane.folder:
            self.other(pane).reload()
        pane.tree.setFocus()
        if was_video:
            self.player.setPosition(pos)

    def do_mkdir(self):
        pane = self.active
        if pane.folder is None:
            return
        name, ok = QInputDialog.getText(self, "フォルダ作成", f"{pane.folder} に作成:", text="新しいフォルダ")
        name = name.strip()
        if not ok or not name:
            return
        if any(c in BAD_CHARS for c in name):
            QMessageBox.warning(self, "フォルダ作成", '使えない文字が含まれています: \\ / : * ? " < > |')
            return
        try:
            (pane.folder / name).mkdir()
        except OSError as e:
            QMessageBox.critical(self, "フォルダ作成失敗", str(e))
            return
        pane.load_folder(pane.folder, name)
        if self.other(pane).folder == pane.folder:
            self.other(pane).reload()

    def do_delete(self):
        pane = self.active
        targets = pane.targets()
        if not targets:
            return
        if send2trash is None:
            QMessageBox.information(self, "削除", "pip install send2trash で有効になります")
            return
        msg = (f"「{targets[0].name}」" if len(targets) == 1 else f"{len(targets)}件") + "をごみ箱へ移動しますか？"
        if QMessageBox.question(self, "ごみ箱へ移動", msg) != QMessageBox.Yes:
            return
        keep = pane.neighbor_name(targets)
        self.release_file()
        errors = []
        for t in targets:
            try:
                send2trash(str(t))
            except Exception as e:
                errors.append(f"{t.name}: {e}")
            self.thumb_cache.pop(str(t), None)
        pane.marks.clear()
        pane.load_folder(pane.folder, keep)
        if self.other(pane).folder == pane.folder:
            self.other(pane).reload()
        if errors:
            QMessageBox.warning(self, "削除", "失敗したもの:\n" + "\n".join(errors))

    def do_transfer(self, mode: str, ask=True):
        """mode = 'move' / 'copy' ：アクティブ → 反対ペイン"""
        if self.op_worker is not None:
            return
        src_pane, dst_pane = self.active, self.other()
        dst = dst_pane.folder
        verb = "移動" if mode == "move" else "コピー"
        if dst is None:
            QMessageBox.information(self, verb, "反対側のペインでフォルダを開いてください")
            return
        sources = src_pane.targets()
        if not sources:
            return
        if src_pane.folder == dst:
            QMessageBox.information(self, verb, "反対側のペインが同じフォルダです")
            return
        bad = [s for s in sources if s.is_dir() and (dst == s or s in dst.parents)]
        if bad:
            QMessageBox.warning(self, verb, f"フォルダを自分自身の中には{verb}できません:\n{bad[0].name}")
            return
        conflicts = [s for s in sources if (dst / s.name).exists()]
        if conflicts:
            box = QMessageBox(self)
            box.setWindowTitle(verb)
            names = "\n".join(c.name for c in conflicts[:10]) + ("\n…" if len(conflicts) > 10 else "")
            box.setText(f"{dst} に同名のものが {len(conflicts)}件 あります。\n{names}")
            b_over = box.addButton("上書き", QMessageBox.AcceptRole)
            b_skip = box.addButton("スキップ", QMessageBox.ActionRole)
            box.addButton("中止", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b_skip:
                sources = [s for s in sources if s not in conflicts]
            elif box.clickedButton() is not b_over:
                return
            if not sources:
                return
        elif ask:
            what = f"「{sources[0].name}」" if len(sources) == 1 else f"{len(sources)}件"
            if QMessageBox.question(self, verb, f"{what}を\n{dst}\nへ{verb}しますか？") != QMessageBox.Yes:
                return

        keep = src_pane.neighbor_name(sources) if mode == "move" else src_pane._cur_name()
        if mode == "move":
            self.release_file()
        dlg = QProgressDialog(f"{verb}中…", "中止", 0, 1000, self)
        dlg.setWindowTitle(verb)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)
        dlg.setMinimumWidth(480)
        w = FileOpWorker(mode, sources, dst)
        self.op_worker = w

        def on_progress(v, name):
            dlg.setValue(v)
            dlg.setLabelText(f"{verb}中: {name}")

        def on_done(errors, cancelled):
            dlg.reset()
            dlg.close()
            w.wait()
            self.op_worker = None
            for s in sources:
                self.thumb_cache.pop(str(s), None)
            src_pane.marks.clear()
            src_pane.load_folder(src_pane.folder, keep)
            dst_pane.reload()
            src_pane.tree.setFocus()
            self.on_select(src_pane.current_item())
            msg = f"{verb}{'を中止しました' if cancelled else '完了'}: {len(sources)}件 → {dst}"
            self.statusBar().showMessage(msg, 6000)
            if errors:
                QMessageBox.warning(self, verb, "失敗したもの:\n" + "\n".join(errors[:30]))

        w.progress.connect(on_progress)
        w.done.connect(on_done)
        dlg.canceled.connect(w.requestInterruption)
        w.start()

    # ================= キー操作 =================
    def eventFilter(self, obj, ev):
        et = ev.type()
        if et == QEvent.FocusIn:
            for p in self.panes:
                if obj is p.tree or obj is p.addr:
                    self.set_active(p)
        if et != QEvent.KeyPress:
            return super().eventFilter(obj, ev)
        k, mod = ev.key(), ev.modifiers()

        if obj is self.edit_name:
            if k == Qt.Key_Escape:
                if self.current:
                    self.edit_name.setText(
                        self.current.name if self.current_kind == K_DIR else self.current.stem)
                self.active.tree.setFocus()
                return True
            return False

        pane = next((p for p in self.panes if obj is p.tree), None)
        if pane is None:
            if k == Qt.Key_Escape:  # アドレス欄から戻る
                self.active.tree.setFocus()
                return True
            return False
        if mod & Qt.AltModifier:
            return False  # Alt+←→↑ はショートカットへ

        if k in (Qt.Key_Left, Qt.Key_Right):
            if mod & Qt.ControlModifier:   # Ctrl+←→ : シーク（+Shiftで1秒刻み）
                step = 1000 if mod & Qt.ShiftModifier else 5000
                self.seek_rel(step if k == Qt.Key_Right else -step)
            else:                          # ←→ : 左右ペインへ
                (self.pane_a if k == Qt.Key_Left else self.pane_b).tree.setFocus()
            return True
        if k == Qt.Key_A and mod & Qt.ControlModifier:
            pane.mark_all()
            return True
        if mod & Qt.ControlModifier:
            return False
        if k in (Qt.Key_Tab, Qt.Key_Backtab):
            self.other(pane).tree.setFocus()
            return True
        if k == Qt.Key_Space:
            pane.toggle_mark()
            return True
        if k == PLAY_KEY:
            self.toggle_play()
            return True
        if k == Qt.Key_M:
            self.do_transfer("move")
            return True
        if k == Qt.Key_C:
            self.do_transfer("copy")
            return True
        if k == Qt.Key_K:
            self.do_mkdir()
            return True
        if k in (Qt.Key_D, Qt.Key_Delete):
            self.do_delete()
            return True
        if k == Qt.Key_L:
            self.drive_menu()
            return True
        if k == Qt.Key_R:
            self.focus_rename()
            return True
        if k == Qt.Key_O:
            self.other(pane).navigate(pane.folder)
            return True
        if k == Qt.Key_Backspace:
            pane.go_up()
            return True
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.on_activate(pane, pane.current_item())
            return True
        return False

    def closeEvent(self, e):
        if self.op_worker is not None:
            self.op_worker.requestInterruption()
            self.op_worker.wait(5000)
        self.stop_worker()
        self.player.stop()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    for p in w.panes:
        last = w.settings.value(f"last_folder_{p.key}", "") or w.settings.value("last_folder", "")
        if p is w.pane_a and len(sys.argv) > 1:
            last = sys.argv[1]
        start = Path(last) if last else None
        if start and start.is_file():
            p.navigate(start.parent, select_name=start.name, record=False)
        else:
            p.navigate(start if start and start.is_dir() else None, record=False)
    w.pane_a.tree.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
