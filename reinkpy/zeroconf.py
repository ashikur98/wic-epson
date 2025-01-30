# SPDX-License-Identifier: AGPL-3.0-or-later
__all__ = (
    'Browser',
    'find'
)

import logging
_log = logging.getLogger(__name__)

try:
    from zeroconf import Zeroconf, ServiceStateChange, IPVersion, ServiceBrowser
except ImportError as e:
    _log.warning(e)
    AVAILABLE = False
else:
    AVAILABLE = True

import collections, time


class Browser:

    def __init__(self, types=('_ipp._tcp.local.','_ipps._tcp.local.','_printer._tcp.local.')):
        self.by_type = dict((k,{}) for k in types)
        self.by_addr = collections.ChainMap(*self.by_type.values())
        self.zc = Zeroconf() # ip_version=IPVersion.V4Only

    def run(self, duration):
        # starts a thread
        with ServiceBrowser(self.zc, list(self.by_type.keys()), handlers=[self.on_change]):
            _log.info('Looking for network devices (%s sec)...', duration)
            time.sleep(duration)
        return self

    def on_change(self, zeroconf: Zeroconf, service_type: str, name: str,
                  state_change: ServiceStateChange) -> None:
        _log.info(f"Service {name} of type {service_type} changed: ${state_change.name}")
        info = zeroconf.get_service_info(service_type, name)
        _log.debug(f"{info}")
        d = self.by_type[service_type]
        name = info.get_name()
        for a in info.parsed_scoped_addresses():
            if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                d[a] = name
            elif state_change is ServiceStateChange.Removed:
                d.pop(a, None)


def find(timeout=5):
    return Browser().run(timeout).by_addr.items() if AVAILABLE else ()


if __name__ == "__main__":
    for (addr, name) in find():
        print(f'{name} @{addr}')
