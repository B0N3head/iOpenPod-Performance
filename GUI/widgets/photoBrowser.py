from __future__ import annotations

import copy
from collections import deque
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QFont, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from SyncEngine.photos import (
    PhotoDB,
    PhotoEditState,
    PhotoEntry,
    apply_photo_sync_plan,
    build_photo_library_from_device,
    build_photo_sync_plan,
    ensure_photo_visual_hashes,
    load_photo_preview,
    merge_photo_sync_plan,
    write_photo_db_metadata_only,
)
from ipod_device.artwork import ITHMB_FORMAT_MAP

from ..styles import (
    Colors,
    FONT_FAMILY,
    Metrics,
    make_scroll_area,
    sidebar_nav_css,
    sidebar_nav_selected_css,
)
from .browserChrome import (
    BrowserHeroHeader,
    BrowserPane,
    chrome_action_btn_css,
    style_browser_splitter,
)
from .formatters import format_size
from .gridHeaderBar import GridHeaderBar
from .MBGridView import _FlowLayout
from .photoTile import PhotoGridTile
from .photoViewer import PhotoViewerPane, pil_to_pixmap


class PhotoGridView(QFrame):
    currentIndexChanged = pyqtSignal(int)
    checkedChanged = pyqtSignal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flow = _FlowLayout(self, spacing=14)
        self._flow.setContentsMargins(14, 14, 14, 14)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._tiles: list[PhotoGridTile] = []
        self._selected_index = -1

    def clearGrid(self) -> None:
        self._selected_index = -1
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    widget.deleteLater()
        self._tiles.clear()
        self.setMinimumHeight(0)

    def addPhoto(
        self,
        title: str,
        pixmap: QPixmap | None,
        *,
        checkable: bool = False,
        checked: bool = False,
    ) -> None:
        index = len(self._tiles)
        tile = PhotoGridTile(title, checkable=checkable, parent=self)
        tile.setPixmap(pixmap)
        if checkable:
            tile.setChecked(checked)
            tile.checked_changed.connect(
                lambda state, idx=index: self.checkedChanged.emit(idx, state)
            )
        tile.clicked.connect(lambda idx=index: self.setCurrentIndex(idx))
        self._tiles.append(tile)
        self._flow.addWidget(tile)

    def setTilePixmap(self, index: int, pixmap: QPixmap | None) -> None:
        if index < 0 or index >= len(self._tiles):
            return
        self._tiles[index].setPixmap(pixmap)

    def setTileChecked(self, index: int, checked: bool) -> None:
        if index < 0 or index >= len(self._tiles):
            return
        self._tiles[index].setChecked(checked)

    def count(self) -> int:
        return len(self._tiles)

    def currentIndex(self) -> int:
        return self._selected_index

    def setCurrentIndex(self, index: int) -> None:
        if index < 0 or index >= len(self._tiles):
            index = -1
        if self._selected_index == index:
            return
        self._selected_index = index
        for i, tile in enumerate(self._tiles):
            tile.setSelected(i == index)
        self.currentIndexChanged.emit(index)

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        width = a0.size().width() if a0 else self.width()
        if width > 0 and self._flow.count():
            self.setMinimumHeight(self._flow.heightForWidth(width))

    def showEvent(self, a0):
        super().showEvent(a0)
        if self._flow.count():
            QTimer.singleShot(0, self._force_relayout)

    def _force_relayout(self) -> None:
        width = self.width()
        if width > 0 and self._flow.count():
            self._flow.setGeometry(self.rect())
            self.setMinimumHeight(self._flow.heightForWidth(width))


