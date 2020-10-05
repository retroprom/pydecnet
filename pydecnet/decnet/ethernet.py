#!

"""The Ethernet datalink layer.

"""

try:
    from fcntl import *
except ImportError:
    fcntl = None
import select
import socket
import struct
import os
import sys

from .common import *
from . import logging
from . import datalink
from . import pcap

SvnFileRev = "$LastChangedRevision$"

FILL = b'\x42' * 60
ETH_MTU = 1518
ETH_TMO = 100    # ms

class EthPort (datalink.BcPort):
    """DEC Ethernet port class.
    """
    def __init__ (self, datalink, owner, proto, pad = True):
        super ().__init__ (datalink, owner, proto)
        self.pad = pad
        f = self.frame = bytearray (1514)
        f[12:14] = self.proto

    def send (self, msg, dest):
        destb = makebytes (dest)
        if len (destb) != 6:
            raise ValueError ("Invalid destination address length")
        msg = makebytes (msg)
        l = len (msg)
        f = self.frame
        f[0:6] = destb
        f[6:12] = self.macaddr
        if self.pad:
            if l > 1498:
                raise ValueError ("Ethernet packet too long")
            f[14] = l & 0xff
            f[15] = l >> 8
            f[16:16 + l] = msg
            l += 16
        else:
            if l > 1500:
                raise ValueError ("Ethernet packet too long")
            f[14:14 + l] = msg
            l += 14
        self.counters.bytes_sent += l
        self.counters.pkts_sent += 1
        # Always send packet padded to min of 60 if need be, whether
        # pad mode is specified or not.
        if l < 60:
            f[l:60] = FILL[l:60]
        l = max (l, 60)
        f = memoryview (f)[:l]
        if logging.tracing:
            logging.tracepkt ("Sending packet on {} to {}",
                              self.parent.name, dest, pkt = f)
        self.parent.send_frame (f)

class _Ethernet (datalink.BcDatalink, StopThread):
    """DEC Ethernet datalink.
    """
    port_class = EthPort
    
    def __init__ (self, owner, name, dev, config):
        tname = "{}.{}".format (owner.node.nodename, name)
        StopThread.__init__ (self, name = tname)
        datalink.BcDatalink.__init__ (self, owner, name, config)
        self.dev = dev
        if config.random_address:
            r = (random.getrandbits (46) << 2) + 2
            self.hwaddr = Macaddr (r.to_bytes (6, "little"))
        else:
            self.hwaddr = config.hwaddr
        self.randaddr = config.random_address
    
    def open (self):
        # If no explicit address was set, see if we find one to use.
        if self.hwaddr == NULLID:
            for dname, desc, addrs, flags in pcap.findalldevs ():
                if dname == self.dev and addrs:
                    self.hwaddr = Macaddr (addrs[0][0])
        if self.hwaddr == NULLID:
            logging.error ("No hardware address for Ethernet {}", self.name)
            return
        logging.debug ("Ethernet {} hardware address is {}",
                       self.name, self.hwaddr)
        # start receive thread
        self.start ()
        
    def close (self):
        self.stop ()
        
    def create_port (self, owner, proto, pad = True):
        return super ().create_port (owner, proto, pad)

    def receive (self, plen, packet, ts):
        if not packet:
            # pcap_next returns None if we got a timeout
            return
        if plen < 60:
            # Runt???
            return
        proto = packet[12:14]
        try:
            port = self.ports[proto]
        except KeyError:
            # No protocol type match, ignore packet
            self.counters.unk_dest += 1
            return
        dest = packet[:6]
        src = Macaddr (packet[6:12])
        if src.ismulti ():
            # "source routed"?  Ignore
            return
        # Note that we don't count packets that fail the address
        # filter, otherwise we'd count lots of stuff for others.
        if dest == port.macaddr or dest in port.destfilter:
            # We only log packets that make it past the address and
            # protocol type filters.
            if logging.tracing:
                logging.tracepkt ("Received packet on {}",
                                  self.name, pkt = packet)
            if dest[0] & 1:
                self.counters.mcbytes_recv += plen
                self.counters.mcpkts_recv += 1
            port.counters.bytes_recv += plen
            port.counters.pkts_recv += 1
            if port.pad:
                plen2 = packet[14] + (packet[15] << 8)
                if plen < plen2 + 16:
                    logging.debug ("On {}, packet length field {} "
                                   "inconsistent with packet length {}",
                                   self.name, plen2, plen)
                    return
                payload = memoryview (packet)[16:16 + plen2]
            else:
                payload = memoryview (packet)[14:]
            # Pass the payload as "packet" but also pass up the whole
            # PDU for users like the bridge.  Also the third argument,
            # which is timestamp for Pcap (not interesting) but source
            # host/port for Bridge (which we'll need for flooding)
            self.node.addwork (Received (port.owner,
                                         src = src, packet = payload,
                                         pdu = packet, extra = ts))
        else:
            # No address match, count that.  Strictly speaking this is
            # probably only correct for multicast mismatch, but we'll
            # count it for anything to get a sense of the
            # effectiveness of any lower layer (e.g., PCAP) filtering
            # mechanisms.
            self.counters.unk_dest += 1
            
    def nice_read_line (self, req, resp):
        super ().nice_read_line (req, resp)
        r = resp[str (self.name)]
        if req.info == 2:
            r.hardware_address = self.hwaddr

