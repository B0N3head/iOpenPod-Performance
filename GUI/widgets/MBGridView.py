import difflib
import logging
from collections import deque
from PyQt6.QtCore import QRect, QSize, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLayout, QLayoutItem, QSizePolicy
from .MBGridViewItem import MusicBrowserGridItem
from ..styles import Metrics

# Fuzzy search: only attempt fuzzy matching for tokens at least this long,
# and require a SequenceMatcher ratio above the threshold.
_FUZZY_MIN_LEN = 3
_FUZZY_THRESHOLD = 0.78


def _token_matches(token: str, corpus_words: list[str]) -> bool:
    """Return True if *token* matches any word in *corpus_words*.

    Two-pass:
      1. Exact substring (fast) — handles normal typing and partial words.
      2. Fuzzy ratio (difflib) — handles typos for tokens >= _FUZZY_MIN_LEN.
    """
    # Pass 1: exact substring against each corpus word
    for word in corpus_words:
        if token in word:
            return True
    # Pass 2: fuzzy match for tokens long enough to be meaningful
    if len(token) >= _FUZZY_MIN_LEN:
        for word in corpus_words:
            if len(word) >= _FUZZY_MIN_LEN:
                ratio = difflib.SequenceMatcher(
                    None, token, word, autojunk=False
                ).ratio()
                if ratio >= _FUZZY_THRESHOLD:
                    return True
    return False


log = logging.getLogger(__name__)


# -- Flow layout ──────────────────────────────────────────────────────────────
# Lays out fixed-size children left-to-right, wrapping to the next row.
# Items are always left-aligned; no centering hack needed.

