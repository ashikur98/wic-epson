# SPDX-License-Identifier: AGPL-3.0-or-later
__all__ = (
    'EpsonDriver',
)

import collections, contextlib, dataclasses, functools, hashlib, importlib.resources, re, struct, tomllib, typing
import logging
_log = logging.getLogger(__name__)
del logging


DB = {}
def get_db():
    # global DB
    if not DB:
        with importlib.resources.files('reinkpy').joinpath('epson.toml').open('rb') as f:
            specs = tomllib.load(f)['EPSON']
        for s in specs:
            if 'wkey' in s:
                s['wkey'] = s['wkey'].encode('latin-1')
            # s['brand'] = 'EPSON'
            for m in s.get('models',()):
                DB[m] = collections.ChainMap({'model': m}, s)
    return DB


@dataclasses.dataclass(kw_only=True)
class Spec:
    "Specification for a group of printer models"
    # USB-IF
    brand: str = "EPSON"             # iManufacturer
    idVendor: int = 0x04b8
    idProduct: int = None
    # serial_number: str = None
    # 2-bytes model code, needed to read from EEPROM
    rkey: int = 0
    # 8-bytes, needed to write to EEPROM
    wkey: bytes = None
    # wkey Caesar-shifted
    wkey1: str = None
    # byte length of addresses to (read, write)
    rlen: int = 2
    wlen: int = 2
    mem_low: int = 0x00
    mem_high: int = 0xff
    # known eeprom addresses {addr:list[int], desc, reset}
    mem: list[dict] = ()
    # Available operations {desc, cmd, ...}
    # ops: list[dict] = ()
    # all models with similar specs
    models: list[str] = ()
    model: str = None

    def get_mem(self, pat='waste counter'):
        "Get a merge of entries in mem whose `desc` matches pattern."
        av = {}
        for m in self.mem:
            if re.search(pat, m['desc'], re.I):
                av.update(zip(m['addr'], m.get('reset', [0]*len(m['addr']))))
        if av:
            return {'desc': 'All %ss' % pat, 'addr': list(av.keys()),
                    'reset': list(av.values())}


