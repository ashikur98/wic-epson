# SPDX-License-Identifier: AGPL-3.0-or-later

try:
    import importlib.metadata
    try:
        __version__ = importlib.metadata.version(__name__)
    except importlib.metadata.PackageNotFoundError:
        __version__ = None
    del importlib
except ImportError:
    __version__ = None

import logging, os
logging.basicConfig(format="{levelname: <6} {name: <16} {message}",
                    level=os.environ.get('LOGLEVEL', logging.INFO), style="{")
_log = logging.getLogger(__name__)
del logging, os

import functools


__doc__ = """See README."""

PREFACE = """Dear User,

ReInkPy is user-made, collaborative, free software.
It is offered to you with the sub-legal request that you will fight against
e-waste and planned obsolescence beyond fixing your own printer. Please

  + Report on how it works for specific devices;
  + Help others fix theirs;
  + Support #FreeSoftware and the #RightToRepair — vote, lobby, join, donate.

"""


def _parse_ieee1284_id(b: str) -> dict:
    "Parse IEEE 1284 device id string"
    _log.debug(f'Parsing "{b}"')
    try:
        assert b.isascii()
        d = dict((k, v) for (k,s,v) in
                 (kv.partition(':') for kv in b.split(';') if kv))
        if 'MANUFACTURER' in d: d['MFG'] = d['MANUFACTURER']
        if 'MODEL' in d: d['MDL'] = d['MODEL']
        if 'COMMAND SET' in d: d['CMD'] = d['COMMAND SET']
        if 'CMD' in d: d['CMD'] = tuple(d['CMD'].split(','))
        # COMMENT ; ACTIVE COMMAND SET
        return d
    except:
        _log.exception('Invalid ID string: %r', b)


class Device:

    @property
    def brand(self) -> str|None:
        i = self.info
        return i.get('brand') or i.get('MFG') or i.get('MANUFACTURER') or \
            i.get('manufacturer')
    @property
    def model(self) -> str|None:
        i = self.info
        return i.get('model') or i.get('MDL') or i.get('MODEL') or i.get('product')
    @property
    def serial_number(self) -> str|None:
        i = self.info
        return i.get('SN') or i.get('serial_number')

    @property
    def name(self) -> str|None:
        return '%s %s' % (self.brand or '(unknown brand)',
                          self.model or '(unknown model)')

    def __str__(self):
        return '%s sn:%s' % (self.name, self.serial_number or '?')

    @classmethod
    def find(cls, timeout=5):
        "List available printer devices"
        res = [d for c in cls.__subclasses__() for d in c.ifind(timeout=timeout)]
        _log.info('Found %i devices', len(res))
        return res

    @staticmethod
    def from_file(fname):
        return UsbDevice(FileIO(fname))
    @staticmethod
    def from_usb(**spec):
        from .usb import UsbIO
        return UsbDevice(UsbIO.from_spec(**spec))
    @staticmethod
    def from_ip(ip):
        return NetworkDevice(ip)


class UsbDevice(Device):

    @classmethod
    def ifind(cls, **kw):
        from .usb import UsbIO
        for c in (FileIO, UsbIO):
            for i in c.ifind():
                yield cls(i)

    def __init__(self, io):
        self.io = io

    @functools.cached_property
    def info(self):
        from collections import ChainMap
        return ChainMap({}, self.io.info, self.epson.info)

    @functools.cached_property
    def d4(self):
        from .d4 import D4Link
        return D4Link(self.io)

    @functools.cached_property
    def epson(self):
        from .epson import EpsonD4
        return EpsonD4(self.d4).configure()

    def __str__(self):
        return super().__str__() + f' @{self.io}'

    def __repr__(self):
        return f"{self.__class__.__name__}({self.io!r})"


class NetworkDevice(Device):

    @classmethod
    def ifind(cls, timeout=5):
        from .zeroconf import find
        for (ip, name) in find(timeout):
            if ':' not in ip:   # ignore IPv6, not supported by pysnmp
                yield cls(ip, name=name)

    def __init__(self, ip, **kw):
        self.ip = ip
        self.__dict__.update(kw)

    @functools.cached_property
    def info(self):
        from collections import ChainMap
        return ChainMap({}, self.snmp.info, self.epson.info)

    @functools.cached_property
    def snmp(self):
        from .snmp import SNMPLink
        return SNMPLink(self.ip)

    @functools.cached_property
    def epson(self):
        from .epson import EpsonSNMP
        return EpsonSNMP(self.snmp).configure()

    def __str__(self):
        return super().__str__() + f' @{self.ip}'

    def __repr__(self):
        return f"{self.__class__.__name__}({self.ip!r})"


class FileIO:

    @classmethod
    def ifind(cls, globs=('/dev/lp?', '/dev/usb/lp?')):
        from glob import iglob
        from pathlib import Path
        for g in globs:
            for p in iglob(g):
                p = Path(p)
                if p.is_char_device():
                    yield cls(p)

    def __init__(self, path, mode='a+b'):
        self.path = path
        self.mode = mode
        self._nctx = 0

    @functools.cached_property
    def info(self):
        return {'file_path': self.path}

    # def probe(self):
    #     with self:
    #         # IEEE 1284.1 RDC Request Summary cmd
    #         self.write(b'\xa5'       # START-PACKET
    #                    b'\x00\x03'   # PAYLOAD-LENGTH:>H
    #                    b'\x50'       # FLAG:component&reply
    #                    b'\x01\x00')  # RDC RS
    #         r = self.read()
    #     return r

    def __enter__(self):
        if self._nctx == 0:
            self._f = open(self.path, self.mode)
        self._nctx += 1
        assert not self._f.closed
        return self

    def __exit__(self, *exc):
        self._nctx -= 1
        if self._nctx == 0:
            self._f.close()

    def write(self, data):
        return self._f.write(data)

    def read(self, size=None):
        return self._f.read(size)

    def __str__(self):
        return f'file:{self.path}'

    def __repr__(self):
        return f'{self.__class__.__name__}({self.path})'


del functools
