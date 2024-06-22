# coding: utf-8

from server import udp_server

from tkinter import Tk, Frame, Button, Checkbutton, filedialog, Entry, Label, \
    BooleanVar, StringVar, IntVar
from tkinter.ttk import Combobox
from multiprocessing import Pool
from os.path import abspath
from re import findall
from wmi import WMI

def server(debug=True, log_file='server.log'):
    '''
    server(debug=True, log_file='server.log') \
        .start(dhcpc=True, dhcpd=True, proxy_dhcpd=True, tftpd=True, httpd=True)
    '''
    return udp_server(debug=debug, log_file=log_file)
def start(**kwargs):
    s = server(debug=kwargs.get('debug'), log_file=kwargs.get('log_file'))
    s.separate = kwargs.get('separate')
    s.path = kwargs.get('path')
    s.dhcpc_port = kwargs.get('dhcpc_port')
    s.dhcpd_port = kwargs.get('dhcpd_port')
    s.proxy_dhcpd_port = kwargs.get('proxy_dhcpd_port')
    s.tftpd_port = kwargs.get('tftpd_port')
    s.httpd_port = kwargs.get('httpd_port')
    s.kernel = kwargs.get('kernel')
    s.menu = kwargs.get('menu')
    s.siaddr = kwargs.get('siaddr')
    s.mask = kwargs.get('mask')
    s.router = kwargs.get('router')
    s.dns = kwargs.get('dns')
    s.begin = kwargs.get('begin')
    s.end = kwargs.get('end')
    s.lease_time = kwargs.get('lease_time')
    s.unicast = kwargs.get('unicast')
    s.broadcast = kwargs.get('broadcast')
    s.start(dhcpc=kwargs.get('dhcpc'), dhcpd=kwargs.get('dhcpd'), proxy_dhcpd=kwargs.get('proxy_dhcpd'), tftpd=kwargs.get('tftpd'), httpd=kwargs.get('httpd'))
def stop(pool):
    pool.close()
    windows.destroy()
    windows.quit()
def combobox_selected_macaddr(*args):
    interface = list(filter(lambda dicts: dicts['macaddr'] == combobox_macaddr_var.get(), interfaces))[0]
    entry_siaddr.set(value=interface['siaddr'])
    entry_mask.set(value=interface['mask'])
    entry_router.set(value=interface['router'])
    entry_begin.set(value=findall(r'(?<!\d)\d{1,3}\.\d{1,3}\.\d{1,3}(?=\.\d)', interface['siaddr'])[0] + '.100')
    entry_end.set(value=findall(r'(?<!\d)\d{1,3}\.\d{1,3}\.\d{1,3}(?=\.\d)', interface['siaddr'])[0] + '.110')

