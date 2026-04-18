import logging
import os
import sys
import traceback
from pathlib import Path
from PyQt6.QtCore import QRunnable, pyqtSignal, pyqtSlot, QObject, QThreadPool, QThread, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QHBoxLayout, QMessageBox, QStackedWidget,
    QDialog, QVBoxLayout, QLabel, QPushButton, QProgressBar,
)
from GUI.widgets.musicBrowser import MusicBrowser
from GUI.widgets.sidebar import Sidebar
from GUI.widgets.syncReview import SyncReviewWidget, SyncWorker, PCFolderDialog, SyncExecuteWorker, QuickPlaylistSyncWorker
from GUI.widgets.settingsPage import SettingsPage
from GUI.widgets.backupBrowser import BackupBrowserWidget
from GUI.widgets.dropOverlay import DropOverlayWidget
from settings import get_settings
from GUI.notifications import Notifier
from GUI.styles import Colors, FONT_FAMILY, Metrics, btn_css
from GUI.glyphs import glyph_pixmap
import threading

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("iOpenPod")

        # Load users settings
        settings = get_settings()

        # Restore remembered window size
        self.resize(settings.window_width, settings.window_height)

        # Initialize system notifications
        self._notifier = Notifier.get_instance(self)

        # Drag-and-drop support
        self.setAcceptDrops(True)
        self._drop_worker = None

        # Sync worker reference
        self._sync_worker = None
        self._sync_execute_worker = None
        self._plan = None
        self._last_pc_folder = settings.media_folder or ""

        # Eject worker (safe-unmount off the UI thread)
        self._eject_worker: _EjectWorker | None = None

        # Quick metadata write (track flags, rating, etc.)
        self._quick_meta_worker: _QuickMetadataWorker | None = None
        self._quick_meta_timer = QTimer(self)
        self._quick_meta_timer.setSingleShot(True)
        self._quick_meta_timer.setInterval(1500)  # 1.5 s debounce
        self._quick_meta_timer.timeout.connect(self._start_quick_meta_write)

        # Quick playlist sync (add/remove/reorder tracks in playlist)
        self._quick_pl_worker: QuickPlaylistSyncWorker | None = None
        self._quick_pl_timer = QTimer(self)
        self._quick_pl_timer.setSingleShot(True)
        self._quick_pl_timer.setInterval(1500)  # 1.5 s debounce
        self._quick_pl_timer.timeout.connect(self._start_quick_playlist_sync)

        # Defer expensive theme rebuilds (e.g., match-iPod accent) so device
        # load/UI hydration is not blocked on the same event-loop turn.
        self._pending_theme_rebuild = False
        self._theme_rebuild_restore_page = 0
        self._theme_rebuild_timer = QTimer(self)
        self._theme_rebuild_timer.setSingleShot(True)
        self._theme_rebuild_timer.setInterval(20)
        self._theme_rebuild_timer.timeout.connect(self._run_deferred_theme_rebuild)

        # Central widget with stacked layout for main/sync views
        self.centralStack = QStackedWidget()
        self.setCentralWidget(self.centralStack)

        # Build all child widgets and connect signals
        self._build_ui()

        # Drop overlay (created after _build_ui so it sits on top)
        self._drop_overlay = DropOverlayWidget(self)

        # Connect device manager to reload data when device changes
        DeviceManager.get_instance().device_changed.connect(self.onDeviceChanged)

        # Connect cache ready signal to refresh UI
        iTunesDBCache.get_instance().data_ready.connect(self.onDataReady)
        self.musicBrowser.photoBrowser.bind_cache(iTunesDBCache.get_instance())

        # Schedule an immediate write whenever track flags are edited in the UI
        iTunesDBCache.get_instance().tracks_changed.connect(self._schedule_quick_meta_write)

        # Instant playlist sync whenever playlists are added/edited via context menu
        iTunesDBCache.get_instance().playlist_quick_sync.connect(self._quick_sync_playlists)

        # Restore last device path if it still looks like a real iPod
        if settings.last_device_path:
            device_manager = DeviceManager.get_instance()
            if device_manager.is_valid_ipod_root(settings.last_device_path):
                # Run a quick scan so discovered_ipod is populated
                # (needed for FireWire GUID, model info, etc.)
                try:
                    from ipod_device import scan_for_ipods
                    found_ipod = False
                    for ipod in scan_for_ipods():
                        if os.path.normpath(ipod.path) == os.path.normpath(settings.last_device_path):
                            device_manager.discovered_ipod = ipod
                            device_manager.device_path = settings.last_device_path
                            found_ipod = True
                            break
                    if not found_ipod:
                        logger.warning("Last device path '%s' not discovered during auto-restore scan", settings.last_device_path)
                except Exception as e:
                    logger.warning("Auto-restore scan failed: %s", e)

            # Default to a no-device placeholder page until an iPod is selected.
            self._show_default_page()

        # Auto-check for updates in the background (silent — no popup if up-to-date)
        self._startup_update_checker = None
        QTimer.singleShot(2000, self._auto_check_for_updates)

    def _auto_check_for_updates(self):
        """Silently check for updates at startup. Only shows UI if an update is found."""
        from GUI.auto_updater import UpdateChecker

        checker = UpdateChecker(self)
        self._startup_update_checker = checker

        def _on_result(result):
            self._startup_update_checker = None
            if result.error or not result.update_available:
                return
            # An update is available — delegate to the settings page handler
            self.settingsPage._handle_update_result(result)

        checker.result_ready.connect(_on_result)
        checker.start()

    def _build_ui(self):
        """Create child widgets and wire up signals.

        Called once from ``__init__`` and again by ``_on_theme_changed``
        to rebuild the UI with fresh themed styles.
        """
        # Main browsing page
        self.mainWidget = QWidget()
        self.mainLayout = QHBoxLayout(self.mainWidget)
        self.mainLayout.setContentsMargins(0, 0, 0, 0)

        self.musicBrowser = MusicBrowser()
        self.musicBrowser.podcastBrowser.podcast_sync_requested.connect(self._onPodcastSyncRequested)
        self.musicBrowser.browserTrack.remove_from_ipod_requested.connect(self._onRemoveFromIpod)
        self.musicBrowser.playlistBrowser.trackList.remove_from_ipod_requested.connect(self._onRemoveFromIpod)

        self.sidebar = Sidebar()
        self.sidebar.category_changed.connect(self.musicBrowser.updateCategory)
        self.sidebar.device_renamed.connect(self._onDeviceRenamed)
        self.sidebar.eject_requested.connect(self._onEjectDevice)
        self.sidebar.deviceButton.clicked.connect(self.selectDevice)
        self.sidebar.rescanButton.clicked.connect(self.resyncDevice)
        self.sidebar.syncButton.clicked.connect(self.startPCSync)
        self.sidebar.settingsButton.clicked.connect(self.showSettings)
        self.sidebar.backupButton.clicked.connect(self.showBackupBrowser)

        self.mainContentStack = QStackedWidget()

        self.mainLayout.addWidget(self.sidebar)
        self.mainLayout.addWidget(self.mainContentStack)
        self.centralStack.addWidget(self.mainWidget)  # Index 0

        # Sync review page
        self.syncReview = SyncReviewWidget()
        self.syncReview.cancelled.connect(self._onSyncReviewCancelled)
        self.syncReview.sync_requested.connect(self.executeSyncPlan)
        self.centralStack.addWidget(self.syncReview)  # Index 1

        # Settings page
        self.settingsPage = SettingsPage()
        self.settingsPage.closed.connect(self.hideSettings)
        self.settingsPage.theme_changed.connect(self._on_theme_changed)
        self.centralStack.addWidget(self.settingsPage)  # Index 2

        # Backup browser page
        self.backupBrowser = BackupBrowserWidget()
        self.backupBrowser.closed.connect(self.hideBackupBrowser)
        self.centralStack.addWidget(self.backupBrowser)  # Index 3

        # Selective sync browser page
        from GUI.widgets.selectiveSyncBrowser import SelectiveSyncBrowser
        self.selectiveSyncBrowser = SelectiveSyncBrowser()
        self.selectiveSyncBrowser.selection_done.connect(self._onSelectiveSyncDone)
        self.selectiveSyncBrowser.cancelled.connect(self._onSelectiveSyncCancelled)
        self.centralStack.addWidget(self.selectiveSyncBrowser)  # Index 4

        # No-device placeholder section (shown in content area; sidebar stays visible)
        self.noDeviceWidget = QWidget()
        no_device_layout = QVBoxLayout(self.noDeviceWidget)
        no_device_layout.setContentsMargins((36), (36), (36), (36))
        no_device_layout.setSpacing((12))

        no_device_layout.addStretch(1)

        title = QLabel("Select an iPod to continue")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont(FONT_FAMILY, Metrics.FONT_XXL, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        no_device_layout.addWidget(title)

        subtitle = QLabel(
            "No device is currently selected.\n"
            "Choose an iPod to access your library and sync tools."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
        subtitle.setStyleSheet(f"color: {Colors.TEXT_TERTIARY}; background: transparent;")
        no_device_layout.addWidget(subtitle)

        select_btn = QPushButton("Select Device")
        select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_btn.setFixedWidth((170))
        select_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD, QFont.Weight.DemiBold))
        select_btn.setStyleSheet(btn_css(
            bg=Colors.ACCENT,
            bg_hover=Colors.ACCENT_LIGHT,
            bg_press=Colors.ACCENT,
            fg=Colors.TEXT_ON_ACCENT,
            border="none",
            padding="8px 14px",
        ))
        select_btn.clicked.connect(self.selectDevice)

        select_row = QHBoxLayout()
        select_row.addStretch(1)
        select_row.addWidget(select_btn)
        select_row.addStretch(1)
        no_device_layout.addLayout(select_row)

        no_device_layout.addStretch(2)

        self.mainContentStack.addWidget(self.musicBrowser)   # Index 0
        self.mainContentStack.addWidget(self.noDeviceWidget)  # Index 1

    def _show_default_page(self):
        """Show main page and switch content area by device selection state."""
        has_device = bool(DeviceManager.get_instance().device_path)
        self.sidebar.setLibraryTabsVisible(has_device)
        self.mainContentStack.setCurrentIndex(0 if has_device else 1)
        self.centralStack.setCurrentIndex(0)

    def _rebuild_themed_ui(self, restore_page: int | None = None):
        """Tear down and rebuild all widgets after a theme/accent change.

        Args:
            restore_page: Stack index to show after rebuild. ``None`` keeps
                          the current page index.
        """
        from GUI.styles import build_palette, app_stylesheet

        if restore_page is None:
            restore_page = self.centralStack.currentIndex()

        self.setUpdatesEnabled(False)
        try:
            app = QApplication.instance()
            if isinstance(app, QApplication):
                app.setPalette(build_palette())
                app.setStyleSheet(app_stylesheet())

            # Tear down existing widgets
            while self.centralStack.count():
                w = self.centralStack.widget(0)
                if w is not None:
                    self.centralStack.removeWidget(w)
                    w.deleteLater()

            # Rebuild with newly set styles
            self._build_ui()
            self.musicBrowser.photoBrowser.bind_cache(iTunesDBCache.get_instance())

            # Restore page and settings state
            self.settingsPage.load_from_settings()
            self.centralStack.setCurrentIndex(
                min(restore_page, self.centralStack.count() - 1)
            )

            # If cache is loaded, reload UI from cache.
            # Use get_data() rather than get_tracks() so device info still
            # repopulates for empty libraries / partial parser data.
            cache = iTunesDBCache.get_instance()
            if cache.get_data() is not None:
                self.onDataReady()
        finally:
            self.setUpdatesEnabled(True)

    def _on_theme_changed(self):
        """Rebuild the entire UI after a live theme switch (from settings)."""
        self._rebuild_themed_ui(restore_page=2)

    def selectDevice(self):
        """Open device picker dialog to scan and select an iPod."""
        from GUI.widgets.devicePicker import DevicePickerDialog

        dialog = DevicePickerDialog(self)
        if dialog.exec() and dialog.selected_path:
            folder = dialog.selected_path
            device_manager = DeviceManager.get_instance()
            if device_manager.is_valid_ipod_root(folder):
                device_manager.discovered_ipod = dialog.selected_ipod
                device_manager.device_path = folder
                # Persist selection
                settings = get_settings()
                settings.last_device_path = folder
                settings.save()
            else:
                QMessageBox.warning(
                    self,
                    "Invalid iPod Folder",
                    "The selected folder does not appear to be a valid iPod root.\n\n"
                    "Expected structure:\n"
                    "  <selected folder>/iPod_Control/iTunes/\n\n"
                    "Please select the root folder of your iPod."
                )

    def onDeviceChanged(self, path: str):
        """Handle device selection - start loading data."""
        # Cancel any pending style rebuild from a prior device before starting
        # a new load cycle.
        if self._theme_rebuild_timer.isActive():
            self._theme_rebuild_timer.stop()
        self._pending_theme_rebuild = False

        # Clear the thread pool of pending tasks
        thread_pool = ThreadPoolSingleton.get_instance()
        thread_pool.clear()

        from .imgMaker import clear_artworkdb_cache
        clear_artworkdb_cache()

        self.musicBrowser.reloadData()
        self.sidebar.clearDeviceInfo()

        if path:
            self._show_default_page()
            # Start loading data (will emit data_ready when done)
            iTunesDBCache.get_instance().start_loading()
        else:
            self.sidebar.clearDeviceInfo()
            self._show_default_page()

    def resyncDevice(self):
        """Rebuild the cache from the current device."""
        device = DeviceManager.get_instance()
        if not device.device_path:
            return
        iTunesDBCache.get_instance().clear()
        self.onDeviceChanged(device.device_path)

    def onDataReady(self):
        """Called when iTunesDB data is loaded and ready."""
        cache = iTunesDBCache.get_instance()

        tracks = cache.get_tracks()
        albums = cache.get_albums()
        db_data = cache.get_data()
        classified = self._classify_tracks(tracks)

        from ipod_device import get_current_device
        from iTunesDB_Shared.constants import get_version_name
        dev = get_current_device()

        # If accent is "match-ipod", apply the device color and schedule a
        # deferred full rebuild so we do not block this load callback.
        if self._apply_match_ipod_accent(dev):
            self._schedule_themed_rebuild(restore_page=0)

        # Refresh disk usage so the storage bar reflects post-sync changes
        if dev and dev.path:
            try:
                import shutil as _shutil
                _total, _used, _free = _shutil.disk_usage(dev.path)
                dev.disk_size_gb = round(_total / 1e9, 1)
                dev.free_space_gb = round(_free / 1e9, 1)
            except OSError:
                pass

        device_name = dev.ipod_name if dev else "Unk iPod"
        model = dev.display_name if dev else "Unk iPod"

        db_version_hex = db_data.get('VersionHex', '') if db_data else ''
        db_version_name = get_version_name(db_version_hex) if db_version_hex else ''
        db_id = db_data.get('DatabaseID', 0) if db_data else 0

        self.sidebar.updateDeviceInfo(
            name=device_name,
            model=model,
            tracks=len(tracks),
            albums=len(albums),
            size_bytes=sum(t.get("size", 0) for t in tracks),
            duration_ms=sum(t.get("length", 0) for t in tracks),
            db_version_hex=db_version_hex,
            db_version_name=db_version_name,
            db_id=db_id,
            videos=len(classified["video"]),
            podcasts=len(classified["podcast"]),
            audiobooks=len(classified["audiobook"]),
        )
        self._update_sidebar_visibility(classified)
        self.musicBrowser.browserTrack.clearTable(clear_cache=True)
        self._update_podcast_statuses()
        self.musicBrowser.onDataReady()

    def _schedule_themed_rebuild(self, restore_page: int = 0) -> None:
        """Queue a deferred themed UI rebuild if one is not already pending."""
        self._theme_rebuild_restore_page = restore_page
        if self._pending_theme_rebuild:
            return
        self._pending_theme_rebuild = True
        self._theme_rebuild_timer.start()

    def _run_deferred_theme_rebuild(self) -> None:
        """Execute a previously scheduled themed rebuild."""
        self._pending_theme_rebuild = False
        self._rebuild_themed_ui(restore_page=self._theme_rebuild_restore_page)

    def _apply_match_ipod_accent(self, dev=None):
        """Re-apply accent color when 'match-ipod' is active and device is known.

        Returns True if the accent actually changed (UI rebuild needed).
        """
        from settings import get_settings
        s = get_settings()
        if s.accent_color != "match-ipod":
            return False
        if dev is None:
            from ipod_device import get_current_device
            dev = get_current_device()

        # Resolve the image filename for this device
        from ipod_device import resolve_image_filename, image_for_model
        img = ""
        if dev and dev.model_number:
            img = image_for_model(dev.model_number)
        if not img and dev and dev.model_family and dev.generation:
            img = resolve_image_filename(
                dev.model_family, dev.generation, dev.color or "",
            )

        from GUI.styles import resolve_accent_color, Colors
        accent_hex = resolve_accent_color("match-ipod", img)

        # Always apply the resolved accent, including "blue" fallback.
        # This ensures switching from a colorful device to a gray/white/black
        # device resets the UI back to the default accent.
        old_accent = Colors.ACCENT
        Colors.apply_theme(s.theme, s.high_contrast, accent_hex)
        return Colors.ACCENT != old_accent

    @staticmethod
    def _classify_tracks(tracks: list) -> dict[str, list]:
        """Partition tracks by media type into audio/video/podcast/audiobook."""
        from iTunesDB_Shared.constants import (
            MEDIA_TYPE_AUDIO, MEDIA_TYPE_PODCAST, MEDIA_TYPE_AUDIOBOOK,
            MEDIA_TYPE_VIDEO_MASK,
        )
        audio, video, podcast, audiobook = [], [], [], []
        for t in tracks:
            mt = t.get("media_type", 1)
            if mt == 0 or mt & MEDIA_TYPE_AUDIO:
                audio.append(t)
            if (mt & MEDIA_TYPE_VIDEO_MASK) and not (mt & MEDIA_TYPE_AUDIO) and mt != 0:
                video.append(t)
            if mt & MEDIA_TYPE_PODCAST:
                podcast.append(t)
            if mt & MEDIA_TYPE_AUDIOBOOK:
                audiobook.append(t)
        return {"audio": audio, "video": video, "podcast": podcast, "audiobook": audiobook}

    def _update_sidebar_visibility(self, classified: dict[str, list]) -> None:
        """Show/hide sidebar categories based on tracks and device capabilities."""
        from ipod_device import get_current_device
        dev = get_current_device()
        caps = dev.capabilities if dev else None

        has_video = len(classified["video"]) > 0
        has_podcast = len(classified["podcast"]) > 0
        photodb = iTunesDBCache.get_instance().get_photo_db()
        has_photos = bool(photodb and getattr(photodb, "photos", {}))

        self.sidebar.setVideoVisible(has_video or (caps.supports_video if caps else False))
        self.sidebar.setPodcastVisible(has_podcast or (caps.supports_podcast if caps else False))
        self.sidebar.setPhotoVisible(has_photos or (caps.supports_photo if caps else False))

    def _onDeviceRenamed(self, new_name: str):
        """Handle device rename from sidebar — update master playlist and write to iPod."""
        device = DeviceManager.get_instance()
        if not device.device_path:
            return

        cache = iTunesDBCache.get_instance()
        data = cache.get_data()
        if not data:
            return

        # Update DeviceInfo.ipod_name
        try:
            from ipod_device import get_current_device
            dev = get_current_device()
            if dev:
                dev.ipod_name = new_name
        except Exception:
            pass

        # Update master playlist Title in the cache
        playlists = cache.get_playlists()
        master_pl = None
        for pl in playlists:
            if pl.get("master_flag"):
                pl["Title"] = new_name
                master_pl = pl
                break

        if not master_pl:
            logger.warning("Could not find master playlist to rename")
            return

        logger.info("Renaming iPod to '%s'", new_name)

        # Write the full database to persist the rename
        self._rename_worker = _DeviceRenameWorker(device.device_path, new_name)
        self._rename_worker.finished_ok.connect(self._onRenameDone)
        self._rename_worker.failed.connect(self._onRenameFailed)
        self._rename_worker.start()

    def _onRenameDone(self):
        """Device rename write completed."""
        logger.info("iPod renamed successfully")
        Notifier.get_instance().notify("iPod Renamed", "Device name updated successfully")
        # Reload the database to reflect changes
        cache = iTunesDBCache.get_instance()
        cache.clear()
        cache.start_loading()

    def _onRenameFailed(self, error_msg: str):
        """Device rename write failed."""
        logger.error("iPod rename failed: %s", error_msg)
        QMessageBox.critical(
            self, "Rename Failed",
            f"Failed to rename iPod:\n{error_msg}"
        )

    # ── Eject ──────────────────────────────────────────────────────────

    def _onEjectDevice(self):
        """Safely eject the current iPod from the OS."""
        device = DeviceManager.get_instance()
        path = device.device_path
        if not path:
            return

        if self._is_sync_running():
            QMessageBox.warning(
                self, "Sync In Progress",
                "Please wait for the current sync to finish before ejecting."
            )
            return

        # Flush any pending in-memory edits before pulling the volume out.
        if self._quick_meta_timer.isActive():
            self._quick_meta_timer.stop()
            self._start_quick_meta_write()

        self.sidebar.device_card.eject_button.setEnabled(False)

        self._eject_worker = _EjectWorker(path)
        self._eject_worker.finished_ok.connect(self._onEjectDone)
        self._eject_worker.failed.connect(self._onEjectFailed)
        self._eject_worker.start()

    def _onEjectDone(self, message: str):
        logger.info("iPod ejected: %s", message)
        if self._eject_worker is not None:
            self._eject_worker.deleteLater()
            self._eject_worker = None
        Notifier.get_instance().notify("iPod Ejected", message)
        DeviceManager.get_instance().device_path = None
        # Forget the restored device so it doesn't auto-reconnect next launch.
        try:
            s = get_settings()
            s.last_device_path = ""
            s.save()
        except Exception:
            logger.warning("Failed to clear last_device_path from settings", exc_info=True)

    def _onEjectFailed(self, error_msg: str):
        logger.error("iPod eject failed: %s", error_msg)
        if self._eject_worker is not None:
            self._eject_worker.deleteLater()
            self._eject_worker = None
        # Re-enable the button so the user can retry.
        has_device = bool(DeviceManager.get_instance().device_path)
        self.sidebar.device_card.eject_button.setEnabled(has_device)
        QMessageBox.critical(
            self, "Eject Failed",
            f"Failed to eject the iPod:\n{error_msg}"
        )

    # ── Quick metadata write (track flags, rating, etc.) ────────────────────

    def _is_sync_running(self) -> bool:
        return (
            (self._sync_worker is not None and self._sync_worker.isRunning())
            or (self._sync_execute_worker is not None and self._sync_execute_worker.isRunning())
        )

    def _schedule_quick_meta_write(self):
        """Debounce-schedule a quick metadata write after track flags change."""
        if self._is_sync_running():
            # Full sync is running — edits will be included there; skip quick write.
            return
        device = DeviceManager.get_instance()
        if not device.device_path:
            return
        self._quick_meta_timer.start()  # Resets the timer if already running

    def _start_quick_meta_write(self):
        """Launch the quick metadata worker (called by debounce timer)."""
        if self._is_sync_running():
            return
        if self._quick_meta_worker is not None and self._quick_meta_worker.isRunning():
            # Already saving — reschedule so the in-flight write finishes first
            self._quick_meta_timer.start()
            return

        device = DeviceManager.get_instance()
        if not device.device_path:
            return

        cache = iTunesDBCache.get_instance()
        edits = cache.pop_track_edits()
        if not edits:
            return

        logger.info("Quick metadata write: %d track(s) edited", len(edits))
        self.sidebar.show_save_indicator("saving")

        self._quick_meta_worker = _QuickMetadataWorker(device.device_path, edits)
        self._quick_meta_worker.finished_ok.connect(self._on_quick_meta_ok)
        self._quick_meta_worker.failed.connect(self._on_quick_meta_failed)
        self._quick_meta_worker.start()

    def _on_quick_meta_ok(self):
        logger.info("Quick metadata write completed successfully")
        self.sidebar.show_save_indicator("saved")

    def _on_quick_meta_failed(self, error_msg: str):
        logger.error("Quick metadata write failed: %s", error_msg)
        self.sidebar.show_save_indicator("error")
        # Re-queue edits: they were already popped, so the worker's snapshot is
        # now lost.  Inform the user so they can re-edit or do a full sync.
        QMessageBox.warning(
            self, "Save Failed",
            f"Could not save track changes to iPod:\n{error_msg}\n\n"
            "Your edits are lost for this session. "
            "You can re-apply them and sync again."
        )

    # ── End quick metadata write ─────────────────────────────────────────────

    def startPCSync(self):
        """Start the PC to iPod sync process."""
        # If a quick metadata write is in progress, cancel the pending timer and
        # wait briefly for the worker to finish so we don't race on the DB.
        self._quick_meta_timer.stop()
        if self._quick_meta_worker is not None and self._quick_meta_worker.isRunning():
            self._quick_meta_worker.wait(5000)

        device = DeviceManager.get_instance()
        if not device.device_path:
            QMessageBox.warning(
                self,
                "No Device",
                "Please select an iPod device first."
            )
            return

        # Pre-flight: check for required external tools
        from SyncEngine.audio_fingerprint import is_fpcalc_available
        from SyncEngine.transcoder import is_ffmpeg_available
        from SyncEngine.dependency_manager import is_platform_supported

        missing_fpcalc = not is_fpcalc_available()
        missing_ffmpeg = not is_ffmpeg_available()

        if missing_fpcalc or missing_ffmpeg:
            names = []
            if missing_fpcalc:
                names.append("fpcalc (Chromaprint)")
            if missing_ffmpeg:
                names.append("FFmpeg")
            tool_list = " and ".join(names)

            if is_platform_supported():
                dlg = _MissingToolsDialog(self, tool_list, can_download=True)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    self._download_missing_tools_then_sync(missing_ffmpeg, missing_fpcalc)
                    return
                elif missing_fpcalc:
                    return
                # ffmpeg missing but user declined — let them continue with MP3/M4A only
            else:
                # Platform doesn't support auto-download
                lines = ""
                if missing_fpcalc:
                    lines += "fpcalc is required for sync.\nInstall from: https://acoustid.org/chromaprint\n\n"
                if missing_ffmpeg:
                    lines += "FFmpeg is needed for transcoding.\nInstall from: https://ffmpeg.org\n\n"
                lines += "You can also set custom paths in\nSettings → External Tools."

                dlg = _MissingToolsDialog(
                    self, tool_list, can_download=False, detail_lines=lines,
                )
                if not missing_fpcalc:
                    dlg.add_continue_option()

                if dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                # User clicked Continue Anyway (only possible when fpcalc is present)

        # Show folder selection dialog
        dialog = PCFolderDialog(self, self._last_pc_folder)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        self._last_pc_folder = dialog.selected_folder
        # Persist the folder choice
        settings = get_settings()
        settings.media_folder = dialog.selected_folder
        settings.save()

        # Branch: selective sync opens the PC library browser first
        if dialog.sync_mode == "selective":
            self.centralStack.setCurrentIndex(4)
            self.selectiveSyncBrowser.load(self._last_pc_folder)
            return

        # Switch to sync review view
        self.centralStack.setCurrentIndex(1)
        self.syncReview.show_loading()

        # Check device video capability
        from ipod_device import get_current_device
        dev = get_current_device()
        caps = dev.capabilities if dev else None
        supports_video = bool(caps and caps.supports_video)
        supports_podcast = bool(caps and caps.supports_podcast)

        # Gather GUI state to pass forward (not pulled by SyncEngine)
        cache = iTunesDBCache.get_instance()
        ipod_tracks = cache.get_tracks()

        track_edits = cache.get_track_edits()
        photo_edits = cache.get_photo_edits()
        try:
            sync_workers = settings.sync_workers
            rating_strategy = settings.rating_conflict_strategy
        except Exception:
            sync_workers = 0  # auto
            rating_strategy = "ipod_wins"

        device_manager = DeviceManager.get_instance()

        self._sync_worker = SyncWorker(
            pc_folder=self._last_pc_folder,
            ipod_tracks=ipod_tracks,
            ipod_path=device_manager.device_path or "",
            supports_video=supports_video,
            supports_podcast=supports_podcast,
            track_edits=track_edits,
            photo_edits=photo_edits,
            sync_workers=sync_workers,
            rating_strategy=rating_strategy,
        )
        self._sync_worker.progress.connect(self.syncReview.update_progress)
        self._sync_worker.finished.connect(self._onSyncDiffComplete)
        self._sync_worker.error.connect(self._onSyncError)
        self._sync_worker.start()

    def _download_missing_tools_then_sync(self, need_ffmpeg: bool, need_fpcalc: bool):
        """Download missing tools in a background thread, then restart sync."""
        progress = _DownloadProgressDialog(self)
        progress.show()

        # Keep a reference so it isn't garbage collected
        self._dl_progress = progress

        import threading

        def _do():
            from SyncEngine.dependency_manager import download_ffmpeg, download_fpcalc
            if need_fpcalc:
                download_fpcalc()
            if need_ffmpeg:
                download_ffmpeg()

            from PyQt6.QtCore import QMetaObject, Qt as QtCore_Qt
            QMetaObject.invokeMethod(
                self, "_on_tools_downloaded",
                QtCore_Qt.ConnectionType.QueuedConnection,
            )

        threading.Thread(target=_do, daemon=True).start()

    @pyqtSlot()
    def _on_tools_downloaded(self):
        """Called on main thread after tool downloads finish."""
        if hasattr(self, '_dl_progress') and self._dl_progress:
            self._dl_progress.close()
            self._dl_progress = None
        # Re-run sync now that tools should be available
        self.startPCSync()

    def _onPodcastSyncRequested(self, plan):
        """Handle podcast sync plan from PodcastBrowser.

        Receives a SyncPlan with podcast episodes as to_add items and
        sends it through the standard sync review pipeline.
        """
        self._plan = plan
        cache = iTunesDBCache.get_instance()
        self.syncReview._ipod_tracks_cache = cache.get_tracks() or []

        # Switch to sync review view and show the plan
        self.centralStack.setCurrentIndex(1)
        self.syncReview.show_plan(plan)

    def _onRemoveFromIpod(self, tracks: list):
        """Build a removal-only SyncPlan for the selected tracks and show sync review."""
        from SyncEngine.fingerprint_diff_engine import SyncAction, SyncItem, SyncPlan, StorageSummary

        if not tracks:
            return

        to_remove = []
        bytes_to_remove = 0
        for t in tracks:
            db_id = t.get("db_id")
            title = t.get("Title", "Unknown")
            artist = t.get("Artist", "")
            size = t.get("Size", 0)
            to_remove.append(SyncItem(
                action=SyncAction.REMOVE_FROM_IPOD,
                db_id=db_id,
                ipod_track=t,
                description=f"Remove: {artist} – {title}" if artist else f"Remove: {title}",
            ))
            bytes_to_remove += size

        plan = SyncPlan(
            to_remove=to_remove,
            storage=StorageSummary(bytes_to_remove=bytes_to_remove),
            removals_pre_checked=True,
        )
        self._plan = plan
        cache = iTunesDBCache.get_instance()
        self.syncReview._ipod_tracks_cache = cache.get_tracks() or []
        self.centralStack.setCurrentIndex(1)
        self.syncReview.show_plan(plan)

    def _onSyncDiffComplete(self, plan):
        """Called when sync diff calculation is complete."""
        self._plan = plan  # Store for executeSyncPlan to access matched_pc_paths
        # Provide iPod tracks cache so the review widget can list artwork-missing tracks
        cache = iTunesDBCache.get_instance()
        ipod_tracks = cache.get_tracks() or []
        self.syncReview._ipod_tracks_cache = ipod_tracks

        # ── Populate playlist change info on the plan ──────────────
        self._populate_playlist_changes(plan, cache)

        # ── Merge podcast managed plan ─────────────────────────────
        # This requires refreshing RSS feeds and possibly downloading
        # episodes, so it runs in the background.  The sync review is
        # shown after the podcast plan is merged (or immediately if
        # there are no podcast subscriptions).
        browser = self.musicBrowser.podcastBrowser
        store = browser._store
        feeds = store.get_feeds() if store else []

        if not feeds:
            self.syncReview.show_plan(plan)
            return

        self.syncReview.update_progress("podcast_sync", 0, 0, "Refreshing podcast feeds…")

        worker = Worker(
            self._build_podcast_plan_bg, feeds, ipod_tracks, store,
        )
        worker.signals.result.connect(
            lambda podcast_plan: self._on_podcast_plan_ready(plan, podcast_plan),
        )
        worker.signals.error.connect(
            lambda err: self._on_podcast_plan_error(plan, err),
        )
        ThreadPoolSingleton.get_instance().start(worker)

    def _build_podcast_plan_bg(self, feeds, ipod_tracks, store):
        """Background: refresh feeds from RSS, then build podcast plan.

        Episodes that need downloading are included in the plan — the
        actual download happens during sync execution.
        """
        import logging
        _log = logging.getLogger(__name__)

        from PodcastManager.feed_parser import fetch_feed
        from PodcastManager.podcast_sync import build_podcast_managed_plan

        # Refresh all feeds from RSS to get full episode catalogs
        refreshed = []
        for feed in feeds:
            try:
                refreshed.append(fetch_feed(feed.feed_url, existing=feed))
            except Exception as exc:
                _log.warning("Podcast refresh failed for %s: %s", feed.title, exc)
                refreshed.append(feed)
        store.update_feeds(refreshed)

        return build_podcast_managed_plan(refreshed, ipod_tracks, store)

    def _on_podcast_plan_ready(self, plan, podcast_plan) -> None:
        """Podcast plan built — merge into music plan and show."""
        if podcast_plan.to_add:
            plan.to_add.extend(podcast_plan.to_add)
            plan.storage.bytes_to_add += podcast_plan.storage.bytes_to_add
        if podcast_plan.to_remove:
            plan.to_remove.extend(podcast_plan.to_remove)
            plan.storage.bytes_to_remove += podcast_plan.storage.bytes_to_remove
        self.syncReview.show_plan(plan)

    def _on_podcast_plan_error(self, plan, error_tuple) -> None:
        """Podcast plan failed — show music-only plan."""
        import logging
        _, value, _ = error_tuple
        logging.getLogger(__name__).warning(
            "Failed to build podcast plan: %s", value,
        )
        self.syncReview.show_plan(plan)

    def _populate_playlist_changes(self, plan, cache: 'iTunesDBCache'):
        """Compute playlist add/edit/remove lists for the sync plan.

        Compares user-created/edited playlists (pending in cache) against
        the existing iPod playlists to categorize changes.
        """
        user_playlists = cache.get_user_playlists()
        if not user_playlists:
            return

        # Build set of existing iPod playlist IDs (from parsed DB)
        existing_ids: set[int] = set()
        data = cache.get_data()
        if data:
            for pl in data.get("mhlp", []):
                pid = pl.get("playlist_id", 0)
                if pid:
                    existing_ids.add(pid)
            for pl in data.get("mhlp_podcast", []):
                pid = pl.get("playlist_id", 0)
                if pid:
                    existing_ids.add(pid)
            for pl in data.get("mhlp_smart", []):
                pid = pl.get("playlist_id", 0)
                if pid:
                    existing_ids.add(pid)

        for upl in user_playlists:
            pid = upl.get("playlist_id", 0)
            is_new = upl.get("_isNew", False)
            if is_new or pid not in existing_ids:
                plan.playlists_to_add.append(upl)
            else:
                plan.playlists_to_edit.append(upl)

    def _onSyncError(self, error_msg: str):
        """Called when sync diff fails."""
        self.syncReview.show_error(error_msg)

    def _onSelectiveSyncDone(self, folder: str, selected_paths):
        """User finished picking tracks in selective sync; run diff on selection."""
        self._last_pc_folder = folder
        self.centralStack.setCurrentIndex(1)
        self.syncReview.show_loading()

        from ipod_device import get_current_device
        dev = get_current_device()
        caps = dev.capabilities if dev else None
        supports_video = bool(caps and caps.supports_video)
        supports_podcast = bool(caps and caps.supports_podcast)

        cache = iTunesDBCache.get_instance()
        ipod_tracks = cache.get_tracks()
        track_edits = cache.get_track_edits()
        selected_track_paths = selected_paths
        selected_photo_imports = ()
        if isinstance(selected_paths, dict):
            selected_track_paths = frozenset(selected_paths.get("tracks", ()))
            selected_photo_imports = tuple(selected_paths.get("photos", ()))

        photo_edits = None
        if selected_photo_imports:
            from SyncEngine.photos import PhotoEditState
            photo_edits = PhotoEditState(imported_files=list(selected_photo_imports))
        settings = get_settings()
        try:
            sync_workers = settings.sync_workers
            rating_strategy = settings.rating_conflict_strategy
        except Exception:
            sync_workers = 0
            rating_strategy = "ipod_wins"

        device_manager = DeviceManager.get_instance()
        self._sync_worker = SyncWorker(
            pc_folder=folder,
            ipod_tracks=ipod_tracks,
            ipod_path=device_manager.device_path or "",
            supports_video=supports_video,
            supports_podcast=supports_podcast,
            track_edits=track_edits,
            photo_edits=photo_edits,
            sync_workers=sync_workers,
            rating_strategy=rating_strategy,
            allowed_paths=frozenset(selected_track_paths),
        )
        self._sync_worker.progress.connect(self.syncReview.update_progress)
        self._sync_worker.finished.connect(self._onSyncDiffComplete)
        self._sync_worker.error.connect(self._onSyncError)
        self._sync_worker.start()

    def _onSelectiveSyncCancelled(self):
        """User cancelled selective sync browser."""
        self._show_default_page()

    def _onSyncReviewCancelled(self) -> None:
        """Handle cancel from the sync review page.

        During sync execution we only request cancellation and keep the page
        visible so partial-save confirmation (save vs discard) can be shown.
        """
        if self._sync_execute_worker is not None and self._sync_execute_worker.isRunning():
            self._sync_execute_worker.requestInterruption()
            return
        self.hideSyncReview()

    def hideSyncReview(self):
        """Return to the main browsing view, stopping any background work."""
        if self._sync_worker is not None and self._sync_worker.isRunning():
            self._sync_worker.requestInterruption()
        self._cleanup_sync_execute_worker()
        self._show_default_page()

    def _cleanup_sync_execute_worker(self):
        """Request interruption and disconnect all signals from the execute worker.

        The worker thread may continue running briefly (in-flight futures
        can't be force-killed), but with signals disconnected it can't
        affect the UI. Clearing the reference lets ``_is_sync_running``
        return False so a new sync can start cleanly.
        """
        w = self._sync_execute_worker
        if w is None:
            return
        if w.isRunning():
            w.requestInterruption()
        # Disconnect all signals so stale callbacks don't fire
        for sig in (w.progress, w.finished, w.error, w.confirm_partial_save):
            try:
                sig.disconnect()
            except TypeError:
                pass
        self._disconnect_skip_signal()
        self._sync_execute_worker = None

    def showSettings(self):
        """Show the settings page."""
        self.settingsPage.load_from_settings()
        self.centralStack.setCurrentIndex(2)

    def hideSettings(self):
        """Return from settings to the main browsing view."""
        # Re-read persisted settings to pick up changes
        settings = get_settings()
        self._last_pc_folder = settings.media_folder or self._last_pc_folder
        self._show_default_page()

    def showBackupBrowser(self):
        """Show the backup browser page."""
        self.backupBrowser.refresh()
        self.centralStack.setCurrentIndex(3)

    def hideBackupBrowser(self):
        """Return from backup browser to the main browsing view."""
        self._show_default_page()

    def executeSyncPlan(self, selected_items):
        """Execute the selected sync actions."""
        from SyncEngine.fingerprint_diff_engine import SyncAction, SyncPlan

        # Get device path
        device_manager = DeviceManager.get_instance()
        if not device_manager.device_path:
            QMessageBox.warning(self, "No Device", "No iPod device selected.")
            return

        # Filter items by action type
        add_items = [s for s in selected_items if s.action == SyncAction.ADD_TO_IPOD]
        remove_items = [s for s in selected_items if s.action == SyncAction.REMOVE_FROM_IPOD]
        meta_items = [s for s in selected_items if s.action == SyncAction.UPDATE_METADATA]
        file_items = [s for s in selected_items if s.action == SyncAction.UPDATE_FILE]
        art_items = [s for s in selected_items if s.action == SyncAction.UPDATE_ARTWORK]
        playcount_items = [s for s in selected_items if s.action == SyncAction.SYNC_PLAYCOUNT]
        rating_items = [s for s in selected_items if s.action == SyncAction.SYNC_RATING]

        # Create filtered plan
        # Carry matched_pc_paths, artwork info, and playlist changes from the original plan
        original_plan = self._plan  # stored in _onSyncDiffComplete

        # Playlists: only include if the playlist card's checkbox is checked
        pl_card = getattr(self.syncReview, '_playlist_card', None)
        include_playlists = (
            pl_card is not None and pl_card._select_all_cb.isChecked()
        ) if pl_card else True  # default to True if no card exists

        filtered_plan = SyncPlan(
            to_add=add_items,
            to_remove=remove_items,
            to_update_metadata=meta_items,
            to_update_file=file_items,
            to_update_artwork=art_items,
            to_sync_playcount=playcount_items,
            to_sync_rating=rating_items,
            matched_pc_paths=original_plan.matched_pc_paths if original_plan else {},
            _stale_mapping_entries=original_plan._stale_mapping_entries if original_plan else [],
            _integrity_removals=original_plan._integrity_removals if original_plan else [],
            mapping=original_plan.mapping if original_plan else None,
            playlists_to_add=original_plan.playlists_to_add if (original_plan and include_playlists) else [],
            playlists_to_edit=original_plan.playlists_to_edit if (original_plan and include_playlists) else [],
            playlists_to_remove=original_plan.playlists_to_remove if (original_plan and include_playlists) else [],
            photo_plan=self.syncReview.get_selected_photo_plan() if original_plan else None,
        )

        if not filtered_plan.has_changes:
            return

        # Show progress in sync review widget
        self.syncReview.show_executing()

        # Respect the user's pre-sync backup choice from the prompt
        skip_backup = getattr(self.syncReview, '_skip_presync_backup', False)

        # Gather GUI state to pass to executor (instead of it pulling from GUI)
        cache = iTunesDBCache.get_instance()
        user_playlists = cache.get_user_playlists()

        def _on_sync_complete():
            """Called by executor after successful DB write to clear pending state."""
            c = iTunesDBCache.get_instance()
            if c.has_pending_playlists():
                c._user_playlists.clear()
            if c.has_pending_track_edits():
                c.clear_track_edits()
            c.clear_photo_edits()

        # Start sync execution worker
        self._sync_execute_worker = SyncExecuteWorker(
            ipod_path=device_manager.device_path,
            plan=filtered_plan,
            skip_backup=skip_backup,
            user_playlists=user_playlists,
            on_sync_complete=_on_sync_complete,
        )
        self._sync_execute_worker.progress.connect(self.syncReview.update_execute_progress)
        self._sync_execute_worker.finished.connect(self._onSyncExecuteComplete)
        self._sync_execute_worker.error.connect(self._onSyncExecuteError)
        self._sync_execute_worker.confirm_partial_save.connect(self._onConfirmPartialSave)
        # Allow the user to skip the in-progress backup from the progress screen
        self.syncReview.skip_backup_signal.connect(self._sync_execute_worker.request_skip_backup)
        self.syncReview.give_up_scrobble_signal.connect(self._sync_execute_worker.request_give_up_scrobble)
        self._sync_execute_worker.start()

    def _onSyncExecuteComplete(self, result):
        """Called when sync execution is complete."""
        self._disconnect_skip_signal()
        # Show styled results view instead of a plain message box
        self.syncReview.show_result(result)

        # Desktop notification if app is not focused
        if not self.isActiveWindow():
            self._notifier.notify_sync_complete(
                added=getattr(result, 'tracks_added', 0),
                removed=getattr(result, 'tracks_removed', 0),
                updated=getattr(result, 'tracks_updated_metadata', 0) + getattr(result, 'tracks_updated_file', 0),
                errors=len(getattr(result, 'errors', [])),
            )

        # Reload the database to show changes (delay lets OS flush writes)
        QTimer.singleShot(500, self._rescanAfterSync)

    def _update_podcast_statuses(self):
        """Mark synced podcast episodes as 'on_ipod' in the subscription store."""
        try:
            browser = self.musicBrowser.podcastBrowser
            if not browser._store:
                return

            cache = iTunesDBCache.get_instance()
            ipod_tracks = cache.get_tracks() or []

            browser.reconcile_ipod_statuses(ipod_tracks)

            # Refresh the podcast browser episode table so status is visible
            browser.refresh_episodes()
        except Exception as e:
            logger.debug("Could not update podcast statuses: %s", e)

    def _rescanAfterSync(self):
        """Rescan the iPod database after a short post-write delay."""
        cache = iTunesDBCache.get_instance()
        # Use clear() (not invalidate()) to fully reset the cache state.
        # invalidate() does not reset _is_loading, so if a prior load is
        # still in-flight start_loading() would silently bail out and the
        # UI would never refresh.
        cache.clear()

        # Clear artwork cache — sync may have added/changed album art
        from .imgMaker import clear_artworkdb_cache
        clear_artworkdb_cache()

        # Clear UI so the reload starts from a clean slate
        self.musicBrowser.reloadData()

        cache.start_loading()

    # ── Quick Playlist Sync ────────────────────────────────────────────────

    def _quick_sync_playlists(self) -> None:
        """Debounce-schedule a quick playlist sync after playlist edits."""
        if self._is_sync_running():
            return
        device_manager = DeviceManager.get_instance()
        if not device_manager.device_path:
            return
        self._quick_pl_timer.start()  # resets if already running

    def _start_quick_playlist_sync(self) -> None:
        """Launch the quick playlist sync worker (called by debounce timer)."""
        if self._is_sync_running():
            return

        cache = iTunesDBCache.get_instance()
        if not cache.has_pending_playlists():
            return

        device_manager = DeviceManager.get_instance()
        ipod_path = device_manager.device_path
        if not ipod_path:
            return

        # Prevent overlapping quick syncs (with itself or quick meta write)
        if self._quick_pl_worker is not None and self._quick_pl_worker.isRunning():
            self._quick_pl_timer.start()  # retry after debounce
            return
        if self._quick_meta_worker is not None and self._quick_meta_worker.isRunning():
            self._quick_pl_timer.start()  # retry after debounce
            return

        user_playlists = cache.get_user_playlists()

        def _on_complete():
            c = iTunesDBCache.get_instance()
            if c.has_pending_playlists():
                c._user_playlists.clear()

        self.sidebar.show_save_indicator("saving")

        self._quick_pl_worker = QuickPlaylistSyncWorker(
            ipod_path=ipod_path,
            user_playlists=user_playlists,
            on_complete=_on_complete,
        )
        self._quick_pl_worker.completed.connect(self._on_quick_playlist_sync_done)
        self._quick_pl_worker.error.connect(self._on_quick_playlist_sync_error)
        self._quick_pl_worker.start()

    def _on_quick_playlist_sync_done(self, result) -> None:
        """Called when quick playlist sync finishes."""
        # Wait for thread to fully exit before dropping the reference
        if self._quick_pl_worker is not None:
            self._quick_pl_worker.wait()
            self._quick_pl_worker = None
        if result.success:
            logger.info("Quick playlist sync completed successfully")
            self.sidebar.show_save_indicator("saved")
        else:
            errors = "; ".join(msg for _, msg in result.errors)
            logger.error("Quick playlist sync failed: %s", errors)
            self.sidebar.show_save_indicator("error")

    def _on_quick_playlist_sync_error(self, error_msg: str) -> None:
        """Called when quick playlist sync raises an exception."""
        if self._quick_pl_worker is not None:
            self._quick_pl_worker.wait()
            self._quick_pl_worker = None
        logger.error("Quick playlist sync error: %s", error_msg)
        self.sidebar.show_save_indicator("error")

    def _disconnect_skip_signal(self):
        """Disconnect worker control signals from the finished worker."""
        try:
            self.syncReview.skip_backup_signal.disconnect()
        except TypeError:
            pass  # Already disconnected
        try:
            self.syncReview.give_up_scrobble_signal.disconnect()
        except TypeError:
            pass  # Already disconnected

    def _onSyncExecuteError(self, error_msg: str):
        """Called when sync execution fails."""
        self._disconnect_skip_signal()
        # Desktop notification if app is not focused
        if not self.isActiveWindow():
            self._notifier.notify_sync_error(error_msg)

        from settings import get_settings
        settings = get_settings()

        msg = f"Sync failed:\n\n{error_msg}"
        if settings.backup_before_sync:
            msg += (
                "\n\nA backup was created before this sync. "
                "You can restore it from the Backups page."
            )

        QMessageBox.critical(self, "Sync Error", msg)
        self.hideSyncReview()

    def _onConfirmPartialSave(self, n_added: int, n_skipped: int) -> None:
        """Called from the sync worker when the user cancels mid-sync with tracks already copied.
        Shows a dialog asking whether to save the partial database, then unblocks the worker."""
        worker = getattr(self, '_sync_execute_worker', None)
        if worker is None:
            return

        tracks_word = "track" if n_added == 1 else "tracks"
        skipped_line = (
            f"{n_skipped} more {'track was' if n_skipped == 1 else 'tracks were'} not copied."
            if n_skipped > 0 else ""
        )

        msg = QMessageBox(self)
        msg.setWindowTitle("Save Partial Sync?")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(
            f"{n_added} {tracks_word} were successfully copied to your iPod before the sync was cancelled."
        )
        detail = "Would you like to save these tracks to your iPod's database?"
        if skipped_line:
            detail = skipped_line + "\n\n" + detail
        detail += (
            "\n\nIf you discard, the copied files will be cleaned up automatically the next time you sync."
        )
        msg.setInformativeText(detail)
        save_btn = msg.addButton("Save Partial Database", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = msg.addButton("Discard", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(save_btn)
        msg.exec()

        # Default to save if dialog was closed via X button (no explicit choice)
        save = (msg.clickedButton() != discard_btn)
        worker.respond_to_partial_save(save)

    # ── Drag-and-drop support ──────────────────────────────────────────────

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        if hasattr(self, '_drop_overlay') and self._drop_overlay.isVisible():
            self._drop_overlay.setGeometry(self.rect())

    def dragEnterEvent(self, a0):
        if a0 is None:
            return
        # Reject drops when no device is selected or sync is executing
        device = DeviceManager.get_instance()
        if not device.device_path:
            a0.ignore()
            return
        if self._sync_execute_worker and self._sync_execute_worker.isRunning():
            a0.ignore()
            return

        mime = a0.mimeData()
        if mime and mime.hasUrls():
            from SyncEngine.pc_library import MEDIA_EXTENSIONS
            for url in mime.urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.is_dir() or p.suffix.lower() in MEDIA_EXTENSIONS:
                        a0.acceptProposedAction()
                        self._drop_overlay.show_overlay()
                        return
        a0.ignore()

    def dragMoveEvent(self, a0):
        if a0:
            a0.acceptProposedAction()

    def dragLeaveEvent(self, a0):
        self._drop_overlay.hide_overlay()

    def dropEvent(self, a0):
        self._drop_overlay.hide_overlay()
        if a0 is None:
            return
        mime = a0.mimeData()
        if not mime or not mime.hasUrls():
            return

        paths: list[Path] = []
        for url in mime.urls():
            if url.isLocalFile():
                paths.append(Path(url.toLocalFile()))

        if paths:
            a0.acceptProposedAction()
            self._on_files_dropped(paths)

    def _on_files_dropped(self, paths: list[Path]):
        """Process dropped files/folders in a background thread."""
        from SyncEngine.pc_library import MEDIA_EXTENSIONS

        # Collect all file paths (recurse folders)
        file_paths: list[Path] = []
        for p in paths:
            if p.is_dir():
                for root, _, files in os.walk(p):
                    for fname in files:
                        fp = Path(root) / fname
                        if fp.suffix.lower() in MEDIA_EXTENSIONS:
                            file_paths.append(fp)
            elif p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS:
                file_paths.append(p)

        if not file_paths:
            return

        # Remember whether we already have a plan to merge into
        self._drop_merge = (
            self._plan is not None
            and self.centralStack.currentIndex() == 1
        )

        # Switch to sync review and show loading
        self.centralStack.setCurrentIndex(1)
        self.syncReview.show_loading()
        self.syncReview.loading_label.setText("Reading dropped files...")

        # Run metadata reading in background thread
        self._drop_worker = _DropScanWorker(file_paths)
        self._drop_worker.finished.connect(self._on_drop_scan_complete)
        self._drop_worker.error.connect(self._onSyncError)
        self._drop_worker.start()

    def _on_drop_scan_complete(self, plan):
        """Merge dropped-file plan into any existing plan, then show."""
        if self._drop_merge and self._plan is not None:
            self._plan.to_add.extend(plan.to_add)
            self._plan.storage.bytes_to_add += plan.storage.bytes_to_add
            self.syncReview.show_plan(self._plan)
        else:
            self._plan = plan
            self.syncReview.show_plan(plan)

    def closeEvent(self, a0):
        """Ensure all threads are stopped when the window is closed."""
        # Persist window dimensions
        try:
            from settings import get_settings as _get_settings
            _s = _get_settings()
            _s.window_width = self.width()
            _s.window_height = self.height()
            _s.save()
        except Exception:
            pass

        # Clean up system tray notification icon
        Notifier.shutdown()

        # Request graceful stop for sync workers
        if self._sync_worker and self._sync_worker.isRunning():
            self._sync_worker.requestInterruption()
            self._sync_worker.wait(3000)
        if self._sync_execute_worker and self._sync_execute_worker.isRunning():
            self._sync_execute_worker.requestInterruption()
            self._sync_execute_worker.wait(3000)

        thread_pool = ThreadPoolSingleton.get_instance()
        if thread_pool:
            thread_pool.clear()  # Remove pending tasks
            thread_pool.waitForDone(3000)  # Wait up to 3 seconds for running tasks
        if a0:
            a0.accept()


# ============================================================================
# Dialogs
# ============================================================================

class _MissingToolsDialog(QDialog):
    """Dark-themed dialog prompting the user to download missing tools."""

    def __init__(
        self,
        parent: QWidget,
        tool_list: str,
        can_download: bool,
        detail_lines: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Missing Tools")
        self.setFixedWidth((420))
        self.setStyleSheet(f"""
            QDialog {{
                background: {Colors.DIALOG_BG};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins((28), (24), (28), (24))
        layout.setSpacing((10))

        # Icon + title row
        icon_label = QLabel()
        _warnpx = glyph_pixmap("warning-triangle", Metrics.FONT_ICON_MD, Colors.WARNING)
        if _warnpx:
            icon_label.setPixmap(_warnpx)
        else:
            icon_label.setText("△")
            icon_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_ICON_MD))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        title = QLabel(f"{tool_list} Not Found")
        title.setFont(QFont(FONT_FAMILY, Metrics.FONT_TITLE, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)

        layout.addSpacing((4))

        if can_download:
            body = QLabel(
                "iOpenPod can download these automatically (~80 MB).\n"
                "Download now?"
            )
        else:
            body = QLabel(detail_lines)
        body.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
        body.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(body)

        layout.addSpacing((12))

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing((12))

        if can_download:
            no_btn = QPushButton("Not Now")
            no_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_LG))
            no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            no_btn.setMinimumHeight((40))
            no_btn.setStyleSheet(btn_css(
                bg=Colors.SURFACE_RAISED,
                bg_hover=Colors.SURFACE_HOVER,
                bg_press=Colors.SURFACE_ACTIVE,
                border=f"1px solid {Colors.BORDER_SUBTLE}",
                padding="8px 24px",
            ))
            no_btn.clicked.connect(self.reject)
            btn_row.addWidget(no_btn)

            yes_btn = QPushButton("Download")
            yes_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_LG))
            yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            yes_btn.setMinimumHeight((40))
            yes_btn.setStyleSheet(btn_css(
                bg=Colors.ACCENT_DIM,
                bg_hover=Colors.ACCENT_HOVER,
                bg_press=Colors.ACCENT_PRESS,
                border=f"1px solid {Colors.ACCENT_BORDER}",
                padding="8px 24px",
            ))
            yes_btn.clicked.connect(self.accept)
            btn_row.addWidget(yes_btn)
        else:
            ok_btn = QPushButton("OK")
            ok_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_LG))
            ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ok_btn.setMinimumHeight((40))
            ok_btn.setStyleSheet(btn_css(
                bg=Colors.SURFACE_RAISED,
                bg_hover=Colors.SURFACE_HOVER,
                bg_press=Colors.SURFACE_ACTIVE,
                border=f"1px solid {Colors.BORDER_SUBTLE}",
                padding="8px 24px",
            ))
            ok_btn.clicked.connect(self.reject)
            btn_row.addWidget(ok_btn)

            # If only ffmpeg is missing, offer to continue
            self._continue_btn: QPushButton | None = None

        layout.addLayout(btn_row)

    def add_continue_option(self):
        """Add a 'Continue Anyway' button (for ffmpeg-only missing)."""
        btn_layout = self.layout()
        assert isinstance(btn_layout, QVBoxLayout)
        # Get the last item which is the btn_row layout
        btn_row_item = btn_layout.itemAt(btn_layout.count() - 1)
        row_layout = btn_row_item.layout() if btn_row_item else None
        if row_layout is not None:
            cont_btn = QPushButton("Continue Anyway")
            cont_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_LG))
            cont_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cont_btn.setMinimumHeight((40))
            cont_btn.setStyleSheet(btn_css(
                bg=Colors.ACCENT_DIM,
                bg_hover=Colors.ACCENT_HOVER,
                bg_press=Colors.ACCENT_PRESS,
                border=f"1px solid {Colors.ACCENT_BORDER}",
                padding="8px 24px",
            ))
            cont_btn.clicked.connect(self.accept)
            row_layout.addWidget(cont_btn)


class _DownloadProgressDialog(QDialog):
    """Dark-themed modal progress dialog for downloading tools."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Downloading")
        self.setFixedSize((380), (180))
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowCloseButtonHint  # type: ignore[operator]
        )
        self.setStyleSheet(f"""
            QDialog {{
                background: {Colors.DIALOG_BG};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins((28), (24), (28), (24))
        layout.setSpacing((14))

        title = QLabel("Downloading Tools…")
        title.setFont(QFont(FONT_FAMILY, Metrics.FONT_XXL, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._status = QLabel("Preparing download…")
        self._status.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
        self._status.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate
        bar.setFixedHeight((6))
        bar.setTextVisible(False)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: {Colors.SURFACE};
                border: none;
                border-radius: {(3)}px;
            }}
            QProgressBar::chunk {{
                background: {Colors.ACCENT};
                border-radius: {(3)}px;
            }}
        """)
        layout.addWidget(bar)

        layout.addStretch()

    def set_status(self, text: str):
        """Update the status label (must be called from the main thread)."""
        self._status.setText(text)


# ============================================================================
# Threading Utilities
# ============================================================================

class CancellationToken:
    """Thread-safe cancellation token for workers."""

    def __init__(self):
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def reset(self):
        self._cancelled.clear()


class ThreadPoolSingleton:
    _instance: QThreadPool | None = None

    @classmethod
    def get_instance(cls) -> QThreadPool:
        if cls._instance is None:
            cls._instance = QThreadPool.globalInstance()
        assert cls._instance is not None
        return cls._instance


class Worker(QRunnable):
    """Generic background worker with error recovery.

    Wraps a function to run in a thread pool with proper cancellation,
    error handling, and cleanup support.
    """

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        # Capture the current cancellation token at creation time
        self._cancellation_token = DeviceManager.get_instance().cancellation_token
        self._is_cancelled = False
        self._fn_name = getattr(fn, '__name__', str(fn))

    def is_cancelled(self) -> bool:
        """Check if this worker has been cancelled."""
        return self._is_cancelled or self._cancellation_token.is_cancelled()

    def cancel(self):
        """Mark this worker as cancelled."""
        self._is_cancelled = True

    @pyqtSlot()
    def run(self):
        # Check cancellation before starting
        if self.is_cancelled():
            logger.debug(f"Worker {self._fn_name} cancelled before start")
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass
            return

        try:
            result = self.fn(*self.args, **self.kwargs)
            # Check cancellation before emitting result
            if not self.is_cancelled():
                try:
                    self.signals.result.emit(result)
                except RuntimeError:
                    # Signal receiver was deleted
                    logger.debug(f"Worker {self._fn_name} result signal receiver deleted")
        except Exception as e:
            if not self.is_cancelled():
                logger.error(f"Worker {self._fn_name} failed: {e}", exc_info=True)
                exectype, value = sys.exc_info()[:2]
                try:
                    self.signals.error.emit((exectype, value, traceback.format_exc()))
                except RuntimeError:
                    logger.debug(f"Worker {self._fn_name} error signal receiver deleted")
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass


class WorkerSignals(QObject):
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)
    progress = pyqtSignal(int)


# ============================================================================
# State Management
# ============================================================================

class DeviceManager(QObject):
    """Manages the currently selected iPod device path."""
    device_changed = pyqtSignal(str)  # Emits the new device path
    device_changing = pyqtSignal()  # Emitted before device change to trigger cleanup

    _instance = None

    def __init__(self):
        super().__init__()
        self._device_path = None
        self._discovered_ipod = None  # cached DeviceInfo from last scan
        self._cancellation_token = CancellationToken()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DeviceManager()
        return cls._instance

    @property
    def cancellation_token(self) -> CancellationToken:
        return self._cancellation_token

    def cancel_all_operations(self):
        """Cancel all ongoing operations and create a new token."""
        self._cancellation_token.cancel()
        self._cancellation_token = CancellationToken()

    @property
    def device_path(self) -> str | None:
        return self._device_path

    @property
    def discovered_ipod(self):
        """Return the cached DeviceInfo from the last scan, if any."""
        return self._discovered_ipod

    @discovered_ipod.setter
    def discovered_ipod(self, ipod):
        self._discovered_ipod = ipod
        # Store in the centralised device info store
        self._sync_device_info(ipod)

    @device_path.setter
    def device_path(self, path: str | None):
        # Signal that device is changing (for cleanup)
        self.device_changing.emit()
        # Cancel all ongoing operations
        self.cancel_all_operations()
        # Clear the iTunesDB cache
        iTunesDBCache.get_instance().clear()
        self._device_path = path
        if path is None:
            self._discovered_ipod = None
            # Clear centralized device store
            from ipod_device import clear_current_device
            clear_current_device()
        self.device_changed.emit(path or "")

    @property
    def itunesdb_path(self) -> str | None:
        if not self._device_path:
            return None
        from ipod_device import resolve_itdb_path
        return resolve_itdb_path(self._device_path)

    @property
    def artworkdb_path(self) -> str | None:
        if not self._device_path:
            return None
        return os.path.join(self._device_path, "iPod_Control", "Artwork", "ArtworkDB")

    @property
    def artwork_folder_path(self) -> str | None:
        if not self._device_path:
            return None
        return os.path.join(self._device_path, "iPod_Control", "Artwork")

    def is_valid_ipod_root(self, path: str) -> bool:
        """Check if the given path looks like a valid iPod root."""
        ipod_control = os.path.join(path, "iPod_Control")
        itunes_folder = os.path.join(ipod_control, "iTunes")
        return os.path.isdir(ipod_control) and os.path.isdir(itunes_folder)

    @staticmethod
    def _sync_device_info(ipod) -> None:
        """Store a DeviceInfo (from scanner) in the centralised store.

        The scanner already calls ``enrich()`` so devices arrive
        fully populated — no conversion or re-probing needed.
        """
        from ipod_device import set_current_device, clear_current_device

        if ipod is None:
            clear_current_device()
            return

        set_current_device(ipod)


class iTunesDBCache(QObject):
    """Cache for parsed iTunesDB data. Loads once when device selected, all tabs consume."""
    data_ready = pyqtSignal()  # Emitted when data is loaded and ready
    _instance: "iTunesDBCache | None" = None

    playlists_changed = pyqtSignal()   # Emitted when user playlists are added/edited/removed
    playlist_quick_sync = pyqtSignal()  # Emitted when playlists should be written to iPod immediately
    tracks_changed = pyqtSignal()       # Emitted when track flags are modified (pending sync)
    photos_changed = pyqtSignal()       # Emitted when staged photo state changes

    def __init__(self):
        super().__init__()
        self._data: dict | None = None
        self._device_path: str | None = None
        self._is_loading: bool = False
        self._lock = threading.Lock()
        # Pre-computed indexes for fast lookups
        self._album_index: dict | None = None  # (album, artist) -> list of tracks
        self._album_only_index: dict | None = None  # album -> list of tracks
        self._artist_index: dict | None = None  # artist -> list of tracks
        self._genre_index: dict | None = None   # genre -> list of tracks
        self._track_id_index: dict | None = None  # trackID -> track dict
        self._photo_db = None
        # User-created/edited playlists (persisted in memory until sync)
        self._user_playlists: list[dict] = []
        # Pending track flag edits: db_id -> { field: (original, new), ... }
        # Originals are captured on first edit so the diff engine can
        # revert in-memory track dicts before comparing.
        self._track_edits: dict[int, dict[str, tuple]] = {}
        from SyncEngine.photos import PhotoEditState
        self._photo_edits = PhotoEditState()

    @classmethod
    def get_instance(cls) -> "iTunesDBCache":
        if cls._instance is None:
            cls._instance = iTunesDBCache()
        return cls._instance

    def clear(self):
        """Clear the cache (called when device changes)."""
        with self._lock:
            self._data = None
            self._device_path = None
            self._is_loading = False
            self._album_index = None
            self._album_only_index = None
            self._artist_index = None
            self._genre_index = None
            self._track_id_index = None
            self._photo_db = None
            self._user_playlists.clear()
            self._track_edits.clear()
            from SyncEngine.photos import PhotoEditState
            self._photo_edits = PhotoEditState()

    def invalidate(self):
        """Mark cached data stale so the next start_loading() re-parses."""
        with self._lock:
            self._data = None
            self._album_index = None
            self._album_only_index = None
            self._artist_index = None
            self._genre_index = None
            self._track_id_index = None

    def is_ready(self) -> bool:
        """Check if data is cached and ready."""
        device = DeviceManager.get_instance()
        with self._lock:
            return (self._data is not None and self._device_path == device.device_path and not self._is_loading)

    def is_loading(self) -> bool:
        """Check if data is currently being loaded."""
        with self._lock:
            return self._is_loading

    def get_data(self) -> dict | None:
        """Get cached data if available for current device."""
        device = DeviceManager.get_instance()
        with self._lock:
            if self._data is not None and self._device_path == device.device_path:
                return self._data
            return None

    def get_tracks(self) -> list:
        """Get tracks from cached data."""
        data = self.get_data()
        return list(data.get("mhlt", [])) if data else []

    def get_albums(self) -> list:
        """Get album list from cached data."""
        data = self.get_data()
        return list(data.get("mhla", [])) if data else []

    def get_photo_db(self):
        data = self.get_data()
        return data.get("photodb") if data else None

    def replace_photo_db(self, photodb) -> None:
        with self._lock:
            self._photo_db = photodb
            if self._data is not None:
                self._data["photodb"] = photodb
        self.photos_changed.emit()

    def get_album_index(self) -> dict:
        """Get pre-computed album index: (album, artist) -> list of tracks."""
        with self._lock:
            return self._album_index or {}

    def get_album_only_index(self) -> dict:
        """Get pre-computed album-only index: album -> list of tracks (fallback)."""
        with self._lock:
            return self._album_only_index or {}

    def get_artist_index(self) -> dict:
        """Get pre-computed artist index: artist -> list of tracks."""
        with self._lock:
            return self._artist_index or {}

    def get_genre_index(self) -> dict:
        """Get pre-computed genre index: genre -> list of tracks."""
        with self._lock:
            return self._genre_index or {}

    def get_track_id_index(self) -> dict:
        """Get pre-computed trackID index: trackID -> track dict."""
        with self._lock:
            return self._track_id_index or {}

    def get_playlists(self) -> list:
        """Get all playlists (regular + podcast + smart), tagged with _source.

        Deduplicates by playlistID since the podcast dataset (type 3) often
        contains the same playlists as the regular dataset (type 2).  The
        regular copy is preferred when duplicates exist.  Playlists from
        mhlp_podcast are only tagged as 'podcast' when their podcastFlag is
        set — otherwise they are just duplicates of regular playlists.

        Nano 5G+ / newer iTunes versions may omit dataset type 2 entirely,
        placing the master playlist and all user playlists in type 3 instead.
        In that case we honour isMaster from type 3 to avoid losing the
        master playlist.
        """
        data = self.get_data()
        if not data:
            return []

        seen_ids: set[int] = set()
        result: list[dict] = []

        # 1. Regular playlists (mhlp / dataset type 2) — always preferred
        has_type2_master = False
        for pl in data.get("mhlp", []):
            pl = {**pl, "_source": "regular"}
            pid = pl.get("playlist_id", 0)
            if pid not in seen_ids:
                seen_ids.add(pid)
                result.append(pl)
                if pl.get("master_flag"):
                    has_type2_master = True

        # 2. Podcast playlists (mhlp_podcast / dataset type 3)
        #    Only add if not already seen, and tag as podcast only when
        #    podcastFlag is actually set.
        #    When type 2 provided a master playlist, force master_flag=False
        #    on type 3 entries (they duplicate the master flag).  But when
        #    type 2 is absent (Nano 5G+, newer iTunes), honour master_flag
        #    from type 3 — that's where the master playlist actually lives.
        for pl in data.get("mhlp_podcast", []):
            pid = pl.get("playlist_id", 0)
            if pid in seen_ids:
                continue  # duplicate of a regular playlist
            source = "podcast" if pl.get("podcast_flag", 0) == 1 else "regular"
            pl = {**pl, "_source": source}
            if has_type2_master:
                pl["master_flag"] = 0
            seen_ids.add(pid)
            result.append(pl)

        # 3. Smart playlists (mhlp_smart / dataset type 5)
        #    master_flag is forced 0 — dataset 5 MHYP entries reuse the
        #    same type byte at offset 0x14 (1=master), but for dataset 5
        #    it denotes an iPod built-in category (Music, Movies, etc.),
        #    NOT the master playlist.  Only dataset 2 or 3 has the real master.
        for pl in data.get("mhlp_smart", []):
            pid = pl.get("playlist_id", 0)
            if pid in seen_ids:
                continue
            pl = {**pl, "_source": "smart", "master_flag": 0}
            seen_ids.add(pid)
            result.append(pl)

        # 4. User-created/edited playlists (from GUI, pending sync)
        with self._lock:
            for upl in self._user_playlists:
                pid = upl.get("playlist_id", 0)
                if pid in seen_ids:
                    # Replace the existing entry with the edited version
                    result = [upl if r.get("playlist_id") == pid else r for r in result]
                else:
                    seen_ids.add(pid)
                    result.append(upl)

        return result

    # ─────────────────────────────────────────────────────────────
    # User playlist management (in-memory, written at sync time)
    # ─────────────────────────────────────────────────────────────

    def save_user_playlist(self, playlist: dict) -> None:
        """Add or update a user-created/edited playlist in memory.

        If the playlist has a playlistID that matches an existing user
        playlist, the old entry is replaced.  Otherwise a new ID is generated.
        Emits playlists_changed so the UI can refresh.
        """
        import random

        with self._lock:
            pid = playlist.get("playlist_id", 0)

            # Assign a new playlist_id if this is a brand-new playlist
            if not pid:
                pid = random.getrandbits(64)
                playlist["playlist_id"] = pid

            # Replace existing or append
            replaced = False
            for i, upl in enumerate(self._user_playlists):
                if upl.get("playlist_id") == pid:
                    self._user_playlists[i] = playlist
                    replaced = True
                    break
            if not replaced:
                self._user_playlists.append(playlist)

        logger.info(
            "User playlist saved: '%s' (id=0x%016X, new=%s)",
            playlist.get("Title", "?"), pid, not replaced,
        )
        self.playlists_changed.emit()

    def remove_user_playlist(self, playlist_id: int) -> bool:
        """Remove a user playlist by playlist_id. Returns True if found."""
        with self._lock:
            before = len(self._user_playlists)
            self._user_playlists = [
                p for p in self._user_playlists
                if p.get("playlist_id") != playlist_id
            ]
            removed = len(self._user_playlists) < before
        if removed:
            self.playlists_changed.emit()
        return removed

    def get_user_playlists(self) -> list[dict]:
        """Get all user-created/edited playlists (pending sync)."""
        with self._lock:
            return list(self._user_playlists)

    def has_pending_playlists(self) -> bool:
        """Check if there are user playlists waiting to be synced."""
        with self._lock:
            return len(self._user_playlists) > 0

    # ─────────────────────────────────────────────────────────────
    # Track flag edits (in-memory, applied at sync time)
    # ─────────────────────────────────────────────────────────────

    def update_track_flags(self, tracks: list[dict], changes: dict) -> None:
        """Apply flag changes to one or more tracks.

        Updates the in-memory track dicts immediately (so the UI reflects
        the change) and records the edit as ``(original, new)`` so the diff
        engine can revert to the true iPod state before comparing.

        Args:
            tracks:  List of track dicts (from the parsed iTunesDB).
            changes: Field→value mapping, e.g.
                     ``{"skip_when_shuffling": 1, "compilation_flag": 0}``.
        """
        with self._lock:
            for track in tracks:
                db_id = track.get("db_id", 0)
                if not db_id:
                    continue
                edits = self._track_edits.setdefault(db_id, {})
                for key, value in changes.items():
                    if key in edits:
                        # Already edited — keep the *original* value, update new
                        orig, _ = edits[key]
                        edits[key] = (orig, value)
                    else:
                        # First edit for this field — snapshot original
                        edits[key] = (track.get(key), value)
                    # Apply to the in-memory dict so the UI sees it instantly
                    track[key] = value

        n = len(tracks)
        fields = ", ".join(f"{k}={v}" for k, v in changes.items())
        logger.info("Track flags updated on %d track(s): %s", n, fields)
        self.tracks_changed.emit()

    def get_track_edits(self) -> dict[int, dict[str, tuple]]:
        """Get all pending track flag edits: db_id → {field: (original, new)}."""
        with self._lock:
            return dict(self._track_edits)

    def has_pending_track_edits(self) -> bool:
        """Check if there are track edits waiting to be synced."""
        with self._lock:
            return len(self._track_edits) > 0

    def clear_track_edits(self) -> None:
        """Clear pending track edits (called after successful sync)."""
        with self._lock:
            self._track_edits.clear()

    def get_photo_edits(self):
        with self._lock:
            return self._photo_edits

    def clear_photo_edits(self) -> None:
        from SyncEngine.photos import PhotoEditState
        with self._lock:
            self._photo_edits = PhotoEditState()
        self.photos_changed.emit()

    def has_pending_photo_edits(self) -> bool:
        with self._lock:
            return bool(self._photo_edits.has_changes)

    def stage_photo_import(self, source_path: str, album_name: str = "") -> None:
        with self._lock:
            self._photo_edits.imported_files.append((source_path, album_name))
        self.photos_changed.emit()

    def stage_photo_album_create(self, album_name: str) -> None:
        with self._lock:
            self._photo_edits.created_albums.add(album_name)
        self.photos_changed.emit()

    def stage_photo_album_rename(self, old_name: str, new_name: str) -> None:
        with self._lock:
            self._photo_edits.renamed_albums[old_name] = new_name
        self.photos_changed.emit()

    def stage_photo_album_delete(self, album_name: str) -> None:
        with self._lock:
            self._photo_edits.deleted_albums.add(album_name)
        self.photos_changed.emit()

    def stage_photo_membership_remove(self, visual_hash: str, album_name: str) -> None:
        with self._lock:
            self._photo_edits.membership_removals.add((visual_hash, album_name))
        self.photos_changed.emit()

    def stage_photo_delete(self, visual_hash: str) -> None:
        with self._lock:
            self._photo_edits.deleted_photos.add(visual_hash)
        self.photos_changed.emit()

    def pop_track_edits(self) -> "dict[int, dict[str, tuple]]":
        """Atomically return and clear all pending track edits."""
        with self._lock:
            edits = dict(self._track_edits)
            self._track_edits.clear()
            return edits

    def set_data(self, data: dict, device_path: str):
        """Set cached data, build indexes, and emit ready signal."""
        # Build indexes for fast lookups
        album_index = {}  # (album, artist) -> list of tracks
        album_only_index = {}  # album -> list of tracks (fallback when mhla lacks artist)
        artist_index = {}  # artist -> list of tracks
        genre_index = {}   # genre -> list of tracks

        track_id_index = {}  # trackID -> track dict

        tracks = list(data.get("mhlt", []))
        for track in tracks:
            tid = track.get("track_id")
            if tid is not None:
                track_id_index[tid] = track

            # Only include audio tracks in album/artist/genre indices.
            # Video, podcast, audiobook etc. tracks belong in their own
            # sidebar categories and should not pollute the music views.
            # media_type 0 ("Audio/Video") appears in both menus per iTunes.
            mt = track.get("media_type", 1)
            if mt != 0 and not (mt & 0x01):
                continue

            album = track.get("Album", "Unknown Album")
            artist = track.get("Artist", "Unknown Artist")
            # Use Album Artist for album grouping (matches mhla's "Artist (Used by Album Item)")
            album_artist = track.get("Album Artist") or artist
            genre = track.get("Genre", "Unknown Genre")

            # Album index (keyed by album + album_artist to match mhla)
            album_key = (album, album_artist)
            if album_key not in album_index:
                album_index[album_key] = []
            album_index[album_key].append(track)

            # Album-only index (fallback for mhla entries without artist)
            if album not in album_only_index:
                album_only_index[album] = []
            album_only_index[album].append(track)

            # Artist index
            if artist not in artist_index:
                artist_index[artist] = []
            artist_index[artist].append(track)

            # Genre index
            if genre not in genre_index:
                genre_index[genre] = []
            genre_index[genre].append(track)

        with self._lock:
            self._data = data
            self._device_path = device_path
            self._is_loading = False
            self._album_index = album_index
            self._album_only_index = album_only_index
            self._artist_index = artist_index
            self._genre_index = genre_index
            self._track_id_index = track_id_index
            self._photo_db = data.get("photodb")
        # Emit signal outside lock to avoid deadlock
        self.data_ready.emit()

    def set_loading(self, loading: bool):
        """Set loading state."""
        with self._lock:
            self._is_loading = loading

    def start_loading(self):
        """Start loading data for the current device. Called once when device selected."""
        device = DeviceManager.get_instance()
        if not device.device_path:
            return

        with self._lock:
            if self._is_loading:
                return  # Already loading
            if self._data is not None and self._device_path == device.device_path:
                # Already have data for this device, just emit ready
                self.data_ready.emit()
                return
            self._is_loading = True

        # Start background load
        worker = Worker(self._load_data, device.device_path, device.itunesdb_path)
        worker.signals.result.connect(self._on_load_complete)
        worker.signals.error.connect(self._on_load_error)
        ThreadPoolSingleton.get_instance().start(worker)

    def _load_data(self, device_path: str, itunesdb_path: str) -> tuple:
        """Background thread: parse the iTunesDB and merge Play Counts."""
        data: dict = {}

        try:
            from iTunesDB_Parser.ipod_library import load_ipod_library
            parsed = load_ipod_library(itunesdb_path) or {}
            if isinstance(parsed, dict):
                data = parsed
            else:
                logger.warning("iTunesDB parser returned unexpected type: %s", type(parsed).__name__)
        except Exception:
            logger.exception("Failed to load iTunesDB for device: %s", device_path)

        try:
            from SyncEngine.photos import read_photo_db
            data["photodb"] = read_photo_db(device_path)
        except Exception:
            logger.exception("Failed to load photo database for device: %s", device_path)
            from SyncEngine.photos import PhotoDB
            data["photodb"] = PhotoDB()

        return (data, device_path)

    def _on_load_error(self, error: tuple) -> None:
        """Reset loading state when background load worker errors."""
        exctype, value, _tb = error
        logger.error("Device load worker failed: %s: %s", getattr(exctype, "__name__", str(exctype)), value)
        self.set_loading(False)

    def _on_load_complete(self, result: tuple):
        """Called when background load finishes."""
        data, device_path = result
        # Verify this is still the current device
        if device_path != DeviceManager.get_instance().device_path:
            self.set_loading(False)  # Reset so future loads aren't blocked
            return
        if data:
            self.set_data(data, device_path)
        else:
            self.set_loading(False)


# ============================================================================
# Data Transform (convert cached state data to UI-ready format)
# ============================================================================

def build_album_list(cache: iTunesDBCache) -> list:
    """Transform cached data into album list for grid display.

    Uses the pre-built album index for O(1) lookups instead of O(n*m) scan.
    Falls back to album-only lookup when mhia entry lacks artist info.
    """
    albums = cache.get_albums()
    album_index = cache.get_album_index()
    album_only_index = cache.get_album_only_index()

    items = []
    for album_entry in albums:
        artist = album_entry.get("Artist (Used by Album Item)")
        album = album_entry.get("Album (Used by Album Item)", "Unknown Album")

        # Try exact (album, artist) lookup first
        matching_tracks = []
        if artist:
            matching_tracks = album_index.get((album, artist), [])

        # Fallback: if no artist in mhia or no match, lookup by album name only
        if not matching_tracks:
            matching_tracks = album_only_index.get(album, [])
            # If we found tracks but had no artist, use the album artist from tracks
            if matching_tracks and not artist:
                artist = matching_tracks[0].get("Album Artist") or matching_tracks[0].get("Artist", "Unknown Artist")

        if not artist:
            artist = "Unknown Artist"

        mhiiLink = None
        track_count = len(matching_tracks)
        year = None
        total_length_ms = 0

        if track_count > 0:
            mhiiLink = matching_tracks[0].get("artwork_id_ref")
            # Get year from first track that has it
            year = next((t.get("year") for t in matching_tracks if t.get("year")), None)
            # Calculate total album duration
            total_length_ms = sum(t.get("length", 0) for t in matching_tracks)

        # Build subtitle: "Artist • Year • N tracks"
        subtitle_parts = [artist]
        if year and year > 0:
            subtitle_parts.append(str(year))
        subtitle_parts.append(f"{track_count} tracks")
        subtitle = " · ".join(subtitle_parts)

        # Skip albums that have no audio tracks (e.g. video-only albums)
        if track_count == 0:
            continue

        items.append({
            "title": album,
            "subtitle": subtitle,
            "album": album,
            "artist": artist,
            "year": year,
            "artwork_id_ref": mhiiLink,
            "category": "Albums",
            "filter_key": "Album",
            "filter_value": album,
            "track_count": track_count,
            "total_length_ms": total_length_ms
        })

    return sorted(items, key=lambda x: x["title"].lower())


def build_artist_list(cache: iTunesDBCache) -> list:
    """Transform cached data into artist list for grid display.

    Uses the pre-built artist index for O(1) lookups.
    """
    artist_index = cache.get_artist_index()

    items = []
    for artist, tracks in artist_index.items():
        track_count = len(tracks)
        # Get first available artwork
        mhiiLink = next((t.get("artwork_id_ref") for t in tracks if t.get("artwork_id_ref")), None)
        # Count unique albums
        album_count = len(set(t.get("Album", "") for t in tracks))
        # Total plays
        total_plays = sum(t.get("play_count_1", 0) for t in tracks)

        # Build subtitle: "N albums · M tracks" or add plays if any
        subtitle_parts = []
        if album_count > 1:
            subtitle_parts.append(f"{album_count} albums")
        subtitle_parts.append(f"{track_count} tracks")
        if total_plays > 0:
            subtitle_parts.append(f"{total_plays} plays")

        items.append({
            "title": artist,
            "subtitle": " · ".join(subtitle_parts),
            "artwork_id_ref": mhiiLink,
            "category": "Artists",
            "filter_key": "Artist",
            "filter_value": artist,
            "track_count": track_count,
            "album_count": album_count,
            "total_plays": total_plays
        })

    return sorted(items, key=lambda x: x["title"].lower())


def build_genre_list(cache: iTunesDBCache) -> list:
    """Transform cached data into genre list for grid display.

    Uses the pre-built genre index for O(1) lookups.
    """
    genre_index = cache.get_genre_index()

    items = []
    for genre, tracks in genre_index.items():
        track_count = len(tracks)
        # Get first available artwork
        mhiiLink = next((t.get("artwork_id_ref") for t in tracks if t.get("artwork_id_ref")), None)
        # Count unique artists
        artist_count = len(set(t.get("Artist", "") for t in tracks))
        # Total duration
        total_length_ms = sum(t.get("length", 0) for t in tracks)
        total_hours = total_length_ms / (1000 * 60 * 60)

        # Build subtitle: "N artists · M tracks · X.X hours"
        subtitle_parts = []
        if artist_count > 1:
            subtitle_parts.append(f"{artist_count} artists")
        subtitle_parts.append(f"{track_count} tracks")
        if total_hours >= 1:
            subtitle_parts.append(f"{total_hours:.1f} hours")

        items.append({
            "title": genre,
            "subtitle": " · ".join(subtitle_parts),
            "artwork_id_ref": mhiiLink,
            "category": "Genres",
            "filter_key": "Genre",
            "filter_value": genre,
            "track_count": track_count,
            "artist_count": artist_count,
            "total_length_ms": total_length_ms
        })

    return sorted(items, key=lambda x: x["title"].lower())


# _DeviceRenameWorker — background thread for iPod rename (full DB rewrite)
class _EjectWorker(QThread):
    """Run the cross-platform safe-eject off the UI thread."""

    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, ipod_path: str):
        super().__init__()
        self._ipod_path = ipod_path

    def run(self):
        try:
            from ipod_device.eject import eject_ipod
            ok, message = eject_ipod(self._ipod_path)
            if ok:
                self.finished_ok.emit(message)
            else:
                self.failed.emit(message)
        except Exception as exc:
            logger.exception("EjectWorker: unexpected error")
            self.failed.emit(str(exc))


