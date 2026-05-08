from __future__ import annotations

from PyQt6.QtWidgets import QScrollArea

from GUI.widgets.pooledPhotoGrid import PhotoTileModel, PooledPhotoGridView


def _mount_grid(
    qtbot,
    *,
    width: int = 920,
    height: int = 620,
    checkable: bool = False,
) -> tuple[QScrollArea, PooledPhotoGridView]:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    grid = PooledPhotoGridView(checkable=checkable)
    scroll.setWidget(grid)
    grid.attachScrollArea(scroll)

    qtbot.addWidget(scroll)
    scroll.resize(width, height)
    scroll.show()
    qtbot.wait(50)
    return scroll, grid


def _build_records(count: int) -> list[PhotoTileModel]:
    return [
        PhotoTileModel(
            key=f"photo-{index:04d}",
            title=f"Photo {index:04d}",
            checked=bool(index % 2),
        )
        for index in range(count)
    ]


def test_pooled_photo_grid_recycles_widgets_on_scroll(qtbot):
    scroll, grid = _mount_grid(qtbot)
    records = _build_records(3000)

    grid.setRecords(records, fallback_index=0)
    qtbot.waitUntil(lambda: len(grid.gridItems) > 0, timeout=2000)

    initial_widget_ids = {id(widget) for widget in grid.findChildren(type(grid.gridItems[0]))}
    initial_titles = [widget.title_label.text() for widget in grid.gridItems]

    assert len(initial_widget_ids) < 100

    bar = scroll.verticalScrollBar()
    bar.setValue(max(1, bar.maximum() // 2))
    qtbot.waitUntil(
        lambda: grid.gridItems and grid.gridItems[0].title_label.text() not in initial_titles,
        timeout=2000,
    )

    scrolled_widget_ids = {id(widget) for widget in grid.findChildren(type(grid.gridItems[0]))}
    assert len(scrolled_widget_ids) < 100
    assert len(initial_widget_ids & scrolled_widget_ids) >= len(initial_widget_ids) // 2


def test_pooled_photo_grid_preserves_checked_state_by_record_key(qtbot):
    _scroll, grid = _mount_grid(qtbot, checkable=True)
    records = _build_records(50)

    grid.setRecords(records, fallback_index=0)
    qtbot.waitUntil(lambda: len(grid.gridItems) > 0, timeout=2000)

    grid.setRecordChecked("photo-0000", True)
    first = grid.recordAt(0)

    assert first is not None
    assert first.checked is True
    assert grid.gridItems[0].checkbox is not None
    assert grid.gridItems[0].checkbox.isChecked() is True
