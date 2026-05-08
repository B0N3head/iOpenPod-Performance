import difflib
import logging
from collections import Counter, deque
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QEvent, QRect, QSize, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLayout, QLayoutItem, QScrollArea, QSizePolicy
from .MBGridViewItem import MusicBrowserGridItem
from ..styles import Metrics

if TYPE_CHECKING:
    from app_core.services import DeviceSessionService, LibraryCacheLike

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
_VIRTUALIZE_MIN_ITEMS = 200  # switch to pooled widgets beyond this count
_VIRTUAL_ROW_BUFFER = 1  # extra rows above/below viewport
_VIRTUAL_SCROLL_THROTTLE_MS = 16


class MusicBrowserGrid(QFrame):
    """Grid view that displays albums, artists, or genres as clickable items."""
    item_selected = pyqtSignal(dict)  # Emits when an item is clicked

    def __init__(
        self,
        *,
        device_sessions: "DeviceSessionService | None" = None,
        library_cache: "LibraryCacheLike | None" = None,
    ):
        super().__init__()
        self._device_sessions = device_sessions
        self._library_cache = library_cache
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

        # Scroll area binding (for virtualized layout updates)
        self._scroll_area: QScrollArea | None = None

        # Virtualized grid state
        self._virtual_enabled = False
        self._virtual_items: list[dict] = []
        self._virtual_pool: list[MusicBrowserGridItem] = []
        self._virtual_visible: dict[int, MusicBrowserGridItem] = {}
        self._virtual_columns = 1
        self._virtual_refresh_scheduled = False
        self._virtual_force_refresh = False
        self._virtual_last_range: tuple[int, int, int] | None = None

        # Widget reuse tracking (non-virtual)
        self._item_widgets_by_key: dict[tuple, MusicBrowserGridItem] = {}
        self._item_order_keys: list[tuple] = []

        # Artwork loading state
        self._items_by_link: dict[int, list[MusicBrowserGridItem]] = {}  # mhiiLink -> items waiting for art
        self._art_pending: set[int] = set()  # links currently being loaded
        self._art_seen: set[int] = set()  # links confirmed missing artwork

        # Sort / filter state
        self._all_items: list[dict] = []
        self._sort_key: str = "title"
        self._sort_reverse: bool = False
        self._search_query: str = ""
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter_and_sort)

    def attachScrollArea(self, scroll_area: QScrollArea | None) -> None:
        """Bind the grid to a QScrollArea to drive virtualized updates."""
        if self._scroll_area is scroll_area:
            return

        if self._scroll_area is not None:
            try:
                self._scroll_area.verticalScrollBar().valueChanged.disconnect(
                    self._on_scroll_changed
                )
            except Exception:
                pass
            try:
                self._scroll_area.viewport().removeEventFilter(self)
            except Exception:
                pass

        self._scroll_area = scroll_area
        if scroll_area is None:
            return

        scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_scroll_changed
        )
        scroll_area.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if (
            self._scroll_area is not None
            and obj is self._scroll_area.viewport()
            and event.type() in (QEvent.Type.Resize, QEvent.Type.Show)
        ):
            self._schedule_virtual_refresh(force=True)
        return super().eventFilter(obj, event)

    def loadCategory(self, category: str):
        """Load and display items for the specified category."""
        from app_core.runtime import (
            build_album_list,
            build_artist_list,
            build_genre_list,
        )
        log.debug(f"loadCategory() called: {category}")

        self._current_category = category

        cache = self._library_cache
        if cache is None:
            return
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
        if self._virtual_enabled:
            self._set_virtual_items(items)
            return

        self.clearGrid(preserve_all_items=True)
        current_load_id = self._load_id

        self._item_order_keys = [self._item_key(item) for item in items]
        self.pendingItems = deque(zip(self._item_order_keys, items))

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

                key, item = self.pendingItems.popleft()

                if isinstance(item, dict):
                    gridItem = self._create_grid_item(item)
                    self.gridItems.append(gridItem)
                    self._item_widgets_by_key[key] = gridItem

                    # Track which items need artwork
                    if gridItem.mhiiLink is not None:
                        if getattr(gridItem, "_art_applied_link", None) != gridItem.mhiiLink:
                            if not self._apply_cached_art(gridItem) and int(gridItem.mhiiLink) not in self._art_seen:
                                self._items_by_link.setdefault(int(gridItem.mhiiLink), []).append(gridItem)

                elif isinstance(item, MusicBrowserGridItem):
                    gridItem = item
                    if not getattr(gridItem, "_click_connected", False):
                        gridItem.clicked.connect(self._onItemClicked)
                        gridItem._click_connected = True
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

    @staticmethod
    def _item_key(item: dict) -> tuple:
        """Stable identity key for reuse across sorts/filters."""
        return (
            item.get("category", ""),
            item.get("album") or "",
            item.get("artist") or "",
            item.get("title") or "",
            item.get("filter_key") or "",
            item.get("filter_value") or "",
        )

    @staticmethod
    def _normalize_item(item: dict) -> tuple[str, str, Any, dict]:
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
        return title, subtitle, mhiiLink, item_data

    def _create_grid_item(self, item: dict) -> MusicBrowserGridItem:
        title, subtitle, mhiiLink, item_data = self._normalize_item(item)
        gridItem = MusicBrowserGridItem(title, subtitle, mhiiLink, item_data)
        gridItem.setParent(self)
        gridItem.clicked.connect(self._onItemClicked)
        gridItem._click_connected = True
        gridItem._item_key = self._item_key(item)
        return gridItem

    def _apply_cached_art(self, widget: MusicBrowserGridItem) -> bool:
        """Apply cached art immediately if available."""
        link = widget.mhiiLink
        if link is None:
            return False

        if getattr(widget, "_art_applied_link", None) == link:
            return True

        try:
            from ..imgMaker import get_artwork
        except Exception:
            return False

        cached = get_artwork(int(link), mode="cache_only")
        if cached is None:
            return False

        img, dcol, album_colors = cached
        widget.applyImageResult(img, dcol, album_colors)
        return True

    # -------------------------------------------------------------------------
    # Batched artwork loading
    # -------------------------------------------------------------------------

    def _load_art_async(self):
        """Collect unique mhiiLinks and load artwork in background batches."""
        from app_core.runtime import ThreadPoolSingleton, Worker

        links_to_load = (
            set(self._items_by_link.keys())
            - self._art_pending
            - self._art_seen
        )
        if not links_to_load:
            return
        if self._device_sessions is None:
            return

        session = self._device_sessions.current_session()
        if not session.device_path or not session.artworkdb_path:
            return
        artwork_folder = session.artwork_folder_path or ""
        cancellation_token = self._device_sessions.manager().cancellation_token

        self._art_pending |= links_to_load
        load_id = self._load_id
        links_list = list(links_to_load)
        pool = ThreadPoolSingleton.get_instance()

        for i in range(0, len(links_list), _ART_BATCH_SIZE):
            chunk = links_list[i:i + _ART_BATCH_SIZE]
            worker = Worker(
                self._load_art_batch,
                chunk,
                session.artworkdb_path,
                artwork_folder,
                cancellation_token,
            )
            worker.signals.result.connect(
                lambda result, lid=load_id: self._on_art_loaded(result, lid)
            )
            pool.start(worker)

    @staticmethod
    def _load_art_batch(
        links: list[int],
        artworkdb_path: str,
        artwork_folder: str,
        cancellation_token: Any,
    ) -> dict:
        """Background worker: decode artwork + colors for a batch of mhiiLinks."""
        from ..imgMaker import configure_artwork_api, get_artwork
        import os

        if not artworkdb_path or not os.path.exists(artworkdb_path):
            return {}

        configure_artwork_api(artworkdb_path, artwork_folder)
        results: dict[int, tuple | None] = {}

        for link in links:
            if cancellation_token.is_cancelled():
                break
            result = get_artwork(int(link), mode="with_colors")
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
                    self._art_seen.add(link)
                    for item in items:
                        if item.mhiiLink == link:
                            item.applyImageResult(None, None, None)
                    continue

                w, h, rgba, dcol, album_colors = data
                pil_img = Image.frombytes("RGBA", (w, h), rgba)

                for item in items:
                    if item.mhiiLink == link:
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
        if self._search_timer.isActive():
            self._search_timer.stop()
        self._search_timer.start(250)

    def resetFilters(self) -> None:
        """Reset sort and search to defaults without reloading source data."""
        self._sort_key = "title"
        self._sort_reverse = False
        self._search_query = ""
        if self._search_timer.isActive():
            self._search_timer.stop()
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
        self._update_grid(items)

    def _update_grid(self, items: list[dict]) -> None:
        if self.timerActive or self.pendingItems:
            # Cancel any in-progress batch build before re-sorting.
            self.clearGrid(preserve_all_items=True)

        if self._should_virtualize(items):
            if not self._virtual_enabled:
                self._enter_virtual_mode()
            self._set_virtual_items(items)
            return

        if self._virtual_enabled:
            self._exit_virtual_mode()

        self._update_non_virtual(items)

    def _should_virtualize(self, items: list[dict]) -> bool:
        return len(items) >= _VIRTUALIZE_MIN_ITEMS and self._scroll_area is not None

    def _enter_virtual_mode(self) -> None:
        self.clearGrid(preserve_all_items=True)
        self._virtual_enabled = True

    def _exit_virtual_mode(self) -> None:
        self._clear_virtual_widgets(delete_widgets=True)
        self._virtual_enabled = False

    def _update_non_virtual(self, items: list[dict]) -> None:
        if not items:
            self.clearGrid(preserve_all_items=True)
            return

        new_keys = [self._item_key(item) for item in items]

        if not self.gridItems:
            self.populateGrid(items)
            return

        if Counter(new_keys) == Counter(self._item_order_keys):
            if new_keys != self._item_order_keys:
                self._reorder_existing_widgets(new_keys)
            return

        self._diff_rebuild_non_virtual(items, new_keys)

    def _reorder_existing_widgets(self, new_keys: list[tuple]) -> None:
        while self._flow.count():
            self._flow.takeAt(0)

        new_items: list[MusicBrowserGridItem] = []
        for key in new_keys:
            widget = self._item_widgets_by_key.get(key)
            if widget is None:
                continue
            self._flow.addWidget(widget)
            new_items.append(widget)

        self.gridItems = new_items
        self._item_order_keys = new_keys

        w = self.width()
        if w > 0:
            self.setMinimumHeight(self._flow.heightForWidth(w))

    def _diff_rebuild_non_virtual(self, items: list[dict], new_keys: list[tuple]) -> None:
        old_map = dict(self._item_widgets_by_key)
        self._item_widgets_by_key.clear()
        self._item_order_keys = new_keys
        self.gridItems = []
        self._items_by_link.clear()

        while self._flow.count():
            self._flow.takeAt(0)

        for item, key in zip(items, new_keys):
            widget = old_map.pop(key, None)
            if widget is None:
                widget = self._create_grid_item(item)
            else:
                title, subtitle, mhiiLink, item_data = self._normalize_item(item)
                widget.update_item_data(title, subtitle, mhiiLink, item_data)
                widget._item_key = key

            self._item_widgets_by_key[key] = widget
            self.gridItems.append(widget)
            self._flow.addWidget(widget)

            if widget.mhiiLink is not None:
                if not self._apply_cached_art(widget) and int(widget.mhiiLink) not in self._art_seen:
                    self._items_by_link.setdefault(int(widget.mhiiLink), []).append(widget)

        for widget in old_map.values():
            try:
                widget.cleanup()
            except Exception:
                pass
            widget.deleteLater()

        w = self.width()
        if w > 0:
            self.setMinimumHeight(self._flow.heightForWidth(w))

        self._load_art_async()

    # ── Virtualized layout ───────────────────────────────────────────────

    def _set_virtual_items(self, items: list[dict]) -> None:
        self._virtual_items = items
        self._virtual_force_refresh = True
        self._schedule_virtual_refresh(force=True)

    def _schedule_virtual_refresh(self, *, force: bool = False) -> None:
        if not self._virtual_enabled:
            return
        if force:
            self._virtual_force_refresh = True
        if self._virtual_refresh_scheduled:
            return
        self._virtual_refresh_scheduled = True
        delay = 0 if force else _VIRTUAL_SCROLL_THROTTLE_MS
        QTimer.singleShot(delay, self._refresh_virtual_viewport)

    def _on_scroll_changed(self, _value: int) -> None:
        if self._virtual_enabled:
            self._schedule_virtual_refresh()

    def _refresh_virtual_viewport(self) -> None:
        self._virtual_refresh_scheduled = False
        if not self._virtual_enabled:
            return

        items = self._virtual_items
        count = len(items)
        if count == 0:
            self._clear_virtual_widgets(delete_widgets=False)
            self.setMinimumHeight(0)
            return

        width = self.width()
        if width <= 0:
            self._schedule_virtual_refresh(force=True)
            return

        columns = self._compute_columns(width)
        force_rebuild = self._virtual_force_refresh or columns != self._virtual_columns
        if columns != self._virtual_columns or self._virtual_force_refresh:
            self._virtual_columns = max(1, columns)
            self.columnCount = self._virtual_columns
            self._recycle_virtual_visible()
            self._virtual_last_range = None

        margin = Metrics.GRID_SPACING
        row_height = Metrics.GRID_ITEM_H + Metrics.GRID_SPACING
        total_rows = (count + self._virtual_columns - 1) // self._virtual_columns
        total_height = (
            margin * 2
            + total_rows * Metrics.GRID_ITEM_H
            + max(0, total_rows - 1) * Metrics.GRID_SPACING
        )
        self.setMinimumHeight(total_height)

        scroll_value = 0
        viewport_height = self.height()
        if self._scroll_area is not None and self._scroll_area.viewport() is not None:
            scroll_value = self._scroll_area.verticalScrollBar().value()
            viewport_height = self._scroll_area.viewport().height()
            if viewport_height <= 0:
                self._schedule_virtual_refresh(force=True)
                return

        first_row = max(0, (scroll_value - margin) // row_height)
        last_row = min(total_rows - 1, (scroll_value + viewport_height - margin) // row_height)
        first_row = max(0, first_row - _VIRTUAL_ROW_BUFFER)
        last_row = min(total_rows - 1, last_row + _VIRTUAL_ROW_BUFFER)

        start_index = first_row * self._virtual_columns
        end_index = min(count, (last_row + 1) * self._virtual_columns)

        current_range = (start_index, end_index, self._virtual_columns)
        if self._virtual_last_range == current_range and not force_rebuild:
            return
        self._virtual_last_range = current_range
        self._virtual_force_refresh = False

        visible_indices = set(range(start_index, end_index))
        for idx in list(self._virtual_visible.keys()):
            if idx not in visible_indices:
                widget = self._virtual_visible.pop(idx)
                widget.hide()
                self._virtual_pool.append(widget)

        for idx in range(start_index, end_index):
            widget = self._virtual_visible.get(idx)
            if widget is None:
                widget = self._virtual_pool.pop() if self._virtual_pool else None
                if widget is None:
                    widget = self._create_grid_item(items[idx])
                else:
                    title, subtitle, mhiiLink, item_data = self._normalize_item(items[idx])
                    widget.update_item_data(title, subtitle, mhiiLink, item_data)
                    widget._item_key = self._item_key(items[idx])
                if not getattr(widget, "_click_connected", False):
                    widget.clicked.connect(self._onItemClicked)
                    widget._click_connected = True
                self._virtual_visible[idx] = widget

            row = idx // self._virtual_columns
            col = idx % self._virtual_columns
            x = margin + col * (Metrics.GRID_ITEM_W + Metrics.GRID_SPACING)
            y = margin + row * (Metrics.GRID_ITEM_H + Metrics.GRID_SPACING)
            widget.setGeometry(QRect(x, y, Metrics.GRID_ITEM_W, Metrics.GRID_ITEM_H))
            widget.show()

        ordered_indices = sorted(self._virtual_visible)
        self.gridItems = [self._virtual_visible[i] for i in ordered_indices]

        self._items_by_link.clear()
        for widget in self.gridItems:
            if widget.mhiiLink is None:
                continue
            if getattr(widget, "_art_applied_link", None) == widget.mhiiLink:
                continue
            if not self._apply_cached_art(widget) and int(widget.mhiiLink) not in self._art_seen:
                self._items_by_link.setdefault(int(widget.mhiiLink), []).append(widget)

        self._load_art_async()

    @staticmethod
    def _compute_columns(width: int) -> int:
        margin = Metrics.GRID_SPACING
        usable = max(1, width - (margin * 2))
        cell = Metrics.GRID_ITEM_W + Metrics.GRID_SPACING
        return max(1, (usable + Metrics.GRID_SPACING) // cell)

    def _recycle_virtual_visible(self) -> None:
        for widget in self._virtual_visible.values():
            widget.hide()
            self._virtual_pool.append(widget)
        self._virtual_visible.clear()

    def _clear_virtual_widgets(self, *, delete_widgets: bool) -> None:
        for widget in list(self._virtual_visible.values()) + list(self._virtual_pool):
            widget.hide()
            if delete_widgets:
                try:
                    widget.cleanup()
                except Exception:
                    pass
                widget.deleteLater()

        self._virtual_visible.clear()
        self._virtual_pool.clear()
        self._virtual_items = []
        self._virtual_last_range = None
        self.gridItems = []
        self._items_by_link.clear()

    # ── Grid management ───────────────────────────────────────────────────────

    def rearrangeGrid(self):
        """Trigger a re-layout (flow layout handles this automatically)."""
        if self._virtual_enabled:
            self._schedule_virtual_refresh(force=True)
        else:
            self._flow.activate()

    def clearGrid(self, preserve_all_items: bool = False):
        """Clear all grid items to prepare for reloading."""
        self.timerActive = False
        self.pendingItems = deque()
        self._load_id += 1
        self._items_by_link.clear()
        self._art_pending.clear()
        self._art_seen.clear()
        self._item_widgets_by_key.clear()
        self._item_order_keys = []
        self._virtual_items = []

        if self._virtual_enabled:
            self._clear_virtual_widgets(delete_widgets=True)

        while self._flow.count():
            item = self._flow.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    if isinstance(widget, MusicBrowserGridItem):
                        widget.cleanup()
                    widget.deleteLater()

        self.gridItems = []

        if not preserve_all_items:
            self._all_items = []

        self.setMinimumHeight(0)

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        if self._virtual_enabled:
            self._schedule_virtual_refresh(force=True)
            return
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
        if self._virtual_enabled:
            self._schedule_virtual_refresh(force=True)
            return
        # When the widget becomes visible (e.g. stacked-widget page switch),
        # defer the relayout to after Qt finishes settling geometry.
        # Items added while hidden (width=0) are all at (0,0); activate() is
        # a no-op when Qt thinks the layout is current, so we call
        # setGeometry() directly to force a real repositioning pass.
        if self._flow.count():
            QTimer.singleShot(0, self._force_relayout)

    def _force_relayout(self):
        if self._virtual_enabled:
            return
        w = self.width()
        if w > 0 and self._flow.count():
            self._flow.setGeometry(self.rect())
            self.setMinimumHeight(self._flow.heightForWidth(w))
