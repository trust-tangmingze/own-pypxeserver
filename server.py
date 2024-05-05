# coding: utf-8

from dhcppython import DHCPPacket, OptionList, MalformedPacketError, \
    options
from http_server import HTTPServer, SimpleHTTPRequestHandler
from tftp_server import TftpServer

from functools import partial
from ipaddress import ip_interface
from logging import basicConfig, FileHandler, getLogger, StreamHandler, \
    DEBUG, INFO
from os.path import getsize, join
from socket import gethostname, inet_aton, inet_ntoa, socket, \
    AF_INET, SO_BROADCAST, SO_REUSEADDR, SOCK_DGRAM, SOL_SOCKET
from struct import pack, unpack
from sys import exit
from threading import Thread
from time import sleep

class udp_server:
    def __init__(self, debug=False, log_file='server.log'):
        self.separate = 1
        self.path = r'C:\Users\Administrator\Downloads\own-pypxeserver\files'
        self.kernel = 'ipxe-x86_64.efi'
        self.menu = 'boot.ipxe'
        self.siaddr = '192.168.0.1'
        self.mask = '255.255.255.0'
        self.router = '192.168.0.251'
        self.dns = '223.5.5.5'
        self.begin = '192.168.0.100'
        self.end = '192.168.0.110'
        self.lease_time = 120
        self.unicast = '0.0.0.0'
        self.broadcast = '255.255.255.255'
        self.chaddr_to_ipaddr = {}
        self.ipaddr_list = [inet_ntoa(pack('!I', ipaddr)) for ipaddr in range(unpack('!I', inet_aton(self.begin))[0], unpack('!I', inet_aton(self.end))[0])]
        # logging
        logging_level = DEBUG if debug else INFO
        basicConfig(
            level=logging_level,
            format='%(asctime)s.%(msecs)03d %(name)s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                FileHandler(log_file, mode='a', encoding=None),
                StreamHandler()
            ]
        )
        self.logger = getLogger('udp_server')
        # threading
        self.threadings = {}
    def start(self, dhcpc=True, dhcpd=True, proxy_dhcpd=True, tftpd=True, httpd=True):
        self.logger.info(f'PATH {self.path}')
        try:
            # dhcpc
            self.threadings.update(self.dhcpc(logger=self.get_short_logger('DHCPc'))) if dhcpc else dhcpc
            # dhcpd
            self.threadings.update(self.dhcpd(logger=self.get_short_logger('DHCPd'))) if dhcpd else dhcpd
            # proxy_dhcpd
            self.threadings.update(self.proxy_dhcpd(logger=self.get_short_logger('PorxyDHCPd'))) if self.separate and proxy_dhcpd else proxy_dhcpd
            # tftpd
            self.threadings.update(self.tftpd(logger=self.get_short_logger('TFTPd'), path=self.path)) if tftpd else tftpd
            # httpd
            self.threadings.update(self.httpd(logger=self.get_short_logger('HTTPd'), path=self.path)) if httpd else httpd
            # thread to start
            [dicts['_thread'].start() for dicts in self.threadings.values() if dicts is not None]
            while all(map(lambda dicts: dicts['_thread'].is_alive(), self.threadings.values())):
                sleep(1)
        except KeyboardInterrupt:
            [dicts['_stop']() for dicts in self.threadings.values() if dicts is not None]
            exit()
    def udp_socket(self):
        socks = socket(AF_INET, SOCK_DGRAM)
        socks.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)
        socks.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        return socks
    def get_logger(self, root_name, child_name):
        '''
        e.g. self.get_logger(self.logger.name, 'DHCPc')
        '''
        return getLogger(f'{root_name}.{child_name}')
    def get_short_logger(self, child_name):
        '''
        e.g. self.get_short_logger('DHCPc')
        '''
        return getLogger(f'{child_name}')
    def dhcpc(self, logger):
        logger.info(f'(68) {self.unicast} started...')
        def _stop():
            logger.info(f'(68) stopped...')
        def _thread():
            another_dhcpd = []
            socks.bind((self.unicast, 68))
            while socks is not None:
                try:
                    msg, addr = socks.recvfrom(65536)
                    dhcp_packet = DHCPPacket.from_bytes(msg)
                except MalformedPacketError as e:
                    logger.warning(f'(68) {e}')
                    continue
                dhcp_server = dhcp_packet.options.by_code(54)
                if dhcp_server:
                    dhcp_server = ip_interface(dhcp_server.data).ip
                    if dhcp_server not in another_dhcpd:
                        if not another_dhcpd:
                            logger.info(f'(68) discovering for another DHCPd on LAN')
                        logger.info(f'(68) another DHCPd detected on your LAN @ {dhcp_server}')
                        another_dhcpd.append(dhcp_server)
                        logger.debug('(68) {} received, MAC {}, XID {}'.format(
                            dhcp_packet.op, \
                            dhcp_packet.chaddr, \
                            dhcp_packet.xid
                        ))
                        logger.debug('(68) msg is %s' % msg)
        socks = self.udp_socket()
        return {'dhcpc' : {'_thread' : Thread(target=_thread, daemon=True), '_stop' : _stop}}
    def dhcpd(self, logger):
        logger.info(f'(67) {self.unicast} started...')
        def _chaddr_to_yiaddr(chaddr):
            if chaddr not in self.chaddr_to_ipaddr:
                yiaddr = self.ipaddr_list[0]
                self.ipaddr_list.pop(0)
                self.chaddr_to_ipaddr.update({chaddr:yiaddr})
                return yiaddr
            return self.chaddr_to_ipaddr[chaddr]
        def _separate(msg, dhcp_packet):
            if dhcp_packet.msg_type == 'DHCPDISCOVER':
                logger.info('(67) {} received, MAC {}, XID {}'.format(
                    dhcp_packet.msg_type, \
                    dhcp_packet.chaddr, \
                    dhcp_packet.xid
                ))
                logger.debug('(67) msg is %s' % msg)
                user_class = dhcp_packet.options.by_code(77)
                if user_class:
                    logger.info(f'(67) iPXE user-class detected')
                    fname = self.menu
                else:
                    fname = self.kernel
                offer_packet = DHCPPacket.Offer(
                    seconds=0, \
                    tx_id=dhcp_packet.xid, \
                    mac_addr=dhcp_packet.chaddr, \
                    yiaddr=self.unicast, \
                    use_broadcast=True, \
                    relay=self.unicast, \
                    sname=gethostname().encode('unicode-escape'), \
                    fname=fname.encode('unicode-escape'), \
                    option_list=OptionList([
                        options.short_value_to_object(13, round(getsize(join(self.path, fname))/1024)*2), \
                        options.short_value_to_object(54, ip_interface(self.siaddr).ip.packed), \
                        options.short_value_to_object(60, 'PXEClient'), \
                        options.short_value_to_object(66, self.siaddr)
                    ])
                )
                offer_packet.siaddr = ip_interface(self.siaddr).ip
                logger.info('(67) {} sent, {}:68, XID {}'.format(
                    offer_packet.msg_type, \
                    self.broadcast, \
                    offer_packet.xid
                ))
                offer_packet = offer_packet.asbytes
                logger.debug(f'(67) offer_packet is {offer_packet}')
                sleep(1) if user_class else ''
                socks.sendto(offer_packet, (str(self.broadcast), 68))
            else:
                logger.info('(67) {} discarded, MAC {}, XID {}'.format(
                    dhcp_packet.msg_type, \
                    dhcp_packet.chaddr, \
                    dhcp_packet.xid
                ))
        def _combine(msg, dhcp_packet):
            if dhcp_packet.msg_type == 'DHCPDISCOVER':
                logger.info('(67) {} received, MAC {}, XID {}'.format(
                    dhcp_packet.msg_type, \
                    dhcp_packet.chaddr, \
                    dhcp_packet.xid
                ))
                logger.debug('(67) msg is %s' % msg)
                user_class = dhcp_packet.options.by_code(77)
                if user_class:
                    logger.info(f'(67) iPXE user-class detected')
                    fname = self.menu
                else:
                    fname = self.kernel
                yiaddr = _chaddr_to_yiaddr(dhcp_packet.chaddr)
                broadcast = ip_interface(f'{self.siaddr}/{self.mask}').network.broadcast_address
                offer_packet = DHCPPacket.Offer(
                    seconds=0, \
                    tx_id=dhcp_packet.xid, \
                    mac_addr=dhcp_packet.chaddr, \
                    yiaddr=yiaddr, \
                    use_broadcast=True, \
                    relay=self.unicast, \
                    sname=gethostname().encode('unicode-escape'), \
                    fname=fname.encode('unicode-escape'), \
                    option_list=OptionList([
                        options.short_value_to_object(1, self.mask), \
                        options.bytes_to_object(b'\x03\x04' + ip_interface(f'{self.router}').ip.packed), \
                        options.bytes_to_object(b'\x06\x04' + ip_interface(f'{self.dns}').ip.packed), \
                        options.short_value_to_object(13, round(getsize(join(self.path, fname))/1024)*2), \
                        options.short_value_to_object(28, broadcast), \
                        options.short_value_to_object(51, self.lease_time), \
                        options.short_value_to_object(54, ip_interface(self.siaddr).ip.packed), \
                        options.short_value_to_object(66, self.siaddr)
                    ])
                )
                offer_packet.siaddr = ip_interface(self.siaddr).ip
                logger.info('(67) {} sent, {}:68, XID {}'.format(
                    offer_packet.msg_type, \
                    self.broadcast, \
                    offer_packet.xid
                ))
                offer_packet = offer_packet.asbytes
                logger.debug(f'(67) offer_packet is {offer_packet}')
                sleep(1) if user_class else ''
                socks.sendto(offer_packet, (str(self.broadcast), 68))
            if dhcp_packet.msg_type == 'DHCPREQUEST':
                logger.info('(67) {} received, MAC {}, XID {}'.format(
                    dhcp_packet.msg_type, \
                    dhcp_packet.chaddr, \
                    dhcp_packet.xid
                ))
                logger.debug('(67) msg is %s' % msg)
                user_class = dhcp_packet.options.by_code(77)
                if user_class:
                    logger.info(f'(67) iPXE user-class detected')
                    fname = self.menu
                else:
                    fname = self.kernel
                yiaddr = _chaddr_to_yiaddr(dhcp_packet.chaddr)
                broadcast = ip_interface(f'{self.siaddr}/{self.mask}').network.broadcast_address
                ack_packet = DHCPPacket.Ack(
                    seconds=0, \
                    tx_id=dhcp_packet.xid, \
                    mac_addr=dhcp_packet.chaddr, \
                    yiaddr=yiaddr, \
                    use_broadcast=True, \
                    relay=self.unicast, \
                    sname=gethostname().encode('unicode-escape'), \
                    fname=fname.encode('unicode-escape') if isinstance(fname, str) else fname, \
                    option_list=OptionList([
                        options.short_value_to_object(1, self.mask), \
                        options.bytes_to_object(b'\x03\x04' + ip_interface(f'{self.router}').ip.packed), \
                        options.bytes_to_object(b'\x06\x04' + ip_interface(f'{self.dns}').ip.packed), \
                        options.short_value_to_object(13, round(getsize(join(self.path, fname))/1024)*2), \
                        options.short_value_to_object(28, broadcast), \
                        options.short_value_to_object(51, self.lease_time), \
                        options.short_value_to_object(54, ip_interface(self.siaddr).ip.packed), \
                        options.short_value_to_object(66, self.siaddr)
                    ])
                )
                ack_packet.siaddr = ip_interface(self.siaddr).ip
                logger.info('(67) {} sent, {}:68, XID {}'.format(
                    ack_packet.msg_type, \
                    self.broadcast, \
                    ack_packet.xid
                ))
                ack_packet = ack_packet.asbytes
                logger.debug(f'(67) ack_packet is {ack_packet}')
                socks.sendto(ack_packet, (str(self.broadcast), 68))
        def _stop():
            logger.info(f'(67) stopped...')
        def _thread():
            socks.bind((self.unicast, 67))
            while socks is not None:
                try:
                    msg, addr = socks.recvfrom(65536)
                    dhcp_packet = DHCPPacket.from_bytes(msg)
                except MalformedPacketError as e:
                    logger.warning(f'(67) {e}')
                    continue
                vendor_class = dhcp_packet.options.by_code(60)
                if vendor_class:
                    _separate(msg, dhcp_packet) if self.separate else _combine(msg, dhcp_packet)
        socks = self.udp_socket()
        return {'dhcpd' : {'_thread' : Thread(target=_thread, daemon=True), '_stop' : _stop}}
    def proxy_dhcpd(self, logger):
        logger.info(f'(4011) {self.siaddr} started...')
        def _stop():
            logger.info(f'(4011) stopped...')
        def _thread():
            socks.bind((self.siaddr, 4011))
            while socks is not None:
                try:
                    msg, addr = socks.recvfrom(65536)
                    dhcp_packet = DHCPPacket.from_bytes(msg)
                except MalformedPacketError as e:
                    logger.warning(f'(4011) {e}')
                    continue
                uuid_guid_based_client = dhcp_packet.options.by_code(97)
                if uuid_guid_based_client:
                    logger.info('(4011) {} received, MAC {}, XID {}'.format(
                        dhcp_packet.msg_type, \
                        dhcp_packet.chaddr, \
                        dhcp_packet.xid
                    ))
                    logger.debug('(4011) msg is %s' % msg)
                    logger.info(f'(4011) Proxy boot filename empty?')
                    fname = dhcp_packet.file if dhcp_packet.file else self.kernel
                    ack_packet = DHCPPacket.Ack(
                        seconds=0, \
                        tx_id=dhcp_packet.xid, \
                        mac_addr=dhcp_packet.chaddr, \
                        yiaddr=self.unicast, \
                        use_broadcast=False, \
                        relay=self.unicast, \
                        sname=gethostname().encode('unicode-escape'), \
                        fname=fname.encode('unicode-escape') if isinstance(fname, str) else fname, \
                        option_list=OptionList([
                            options.short_value_to_object(13, round(getsize(join(self.path, fname))/1024)*2), \
                            options.short_value_to_object(54, ip_interface(self.siaddr).ip.packed), \
                            options.short_value_to_object(60, 'PXEClient'), \
                            options.short_value_to_object(66, self.siaddr), \
                            options.bytes_to_object(uuid_guid_based_client.asbytes)
                        ])
                    )
                    ack_packet.siaddr = ip_interface(self.siaddr).ip
                    logger.info('(4011) {} sent, {}:4011, XID {}'.format(
                        ack_packet.msg_type, \
                        dhcp_packet.ciaddr, \
                        ack_packet.xid
                    ))
                    ack_packet = ack_packet.asbytes
                    logger.debug(f'(4011) ack_packet is {ack_packet}')
                    socks.sendto(ack_packet, (str(dhcp_packet.ciaddr), 4011))
        socks = self.udp_socket()
        return {'proxy_dhcpd' : {'_thread' : Thread(target=_thread, daemon=True), '_stop' : _stop}}
    def tftpd(self, logger, path):
        logger.info(f'(69) {self.unicast} started...')
        def _stop():
            logger.info(f'(69) stopped...')
        def _thread():
            server.listen(self.unicast, 69)
        server = TftpServer(tftproot=path, logger=logger)
        return {'tftpd' : {'_thread' : Thread(target=_thread, daemon=True), '_stop' : _stop}}
    def httpd(self, logger, path):
        logger.info(f'(80) {self.unicast} started...')
        def _stop():
            logger.info(f'(80) stopped...')
        def _thread():
            server.serve_forever()
        server = HTTPServer((self.unicast, 80), partial(SimpleHTTPRequestHandler, directory=path, logger=logger))
        return {'httpd' : {'_thread' : Thread(target=_thread, daemon=True), '_stop' : _stop}}

if __name__ == '__main__':
    server = udp_server(debug=True)
    server.start(dhcpc=True, dhcpd=True, proxy_dhcpd=True, tftpd=True, httpd=True)