class Epson:

    def __init__(self, link, **spec):
        self.spec = Spec(**spec) # -> prop
        self.link = link        # d4 / snmp
        self._init_link()

    @property
    def detected_model(self):
        "Automatically detected printer model name"
        if 'MDL' in self.info:
            return re.sub(' Series$', '', self.info['MDL'])
        else:
            _log.warn('Detecting model name failed.')

    @classmethod
    def list_models(cls):
        "List known models"
        return get_db().keys()

    def configure(self, name: str|bool = True) -> typing.Self:
        """Load specs for given model name.

        Pass `True` to autodetect model, `False` to clear specs.
        """
        if name is True:
            name = self.detected_model
        if name:
            if name in get_db():
                self.spec = Spec(**get_db()[name])
            else:
                _log.warn(f'Unknown model name: "{name}"')
        else:
            self.spec = Spec()
        m, d = (self.spec.model, self.detected_model)
        if m and d and m != d:
            _log.warn('Loading specs for model "%s" but the printer '
                      'presents itself as "%s"', m, d)
        return self

    def _mem_ops(self):
        for m in self.spec.mem:
            yield self._make_reset(**m)
        for g in ('waste counter', 'platen pad counter'):
            m = self.spec.get_mem(g)
            if m: yield self._make_reset(**m)

    def _make_reset(self, addr=(), desc='', reset=(), min=(), **kw):
        reset = reset or min or [0]*len(addr)
        f = lambda: self.write_eeprom(*zip(addr, reset), atomic=True)
        f.__name__ = '_'.join(('do_reset', re.subn(r'\W', '_', desc)[0],
                               ''.join('x%02X' % a for a in addr)))
        f.__doc__ = f'Reset {desc} {addr}'
        return f

    def __dir__(self):
        yield from super().__dir__()
        for f in self._mem_ops():
            yield f.__name__

    def __getattr__(self, name):
        if name.startswith('do_'):
            for f in self._mem_ops():
                if name == f.__name__:
                    return f
        raise AttributeError


    def ctrl(self, *msg: bytes | tuple['cmd', 'payload']) -> tuple[bytes, ...]:
        """Send msg to the CTRL channel"""
        return tuple(self._ictrl(*msg))

    def _ictrl(self, *msg: bytes | tuple['cmd', 'payload']) -> typing.Iterator[bytes]:
        with self.ctrl_channel as c:
            for m in self._iencode(*msg):
                yield c(m)

    def _iencode(self, *msg: bytes | tuple['cmd', 'payload']) -> typing.Iterator[bytes]:
        for m in msg:
            if isinstance(m, tuple):
                assert len(m) == 2
                m = self.encode(*m)
            assert isinstance(m, bytes)
            yield m

    def encode(self, cmd: str | tuple[str, str], payload: bytes = b'') -> bytes:
        # payload = bytes(payload)
        if isinstance(cmd, tuple): # "factory command"
            c = ord(cmd[1])
            p = struct.pack('<HBBB', self.spec.rkey, c, ~c & 0xff, (c>>1 & 0x7f) | (c<<7 & 0x80))
            cmd, payload = cmd[0]*2, p + payload
        if isinstance(cmd, str):
            cmd = cmd.encode('ascii')
        # assert len(cmd) == 2
        return cmd + struct.pack('<H', len(payload)) + payload

    def read_eeprom(self, *addr: int) -> list[tuple[int, int|None]]:
        """Read addresses from EEPROM"""
        if not addr:
            addr = range(self.spec.mem_low, self.spec.mem_high+1)
        c = 'B' if self.spec.rlen == 1 else 'H'
        CMD = ('|', 'A') # (0x7c, 0x41)
        res = []
        for (a,r) in zip(addr, self._ictrl(*((CMD, struct.pack('<'+c, a)) for a in addr))):
            try:
                # '@BDC PS EE:ED0100;'
                v = re.match(r'.*?\sEE:([0-9a-fA-F]{6,6});', r.decode('ascii'))
                # values are big endian; here assuming 1-byte
                p, val = struct.unpack('>'+c+'B', bytes.fromhex(v.group(1)))
                assert p == a
                res.append((a, val))
            except:
                _log.warn('Invalid response reading addr %s: %r', a, r)
                res.append((a, None))
        return res

    def write_eeprom(self, *addrval: tuple[int,int], wkey=None, check_read=True,
                     atomic=False) -> bool:
        """Write to EEPROM

        wkey -- secret 54-bit / 8 ASCII chars suffix key as found on XP Series
        check_read -- read `addr` again to check that value was written
        atomic -- try to restore original values on error
        """
        if atomic:
            prev = self.read_eeprom(*(a[0] for a in addrval))
            _log.info('Current EEPROM values: %s', prev)
        _log.info('Writing to EEPROM: %s', addrval)
        if wkey is None:
            wkey = self.spec.wkey
        c = 'B' if self.spec.wlen == 1 else 'H'
        CMD = ('|', 'B') # (0x7c, 0x42)
        # addresses are little endian; field values big endian (here 1-byte)
        res = True
        for ((a,v),r) in zip(addrval,
                             self._ictrl(*((CMD, struct.pack('<'+c+'B', a, v) + wkey)
                                           for (a,v) in addrval))):
            res &= ((b':OK;' in r) and ((not check_read) or
                                        (self.read_eeprom(a) == [(a, v)])))
        if atomic and not res:
            _log.warn('Writing failed. Trying to restore previous values')
            self.write_eeprom(*prev, wkey=wkey, check_read=check_read, atomic=False)
        return res

    def do_status(self):
        "Get a summary of printer state"
        return self.ctrl(('st', b'\x01'))[0] # b'@BDC ST2\r\n...'
        # To turn printer state reply on/off (Remote Mode): ST 02H 00H 00H m1

    def do_rw(self):
        """Run generic "rw" command (for "reset waste"?)"""
        n = self.info.get('serial_number', None)
        if n:
            return self.ctrl(('rw', b'\x00' + hashlib.sha1(n.encode('ascii')).digest()))[0]
        else:
            _log.warn('Serial number unknown. Cannot use "rw" command.')

    def reset_waste(self):
        "Reset all known waste counters for this model"
        for name in dir(self):
            if name.startswith('do_reset_All_waste_counters_'):
                f = getattr(self, name)
                break
        else:
            _log.error('Operation unavailable')
            return
        _log.info('Running %s', f.__name__)
        return f()

    # "REMOTE MODE"
    # 'fl' / 'gm' # before loading firmware, in recovery mode
    # open: ('fl', b'\x01') close: ('fl', b'\x03')
    # [0] cd:01:NA;
    # cx:00;
    # ex:NA;
    # ht:0000;
    # ia: @BDC PS\r\nIA:01;NAVL,NAVL,NAVL,NAVL,NAVL;
    # ii:NA;
    # pc:\x01:NA;
    # pm:@BDC...
    # rj:NA;
    # [1] rs:01:OK; reset? request summary?
    # ti: # set timer 08H 00H 00H YYYY MM DD hh mm ss
    # vi:01:NA;

    # ('Ink Information', b'\x0f\x13\x03(BBB)*', 'inkCartridgeName inkColor inkRemainCounter')

    def find_rkey(self, ikeys=range(0x0,0xffff)):
        "Find and set the 2-bytes read key / model code (brute force)"
        orig = self.spec.rkey
        pos = self.spec.mem_low
        with self.ctrl_channel:
            for c in ikeys:
                _log.info('Trying model code %04X', c)
                self.spec.rkey = c
                if self.read_eeprom(pos)[0][1] is not None:
                    _log.warn('Found model code: %04X', c)
                    return c
            else:
                self.spec.rkey = orig

    def find_wkey(self, ikeys=(b'',), addr=None):
        "Try each key in ikeys iterable to write to addr"
        if addr is None:
            addr = self.spec.mem_low
        val = self.read_eeprom(addr)[0][1]
        newval = val + 1
        # TODO: DB
        if ikeys is None:
            DB = get_db()
            ikeys = set(s.get('wkey', b'') for s in DB.values())
        with self.ctrl_channel:
            for k in ikeys:
                _log.info('Trying key %s', k)
                if self.write_eeprom((addr, newval), wkey=k, check_read=True):
                    _log.warn('>>>    !!! FOUND KEY !!!   %r   <<<', k)
                    print(k)
                    self.write_eeprom((addr, val), wkey=k)
                    self.spec.wkey = k
                    return k


