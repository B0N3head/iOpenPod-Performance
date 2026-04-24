"""Thin entry point that delegates bootstrapping to app_core."""

from app_core.bootstrap import main, run_pyqt_app

__all__ = ["main", "run_pyqt_app"]


if __name__ == "__main__":
    main()
