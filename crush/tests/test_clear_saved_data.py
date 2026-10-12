# SPDX-License-Identifier: Apache-2.0
"""Saved-data cleanup uses only disposable settings and generated public files."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from crush.ui import fs_panel, main_window


@pytest.fixture
def isolated(tmp_path, monkeypatch, qapp):
    settings_path = tmp_path / "settings.ini"

    def settings(*args, **kwargs):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)

    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(main_window, "QSettings", settings)
    monkeypatch.setattr(fs_panel, "QSettings", settings)
    monkeypatch.setattr(main_window.tempfile, "gettempdir", lambda: str(logs))
    monkeypatch.setattr(main_window.MainWindow, "_open_windows", [])
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: notices.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: notices.append(args))
    yield settings(), logs, notices
    for window in list(main_window.MainWindow._open_windows):
        if shiboken6.isValid(window):
            window.close()
            if window._file_handler is not None:
                window._raw_logger.removeHandler(window._file_handler)
                window._file_handler.close()
            window._raw_logger.removeHandler(window._log_signal_handler)
            shiboken6.delete(window)


def _window(isolated):
    settings, _, _ = isolated
    settings.setValue("recent_files", ["public-source.img"])
    settings.setValue("recent_disk_images", ["public-source.img"])
    settings.setValue("filter_history", ["public-query"])
    settings.setValue("theme", "dark")
    settings.setValue("image_thumbnails", True)
    return main_window.MainWindow()


def test_cancel_keeps_saved_settings_logs_and_cache(isolated, monkeypatch):
    from crush.ui.clear_saved_data import ClearSavedDataDialog

    window = _window(isolated)
    window._fs_panel._thumbnails._cache[(1, "public")] = QImage()
    old_log = window._log_path
    monkeypatch.setattr(ClearSavedDataDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window._clear_saved_data()
    assert isolated[0].value("recent_files", type=list) == ["public-source.img"]
    assert old_log.exists()
    assert window._fs_panel._thumbnails._cache
    assert isolated[2] == []


def test_clear_button_preserves_preferences_by_default(isolated, monkeypatch):
    from crush.ui.clear_saved_data import ClearSavedDataDialog

    window = _window(isolated)

    def accept(dialog):
        assert not dialog.reset_preferences.isChecked()
        dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
        return dialog.result()

    monkeypatch.setattr(ClearSavedDataDialog, "exec", accept)
    window._clear_saved_data()
    assert isolated[0].value("recent_files", [], type=list) == []
    assert isolated[0].value("theme") == "dark"
    assert isolated[0].value("image_thumbnails", type=bool)
    assert isolated[2]


def test_cleanup_refreshes_all_windows_and_preserves_sources_and_exports(isolated):
    first = _window(isolated)
    second = main_window.MainWindow()
    settings, logs, _ = isolated
    source = logs / "public-source.img"
    source.write_bytes(b"public source")
    exported = logs / "exported-diagnostic.log"
    exported.write_bytes(b"exported log")
    unrelated = logs / "crush-20260101-010203.log.export"
    unrelated.write_bytes(b"unrelated")
    nested = logs / "nested"
    nested.mkdir()
    nested_log = nested / "crush-20260101-010203.log"
    nested_log.write_bytes(b"nested export")
    old = logs / "crush-20260101-010203.log"
    old.write_bytes(b"old public diagnostic")
    for window in (first, second):
        window._fs_panel._thumbnails._cache[(1, "public")] = QImage()
    old_handlers = [first._file_handler, second._file_handler]
    assert first._clear_saved_data_now() == []
    assert settings.value("recent_files", [], type=list) == []
    assert settings.value("recent_disk_images", [], type=list) == []
    assert settings.value("filter_history", [], type=list) == []
    assert not old.exists()
    assert source.read_bytes() == b"public source"
    assert exported.read_bytes() == b"exported log"
    assert unrelated.exists() and nested_log.exists()
    for window in (first, second):
        assert window._recent_on_welcome.count() == 0
        assert not window._recent_menu.actions()[0].isEnabled()
        assert window._fs_panel._filter_history_model.stringList() == []
        assert not window._fs_panel._thumbnails._cache
        assert window._fs_panel._thumbnails.enabled
        window._logger.info("fresh public diagnostic")
        window._file_handler.flush()
        assert "old public diagnostic" not in window._log_path.read_text()
        assert "fresh public diagnostic" in window._log_path.read_text()
    assert all(handler.stream is None for handler in old_handlers)


def test_optional_reset_removes_preferences_disables_previews_and_requests_restart(isolated, monkeypatch):
    from crush.ui.clear_saved_data import ClearSavedDataDialog

    window = _window(isolated)
    isolated[0].setValue("filesystem_view_mode", "grid")

    def accept(dialog):
        dialog.reset_preferences.setChecked(True)
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(ClearSavedDataDialog, "exec", accept)
    window._clear_saved_data()
    assert not isolated[0].contains("theme")
    assert not isolated[0].value("image_thumbnails", False, type=bool)
    assert not window._fs_panel._thumbnails.enabled
    assert not window._thumbnails_action.isChecked()
    assert any("restart" in str(notice).lower() for notice in isolated[2])
    assert shiboken6.isValid(window)


def test_failed_log_delete_reports_partial_cleanup_and_resumes_logging(isolated, monkeypatch):
    window = _window(isolated)
    locked = isolated[1] / "crush-20260101-010203.log"
    locked.write_bytes(b"another instance")
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == locked:
            raise PermissionError("locked by another instance")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    errors = window._clear_saved_data_now()
    assert locked.read_bytes() == b"another instance"
    assert any(locked.name in error and "locked" in error for error in errors)
    assert isolated[0].value("recent_files", [], type=list) == []
    window._logger.info("logging resumed")
    window._file_handler.flush()
    assert "logging resumed" in window._log_path.read_text()


def test_symlink_logs_are_rejected_without_touching_target(isolated):
    window = _window(isolated)
    target = isolated[1] / "exported.log"
    target.write_bytes(b"public export")
    link = isolated[1] / "crush-20260101-010203.log"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation requires Windows developer mode or privilege")
    errors = window._clear_saved_data_now()
    assert link.is_symlink()
    assert target.read_bytes() == b"public export"
    assert any(link.name in error for error in errors)


def test_clear_recent_also_refreshes_welcome_in_every_window(isolated):
    first = _window(isolated)
    second = main_window.MainWindow()
    assert first._recent_on_welcome.count() and second._recent_on_welcome.count()
    first._clear_recent_files()
    for window in (first, second):
        assert window._recent_on_welcome.count() == 0
        assert not window._recent_menu.actions()[0].isEnabled()


def test_detached_file_handlers_cannot_recreate_deleted_log(isolated):
    window = _window(isolated)
    old_path, handler = window._log_path, window._file_handler
    window._clear_saved_data_now()
    record = logging.LogRecord("crush", logging.INFO, __file__, 1, "late record", (), None)
    handler.handle(record)
    assert handler.stream is None
    assert old_path != window._log_path
    assert not old_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows open-file delete protection")
def test_real_locked_log_is_kept_and_reported(isolated):
    window = _window(isolated)
    locked = isolated[1] / "crush-20260101-010203.log"
    locked.write_bytes(b"public diagnostic owned by another instance")
    with locked.open("rb"):
        errors = window._clear_saved_data_now()
    assert locked.read_bytes() == b"public diagnostic owned by another instance"
    assert any(locked.name in error for error in errors)
    assert window._file_handler is not None


def test_failed_log_resume_displays_partial_failure_without_success_claim(isolated, monkeypatch):
    from crush.ui.clear_saved_data import ClearSavedDataDialog

    window = _window(isolated)

    def accept(dialog):
        dialog.reset_preferences.setChecked(True)
        dialog.accept()
        return dialog.result()

    def cannot_write(*args, **kwargs):
        raise PermissionError("new log denied")

    monkeypatch.setattr(ClearSavedDataDialog, "exec", accept)
    monkeypatch.setattr(window, "_set_log_path", cannot_write)
    window._clear_saved_data()
    message = isolated[2][-1][2]
    assert "new log denied" in message
    assert "restart" in message.lower()
    assert "logging continues" not in message.lower()
    assert window._file_handler is None


def test_settings_write_failure_is_reported(isolated, monkeypatch):
    window = _window(isolated)
    monkeypatch.setattr(QSettings, "status", lambda _: QSettings.Status.AccessError)
    errors = window._clear_saved_data_now()
    assert any("settings" in error.lower() for error in errors)


def test_invalid_generated_names_and_log_named_directories_are_kept(isolated):
    window = _window(isolated)
    invalid = isolated[1] / "crush-99999999-999999.log"
    invalid.write_bytes(b"unrelated public file")
    directory = isolated[1] / "crush-20260101-010203.log"
    directory.mkdir()
    (directory / "public.txt").write_text("keep")
    assert window._clear_saved_data_now() == []
    assert invalid.read_bytes() == b"unrelated public file"
    assert (directory / "public.txt").read_text() == "keep"
