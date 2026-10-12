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

**View → Show image thumbnails** adds image previews beside filenames in the
Filesystem tree and its search results. It is off by default and remembers your
choice. Only visible rows are decoded, in the background, with a bounded memory
cache; no thumbnail files are saved. Closing a source or disabling previews clears
the cache. Unsupported or damaged images keep their normal row presentation.
Automatic previews skip files larger than 32 MiB and images above 40 megapixels;
you can still open those files in the image viewer.

---

