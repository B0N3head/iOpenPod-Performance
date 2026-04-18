import logging
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtWidgets import QFrame, QSplitter, QVBoxLayout, QSizePolicy, QStackedWidget
from .MBGridView import MusicBrowserGrid
from .MBListView import MusicBrowserList
from .playlistBrowser import PlaylistBrowser
from .podcastBrowser import PodcastBrowser
from .photoBrowser import PhotoBrowserWidget
from .trackListTitleBar import TrackListTitleBar
from .gridHeaderBar import GridHeaderBar
from ..styles import Colors, make_scroll_area

log = logging.getLogger(__name__)


class MusicBrowser(QFrame):
    """Main browser widget with grid and track list views."""

    def __init__(self):
        super().__init__()
        self._current_category = "Albums"
        self._tab_dirty: dict[str, bool] = {
            "Playlists": True,
            "Podcasts": True,
            "Photos": True,
        }
        self._tab_loaded: dict[str, bool] = {
            "Playlists": False,
            "Podcasts": False,
            "Photos": False,
        }

        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(0, 0, 0, 0)
        self.mainLayout.setSpacing(0)

        self.gridTrackSplitter = QSplitter(Qt.Orientation.Vertical)

        # Top: Header bar + Grid Browser in scroll area, wrapped in a container
        self.browserGrid = MusicBrowserGrid()
        self.browserGrid.item_selected.connect(self._onGridItemSelected)

        self.browserGridScroll = make_scroll_area()
        self.browserGridScroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.browserGridScroll.setMinimumHeight(0)
        self.browserGridScroll.setMinimumWidth(0)
        self.browserGridScroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.browserGridScroll.minimumSizeHint = lambda: QSize(0, 0)
        self.browserGridScroll.setWidget(self.browserGrid)

        self.gridHeaderBar = GridHeaderBar()
        self.gridHeaderBar.sort_changed.connect(
            lambda key, rev: self.browserGrid.setSort(key, rev)
        )
        self.gridHeaderBar.search_changed.connect(self.browserGrid.setSearchFilter)

        self.gridContainer = QFrame()
        self.gridContainer.setMinimumSize(0, 0)
        gridContainerLayout = QVBoxLayout(self.gridContainer)
        gridContainerLayout.setContentsMargins(0, 0, 0, 0)
        gridContainerLayout.setSpacing(0)
        gridContainerLayout.addWidget(self.gridHeaderBar)
        gridContainerLayout.addWidget(self.browserGridScroll)

        self.gridTrackSplitter.addWidget(self.gridContainer)

        # Bottom: Track Browser
        self.trackContainer = QFrame()
        self.trackContainerLayout = QVBoxLayout(self.trackContainer)
        self.trackContainerLayout.setContentsMargins(0, 0, 0, 0)
        self.trackContainerLayout.setSpacing(0)

        self.browserTrack = MusicBrowserList()
        self.browserTrack.setMinimumHeight(0)
        self.browserTrack.setMinimumWidth(0)
        self.browserTrack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.browserTrack.minimumSizeHint = lambda: QSize(0, 0)

        # Track Browser TitleBar
        self.trackListTitleBar = TrackListTitleBar(self.gridTrackSplitter)
        self.trackContainerLayout.addWidget(self.trackListTitleBar)
        self.trackContainerLayout.addWidget(self.browserTrack)

        self.gridTrackSplitter.addWidget(self.trackContainer)

        # Splitter properties
        handle = self.gridTrackSplitter.handle(1)
        if handle:
            handle.setEnabled(True)
        self.gridTrackSplitter.setCollapsible(0, True)
        self.gridTrackSplitter.setCollapsible(1, True)
        self.gridTrackSplitter.setHandleWidth((3))
        self.gridTrackSplitter.setStretchFactor(0, 2)
        self.gridTrackSplitter.setStretchFactor(1, 1)
        self.gridTrackSplitter.setMinimumSize(0, 0)
        self.gridTrackSplitter.setStyleSheet(f"""
            QSplitter::handle {{
                background: {Colors.BORDER_SUBTLE};
            }}
            QSplitter::handle:hover {{
                background: {Colors.ACCENT};
            }}
            QSplitter::handle:pressed {{
                background: {Colors.ACCENT_LIGHT};
            }}
        """)

        # Set initial sizes (60% grid, 40% tracks) or restore from settings
        try:
            from settings import get_settings
            saved = get_settings().splitter_sizes
            if isinstance(saved, list) and len(saved) == 2:
                self.gridTrackSplitter.setSizes([int(saved[0]), int(saved[1])])
            else:
                self.gridTrackSplitter.setSizes([600, 400])
        except Exception:
            self.gridTrackSplitter.setSizes([600, 400])

        # Persist splitter position on change
        self.gridTrackSplitter.splitterMoved.connect(self._save_splitter_sizes)

        # Playlist browser (shown when Playlists category is active)
        self.playlistBrowser = PlaylistBrowser()

        # Podcast browser (shown when Podcasts category is active)
        self.podcastBrowser = PodcastBrowser()
        self.photoBrowser = PhotoBrowserWidget()

        # Use a stacked widget to toggle between grid/track and playlist views
        self.stack = QStackedWidget()
        self.stack.addWidget(self.gridTrackSplitter)   # index 0
        self.stack.addWidget(self.playlistBrowser)      # index 1
        self.stack.addWidget(self.podcastBrowser)       # index 2
        self.stack.addWidget(self.photoBrowser)         # index 3

        self.mainLayout.addWidget(self.stack)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refreshCurrentCategory)

        self._bind_cache_signals()

    def _bind_cache_signals(self) -> None:
        """Mark heavy tabs dirty when cache-backed data changes."""
        try:
            from ..app import iTunesDBCache

            cache = iTunesDBCache.get_instance()
            cache.playlists_changed.connect(lambda: self._mark_tab_dirty("Playlists"))
            cache.photos_changed.connect(lambda: self._mark_tab_dirty("Photos"))
        except Exception:
            pass

    def _mark_tab_dirty(self, tab_name: str) -> None:
        if tab_name in self._tab_dirty:
            self._tab_dirty[tab_name] = True

    def _mark_all_tabs_dirty(self) -> None:
        for tab_name in self._tab_dirty:
            self._tab_dirty[tab_name] = True

    def _schedule_refresh_current_category(self) -> None:
        """Coalesce rapid category/data changes into one UI refresh tick."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(0)

    def reloadData(self):
        """Reload data from the current device."""
        self.browserGrid.clearGrid()
        self.browserTrack.clearTable(clear_cache=True)
        self.playlistBrowser.clear()
        self.podcastBrowser.clear()
        self.photoBrowser.clear()
        for tab_name in self._tab_loaded:
            self._tab_loaded[tab_name] = False
        self._mark_all_tabs_dirty()
        # Data will be loaded when cache emits data_ready

    def _save_splitter_sizes(self):
        """Persist the current splitter sizes to settings."""
        try:
            from settings import get_settings
            s = get_settings()
            s.splitter_sizes = list(self.gridTrackSplitter.sizes())
            s.save()
        except Exception:
            pass

    def _apply_constrained_splitter_sizes(self):
        """Apply splitter sizing with constraint: track list <= 50% of window height.

        Uses the entire window height (not just splitter) to ensure consistent
        sizing regardless of current layout state. Prevents track list from
        taking more than 50% of window height, ensuring grid stays visible.
        """
        try:
            from settings import get_settings
            saved = get_settings().splitter_sizes
            if isinstance(saved, list) and len(saved) == 2:
                grid_h, track_h = int(saved[0]), int(saved[1])
            else:
                grid_h, track_h = 600, 400
        except Exception:
            grid_h, track_h = 600, 400

        # Calculate 50% based on entire window height
        window = self.window()
        window_h = window.height() if window else 800
        max_track = window_h // 2

        # Constraint: track list should not exceed 50% of window height
        if track_h > max_track:
            track_h = max_track
            grid_h = window_h - track_h

        self.gridTrackSplitter.setSizes([grid_h, track_h])

    def onDataReady(self):
        """Called when iTunesDB cache is loaded. Refresh current view."""
        self._mark_all_tabs_dirty()
        self._schedule_refresh_current_category()

    def updateCategory(self, category: str):
        """Update the display for the selected category."""
        self._current_category = category
        self._schedule_refresh_current_category()

    def _refreshCurrentCategory(self):
        """Refresh display based on current category and cache state."""
        from ..app import iTunesDBCache
        cache = iTunesDBCache.get_instance()

        # Don't do anything if cache isn't ready yet
        if not cache.is_ready():
            return

        category = self._current_category

        if category == "Tracks":
            self.stack.setCurrentIndex(0)
            # Hide entire grid container (header + grid) for fullscreen tracklist
            self.gridContainer.hide()
            self.browserGrid.clearGrid()  # Clear grid to cancel pending image loads
            self.browserTrack.clearTable()  # Clear track list before reloading
            self.browserTrack.clearFilter()
            self.browserTrack.loadTracks(media_type_filter=0x01)  # Audio only
            self.trackListTitleBar.setTitle("All Tracks")
            self.trackListTitleBar.resetColor()
            self.trackListTitleBar.setFullscreenMode(True)
        elif category == "Playlists":
            self.stack.setCurrentIndex(1)
            self.trackListTitleBar.setFullscreenMode(False)
            if self._tab_dirty["Playlists"] or not self._tab_loaded["Playlists"]:
                self.playlistBrowser.loadPlaylists()
                self._tab_dirty["Playlists"] = False
                self._tab_loaded["Playlists"] = True
        elif category == "Podcasts":
            # Podcast manager — full subscription browser
            self.stack.setCurrentIndex(2)
            self.trackListTitleBar.setFullscreenMode(False)
            if self._tab_dirty["Podcasts"] or not self._tab_loaded["Podcasts"]:
                self._ensure_podcast_device()
                self._tab_dirty["Podcasts"] = False
                self._tab_loaded["Podcasts"] = True
        elif category == "Photos":
            self.stack.setCurrentIndex(3)
            self.trackListTitleBar.setFullscreenMode(False)
            if self._tab_dirty["Photos"] or not self._tab_loaded["Photos"]:
                self.photoBrowser.reload()
                self._tab_dirty["Photos"] = False
                self._tab_loaded["Photos"] = True
        elif category == "Audiobooks":
            # Non-music audio categories — hide entire grid container for fullscreen tracklist
            log.debug(f"  Showing {category} view")
            self.stack.setCurrentIndex(0)
            self.gridContainer.hide()
            self.browserGrid.clearGrid()
            self.browserTrack.clearTable()
            self.browserTrack.clearFilter()
            self.browserTrack.loadTracks(media_type_filter=0x08)  # MEDIA_TYPE_AUDIOBOOK
            self.trackListTitleBar.setTitle(category)
            self.trackListTitleBar.resetColor()
            self.trackListTitleBar.setFullscreenMode(True)
        elif category in ("Videos", "Movies", "TV Shows", "Music Videos"):
            # Video categories: hide entire grid container for fullscreen tracklist
            _MEDIA_TYPE_FILTER = {
                "Videos": 0x62,        # All video (VIDEO|MUSIC_VIDEO|TV_SHOW)
                "Movies": 0x02,        # MEDIA_TYPE_VIDEO
                "TV Shows": 0x40,      # MEDIA_TYPE_TV_SHOW
                "Music Videos": 0x20,  # MEDIA_TYPE_MUSIC_VIDEO
            }
            self.stack.setCurrentIndex(0)
            self.gridContainer.hide()
            self.browserGrid.clearGrid()
            self.browserTrack.clearTable()
            self.browserTrack.clearFilter()
            self.browserTrack.loadTracks(media_type_filter=_MEDIA_TYPE_FILTER[category])
            self.trackListTitleBar.setTitle(category)
            self.trackListTitleBar.resetColor()
            self.trackListTitleBar.setFullscreenMode(True)
        else:
            self.stack.setCurrentIndex(0)
            # Show grid for Albums, Artists, Genres
            self.gridContainer.show()
            self.gridHeaderBar.setCategory(category)
            self.gridHeaderBar.resetState()

            self.browserGrid.resetFilters()
            self.browserGrid.loadCategory(category)
            # Pre-load audio-only tracks so filterByAlbum/Artist/Genre
            # won't include video tracks in results.
            self.browserTrack.loadTracks(media_type_filter=0x01)
            self.browserTrack.clearFilter()
            self.trackListTitleBar.setTitle(f"Select a{'n' if category[0] in 'AE' else ''} {category[:-1]}")
            self.trackListTitleBar.resetColor()
            self.trackListTitleBar.setFullscreenMode(False)

            # Apply constrained splitter sizes (50% grid max, 50% track list min)
            self._apply_constrained_splitter_sizes()

    def _onGridItemSelected(self, item_data: dict):
        """Handle when a grid item is clicked."""
        category = item_data.get("category", "Albums")
        title = item_data.get("title", "")
        filter_key = item_data.get("filter_key")
        filter_value = item_data.get("filter_value")

        # Update title bar with album color
        self.trackListTitleBar.setTitle(title)
        dominant_color = item_data.get("dominant_color")
        if dominant_color:
            r, g, b = dominant_color
            album_colors = item_data.get("album_colors", {})
            text = album_colors.get("text")
            text_sec = album_colors.get("text_secondary")
            self.trackListTitleBar.setColor(r, g, b, text=text, text_secondary=text_sec)
        else:
            self.trackListTitleBar.resetColor()

        # Apply filter to track list
        if filter_key and filter_value:
            self.browserTrack.applyFilter(item_data)
        elif category == "Albums":
            album = item_data.get("album") or title
            artist = item_data.get("artist") or item_data.get("subtitle")
            self.browserTrack.filterByAlbum(album, artist)
        elif category == "Artists":
            self.browserTrack.filterByArtist(title)
        elif category == "Genres":
            self.browserTrack.filterByGenre(title)

    def _ensure_podcast_device(self):
        """Bind the podcast browser to the current iPod device if not done."""
        from ..app import DeviceManager
        from ipod_device import get_current_device

        dm = DeviceManager.get_instance()
        if not dm.device_path:
            return

        device = get_current_device()
        serial = (device.serial or device.firewire_guid or "_default") if device else "_default"
        self.podcastBrowser.set_device(serial, dm.device_path)
