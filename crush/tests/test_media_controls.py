# SPDX-License-Identifier: Apache-2.0
"""Viewer controls operate on disposable media without changing its bytes."""
from __future__ import annotations

import io
from pathlib import Path

import av
import pytest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QTabWidget

from crush.viewers.image_viewer import ImageViewer
from crush.viewers.media_viewer import MediaViewer


@pytest.fixture(scope="module")
def short_video():
    output = io.BytesIO()
    with av.open(output, "w", format="avi") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height = 64, 32
        stream.pix_fmt = "yuv420p"
        image = Image.new("RGB", (64, 32), "red")
        image.paste("blue", (0, 0, 32, 32))
        for index in range(10):
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


def test_loop_restarts_at_end_and_switching_off_stops(qtbot, short_video):
    viewer = MediaViewer(short_video)
    qtbot.addWidget(viewer)
    viewer._audio.setMuted(True)
    viewer.show()
    try:
        assert not viewer._loop_checkbox.isChecked()
        assert viewer._player.loops() == QMediaPlayer.Loops.Once
        viewer._loop_checkbox.setChecked(True)
        assert viewer._player.loops() == QMediaPlayer.Loops.Infinite
        positions = []
        viewer._player.positionChanged.connect(positions.append)
        viewer._play_btn.click()
        qtbot.waitUntil(
            lambda: any(before >= 700 and after < 300
                        for before, after in zip(positions, positions[1:])),
            timeout=6000,
        )
        assert viewer._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        viewer._loop_checkbox.setChecked(False)
        assert viewer._player.loops() == QMediaPlayer.Loops.Once
        qtbot.waitUntil(lambda: viewer._player.mediaStatus()
                        == QMediaPlayer.MediaStatus.EndOfMedia, timeout=4000)
        assert viewer._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    finally:
        viewer.close()


def test_video_rotation_fits_after_resize_and_preserves_source(qtbot, short_video):
    viewer = MediaViewer(short_video)
    qtbot.addWidget(viewer)
    viewer._audio.setMuted(True)
    viewer.resize(640, 480)
    viewer.show()
    path = Path(viewer._tmp_path)
    try:
        viewer._play_btn.click()
        qtbot.waitUntil(lambda: viewer._video_item.nativeSize().width() == 64)
        qtbot.waitUntil(lambda: viewer._video_item.videoSink().videoFrame().isValid())
        viewer._player.pause()
        viewer._rotate_right_btn.click()
        assert viewer._video_item.rotation() == 90
        bounds = viewer._video_item.sceneBoundingRect()
        assert bounds.width() == pytest.approx(32)
        assert bounds.height() == pytest.approx(64)
        for width, height in [(640, 480), (320, 600), (800, 280)]:
            viewer.resize(width, height)
            qtbot.wait(20)
            displayed = viewer._video_widget.mapFromScene(bounds).boundingRect()
            viewport = viewer._video_widget.viewport().rect()
            assert viewport.adjusted(-1, -1, 1, 1).contains(displayed)
            assert max(displayed.width() / viewport.width(),
                       displayed.height() / viewport.height()) > 0.95
            rendered = viewer._video_widget.viewport().grab().toImage()
            top = rendered.pixelColor(displayed.center().x(),
                                      displayed.top() + displayed.height() // 4)
            bottom = rendered.pixelColor(displayed.center().x(),
                                         displayed.top() + 3 * displayed.height() // 4)
            assert top.blue() > 200 and top.red() < 50
            assert bottom.red() > 200 and bottom.blue() < 50
        for _ in range(3):
            viewer._rotate_right_btn.click()
        assert viewer._video_item.rotation() == 0
        viewer._rotate_left_btn.click()
        assert viewer._video_item.rotation() == 270
        assert path.read_bytes() == short_video
    finally:
        viewer.close()


def test_close_stops_looping_video_and_releases_file(qtbot, short_video):
    viewer = MediaViewer(short_video)
    qtbot.addWidget(viewer)
    viewer._audio.setMuted(True)
    path = Path(viewer._tmp_path)
    try:
        viewer._loop_checkbox.setChecked(True)
        viewer._play_btn.click()
        qtbot.waitUntil(lambda: viewer._player.playbackState()
                        == QMediaPlayer.PlaybackState.PlayingState)
        viewer.close()
        assert viewer._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        assert viewer._player.source().isEmpty()
        assert not path.exists()
        qtbot.wait(1200)
        assert viewer._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    finally:
        viewer.close()


def test_pcm_audio_hides_video_controls(qtbot, monkeypatch):
    from crush.viewers import media_viewer

    monkeypatch.setattr(media_viewer, "_decode_audio_pcm",
                        lambda data: (bytes(48000 * 2), 48000, 1))
    viewer = MediaViewer(b"OggS public fixture")
    qtbot.addWidget(viewer)
    viewer.show()
    try:
        assert viewer._using_pcm
        for control in (viewer._video_widget, viewer._loop_checkbox,
                        viewer._rotate_left_btn, viewer._rotate_right_btn):
            assert control.isHidden()
    finally:
        viewer.close()


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("close_window", [False, True])
def test_tab_or_window_close_stops_looping_video(
    qtbot, tmp_path, monkeypatch, short_video, wrapped, close_window,
):
    from crush.ui import main_window

    monkeypatch.setattr(main_window, "QSettings", lambda *args: QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat,
    ))
    window = main_window.MainWindow()
    qtbot.addWidget(window)
    viewer = MediaViewer(short_video)
    viewer._audio.setMuted(True)
    tab = viewer
    if wrapped:
        tab = QTabWidget()
        tab.addTab(viewer, "View")
    window._viewer_tabs.addTab(tab, "public looping video")
    path = Path(viewer._tmp_path)
    try:
        viewer._loop_checkbox.setChecked(True)
        viewer._play_btn.click()
        qtbot.waitUntil(lambda: viewer._player.playbackState()
                        == QMediaPlayer.PlaybackState.PlayingState)
        if close_window:
            window.close()
        else:
            window._close_tab(0)
        assert viewer._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        assert viewer._player.source().isEmpty()
        assert not path.exists()
    finally:
        viewer.close()
        window.close()


def test_existing_photo_rotation_keeps_original_decoded_image(qtbot):
    image = Image.new("RGB", (64, 32), "red")
    image.paste("blue", (0, 0, 32, 32))
    output = io.BytesIO()
    image.save(output, format="PNG")
    viewer = ImageViewer(output.getvalue())
    qtbot.addWidget(viewer)
    viewer.show()
    try:
        viewer._rotate_right_btn.click()
        assert viewer._rotated_pixmap().size().width() == 32
        assert viewer._rotated_pixmap().size().height() == 64
        assert viewer.decoded_image().size().width() == 64
        assert viewer.decoded_image().pixelColor(0, 0).name() == "#0000ff"
        for _ in range(3):
            viewer._rotate_right_btn.click()
        assert viewer._rotated_pixmap().toImage() == viewer.decoded_image()
        viewer._rotate_left_btn.click()
        assert viewer._rotation == 270
    finally:
        viewer.close()