class _DeviceRenameWorker(QThread):
    """Rewrite the iTunesDB after renaming the iPod (master playlist title)."""

    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, ipod_path: str, new_name: str):
        super().__init__()
        self._ipod_path = ipod_path
        self._new_name = new_name

    def run(self):
        try:
            from SyncEngine.sync_executor import SyncExecutor, _SyncContext
            from SyncEngine.mapping import MappingFile

            executor = SyncExecutor(self._ipod_path)
            existing_db = executor._read_existing_database()
            existing_tracks_data = existing_db["tracks"]
            existing_playlists_raw = list(existing_db["playlists"])
            existing_smart_raw = list(existing_db["smart_playlists"])

            all_tracks = [
                executor._track_dict_to_info(t) for t in existing_tracks_data
            ]

            # Build a minimal context for _build_and_evaluate_playlists
            ctx = _SyncContext(
                plan=None,  # type: ignore[arg-type]
                mapping=MappingFile(),
                progress_callback=None,
                dry_run=False,
                write_back_to_pc=False,
                _is_cancelled=None,
            )
            ctx.existing_tracks_data = existing_tracks_data
            ctx.existing_playlists_raw = existing_playlists_raw
            ctx.existing_smart_raw = existing_smart_raw

            _master_name, playlists, smart_playlists = executor._build_and_evaluate_playlists(
                ctx, all_tracks,
            )

            # Use the explicitly requested name, NOT the one read from disk.
            success = executor._write_database(
                tracks=all_tracks,
                playlists=playlists,
                smart_playlists=smart_playlists,
                master_playlist_name=self._new_name,
            )

            if success:
                self.finished_ok.emit()
            else:
                self.failed.emit("Database write returned False.")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(e))


