import logging
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtWidgets import QLabel, QFrame, QVBoxLayout
from PyQt6.QtGui import QFont, QPixmap, QCursor, QImage
from ..hidpi import scale_pixmap_for_display
from ..styles import (
    Colors,
    FONT_FAMILY,
    Metrics,
    current_accent_rgb,
    display_accent_rgb,
    text_rgb_for_background,
)
from ..glyphs import glyph_pixmap
from .scrollingLabel import ScrollingLabel

log = logging.getLogger(__name__)


class MusicBrowserGridItem(QFrame):
    """A clickable grid item that displays album art, title, and subtitle."""
    clicked = pyqtSignal(dict)  # Emits item data when clicked

    def __init__(self, title: str, subtitle: str, mhiiLink, item_data: dict | None = None):
        super().__init__()
        self.title_text = title
        self.subtitle_text = subtitle
        self.mhiiLink = mhiiLink
        self.item_data = item_data or {"title": title, "subtitle": subtitle, "artwork_id_ref": mhiiLink}

        self.setFixedSize(QSize(Metrics.GRID_ITEM_W, Metrics.GRID_ITEM_H))
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._setupStyle()

        self.gridItemLayout = QVBoxLayout(self)
        self.gridItemLayout.setContentsMargins((10), (10), (10), (10))
        self.gridItemLayout.setSpacing((6))

        # Album art
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setFixedSize(QSize(Metrics.GRID_ART_SIZE, Metrics.GRID_ART_SIZE))
        self.img_label.setStyleSheet(f"""
            border: none;
            background: {Colors.SURFACE_ALT};
            border-radius: {Metrics.BORDER_RADIUS}px;
        """)
        self.gridItemLayout.addWidget(self.img_label)

        if mhiiLink is None:
            self._setPlaceholderImage()

        # Title
        self.title_label = ScrollingLabel(title)
        self.title_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD, QFont.Weight.DemiBold))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setStyleSheet(f"border: none; background: transparent; color: {Colors.TEXT_PRIMARY};")
        self.title_label.setFixedHeight((20))
        self.gridItemLayout.addWidget(self.title_label)

        # Subtitle
        self.subtitle_label = ScrollingLabel(subtitle)
        self.subtitle_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.subtitle_label.setStyleSheet(f"border: none; background: transparent; color: {Colors.TEXT_SECONDARY};")
        self.subtitle_label.setFixedHeight((18))
        self.gridItemLayout.addWidget(self.subtitle_label)

    def _setupStyle(self):
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE_RAISED};
                border: 1px solid {Colors.BORDER_SUBTLE};
                border-radius: {Metrics.BORDER_RADIUS_XL}px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QFrame:hover {{
                background-color: {Colors.SURFACE_ACTIVE};
                border: 1px solid {Colors.BORDER};
            }}
        """)

    def _setPlaceholderImage(self):
        """Set a placeholder when no artwork is available."""
        r, g, b = display_accent_rgb(
            current_accent_rgb(),
            background=Colors.BG_DARK,
            target_ratio=Colors.GRID_ART_CONTRAST_TARGET,
        )
        bg = f"rgba({r}, {g}, {b}, 14)"

        px = glyph_pixmap("music", Metrics.FONT_ICON_LG, Colors.TEXT_TERTIARY)
        if px:
            self.img_label.setPixmap(px)
        else:
            self.img_label.setText("\u266a")
            self.img_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_ICON_LG))
        self.img_label.setStyleSheet(f"""
            border: none;
            background: {bg};
            border-radius: {Metrics.BORDER_RADIUS}px;
            color: {Colors.TEXT_TERTIARY};
        """)

    def applyImageResult(self, pil_image, dcol, album_colors):
        """Apply a pre-loaded image result (called by MusicBrowserGrid)."""
        try:
            if not self.isVisible() and not self.parent():
                return
        except RuntimeError:
            return

        if pil_image is not None:
            pil_image = pil_image.convert("RGBA")
            data = pil_image.tobytes("raw", "RGBA")
            qimage = QImage(data, pil_image.width, pil_image.height, QImage.Format.Format_RGBA8888)
            qimage = qimage.copy()
            pixmap = scale_pixmap_for_display(
                QPixmap.fromImage(qimage),
                Metrics.GRID_ART_SIZE, Metrics.GRID_ART_SIZE,
                widget=self.img_label,
                aspect_mode=Qt.AspectRatioMode.KeepAspectRatio,
                transform_mode=Qt.TransformationMode.SmoothTransformation,
            )
            self.img_label.setPixmap(pixmap)
            self.img_label.setStyleSheet(f"""
                border: none;
                background: transparent;
                border-radius: {Metrics.BORDER_RADIUS}px;
            """)

            display_color = None
            if dcol:
                display_color = display_accent_rgb(
                    dcol,
                    background=Colors.BG_DARK,
                    target_ratio=Colors.GRID_ART_CONTRAST_TARGET,
                )
                self.item_data["dominant_color"] = dcol
                self.item_data["display_dominant_color"] = display_color
            if album_colors:
                self.item_data["album_colors"] = album_colors
                if display_color:
                    text = text_rgb_for_background(display_color)
                    secondary = (
                        (225, 230, 238)
                        if text == (255, 255, 255)
                        else (45, 50, 60)
                    )
                    rendered_album_colors = dict(album_colors)
                    rendered_album_colors["bg"] = display_color
                    rendered_album_colors["text"] = text
                    rendered_album_colors["text_secondary"] = secondary
                    self.item_data["display_album_colors"] = rendered_album_colors

            if display_color:
                r, g, b = display_color
                self.setStyleSheet(f"""
                    QFrame {{
                        background-color: rgba({r}, {g}, {b}, 30);
                        border: 1px solid rgba({r}, {g}, {b}, 25);
                        border-radius: {Metrics.BORDER_RADIUS_XL}px;
                        color: {Colors.TEXT_PRIMARY};
                    }}
                    QFrame:hover {{
                        background-color: rgba({r}, {g}, {b}, 55);
                        border: 1px solid rgba({r}, {g}, {b}, 45);
                    }}
                """)
        else:
            self._setPlaceholderImage()

    def mousePressEvent(self, a0):
        if a0 and a0.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item_data)
        super().mousePressEvent(a0)

    def cleanup(self):
        """Mark widget for destruction (no-op now that loading is centralized)."""
        pass
