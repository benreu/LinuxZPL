import os, sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide2.QtWidgets import QApplication, QDialog, QDialogButtonBox
from PySide2.QtCore import QTimer
app = QApplication([])
from zplcore import fonts as zpl_fonts, textraster
from zplcore.model import Document, BarcodeElement
from qtui import canvas as qt_canvas, window as qt_main, dialogs as qt_dialogs

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + str(extra)) if extra else ""))
    if not cond: fails.append(name)

FONT = zpl_fonts.file_for_family('DejaVu Sans')

# --- 18.1  canvas text is no longer truncated to 20 characters ---------------
w = qt_main.ZPLDesignerWindow(); w.resize(900, 1000); w.canvas.resize(600, 900)
long_text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef"   # 42 chars
el = w.document.add_text_element(long_text)
el.font_path = FONT
w.document.sync_text_width(el)
raster_full = qt_canvas.to_qimage(textraster.raster(el.text, FONT, el.font_height))
short = w.document.add_text_element(long_text[:20]); short.font_path = FONT
raster_20 = qt_canvas.to_qimage(textraster.raster(short.text, FONT, short.font_height))
check("18.1 full string is rasterised, not the first 20 chars",
      raster_full.width() > raster_20.width() * 1.8,
      f"{raster_full.width()}px for 42 chars vs {raster_20.width()}px for 20")
check("18.1 drawn raster matches the element box it prints in",
      abs(raster_full.width() - el.printed_width(None) * el.font_height / el.font_width) < 8,
      f"raster {raster_full.width()} vs box-at-em {el.printed_width(None)*el.font_height/el.font_width:.0f}")

# --- 18.2  barcode dialog uses the element's own module width ----------------
# A label rescaled to 300 dpi has module_width 3; editing its value must not
# recompute the width as though the module were 2.
bc = BarcodeElement(0, 0, barcode_value="123456789", module_width=3)
doc = Document(); doc.elements.append(bc)

def accept_dialog():
    for widget in app.topLevelWidgets():
        if isinstance(widget, QDialog) and widget.isVisible():
            for line in widget.findChildren(type(widget.findChild(QDialogButtonBox))):
                pass
            box = widget.findChild(QDialogButtonBox)
            # type a longer value first
            from PySide2.QtWidgets import QLineEdit
            edit = widget.findChild(QLineEdit)
            edit.setText("1234567890123")     # 13 chars
            box.button(QDialogButtonBox.Ok).click()

QTimer.singleShot(150, accept_dialog)
changed = qt_dialogs.edit_barcode_dialog(None, bc)
expected_correct = (35 + 13 * 11) * 3          # its own module width
expected_old_bug = (35 + 13 * 11) * 2          # the hardcoded 2
check("18.2 barcode width uses the element's module_width",
      changed and bc.width == expected_correct,
      f"{bc.width} (correct {expected_correct}, old bug {expected_old_bug})")

# --- 18.5  File > New --------------------------------------------------------
w.unsaved_changes = False
w.on_add_text(); w.on_add_frame()
check("New: history and elements exist before", w.document.elements and w._undo_stack)
w.current_filepath = "/tmp/whatever.zpl"
w.unsaved_changes = False          # so New does not prompt
w.on_new()
check("18.5 New clears the elements", not w.document.elements)
check("18.5 New clears the history", not w._undo_stack and not w._redo_stack)
check("18.5 New clears the current file", w.current_filepath is None)
check("18.5 New resets the status", w.statusBar().currentMessage() == "Ready")
check("18.5 New restores a 4x6 label at the printer dpi",
      w.document.label_width == 4 * w.printer_dpi and w.document.label_height == 6 * w.printer_dpi,
      f"{w.document.label_width}x{w.document.label_height} at {w.printer_dpi}dpi")
check("18.5 New has a Ctrl+N shortcut",
      w.new_action.shortcut().toString() == "Ctrl+N", w.new_action.shortcut().toString())

# --- dpi rescale prompt, which only fires on a mismatch ----------------------
answers = []
qt_dialogs.ask_dpi_rescale = lambda parent, old, new, assumed, wi, hi: (
    answers.append((old, new, assumed)) or 'rescale')
w.printer_dpi = 203
w.unsaved_changes = False
w.load_zpl_file(str(Path(__file__).resolve().parent / 'fixtures' / 'sample_300dpi.zpl'))
check("300dpi file opened at 203dpi offers a rescale", answers == [(300, 203, False)], answers)
check("rescale shrank the label to 203dpi dots",
      abs(w.document.label_width - 1200 * 203 / 300) <= 2, w.document.label_width)
check("status reports the rescale",
      w.statusBar().currentMessage().startswith("Loaded"), w.statusBar().currentMessage())

answers.clear()
no_dpi = "^XA\n^PW812\n^LL1218\n^FO10,10\n^AFN,36,20\n^FDplain^FS\n^XZ"
import tempfile
p = os.path.join(tempfile.mkdtemp(), 'nodpi.zpl'); open(p, 'w').write(no_dpi)
w.printer_dpi = 300
w.unsaved_changes = False
w.load_zpl_file(p)
check("a file with no recorded dpi is assumed 203, and says so",
      answers == [(203, 300, True)], answers)

print()
print("ALL DEVIATION CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
