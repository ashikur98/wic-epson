# SPDX-License-Identifier: AGPL-3.0-or-later
__all__ = (
    'SNMPLink',
)

from pysnmp.hlapi import *
import contextlib, functools
import logging
_log = logging.getLogger(__name__)
del logging


class SNMPLink:

    OID_PRINTER = '1.3.6.1.2.1.43'
    OID_ENTERPRISE = '1.3.6.1.4.1'
    OID_ppmPrinterIEEE1284DeviceId = OID_ENTERPRISE + '.2699.1.2.1.2.1.1.3.1'

    def __init__(self, ip, port=161, version='1', user='public'): # 'admin'
        self.ip = ip
        self.port = port
        assert version in ('1', '2c', '3')
        self.version = version
        self.user = user
        # ObjectType(ObjectIdentity('SNMPv2-MIB', 'system'))# ('system', None)
        # self._engine.unregisterTransportDispatcher()

    def _get_cmd(self, oid):
        if self.version == '1':
            auth = CommunityData(self.user, mpModel=0)
        elif self.version == '2c':
            auth = CommunityData(self.user, mpModel=1)
        else:
            auth = UsmUserData(self.user)
        engine = SnmpEngine()
        # TMPFIX: ensure running loop
        import asyncio
        try: asyncio.get_running_loop()
        except RuntimeError: asyncio.set_event_loop(asyncio.new_event_loop())
        return getCmd(
            engine,
            auth,
            UdpTransportTarget((self.ip, self.port)),
            ContextData(),
            ObjectType(ObjectIdentity(oid), None),
            lookupMib=True
        )

    def get(self, oid):
        oid = getattr(self, f'OID_{oid}', oid)
        eInd, eStat, eIdx, varBinds = self._get_cmd(oid) #_iget.send((oid, None))
        if eInd:
            _log.warn(eInd)
        elif eStat:
            _log.warn('%s at %s', eStat.prettyPrint(),
                      varBinds[int(eIdx) - 1][0] if eIdx else '?')
        else:
            _log.debug('\n'.join((v.prettyPrint() for v in varBinds)))
            return varBinds

    @functools.cached_property
    def info(self) -> dict:
        try:
            r = self.get('ppmPrinterIEEE1284DeviceId')[0][1].asOctets().decode('ascii')
            from . import _parse_ieee1284_id
            return _parse_ieee1284_id(r)
        except:
            _log.exception('Reading IEEE 1284 device id failed.')
            return {}
