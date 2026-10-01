# Fonts on the printer: upload, retrieval, and matching names across sessions

How a local `.ttf` becomes an object in a printer's memory, what can and cannot
be fetched back out, and what two sessions must agree on before the same label
prints in the same face on both.

`FUNCTIONAL_SPEC.md` §10 states the required behaviour. This is the how-to for
working with it: the exact calls, the wire format each one sends, and the
places where the obvious method is the wrong one.

Everything here lives in [zplcore/fonts.py](zplcore/fonts.py),
[zplcore/printer_objects.py](zplcore/printer_objects.py),
[zplcore/printer_io.py](zplcore/printer_io.py) and
[zplcore/workflow.py](zplcore/workflow.py). No GUI is needed for any of it.

---

## 1. The only identity that crosses a session boundary

A saved `.zpl` records a font as the fourth parameter of `^A@`:

```
^A@N,53,19,E:DEJAVUSA.TTF
```

That is the whole record. The file does not carry the font's bytes, its family
name, its version, a checksum, or the path it came from. So when a second
session opens that label, or checks that printer, **the 8-character object name
plus its drive letter is the entire basis for deciding whether two things are
the same font.** Every synchronisation problem below is a consequence of that.

Three separate values are easy to confuse, and they are not interchangeable:

| Value | Example | Where it lives | Rule |
|---|---|---|---|
| **spec** | `E:DEJAVUSA.TTF` | `^A@`'s path, `TextElement.printer_font_spec`, `query_printer_fonts()` results, `Document.font_sources()` keys | Written back **exactly as the file gave it**, case included |
| **name** | `DEJAVUSA` | `TextElement.printer_font_name`, `~DY` headers, renderer font registry | Drive and extension stripped, **forced upper case** — this is the lookup key |
| **local path** | `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` | `TextElement.font_path`, session only | Never saved to the `.zpl`, never sent except as upload payload |

