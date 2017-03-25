import asyncio
from copy import copy
from datetime import datetime
from logging import getLogger
import os

from misc.configuration import flatten_dict, load_files_from_shell
from .monitored_network import MonitoredNetwork
from .syslog_handler import SyslogHandler

DEFAULT_SETTINGS = {
    'monitored_networks': {},
    'poll_interval': 5,
    'route': {
        'delay': 10,
        'multipath_table': 323,
        'base_table': 200,
    },
}


class Application(object):

    logger = None
    loop = None
    settings = None
    is_active = True
    is_defining_route = False

    networks = None
    networks_hash = None
    queue = None

    base_dir = None
    root_dir = None

    reroute_timestamp = None
    future = None
    syslog_handler = None


    def __init__(self, loop, base_dir, user_settings=None):
        self.logger = getLogger(type(self).__name__)
        self.loop = loop
        self.base_dir = base_dir
        self.root_dir = os.environ.get('ROOT_DIR', os.path.dirname(base_dir))
        self.networks = []
        self.queue = asyncio.Queue()

        settings = copy(DEFAULT_SETTINGS)
        load_files_from_shell(settings)
        if user_settings is not None:
            settings.update(user_settings)

        self.settings = dict(flatten_dict(
                None,
                settings,
                exclude=
                (
                    'monitored_networks',
                )))


    async def on_network_connected(self, name, timestamp):
        await self.queue.put((1, name, timestamp))


    async def on_network_disconnected(self, name, timestamp):
        await self.queue.put((0, name, timestamp))


    async def execute(self):
        while self.is_active:
            await asyncio.sleep(self.settings['poll_interval'])
            try:
                await self._execute()
            except:
                self.logger.exception()


    async def _execute(self):
        try:
            while not self.queue.empty():
                active, name, _ = await self.queue.get()

                for network in self.networks:
                    if network.interface_name == name:
                        if active:
                            await network.on_connect()
                        else:
                            await network.on_disconnect()
                        self.reroute_timestamp = datetime.now()
                        break

        except asyncio.QueueEmpty:
            pass

        if self.reroute_timestamp is None:
            return

        delta = datetime.now() - self.reroute_timestamp
        delay = self.settings['route.delay']
        if delta.seconds < delay:
            self.logger.debug('Until reroute %i seconds.',
                    delay - delta.seconds)
            return

        self.reroute_timestamp = None

        new_hash = await self.get_networking_hash()
        if self.networks_hash == new_hash:
            self.logger.info('Reroute canceled because same hash.')
            return

        self.logger.info('Defining route...')
        self.is_defining_route = True
        try:
            await self.do_reroute()
            self.networks_hash = await self.get_networking_hash()
            self.logger.info('Route defined.')
        except: # pylint:disable=bare-except
            self.logger.exception('Rerouting error:')

        self.is_defining_route = False


    async def do_reroute(self):
        multipath_table = str(self.settings['route.multipath_table'])
        base_table = self.settings['route.base_table']

        self.logger.debug('Clean routing table.')

        count = len(self.networks)
        if count < 100:
            count = 100

        for ii in range(count):
            await self.purge_routing_table(str(base_table + ii + 1))

        await self.purge_routing_table(multipath_table)

        self.logger.debug(' '.join(['iptables', '-t', 'nat',
                '-F']))
        process = await asyncio.create_subprocess_exec('iptables', '-t', 'nat',
                '-F')

        await process.wait()

        # main table without default gateway
        await self.run_until_error('ip', 'route', 'del', 'default')

        self.logger.debug('Create new routing table.')

        for ii, network in enumerate(self.networks):
            if not network.connected:
                continue

            table_id = str(base_table + ii + 1)

            self.logger.debug(' '.join(['ip', 'rule', 'add', 'prio', table_id,
                    'from',
                    network.local_ip, 'lookup', table_id]))
            process = await asyncio.create_subprocess_exec(
                    'ip', 'rule', 'add', 'prio', table_id, 'from',
                    network.local_ip, 'lookup', table_id)

            await process.wait()

            self.logger.debug(' '.join([
                    'ip', 'route', 'add', 'default',
                    'src', network.local_ip, 'proto', 'static', 'table',
                    table_id, *network.route.split(' ')]))
            process = await asyncio.create_subprocess_exec(
                    'ip', 'route', 'add', 'default',
                    'src', network.local_ip, 'proto', 'static', 'table',
                    table_id, *network.route.split(' '))

            await process.wait()

            self.logger.debug(' '.join(['ip', 'route', 'append', 'prohibit',
                    'default',
                    'metric', '1', 'proto', 'static', 'table', table_id]))
            process = await asyncio.create_subprocess_exec(
                    'ip', 'route', 'append', 'prohibit', 'default',
                    'metric', '1', 'proto', 'static', 'table', table_id)

            await process.wait()

            self.logger.debug(' '.join(['iptables', '-t', 'nat', '-A',
                    'POSTROUTING', '-o', network.interface_name, '-j',
                    'MASQUERADE']))
            process = await asyncio.create_subprocess_exec(
                    'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-o',
                    network.interface_name, '-j', 'MASQUERADE')

            await process.wait()

        self.logger.debug(' '.join(['ip', 'rule', 'del', 'prio', '32765']))
        process = await asyncio.create_subprocess_exec(
                'ip', 'rule', 'del', 'prio', '32765',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)

        await process.wait()

        self.logger.debug(' '.join(['ip', 'rule', 'add', 'prio', '32765',
                'lookup', 'main']))
        process = await asyncio.create_subprocess_exec(
                'ip', 'rule', 'add', 'prio', '32765', 'lookup', 'main')

        await process.wait()

        self.logger.debug(' '.join(['ip', 'rule', 'del', 'prio', '32766']))
        process = await asyncio.create_subprocess_exec(
                'ip', 'rule', 'del', 'prio', '32766',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)

        await process.wait()

        self.logger.debug(' '.join(['ip', 'rule', 'add', 'prio', '32766',
                'lookup', multipath_table]))
        process = await asyncio.create_subprocess_exec(
                'ip', 'rule', 'add', 'prio', '32766', 'lookup', multipath_table)

        await process.wait()

        load_balancing = ['ip', 'route', 'add', 'default', 'table',
                multipath_table, 'proto', 'static']

        hops = [network for network in self.networks if network.connected]

        self.logger.debug(repr(hops))

        if len(hops) == 0:
            load_balancing = None
        elif len(hops) == 1:
            load_balancing.extend(hops[0].route.split(' '))
        else:
            for network in hops:
                load_balancing.append('nexthop')
                load_balancing.extend(network.route.split(' '))
                load_balancing.append('weight')
                load_balancing.append(str(network.settings['weight']))

        if load_balancing:
            self.logger.debug(' '.join(load_balancing))
            process = await asyncio.create_subprocess_exec(*load_balancing)
            await process.wait()

        self.logger.debug(' '.join(['ip', 'route', 'flush', 'cache']))
        process = await asyncio.create_subprocess_exec(
                'ip', 'route', 'flush', 'cache')

        await process.wait()

        for network in self.networks:
            self.loop.create_task(network.ping())


    async def on_syslog_connected(self):
        for name, settings in self.settings['monitored_networks'].items():
            if not settings['active']:
                continue

            network = MonitoredNetwork(self, name, settings)
            self.networks.append(network)

            await network.restart()
            self.loop.create_task(network.ping())

        self.loop.create_task(self.execute())


    def startup(self):
        self.future = self.loop.create_future()

        syslog_handler = SyslogHandler(self)
        server = self.loop.create_datagram_endpoint(syslog_handler,
                ('127.0.0.1', 1979))

        self.loop.create_task(server)
        self.syslog_handler = server
        return self.future


    async def close(self):
        self.syslog_handler.close()


    def shutdown(self):
        self.is_active = False
        self.future.set_result(None)


    async def run_until_error(self, *args):
        max_retry = 5
        while max_retry:
            process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)

            ret = await process.wait()
            if ret:
                break
            max_retry -= 1


    async def purge_routing_table(self, table_id):
        self.logger.debug(' '.join(['ip', 'rule', 'del', 'prio', table_id]))
        await self.run_until_error('ip', 'rule', 'del', 'prio', table_id)

        self.logger.debug(' '.join(['ip', 'route', 'del', 'all', 'table',
                table_id]))
        await self.run_until_error('ip', 'route', 'del', 'all', 'table',
                table_id)


    async def get_networking_hash(self):
        new_hash = []
        for network in self.networks:
            new_hash.append((network.interface_name, network.connected,
                    network.local_ip, network.network, network.route))

        process = await asyncio.create_subprocess_exec(
                'ip', 'route', 'show',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        out, err = await process.communicate()
        out = out.decode('utf-8')
        new_hash.extend(out.splitlines())

        return hash(frozenset(new_hash))
