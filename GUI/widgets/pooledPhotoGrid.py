from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QWidget

from .photoTile import PhotoGridTile
from .pooledCardGrid import PooledCardGrid


@dataclass
class PhotoTileModel:
    key: Hashable
    title: str
    pixmap: QPixmap | None = None
    checked: bool = False


class PooledPhotoGridView(PooledCardGrid):
    checkedChanged = pyqtSignal(int, bool)

    def __init__(self, *, checkable: bool = False, parent=None) -> None:
        super().__init__()
        if parent is not None:
            self.setParent(parent)
        self._checkable = checkable
        self._record_index_by_key: dict[Hashable, int] = {}

    def setRecords(
        self,
        records: list[PhotoTileModel],
        *,
        reset_scroll: bool = True,
        preserve_selection: bool = True,
        fallback_index: int = -1,
    ) -> None:
        self._record_index_by_key = {
            record.key: index for index, record in enumerate(records)
        }
        self._set_viewport_records(
            records,
            reset_scroll=reset_scroll,
            preserve_selection=preserve_selection,
            fallback_index=fallback_index,
        )

    def recordAt(self, index: int) -> PhotoTileModel | None:
        record = self._record_for_index(index)
        return record if isinstance(record, PhotoTileModel) else None

    def setRecordPixmap(self, key: Hashable, pixmap: QPixmap | None) -> None:
        index = self._record_index_by_key.get(key)
        if index is None:
            return
        record = self.recordAt(index)
        if record is None:
            return
        record.pixmap = pixmap
        widget = self._visible_widgets.get(index)
        if isinstance(widget, PhotoGridTile):
            widget.setPixmap(pixmap)

    def setRecordChecked(self, key: Hashable, checked: bool) -> None:
        index = self._record_index_by_key.get(key)
        if index is None:
            return
        record = self.recordAt(index)
        if record is None:
            return
        record.checked = checked
        widget = self._visible_widgets.get(index)
        if isinstance(widget, PhotoGridTile):
            widget.setChecked(checked)

    def setAllRecordsChecked(self, checked: bool) -> None:
        for record in self._viewport_records:
            if isinstance(record, PhotoTileModel):
                record.checked = checked
        for index, widget in self._visible_widgets.items():
            if not isinstance(widget, PhotoGridTile):
                continue
            record = self.recordAt(index)
            if record is None:
                continue
            widget.setChecked(record.checked)

    def clearGrid(self, preserve_all_items: bool = False) -> None:
        if not preserve_all_items:
            self._record_index_by_key.clear()
        super().clearGrid(preserve_all_items=preserve_all_items)

    def _record_identity(self, record: PhotoTileModel) -> Hashable:
        return record.key

    def _create_pooled_widget(self) -> PhotoGridTile:
        return PhotoGridTile("", checkable=self._checkable)

    def _connect_widget(self, widget) -> None:
        if not isinstance(widget, PhotoGridTile):
            return
        widget.clicked.connect(lambda w=widget: self._on_tile_clicked(w))
        if self._checkable:
            widget.checked_changed.connect(
                lambda checked, w=widget: self._on_tile_checked(w, checked)
            )

    def _bind_widget(
        self,
        widget: QWidget,
        record_index: int,
        record: PhotoTileModel,
    ) -> None:
        if not isinstance(widget, PhotoGridTile):
            return
        widget.setTitle(record.title)
        widget.setPixmap(record.pixmap)
        if self._checkable:
            widget.setChecked(record.checked)

    def _apply_widget_selection(self, widget, selected: bool) -> None:
        if isinstance(widget, PhotoGridTile):
            widget.setSelected(selected)

    def _on_tile_clicked(self, widget: PhotoGridTile) -> None:
        record_index = self._record_index_for_widget(widget)
        if record_index is not None:
            self.setCurrentIndex(record_index)

    def _on_tile_checked(self, widget: PhotoGridTile, checked: bool) -> None:
        record_index = self._record_index_for_widget(widget)
        if record_index is None:
            return
        record = self.recordAt(record_index)
        if record is None:
            return
        record.checked = checked
        self.checkedChanged.emit(record_index, checked)
