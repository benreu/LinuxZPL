#!/bin/bash
# Run every suite. Qt goes through the offscreen platform plugin; the GTK
# frontend needs a display, so the conformance suite runs under xvfb-run when
# there is no DISPLAY (apt install xvfb).
cd "$(dirname "$0")/.."
status=0

for suite in tests/test_core.py tests/test_deviations.py; do
  echo "=== $suite ==="
  QT_QPA_PLATFORM=offscreen python3 -u "$suite" || status=1
done

if [ -f tests/test_conformance.py ]; then
  echo "=== tests/test_conformance.py ==="
  if [ -n "$DISPLAY" ]; then
    python3 -u tests/test_conformance.py || status=1
  elif command -v xvfb-run >/dev/null; then
    xvfb-run -a python3 -u tests/test_conformance.py || status=1
  else
    echo "SKIPPED: needs a display or xvfb (apt install xvfb)"
  fi
fi

exit $status
