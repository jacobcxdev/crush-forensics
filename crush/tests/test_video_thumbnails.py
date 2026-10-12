# SPDX-License-Identifier: Apache-2.0
"""Video previews decode public data without playback or temporary copies."""
from __future__ import annotations

import io

import av
import pytest
from PIL import Image

from crush.core.vfs import DirectoryVFS
from crush.ui.image_thumbnails import _read_thumbnail


def test_compressed_video_seek_cannot_bypass_preview_budget():
    import zipfile
    from crush.ui.image_thumbnails import _PreviewStream

    archive = io.BytesIO()
    size = 65 << 20
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as writer:
        writer.writestr("public.mp4", bytes(size))
    archive.seek(0)
    with zipfile.ZipFile(archive) as reader:
        with _PreviewStream(reader.open("public.mp4"), size) as preview:
            preview.seek(-1, io.SEEK_END)
            assert preview._stream.tell() == 0  # Size probes stay lazy.
            with pytest.raises(OSError, match="seek limit"):
                preview.read(1)
            assert preview._stream.tell() == 0  # No decompression before rejection.


def test_large_archive_video_does_not_buffer_during_open(qapp, tmp_path, monkeypatch):
    import zipfile
    from crush.core.vfs import ZipVFS

    path = tmp_path / "public.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as writer:
        writer.writestr("public.mp4", b"\0\0\0\x18ftyp" + bytes(40 << 20))
    vfs = ZipVFS(path)
    try:
        monkeypatch.setattr(vfs, "open", lambda node: pytest.fail("archive open buffers entire video"))
        assert _read_thumbnail(vfs.root().children[0], vfs).isNull()
    finally:
        vfs.close()


@pytest.fixture
def video_file(tmp_path):
    output = io.BytesIO()
    with av.open(output, "w", format="avi") as container:
        stream = container.add_stream("mpeg4", rate=1)
        stream.width, stream.height, stream.pix_fmt = 320, 160, "yuv420p"
        for i in range(3):
            frame = av.VideoFrame.from_image(Image.new("RGB", (320, 160), "blue"))
            frame.pts = i
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    path = tmp_path / "public.avi"
    path.write_bytes(output.getvalue())
    return path


@pytest.mark.parametrize("large", [False, True])
def test_video_preview_uses_stream_and_large_memory_only_thumbnail(
    qapp, video_file, monkeypatch, large,
):
    if large:
        with video_file.open("ab") as output:
            output.truncate(128 << 20)
    vfs = DirectoryVFS(video_file.parent)
    node = next(n for n in vfs.root().children if n.name == video_file.name)
    monkeypatch.setattr(vfs, "read", lambda node: pytest.fail("video read in full"))
    before = set(video_file.parent.iterdir())
    image = _read_thumbnail(node, vfs)
    assert not image.isNull()
    assert image.width() >= 120
    assert image.pixelColor(32, 32).blue() > 200
    assert set(video_file.parent.iterdir()) == before


def test_renamed_video_signature_and_broken_video(qapp, video_file):
    renamed = video_file.with_suffix(".bin")
    video_file.rename(renamed)
    (renamed.parent / "broken.mp4").write_bytes(b"not a video")
    vfs = DirectoryVFS(renamed.parent)
    for node in vfs.root().children:
        image = _read_thumbnail(node, vfs)
        assert image.isNull() == (node.name == "broken.mp4")


def test_visible_grid_video_is_opt_in_and_does_not_write_preview_files(
    qtbot, video_file, monkeypatch, tmp_path,
):
    from PySide6.QtCore import QSettings
    from crush.core.session import Session
    from crush.ui import fs_panel

    monkeypatch.setattr(fs_panel, "QSettings", lambda *args: QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat,
    ))
    vfs = DirectoryVFS(video_file.parent)
    node = next(n for n in vfs.root().children if n.name == video_file.name)
    panel = fs_panel.FilesystemPanel(Session())
    qtbot.addWidget(panel)
    panel.resize(520, 420)
    panel.show()
    panel.load_vfs(vfs)
    panel.set_view_mode("grid")
    qtbot.waitUntil(lambda: not panel._build_timer.isActive())
    key = (id(vfs), node.path)
    assert not panel._thumbnails.enabled
    assert not panel._thumbnails._cache
    panel._thumbnails.set_enabled(True)
    qtbot.waitUntil(lambda: key in panel._thumbnails._cache)
    assert panel._thumbnails._cache[key].pixelColor(32, 32).blue() > 200
    panel.close()


def test_sevenzip_video_preview_does_not_grow_original_cache(qapp, video_file, tmp_path):
    import py7zr
    from crush.core.vfs import SevenZipVFS

    archive = tmp_path / "public.7z"
    with py7zr.SevenZipFile(archive, "w") as writer:
        writer.write(video_file, "public.avi")
    vfs = SevenZipVFS(archive)
    try:
        before = dict(vfs._read_cache)
        node = vfs.root().children[0]
        assert not _read_thumbnail(node, vfs).isNull()
        assert vfs._read_cache == before
    finally:
        vfs.close()
