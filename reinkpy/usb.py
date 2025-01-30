# SPDX-License-Identifier: AGPL-3.0-or-later
__all__ = (
    'UsbIO',
)

from .helpers import hexdump

import functools
import logging
_log = logging.getLogger(__name__)
del logging

AVAILABLE = False
try:
    import usb
    import usb.backend.libusb1, usb.backend.openusb, usb.backend.libusb0
    for m in (usb.backend.libusb1, usb.backend.openusb, usb.backend.libusb0):
        backend = m.get_backend()
        if backend is not None:
            _log.info('Using backend "%s"', m.__name__)
            AVAILABLE = True
            break
except ImportError as e:
    _log.warn(e)

BCLASS_PRINTER = 0x07
DEVICE_FIELDS = ('idVendor', 'idProduct',
                 'iManufacturer', 'iProduct', 'iSerialNumber',
                 'manufacturer', 'product', 'serial_number')
IFACE_FIELDS = ('bInterfaceNumber', 'bAlternateSetting')


class UsbIO:

    def __init__(self, epIn, epOut, ifc, cfg, dev):
        self.epIn, self.epOut = epIn, epOut
        self.ifc, self.cfg, self.dev = ifc, cfg, dev

    def write(self, data):
        # if len(data) > self.epOut.wMaxPacketSize: raise
        _log.debug('Writing...:\n%r', hexdump(data))
        return self.epOut.write(data)

    def read(self, size=None):
        res = self.epIn.read(size or self.epIn.wMaxPacketSize)
        _log.debug('Received %iB:\n%s', len(res), hexdump(res))
        return res

    def __str__(self):
        return 'usb:{0.dev.bus}:{0.dev.address}:{0.ifc.bInterfaceNumber}:({0.epIn.bEndpointAddress},{0.epOut.bEndpointAddress})'.format(self)

    def __enter__(self):
        dev, i = self.dev, self.ifc.index
        try:
            if dev.is_kernel_driver_active(i):
                dev.detach_kernel_driver(i)
                # ? .claim_interface
                self._detached_kernel_driver = i
        except NotImplementedError:
            _log.exception('Ignoring not implemented is_kernel_driver_active')
        # _log.info('Setting configuration...')
        # try:
        #     dev.set_configuration(cfg)
        # except usb.USBError:
        #     _log.exception('Failed to configure device: %s', dev)
        return self

    def __exit__(self, *exc):
        if hasattr(self, '_detached_kernel_driver'):
            try:
                self.dev.attach_kernel_driver(self._detached_kernel_driver)
                del self._detached_kernel_driver
            except usb.core.USBError as e:
                _log.error(e)
        # restore config?

    @classmethod
    def ifind(cls):
        if AVAILABLE:
            for (dev, cfg, ifc) in iter_interfaces():
                eps = get_bulk_io(ifc)
                if eps and ifc.bAlternateSetting == 0:
                    yield cls(*eps, ifc, cfg, dev)

    @classmethod
    def from_spec(cls, **spec):
        _log.info('Locating USB interface for %s...', spec)
        for (dev, cfg, ifc) in iter_interfaces(**spec):
            eps = get_bulk_io(ifc)
            if eps: break
        else:
            raise Exception('No USB interface found for %s' % spec)
        _log.info('Found device %s, interface %s.', dev, eps)
        # if dev.is_kernel_driver_active(ifc.index):
        #     dev.detach_kernel_driver(ifc.index)
        # try:
        #     dev.set_configuration(cfg)
        # except usb.USBError as e:
        #     _log.warning('Failed to configure device: %s, %s', spec, e)
        return cls(*eps, ifc, cfg, dev)

    @functools.cached_property
    def info(self):
        return dict([(k, getattr(self.dev, k)) for k in DEVICE_FIELDS] +
                    [(k, getattr(self.ifc, k)) for k in IFACE_FIELDS])


def iter_interfaces(bClass=BCLASS_PRINTER,  # match bDeviceClass or bInterfaceClass
                    **spec):
    devspec = dict((k,v) for (k,v) in spec.items() if k in DEVICE_FIELDS and v is not None)
    ifacespec = dict((k,v) for (k,v) in spec.items() if k in IFACE_FIELDS and v is not None)
    _log.debug('Looking for interfaces matching:\n  device spec: %s \n  interface spec: %s',
               devspec, ifacespec)
    m = is_bClass(bClass) if bClass is not None else None
    for dev in usb.core.find(True, custom_match=m, **devspec):
        _log.debug(dev._str())
        for cfg in dev:
            _log.debug(cfg._str())
            for iface in usb.util.find_descriptor(cfg, True, **ifacespec):
                _log.debug(iface._str())
                if bClass is None or (
                        dev.bDeviceClass == bClass or iface.bInterfaceClass == bClass):
                    yield (dev, cfg, iface)


def get_bulk_io(iface):
    i = usb.util.find_descriptor(iface, custom_match=lambda e: is_bulk(e) and is_in(e))
    o = usb.util.find_descriptor(iface, custom_match=lambda e: is_bulk(e) and is_out(e))
    return (i, o) if (i and o) else None

is_bulk = lambda e: usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
is_in = lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
is_out = lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT

class is_bClass:
    def __init__(self, bClass):
        self.bClass = bClass
    def __call__(self, device):
        if device.bDeviceClass == self.bClass:
            return True
        for cfg in device: # if specified at interface
            if usb.util.find_descriptor(cfg, bInterfaceClass=self.bClass):
                return True
        return False
