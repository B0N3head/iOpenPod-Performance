#!/bin/sh
# Wrapper that ensures the PyInstaller bundle's bundled Qt libraries and
# plugins are found before handing off to the real executable.
BUNDLE=/app/lib/iOpenPod
export LD_LIBRARY_PATH="$BUNDLE/_internal:$BUNDLE/_internal/Qt/lib:${LD_LIBRARY_PATH:-}"
export QT_PLUGIN_PATH="$BUNDLE/_internal/plugins:${QT_PLUGIN_PATH:-}"
exec "$BUNDLE/iOpenPod" "$@"
