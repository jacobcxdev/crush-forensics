# SPDX-License-Identifier: Apache-2.0
"""Confirmation and narrowly scoped diagnostic-log cleanup."""
from __future__ import annotations

import re
import stat
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget,
)

from crush.ui.i18n import translate


class ClearSavedDataDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("ClearSavedDataDialog", "Clear saved data"))
        layout = QVBoxLayout(self)
        text = QLabel(translate(
            "ClearSavedDataDialog",
            "Clear these data saved by Crush?\n\n"
            "• Recent files and disk-image paths\n"
            "• Search history\n"
            "• Automatic diagnostic logs in the system temporary directory\n"
            "• In-memory thumbnail caches in all open Crush windows\n\n"
            "Source files, disk images and exported files are kept.\n"
            "Logging continues in a new diagnostic log.",
        ))
        text.setWordWrap(True)
        layout.addWidget(text)
        self.reset_preferences = QCheckBox(translate(
            "ClearSavedDataDialog", "Also reset preferences (restart Crush afterwards)"
        ))
        self.reset_preferences.setChecked(False)
        layout.addWidget(self.reset_preferences)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translate("ClearSavedDataDialog", "Clear")
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


def clear_diagnostic_logs(directory: Path) -> list[str]:
    """Remove only automatic, regular log files directly inside OS temp."""
    errors: list[str] = []
    try:
        paths = list(directory.iterdir())
    except OSError as exc:
        return [f"{directory}: {exc}"]
    for path in paths:
        if not re.fullmatch(r"crush-[0-9]{8}-[0-9]{6}\.log", path.name):
            continue
        try:
            datetime.strptime(path.name, "crush-%Y%m%d-%H%M%S.log")
        except ValueError:
            continue
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                errors.append(translate(
                    "ClearSavedDataDialog", "Skipped link: {path}"
                ).format(path=path))
                continue
            if stat.S_ISREG(info.st_mode):
                path.unlink()
        except FileNotFoundError:
            pass  # Another instance may have cleared an old log concurrently.
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return errors
