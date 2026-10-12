# SPDX-License-Identifier: Apache-2.0
"""Thumbnail latency, cancellation and bounded scheduling on public data."""
from __future__ import annotations

import threading

import pytest
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QListView

from crush.core.vfs import DirectoryVFS
from crush.ui.image_thumbnails import ThumbnailPreviews, _read_thumbnail


@pytest.fixture
def previews(qtbot, tmp_path):
    for i in range(12):
        Image.new("RGB", (160, 80), "red").save(tmp_path / f"photo-{i:02}.jpg")
    vfs = DirectoryVFS(tmp_path)
    view = QListView()
    qtbot.addWidget(view)
    manager = ThumbnailPreviews((view,), 1001, 1002, view)
    manager.set_enabled(True)
    yield manager, vfs, vfs.root().children
    manager.set_enabled(False)
    qtbot.waitUntil(lambda: not manager._busy)
    vfs.close()


def test_slow_thumbnail_does_not_block_other_visible_media(previews, qtbot, monkeypatch):
    manager, vfs, nodes = previews
    started, release = threading.Event(), threading.Event()
    read = vfs.read

    def slow_first(node):
        if node is nodes[0]:
            started.set()
            release.wait(5)
        return read(node)

    monkeypatch.setattr(vfs, "read", slow_first)
    try:
        manager.icon(nodes[0], vfs)
        qtbot.waitUntil(started.is_set)
        manager.icon(nodes[1], vfs)
        qtbot.waitUntil(lambda: (id(vfs), nodes[1].path) in manager._cache, timeout=1000)
        assert not release.is_set()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not manager._busy)


def test_worker_count_is_bounded_and_duplicate_requests_read_once(previews, qtbot, monkeypatch):
    manager, vfs, nodes = previews
    release = threading.Event()
    started = []
    read = vfs.read

    def blocked(node):
        started.append(node.path)
        release.wait(5)
        return read(node)

    monkeypatch.setattr(vfs, "read", blocked)
    try:
        for _ in range(3):
            for node in nodes:
                manager.icon(node, vfs)
        qtbot.waitUntil(lambda: len(started) >= 4, timeout=1000)
        assert len(started) == 4
        release.set()
        qtbot.waitUntil(lambda: len(manager._cache) == len(nodes))
        assert sorted(started) == sorted(node.path for node in nodes)
    finally:
        release.set()


def test_disabling_previews_skips_decode_after_inflight_read(previews, qtbot, monkeypatch):
    manager, vfs, nodes = previews
    started, release = threading.Event(), threading.Event()
    read = vfs.read
    decoded = []
    image_open = Image.open

    def blocked(node):
        started.set()
        release.wait(5)
        return read(node)

    def tracked_open(*args, **kwargs):
        decoded.append(True)
        return image_open(*args, **kwargs)

    monkeypatch.setattr(vfs, "read", blocked)
    monkeypatch.setattr(Image, "open", tracked_open)
    try:
        manager.icon(nodes[0], vfs)
        qtbot.waitUntil(started.is_set)
        manager.set_enabled(False)
        release.set()
        qtbot.waitUntil(lambda: not manager._busy)
        assert not decoded
        assert not manager._cache
    finally:
        release.set()


def test_cached_preview_reuses_its_gui_icon(previews, qtbot):
    manager, vfs, nodes = previews
    manager.icon(nodes[0], vfs)
    qtbot.waitUntil(lambda: (id(vfs), nodes[0].path) in manager._cache)
    first = manager.icon(nodes[0], vfs)
    for _ in range(10):
        assert manager.icon(nodes[0], vfs).cacheKey() == first.cacheKey()


def test_cancel_during_sniff_does_not_read_full_file(previews, qtbot, monkeypatch):
    manager, vfs, nodes = previews
    started, release = threading.Event(), threading.Event()
    peek, read = vfs.peek, vfs.read
    reads = []

    def blocked_peek(node, n=32):
        started.set()
        release.wait(5)
        return peek(node, n)

    def tracked_read(node):
        reads.append(node.path)
        return read(node)

    monkeypatch.setattr(vfs, "peek", blocked_peek)
    monkeypatch.setattr(vfs, "read", tracked_read)
    try:
        manager.icon(nodes[0], vfs)
        qtbot.waitUntil(started.is_set)
        manager.set_enabled(False)
        release.set()
        qtbot.waitUntil(lambda: not manager._busy)
        assert not reads
    finally:
        release.set()
        qtbot.waitUntil(lambda: not manager._busy)


def test_jpeg_preview_does_not_load_optional_decoders(previews, monkeypatch):
    from crush.ui import image_thumbnails
    _, vfs, nodes = previews
    monkeypatch.setattr(image_thumbnails, "ensure_pil_plugins",
                        lambda: pytest.fail("JPEG should not initialize HEIF/JXL decoders"))
    assert not _read_thumbnail(nodes[0], vfs).isNull()


def test_scrolling_discards_offscreen_work(previews, qtbot, monkeypatch):
    manager, vfs, nodes = previews
    view = manager._views[0]
    model = QStandardItemModel(view)
    for node in nodes:
        item = QStandardItem(node.name)
        item.setData(node, 1001)
        item.setData(vfs, 1002)
        model.appendRow(item)
    view.setModel(model)
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setGridSize(QSize(160, 180))
    view.resize(520, 260)
    started = []
    release = threading.Event()
    read = vfs.read

    def blocked(node):
        started.append(node.path)
        release.wait(5)
        return read(node)

    monkeypatch.setattr(vfs, "read", blocked)
    view.show()
    try:
        qtbot.waitUntil(lambda: len(started) == 4)
        old_started = set(started)
        old_pending = {nodes[i].path for i in range(len(nodes))
                       if view.visualRect(model.index(i, 0)).intersects(view.viewport().rect())} - old_started
        last = model.index(len(nodes) - 1, 0)
        view.scrollTo(last, QListView.ScrollHint.PositionAtTop)
        qtbot.waitUntil(lambda: (id(vfs), nodes[-1].path) in manager._pending)
        release.set()
        qtbot.waitUntil(lambda: (id(vfs), nodes[-1].path) in manager._cache)
        assert not old_pending.intersection(started)
        assert not old_started.intersection(path for _, path in manager._cache)
    finally:
        release.set()
        qtbot.waitUntil(lambda: not manager._busy)
