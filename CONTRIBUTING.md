# Working on LinuxZPL

Two frontends, one core. GTK3 and PySide2/Qt5 are both first-class; neither is
a port of the other, and both answer to `FUNCTIONAL_SPEC.md`.

## Where code goes

`zplcore/` is everything that is not a user interface: the label model, the ZPL
read and write, fonts and printer I/O, the geometry of editing, the text
raster, and the decisions in `workflow.py`. **No module in `zplcore` may import
a GUI toolkit at import time.** The core has to run headless, and a frontend
for one toolkit must never depend on the other's.

`gtkui/` and `qtui/` hold only what genuinely differs: painting, dialogs,
menus, events.

If you are unsure which side something belongs on, ask whether it could be
wrong in a way that changes what the printer produces. If it could, it belongs
in the core.

## Definition of done

A feature is finished when:

1. The behaviour, the ZPL and the geometry are in `zplcore`, with a check in
   `tests/test_core.py`.
2. **Both frontends are wired up** - or the difference is written into
   `FUNCTIONAL_SPEC.md` as deliberate. Section 18 is where behaviours that are
   decisions rather than requirements are recorded.
3. `tests/test_conformance.py` passes.
4. Both frontends have been run and looked at.

## Running the tests

```bash
./tests/run.sh
```

`test_core.py` and `test_deviations.py` run offscreen and need no display.
`test_conformance.py` drives both frontends and needs one: it uses `$DISPLAY`
if set, otherwise `xvfb-run` (`apt install xvfb`).

The conformance suite is the reason two frontends are maintainable. It runs the
same scripted editing session against both and diffs the ZPL after every step.
Duplicated behaviour does not fail loudly when it diverges - it prints a label
that is subtly wrong - so if a change makes the two disagree, that suite is
what tells you.

## When a toolkit cannot match the other

It happens. Qt5 has no font chooser that can be filtered to the TrueType
families the printer can accept, so the Qt frontend has a custom family picker
where GTK uses `Gtk.FontChooserDialog` with a filter. The spec records the
required *behaviour* - only installed TrueType families are offered - and each
frontend meets it its own way. Match behaviour, not implementation.
