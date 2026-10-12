## The Interface

```
┌─────────────────┬──────────────────────────────────┬───────────────┐
│  Filesystem     │         Viewer tabs               │  Properties   │
│  panel (left)   │                                   │  panel (right)│
│                 │                                   │               │
│                 │                                   │               │
└─────────────────┴──────────────────────────────────┴───────────────┘
│  Log panel (bottom, hidden by default)                              │
└─────────────────────────────────────────────────────────────────────┘
```

All panels are dockable and can be floated, resized, or hidden via **View** menu. Use **View → Reset Panel Layout** to restore defaults.

The Filesystem panel's **Tree / Grid** selector switches between the hierarchy
and a folder browser. Grid displays one directory as large tiles. Double-click a
folder to enter it, use **Up** to return to its parent, and double-click a file to
open it. The view choice is remembered. Filtering also works in Grid; clearing
the filter returns to the current directory. Long file names are shortened in
the middle while keeping the extension visible.

**View → Show thumbnails** adds image and video previews in either view and in
search results. It is off by default and remembers your choice. Only visible
items are decoded, in the background, with a bounded memory cache; no thumbnail
files are saved. Closing a source or disabling previews clears the cache.
Loading uses up to four workers, skips queued items that scroll out of view,
and stops obsolete work between reads and decoding steps. Results share cached
GUI icons and batched repaints so a slow file does not hold up the whole folder.
Unsupported or damaged files keep their normal presentation. Image previews
skip files larger than 32 MiB and images above 40 megapixels. Video previews read
a frame through the source stream with an I/O limit. Videos above 32 MiB use
streaming previews for disk images and regular files; other sources retain a
generic icon to avoid buffering or disk staging. These preview limits do not
prevent opening the file normally.

**Tools → Clear saved data…** clears recent file and disk-image paths, search
history, automatic diagnostic logs in the system temporary folder, and thumbnail
caches in open Crush windows. The dialog offers an optional preferences reset,
which is unchecked by default and finishes taking effect after restarting Crush.
Logging resumes in a fresh log. Cleanup reports any files or settings it could
not clear. It does not delete source files or disk images.

---

