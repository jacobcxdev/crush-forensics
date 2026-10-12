# SPDX-License-Identifier: Apache-2.0
"""Closing viewer tabs releases playback, including nested media viewers."""
from __future__ import annotations

import io
from pathlib import Path

import av
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtMultimedia import QAudio, QMediaPlayer
from PySide6.QtWidgets import QLabel, QTabWidget

from crush.ui.main_window import MainWindow
from crush.viewers.media_viewer import MediaViewer


@pytest.fixture(scope="module")
def silent_video():
    output = io.BytesIO()
    with av.open(output, "w", format="avi") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width = video.height = 32
        video.pix_fmt = "yuv420p"
        audio = container.add_stream("pcm_s16le", rate=8000)
        audio.layout = "mono"
        for second in range(30):
            for i in range(10):
                frame = av.VideoFrame(32, 32, "rgb24")
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))
                frame.pts = second * 10 + i
                for packet in video.encode(frame):
                    container.mux(packet)
            frame = av.AudioFrame(format="s16", layout="mono", samples=8000)
            frame.sample_rate = 8000
            frame.pts = second * 8000
            frame.planes[0].update(bytes(frame.planes[0].buffer_size))
            for packet in audio.encode(frame):
                container.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None):
                container.mux(packet)
    return output.getvalue()


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("close", ["one", "all", "others", "source", "window"])
def test_closing_media_tab_stops_playback_and_removes_temp(
    qtbot, tmp_path, monkeypatch, silent_video, wrapped, close,
):
    from crush.ui import main_window

    monkeypatch.setattr(main_window, "QSettings", lambda *args: QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat,
    ))
    window = MainWindow()
    qtbot.addWidget(window)
    media = MediaViewer(silent_video)
    media._audio.setMuted(True)
    tab = media
    if wrapped:
        tab = QTabWidget()
        tab.addTab(media, "View")
        tab.addTab(QLabel("public hex placeholder"), "Hex")
    source = object()
    tab.setProperty("crush_vfs", source)
    window._viewer_tabs.addTab(tab, "public silent video")
    keep = QLabel("keep this tab")
    window._viewer_tabs.addTab(keep, "keep")
    temp = Path(media._tmp_path)
    try:
        media._player.play()
        qtbot.waitUntil(lambda: media._player.playbackState()
                        == QMediaPlayer.PlaybackState.PlayingState)
        if close == "one":
            window._close_tab(0)
        elif close == "all":
            window._close_all_tabs()
        elif close == "others":
            window._close_other_tabs(1)
        elif close == "source":
            assert window._close_tabs_for_vfs(source) == 1
        else:
            window.close()
        assert media._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        assert media._player.source().isEmpty()
        assert not temp.exists()
        if close in {"one", "others", "source"}:
            assert window._viewer_tabs.widget(0) is keep
    finally:
        media.close()
        window.close()


def test_closing_nested_pcm_media_stops_sink_and_timer(qtbot, tmp_path, monkeypatch):
    from crush.ui import main_window
    from crush.viewers import media_viewer

    monkeypatch.setattr(main_window, "QSettings", lambda *args: QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat,
    ))
    monkeypatch.setattr(media_viewer, "_decode_audio_pcm",
                        lambda data: (bytes(48000 * 2 * 30), 48000, 1))
    window = MainWindow()
    qtbot.addWidget(window)
    media = MediaViewer(b"OggS public test fixture")
    tab = QTabWidget()
    tab.addTab(media, "View")
    window._viewer_tabs.addTab(tab, "public PCM audio")
    try:
        assert media._pcm_timer.isActive()
        window._close_tab(0)
        assert not media._pcm_timer.isActive()
        assert media._sink.state().value == QAudio.State.StoppedState.value
    finally:
        media.close()
        window.close()
