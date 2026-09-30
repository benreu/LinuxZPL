^XA
^CI28
^PW812
^LL1218
^FX Registration: a rule along the top and one down the left
^FO30,30^GB752,4,4^FS
^FO30,30^GB4,1150,4^FS
^FX MicroPDF417: whether h is each row's height or the whole symbol's. The
^FX manual's own example at module 2 (mode 3, 1 x 20), mode 0 (1 x 11), mode
^FX 18 (3 x 20) and mode 33 (4 x 4); then data too long for mode 0, at the
^FX ticks
^FO70,70^BY2^BFN,8,3^FDABCDEFGHIJKLMNOPQRSTUV^FS
^FO190,70^BY3^BFN,10,0^FDZPL1^FS
^FO350,70^BY2^BFN,4,18^FDMicroPDF417 mode 18: three columns, twenty rows^FS
^FO560,70^BY2^BFN,12,33^FDMode 33^FS
^FO560,190^BY3^BFN,10,0^FDFar too long for mode 0^FS
^FO559,160^GB2,20,2^FS
^FO530,189^GB20,2,2^FS
^FX Multiple origins: a PDF417 cut into three symbols, the second excluded,
^FX and an X placed by a bare FT after it; a MicroPDF417 cut into two; and
^FX data that fits one symbol
^FM70,300,e,e,440,300^BY2^B7N,4,1,3,10^FDMacro PDF417: this message is cut into three symbols and the second is not printed.^FS
^FT^A0N,30,30^FDX^FS
^FM70,420,250,420^BY2^BFN,4,10^FDA MicroPDF417 message too long for one symbol, in two.^FS
^FM440,420^BY2^B7N,4,1,3^FDOne symbol^FS
^FX TLC39: the manual's own example (p.135), every size left to the defaults
^FO70,560^BT^FD123456,ABCd12345678901234,5551212,88899^FS
^FX Text blocks, each inside a box 6 dots clear of it. Three lines of text
^FX that has more; right justified by the origin's z, the origin at the
^FX tick; a block a line and a half tall
^FO64,694^GB312,107,2^FS
^FO70,700^A0N,30,30^TBN,300,95^FDThe quick brown fox jumps over the lazy dog, and then over the fence and far away^FS
^FO454,694^GB312,107,2^FS
^FO760,700,1^A0N,30,30^TBN,300,95^FDRight justified lines of a text block^FS
^FO759,670^GB2,20,2^FS
^FO64,824^GB312,57,2^FS
^FO70,830^A0N,30,30^TBN,300,45^FDOne line and a half is all this block holds^FS
^FX <<> and another <...>, and a forced break
^FO454,824^GB312,107,2^FS
^FO460,830^A0N,30,30^TBN,300,95^FDa <<> b <x> c\&after a forced break^FS
^FX A no-break space where the line would break, and a soft hyphen where it
^FX would break if it could
^FO64,944^GB172,77,2^FS
^FO70,950^A0N,30,30^TBN,160,65^FDaaaa bbbb cccc^FS
^FO264,944^GB112,77,2^FS
^FO270,950^A0N,30,30^TBN,100,65^FDxx abc­def^FS
^FX A word too long for its block, and a block turned by its own rotation
^FX where the font says N
^FO64,1054^GB162,77,2^FS
^FO70,1060^A0N,30,30^TBN,150,65^FDSupercalifragilistic word^FS
^FO554,944^GB107,212,2^FS
^FO560,950^A0N,30,30^TBR,200,95^FDturned by the text block^FS
^XZ
