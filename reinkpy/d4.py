# SPDX-License-Identifier: AGPL-3.0-or-later
"""A partial and rough implementation of IEEE 1284.4"""
__all__ = (
    'D4Link',
    'decode',
)

from . import helpers

import collections, contextlib, struct, time
import logging
_log = logging.getLogger(__name__)
del logging


DELAY = 0.0


class D4Link:

    CMD_ENTER_D4: bytes = None
    CMD_ENTER_D4_REPLY: bytes = None

    class protocol:
        hTuple = collections.namedtuple('D4PacketHeader', 'psid ssid length credit control')
        hStruct = struct.Struct('>BBHBB')
        hLen = hTuple.hLen = struct.calcsize(hStruct.format) # 6
        hTuple.payload_length = property(lambda self: self.length - self.hLen)
        hTuple.cid = property(lambda self: self[:2])

        @classmethod
        def decode(cls, b):
            "Returns (header, payload)"
            return cls.hTuple(*cls.hStruct.unpack(b[:cls.hLen])), b[cls.hLen:]

        @classmethod
        def encode(cls, payload=b'', psid=0, ssid=0, credit=1, control=0):
            payload = bytes(payload)
            return cls.hStruct.pack(psid, ssid, (cls.hLen + len(payload)),
                                    credit, control) + payload

    def __init__(self, target):
        self.target = target
        self._init_channels()
        self._nctx = 0

    def _init_channels(self):
        self.channels = {}      # (psid, ssid): channel
        self.txn = self.channels[TXChannel.cid] = TXChannel(self)

    def __enter__(self):
        if self._nctx == 0:
            self._exit_stack = contextlib.ExitStack()
            self._exit_stack.enter_context(self.target)
            if self.CMD_ENTER_D4:
                _log.info('Entering IEEE 1284.4 mode...')
                assert self.target.write(self.CMD_ENTER_D4)
                r = b''
                for i in range(5):
                    r += self.target.read()
                    if self.CMD_ENTER_D4_REPLY in r:
                        break
                else:
                    _log.warn('Entering IEEE 1284.4 mode failed with response: %r' % r)
            if not self._send_init():
                raise Exception('Init failed')
        self._nctx += 1
        return self

    def _send_init(self, revision=0x20):
        res, rev = self.txn('Init', revision)
        if res == 0x00:
            return True
            # return self.txn('OpenChannel', 0, 0)  # needed in rev 0x10?
        elif res in (0x01, 0x0B):
            _log.warning('Init reply: retry later...')
            raise NotImplemented
        elif res == 0x02:
            if rev != revision:
                if rev == 0x10:
                    self.txn.protocol = protocol_0x10
                elif rev == 0x20:
                    self.txn.protocol = protocol_0x20
                else:
                    _log.error('Unknown revision %s', rev)
                    return
                return self._send_init(rev)

    def __exit__(self, *exc):
        self._nctx -= 1
        if self._nctx == 0:
            if self.txn('Exit'):
                self.txn.credit = 0
                _log.info('Exit OK')
            else:
                _log.error('Exit failed')
            self._exit_stack.close()

    # def __call__(self, *a, **kw):
    #     return self.txn(*a, **kw)

    def send(self, payload, channel, credit=1, control=0, cost=1, check=True):
        if check:
            for retry in range(3):
                if channel.credit >= cost: # TODO: retry in loop?
                    break
                self.txn('CreditRequest', *channel.cid)
                # self.txn('Credit', *channel.cid, 1)
            else:
                _log.error('Missing credits to send on %s', (channel.cid,))
                return
        channel.credit -= cost
        b = self.protocol.encode(payload, *channel.cid, credit, control)
        _log.debug('Sending on %s: %s', channel.cid, helpers.hexdump(b, prefix='\n<<'))
        return self.target.write(b)

    def retreive(self, retries=6): # needs grouping packets->message?
        rest = getattr(self, '_received', b'')
        for i in range(1 + retries):
            resp = self.target.read()
            if resp:
                rest += resp
                header, payload = self.protocol.decode(rest)
                if len(payload) < header.payload_length:
                    continue
                payload, rest = payload[:header.payload_length], payload[header.payload_length:]
                self._received = rest
                return self._on_received(header, payload)

    def _on_received(self, header, payload): # dispatch to channels
        _log.debug('Received packet in %s: %s', header, helpers.hexdump(payload, prefix='\n>>'))
        if header.cid not in self.channels:
            _log.warning('Ignoring packet received on unknown channel: %s', header.cid)
            return
        c = self.channels[header.cid]
        c.credit += header.credit # piggybacked
        if payload:
            return c.on_received(payload, header)

    def get_channel(self, serviceName=None, cid=None):
        if serviceName is None and cid is None:
            raise ValueError('A service name or channel ID is required')
        elif serviceName and cid:
            if cid not in self.channels:
                self.channels[cid] = Channel(self, cid, serviceName)
            c = self.channels[cid]
            assert c.name == serviceName
            return c
        elif cid:
            if cid in self.channels:
                return self.channels[cid]
            else:
                p = self.txn('GetServiceName', cid[1])
        else:
            for c in self.channels.values():
                if c.name == serviceName:
                    return c
            else:
                p = self.txn('GetSocketID', serviceName)
        if not p or p.result != 0x00:
            _log.error('Cannot get channel for %s %s: %s', (serviceName, cid, p))
            return
        cid = (p.socketID, p.socketID) # mirror peer
        c = self.channels[cid] = Channel(self, cid, p.serviceName)
        return c