if __name__ == '__main__':
    windows = Tk()
    frame = Frame(windows)
    frame.grid()
    interfaces = [{'macaddr' : o.MACAddress, 'siaddr' : o.IPAddress[0] if isinstance(o.IPAddress, tuple) else '', 'mask' : o.IPSubnet[0] if isinstance(o.IPSubnet, tuple) else '', 'router' : o.DefaultIPGateway[0] if isinstance(o.DefaultIPGateway, tuple) else ''} for o in WMI().query("select MACAddress,IPAddress,IPSubnet,DefaultIPGateway from Win32_NetworkAdapterConfiguration where IPEnabled=TRUE")]
    checkbutton_debug, checkbutton_dhcpc, checkbutton_dhcpd, checkbutton_proxy_dhcpd, checkbutton_tftpd, checkbutton_httpd, entry_separate = BooleanVar(value=1), BooleanVar(value=1), BooleanVar(value=1), BooleanVar(value=1), BooleanVar(value=1), BooleanVar(value=1), BooleanVar(value=1)
    entry_log_file, entry_path, entry_kernel, entry_menu, entry_siaddr, combobox_macaddr_var, entry_mask, entry_router, entry_dns, entry_begin, entry_end, entry_unicast, entry_broadcast = StringVar(value='server.log'), StringVar(value=r'C:\Users\Administrator\Downloads\own-pypxeserver\files'), StringVar(value='pxelinux.0'), StringVar(value='pxelinux.0'), StringVar(value='192.168.0.1'), StringVar(value=interfaces[0]['macaddr']), StringVar(value='255.255.255.0'), StringVar(value='192.168.0.251'), StringVar(value='223.5.5.5'), StringVar(value='192.168.0.100'), StringVar(value='192.168.0.110'), StringVar(value='0.0.0.0'), StringVar(value='255.255.255.255')
    entry_dhcpc_port, entry_dhcpd_port, entry_proxy_dhcpd_port, entry_tftpd_port, entry_httpd_port, entry_lease_time = IntVar(value=68), IntVar(value=67), IntVar(value=4011), IntVar(value=69), IntVar(value=80), IntVar(value=120)
    Checkbutton(frame, text='separate', variable=entry_separate).grid(row=0, column=0)
    Checkbutton(frame, text='debug', variable=checkbutton_debug).grid(row=0, column=1)
    Label(frame, text='log_file').grid(row=0, column=2)
    Entry(frame, textvariable=entry_log_file, width=14).grid(row=0, column=3, columnspan=1)
    Label(frame, text='path').grid(row=1, column=0)
    Entry(frame, textvariable=entry_path, width=50).grid(row=1, column=1, columnspan=5)
    Button(frame, text='...', command=lambda: entry_path.set(abspath(filedialog.askdirectory(initialdir=entry_path.get())))).grid(row=1, column=5)
    Checkbutton(frame, text='dhcpc', variable=checkbutton_dhcpc).grid(row=2, column=0)
    Checkbutton(frame, text='dhcpd', variable=checkbutton_dhcpd).grid(row=2, column=1)
    Checkbutton(frame, text='proxy_dhcpd', variable=checkbutton_proxy_dhcpd).grid(row=2, column=2)
    Checkbutton(frame, text='tftpd', variable=checkbutton_tftpd).grid(row=2, column=3)
    Checkbutton(frame, text='httpd', variable=checkbutton_httpd).grid(row=2, column=4)
    Entry(frame, textvariable=entry_dhcpc_port, width=5).grid(row=3, column=0)
    Entry(frame, textvariable=entry_dhcpd_port, width=5).grid(row=3, column=1)
    Entry(frame, textvariable=entry_proxy_dhcpd_port, width=5).grid(row=3, column=2)
    Entry(frame, textvariable=entry_tftpd_port, width=5).grid(row=3, column=3)
    Entry(frame, textvariable=entry_httpd_port, width=5).grid(row=3, column=4)
    Label(frame, text='kernel').grid(row=4, column=0)
    Entry(frame, textvariable=entry_kernel, width=14).grid(row=4, column=1)
    Label(frame, text='menu').grid(row=5, column=0)
    Entry(frame, textvariable=entry_menu, width=14).grid(row=5, column=1)
    Label(frame, text='siaddr').grid(row=6, column=0)
    Entry(frame, textvariable=entry_siaddr, width=14).grid(row=6, column=1)
    Label(frame, text='macaddr').grid(row=6, column=2)
    combobox_macaddr = Combobox(frame, width=15, textvariable=combobox_macaddr_var, values=[i['macaddr'] for i in tuple(interfaces)])
    combobox_macaddr.current(0)
    combobox_macaddr.bind('<<ComboboxSelected>>', combobox_selected_macaddr)
    combobox_selected_macaddr()
    combobox_macaddr.grid(row=6, column=3)
    Label(frame, text='mask').grid(row=7, column=0)
    Entry(frame, textvariable=entry_mask, width=14).grid(row=7, column=1)
    Label(frame, text='router').grid(row=8, column=0)
    Entry(frame, textvariable=entry_router, width=14).grid(row=8, column=1)
    Label(frame, text='dns').grid(row=9, column=0)
    Entry(frame, textvariable=entry_dns, width=14).grid(row=9, column=1)
    Label(frame, text='begin').grid(row=10, column=0)
    Entry(frame, textvariable=entry_begin, width=14).grid(row=10, column=1)
    Label(frame, text='end').grid(row=11, column=0)
    Entry(frame, textvariable=entry_end, width=14).grid(row=11, column=1)
    Label(frame, text='lease_time').grid(row=12, column=0)
    Entry(frame, textvariable=entry_lease_time, width=14).grid(row=12, column=1)
    Label(frame, text='unicast').grid(row=13, column=0)
    Entry(frame, textvariable=entry_unicast, width=14).grid(row=13, column=1)
    Label(frame, text='broadcast').grid(row=14, column=0)
    Entry(frame, textvariable=entry_broadcast, width=14).grid(row=14, column=1)
    pool = Pool(processes=1)
    Button(frame, text='start', command=lambda: pool.apply_async(func=start, kwds={ \
        'debug':checkbutton_debug.get(), \
        'log_file':entry_log_file.get(), \
        'separate':entry_separate.get(), \
        'path':entry_path.get(), \
        'dhcpc_port':entry_dhcpc_port.get(), \
        'dhcpd_port':entry_dhcpd_port.get(), \
        'proxy_dhcpd_port':entry_proxy_dhcpd_port.get(), \
        'tftpd_port':entry_tftpd_port.get(), \
        'httpd_port':entry_httpd_port.get(), \
        'kernel':entry_kernel.get(), \
        'menu':entry_menu.get(), \
        'siaddr':entry_siaddr.get(), \
        'mask':entry_mask.get(), \
        'router':entry_router.get(), \
        'dns':entry_dns.get(), \
        'begin':entry_begin.get(), \
        'end':entry_end.get(), \
        'lease_time':entry_lease_time.get(), \
        'unicast':entry_unicast.get(), \
        'broadcast':entry_broadcast.get(), \
        'dhcpc':checkbutton_dhcpc.get(), \
        'dhcpd':checkbutton_dhcpd.get(), \
        'proxy_dhcpd':checkbutton_proxy_dhcpd.get(), \
        'tftpd':checkbutton_tftpd.get(), \
        'httpd':checkbutton_httpd.get() \
    })).grid(row=15, column=0)
    Button(frame, text='stop', command=lambda: stop(pool)).grid(row=15, column=1)
    windows.mainloop()