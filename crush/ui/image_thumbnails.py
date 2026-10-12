# SPDX-License-Identifier: Apache-2.0
"""Visible-row image previews, decoded off the GUI thread and cached in memory."""
from __future__ import annotations

import io
import threading
from collections import OrderedDict

from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QTreeView

from crush.core.pil_plugins import ensure_pil_plugins
from crush.core.vfs import VFS, VFSNode, SevenZipVFS
from crush.core.work_priority import background_io

_SIZE = 48
_MAX_FILE_BYTES = 32 << 20
_MAX_PIXELS = 40_000_000
_CACHE_ENTRIES = 256


def _read_thumbnail(node: VFSNode, vfs: VFS) -> QImage:
    """Read and reduce one image without exporting it or creating a temp file."""
    if node.is_dir or not 0 < node.size <= _MAX_FILE_BYTES:
        return QImage()
    try:
        from PIL import Image, ImageOps

        from crush.parsers.image_parser import _looks_like_image

        with background_io():
            source, entry = vfs.delegate(node)
            if isinstance(source, SevenZipVFS):
                # Neither read() nor peek() may retain the original bytes for
                # every image scrolled past in a large archive.
                raw = source.read_uncached(entry)
            else:
                if not _looks_like_image(vfs.peek(node, 512)):
                    return QImage()
                raw = vfs.read(node)
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

    def __init__(self, views: tuple[QTreeView, ...], node_role: int, vfs_role: int,
                 parent: QObject) -> None:
        super().__init__(parent)
        self.enabled = False
        self._views = views
        self._cache: OrderedDict[tuple[int, str], QImage] = OrderedDict()
        self._generation = 0
        self._busy = False
        self._ready.connect(self._on_ready)
        for view in views:
            view.setItemDelegateForColumn(0, _ThumbnailDelegate(self, node_role, vfs_role, view))

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
                 view: QTreeView) -> None:
        super().__init__(view)
        self._previews = previews
        self._node_role = node_role
        self._vfs_role = vfs_role

    def initStyleOption(self, option, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        if not self._previews.enabled:
            return
        node, vfs = index.data(self._node_role), index.data(self._vfs_role)
        if node is not None and vfs is not None and not node.is_dir:
            icon = self._previews.icon(node, vfs, request=False)
            if not icon.isNull():
                option.icon = icon
                option.decorationSize = QSize(_SIZE, _SIZE)
                option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration

    def paint(self, painter, option, index) -> None:
        node, vfs = index.data(self._node_role), index.data(self._vfs_role)
        if node is not None and vfs is not None:
            self._previews.icon(node, vfs)
        super().paint(painter, option, index)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        if self._previews.enabled:
            size.setHeight(max(size.height(), _SIZE + 4))
        return size