def _make_tx_command(code, name, sformat, fields, defaults=()):
    hTuple = collections.namedtuple('%s' % name, fields, defaults=defaults)
    hTuple.code = code
    hTuple.name = name

    hStruct = struct.Struct('>' + sformat.replace('*', ''))
    hFormat = hStruct.format
    hLen = struct.calcsize(hStruct.format)
    star = fields[-1] if sformat.endswith('*') else None # serviceName

    def decode(b):
        if len(b) < hLen and not star:
            # _log.debug('Decoding truncated commmand: %s', b.hex())
            for sCap in range(len(hFormat)-1,1,-1):
                bCap = struct.calcsize(hFormat[:sCap])
                if bCap <= len(b):
                    return hTuple(*struct.unpack(hFormat[:sCap], b[:bCap]))
        f = hStruct.unpack(b[:hLen])
        if star:
            return hTuple(*f, b[hLen:].decode('ascii'))
        else:
            return hTuple(*f)
    hTuple.decode = staticmethod(decode)

    if star:
        def encode(*a, **kw):
            t = hTuple(*a, **kw)
            # _log.debug('Encoding: %s', t)
            return (code.to_bytes(1, 'big') + hStruct.pack(*t[:-1]) + t[-1].encode('ascii'))
    else:
        def encode(*a, **kw):
            t = hTuple(*a, **kw)
            # _log.debug('Encoding: %s', t)
            return code.to_bytes(1, 'big') + hStruct.pack(*t)
    hTuple.encode = staticmethod(encode)

    return hTuple



class protocol:
    @classmethod
    def decode(cls, b):
        cmd = cls.cmd_by_code[struct.unpack('B', b[:1])[0]]
        return cmd.decode(b[1:])

    @classmethod
    def encode(cls, cmd, *a, **kw):
        return cls.cmd_by_name[cmd].encode(*a, **kw)

    ERRORS = dict((
        (0x80, 'A malformed packet was received. All fields in the packet shall be ignored.'),
        (0x81, 'A packet was received for which no credit had been granted. The packet was ignored.'),
        (0x82, ' A 1284.4 reply was received that could not be matched to an outstanding command. The reply was ignored. Credit granted in the reply was ignored.'),
        (0x83, ' A packet of data was received that was larger than the negotiated maximum size for the socket indicated. The data was ignored'),
        (0x84, ' A data packet was received for a channel that was not open.'),
        (0x85, ' A reply packet with an unknown Result value was received.'),
        (0x86, ' Piggybacked credit received in a data packet caused a credit overflow for that channel.'),
        (0x87, ' A reserved or deprecated IEEE 1284.4 command or reply was received. Any piggybacked credit was ignored.')))