class EpsonD4(Epson):

    def _init_link(self):
        # EJL: "Epson Job Language"
        # "Exit packet mode"
        self.link.CMD_ENTER_D4 = b'\x00\x00\x00\x1b\x01@EJL 1284.4\n@EJL\n@EJL\n'
        self.link.CMD_ENTER_D4_REPLY = b'\x00\x00\x00\x08\x01\x00\xc5\x00'
        self.ctrl_channel = self.link.get_channel('EPSON-CTRL', (0x02, 0x02))
        # data_channel = .get_channel('EPSON-DATA', (0x40, 0x40))

    def _read_id_string(self):
        # Or send '\x1b01@EJL ID\r\n' to data_channel / non-D4?
        r = self.ctrl(('di', b'\x01'))[0]
        assert isinstance(r, bytes) and r.isascii()
        return re.match(r'^@EJL ID\s+((.|\s)+)', r.decode('ascii')).group(1)

    @functools.cached_property
    def info(self) -> dict:
        try:
            r = self._read_id_string()
            from . import _parse_ieee1284_id
            return _parse_ieee1284_id(r)
        except:
            _log.exception('Reading printer ID failed.')
            return {}


class EpsonSNMP(Epson):

    def _init_link(self):
        self.link.OID_EPSON = self.link.OID_ENTERPRISE + '.1248'
        self.link.OID_CTRL = self.link.OID_EPSON + '.1.2.2.44.1.1.2.1'
        self.ctrl_channel = contextlib.nullcontext(self._ctrl_send)

    def _ctrl_send(self, m):
        # *struct.unpack('B'*len(payload), payload)
        res = self.link.get('.'.join((self.link.OID_CTRL, *(str(b) for b in m))))
        #
        return res[0][1].asOctets()

    # @functools.cached_property
    @property
    def info(self) -> dict:
        return self.link.info



def search_bin(bstr=b'', yield_raw=True):
    """Yields read/write operations found in bytes"""
    pat = br'\|\|(?P<length>..)(?P<rkey>..)(?P<cmd>A\xbe\xa0|B\xbd!)'
    for m in re.finditer(pat, bstr):
        try:
            end = m.span()[1]
            payload = bstr[end:end + struct.unpack('<H', m.group('length'))[0] - 5]
            rkey = struct.unpack('<H', m.group('rkey'))[0]
            if m.group('cmd')[0] == 0x41:
                addr = struct.unpack('<H', payload[:2])[0]
                yield f'rkey:{rkey:04x} READ addr:{addr:04x}'
            else:
                a,v = struct.unpack('<HB', payload[:3])
                yield f'rkey:{rkey:04x} WRITE addr:{a:04x} val:{v:02x} wkey:{payload[3:]}'
        except:
            _log.exception()
            yield 'INVALID %r' % m.group()
    if yield_raw: # any 8-chars strings
        for m in re.finditer(b'([\x20-\x7E]{8})', bstr):
            yield m.group().decode('ascii')


if __name__ == '__main__':
    import argparse
    c = argparse.ArgumentParser(
        prog="python -m reinkpy.epson",
        description="Helpers for Epson printers",
        epilog="")
    c.add_argument('--search-file', type=argparse.FileType('rb'),
                   help="""Search a traffic log file (like pcapng)
                   for read/write operations and (if extension is not .pcapng)
                   an "adjustment program" binary for potential keys.""")
    args = c.parse_args()

    if args.search_file is not None:
        for res in search_bin(args.search_file.read(),
                              yield_raw=not args.search_file.name.endswith('.pcapng')):
            print(res)
    else:
        c.print_help()