Compare specs, not names. The same name on two drives is two different objects,
and a `^A@` naming `E:MYFONT.TTF` is not satisfied by the printer holding
`R:MYFONT.TTF` — the printer would not find it either and would silently fall
back to a substitute face. `Document.font_sources()`
([zplcore/model.py:1993](zplcore/model.py#L1993)) and
`workflow.missing_printer_fonts()`
([zplcore/workflow.py:70](zplcore/workflow.py#L70)) both key on the full spec
for exactly this reason.

---

## 2. Deriving the name from a file (forward direction)

`fonts.printer_font_name(path, taken=())`
([zplcore/fonts.py:305](zplcore/fonts.py#L305)):

1. Take the **filename stem** — `Path(path).stem`. *Not* the family name
   recorded inside the font.
2. Upper-case it.
3. Drop non-ASCII (`encode('ascii', 'ignore')`).
4. Delete every remaining character outside `[A-Z0-9_-]` — deleted, not
   replaced with an underscore.
5. Truncate to 8 characters (`MAX_NAME_LEN`).
6. An empty result becomes `FONT`.
7. If the result is in `taken`, append `2`, `3`, … trimming the stem to keep
   the total at 8 (see §3.2 — this step does not survive a session).

Verified output:

| Local file | Object name |
|---|---|
| `DejaVuSans.ttf` | `DEJAVUSA` |
| `DejaVuSans-Bold.ttf` | `DEJAVUSA` *(same — truncation is lossy)* |
| `dejavu-sans.ttf` | `DEJAVU-S` *(same face, different filename, different object)* |
| `Catrina Demo.ttf` | `CATRINAD` |
| `my.font.v2.ttf` | `MYFONTV2` |
| `A_LONGER_NAME.ttf` | `A_LONGER` |
| `日本語.ttf` | `FONT` |

Then `fonts.printer_font_path(name, device)` builds the spec:
`printer_font_path('ANI', 'B')` → `B:ANI.TTF`.

**The step that trips up cross-machine work is step 1.** The name is derived
from the filename, so two machines that have the identical typeface installed
under different filenames produce different printer objects. Distributions
disagree about font filenames; a font installed by hand from a vendor zip is
unlikely to match a packaged one at all. See §7.

---

## 3. Mapping a name back to a file (reverse direction)

`fonts.file_for_printer_name(name)`
([zplcore/fonts.py:194](zplcore/fonts.py#L194)) is how a reopened label finds
its real font again: it derives an object name for every installed `.ttf`
(`_all_ttf_paths()`) and returns the first path that matches. Ties — and
truncation makes them common — are broken by preferring the face the family
listing already picked as canonical, then the shortest filename, so
`DejaVuSans.ttf` wins over `DejaVuSans-Bold.ttf`.

Consequence worth knowing before someone reports it as a bug: **a label saved
with a bold face reopens showing the regular face of the same family.** What
prints is unaffected — the printer only has the one object.

### 3.1 It takes a bare name, not a spec

```python
fonts.file_for_printer_name('E:DEJAVUSA.TTF')   # -> None   (wrong)
fonts.file_for_printer_name('DEJAVUSA')         # -> '/usr/.../DejaVuSans.ttf'
fonts.file_for_printer_name('dejavusa')         # -> same; input is upper-cased
```

Split first, always with the **font** splitter:

```python
device, name = fonts.split_font_spec('E:DEJAVUSA.TTF')   # ('E', 'DEJAVUSA')
```

`fonts.split_font_spec` ([zplcore/fonts.py:336](zplcore/fonts.py#L336)) defaults
a driveless spec to `E:` (the font default). Do **not** reach for
`graphic_store.split_device_spec` here: it defaults to `R:` and `.GRF`, which is
right for a graphic and wrong for a font — a delete aimed at the wrong drive
quietly removes nothing, or the wrong thing.

### 3.2 Collision suffixes do not survive a session

This is the one hard limit on cross-session matching. The `taken` argument is
the set of names already used **by the open document at the moment a font is
picked** (`Document.printer_font_names(exclude=…)`). It is state of that
editing session, and it is recorded nowhere:

```python
fonts.printer_font_name('/f/DejaVuSans-Bold.ttf', taken={'DEJAVUSA'})  # 'DEJAVUS2'
fonts.file_for_printer_name('DEJAVUS2')                                # None
```

`file_for_printer_name` derives candidates with no `taken`, so a suffixed name
can never match anything. A label that used two faces of one family therefore
reopens with the second one:

- unresolved to any local file (`font_path is None`),
- drawn in a substitute face at the wrong width,
- listed by the pre-print check as `(source file unknown)`,
- **absent from `uploadable`**, so "Upload & Print" cannot send it — only
  "Print Anyway" remains.

If a label must carry two faces of one family across sessions, give the files
names that differ within the first 8 characters (`DJVSANS.ttf`,
`DJVBOLD.ttf`) before choosing them, so each derives to its own unsuffixed
name on every machine.

---

## 4. Uploading

```python
from zplcore import fonts

name = fonts.printer_font_name('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
fonts.upload_font('10.0.0.50', 9100,
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                  name, device='E')          # default timeout 30s
```

`fonts.build_font_upload()`
([zplcore/fonts.py:351](zplcore/fonts.py#L351)) is the payload builder, and
useful on its own for tests or for writing a payload to a file:

```
~DYE:MYFONT,A,TT,12,12,<12 raw bytes>
```

Four things about that line are deliberate and easy to "fix" wrongly:

- **No `^XA` / `^XZ` wrapper.** `~DY` is an immediate command; wrapping it
  changes nothing for the better and is not what this app sends.
- **`A,TT` for the `b,x` parameters.** The ZPL manual's own tables give `B`
  for "uncompressed (.TTE, .TTF, binary)" and `T` for TrueType, and list no
  `TT` at all — yet `A,TT` is what this app has always sent and what real
  hardware accepts. Changing it is a question to settle against a printer, not
  against a reading of the manual.
- **`t` and `w` are both the byte length.** For a font there is no row width to
  give, so the total is sent twice.
- **The name in the `~DY` header carries no extension** (`E:MYFONT`), while
  `^HW` lists it, `^A@` references it and `^ID` deletes it as `E:MYFONT.TTF`.
  The extension is implied by the `TT` parameter. Do not add `.TTF` to the
  upload header.

### Which drive

`fonts.DEVICES` is `('R', 'E', 'B', 'A')` — reused from `graphic_store`. `Z:` is
excluded everywhere in the font path: `~DY` cannot write read-only factory
content and `^ID` will not delete it.

`DEFAULT_FONT_DEVICE` is `'E'`. Note that **ZPL's own default for an omitted
drive is `R:`, which is not the same thing** — so every command in
`fonts.py` spells the device out rather than relying on the printer's default.

The device a *this-app-assigned* font goes to is the **Font memory** setting
(`Document.font_device`, persisted as `[printer] font_device` in
`~/.config/linuxzpl/settings.ini`, with a project-directory fallback). A value
outside `R:/E:/B:/A:` is ignored on load, so a hand-edited settings file cannot
point every upload at a drive the printer has not got. It is **not** written
into the `.zpl` — the saved file already answers the question concretely in
each `^A@`.

### Upload happens at print time, not at pick time

Picking a font only records it, so choosing one never blocks on the network.
The pre-print chain is three steps a frontend runs itself, because the middle
one is a prompt and the other two are network calls that must stay off the GUI
thread ([zplcore/workflow.py:62](zplcore/workflow.py#L62)):

```python
if not document.font_sources():          # built-in fonts only: nothing to check
    print_now()
    return
result = workflow.missing_printer_fonts(document, address, port, cancel=token)
missing, uploadable, unreadable = (None, {}, ()) if result is None else result
if result is not None and not missing:
    print_now()
    return
text, detail = workflow.font_problem_prompt(missing, unreadable)
# ... ask, and on "Upload & Print":
workflow.upload_fonts(uploadable, address, port, on_progress=…, cancel=token)
```

**Guard on `font_sources()` before calling.** Both frontends do. A label using
built-in fonts only has nothing to check, and the guard saves the round trip;
`missing_printer_fonts` answers `({}, {}, [])` if you call it anyway.

**A font wanted on a drive that could not be read is "unknown", not
"missing"** (`FUNCTIONAL_SPEC.md` §10.3). `missing_printer_fonts` drops it from `missing` deliberately —
calling it missing would offer to upload it to the very drive that is failing,
on no evidence that it is not already sitting there. Pass the third value to
`font_problem_prompt(missing, unreadable)` so the prompt names the drives that
went unchecked instead of letting a user assume all four were.

`upload_fonts` sends **each font to the drive its own spec names**, not all to
one drive — that is the whole point of keying on specs. A failure raises
`OSError` naming the font, and the caller must not print afterwards; a
`printer_io.Cancelled` passes through untouched rather than being reported as a
failed upload.

### Fonts that are not TrueType are always reported missing

`query_printer_fonts` only ever matches `*.TTF`, while `font_sources()` keys on
the spec a loaded label actually wrote. A label naming `B:CYRI_UB.FNT` (a
bitmap font) or a `.TTE` therefore always reports as missing with source
unknown, and is never uploadable:

```python
document.font_sources()        # {'B:CYRI_UB.FNT': None, 'E:DEJAVUSA.TTF': '/f/DejaVuSans.ttf'}
workflow.missing_printer_fonts(document, host, port)
                               # ({'B:CYRI_UB.FNT': None}, {}, [])
```

That is correct as far as this app can act — it has no local file for such a
font and `~DY ...,TT,` could not send one — but expect the warning on every
print of such a label, and do not read it as "the printer lost the font". (If
that font's own drive is among `unreadable`, it drops out of `missing`
altogether: unknown rather than missing, per the rule above.)

---

## 5. Listing what the printer has

```python
answer = fonts.query_printer_fonts(address, port, cancel=token)
if answer is None:
    ...                                  # the printer could not be asked at all
else:
    specs, unreadable = answer           # Set[str], list of device letters
```

One `^XA^HW<d>:*.TTF^XZ` per device, in `DEVICES` order, replies parsed for
`NAME.TTF` and re-emitted as `d:NAME.TTF` with the name **upper-cased**.

Four contracts to respect:

- **The answer is a pair, or `None`.** All three device queries —
  `fonts.query_printer_fonts`, `graphic_store.query_printer_graphics`,
  `printer_objects.query_printer_objects` — answer `(specs, unreadable)` on
  success and `None` when the printer could not be asked at all. They are kept
  deliberately identical; if you change how one judges a reply, change all
  three.
- **`None` and an empty listing mean different things.** Empty is "the printer
  has none of these"; `None` is "the printer could not be asked" —
  unreachable, or no `^HW` support. Say so rather than showing an empty list as
  if the printer had answered.
- **`unreadable` is not an error.** It is every device that errored or said
  nothing, and a drive that simply is not fitted looks exactly like that —
  which is why it rides alongside the result instead of replacing it. Use it to
  say which drives a listing is short of, rather than presenting a partial
  listing as complete. `workflow.listing_status(address, noun, devices, specs,
  unreadable)` ([zplcore/workflow.py:107](zplcore/workflow.py#L107)) composes
  that sentence, and both frontends share it so the wording cannot drift.
- **How unreachability is judged.** A failure to *connect* on the very first
  device (`R:`) is decisive and returns `None` immediately, so a dead host
  fails fast instead of timing out once per drive. Silence is weaker: a device
  that says nothing is recorded as unreadable and skipped, and only a run in
  which *no* device answered at all reads as `None`. A device that answers,
  even to list nothing, proves the printer is there and understood the
  question.

Related queries:

```python
fonts.query_resident_fonts(address, port)  # ^HWZ:*.FNT — built-in A–H/0/GS, best effort
fonts.query_printer_dpi(address, port)     # ~HI, dots-per-mm field → 152/203/300/600, else None
```

`query_printer_dpi` returns `None` rather than a guess, so a manual DPI setting
stays authoritative when the printer is unreachable or answers oddly.

Delete with name + device, never a spec:

```python
device, name = fonts.split_font_spec('B:MYFONT.TTF')
fonts.delete_printer_font(address, port, name, device)   # ^XA^ID B:MYFONT.TTF ^FS^XZ, timeout 10
```

---

## 6. Retrieval

### 6.1 There is no font retrieval, and that is not an oversight

`fonts.py` has no download function. Printers do not hand a font's bytes back
once uploaded, which was confirmed live rather than inferred:

- the `file.type` Set/Get/Do retrieval gets **no reply at all** for a `.TTF`,
  while other objects on the same printer (a `.GRF`, say) download fine;
- a printer's own FTP server, where it has one, answers a `.TTF` with a plain
  `550 Permission denied` while other stored files download normally.

The reason is font distribution rights, and the app says so rather than
retrying: the font manager's preview falls back to the **local** file via
`file_for_printer_name` and, when there is none, shows

> (preview unavailable — printers block downloading fonts to protect font
> distribution rights)

So: **to get a font back, find the local `.ttf` again. Do not plan a round
trip through the printer.** The printer is authoritative for *which objects
exist*, never for their contents.

### 6.2 The generic retrieval path, for when you try anyway

Retrieval of any stored object goes through
[zplcore/printer_objects.py](zplcore/printer_objects.py), not `fonts.py`:

```python
from zplcore import printer_objects

try:
    data = printer_objects.download_printer_object(address, port, 'E:LOGO.GRF')
except printer_objects.ObjectNotRetrievable:
    ...   # printer accepted the connection but never answered
```

Specifics that matter:

- The command is `! U1 setvar "file.type" "E:LOGO.GRF"\r\n` — a Set/Get/Do
  command, **sent standalone, not wrapped in `^XA`/`^XZ`**, and the reply
  arrives on the same socket. This is the one genuinely object-agnostic
  retrieval in the ZPL manual; `^HG` only makes sense of a graphic's bitmap
  shape and there is no host command for a stored format or a WML menu.
- An empty reply raises `ObjectNotRetrievable` (a subclass of `OSError`, but
  distinct from a plain connection failure so a caller can say "this object
  could not be retrieved" precisely).
- **It carries no session-wide state.** One object refusing to download does
  not mean the next will: each retrieval is judged on its own reply. Do not add
  a "this printer can't retrieve" flag.
- The default timeout is a short **5 s** for every call in that module,
  precisely because a real reply arrives quickly and a silent one should fail
  fast.
- Bytes are returned verbatim and should be saved verbatim — no decoding. For
  diagnostics, `printer_io.send_command()` decodes with replacement characters,
  which is lossy; use it for `~HS`-style chatter, never for retrieval.

### 6.3 Storing an arbitrary file (`CISDFCRC16`)

```python
printer_objects.upload_printer_object(address, port, 'MYFILE', 'DAT', data)
```

```
! CISDFCRC16\r\n0000\r\nMYFILE.DAT\r\n0000000C\r\n0000\r\n<raw bytes>
```

- **`E:` only.** `CISDFCRC16`'s own parameter table offers no device choice,
  unlike `^HW`/`^ID`/`file.type` — which is why the Store prompt asks for a name
  and extension and never a device.
- **CRC and checksum are both `0000` on purpose.** The command documents that
  value as skipping validation. CRC-16/CCITT itself is unambiguous, but the
  manual's worked example of the checksum (an 8-bit two's-complement byte sum)
  does not obviously produce the 4 hex digits its own file example shows, and a
  checksum computed the wrong way would make every upload fail outright rather
  than merely go unverified.
- Size is the payload length as 8 upper-case hex digits; lines are CR/LF
  terminated; no `^XA` wrapper.

### 6.4 Case: the one place names are not upper-cased

`fonts.py` and `graphic_store.py` force names upper because they only ever
*create* upper-case 8.3 objects. `printer_objects.py` deliberately does not: a
name there may have come from `CISDFCRC16` (whose manual examples,
`privkey.nrd` and `feedback.get`, are lower case) or from some other tool
entirely, and **`file.type` retrieval is documented as case sensitive** — a
name normalised the wrong way silently stops matching the real object. Round
names through that module exactly as the printer gave them.

Also: `^ID` reaches `R:/E:/B:/A:` only. A `Z:` target is silently *ignored*,
not deleted, so Delete is withheld for `Z:` in the UI rather than letting a
click report success and change nothing.

---

## 7. Making a second session agree

Two sessions — a second machine, a colleague, a CI job, a later checkout —
match fonts only if they agree on the spec. Work through these in order.

**1. Agree on the filename, not the typeface.** The object name comes from the
filename stem (§2). Before assuming two machines match, compare derivations,
not fonts:

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from zplcore import fonts
for p in fonts._all_ttf_paths():
    print(fonts.printer_font_name(p), p)" | sort
```

Run that on both, diff it. Same object name from both sides is the only
evidence that matters. If the same typeface derives to two names, install it
under one agreed filename (or copy the file into a directory both sessions
scan) and re-pick the font in the label.

**2. Check for collisions and suffixes.** Any name in the list above appearing
twice is a latent collision; any `…2`/`…3` name already inside a `.zpl` is an
unrecoverable reference (§3.2). Fix it by renaming the files so the first 8
characters differ, then re-picking.

**3. Agree on the drive.** Font memory is a per-installation setting, not part
of the label. It only affects fonts *this app assigns*; elements loaded from a
file keep their own path. The trap is mixing: opening an `E:`-authored label on
a session set to `R:` and then re-picking the font on **one** element clears
that element's `printer_font_spec`, so it moves to `R:` while its siblings stay
on `E:`. The label now needs the same font on two drives, and the pre-print
check — correctly — reports whichever one is absent.

**4. Reconcile with the printer before printing.** Compare specs, upper-cased
on both sides:

```python
wanted = document.font_sources()                    # spec -> local path or None
answer = fonts.query_printer_fonts(address, port)   # None means "could not ask at all"
if answer is not None:
    present, unreadable = answer
    unknown = {d.upper() for d in unreadable}       # drives that did not answer
    missing = {s: p for s, p in wanted.items()
               if s.upper() not in {x.upper() for x in present}
               and s.split(':', 1)[0].upper() not in unknown}
```

Upper-case both sides: the printer's listing is upper-cased on the way in, and
a label may name a font in any case because `^A@`'s path is kept exactly as the
file wrote it.

**5. Upload what is missing, to the drive each spec names.** `workflow.upload_fonts`
already does this; a hand-rolled loop must not collapse every font onto one
drive.

**6. Re-list to confirm.** `query_printer_fonts` again is the only confirmation
that exists — there is no read-back of contents (§6.1) — and it confirms only
the drives that answered, so check that `unreadable` came back empty before
calling a reconciliation complete. A same-named object is
assumed to be the same font. If you need certainty that a printer holds a
*specific* version of a face, delete the object first and re-upload; nothing in
this design can tell two same-named fonts apart.

**7. Refresh local caches if the filesystem changed.** `list_ttf_families()`,
`_all_ttf_paths()` and `scan_font_directories()` cache at module level. A font
installed while a session is running is invisible to it until something passes
`refresh=True` (the Local Fonts… dialog's Rescan). A long-lived process that
installs fonts must refresh, or it will keep deriving names from a stale list.

Note also that discovery asks `fc-list` first and falls back to walking
`FALLBACK_FONT_DIRS` (plus `/opt/*/usr/share/fonts`) when fontconfig is
missing, broken, or reports nothing. Two sessions can therefore see different
font sets on the same machine if one has fontconfig set up and the other does
not — `fonts.font_discovery_status()` reports which path is in use.

One more folder of your choosing can join that scan: Settings → Local Fonts… →
Extra Font Folder… (stored as `extra_dir` under `[fonts]` in settings.ini), or
`fonts.set_extra_font_dir(path)` from Python. It is only read on the fallback
path, so it changes nothing while `fc-list` works, and it appears in the
Local Fonts… dialog's list of scanned folders.

---

## 8. Operation reference

| Operation | Call | Sent | Wrapped in `^XA…^XZ`? | Device | Default timeout |
|---|---|---|---|---|---|
| Upload font | `fonts.upload_font` | `~DYd:NAME,A,TT,len,len,<bytes>` | no | any of `R/E/B/A` | 30 s |
| List fonts | `fonts.query_printer_fonts` | `^HWd:*.TTF`, once per device | yes | all of `R/E/B/A` | 5 s each |
| List built-ins | `fonts.query_resident_fonts` | `^HWZ:*.FNT` | yes | `Z:` | 5 s |
| Delete font | `fonts.delete_printer_font` | `^IDd:NAME.TTF^FS` | yes | as given | 10 s |
| Printer DPI | `fonts.query_printer_dpi` | `~HI` | no | — | 5 s |
| List all objects | `printer_objects.query_printer_objects` | `^HWd:*.*`, once per device | yes | `R/E/B/A/Z` | 5 s each |
| Retrieve object | `printer_objects.download_printer_object` | `! U1 setvar "file.type" "d:N.E"` | no | as given | 5 s |
| Store object | `printer_objects.upload_printer_object` | `! CISDFCRC16` block | no | **`E:` only** | 5 s |
| Delete object | `printer_objects.delete_printer_object` | `^IDd:N.E^FS` | yes | `R/E/B/A` (`Z:` ignored) | 5 s |

The three device queries — fonts, graphics and objects — each answer
`(specs, unreadable)` on success and `None` when the printer could not be asked
at all (§5). Names are forced upper case by `fonts.py`, preserved as-given by
`printer_objects.py` (§6.4).

---

## 9. Threading, cancellation and timeouts

Every call above blocks for up to its timeout on one socket that it opens and
closes itself — there is no connection reuse and no pooling. Consequences for
anything driving these functions:

- **Run them off the UI thread and hold a `CancelToken` for the duration**
  ([zplcore/printer_io.py:33](zplcore/printer_io.py#L33)). It is the only way
  to abandon a request early, which matters most for exactly the case in §6.1:
  a printer that answers a `.TTF` retrieval with silence leaves the worker
  inside one `recv()` until the timeout expires.
- `CancelToken.cancel()` calls `shutdown()`, not `close()` — on Linux a
  `close()` from another thread does not wake a blocked `recv()`/`sendall()`.
  Closing is left to `send()`'s own `finally`, so two threads never race over
  one descriptor. A `connect()` still in progress cannot be reached this way; a
  cancel during that phase takes effect when the connect timeout expires.
- **One token per operation, not per dialog.** Once cancelled it stays
  cancelled, and `send()` given an already-cancelled token raises before
  opening a socket at all.
- **`printer_io.Cancelled` is deliberately not an `OSError`.** The query
  functions turn `OSError` into "None, the printer could not be asked", and a
  cancel has to travel through them to whoever asked for it rather than be
  mistaken for an unreachable printer. Check `isinstance(error, Cancelled)`
  *before* generic error handling, and treat it as "nothing happened".
- A cancelled request never returns data, however the shutdown surfaced — as an
  `OSError`, as an early EOF with partial data, or not at all.
- Reply reading is idle-based: after the first chunk the socket drops to a
  0.5 s timeout, bounded by the original deadline. Do not lower a timeout on
  the assumption that it only governs the connect.
- The frontends serialise per dialog: `BusyBar.run` ignores a second call while
  one is in flight, and restores each blocked button to the state it had before
  (so a Delete withheld for a `Z:` object stays withheld).

---

## 10. Drawing with the font locally

Uploading to the printer and drawing on canvas are separate registrations.
`fonts.register_app_font(path)` does both halves of the local side:
`FcConfigAppFontAddFile` through `ctypes`, and Qt via
`register_app_fonts_with_qt()`.

One sharp edge: **`QFontDatabase` aborts the process if it is called before a
`QGuiApplication` exists** — it reaches into the platform integration, so it
cannot simply be wrapped in `try`/`except`. Fonts registered before the app
starts are remembered in a module-level set and handed over on the first call
that finds an application running, which is why the window calls
`register_app_fonts_with_qt()` during startup. If you add a code path that
registers fonts earlier, do not "simplify" that deferral away.

Both `set_font`/`set_element_font` already call `register_app_font`, so a font
chosen through the model is registered; a font resolved on load by the parser is
registered there too ([zplcore/parser.py:1306](zplcore/parser.py#L1306)).

---

## 11. Headless recipes

Match a `.zpl`'s fonts against a printer and upload the gaps:

```python
import sys; sys.path.insert(0, '.')
from zplcore import parser, fonts, workflow

document, file_dpi = parser.parse_zpl(open('label.zpl').read())
if not document.font_sources():
    sys.exit("built-in fonts only, nothing to check")
result = workflow.missing_printer_fonts(document, '10.0.0.50', 9100)
if result is None:
    sys.exit("printer could not be asked")
missing, uploadable, unreadable = result
for spec in sorted(missing):
    print('missing', spec, '' if missing[spec] else '(source file unknown)')
if unreadable:
    print('unchecked drives:', ', '.join(f'{d}:' for d in unreadable))
workflow.upload_fonts(uploadable, '10.0.0.50', 9100, on_progress=print)
```

Inspect a payload without a printer:

```python
open('/tmp/dy.bin','wb').write(fonts.build_font_upload('/f/DejaVuSans.ttf', 'DEJAVUSA', 'E'))
```

What name would this file get, and does anything else claim it:

```python
name = fonts.printer_font_name('/f/DejaVuSans.ttf')
clash = [p for p in fonts._all_ttf_paths() if fonts.printer_font_name(p) == name]
```

---

## 12. Where to look

- [zplcore/fonts.py](zplcore/fonts.py) — discovery, naming, upload, list,
  delete, local registration
- [zplcore/printer_objects.py](zplcore/printer_objects.py) — generic retrieve /
  store / delete, `ObjectNotRetrievable`
- [zplcore/printer_io.py](zplcore/printer_io.py) — the single socket primitive,
  `CancelToken`, `Cancelled`
- [zplcore/workflow.py](zplcore/workflow.py) — the pre-print check and the
  upload loop, shared by both frontends
- [zplcore/model.py](zplcore/model.py) — `font_sources`, `printer_font_spec`,
  `Document.font_device`, `^A@` writing
- [zplcore/parser.py](zplcore/parser.py) — `read_font` and the load-side reverse
  lookup
- `FUNCTIONAL_SPEC.md` §10 — the requirements, including §10.4.1 Font memory
  and §10.5 on the object manager
- [tests/test_core.py](tests/test_core.py) — naming, `^HW` judging,
  `ObjectNotRetrievable`, cancel plumbing, `upload_fonts` device routing;
  [tests/test_font_fallback.py](tests/test_font_fallback.py) — discovery
  without `fc-list`
