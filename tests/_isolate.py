"""
Point the settings file at a throwaway directory, for the duration of a run.

Both frontends persist the printer, the label size and the window geometry to
the user's config directory, and read them back in the constructor - so without
this a suite's result depends on whose machine it runs on. A developer who has
used the app and quit has a real label size remembered, and every window the
Qt suites build opens at it: a 0.90 x 0.75 inch label is 270 x 225 dots, too
small to scroll at 1:1 and too small for the arithmetic several checks do.

The failures that prompted this were not in the code under test. A suite that
passes here and fails there teaches nothing either way, so the settings are
taken out of the equation rather than pinned one value at a time.

Set rather than setdefault, unlike the QT_QPA_PLATFORM line beside it: the real
value is exactly what has to be displaced. Import it BEFORE importing either
frontend - GLib caches the user config directory the first time it is asked,
so a GTK import that got in first would keep the real one.

A suite that needs a particular setting still sets it explicitly; this only
decides what it starts from.
"""

import os
import tempfile

CONFIG_HOME = tempfile.mkdtemp(prefix='linuxzpl-test-')
os.environ['XDG_CONFIG_HOME'] = CONFIG_HOME
