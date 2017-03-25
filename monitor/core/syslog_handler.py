import asyncio
from logging import getLogger
import re
from dateutil import parser as timeparser

INTF_REMOVE_RE = re.compile(r'^(?P<intf>\w+): removing interface')
WPA_REMOVE_RE = re.compile(r'^interface (?P<intf>\w+) DISCONNECTED')

DHCPCD_ADD_RE = re.compile(r'^(?P<intf>\w+): (adding|changing) default ' +\
        r'route (?P<route>.*)')

WPA_ADD_RE = re.compile(r'^interface (?P<intf>\w+) CONNECTED')
KERNEL_ADD_RE = re.compile(r'^(?P<intf>\w+): link becomes ready')

SYSLOG_MESSAGE_RE = re.compile(r'<(?P<facility>\d+)>' +\
        r'(?P<date>\w{3}\s+\d+\s+\d+:\d+:\d+)\s+' +\
        r'(?P<host>\w+)\s+(?P<prog>[^\[:]+)(\[(?P<pid>\d+)\])?:\s+' +\
        r'(?P<msg>.*)')


class SyslogHandler(asyncio.DatagramProtocol):

    app = None
    logger = None

    connected = False


    def connection_made(self, transport):
        self.logger.debug('Connection made.')


    def datagram_received(self, data, addr):
        """ received data from syslog """
        if not self.connected:
            self.connected = True
            self.app.loop.create_task(self.app.on_syslog_connected())

        message = data.decode()
        sysmatch = SYSLOG_MESSAGE_RE.match(message)
        if sysmatch is None:
            self.logger.debug('Cannot parse syslog with regex: ' + message)
            return

        timestamp = timeparser.parse(sysmatch.group('date'))
        message = sysmatch.group('msg')

        match = DHCPCD_ADD_RE.search(message)
        if match is not None:
            self.app.loop.create_task(self.app.on_network_connected(
                    match.group('intf'), timestamp))

            return

        match = INTF_REMOVE_RE.search(message)
        if match is not None:
            self.app.loop.create_task(self.app.on_network_disconnected(
                    match.group('intf'), timestamp))

            return

        match = WPA_REMOVE_RE.search(message)
        if match is not None:
            self.app.loop.create_task(self.app.on_network_disconnected(
                    match.group('intf'), timestamp))

            return

        match = WPA_ADD_RE.search(message)
        if match is None:
            match = KERNEL_ADD_RE.search(message)
        if match is not None:
            # probably interface with static ip was connected
            self.app.loop.create_task(self.app.on_network_connected(
                    match.group('intf'), timestamp))

            return


    def __init__(self, app):
        self.app = app
        self.logger = getLogger(__name__)


    def __call__(self):
        return self


    def error_received(self, exc):
        """ socket error handler """
        self.logger.error(str(exc))
