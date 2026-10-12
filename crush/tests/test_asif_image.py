# SPDX-License-Identifier: Apache-2.0
"""ASIF integration against disposable images, never a user's encrypted disk."""
from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path

import pytest
from fs_explorer import create

from crush.core.passwords import PasswordRequiredError, WrongPasswordError
from crush.core.raw_image import container_format, open_raw_image
from crush.core.vfs import RawImageVFS, VFSNode, open_vfs

PASSWORD = "public-asif-test-password"
CONTENT = b"ASIF integration fixture\n"
# The fixture writer adds 76 GPT sectors around the requested 32 MiB volume.
DISK_SIZE = (32 << 20) + 76 * 512


@pytest.fixture(scope="module")
def asif_images(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("asif")
    payload = folder / "payload"
    (payload / "nested").mkdir(parents=True)
    (payload / "nested" / "hello.txt").write_bytes(CONTENT)
    images = {}
    for name, encryption in (("plain", None), ("encrypted", "AES-256")):
        path = folder / name  # Recognition must not require a filename extension.
        create.dmg(path, [payload], fs="apfs", format="ASIF", size="32M",
                   volname="ASIF test", encryption=encryption,
                   password=PASSWORD if encryption else None)
        images[name] = path
    return images


def _files(node: VFSNode) -> list[VFSNode]:
    return [child for child in node.children if not child.is_dir] + [
        item for child in node.children if child.is_dir for item in _files(child)
    ]


@pytest.mark.parametrize("name", ["plain", "encrypted"])
def test_asif_browses_apfs_in_existing_vfs(asif_images: dict[str, Path], name: str) -> None:
    path = asif_images[name]
    before = hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns
    password = PASSWORD if name == "encrypted" else ""
    vfs = open_vfs(path, as_disk_image=True, password=password)
    try:
        assert isinstance(vfs, RawImageVFS), getattr(vfs, "fallback_note", "")
        hello = next(n for n in _files(vfs.root()) if n.name == "hello.txt")
        assert vfs.peek(hello, 4) == b"ASIF"
        with vfs.open(hello) as stream:
            assert stream.read() == CONTENT
        assert vfs.read(hello) == CONTENT
        assert "ASIF" in str(vfs.root().status)
        assert vfs.acquisition() == "ASIF"
    finally:
        vfs.close()
    assert (hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns) == before


def test_encrypted_asif_uses_existing_password_errors(asif_images: dict[str, Path]) -> None:
    path = asif_images["encrypted"]
    with pytest.raises(PasswordRequiredError):
        open_vfs(path, as_disk_image=True)
    with pytest.raises(WrongPasswordError):
        open_vfs(path, as_disk_image=True, password="incorrect-test-password")


def test_plain_asif_detected_by_content(asif_images: dict[str, Path]) -> None:
    assert container_format(asif_images["plain"]) == "ASIF"


def test_encrypted_asif_zero_length_payload_and_profile(asif_images: dict[str, Path]) -> None:
    # The public generator writes Apple's ASIF encryption profile: a zero
    # declared payload size means the encrypted payload runs to EOF.
    path = asif_images["encrypted"]
    header = path.read_bytes()[:76]
    assert header[:12] == b"encrcdsa\0\0\0\2"
    assert struct.unpack_from(">IQQ", header, 52) == (512, 0, 16384)
    handle = open_raw_image(path, password=PASSWORD)
    try:
        assert handle.size == DISK_SIZE
        handle.image.seek(512)
        assert handle.image.read(8) == b"EFI PART"
        assert not handle.image.writable()
        with pytest.raises((io.UnsupportedOperation, NotImplementedError)):
            handle.image.write(b"must remain read only")
        result = handle.image.verify()
        assert result["bytes"] == DISK_SIZE
        assert result["stored"] == {}
        assert result["match"] is None
    finally:
        handle.close()
    assert handle.image.closed


def test_asif_large_sparse_disk_reads_holes_without_expansion(
    asif_images: dict[str, Path], tmp_path: Path,
) -> None:
    data = bytearray(asif_images["plain"].read_bytes())
    struct.pack_into(">QQ", data, 0x30, (64 << 30) // 512, (64 << 30) // 512)
    path = tmp_path / "large.asif"
    path.write_bytes(data)
    handle = open_raw_image(path)
    try:
        assert handle.size == 64 << 30
        handle.image.seek(-512, io.SEEK_END)
        assert handle.image.read(1024) == bytes(512)
        assert path.stat().st_size < 8 << 20
    finally:
        handle.close()


def test_asif_gui_retries_password_and_opens_file(
    asif_images: dict[str, Path], qapp, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtCore import QTimer

    from crush.ui.disk_image_key_dialog import DiskImageKeyDialog
    from crush.ui.main_window import MainWindow

    attempts: list[str] = []
    real_exec = DiskImageKeyDialog.exec

    def enter_test_password(dialog: DiskImageKeyDialog) -> int:
        attempts.append(dialog.windowTitle())
        dialog._password_edit.setText("wrong" if len(attempts) == 1 else PASSWORD)
        QTimer.singleShot(0, dialog.accept)
        return real_exec(dialog)

    monkeypatch.setattr(DiskImageKeyDialog, "exec", enter_test_password)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window._load_source(str(asif_images["encrypted"]), as_disk_image=True,
                            open_after_load=True, append_to_tree=True)
        qtbot.waitUntil(lambda: bool(window.session.sources), timeout=10_000)
        qtbot.waitUntil(lambda: getattr(window, "_tree_loaded", False), timeout=10_000)
        assert attempts == ["Encrypted Disk Image", "Incorrect Password or Key"]
        vfs = window.session.sources[0]
        assert isinstance(vfs, RawImageVFS)
        hello = next(n for n in _files(vfs.root()) if n.name == "hello.txt")
        window._open_node(hello, vfs)
        assert window._viewer_tabs.tabText(0) == "hello.txt"
    finally:
        window.close()
