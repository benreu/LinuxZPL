#!/usr/bin/env python3
"""
The two frontends must produce identical ZPL.

This is the whole defence against the frontends drifting apart. Duplicated
behaviour does not fail loudly when it diverges - it prints a label that is
subtly wrong - so something has to check the two against each other on every
change. conformance_driver.py runs one scripted session against one frontend
and prints the ZPL after every step; this runs it against both and diffs.

Needs a display for the GTK frontend: run it under DISPLAY, or xvfb-run.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / 'tests' / 'conformance_driver.py'


def run(frontend):
    result = subprocess.run(
        [sys.executable, str(DRIVER), '--frontend', frontend],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    if result.returncode != 0:
        print(f'--- {frontend} driver failed ---')
        print(result.stderr.strip()[-2000:])
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f'--- {frontend} driver produced no JSON ---')
        print((result.stdout or '')[-500:])
        print((result.stderr or '').strip()[-1500:])
        return None


def main():
    gtk, qt = run('gtk'), run('qt')
    if gtk is None or qt is None:
        return 1

    gtk_steps, qt_steps = gtk['steps'], qt['steps']
    if len(gtk_steps) != len(qt_steps):
        print(f'FAIL step count differs: gtk {len(gtk_steps)}, qt {len(qt_steps)}')
        return 1

    failures = []
    for g, q in zip(gtk_steps, qt_steps):
        if g['step'] != q['step']:
            print(f"FAIL step names diverged: {g['step']!r} vs {q['step']!r}")
            return 1
        same = g['zpl'] == q['zpl']
        print(('PASS ' if same else 'FAIL ') + g['step'])
        if not same:
            failures.append(g['step'])
            gl, ql = g['zpl'].split('\n'), q['zpl'].split('\n')
            for i in range(max(len(gl), len(ql))):
                a = gl[i] if i < len(gl) else '<missing>'
                b = ql[i] if i < len(ql) else '<missing>'
                if a != b:
                    print(f'    gtk: {a[:70]}')
                    print(f'    qt : {b[:70]}')

    print()
    if failures:
        print(f'{len(failures)} of {len(gtk_steps)} steps diverged: {failures}')
        return 1
    print(f'ALL {len(gtk_steps)} STEPS IDENTICAL across both frontends')
    return 0


if __name__ == '__main__':
    sys.exit(main())
