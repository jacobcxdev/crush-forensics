# SPDX-License-Identifier: Apache-2.0
"""Opt-in previews use public generated images and isolated settings."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyleOptionViewItem

from crush.core.session import Session
from crush.core.vfs import DirectoryVFS
from crush.ui.fs_panel import FilesystemPanel


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    from crush.ui import main_window

    def settings(*args, **kwargs):
        return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)

    monkeypatch.setattr(main_window, "QSettings", settings)
    return settings()


@pytest.fixture
def image_folder(tmp_path: Path) -> Path:
    Image.new("RGB", (160, 80), "red").save(tmp_path / "photo.png")
    (tmp_path / "broken.png").write_bytes(b"invalid public image")
    hidden = tmp_path / "collapsed"
    hidden.mkdir()
    Image.new("RGB", (80, 160), "blue").save(hidden / "hidden.png")
    for i in range(40):
        (tmp_path / f"z-row-{i:02}.txt").write_text("public offscreen row")
    Image.new("RGB", (80, 80), "green").save(tmp_path / "zzz_offscreen.png")
    return tmp_path


def _icon(view, index):
    option = QStyleOptionViewItem()
    view.itemDelegateForColumn(0).initStyleOption(option, index)
    return QIcon(option.icon)


def _photo_index(panel):
    root = panel._proxy.index(0, 0)
    return next(panel._proxy.index(i, 0, root)
                for i in range(panel._proxy.rowCount(root))
                if panel._proxy.index(i, 0, root).data() == "photo.png")


def test_thumbnail_menu_defaults_off_and_remembers_opt_in(qapp, qtbot, isolated_settings):
    from crush.ui.main_window import MainWindow

    first = MainWindow()
    qtbot.addWidget(first)
    try:
        assert not first._thumbnails_action.isChecked()
        assert not first._fs_panel._thumbnails.enabled
        first._thumbnails_action.setChecked(True)
        assert first._fs_panel._thumbnails.enabled
        assert isolated_settings.value("image_thumbnails", False, type=bool)
        second = MainWindow()
        qtbot.addWidget(second)
        try:
            assert second._thumbnails_action.isChecked()
            second._thumbnails_action.setChecked(False)
            assert not isolated_settings.value("image_thumbnails", True, type=bool)
        finally:
            second.close()
    finally:
        first.close()


def test_previews_only_read_visible_images_after_opt_in(
    qapp, qtbot, image_folder, monkeypatch,
):
    vfs = DirectoryVFS(image_folder)
    reads = []
    original = vfs.read

    def read(node):
        reads.append((node.name, threading.get_ident()))
        return original(node)

    monkeypatch.setattr(vfs, "read", read)
    panel = FilesystemPanel(Session())
    qtbot.addWidget(panel)
    panel.resize(600, 360)
    panel.show()
    panel.load_vfs(vfs)
    qtbot.waitUntil(lambda: panel._proxy.rowCount(panel._proxy.index(0, 0)) >= 44)
    index = _photo_index(panel)
    assert _icon(panel._tree, index).isNull()
    assert not reads
    panel._thumbnails.set_enabled(True)
    qtbot.waitUntil(lambda: not _icon(panel._tree, index).isNull())
    icon = _icon(panel._tree, index)
    pixels = icon.pixmap(48, 48).toImage()
    assert pixels.pixelColor(pixels.width() // 2, pixels.height() // 2).red() == 255
    assert ("photo.png", threading.get_ident()) not in reads
    assert "hidden.png" not in [name for name, _ in reads]
    assert "zzz_offscreen.png" not in [name for name, _ in reads]
    panel._thumbnails.set_enabled(False)
    assert _icon(panel._tree, index).isNull()
    assert not panel._thumbnails._cache
    panel.close()


def test_search_results_share_the_memory_preview(qapp, qtbot, image_folder):
    panel = FilesystemPanel(Session())
    qtbot.addWidget(panel)
    panel.resize(600, 360)
    panel.show()
    vfs = DirectoryVFS(image_folder)
    panel.load_vfs(vfs)
    panel._thumbnails.set_enabled(True)
    panel._apply_filter("photo")
    qtbot.waitUntil(lambda: panel._search_model.rowCount() == 1)
    index = panel._search_model.index(0, 0)
    qtbot.waitUntil(lambda: not _icon(panel._search_view, index).isNull())
    panel.close_vfs(vfs)
    assert not panel._thumbnails._cache
    panel.close()


def test_closing_source_discards_an_inflight_preview(qapp, qtbot, image_folder, monkeypatch):
    panel = FilesystemPanel(Session())
    qtbot.addWidget(panel)
    vfs = DirectoryVFS(image_folder)
    panel._vfs_list = [vfs]
    photo = next(n for n in vfs.root().children if n.name == "photo.png")
    started, release = threading.Event(), threading.Event()
    original = vfs.read

    def slow_read(node):
        started.set()
        release.wait(5)
        return original(node)

    monkeypatch.setattr(vfs, "read", slow_read)
    panel._thumbnails.set_enabled(True)
    try:
        panel._thumbnails.icon(photo, vfs)
        qtbot.waitUntil(started.is_set)
        panel.close_vfs(vfs)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not panel._thumbnails._busy)
    assert not panel._thumbnails._cache
    panel.close()


def test_invalid_or_oversized_images_have_no_preview(qapp, image_folder, monkeypatch):
    from crush.ui.image_thumbnails import _read_thumbnail

    vfs = DirectoryVFS(image_folder)
    broken = next(n for n in vfs.root().children if n.name == "broken.png")
    assert _read_thumbnail(broken, vfs).isNull()
    photo = next(n for n in vfs.root().children if n.name == "photo.png")
    photo.size = 128 << 20
    monkeypatch.setattr(vfs, "read", lambda node: pytest.fail("oversized file was read"))
    assert _read_thumbnail(photo, vfs).isNull()


def test_sevenzip_previews_do_not_retain_original_images(qapp, image_folder, tmp_path):
    import py7zr

    from crush.core.vfs import SevenZipVFS
    from crush.ui.image_thumbnails import _read_thumbnail

    archive = tmp_path / "public.7z"
    with py7zr.SevenZipFile(archive, "w") as writer:
        writer.write(image_folder / "photo.png", "photo.png")
        writer.write(image_folder / "collapsed" / "hidden.png", "hidden.png")
    vfs = SevenZipVFS(archive)
    try:
        original_cache = dict(vfs._read_cache)
        original_heads = dict(vfs._head_cache)
        for node in vfs.root().children:
            assert not _read_thumbnail(node, vfs).isNull()
        assert vfs._read_cache == original_cache
        assert vfs._head_cache == original_heads
    finally:
        vfs.close()
