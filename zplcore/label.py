"""
Label - the way a Python program fills in a ZPL template.

The command line's --field and --save-file, as an object: load a template, give
its numbered fields (and anything else that carries data) their values, then
ask for ZPL text or a rendered image. Nothing here touches a toolkit.

A Label keeps the template's text and the substitutions asked for, and builds a
fresh Document for every output. fill_template rewrites its document (it turns
each ^FN into a plain field and drops the ^DF), so building afresh is what lets
one template be filled again and again without the first label leaking into
the next.
"""

from . import fields as zpl_fields
from . import parser, renderer, workflow


def _field_number(number) -> int:
    """`number` as a field number, or ValueError."""
    if isinstance(number, bool) or not isinstance(number, int) \
            or not 1 <= number <= zpl_fields.MAX_NUMBER:
        raise ValueError(
            f"field number {number!r}: expected an int from 1 to "
            f"{zpl_fields.MAX_NUMBER}")
    return number


def _text(data) -> str:
    """Field data as the string ZPL carries. None is refused rather than
    written as 'None' - FieldTable.set_value would silently ignore it."""
    if data is None:
        raise ValueError("field data may not be None")
    return str(data)


class Label:
    """A ZPL template and the data to print it with."""

    def __init__(self, zpl: str):
        self._zpl = zpl
        self._fields = {}       # ^FN number -> data
        self._prompts = {}      # ^FN number -> prompt
        self._edits = []        # (target, data) for elements with no ^FN
        self._serials = []      # (index, start, increment, leading_zeros)
        self._copies = None
        # Parsed once to validate the text and to answer questions about the
        # template; outputs are built from a parse of their own.
        self._template, self._file_dpi = self._parse()

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path):
        """The template in the file at `path`. OSError if it cannot be read."""
        content, _ = parser.read_file(path)
        return cls(content)

    @classmethod
    def from_zpl(cls, zpl: str):
        """The template in the text `zpl`."""
        return cls(zpl)

    def _parse(self):
        return parser.parse_zpl(self._zpl, renderer.ZPLRenderer())

    # -- what the template holds ------------------------------------------

    @property
    def field_numbers(self) -> list:
        """The ^FN numbers the template uses, in order and without repeats."""
        seen = []
        for element in self._template.elements:
            number = element.field_number
            if number is not None and number not in seen:
                seen.append(number)
        return seen

    @property
    def elements(self) -> list:
        """The template's elements, in the order set_data's indexes count.
        Changing them changes nothing: outputs are built from the text."""
        return list(self._template.elements)

    @property
    def dropped(self) -> list:
        """Commands in the template that writing it back would lose."""
        return workflow.unsupported_commands(self._zpl)

    # -- giving it data ---------------------------------------------------

    def __setitem__(self, number, data):
        self._fields[_field_number(number)] = _text(data)

    def __getitem__(self, number):
        return self._fields[_field_number(number)]

    def __delitem__(self, number):
        del self._fields[_field_number(number)]

    def __contains__(self, number):
        return number in self._fields

    def fill(self, mapping=None, **named):
        """Give several numbered fields their data: a {number: data} mapping.
        A later value for a number wins, as a later --field does.

        Keywords spell numbers as f1=..., f2=..., for call sites that read
        better that way.
        """
        pairs = list((mapping or {}).items())
        for key, data in named.items():
            if not (key[:1] == 'f' and key[1:].isdigit()):
                raise ValueError(f"{key!r}: keyword fields are spelled f1=..., f2=...")
            pairs.append((int(key[1:]), data))
        checked = [(_field_number(n), _text(d)) for n, d in pairs]
        self._fields.update(checked)

    def set_prompt(self, number, prompt):
        """The prompt ^FN`number` names. Only a to_zpl(template=True) writes
        it: a filled label has no ^FN left to carry one."""
        self._prompts[_field_number(number)] = _text(prompt)

    @property
    def copies(self):
        """^PQ's quantity, or None to leave the template's own."""
        return self._copies

    @copies.setter
    def copies(self, quantity):
        if quantity is not None and (isinstance(quantity, bool)
                                     or not isinstance(quantity, int)
                                     or quantity < 1):
            raise ValueError(f"copies {quantity!r}: expected an int of 1 or more")
        self._copies = quantity

    def set_data(self, target, data):
        """Replace the data of a field that is not an ^FN one.

        `target` is an element's index (see `elements`) or, as a str, the data
        it carries now - every element carrying exactly that text is changed.
        IndexError if the index is out of range or the element has no data;
        KeyError if no element carries the text.
        """
        data = _text(data)
        if isinstance(target, int) and not isinstance(target, bool):
            elements = self._template.elements
            if not -len(elements) <= target < len(elements) \
                    or not elements[target].data_attribute:
                raise IndexError(f"element {target} has no data to replace")
            self._edits.append((target % len(elements), data))
            return
        indexes = [i for i, e in enumerate(self._template.elements)
                   if e.data_attribute and e.data_literal() == target]
        if not indexes:
            raise KeyError(f"no element carries the data {target!r}")
        self._edits.extend((i, data) for i in indexes)

    def serial(self, start, increment=1, leading_zeros=False, index=None):
        """Set a ^SN serial field's starting value, step and leading zeros.

        `index` is the element's index; omitted, the template's only serial
        field. KeyError if there is none (or, with no index, several).
        """
        serials = [i for i, e in enumerate(self._template.elements)
                   if e.serial_increment is not None]
        if index is None:
            if len(serials) != 1:
                raise KeyError(
                    "the template has no serial field" if not serials else
                    f"the template has {len(serials)} serial fields; "
                    f"name one by index")
            index = serials[0]
        elif index not in serials:
            raise KeyError(f"element {index} is not a serial (^SN) field")
        if isinstance(increment, bool) or not isinstance(increment, int):
            raise ValueError(f"increment {increment!r}: expected an int")
        self._serials.append((index, _text(start), increment, bool(leading_zeros)))

    # -- output -------------------------------------------------------------

    def _build(self, template: bool):
        document, _ = self._parse()
        elements = document.elements
        # A serial field's literal is what a canvas shows and ^SN's start is
        # what a printer counts from; a file may give either, so both move.
        for index, start, increment, zeros in self._serials:
            element = elements[index]
            element.serial_start = start
            element.serial_increment = increment
            element.serial_leading_zero = zeros
            if element.data_literal():
                setattr(element, element.data_attribute, start)
        pairs = sorted(self._fields.items())
        if template:
            workflow.apply_field_data(document, pairs)
            for element in elements:
                if element.field_number in self._prompts:
                    element.field_prompt = self._prompts[element.field_number]
        else:
            workflow.fill_template(document, pairs)
        for index, data in self._edits:
            setattr(elements[index], elements[index].data_attribute, data)
        if self._copies is not None:
            document.print_quantity = self._copies
        return document

    def to_zpl(self, template: bool = False) -> str:
        """The label as ZPL text - what --save-file writes.

        With `template` the ^FN fields and the ^DF stay, so the result is a
        template again (carrying any prompts set); field data is not written,
        since a template's data is supplied at print time.
        """
        return self._build(template).to_zpl()

    def render(self):
        """The filled label as a PIL image, drawn at the template's size and
        resolution."""
        document = self._build(False)
        return renderer.ZPLRenderer(
            document.label_width, document.label_height, document.dpi
        ).render(document.to_zpl())

    def save(self, path, template: bool = False) -> str:
        """Write the ZPL to `path` (.zpl added if it has no extension) and
        return the path written. OSError if it cannot be."""
        path = workflow.save_filename(str(path))
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.to_zpl(template))
        return path

    def save_image(self, path) -> None:
        """Write the rendered label to `path`; the format follows its suffix."""
        self.render().save(path)
