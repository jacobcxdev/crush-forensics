# SPDX-License-Identifier: Apache-2.0
"""Visible image/video previews, decoded off-thread and cached in memory."""
from __future__ import annotations

import io
import threading
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView

from crush.core.pil_plugins import ensure_pil_plugins
from crush.core.vfs import VFS, VFSNode, DirectoryVFS, FileVFS, RawImageVFS, SevenZipVFS
from crush.core.work_priority import background_io

_SIZE = 160
_MAX_FILE_BYTES = 32 << 20
_MAX_PIXELS = 40_000_000
_CACHE_ENTRIES = 256
_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".3gp", ".3g2", ".mpeg", ".mpg", ".ts"}


def _looks_like_video(head: bytes, node: VFSNode) -> bool:
    return (node.extension.lower() in _VIDEO_EXTENSIONS or head[4:8] == b"ftyp"
            or head[:4] == b"\x1aE\xdf\xa3" or
            (head[:4] == b"RIFF" and head[8:12] == b"AVI "))


class _PreviewStream(io.RawIOBase):
    """Limit decoder I/O, and defer size-probing seeks until bytes are read."""

    def __init__(self, stream, size: int, *, cheap_seeks: bool = False) -> None:
        super().__init__()
        self._stream = stream
        self._size = size
        self._position = 0
        self._remaining = _MAX_FILE_BYTES
        self._deadline = time.monotonic() + 5
        self._cheap_seeks = cheap_seeks or isinstance(stream, io.BytesIO)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return self._stream.seekable()

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        position = offset + (self._size if whence == io.SEEK_END else
                             self._position if whence == io.SEEK_CUR else 0)
        if position < 0:
            raise ValueError("negative preview seek")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        if time.monotonic() > self._deadline or self._remaining <= 0:
            raise OSError("Video preview I/O limit reached")
        current = self._stream.tell()
        if not self._cheap_seeks:
            # Archive and walker streams may seek by reading/discarding.
            cost = self._position - current if self._position >= current else self._position
            if cost > self._remaining:
                raise OSError("Video preview seek limit reached")
            self._remaining -= cost
        with background_io():
            if current != self._position:
                self._stream.seek(self._position)
            data = self._stream.read(min(self._remaining, size) if size >= 0 else self._remaining)
        self._remaining -= len(data)
        self._position += len(data)
        return data

    def close(self) -> None:
        self._stream.close()
        super().close()


def _read_video_thumbnail(stream, size: int, *, cheap_seeks: bool = False) -> QImage:
    import av

    with _PreviewStream(stream, size, cheap_seeks=cheap_seeks) as reader:
        with av.open(reader, mode="r", options={
            "probesize": "1048576", "analyzeduration": "1000000",
            "protocol_whitelist": "pipe",
        }) as container:
            if not container.streams.video:
                return QImage()
            video = container.streams.video[0]
            if video.codec_context.width * video.codec_context.height > _MAX_PIXELS:
                return QImage()
            for frame in container.decode(video):
                if frame.width * frame.height > _MAX_PIXELS:
                    return QImage()
                ratio = min(_SIZE / frame.width, _SIZE / frame.height, 1)
                rgba = frame.reformat(width=max(1, int(frame.width * ratio)),
                                      height=max(1, int(frame.height * ratio)), format="rgba")
                plane = rgba.planes[0]
                return QImage(bytes(plane), rgba.width, rgba.height, plane.line_size,
                              QImage.Format.Format_RGBA8888).copy()
    return QImage()