class protocol_0x20(protocol):
    "Transaction channel protocol revision 0x20"
    cmd_by_name = dict((args[1], _make_tx_command(*args)) for args in (
        (0x00, 'Init',                'B',     'revision', (0x20,)),
        (0x80, 'InitReply',           'BB',    'result revision', (0x20,)),
        (0x01, 'OpenChannel',         'BBHHH', 'sidP sidS maxPTS maxSTP maxCredit',
         (0x100, 0x100, 0x0)),
        (0x81, 'OpenChannelReply',    'BBBHHHH',
         'result sidP sidS maxPTS maxSTP maxCredit grantedCredit', (None,)*5),
        (0x02, 'CloseChannel',        'BB',    'sidP sidS'),
        (0x82, 'CloseChannelReply',   'BBB',   'result sidP sidS'),
        (0x03, 'Credit',              'BBH',   'sidP sidS addCredit'),
        (0x83, 'CreditReply',         'BBB',   'result sidP sidS'),
        (0x04, 'CreditRequest',       'BBH',   'sidP sidS maxCredit', (0x0,)),
        (0x84, 'CreditRequestReply',  'BBBH',  'result sidP sidS addCredit'),
        (0x08, 'Exit',                '',      ''),
        (0x88, 'ExitReply',           'B',     'result'),
        (0x09, 'GetSocketID',         '*',     'serviceName'), # <=40bytes
        (0x89, 'GetSocketIDReply',    'BB*',   'result socketID serviceName'),
        (0x0A, 'GetServiceName',      'B',     'socketID'),
        (0x8A, 'GetServiceNameReply', 'BB*',   'result socketID serviceName'),
        (0x7F, 'Error',               'BBB',   'errorPSID errorSSID errorCode')
    ))
    cmd_by_code = dict((c.code, c) for c in cmd_by_name.values())

class protocol_0x10(protocol):
    "Transaction channel protocol revision 0x10."
    # undocumented ? taken from d4lib.c
    cmd_by_name = dict((args[1], _make_tx_command(*args)) for args in (
        (0x00, 'Init',                'B',     'revision', (0x10,)),
        (0x80, 'InitReply',           'BB',    'result revision', (0x10,)),
        (0x01, 'OpenChannel',         'BBHHHH', 'sidP sidS maxPTS maxSTP maxCredit initCredit',
         (0x0100, 0x0100, 0x0000, 0x0000)),
        (0x81, 'OpenChannelReply',    'BBBHHHH',
         'result sidP sidS maxPTS maxSTP maxCredit grantedCredit', (None,)*5), # no grantedCredit?
        (0x02, 'CloseChannel',        'BBB',    'sidP sidS x1', (0x00,)),
        (0x82, 'CloseChannelReply',   'BBB',   'result sidP sidS'),
        (0x03, 'Credit',              'BBH',   'sidP sidS addCredit'),
        (0x83, 'CreditReply',         'BBB',   'result sidP sidS'),
        (0x04, 'CreditRequest',       'BBHH',  'sidP sidS x1 x2', (0x0080, 0xffff)),
        (0x84, 'CreditRequestReply',  'BBBH',  'result sidP sidS addCredit'),
        (0x08, 'Exit',                '',      ''),
        (0x88, 'ExitReply',           'B',     'result'),
        (0x09, 'GetSocketID',         '*',     'serviceName'), # <=40bytes
        (0x89, 'GetSocketIDReply',    'BB*',   'result socketID serviceName'),
        (0x0A, 'GetServiceName',      'B',     'socketID'),
        (0x8A, 'GetServiceNameReply', 'BB*',   'result socketID serviceName'),
        (0x7F, 'Error',               'BBB',   'errorPSID errorSSID errorCode')
    ))
    cmd_by_code = dict((c.code, c) for c in cmd_by_name.values())