# Some definitions for Linux TAP:
TUNSETIFF = 0x400454ca
IFF_TUN = 0x0001
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000
# sizeof (struct ifreq) is 32 or 40 depending on whether we're on a 32
# bit or 64 bit system.  So we need 32 - 18 = 14 or 40 - 18 = 22 bytes
# of padding respectively to make the ifreq struct come out the right
# size.
if sys.maxsize >> 31:
    # 64 bit system
    ifr_layout = "16sH22x"
else:
    # 32 bit system
    ifr_layout = "16sH14x"

# And for Mac (Darwin) TAP:
# ifr_name, ifru_flags
ifreq = struct.Struct ("=16sH")
sizeof_ifreq = 32
SIOCSIFFLAGS = 0x80000000 + (sizeof_ifreq << 16) + (ord ('i') << 8) + 16
SIOCGIFFLAGS = 0xc0000000 + (sizeof_ifreq << 16) + (ord ('i') << 8) + 17

# API specific classes
if fcntl:
    class _TapEth (_Ethernet):
        def open (self):
            # Set a dummy value in case we get an error
            self.tap = None
            if sys.platform == "linux":
                # Linux-specific preparation
                fd = os.open ("/dev/net/tun", os.O_RDWR)
                # We need 40 - 18 = 22 bytes of padding to make the
                # ifreq struct come out the right size; Linux says
                # it's 40 but what we fill in here only gets us to 18.
                ifr = struct.pack (ifr_layout, self.dev.encode("ascii"),
                                   IFF_TAP | IFF_NO_PI)
                ioctl (fd, TUNSETIFF, ifr)
            else:
                if os.path.sep not in self.dev:
                    self.dev = os.path.join ("/dev", self.dev)
                fd = os.open (self.dev, os.O_RDWR)
            oldflags = fcntl (fd, F_GETFL, 0)
            fcntl (fd, F_SETFL, oldflags | os.O_NONBLOCK)
            self.tap = fd
            self.sellist = ( fd, )
            # Turn the interface on -- needed only on Mac OS
            if sys.platform == "darwin":
                req = bytearray (sizeof_ifreq)
                devname = os.path.basename (self.dev)
                ifreq.pack_into (req, 0, devname.encode ("ascii"), 0)
                s = socket.socket (socket.AF_INET, socket.SOCK_DGRAM, 0)
                ioctl (s, SIOCGIFFLAGS, req)
                name, flags = ifreq.unpack_from (req)
                ifreq.pack_into (req, 0, name, flags | 1)
                ioctl (s, SIOCSIFFLAGS, req)
                s.close ()
            super ().open ()

        def close (self):
            super ().close ()
            try:
                os.close (self.tap)
            except Exception:
                pass
            self.tap = None

        def send_frame (self, buf, skip = None):
            """Send an Ethernet frame.  Ignore any errors, because that's
            the DECnet way.
            """
            try:
                os.write (self.tap, buf)
            except (IOError, TypeError):
                # TypeError will appear if a message is sent while the
                # circuit is being closed, because at that point
                # self.tap has been changed to None.
                pass

        def run (self):
            while True:
                if self.stopnow or not self.tap:
                    break
                try:
                    try:
                        # ETH_TMO is in ms, but select timeout is in seconds.
                        r, w, x = select.select (self.sellist, (),
                                                 self.sellist, ETH_TMO / 1000)
                    except select.error as e:
                        r = True
                    if not r:
                        continue
                    pkt = os.read (self.tap, 1518)
                    if not pkt:
                        continue
                    self.receive (len (pkt), pkt, None)
                except OSError as e:
                    break
else:
    _TapEth = None
     
