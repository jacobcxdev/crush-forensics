# SPDX-License-Identifier: Apache-2.0
"""Read ASIF through fs-explorer as a seekable disk for the existing filesystem reader.

Only the ASIF container is handled here. Other Apple images still use ewfprobe.
Encrypted data is decoded on demand into a bounded memory cache, never a temporary disk.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fs_explorer.containers.asif import MAGIC, ASIFImage
from fs_explorer.containers.dmg_crypt import V2_MAGIC, EncryptedDMG
from fs_explorer.errors import FsExplorerError
from fs_explorer.io.cache import Cache
from fs_explorer.io.parallel import Pool
from fs_explorer.io.source import BytesRA, FileRA, RandomAccess, RAStream
from fs_explorer.io.stats import Stats
from fs_explorer.tree import Context


class ASIFStream(RAStream):
    format = "ASIF"

    def __init__(self, image: ASIFImage, source: FileRA, context: Context) -> None:
        super().__init__(image.device())
        self.size = image.size
        self._source = source
        self._context = context

    def close(self) -> None:
        if not self.closed:
            self._context.pool.close()
            self._context.cache.clear()
            self._ra = BytesRA(b"")
            self._source.close()
        super().close()

    def verify(self, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
        # ASIF stores no whole-disk digest. Compute one without reporting a
        # successful comparison against a digest the container never recorded.
        digest = hashlib.sha256()
        done = 0
        while done < self.size:
            data = self._ra.read_exact(done, min(4 << 20, self.size - done))
            digest.update(data)
            done += len(data)
            if progress:
                progress(done, self.size)
        return {"computed": {"sha256": digest.hexdigest()}, "stored": {}, "match": None,
                "bytes": done, "checksum_errors": [], "missing_page_count": 0,
                "container_checks": []}


def open_asif(path: Path, *, password: str = "") -> tuple[ASIFStream, str] | None:
    """Return an ASIF disk, or None so the existing reader handles other formats.

    Password refusals also fall through to ewfprobe, preserving Crush's existing
    password/key dialog and errors for every encrypted Apple image.
    """
    if not path.is_file():
        return None
    source = FileRA(path)
    context = Context(cache=Cache(), stats=Stats(), pool=Pool(threads=1))
    opened = False
    try:
        device: RandomAccess = source
        encrypted = source.read_at(0, 8) == V2_MAGIC
        if encrypted:
            if not password:
                return None
            context.passwords["/"] = password
            try:
                wrapper = EncryptedDMG(source, context)
                device = wrapper.device()
            except FsExplorerError:
                return None
            finally:
                context.passwords.clear()
        if device.read_at(0, 4) != MAGIC:
            return None
        image = ASIFImage(device, context)
        stream = ASIFStream(image, source, context)
        opened = True
        note = f"an Apple sparse image (ASIF, {image.chunk_size:,}-byte chunks)"
        if encrypted:
            note += f", encrypted (AES-{wrapper.key_bits}) and opened with its password"
        return stream, note
    finally:
        if not opened:
            context.pool.close()
            context.cache.clear()
            source.close()
