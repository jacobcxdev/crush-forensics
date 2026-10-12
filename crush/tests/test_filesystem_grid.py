# SPDX-License-Identifier: Apache-2.0
"""The grid is a folder browser alternative to the filesystem tree."""
from __future__ import annotations

import pytest
from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyleOptionViewItem

from crush.core.session import Session
from crush.core.vfs import DirectoryVFS
from crush.ui.fs_panel import FilesystemPanel


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    from crush.ui import fs_panel

    monkeypatch.setattr(fs_panel, "QSettings", lambda *args: QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat,
    ))
    (tmp_path / "folder").mkdir()
    Image.new("RGB", (600, 300), "red").save(tmp_path / "folder" / "photo.png")
    (tmp_path / "file.txt").write_text("public text")
    result = FilesystemPanel(Session())
    qtbot.addWidget(result)
    result.resize(520, 420)
    result.show()
    result.load_vfs(DirectoryVFS(tmp_path))
    qtbot.waitUntil(lambda: not result._build_timer.isActive())
    return result


def _index(panel, name):
    model, root = panel._grid.model(), panel._grid.rootIndex()
    return next(model.index(i, 0, root) for i in range(model.rowCount(root))
                if model.index(i, 0, root).data() == name)


def test_grid_navigates_folders_opens_files_and_goes_up(panel, qtbot):
    assert panel.view_mode == "tree"
    panel.set_view_mode("grid")
    assert panel._stack.currentWidget() is panel._grid_page
    assert not panel._tree.isVisible()
    folder = _index(panel, "folder")
    panel._grid.doubleClicked.emit(folder)
    qtbot.waitUntil(lambda: panel._grid.model().rowCount(panel._grid.rootIndex()) == 1)
    photo = _index(panel, "photo.png")
    with qtbot.waitSignal(panel.node_activated) as opened:
        panel._grid.doubleClicked.emit(photo)
    assert opened.args[0].name == "photo.png"
    qtbot.mouseClick(panel._grid_up, Qt.MouseButton.LeftButton)
    assert _index(panel, "folder").isValid()
    panel.set_view_mode("tree")
    assert panel._stack.currentWidget() is panel._tree
    panel.close()


def test_grid_shows_large_opt_in_previews_and_clears_on_source_close(panel, qtbot):
    panel.set_view_mode("grid")
    panel._grid.doubleClicked.emit(_index(panel, "folder"))
    qtbot.waitUntil(lambda: panel._grid.model().rowCount(panel._grid.rootIndex()) == 1)
    index = _index(panel, "photo.png")
    assert not panel._thumbnails.enabled
    assert not panel._thumbnails._cache
    panel._thumbnails.set_enabled(True)

    def preview():
        option = QStyleOptionViewItem()
        panel._grid.itemDelegate().initStyleOption(option, index)
        return QIcon(option.icon).pixmap(128, 128).toImage()

    qtbot.waitUntil(lambda: preview().width() >= 120 and preview().pixelColor(32, 32).red() > 200)
    panel.close_vfs(panel._vfs_list[0])
    assert not panel._thumbnails._cache
    assert panel._grid.model().rowCount(panel._grid.rootIndex()) == 0
    panel.close()


def test_grid_search_returns_to_current_folder_and_remembers_mode(panel, qtbot):
    panel.set_view_mode("grid")
    panel._grid.doubleClicked.emit(_index(panel, "folder"))
    qtbot.waitUntil(lambda: panel._grid.model().rowCount(panel._grid.rootIndex()) == 1)
    panel._apply_filter("photo")
    qtbot.waitUntil(lambda: panel._search_model.rowCount() == 1)
    assert panel._grid.model() is panel._search_model
    assert _index(panel, "photo.png").isValid()
    panel._apply_filter("")
    qtbot.waitUntil(lambda: panel._grid.model() is panel._proxy)
    assert _index(panel, "photo.png").isValid()
    other = FilesystemPanel(Session())
    qtbot.addWidget(other)
    assert other.view_mode == "grid"
    other.close()
    panel.close()