class _QuickMetadataWorker(QThread):
    """Write pending track-flag edits directly to the iPod, bypassing full sync.

    Reads the existing iTunesDB, applies the supplied ``track_edits`` snapshot
    (db_id → {field: (orig, new)}), and writes the database back.  The worker
    operates entirely on the snapshot passed at construction time; any edits
    made after construction are not included and remain pending in the cache.
    """

    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, ipod_path: str, track_edits: "dict[int, dict[str, tuple]]"):
        super().__init__()
        self._ipod_path = ipod_path
        self._track_edits = track_edits  # snapshot: db_id → {field: (orig, new)}

    def run(self):
        try:
            from SyncEngine.sync_executor import SyncExecutor, _SyncContext
            from SyncEngine.mapping import MappingFile

            executor = SyncExecutor(self._ipod_path)
            existing_db = executor._read_existing_database()
            existing_tracks_data = existing_db["tracks"]
            existing_playlists_raw = list(existing_db["playlists"])
            existing_smart_raw = list(existing_db["smart_playlists"])

            # Apply edits to the raw track dicts before converting to TrackInfo
            for t in existing_tracks_data:
                db_id = t.get("db_id", 0)
                if db_id in self._track_edits:
                    for field, (_, new_val) in self._track_edits[db_id].items():
                        t[field] = new_val

            all_tracks = [
                executor._track_dict_to_info(t) for t in existing_tracks_data
            ]

            ctx = _SyncContext(
                plan=None,  # type: ignore[arg-type]
                mapping=MappingFile(),
                progress_callback=None,
                dry_run=False,
                write_back_to_pc=False,
                _is_cancelled=None,
            )
            ctx.existing_tracks_data = existing_tracks_data
            ctx.existing_playlists_raw = existing_playlists_raw
            ctx.existing_smart_raw = existing_smart_raw

            master_name, playlists, smart_playlists = executor._build_and_evaluate_playlists(
                ctx, all_tracks,
            )

            success = executor._write_database(
                tracks=all_tracks,
                playlists=playlists,
                smart_playlists=smart_playlists,
                master_playlist_name=master_name,
            )

            if success:
                self.finished_ok.emit()
            else:
                self.failed.emit("Database write returned False.")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(e))