class _PhotoWriteWorker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        ipod_path: str,
        device_photos: PhotoDB,
        action: str,
        *,
        image_id: int | None = None,
        album_name: str = "",
        old_name: str = "",
        new_name: str = "",
    ):
        super().__init__()
        self._ipod_path = ipod_path
        self._device_photos = copy.deepcopy(device_photos)
        self._action = action
        self._image_id = image_id
        self._album_name = album_name
        self._old_name = old_name
        self._new_name = new_name

    def _delete_photo_fast_path(self) -> PhotoDB:
        if self._image_id is None:
            raise RuntimeError("No device photo selected.")

        photodb = copy.deepcopy(self._device_photos)
        photo = photodb.photos.pop(self._image_id, None)
        if photo is None:
            raise RuntimeError("Selected photo could not be resolved on the iPod.")

        for album in photodb.albums:
            album.members = [mid for mid in album.members if mid != self._image_id]

        if photo.full_res_path:
            full_res_path = Path(self._ipod_path) / "Photos" / Path(photo.full_res_path)
            try:
                full_res_path.unlink(missing_ok=True)
            except OSError:
                pass

        write_photo_db_metadata_only(self._ipod_path, photodb)
        return photodb

    def _resolve_photo_for_membership_action(self) -> PhotoEntry:
        if self._image_id is None:
            raise RuntimeError("No device photo selected.")
        photo = self._device_photos.photos.get(self._image_id)
        if photo is None or not photo.visual_hash:
            raise RuntimeError("Selected photo could not be resolved on the iPod.")
        return photo

    def _build_edits_for_action(self) -> PhotoEditState:
        edits = PhotoEditState()
        if self._action == "create_album":
            edits.created_albums.add(self._album_name)
            return edits
        if self._action == "rename_album":
            edits.renamed_albums[self._old_name] = self._new_name
            return edits
        if self._action == "delete_album":
            edits.deleted_albums.add(self._album_name)
            return edits

        photo = self._resolve_photo_for_membership_action()
        if self._action == "add_to_album":
            edits.membership_adds.add((photo.visual_hash, self._album_name))
            return edits
        if self._action == "remove_from_album":
            edits.membership_removals.add((photo.visual_hash, self._album_name))
            return edits

        raise RuntimeError(f"Unknown photo action: {self._action}")

    def _apply_edit_state(self, edits: PhotoEditState) -> PhotoDB:
        desired_library = build_photo_library_from_device(self._device_photos)
        plan = build_photo_sync_plan(
            desired_library,
            self._device_photos,
            edits,
            ipod_path=self._ipod_path,
        )

        needs_payload_writes = bool(
            plan.photos_to_add or plan.photos_to_remove or plan.photos_to_update
        )
        if needs_payload_writes:
            return apply_photo_sync_plan(self._ipod_path, plan)

        photodb = merge_photo_sync_plan(copy.deepcopy(self._device_photos), plan)
        write_photo_db_metadata_only(self._ipod_path, photodb)
        return photodb

    def run(self) -> None:
        try:
            if self._action == "delete_photo":
                photodb = self._delete_photo_fast_path()
                self.finished_ok.emit(photodb)
                return

            ensure_photo_visual_hashes(self._device_photos, self._ipod_path)
            edits = self._build_edits_for_action()
            photodb = self._apply_edit_state(edits)
            self.finished_ok.emit(photodb)
        except Exception as exc:
            self.failed.emit(str(exc))


class PhotoBrowserWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_album = ""
        self._device_db: PhotoDB | None = None
        self._filtered_items: list[tuple[str, PhotoEntry]] = []
        self._current_preview_photo: PhotoEntry | None = None
        self._current_format_ids: list[int] = []
        self._bound_cache = None
        self._search_query = ""
        self._sort_key = "title"
        self._sort_reverse = False
        self._album_buttons: dict[str, QPushButton] = {}
        self._selected_album_btn: QPushButton | None = None
        self._write_worker: _PhotoWriteWorker | None = None
        self._tile_pixmap_cache: dict[int, QPixmap] = {}
        self._preview_pixmap_cache: dict[tuple[int, int], QPixmap] = {}
        self._cache_marker: tuple[str, int, int] | None = None
        self._thumb_queue: deque[tuple[int, PhotoEntry, int]] = deque()
        self._grid_load_token = 0
        self._grid_device_path = ""
        self._pending_grid_device_path = ""
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload_now)
        self._grid_reload_timer = QTimer(self)
        self._grid_reload_timer.setSingleShot(True)
        self._grid_reload_timer.timeout.connect(self._run_grid_reload)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.timeout.connect(self._process_thumb_batch)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = BrowserHeroHeader("Photos", self)

        self.new_album_btn = QPushButton("New Album")
        self.add_to_album_btn = QPushButton("Add to Album")
        self.rename_album_btn = QPushButton("Rename Album")
        self.delete_album_btn = QPushButton("Delete Album")
        self.remove_from_album_btn = QPushButton("Remove from Album")
        self.delete_photo_btn = QPushButton("Delete Photo")
        for btn in (
            self.new_album_btn,
            self.add_to_album_btn,
            self.rename_album_btn,
            self.delete_album_btn,
            self.remove_from_album_btn,
            self.delete_photo_btn,
        ):
            btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(chrome_action_btn_css())
            header.actions_layout.addWidget(btn)
        header.actions_layout.addStretch()

        root.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        style_browser_splitter(splitter)
        root.addWidget(splitter, 1)

        self._album_panel = BrowserPane(
            "Albums",
            min_width=220,
            body_margins=(8, 2, 8, 8),
            parent=splitter,
        )

        self._album_scroll = make_scroll_area()
        self._album_inner = QWidget()
        self._album_inner.setStyleSheet("background: transparent; border: none;")
        self._album_inner_layout = QVBoxLayout(self._album_inner)
        self._album_inner_layout.setContentsMargins(0, 0, 0, 0)
        self._album_inner_layout.setSpacing(2)
        self._album_inner_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._album_scroll.setWidget(self._album_inner)
        self._album_panel.addWidget(self._album_scroll, 1)
        splitter.addWidget(self._album_panel)

        self._grid_panel = BrowserPane("", parent=splitter)

        self.grid_header = GridHeaderBar()
        self.grid_header.setCategory("Photos")
        self.grid_header.sort_changed.connect(self._on_sort_changed)
        self.grid_header.search_changed.connect(self._on_search_changed)
        self._grid_panel.addWidget(self.grid_header)

        self.photo_scroll = make_scroll_area()
        self.photo_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.photo_grid = PhotoGridView()
        self.photo_scroll.setWidget(self.photo_grid)
        self._grid_panel.addWidget(self.photo_scroll, 1)
        splitter.addWidget(self._grid_panel)

        self.viewer = PhotoViewerPane(
            heading="",
            empty_title="No photo selected",
            empty_summary="Select a photo to inspect its preview and album details.",
            parent=splitter,
        )
        splitter.addWidget(self.viewer)
        splitter.setSizes([240, 760, 340])

        self.photo_grid.currentIndexChanged.connect(self._on_photo_changed)
        self.viewer.variantSelected.connect(self._on_variant_selected)
        self.new_album_btn.clicked.connect(self._create_album)
        self.add_to_album_btn.clicked.connect(self._add_to_album)
        self.rename_album_btn.clicked.connect(self._rename_album)
        self.delete_album_btn.clicked.connect(self._delete_album)
        self.remove_from_album_btn.clicked.connect(self._remove_from_album)
        self.delete_photo_btn.clicked.connect(self._delete_photo)
        self._update_action_states()

    def bind_cache(self, cache):
        if self._bound_cache is cache:
            return
        cache.data_ready.connect(self.reload)
        cache.photos_changed.connect(self.reload)
        self._bound_cache = cache

    def clear(self):
        self._reload_timer.stop()
        self._grid_reload_timer.stop()
        self._thumb_timer.stop()
        self._clear_album_sidebar()
        self.photo_grid.clearGrid()
        self._current_preview_photo = None
        self._current_format_ids = []
        self.viewer.clearPreview()
        self._filtered_items = []
        self._thumb_queue.clear()
        self._grid_load_token += 1
        self._tile_pixmap_cache.clear()
        self._preview_pixmap_cache.clear()
        self._cache_marker = None
        self._update_action_states()

    def reload(self):
        self._reload_timer.start(0)

    def _reload_now(self):
        from ..app import DeviceManager, iTunesDBCache

        cache = iTunesDBCache.get_instance()
        photodb = cache.get_photo_db() or PhotoDB()
        self._device_db = photodb
        device_path = DeviceManager.get_instance().device_path or ""

        marker = (device_path, id(photodb), len(photodb.photos))
        if marker != self._cache_marker:
            self._tile_pixmap_cache.clear()
            self._preview_pixmap_cache.clear()
            self._cache_marker = marker

        album_names = self._album_names()
        target = self._current_album if self._current_album and self._current_album in album_names else "All Photos"
        self._rebuild_album_sidebar(album_names)
        self._current_album = target
        self._highlight_album_button(target)

        self.grid_header.setCategory("Photos")
        self._schedule_grid_reload(device_path)

    def _album_names(self) -> list[str]:
        if self._device_db is None:
            return []
        return sorted(album.name for album in self._device_db.albums if album.album_type != 1)

    def _all_items(self) -> list[tuple[str, PhotoEntry]]:
        if self._device_db is None:
            return []
        return [(photo.visual_hash or str(photo.image_id), photo) for photo in self._device_db.photos.values()]

    def _on_search_changed(self, query: str):
        self._search_query = query.strip().lower()
        from ..app import DeviceManager
        self._schedule_grid_reload(DeviceManager.get_instance().device_path or "")

    def _on_sort_changed(self, key: str, reverse: bool):
        self._sort_key = key
        self._sort_reverse = reverse
        from ..app import DeviceManager
        self._schedule_grid_reload(DeviceManager.get_instance().device_path or "")

    def _matches_search(self, photo: PhotoEntry) -> bool:
        if not self._search_query:
            return True
        parts = [
            self._device_photo_title(photo),
            str(photo.image_id),
            photo.full_res_path,
            " ".join(str(format_id) for format_id in self._photo_format_ids(photo)),
            " ".join(sorted(name for name in getattr(photo, "album_names", set()) if name)),
        ]
        haystack = " ".join(part for part in parts if part).lower()
        return self._search_query in haystack

    def _sort_items(self, items: list[tuple[str, PhotoEntry]]) -> list[tuple[str, PhotoEntry]]:
        if self._sort_key == "size":
            key_fn = self._size_sort_key
        elif self._sort_key == "album_count":
            key_fn = self._album_count_sort_key
        else:
            key_fn = self._title_sort_key
        return sorted(items, key=key_fn, reverse=self._sort_reverse)

    def _size_sort_key(self, item: tuple[str, PhotoEntry]) -> tuple[int, str]:
        photo = item[1]
        return self._device_storage_size(photo), self._device_photo_title(photo).lower()

    def _album_count_sort_key(self, item: tuple[str, PhotoEntry]) -> tuple[int, str]:
        photo = item[1]
        return len(getattr(photo, "album_names", set())), self._device_photo_title(photo).lower()

    def _title_sort_key(self, item: tuple[str, PhotoEntry]) -> tuple[str, int]:
        photo = item[1]
        return self._device_photo_title(photo).lower(), self._device_storage_size(photo)

    def _photo_subtitle(self, photo: PhotoEntry) -> str:
        album_names = sorted(name for name in getattr(photo, "album_names", set()) if name)
        if not album_names:
            return "All Photos"
        if len(album_names) <= 2:
            return ", ".join(album_names)
        return f"{album_names[0]}, {album_names[1]} +{len(album_names) - 2} more"

    def _device_photo_title(self, photo: PhotoEntry) -> str:
        if photo.full_res_path:
            stem = Path(photo.full_res_path).stem
            image_suffix = f"_{photo.image_id:05d}"
            if stem.endswith(image_suffix):
                stem = stem[:-len(image_suffix)] or stem
            if stem:
                return stem
            filename = Path(photo.full_res_path).name
            if filename:
                return filename
        return f"Photo {photo.image_id}"

    def _photo_format_ids(self, photo: PhotoEntry) -> list[int]:
        return sorted(
            photo.thumbs,
            key=lambda fmt_id: (
                -(photo.thumbs[fmt_id].width * photo.thumbs[fmt_id].height),
                fmt_id,
            ),
        )

    def _default_preview_format_id(self, format_ids: list[int]) -> int | None:
        for format_id in format_ids:
            fmt = ITHMB_FORMAT_MAP.get(format_id)
            if fmt is not None and fmt.role == "photo_full":
                return format_id
        return format_ids[0] if format_ids else None

    def _default_tile_format_id(self, photo: PhotoEntry) -> int | None:
        format_ids = self._photo_format_ids(photo)
        if not format_ids:
            return None

        role_priority = {
            "photo_thumb": 0,
            "photo_list": 1,
            "photo_preview": 2,
            "photo_large": 3,
            "photo_full": 4,
            "tv_out": 5,
        }

        return min(
            format_ids,
            key=lambda fmt_id: (
                role_priority.get((lambda fmt: fmt.role if fmt is not None else "")(ITHMB_FORMAT_MAP.get(fmt_id)), 9),
                int(fmt_id),
            ),
        )

    def _device_storage_size(self, photo: PhotoEntry) -> int:
        total = max(0, int(getattr(photo, "full_res_size", 0) or 0))
        total += sum(max(0, int(ref.size)) for ref in photo.thumbs.values())
        return total

    def _preview_pixmap(self, photo: PhotoEntry, *, format_id: int | None = None) -> QPixmap:
        from ..app import DeviceManager

        cache_key = (photo.image_id, int(format_id) if format_id is not None else -1)
        cached = self._preview_pixmap_cache.get(cache_key)
        if cached is not None:
            return cached

        pixmap = QPixmap()
        try:
            img = load_photo_preview(
                photo,
                DeviceManager.get_instance().device_path or "",
                format_id=format_id,
            )
            if img is not None:
                pixmap = pil_to_pixmap(img)
        except Exception:
            pixmap = QPixmap()

        self._preview_pixmap_cache[cache_key] = pixmap
        return pixmap

    def _format_usage_label(self, role: str, description: str) -> str:
        usage_map = {
            "photo_full": "Full-screen viewing on the iPod",
            "photo_preview": "Mid-size preview image",
            "photo_list": "List/grid thumbnail",
            "photo_thumb": "Small thumbnail cache",
            "photo_large": "Large photo cache",
            "tv_out": "TV output / slideshow video-out",
        }
        usage = usage_map.get(role)
        if usage:
            return usage
        if description:
            return description
        return role.replace("_", " ").title() if role else "Unknown device usage"

    def _format_photo_timestamp(self, unix_seconds: int) -> str:
        if not unix_seconds:
            return ""
        try:
            return datetime.fromtimestamp(unix_seconds).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            return str(unix_seconds)

    def _format_meta_sections(
        self,
        photo: PhotoEntry,
        selected_format_id: int | None,
        format_ids: list[int],
    ) -> list[tuple[str, list[tuple[str, str]]]]:
        sections: list[tuple[str, list[tuple[str, str]]]] = []

        album_names = sorted(name for name in getattr(photo, "album_names", set()) if name)
        album_label = ", ".join(album_names) if album_names else "All Photos"
        image_rows = [
            ("Image ID", str(photo.image_id)),
            ("Display Name", photo.display_name or self._device_photo_title(photo)),
            ("Albums", album_label),
            ("Format Variants", str(len(format_ids))),
            ("Created", self._format_photo_timestamp(int(getattr(photo, "created_at", 0) or 0))),
            ("Digitized", self._format_photo_timestamp(int(getattr(photo, "digitized_at", 0) or 0))),
            ("Visual Hash", photo.visual_hash),
            ("Source Path", photo.source_path),
        ]
        sections.append(("Image Record", image_rows))

        thumbs_bytes = sum(max(0, int(ref.size)) for ref in photo.thumbs.values())
        storage_rows = [
            ("Original Size", format_size(photo.original_size) if photo.original_size else ""),
            ("Full-Res Size", format_size(photo.full_res_size) if photo.full_res_size else ""),
            ("Full-Res Path", photo.full_res_path),
            ("Thumbnail Bytes", format_size(thumbs_bytes) if thumbs_bytes else ""),
            (
                "Total On Device",
                format_size(self._device_storage_size(photo)) if self._device_storage_size(photo) else "",
            ),
        ]
        sections.append(("Storage", storage_rows))

        if selected_format_id is not None:
            ref = photo.thumbs.get(selected_format_id)
            fmt = ITHMB_FORMAT_MAP.get(selected_format_id)

            width = ref.width if ref is not None else int(fmt.width) if fmt is not None else 0
            height = ref.height if ref is not None else int(fmt.height) if fmt is not None else 0
            usage = self._format_usage_label(
                fmt.role if fmt is not None else "",
                fmt.description if fmt is not None else "",
            )

            selected_rows = [
                ("Format ID", str(selected_format_id)),
                ("Resolution", f"{width} x {height}" if width and height else ""),
                ("Usage", usage),
                ("Role", fmt.role if fmt is not None else ""),
                ("Pixel Format", fmt.pixel_format if fmt is not None else ""),
                ("Row Bytes", str(fmt.row_bytes) if fmt is not None else ""),
                ("Ithmb File", ref.filename if ref is not None else ""),
                ("Offset", f"{ref.offset:,}" if ref is not None else ""),
                ("Stored Size", format_size(ref.size) if ref is not None and ref.size else ""),
                (
                    "Padding",
                    f"h={ref.hpad}, v={ref.vpad}" if ref is not None and (ref.hpad or ref.vpad) else "",
                ),
                (
                    "Format Table",
                    f"{fmt.width} x {fmt.height}" if fmt is not None else "",
                ),
                (
                    "Device Label",
                    fmt.description if fmt is not None else "",
                ),
            ]
            sections.append(("Selected Variant", selected_rows))

        variant_rows: list[tuple[str, str]] = []
        for format_id in format_ids:
            ref = photo.thumbs.get(format_id)
            fmt = ITHMB_FORMAT_MAP.get(format_id)
            width = ref.width if ref is not None else int(fmt.width) if fmt is not None else 0
            height = ref.height if ref is not None else int(fmt.height) if fmt is not None else 0
            parts: list[str] = []
            if width and height:
                parts.append(f"{width}x{height}")
            if ref is not None and ref.size:
                parts.append(format_size(ref.size))
            if ref is not None and ref.filename:
                parts.append(ref.filename)
            if ref is not None:
                parts.append(f"offset {ref.offset:,}")
            if fmt is not None and fmt.role:
                parts.append(fmt.role)
            variant_rows.append((f"Format {format_id}", " · ".join(parts)))
        sections.append(("All Device Variants", variant_rows))

        return sections

    def _show_photo_preview(self, photo: PhotoEntry, *, selected_format_id: int | None = None) -> None:
        self._current_preview_photo = photo
        format_ids = self._photo_format_ids(photo)
        self._current_format_ids = format_ids
        if selected_format_id is None or selected_format_id not in format_ids:
            selected_format_id = self._default_preview_format_id(format_ids)

        summary_parts = [self._photo_subtitle(photo)]
        if format_ids:
            summary_parts.append(f"{len(format_ids)} format variant{'s' if len(format_ids) != 1 else ''}")
        total_device_size = self._device_storage_size(photo)
        if total_device_size:
            summary_parts.append(f"{format_size(total_device_size)} on device")

        self.viewer.setPhoto(
            title=self._device_photo_title(photo),
            pixmap=self._preview_pixmap(photo, format_id=selected_format_id),
            summary=" · ".join(part for part in summary_parts if part),
            meta_sections=self._format_meta_sections(photo, selected_format_id, format_ids),
        )
        self.viewer.setVariantIds(
            format_ids,
            selected_id=selected_format_id,
            label="Formats",
        )

    def _schedule_grid_reload(self, device_path: str) -> None:
        self._pending_grid_device_path = device_path
        self._grid_reload_timer.start(40)

    def _run_grid_reload(self) -> None:
        self._reload_grid(self._pending_grid_device_path)

    def _reload_grid(self, device_path: str):
        self._thumb_timer.stop()
        self._thumb_queue.clear()
        self._grid_load_token += 1
        load_token = self._grid_load_token
        self._grid_device_path = device_path

        self.photo_grid.clearGrid()
        self._filtered_items = []
        album_name = "" if self._current_album in ("", "All Photos") else self._current_album

        for key, photo in self._all_items():
            if album_name:
                photo_albums = getattr(photo, "album_names", set())
                if album_name not in photo_albums:
                    continue
            if not self._matches_search(photo):
                continue
            self._filtered_items.append((key, photo))

        self._filtered_items = self._sort_items(self._filtered_items)

        for index, (_key, photo) in enumerate(self._filtered_items):
            cached = self._tile_pixmap_cache.get(photo.image_id)
            self.photo_grid.addPhoto(
                self._device_photo_title(photo),
                cached if cached is not None else QPixmap(),
            )
            if cached is None and device_path:
                self._thumb_queue.append((index, photo, load_token))

        if self._thumb_queue:
            self._thumb_timer.start(0)

        self._update_collection_summary()
        if self.photo_grid.count():
            self.photo_grid.setCurrentIndex(0)
        else:
            self.viewer.clearPreview(
                title="No photos found",
                summary="Try another album or broaden the search.",
            )
            self._update_action_states()

    def _process_thumb_batch(self) -> None:
        if not self._thumb_queue:
            return

        batch_size = 3
        for _ in range(batch_size):
            if not self._thumb_queue:
                break
            index, photo, token = self._thumb_queue.popleft()
            if token != self._grid_load_token:
                return

            pixmap = self._tile_pixmap_cache.get(photo.image_id)
            if pixmap is None:
                pixmap = QPixmap()
                try:
                    if self._grid_device_path:
                        tile_format_id = self._default_tile_format_id(photo)
                        img = load_photo_preview(
                            photo,
                            self._grid_device_path,
                            format_id=tile_format_id,
                        )
                        if img is None:
                            img = load_photo_preview(photo, self._grid_device_path)
                        if img is not None:
                            img.thumbnail((132, 132))
                            pixmap = pil_to_pixmap(img)
                except Exception:
                    pixmap = QPixmap()
                self._tile_pixmap_cache[photo.image_id] = pixmap

            if token == self._grid_load_token:
                self.photo_grid.setTilePixmap(index, pixmap)

        if self._thumb_queue:
            self._thumb_timer.start(1)

    def _update_collection_summary(self):
        pass

    def _current_photo(self) -> tuple[str | None, PhotoEntry | None]:
        row = self.photo_grid.currentIndex()
        if row < 0 or row >= len(self._filtered_items):
            return None, None
        return self._filtered_items[row]

    def _clear_album_sidebar(self):
        self._album_buttons.clear()
        self._selected_album_btn = None
        while self._album_inner_layout.count():
            item = self._album_inner_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()

    def _rebuild_album_sidebar(self, album_names: list[str]):
        self._clear_album_sidebar()
        self._add_album_button("All Photos")
        for name in album_names:
            self._add_album_button(name)
        self._album_inner_layout.addStretch()

    def _add_album_button(self, name: str):
        btn = QPushButton(name, self._album_inner)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_LG))
        btn.setStyleSheet(sidebar_nav_css())
        btn.clicked.connect(lambda _checked=False, album_name=name: self._on_album_changed(album_name))
        self._album_buttons[name] = btn
        self._album_inner_layout.addWidget(btn)

    def _highlight_album_button(self, album_name: str):
        if self._selected_album_btn is not None:
            self._selected_album_btn.setStyleSheet(sidebar_nav_css())
        btn = self._album_buttons.get(album_name) or self._album_buttons.get("All Photos")
        self._selected_album_btn = btn
        if btn is not None:
            btn.setStyleSheet(sidebar_nav_selected_css())

    def _update_action_states(self):
        is_writing = self._write_worker is not None and self._write_worker.isRunning()
        has_album = bool(self._selected_album_target())
        _key, photo = self._current_photo()
        has_photo = photo is not None
        can_add_to_album = has_photo and bool(self._available_album_targets(photo))
        self.new_album_btn.setEnabled(not is_writing)
        self.add_to_album_btn.setEnabled(not is_writing and can_add_to_album)
        self.rename_album_btn.setEnabled(not is_writing and has_album)
        self.delete_album_btn.setEnabled(not is_writing and has_album)
        self.remove_from_album_btn.setEnabled(not is_writing and has_album and has_photo)
        self.delete_photo_btn.setEnabled(not is_writing and has_photo)

    def _on_album_changed(self, album_name: str):
        self._current_album = album_name
        self._highlight_album_button(album_name)
        from ..app import DeviceManager
        self._schedule_grid_reload(DeviceManager.get_instance().device_path or "")
        self._update_action_states()

    def _on_photo_changed(self, row: int):
        if row < 0 or row >= len(self._filtered_items):
            self._current_preview_photo = None
            self._current_format_ids = []
            self.viewer.clearPreview()
            self._update_action_states()
            return

        _, photo = self._filtered_items[row]
        self._show_photo_preview(photo)
        self._update_action_states()

    def _on_variant_selected(self, format_id: int) -> None:
        if self._current_preview_photo is not None:
            self._show_photo_preview(self._current_preview_photo, selected_format_id=format_id)

    def _selected_album_target(self) -> str:
        return "" if self._current_album in ("", "All Photos") else self._current_album

    def _available_album_targets(self, photo: PhotoEntry) -> list[str]:
        return [
            name for name in self._album_names()
            if name and name not in getattr(photo, "album_names", set())
        ]

    def _show_save_indicator(self, state: str) -> None:
        sidebar = getattr(self.window(), "sidebar", None)
        if sidebar is not None and hasattr(sidebar, "show_save_indicator"):
            sidebar.show_save_indicator(state)

    def _is_sync_running(self) -> bool:
        sync_checker = getattr(self.window(), "_is_sync_running", None)
        if callable(sync_checker):
            return bool(sync_checker())
        return False

    def _start_photo_write(
        self,
        action: str,
        *,
        image_id: int | None = None,
        album_name: str = "",
        old_name: str = "",
        new_name: str = "",
    ) -> None:
        from ..app import DeviceManager

        if self._write_worker is not None and self._write_worker.isRunning():
            QMessageBox.information(self, "Photo Save In Progress", "Please wait for the current photo save to finish.")
            return
        if self._is_sync_running():
            QMessageBox.information(self, "Sync Running", "Wait for the current sync to finish before editing photos.")
            return
        if self._device_db is None:
            QMessageBox.warning(self, "No Photo Database", "The iPod photo database is not loaded yet.")
            return

        ipod_path = DeviceManager.get_instance().device_path or ""
        if not ipod_path:
            QMessageBox.warning(self, "No iPod Connected", "Select an iPod before editing device photos.")
            return

        self._show_save_indicator("saving")
        self._write_worker = _PhotoWriteWorker(
            ipod_path,
            self._device_db,
            action,
            image_id=image_id,
            album_name=album_name,
            old_name=old_name,
            new_name=new_name,
        )
        self._write_worker.finished_ok.connect(self._on_photo_write_ok)
        self._write_worker.failed.connect(self._on_photo_write_failed)
        self._write_worker.finished.connect(self._on_photo_write_finished)
        self._write_worker.finished.connect(self._write_worker.deleteLater)
        self._write_worker.start()
        self._update_action_states()

    def _on_photo_write_ok(self, photodb: object) -> None:
        from ..app import iTunesDBCache

        if isinstance(photodb, PhotoDB):
            self._device_db = photodb
            self._tile_pixmap_cache.clear()
            self._preview_pixmap_cache.clear()
            self._cache_marker = None
            iTunesDBCache.get_instance().replace_photo_db(photodb)
        self._show_save_indicator("saved")

    def _on_photo_write_failed(self, error_msg: str) -> None:
        self._show_save_indicator("error")
        QMessageBox.warning(self, "Photo Save Failed", f"Could not save photo changes to the iPod:\n{error_msg}")

    def _on_photo_write_finished(self) -> None:
        self._write_worker = None
        self._update_action_states()

    def _create_album(self):
        name, ok = QInputDialog.getText(self, "New Album", "Album name:")
        if ok and name.strip():
            self._start_photo_write("create_album", album_name=name.strip())

    def _add_to_album(self):
        _key, photo = self._current_photo()
        if photo is None:
            return
        album_names = self._available_album_targets(photo)
        if not album_names:
            QMessageBox.information(self, "No Available Albums", "Create another album first, or choose a photo that is not already in every album.")
            return
        target_album, ok = QInputDialog.getItem(
            self,
            "Add Photo to Album",
            "Album:",
            album_names,
            0,
            False,
        )
        if ok and target_album:
            self._start_photo_write("add_to_album", image_id=photo.image_id, album_name=target_album)

    def _rename_album(self):
        current = self._selected_album_target()
        if not current:
            return
        new_name, ok = QInputDialog.getText(self, "Rename Album", "New album name:", text=current)
        if ok and new_name.strip() and new_name.strip() != current:
            self._start_photo_write("rename_album", old_name=current, new_name=new_name.strip())

    def _delete_album(self):
        current = self._selected_album_target()
        if not current:
            return
        if QMessageBox.question(self, "Delete Album", f"Delete '{current}' from the iPod now?") == QMessageBox.StandardButton.Yes:
            self._start_photo_write("delete_album", album_name=current)

    def _remove_from_album(self):
        current = self._selected_album_target()
        _key, photo = self._current_photo()
        if not current or photo is None:
            return
        self._start_photo_write("remove_from_album", image_id=photo.image_id, album_name=current)

    def _delete_photo(self):
        _key, photo = self._current_photo()
        if photo is None:
            return
        if QMessageBox.question(
            self,
            "Delete Photo",
            f"Delete '{self._device_photo_title(photo)}' from the iPod now?",
        ) == QMessageBox.StandardButton.Yes:
            self._start_photo_write("delete_photo", image_id=photo.image_id)
