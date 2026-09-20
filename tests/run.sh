#!/bin/bash
# Run every suite. Qt goes through the offscreen platform plugin; the GTK
# frontend needs a display, so the suites that drive it run under xvfb-run when
# there is no DISPLAY (apt install xvfb).
cd "$(dirname "$0")/.."
status=0

for suite in tests/test_core.py tests/test_deviations.py tests/test_font_fallback.py; do
  echo "=== $suite ==="
  QT_QPA_PLATFORM=offscreen python3 -u "$suite" || status=1
done

for suite in tests/test_gtk_editors.py tests/test_conformance.py; do
  [ -f "$suite" ] || continue
  echo "=== $suite ==="
  if [ -n "$DISPLAY" ]; then
    python3 -u "$suite" || status=1
  elif command -v xvfb-run >/dev/null; then
    xvfb-run -a python3 -u "$suite" || status=1
  else
    echo "SKIPPED: needs a display or xvfb (apt install xvfb)"
  fi
done

exit $status
