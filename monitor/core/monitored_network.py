import asyncio
from copy import copy
from datetime import datetime
from logging import getLogger
import re

from misc.configuration import flatten_dict

DEFAULT_SETTINGS = {
    'active': True,
    'test_ip': 'google.co.id',
    'has_reconnect_thread': False,
    'weight': 1,
    'network_type': 'dhcp',
    'num_of_tests': 5,
}

PING_SUCCESS_RE = re.compile(r'(?P<max>\d+)\s+packets\s+transmitted,\s+' +\
        r'(?P<count>\d+)\s+received')

DEFROUTE_RE = re.compile(r'^default\s+(?P<route>via \d+\.\d+\.\d+\.\d+)\s+' +\
        r'src\s+(?P<local_ip>\d+\.\d+\.\d+\.\d+)')

NETWORK_RE = re.compile(r'^(?P<network>\d+\.\d+\.\d+\.\d+/\d+)')


class MonitoredNetwork(object):

    app = None
    interface_name = None
    settings = None
    logger = None

    connected = False
    local_ip = None
    network = None
    route = None

    ping_count = 0
    last_restart = datetime.now()
    last_disconnect = None

    def __init__(self, app, name, user_settings):
        self.app = app
        self.interface_name = name
        self.logger = getLogger(type(self).__name__)

        settings = copy(DEFAULT_SETTINGS)
        settings.update(user_settings)
        self.settings = dict(flatten_dict(None, settings))


    async def on_connect(self):
        self.last_disconnect = None
        self.logger.info('Interface %s is connected.', self.interface_name)

        process = await asyncio.create_subprocess_exec(
                'ip', 'route', 'list', 'dev', self.interface_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)

        out, _ = await process.communicate()
        out = out.decode('utf-8')
        lines = [line.strip() for line in out.splitlines()]
        if len(lines) < 2:
            self.logger.debug('Not connected, no default route.')
            return

        match = DEFROUTE_RE.search(lines[0])
        if match is None:
            self.logger.debug('Regex default route does not match.')
            return

        self.route = match.group('route')
        self.local_ip = match.group('local_ip')

        match = NETWORK_RE.search(lines[1])
        if match is None:
            self.logger.debug('Regex network does not match.')
            return

        self.network = match.group('network')
        self.connected = True


    async def on_disconnect(self):
        self.last_disconnect = datetime.now()
        self.logger.info('Interface %s is disconnected.', self.interface_name)
        self.connected = False
        self.local_ip = None
        self.network = None
        self.route = None


    async def restart(self):
        self.last_restart = datetime.now()
        self.last_disconnect = None
        await asyncio.sleep(5)
        self.logger.info('Restart %s interface...', self.interface_name)
        process = await asyncio.create_subprocess_exec(
                '/etc/init.d/net.%s' % self.interface_name, 'restart',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)

        await process.wait()
        self.last_restart = datetime.now()
        self.logger.info('Restart completed.')
        self.app.loop.create_task(self.ping())


    async def ping(self):
        if self.ping_count:
            self.ping_count = self.settings['num_of_tests']
            return

        self.ping_count = self.settings['num_of_tests']
        while self.ping_count > 0 and self.app.is_active:
            await asyncio.sleep((1 / self.ping_count) * 300)
            if self.app.is_defining_route or self.app.reroute_timestamp:
                self.ping_count = 0
                return

            self.logger.info('Ping with %s interface...', self.interface_name)

            process = await asyncio.create_subprocess_exec(
                    'ping', '-qn', '-I', self.interface_name, '-c2', '-W5',
                    '-w15', self.settings['test_ip'],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)

            out, err = await process.communicate()
            out = out.decode('utf-8')
            match = PING_SUCCESS_RE.search(out)
            success = len(err) == 0 and match is not None and \
                    int(match.group('max')) and int(match.group('count'))

            if success:
                self.ping_count -= 1
                self.logger.info('Ping success.')
            else:
                self.ping_count = 0
                self.logger.error('Ping failed with %s.', out)
                delta_restart = (datetime.now() - self.last_restart).seconds
                if self.last_disconnect is not None:
                    delta_disconn = (datetime.now() - self.last_disconnect).seconds
                else:
                    delta_disconn = 61

                if delta_restart > 60 and delta_disconn > 60:
                    self.app.loop.create_task(self.restart())
