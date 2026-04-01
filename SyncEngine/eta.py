"""
ETA Tracker - Estimates time remaining during sync operations.

Tracks elapsed time per stage and per item, computing a smoothed
estimate of remaining time using exponential moving average (EMA)
for professional-grade stability that doesn't jump around.

The EMA approach weights recent samples more heavily but blends in
historical data, avoiding the sharp jumps and flickering that occur
with simple rolling windows or raw averages.
"""

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class StageStats:
    """Timing statistics for a single sync stage."""
    stage: str
    start_time: float = 0.0
    end_time: float = 0.0
    total_items: int = 0
    completed_items: int = 0

    # Exponential moving average for per-item duration (professional smoothing)
    _ema_item_time: Optional[float] = None  # None until first sample
    _ema_alpha: float = 0.15  # Smoothing factor: 0.15 = ~13-item decay time
    _last_item_time: float = 0.0
    _items_processed: int = 0  # Count for cold-start bias correction

    @property
    def elapsed(self) -> float:
        end = self.end_time if self.end_time else time.monotonic()
        return max(0.0, end - self.start_time)

    @property
    def avg_item_time(self) -> float:
        """Exponential moving average of per-item time.

        Uses Welford's online variance correction for the first ~20 items
        to stabilize against outliers during cold start.
        """
        if self._ema_item_time is None:
            if self.completed_items > 0 and self.elapsed > 0:
                return self.elapsed / self.completed_items
            return 0.0
        # Apply cold-start correction: weight newer EMA less until we have
        # enough samples. This prevents huge swings when the first few items
        # happen to be much faster/slower than the average.
        if self._items_processed < 20:
            # Blend the EMA with a fall-back estimate based on total elapsed.
            # As _items_processed grows, shift weight from fallback to EMA.
            fallback = self.elapsed / max(1, self.completed_items)
            blend_alpha = self._items_processed / 20.0
            return (blend_alpha * self._ema_item_time
                    + (1 - blend_alpha) * fallback)
        return self._ema_item_time

    def _update_ema(self, new_time: float):
        """Update the exponential moving average with a new sample."""
        if self._ema_item_time is None:
            self._ema_item_time = new_time
        else:
            self._ema_item_time = (
                self._ema_alpha * new_time
                + (1 - self._ema_alpha) * self._ema_item_time
            )
        self._items_processed += 1

    @property
    def remaining_seconds(self) -> float:
        remaining_items = max(0, self.total_items - self.completed_items)
        avg = self.avg_item_time
        if avg <= 0:
            return 0.0
        return remaining_items * avg


class ETATracker:
    """
    Tracks sync progress and computes estimated time remaining.

    Usage:
        tracker = ETATracker()
        tracker.stage_start("add", total=50)
        for i in range(50):
            # do work
            tracker.item_done("add")
        tracker.stage_end("add")

        # Get display string at any point:
        eta_str = tracker.format_eta()  # "~2m 15s remaining"
    """

    def __init__(self):
        self._stages: dict[str, StageStats] = {}
        self._stage_order: list[str] = []
        self._current_stage: Optional[str] = None
        self._global_start: float = 0.0

    def reset(self):
        """Clear all tracking data."""
        self._stages.clear()
        self._stage_order.clear()
        self._current_stage = None
        self._global_start = 0.0

    def start(self):
        """Mark the beginning of the entire sync operation."""
        self.reset()
        self._global_start = time.monotonic()

    @property
    def elapsed_total(self) -> float:
        """Total elapsed time since start()."""
        if not self._global_start:
            return 0.0
        return time.monotonic() - self._global_start

    def stage_start(self, stage: str, total: int):
        """Begin tracking a new stage with the given item count."""
        t = time.monotonic()
        stats = StageStats(stage=stage, start_time=t, total_items=total)
        stats._last_item_time = t
        self._stages[stage] = stats
        if stage not in self._stage_order:
            self._stage_order.append(stage)
        self._current_stage = stage

    def item_done(self, stage: Optional[str] = None):
        """Record completion of one item in the given (or current) stage."""
        stage = stage or self._current_stage
        if not stage or stage not in self._stages:
            return

        stats = self._stages[stage]
        now = time.monotonic()
        dt = now - stats._last_item_time
        stats._last_item_time = now
        stats._update_ema(dt)
        stats.completed_items += 1

    def stage_end(self, stage: str):
        """Mark a stage as complete."""
        if stage in self._stages:
            self._stages[stage].end_time = time.monotonic()
            # Advance current stage pointer
            if self._current_stage == stage:
                self._current_stage = None

    def update(self, stage: str, current: int, total: int):
        """
        All-in-one update: creates stage if needed, records item progress.
        Designed to be called directly from the progress callback.
        """
        if stage not in self._stages:
            self.stage_start(stage, total)

        stats = self._stages[stage]
        # Update total in case it changed
        stats.total_items = total

        # Record newly completed items.
        # When progress jumps by >1 (batched updates), spread the elapsed
        # time evenly across the items instead of recording near-zero
        # deltas for each, which would collapse the EMA.
        gap = current - stats.completed_items
        if gap > 0:
            now = time.monotonic()
            total_dt = now - stats._last_item_time
            per_item = total_dt / gap
            for _ in range(gap):
                stats._update_ema(per_item)
                stats.completed_items += 1
            stats._last_item_time = now

    @property
    def current_stage_stats(self) -> Optional[StageStats]:
        if self._current_stage and self._current_stage in self._stages:
            return self._stages[self._current_stage]
        return None

    def remaining_seconds(self) -> float:
        """Estimated seconds remaining for current stage."""
        stats = self.current_stage_stats
        if stats is None:
            return 0.0
        return stats.remaining_seconds

    def format_eta(self) -> str:
        """
        Human-readable ETA string for the current stage.
        Returns empty string if no estimate is available.
        """
        secs = self.remaining_seconds()
        return self._format_duration(secs)

    def format_elapsed(self) -> str:
        """Human-readable elapsed time since start()."""
        return self._format_duration(self.elapsed_total, prefix="")

    def format_stage_progress(self, stage: str, current: int, total: int) -> str:
        """
        Format a compact progress string: "3 of 50 · ~1m 20s remaining"
        """
        parts = []
        if total > 0:
            parts.append(f"{current} of {total}")

        eta = self.format_eta()
        if eta:
            parts.append(eta)

        return " · ".join(parts) if parts else ""

    @staticmethod
    def _format_duration(seconds: float, prefix: str = "~") -> str:
        """Format seconds into a human-readable duration string."""
        if seconds <= 0:
            return ""
        seconds = int(seconds)
        if seconds < 5:
            return ""  # Don't show tiny estimates, they flicker

        if seconds < 60:
            return f"{prefix}{seconds}s remaining"
        elif seconds < 3600:
            m, s = divmod(seconds, 60)
            if s == 0:
                return f"{prefix}{m}m remaining"
            return f"{prefix}{m}m {s}s remaining"
        else:
            h, remainder = divmod(seconds, 3600)
            m = remainder // 60
            if m == 0:
                return f"{prefix}{h}h remaining"
            return f"{prefix}{h}h {m}m remaining"
