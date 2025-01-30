# SPDX-License-Identifier: AGPL-3.0-or-later
from codecs import charmap_decode
# from binascii import hexlify

__all__ = (
    'hexdump',
)


ascii_pr47 = range(0x20,0x7F)
TABLE = dict((*((i,' ') for i in (*range(0x00, 0x20), *range(0x7f, 0x100))),
              *((i, chr(i)) for i in ascii_pr47),
              *((0x00, '°'), # C-@
                (0x09, 'Ť'), # TAB
                (0x0d, 'Ř'), # RET
                (0x1b, 'Ê'), # ESC
                (0x7f, '¬') # DEL
                )))

def hexdump(b, *, W=32, table=TABLE, prefix='\n'):
    if isinstance(b, str):
        b = bytes.fromhex(b)
    def gen():
        for i in range(0, len(b), W):
            chunk = b[i:i+W]
            h = ' '.join('%02X' % c for c in chunk)
            a = charmap_decode(chunk, None, table)[0]
            # yield ('%-{}s\n%-{}s'.format(3*W,3*W)) % (h, '  '.join(a))
            yield ('%-{}s'.format(3*W)) % h
            yield ('%-{}s'.format(3*W)) % '  '.join(a)
            # yield '%-48s  |%-16s|' % (h, a)
    return prefix + prefix.join(gen())