# _DropScanWorker - background thread for reading dropped files metadata
class _DropScanWorker(QThread):
    """Read metadata from dropped files and build a SyncPlan."""

    finished = pyqtSignal(object)  # SyncPlan
    error = pyqtSignal(str)

    def __init__(self, file_paths: list):
        super().__init__()
        self._file_paths = file_paths

    def run(self):
        try:
            from SyncEngine.pc_library import PCLibrary
            from SyncEngine.fingerprint_diff_engine import (
                SyncPlan, SyncItem, SyncAction, StorageSummary,
            )

            items: list[SyncItem] = []
            total_bytes = 0

            for fp in self._file_paths:
                if self.isInterruptionRequested():
                    return
                try:
                    # Use a temporary PCLibrary rooted at the file's parent
                    lib = PCLibrary(fp.parent)
                    track = lib._read_track(fp)
                    if track:
                        items.append(SyncItem(
                            action=SyncAction.ADD_TO_IPOD,
                            pc_track=track,
                            description=f"{track.artist} — {track.title}",
                        ))
                        total_bytes += track.size
                except Exception as e:
                    logger.warning("Failed to read dropped file %s: %s", fp, e)

            plan = SyncPlan(
                to_add=items,
                storage=StorageSummary(bytes_to_add=total_bytes),
            )
            self.finished.emit(plan)
        except Exception as e:
            self.error.emit(str(e))