class TXChannel:
    cid = (0x00, 0x00)
    name = '(transaction channel)'
    # maxPTS, maxSTP = (64, 64) # max packet size NotImplemented
    protocol = protocol_0x20

    def __init__(self, link):
        self.link = link
        self.credit = 0

    def __enter__(self):
        self.link.__enter__()
        return self

    def __exit__(self, *exc):
        self.link.__exit__(*exc)

    def __call__(self, cmd, *a, **kw):
        ok = self.send(cmd, *a, **kw)
        self._received = None
        time.sleep(DELAY)
        for r in range(8):
            self.link.retreive()
            if self._received:
                p = self._received
                if p.name == cmd + 'Reply':
                    return p
                else:
                    _log.debug('TX: dropping packet (not a %s): %s', cmd+'Reply', p)
        _log.warning('TX: did not receive expected %s', cmd+'Reply')

    def send(self, cmd, *a, **kw):
        payload = self.protocol.encode(cmd, *a, **kw)
        _log.debug('TX: sending: %s', (self.protocol.decode(payload),))
        return self.link.send(payload, self, cost=0 if cmd == 'Init' else 1,
                              check=cmd not in ('CreditRequest', 'Credit'))

    def on_received(self, data, header=None):
        try:
            p = self.protocol.decode(data)
        except:
            _log.warning('TX: received invalid packet:\n%s',
                         helpers.hexdump(data, prefix='\n>>'))
        else:
            _log.debug('TX: received: %s', (p,))
            self._received = p
            if hasattr(p, 'addCredit'):
                c = self.link.channels[(p.sidP, p.sidS)]
                c.credit += p.addCredit
                _log.debug('TX: added %i credits to (%s,%s), now has %i', p.addCredit, *c.cid, c.credit)
            if p.name == 'Error':
                _log.warning('TX: received: %s %s', p, self.protocol.ERRORS[p.errorCode])

class Channel:

    def __init__(self, link, cid, name):
        self.link = link
        self.cid = cid
        self.name = name
        self.credit = 0
        # maxPTS, maxSTP = (0xffff, 0xffff)
        self._nctx = 0

    def __enter__(self):
        if self._nctx == 0:
            self.link.__enter__().txn('OpenChannel', *self.cid)
        self._nctx += 1
        return self

    def __exit__(self, *exc):
        self._nctx -= 1
        if self._nctx == 0:
            self.link.txn('CloseChannel', *self.cid)
            self.link.__exit__(*exc)

    def __call__(self, data):
        if not isinstance(data, bytes):
            raise Exception('D4 Channel %s: invalid data (must be bytes): %r' % (self.name, data))
        ok = self.send(data)
        return self.retreive()

    def retreive(self, retries=6):
        # if getattr(self, '_received', None):
        #     _log.debug('%s: Dropping previous packet received: %s', self.name, self._received)
        self._received = None
        for r in range(1 + retries):
            self.link.retreive()
            if self._received:
                return self._received

    def send(self, data):
        _log.debug('%s << %s', self.name, helpers.hexdump(data))
        return self.link.send(data, self)

    def on_received(self, data, header=None):
        _log.debug('%s >> %s', self.name, data)
        self._received = data


def decode(packets):
    """Yields (header, payload) from iterable of `bytes`"""
    prot = protocol_0x20
    for b in packets:
        header, payload = D4Link.protocol.decode(b)
        if header.cid == TXChannel.cid:
            for p in [prot, protocol_0x20, protocol_0x10]:
                try:
                    payload = p.decode(payload)
                except:
                    pass
                else:
                    prot = p
                    break
        yield header, payload