def _read_thumbnail(node: VFSNode, vfs: VFS) -> QImage:
    """Reduce one image/video without exporting it or creating a temp file."""
    if node.is_dir or node.size <= 0:
        return QImage()
    try:
        from PIL import Image, ImageOps

        from crush.parsers.image_parser import _looks_like_image

        stream = None
        with background_io():
            source, entry = vfs.delegate(node)
            if isinstance(source, SevenZipVFS):
                if node.size > _MAX_FILE_BYTES:
                    return QImage()  # Its large-file open() would spill to disk.
                # Neither read() nor peek() may retain the original bytes for
                # every image scrolled past in a large archive.
                raw = source.read_uncached(entry)
                if _looks_like_video(raw[:512], node):
                    stream = io.BytesIO(raw)
            else:
                head = vfs.peek(node, 512)
                if _looks_like_video(head, node):
                    if node.size > _MAX_FILE_BYTES and not isinstance(source, (DirectoryVFS, FileVFS, RawImageVFS)):
                        # Other VFS open() implementations may buffer a whole
                        # member or stage decrypted bytes before returning.
                        return QImage()
                    # Decode through the seekable VFS stream, including large
                    # videos, without loading/caching the entire original.
                    stream = (source.open(entry, streaming=True) if isinstance(source, RawImageVFS)
                              else source.open(entry))
                else:
                    stream = None
                if stream is None and (node.size > _MAX_FILE_BYTES or not _looks_like_image(head)):
                    return QImage()
                if stream is None:
                    raw = vfs.read(node)
        if stream is not None:
            return _read_video_thumbnail(stream, node.size,
                                         cheap_seeks=isinstance(source, (DirectoryVFS, FileVFS)))
        if not _looks_like_image(raw[:512]):
            return QImage()
        if len(raw) > _MAX_FILE_BYTES:
            return QImage()
        ensure_pil_plugins()
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > _MAX_PIXELS:
                return QImage()
            image.draft("RGB", (_SIZE, _SIZE))
            image.thumbnail((_SIZE, _SIZE))
            rgba = ImageOps.exif_transpose(image).convert("RGBA")
            pixels = rgba.tobytes()
            return QImage(pixels, rgba.width, rgba.height, rgba.width * 4,
                          QImage.Format.Format_RGBA8888).copy()
    except Exception:
        # Unsupported/corrupt files retain their normal row presentation.
        return QImage()


class ThumbnailPreviews(QObject):
    _ready = Signal(object, int, object)

    def __init__(self, views: tuple[QAbstractItemView, ...], node_role: int, vfs_role: int,
                 parent: QObject) -> None:
        super().__init__(parent)
        self.enabled = False
        self._views = views
        self._cache: OrderedDict[tuple[int, str], QImage] = OrderedDict()
        self._generation = 0
        self._busy = False
        self._ready.connect(self._on_ready)
        for view in views:
            delegate = _ThumbnailDelegate(self, node_role, vfs_role, view)
            if isinstance(view, QTreeView):
                view.setItemDelegateForColumn(0, delegate)
            else:
                view.setItemDelegate(delegate)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.clear()
        for view in self._views:
            view.doItemsLayout()

    def clear(self) -> None:
        self._generation += 1
        self._cache.clear()
        self._update_views()

    def _update_views(self) -> None:
        for view in self._views:
            view.viewport().update()

    def icon(self, node: VFSNode, vfs: VFS, *, request: bool = True) -> QIcon:
        if not self.enabled or node.is_dir:
            return QIcon()
        key = (id(vfs), node.path)
        if key in self._cache:
            image = self._cache.pop(key)
            self._cache[key] = image
            return QIcon() if image.isNull() else QIcon(QPixmap.fromImage(image))
        if request and not self._busy:
            self._busy = True
            generation = self._generation

            def load() -> None:
                image = _read_thumbnail(node, vfs)
                try:
                    self._ready.emit(key, generation, image)
                except RuntimeError:
                    pass  # The owning panel was destroyed while reading.

            threading.Thread(target=load, daemon=True).start()
        return QIcon()

    def _on_ready(self, key: tuple[int, str], generation: int, image: QImage) -> None:
        self._busy = False
        if self.enabled and generation == self._generation:
            self._cache[key] = image
            while len(self._cache) > _CACHE_ENTRIES:
                self._cache.popitem(last=False)
        self._update_views()


class _ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, previews: ThumbnailPreviews, node_role: int, vfs_role: int,
                 view: QAbstractItemView) -> None:
        super().__init__(view)
        self._previews = previews
        self._node_role = node_role
        self._vfs_role = vfs_role
        self._view = view
        self._grid = isinstance(view, QListView)

    def initStyleOption(self, option, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        if self._grid:
            node = index.data(self._node_role)
            kind = QStyle.StandardPixmap.SP_DirIcon if node is not None and node.is_dir else QStyle.StandardPixmap.SP_FileIcon
            option.icon = self._view.style().standardIcon(kind)
            option.decorationSize = QSize(128, 128)
            option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
        if not self._previews.enabled:
            return
        node, vfs = index.data(self._node_role), index.data(self._vfs_role)
        if node is not None and vfs is not None and not node.is_dir:
            icon = self._previews.icon(node, vfs, request=False)
            if not icon.isNull():
                option.icon = icon
                option.decorationSize = QSize(128, 128) if self._grid else QSize(48, 48)
                option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration

    def paint(self, painter, option, index) -> None:
        node, vfs = index.data(self._node_role), index.data(self._vfs_role)
        if node is not None and vfs is not None:
            self._previews.icon(node, vfs)
        super().paint(painter, option, index)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        if self._grid:
            return QSize(160, 180)
        if self._previews.enabled:
            size.setHeight(max(size.height(), 52))
        return size
