#!/bin/bash
# Run every suite. Qt goes through the offscreen platform plugin; the GTK
# frontend needs a display, so the suites that drive it run under xvfb-run
# (apt install xvfb), falling back to DISPLAY when there is no xvfb.
cd "$(dirname "$0")/.."
status=0

for suite in tests/test_core.py tests/test_deviations.py tests/test_font_fallback.py \
             tests/test_decode.py tests/test_cli.py tests/test_api.py \
             tests/test_help.py; do
  echo "=== $suite ==="
  QT_QPA_PLATFORM=offscreen python3 -u "$suite" || status=1
done

for suite in tests/test_gtk_editors.py tests/test_conformance.py; do
  [ -f "$suite" ] || continue
  echo "=== $suite ==="
  # The windows go to a virtual display, out of the way of whoever is at the
  # screen: on the real one they take the keyboard as they open, and a key
  # meant for something else lands in the test. xvfb-run's own default screen
  # is 640x480 at 8 bits, hence the size. LINUXZPL_TEST_DISPLAY=real puts
  # them on DISPLAY instead, to watch.
  if [ "$LINUXZPL_TEST_DISPLAY" != real ] && command -v xvfb-run >/dev/null; then
    xvfb-run -a -s "-screen 0 1920x1080x24" python3 -u "$suite" || status=1
  elif [ -n "$DISPLAY" ]; then
    python3 -u "$suite" || status=1
  else
    echo "SKIPPED: needs xvfb (apt install xvfb) or a display"
  fi
done

exit $status