class _PcapEth (_Ethernet):
    def __init__ (self, owner, name, dev, config):
        super ().__init__ (owner, name, dev, config)
        self.pcap = pcap.pcapObject ()
        self.opened = False
        self.filter_str = None

    def update_filter (self, fs):
        """This method is called whenever the set of enabled addresses
        and/or protocol types and/or promiscuous mode changes.  The
        argument is the new PCAP filter string. 
        """
        self.filter_str = fs
        if self.opened:
            self.pcap.setfilter (fs)
        
    def open (self):
        # Always set promiscuous mode
        self.pcap.open_live (self.dev, ETH_MTU, 1, ETH_TMO)
        super ().open ()
        self.opened = True
        logging.trace ("pcap handle {}", self.pcap.pcap)
        if self.filter_str:
            self.pcap.setfilter (self.filter_str)
            
    def close (self):
        super ().close ()
        # Don't do the close yet, it crashes for reasons yet unknown
        #self.pcap.close ()

    def send_frame (self, buf, skip = None):
        """Send an Ethernet frame.  Ignore any errors, because that's
        the DECnet way.
        """
        try:
            l2 = self.pcap.inject (buf)
        except IOError:
            pass
        
    def run (self):
        while True:
            if self.stopnow:
                break
            try:
                cnt = self.pcap.dispatch (0, self.receive)
            except pcap._pcap.error:
                break

class _BridgeEth (_Ethernet):
    """Class for talking to a Johnny Billquist bridge (somewhere else,
    external to this process), via UDP packets each carrying an
    Ethernet datagram.
    """
    def __init__ (self, owner, name, dev, config):
        super ().__init__ (owner, name, dev, config)
        lport, host, rport = dev.split (":")
        self.lport = int (lport)
        self.host = datalink.HostAddress (host)
        self.rport = int (rport)
        logging.debug ("Ethernet bridge {} initialized on {}, to {} {}",
                       self.name, self.lport, self.host, self.rport)
        
    def open (self):
        self.socket = socket.socket (socket.AF_INET, socket.SOCK_DGRAM,
                                     socket.IPPROTO_UDP)
        self.socket.setsockopt (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        for retry in range (3):
            # This ought to work reliably, but on Python 3.7 it
            # occasionally does not, and on older pythons it seems to
            # fail more frequently.  So try a few times if necessary.
            sock = self.socket
            if not sock:
                return
            self.sellist = [ sock.fileno () ]
            try:
                sock.bind (("", self.lport))
                break
            except (OSError, socket.error):
                logging.exception ("Ethernet bridge {} socket {} bind {} failed",
                                   self.name, sock, self.lport)
            self.socket = socket.socket (socket.AF_INET, socket.SOCK_DGRAM,
                                         socket.IPPROTO_UDP)
            self.socket.setsockopt (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super ().open ()
        
    def close (self):
        super ().close ()
        try:
            self.socket.close ()
        except Exception:
            pass
        self.socket = None

    def run (self):
        sellist = self.sellist
        sock = self.socket
        logging.trace ("Ethernet bridge {} receive thread started", self.name)
        while True:
            # Look for traffic
            try:
                r, w, e = select.select (sellist, [], sellist, 1)
            except select.error:
                e = True
            if self.stopnow or e:
                break
            if r:
                try:
                    msg, addr = sock.recvfrom (1514)
                except socket.error:
                    msg = None
                if not msg or len (msg) <= 4:
                    self.disconnected ()
                    return
                host, port = addr
                good = False
                if port != self.rport or not self.host.valid (host):
                    # Not from peer, ignore
                    continue
                source = (host, port)
                if msg[6] & 1:
                    continue   # source routed???  ignore it
                self.receive (len (msg), msg, source)

    def send_frame (self, buf, skip = None):
        """Send an Ethernet frame.  Ignore any errors, because that's
        the DECnet way.
        """
        if not self.socket:
            return
        try:
            self.socket.sendto (buf, (self.host.addr, self.rport))
        except (IOError, socket.error) as e:
            pass
        
        
# Factory class -- returns an instance of the appropriate _Ethernet
# subclass instance given the specific device flavor specified.
class Ethernet (datalink.Datalink):
    def __new__ (cls, owner, name, config):
        dev = config.device or name
        api, dev = dev.split (":", 1)
        if api == "tap" and _TapEth:
            c = _TapEth
        elif api == "pcap":
            c = _PcapEth
        elif api == "bridge" or api == "udp":
            # External bridge, i.e., IP connection to a bridge in
            # another host or process.  Allow "udp" because that's how
            # SIMH refers to it.
            c = _BridgeEth
        else:
            raise ValueError ("Unknown Ethernet circuit subtype {}".format (api))
        return c (owner, name, dev, config)