class _FlowLayout(QLayout):
    """Left-aligned, wrapping flow layout for fixed-size grid items."""

    def __init__(self, parent=None, spacing: int = 0):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing

    # -- QLayout API --

    def addItem(self, a0: QLayoutItem | None):
        if a0 is not None:
            self._items.append(a0)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def spacing(self) -> int:
        return self._spacing

    def setSpacing(self, a0: int):
        self._spacing = a0

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, a0: int) -> int:
        return self._do_layout(a0, dry_run=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        # Minimum: one item wide
        w = h = 0
        for item in self._items:
            sz = item.sizeHint()
            w = max(w, sz.width())
            h = max(h, sz.height())
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def setGeometry(self, a0):
        super().setGeometry(a0)
        self._do_layout(a0.width(), dry_run=False)

    # -- Layout engine --

    def _do_layout(self, width: int, *, dry_run: bool) -> int:
        m = self.contentsMargins()
        x = m.left()
        y = m.top()
        right_edge = width - m.right()
        row_height = 0
        sp = self._spacing

        for item in self._items:
            sz = item.sizeHint()
            # Wrap to next row if this item exceeds the right edge
            if x + sz.width() > right_edge and x > m.left():
                x = m.left()
                y += row_height + sp
                row_height = 0

            if not dry_run:
                item.setGeometry(QRect(x, y, sz.width(), sz.height()))

            x += sz.width() + sp
            row_height = max(row_height, sz.height())

        return y + row_height + m.bottom()


_ART_BATCH_SIZE = 20  # mhiiLinks per background worker


class MusicBrowserGrid(QFrame):
    """Grid view that displays albums, artists, or genres as clickable items."""
    item_selected = pyqtSignal(dict)  # Emits when an item is clicked

    def __init__(self):
        super().__init__()
        self._flow = _FlowLayout(self, spacing=Metrics.GRID_SPACING)
        self._flow.setContentsMargins(Metrics.GRID_SPACING, Metrics.GRID_SPACING,
                                      Metrics.GRID_SPACING, Metrics.GRID_SPACING)

        # Allow the widget to shrink inside a QScrollArea.
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.gridItems: list[MusicBrowserGridItem] = []
        self.pendingItems: deque = deque()
        self.timerActive = False
        self.columnCount = 1  # kept for external compat, not used by layout
        self._current_category = "Albums"
        self._load_id = 0

        # Artwork loading state
        self._items_by_link: dict[int, list[MusicBrowserGridItem]] = {}  # mhiiLink -> items waiting for art
        self._art_pending: set[int] = set()  # links currently being loaded

        # Sort / filter state
        self._all_items: list[dict] = []
        self._sort_key: str = "title"
        self._sort_reverse: bool = False
        self._search_query: str = ""

    def loadCategory(self, category: str):
        """Load and display items for the specified category."""
        from ..app import iTunesDBCache, build_album_list, build_artist_list, build_genre_list
        log.debug(f"loadCategory() called: {category}")

        self._current_category = category

        cache = iTunesDBCache.get_instance()
        if not cache.is_ready():
            return

        if category == "Albums":
            items = build_album_list(cache)
        elif category == "Artists":
            items = build_artist_list(cache)
        elif category == "Genres":
            items = build_genre_list(cache)
        else:
            return

        self._all_items = items
        self._apply_filter_and_sort()

    def populateGrid(self, items):
        """Populate the grid with items."""
        _saved_all = self._all_items   # preserve source list across clearGrid()
        self.clearGrid()
        self._all_items = _saved_all
        self._load_id += 1
        current_load_id = self._load_id

        self.pendingItems = deque(enumerate(items))

        if self.pendingItems and not self.timerActive:
            self.timerActive = True
            self._addNextItem(current_load_id)

    def _addNextItem(self, load_id: int):
        """Add the next batch of items."""
        if load_id != self._load_id:
            self.timerActive = False
            return

        if not self.pendingItems:
            self.timerActive = False
            # All items added — kick off batched artwork loading
            self._load_art_async()
            return

        try:
            batch_size = 5
            for _ in range(batch_size):
                if not self.pendingItems:
                    break

                i, item = self.pendingItems.popleft()

                if isinstance(item, dict):
                    title = item.get("title") or item.get("album", "Unknown")
                    subtitle = item.get("subtitle") or item.get("artist", "")
                    mhiiLink = item.get("artwork_id_ref")

                    item_data = {
                        "title": title,
                        "subtitle": subtitle,
                        "artwork_id_ref": mhiiLink,
                        "category": item.get("category", "Albums"),
                        "filter_key": item.get("filter_key", "Album"),
                        "filter_value": item.get("filter_value", title),
                        "album": item.get("album"),
                        "artist": item.get("artist"),
                    }

                    gridItem = MusicBrowserGridItem(title, subtitle, mhiiLink, item_data)
                    gridItem.clicked.connect(self._onItemClicked)
                    self.gridItems.append(gridItem)

                    # Track which items need artwork
                    if mhiiLink is not None:
                        self._items_by_link.setdefault(int(mhiiLink), []).append(gridItem)

                elif isinstance(item, MusicBrowserGridItem):
                    gridItem = item
                    gridItem.clicked.connect(self._onItemClicked)
                else:
                    continue

                self._flow.addWidget(gridItem)

            # Update minimum height so the scroll area can size correctly.
            w = self.width()
            if w > 0:
                self.setMinimumHeight(self._flow.heightForWidth(w))

            if self.pendingItems and load_id == self._load_id:
                QTimer.singleShot(8, lambda: self._addNextItem(load_id))
            else:
                self.timerActive = False
                # All items added — kick off batched artwork loading
                self._load_art_async()

        except RuntimeError:
            # Qt has destroyed the underlying C++ layout/widget (e.g. the
            # MusicBrowserGrid was deleted while this timer was pending).
            # Nothing to do — just stop the loading chain.
            self.timerActive = False

    # -------------------------------------------------------------------------
    # Batched artwork loading
    # -------------------------------------------------------------------------

    def _load_art_async(self):
        """Collect unique mhiiLinks and load artwork in background batches."""
        from ..app import Worker, ThreadPoolSingleton

        links_to_load = set(self._items_by_link.keys()) - self._art_pending
        if not links_to_load:
            return

        self._art_pending |= links_to_load
        load_id = self._load_id
        links_list = list(links_to_load)
        pool = ThreadPoolSingleton.get_instance()

        for i in range(0, len(links_list), _ART_BATCH_SIZE):
            chunk = links_list[i:i + _ART_BATCH_SIZE]
            worker = Worker(self._load_art_batch, chunk)
            worker.signals.result.connect(
                lambda result, lid=load_id: self._on_art_loaded(result, lid)
            )
            pool.start(worker)

    @staticmethod
    def _load_art_batch(links: list[int]) -> dict:
        """Background worker: decode artwork + colors for a batch of mhiiLinks."""
        from ..app import DeviceManager
        from ..imgMaker import find_image_by_img_id, get_artworkdb_cached
        import os

        device = DeviceManager.get_instance()
        if not device.device_path:
            return {}

        artworkdb_path = device.artworkdb_path
        artwork_folder = device.artwork_folder_path
        if not artworkdb_path or not os.path.exists(artworkdb_path):
            return {}

        artworkdb_data, img_id_index = get_artworkdb_cached(artworkdb_path)
        results: dict[int, tuple | None] = {}

        for link in links:
            if device.cancellation_token.is_cancelled():
                break
            result = find_image_by_img_id(artworkdb_data, artwork_folder, link, img_id_index)
            if result is not None:
                pil_img, dcol, album_colors = result
                # Serialize PIL image to RGBA bytes for thread-safe transfer
                pil_img = pil_img.convert("RGBA")
                results[link] = (pil_img.width, pil_img.height,
                                 pil_img.tobytes("raw", "RGBA"),
                                 dcol, album_colors)
            else:
                results[link] = None

        return results

    def _on_art_loaded(self, results: dict | None, load_id: int):
        """Main-thread callback: apply loaded artwork to grid items."""
        if results is None or self._load_id != load_id:
            return

        from PIL import Image

        try:
            for link, data in results.items():
                self._art_pending.discard(link)
                items = self._items_by_link.get(link, [])
                if not items:
                    continue

                if data is None:
                    for item in items:
                        item.applyImageResult(None, None, None)
                    continue

                w, h, rgba, dcol, album_colors = data
                pil_img = Image.frombytes("RGBA", (w, h), rgba)

                for item in items:
                    item.applyImageResult(pil_img, dcol, album_colors)

                # Remove from tracking — these items are done
                self._items_by_link.pop(link, None)

        except RuntimeError:
            pass  # Widget deleted

    def _onItemClicked(self, item_data: dict):
        """Handle grid item click."""
        self.item_selected.emit(item_data)

    # ── Sort / filter ─────────────────────────────────────────────────────────

    def setSort(self, key: str, reverse: bool = False) -> None:
        """Apply a new sort order to the current item list."""
        self._sort_key = key
        self._sort_reverse = reverse
        self._apply_filter_and_sort()

    def setSearchFilter(self, query: str) -> None:
        """Filter grid items whose title contains *query* (case-insensitive)."""
        self._search_query = query
        self._apply_filter_and_sort()

    def resetFilters(self) -> None:
        """Reset sort and search to defaults without reloading source data."""
        self._sort_key = "title"
        self._sort_reverse = False
        self._search_query = ""
        self._apply_filter_and_sort()

    @staticmethod
    def _search_corpus(item: dict) -> str:
        """Build a single lowercase string of every searchable field for *item*.

        Albums:  album title + artist name + year
        Artists: artist name (= title)
        Genres:  genre name (= title)
        """
        parts = []
        for field in ("title", "artist"):
            v = item.get(field)
            if v:
                parts.append(str(v).lower())
        year = item.get("year")
        if year:
            parts.append(str(year))
        return " ".join(parts)

    def _apply_filter_and_sort(self) -> None:
        items = self._all_items

        if self._search_query:
            tokens = self._search_query.lower().split()
            filtered = []
            for x in items:
                words = self._search_corpus(x).split()
                if all(_token_matches(t, words) for t in tokens):
                    filtered.append(x)
            items = filtered

        def _key_fn(x):
            v = x.get(self._sort_key)
            if isinstance(v, str):
                return v.lower()
            return v if v is not None else 0

        items = sorted(items, key=_key_fn, reverse=self._sort_reverse)
        self.populateGrid(items)

    # ── Grid management ───────────────────────────────────────────────────────

    def rearrangeGrid(self):
        """Trigger a re-layout (flow layout handles this automatically)."""
        self._flow.activate()

    def clearGrid(self):
        """Clear all grid items to prepare for reloading."""
        self.timerActive = False
        self.pendingItems = deque()
        self._load_id += 1
        self._items_by_link.clear()
        self._art_pending.clear()
        self._all_items = []

        while self._flow.count():
            item = self._flow.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    if isinstance(widget, MusicBrowserGridItem):
                        widget.cleanup()
                    widget.deleteLater()

        self.gridItems = []

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        # Explicitly set minimum height from the flow layout's heightForWidth
        # so the scroll area knows the correct content height.  QScrollArea's
        # built-in heightForWidth propagation is unreliable when items are
        # added incrementally via QTimer while the widget is hidden or the
        # viewport hasn't settled yet.
        w = a0.size().width() if a0 else self.width()
        if w > 0 and self._flow.count():
            self.setMinimumHeight(self._flow.heightForWidth(w))

    def showEvent(self, a0):
        super().showEvent(a0)
        # When the widget becomes visible (e.g. stacked-widget page switch),
        # defer the relayout to after Qt finishes settling geometry.
        # Items added while hidden (width=0) are all at (0,0); activate() is
        # a no-op when Qt thinks the layout is current, so we call
        # setGeometry() directly to force a real repositioning pass.
        if self._flow.count():
            QTimer.singleShot(0, self._force_relayout)

    def _force_relayout(self):
        w = self.width()
        if w > 0 and self._flow.count():
            self._flow.setGeometry(self.rect())
            self.setMinimumHeight(self._flow.heightForWidth(w))
