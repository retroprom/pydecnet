#!

"""NSP (End Communications and Session Control layers) for DECnet/Python

"""

from collections import deque

from .common import *
from .routing_packets import ShortData, LongData
from . import logging
from . import events
from . import packet
from . import timers
from . import statemachine
from . import modulo
from . import html
from . import nicepackets

SvnFileRev = "$LastChangedRevision$"

# API exceptions
class NSPException (DNAException): pass
class WrongState (NSPException): "Connection is in the wrong state"
class RangeError (NSPException): "Parameter is out of range"
class ConnectionLimit (NSPException): "Connection limit reached"
class CantSend (NSPException): "Can't send interrupt at this time"
class IntLength (NSPException): "Interrupt message too long"
class UnknownNode (NSPException): "Unknown node name"
    
# Packet parsing exceptions
class NSPDecodeError (packet.DecodeError): pass
class InvalidAck (NSPDecodeError): "ACK fields in error"
class InvalidLS (NSPDecodeError): "Reserved LSFLAGS value"

# NSP packet layouts.  These cover the routing layer payload (or the
# datalink layer payload, in the case of Phase II)

# Sequence numbers are modulo 4096
class Seq (Field, modulo.Mod, mod = 4096):
    """Sequence numbers for NSP -- integers modulo 2^12.  Note that
    creating one of these (e.g., from packet decode) ignores high
    order bits rather than complaining about them.
    """
    def __new__ (cls, val):
        return modulo.Mod.__new__ (cls, val & 0o7777)

    @classmethod
    def decode (cls, buf):
        if len (buf) < 2:
            raise MissingData
        v = int.from_bytes (buf[:2], packet.LE)
        return cls (v), buf[2:]

    def encode (self):
        return self.to_bytes (2, packet.LE)

    def __bytes__ (self):
        return self.encode ()
    
class AckNum (Field):
    """Class for the (usually optional) ACK field in an NSP packet.
    """
    # Values for QUAL:
    ACK = 0
    NAK = 1
    XACK = 2
    XNAK = 3
    _labels = ( "ACK", "NAK", "XACK", "XNAK" )
    def __init__ (self, num = 0, qual = ACK):
        if not 0 <= qual <= 3:
            raise ValueError ("Invalid QUAL value {}".format (qual))
        self.qual = qual
        self.num = Seq (num)

    def __str__ (self):
        return "{} {}".format (self._labels[self.qual], self.num)

    def __eq__ (self, other):
        return self.num == other.num and self.qual == other.qual

    def __ne__ (self, other):
        return not self == other
    
    @classmethod
    def decode (cls, buf):
        if len (buf) >= 2:
            v = int.from_bytes (buf[:2], packet.LE)
            if v & 0x8000:
                # ACK field is present.  Always advance past it.
                buf = buf[2:]
                qual = (v >> 12) & 7
                if 0 <= qual <= 3:
                    # Use the field only if QUAL is valid
                    return cls (v, qual), buf
        return None, buf

    @classmethod
    def checktype (cls, name, val):
        # This allows for the field to be optional, which is
        # represented by an attribute value of None.
        if val is None:
            return val
        return super (__class__, cls).checktype (name, val)
    
    def encode (self):
        return (0x8000 + (self.qual << 12) + self.num).to_bytes (2, packet.LE)
    
    def is_nak (self):
        return self.qual == self.NAK or self.qual == self.XNAK

    def is_cross (self):
        return self.qual == self.XACK or self.qual == self.XNAK
    
    def chan (self, this, other):
        if self.is_cross ():
            return other
        return this

class tolerantI (packet.I):
    # A version of the I (image string) field, but coded to be
    # tolerant of messed up input since some implementation such as
    # Cisco send bad values some of the time.
    @classmethod
    def decode (cls, buf, maxlen):
        if not buf:
            logging.trace ("Missing I field, empty field substituted")
            return cls (b""), b""            
        flen = buf[0]
        if flen > maxlen or flen > len (buf) + 1:
            logging.trace ("Invalid I field, empty field substituted")
            return cls (b""), b""
        return super (__class__, cls).decode (buf, maxlen)
    
# Common header -- just the MSGFLG field, expanded into its subfields.
class NspHdr (packet.Packet):
    _layout = (( packet.BM,
                 ( "mbz", 0, 2 ),
                 ( "type", 2, 2 ),
                 ( "subtype", 4, 3 ),
                 ( "int_ls", 4, 1 ),
                 ( "bom", 5, 1 ),    # if int_ls == 0 (data message)
                 ( "eom", 6, 1 ),    # if int_ls == 0 (data message)
                 ( "int", 5, 1 ),    # if int_ls == 1 (other-data message)
                 ( "mbz2", 7, 1 )), )
    mbz = 0
    mbz2 = 0
    # type codes
    DATA = 0
    ACK = 1
    CTL = 2
    # Ack subtype codes
    ACK_DATA = 0
    ACK_OTHER = 1
    ACK_CONN = 2
    # Control subtype codes
    NOP = 0    # NOP (normally only in Phase II, but spec doesn't restrict it)
    CI = 1
    CC = 2
    DI = 3
    DC = 4
    #NI = 5    # Phase 2 node init (handled in routing, doesn't come to NSP)
    RCI = 6    # Retransmitted CI

class AckHdr (NspHdr):
    """The standard packet beginning for packets that have link addresses
    and acknum fields.  Note that the second ACK field is called "acknum2"
    rather than "ackoth" or "ackdat" since those names don't make sense if
    we use this header interchangeably for all packet layouts.  And while
    it is typical to use the first field for "this subchannel" and the 
    second for "the other subchannel", that isn't required.
    """
    _layout = (( packet.B, "dstaddr", 2 ),
               ( packet.B, "srcaddr", 2 ),
               ( AckNum, "acknum" ),
               ( AckNum, "acknum2" ))

    def check (self):
        # Check that the two acknum fields (if both are supplied) point
        # to different subchannels
        if self.acknum and self.acknum2 and \
           self.acknum.is_cross () == self.acknum2.is_cross ():
            logging.debug ("Both acknums refer to the same subchannel")
            raise InvalidAck ("Both acknums refer to the same subchannel")

# Note on classes for packet layouts:
#
# It is tempting at times to make packet type x a subclass of packet
# type y, when x looks just like y but with some extra stuff, or with
# a change in type code only.  IntMsg vs. LinkSvcMsg are an example
# of the former, AckData vs. AckOther or DataSeg vs. IntMsg an example
# of the latter.  This is typically not a good idea, because "isinstance"
# will match an object of a subclass of the supplied class.  To keep
# the actual message classes distinct, in the hierarchy below they are
# almost always derived from a base class that is not in itself an
# actual message class.  However, we can reuse some of the methods of
# other classes (those that we didn't want to use as base class) without
# causing trouble -- consider AckData.check for example.

class AckData (AckHdr):
    type = NspHdr.ACK
    subtype = NspHdr.ACK_DATA

    def check (self):
        AckHdr.check (self)
        if self.acknum is None:
            logging.debug ("acknum field missing")
            raise InvalidAck ("acknum field missing")
        
class AckOther (AckHdr):
    type = NspHdr.ACK
    subtype = NspHdr.ACK_OTHER

    check = AckData.check
        
class AckConn (NspHdr):
    # A Conn Ack doesn't have payload, but VAXELN sends extraneous
    # bytes at the end and pretending that's payload will suppress a
    # parse error.
    _layout = (( packet.B, "dstaddr", 2 ),
               packet.Payload)
    type = NspHdr.ACK
    subtype = NspHdr.ACK_CONN

    def __init__ (self, *args, **kwargs):
        super ().__init__ (*args, **kwargs)
        self.payload = b""
        
class DataSeg (AckHdr):
    _layout = (( packet.BM,
                 ( "segnum", 0, 12, Seq ),
                 ( "dly", 12, 1 )),
                packet.Payload)
    type = NspHdr.DATA
    int_ls = 0
    
class IntMsg (AckHdr):
    _layout = (( Seq, "segnum" ),
               packet.Payload)
    type = NspHdr.DATA
    subtype = 3
    int_ls = 1
    int = 1

# Link Service message also uses the interrupt subchannel.
class LinkSvcMsg (AckHdr):
    _layout = (( Seq, "segnum" ),
               ( packet.BM,
                 ( "fcmod", 0, 2 ),
                 ( "fcval_int", 2, 2 )),
               ( packet.SIGNED, "fcval", 1 ))
    type = NspHdr.DATA
    subtype = 1
    int_ls = 1
    int = 0
    # fcval_int values:
    DATA_REQ = 0
    INT_REQ = 1
    # fcmod values:
    NO_CHANGE = 0
    XOFF = 1
    XON = 2

    def check (self):
        if self.fcval_int > 1 or self.fcmod == 3:
            logging.debug ("Reserved LSFLAGS value")
            raise InvalidLS

# Control messages.  5 (Node init) is handled in route_ptp since it is
# a datalink dependent routing layer message.  0 (NOP) is here,
# however.

# Common parts of CI, RCI, and CC
class ConnMsg (NspHdr):
    _layout = (( packet.B, "dstaddr", 2 ),
               ( packet.B, "srcaddr", 2 ),
               ( packet.BM,
                 ( "mb1", 0, 2 ),
                 ( "fcopt", 2, 2 ),
                 ( "mbz", 4, 4 )),
               ( packet.EX, "info", 1 ),
               ( packet.B, "segsize", 2 ))
    type = NspHdr.CTL
    mb1 = 1
    mbz = 0
    # Services:
    SVC_NONE = 0
    SVC_SEG = 1         # Segment flow control
    SVC_MSG = 2         # Message flow control
    # Info:
    VER_PH3 = 0         # Phase 3 (NSP 3.2)
    VER_PH2 = 1         # Phase 2 (NSP 3.1)
    VER_PH4 = 2         # Phase 4 (NSP 4.0)
    VER_41 = 3          # Phase 4+ (NSP 4.1)

nspverstrings = ( "3.2", "3.1", "4.0", "4.1" )
nspver3 = ( (3, 2, 0), (3, 1, 0), (4, 0, 0), (4, 1, 0))
nspphase = { ConnMsg.VER_PH2 : 2, ConnMsg.VER_PH3 : 3,
             ConnMsg.VER_PH4 : 4, ConnMsg.VER_41 : 4  }

# This is either Connect Initiate or Retransmitted Connect Initiate
# depending on the subtype value.
class ConnInit (ConnMsg):
    _layout = (packet.Payload,)
    #subtype = NspHdr.CI
    #subtype = NspHdr.RCI
    dstaddr = 0
    
# Connect Confirm is very similar to Connect Init (the differences are
# mainly in the session layer, which is just payload to us).
# However, the srcaddr is now non-zero.
class ConnConf (ConnMsg):
    _layout = (( tolerantI, "data_ctl", 16 ),)    # CC payload is an I field
    subtype = NspHdr.CC

class DiscConf (NspHdr):
    _layout = (( packet.B, "dstaddr", 2 ),
               ( packet.B, "srcaddr", 2 ),
               ( packet.B, "reason", 2 ))
    type = NspHdr.CTL
    subtype = NspHdr.DC
    # Supply a dummy value in the object to allow common handling with
    # DiscInit, which does include a disconnect data field.
    data_ctl = b""

# Three reason codes are treated as specific packets in the NSP spec;
# all others are in effect synonyms for disconnect initiate for Phase II
# compatibility.  Define subclasses for the three here so we can use
# those later.
class NoRes (DiscConf):
    reason = 1

class DiscComp (DiscConf):
    reason = 42

class NoLink (DiscConf):
    reason = 41

# DI is like DC but it adds session control disconnect data
class DiscInit (NspHdr):
    _layout = (( packet.B, "dstaddr", 2 ),
               ( packet.B, "srcaddr", 2 ),
               ( packet.B, "reason", 2 ),
               ( tolerantI, "data_ctl", 16 ))
    type = NspHdr.CTL
    subtype = NspHdr.DI
    
OBJ_FAIL = 38       # Object failed (copied from session.py)
UNREACH = 39        # Destination unreachable (copied from session.py)

# Mapping from packet type code (msgflg field) to packet class
msgmap = { (c.type << 2) + (c.subtype << 4) : c
           for c in ( AckData, AckOther, AckConn, ConnConf,
                      DiscConf, DiscInit, IntMsg, LinkSvcMsg ) }
# Put in Connect Init with its two msgflag values
msgmap[(NspHdr.CTL << 2) + (NspHdr.CI << 4)] = ConnInit
msgmap[(NspHdr.CTL << 2) + (NspHdr.RCI << 4)] = ConnInit
# For data segments we put in all 4 combinations of bom/eom flags so
# we can just do the message map without having to check for those cases
# separately.
for st in range (4):
    msgmap[NspHdr.DATA + (st << 5)] = DataSeg
# Put in an "ignore me" entry for NOP packets
msgmap[(NspHdr.CTL << 2) + (NspHdr.NOP << 4)] = None

# Mapping from reason to specific Disconnect Confirm subclass
dcmap = { c.reason : c for c in ( NoRes, DiscComp, NoLink ) }

class NspCounters (BaseCounters):
    nodecounters = [
        ( "time_since_zeroed", "Time since counters zeroed" ),
        ( "byt_rcv", "User bytes received" ),
        ( "byt_xmt", "User bytes sent" ),
        ( "msg_rcv", "User messages received" ),
        ( "msg_xmt", "User messages sent" ),
        ( "t_byt_rcv", "Total bytes received" ),
        ( "t_byt_xmt", "Total bytes sent" ),
        ( "t_msg_rcv", "Total messages received" ),
        ( "t_msg_xmt", "Total messages sent" ),
        ( "con_rcv", "Connects received" ),
        ( "con_xmt", "Connects sent" ),
        ( "timeout", "Response timeouts" ),
        ( "no_res_rcv", "Received connect resource errors" )
    ]

    def __init__ (self, owner):
        super ().__init__ (owner)
        self.byt_rcv = 0
        self.byt_xmt = 0
        self.msg_rcv = 0
        self.msg_xmt = 0
        self.t_byt_rcv = 0
        self.t_byt_xmt = 0
        self.t_msg_rcv = 0
        self.t_msg_xmt = 0
        self.con_rcv = 0
        self.con_xmt = 0
        self.timeout = 0
        self.no_res_rcv = 0

class NSPNode (object):
    """The remote node state needed by NSP.  This is a base class of
    the Nodeinfo object, which is what node.nodeinfo () returns.
    """
    # Allow a subclass to change what counters this node has.
    counterclass = NspCounters
    
    def __init__ (self):
        # NSP specific node state -- see NSP 4.0.1 spec, table 6.
        self.delay = 0
        self.counters = self.counterclass (self)

    def used (self):
        # Returns True if this node has been used. 
        return self.counters.t_byt_rcv or self.counters.t_byt_xmt or \
          self.counters.con_rcv or self.counters.con_xmt

    def get_api (self):
        ret = dict ()
        # Supply counts, but only if we have some
        if self.used ():
            for f, lb in self.counters.nodecounters:
                ret[f] = getattr (self.counters, f)
            ret["delay"] = self.delay
        return ret
    
# Packet types that trigger No Link response if not mapped to a connection
nolinkset = { ConnConf, DiscInit, DataSeg, IntMsg, LinkSvcMsg }

# These reason codes are internal to NSP and not available to SC
reservedreasons = { NoRes.reason, DiscComp.reason, NoLink.reason }

def shortname (n):
    if isinstance (n, Nodeid):
        return Nodeid (n)
    return n.name

def skey (n):
    return not isinstance (n[0], Nodeid), n

class NSP (Element):
    """The NSP Entity.  This owns all the connections.  It implements
    the ECL (formerly NSP) layer of the DECnet Network Architecture.
    """
    def __init__ (self, parent, config):
        super ().__init__ (parent)
        logging.debug ("Initializing NSP")
        # Dictionary of connections indexed by local connection address
        self.connections = EntityDict ()
        # Ditto but indexed by node ID and remote connection address.
        self.rconnections = EntityDict ()
        self.config = config = config.nsp
        self.maxconns = config.max_connections
        # Fixed for now
        self.inact_time = 300
        self.conn_timeout = 30
        #
        self.ectr = parent.routing.nodeinfo.counters
        self.init_id ()
        # Create the "reserved port"
        self.resport = ReservedPort (self)
        # Figure out what NSP version code we will send in CI/CC messages
        self.nspver = (ConnMsg.VER_PH2,
                       ConnMsg.VER_PH3,
                       ConnMsg.VER_PH4)[self.node.phase - 2]
        
    def start (self):
        logging.debug ("Starting NSP")
        self.routing = self.parent.routing

    def stop (self):
        logging.debug ("Stopping NSP")

    def get_api (self):
        return { "version" : nspverstrings[self.nspver],
                 "max_connections" : self.maxconns,
                 "connections" : self.connections.get_api () }

    def connect (self, dest, payload):
        """Session control request for an outbound connection.  Returns
        the connection address if the request was accepted.
        """
        c = Connection (self, outbound = (dest, payload))
        return c
    
    def dispatch (self, item):
        if isinstance (item, Received):
            # Arriving packet delivered up from Routing.  Map the packet
            # to a port (Connection object), see NSP 4.0.1 spec
            # section 6.2 (receive dispatcher)
            buf = item.packet
            # If this packet was looped back from one sent by this
            # NSP, it will be in the form of a Packet object, not a
            # buffer, so trying to parse it will bring pain.  If so,
            # just turn it into bytes so the common code works.
            buf = makebytes (buf)
            msgflg = buf[0]
            try:
                t = msgmap[msgflg]
            except KeyError:
                # TYPE or SUBTYPE invalid, or MSGFLG is extended (step 1)
                logging.trace ("Ill formatted NSP packet received from {}: {}",
                               item.src, item.packet)
                logging.trace ("Unrecognized msgflg value {}, ignored", msgflg)
                # FIXME: this needs to log the message in the right format
                self.node.logevent (events.inv_msg, message = buf,
                                    source_node = item.src)
                return
            if not t:
                # NOP message to be ignored, do so.
                if logging.tracing:
                    logging.trace ("NSP NOP packet received from {}: {}",
                                   item.src, item.packet)
                return
            try:
                pkt = t (buf)
            except packet.DecodeError:
                logging.debug ("Invalid packet {}", buf)
                # Ignore it
                return
            if t is DiscConf:
                # Do a further lookup on disconnect confirm reason code
                # (step 5)
                try:
                    t = dcmap[pkt.reason]
                except KeyError:
                    # Other Disconnect Confirm, that's Phase II stuff.
                    # Parse it as a generic DiscConf packet
                    pass
                pkt = t (buf)
            if logging.tracing:
                logging.trace ("NSP packet received from {}: {}", item.src, pkt)
            if t is ConnInit:
                # Step 4: if this is a returned CI, find the connection
                # that sent it.
                # Note that the error case of CI with non-zero dest addr
                # is caught by the general DecodeError handling above.
                if item.rts:
                    try:
                        conn = self.connections[pkt.srcaddr]
                        if conn.state != conn.ci:
                            # Unexpected RTS, ignore
                            return
                    except KeyError:
                        # Not there, must have been deleted.  Ignore.
                        logging.trace ("Returned CI not matched, ignored")
                        return
                else:
                    # Step 3: see if this is a retransmit, otherwise
                    # map it onto a new Connection if available.
                    cikey = (item.src, pkt.srcaddr)
                    if cikey in self.rconnections:
                        conn = self.rconnections[cikey]
                        if conn.state not in (conn.cr, conn.cc):
                            # Unexpected in this state, discard
                            return
                    else:
                        # Set the parsed packet into the work item
                        item.packet = pkt
                        try:
                            conn = Connection (self, inbound = item)
                            # All done (constructor did all the work)
                            return
                        except Exception:
                            # Can't create another connection, give it
                            # to the reserved port for a No Resources reply.
                            logging.debug ("Can't allocate connection for CI",
                                           exc_info = True)
                            conn = self.resport
            else:
                # Step 6 or 7: look up via the local link address.
                conn = self.connections.get (pkt.dstaddr, None)

                # Do all the port mapping checks.  The NSP spec
                # (section 6.2) lists them in a manner that doesn't
                # directly match the flow here, because here we start
                # with a lookup based only on the dstaddr (our
                # address) field.
                if conn:
                    if conn.state in (conn.ci, conn.cd):
                        # CI or CD state, check the message (rule 6 first
                        # part, rule 7 note).
                        if t in (NoRes, ConnConf, DiscInit, DiscConf):
                            if conn.dstaddr == 0:
                                # Dest address not set yet
                                cikey = (item.src, pkt.srcaddr)
                                self.rconnections[cikey] = conn
                                conn.dstaddr = pkt.srcaddr
                        elif t is not AckConn:
                            conn = None   # Not a valid mapping
                    else:
                        # We have a remote address, do a full check
                        # (rule 6 second part, rule 7)
                        if t is AckConn:
                            # Conn Ack only maps to a connection in CI state
                            conn = None
                    if conn:
                        # We still think we have a connection mapping,
                        # do the source address check.
                        if item.src != conn.dest and \
                           isinstance (conn.dest, Nodeid):
                            # Node address mismatch.  Note we don't
                            # check this for loop nodes, where the
                            # destination "address" is a circuit
                            # rather than a Nodeid.
                            conn = None
                        elif t is not AckConn and pkt.srcaddr != conn.dstaddr:
                            # Mismatch, map to reserved port or discard
                            conn = None
                # No valid connection mapping found, send message to the
                # reserved port or discard, according to message type.
                if not conn:
                    if t in (AckConn, NoRes, DiscConf, DiscComp, NoLink,
                             AckData, AckOther):
                        # discard the packet silently
                        logging.trace ("Packet with bad address discarded: {}", pkt)
                        return
                    # Something that needs a reply, map to reserved port
                    conn = self.resport
            # Packet is mapped to a port, so process it there.  Change
            # the packet attribute in the work item to be the parsed
            # packet from the logic above.
            # Adjust the total counters
            if conn is not self.resport:
                nc = conn.destnode.counters
                nc.t_byt_rcv += len (pkt.decoded_from)
                nc.t_msg_rcv += 1
            item.packet = pkt
            conn.dispatch (item)
            
    def init_id (self):
        # Initialize the free connection ID list.  The algorithm used
        # meets the requirements of the Phase II NSP spec for the case
        # where the node talks to an intercept node.
        #
        # This requires:
        # 1. The low order bits of the ID must be unique
        # 2. The low order bits must not be zero
        # 3. Previously used IDs must not be reused for as long as possible.
        #
        # The solution is to use a circular list (deque) where IDs are
        # taken from one end and put back in the other.  The list is
        # initialized with max-connections entries, each with a
        # different non-zero low order value (for example, if
        # max-connections is 511, "low order" means the bottom 9 bits).
        # Each entry has a random value in the high order bits.  When an
        # ID is freed, the high order part is incremented.
        c = self.maxconns + 1
        fc = [ i + random.randrange (0, 65536, c) for i in range (1, c) ]
        random.shuffle (fc)
        self.freeconns = deque (fc)
        
    def get_id (self):
        if not self.freeconns:
            return None
        return self.freeconns.popleft ()

    def ret_id (self, i):
        i = (i + self.maxconns + 1) & 0xffff
        self.freeconns.append (i)
    
    def http_get (self, mobile, parts, qs):
        infos = ( "summary", "status", "counters", "characteristics" )
        if not parts or parts == ['']:
            what = "summary"
        elif parts[0] in infos:
            what = parts[0]
        else:
            return None, None
        active = infos.index (what) + 1
        sb = html.sbelement (html.sblabel ("Information"),
                             html.sbbutton (mobile, "nsp", "Summary", qs),
                             html.sbbutton (mobile, "nsp/status", "Status", qs),
                             html.sbbutton (mobile, "nsp/counters",
                                            "Counters", qs),
                             html.sbbutton (mobile, "nsp/characteristics",
                                            "Characteristics", qs))
        sb.contents[active].__class__ = html.sbbutton_active
        ret = self.html (what)
        return sb, html.main (*ret)

    def html (self, what):
        title = "NSP {1} for node {0.nodeid} ({0.name})".format (self.parent.routing, what)
        if what == "summary":
            body = [ "Version: {}".format (nspverstrings[self.nspver]),
                     "Current connections: {}".format (len (self.connections)),
                     "Peak connections: {}".format (self.ectr.peak_conns),
                     "Max connections: {}".format (self.maxconns) ]
            return [ html.firsttextsection (title, body) ]
        if what == "characteristics":
            echar = [ "Version: {}".format (nspverstrings[self.nspver]),
                      "Max connections: {}".format (self.maxconns),
                      "NSP weight: {}".format (self.config.nsp_weight),
                      "NSP delay: {:.2f}".format (self.config.nsp_delay),
                      "Queue limit: {}".format (self.config.qmax),
                      "Max retransmits : {}".format (self.config.retransmits) ]
            ret = [ html.firsttextsection (title, echar) ]
            objects = self.parent.session.html_objects ()
            ret.append (objects)
            lpnodes = sorted ([ n.nodename, n.circuit.name ]
                              for n in self.node.nodeinfo_byid.values ()
                              if n.loopnode)
            hdr = [ "Node name", "Circuit" ]
            ret.append (html.tbsection ("Loop nodes", hdr, lpnodes))
            nodes = sorted ((Nodeid (k),
                             html.cell (n.nodename, 'class="double_right"'))
                            for k, n in self.node.nodeinfo_byid.items ()
                            if n.nodename and not n.loopnode)
            nl = len (nodes)
            if nl < 20:
                cols = 1
            elif nl < 40:
                cols = 2
            else:
                cols = 3
            skip = (nl + cols - 1) // cols
            nodes3 = list ()
            for i in range (skip):
                row = list ()
                for s in range (cols):
                    i2 = i + s * skip
                    if i2 < nl:
                        row.extend (nodes[i2])
                row[-1].markup = ""
                nodes3.append (row)
            hdr = [ "Node ID",
                    html.hcell ("Node name", 'class="double_right"') ] * cols
            hdr[-1] = html.hcell ("Node name")
            ret.append (html.tbsection ("Node database", hdr, nodes3))
            return ret
        if what == "status":
            estat = [ "Version: {}".format (nspverstrings[self.nspver]),
                      "Current connections: {}".format (len (self.connections)),
                      "Peak connections: {}".format (self.ectr.peak_conns),
                      "Max connections: {}".format (self.maxconns) ]
            ret = [ html.firsttextsection (title, estat) ]
            # Get the list of active nodes (those with traffic)
            anodes = [ (shortname (k), n.nodename, "{:.1f}".format (n.delay))
                       for k, n in self.node.nodeinfo_byid.items ()
                       if n.used () ]
            anodes.sort (key = skey)
            hdr = ( "Node ID", "Node name", "Delay" )
            if anodes:
                ret.append (html.tbsection ("Node status", hdr, anodes))
            else:
                ret.append (html.textsection ("Node status",
                                              [ "<em>No active nodes</em>" ]))
            conns = self.html_conns (what)
            ret.append (conns)
            return ret
        if what == "counters":
            estat = [ "Version: {}".format (nspverstrings[self.nspver]),
                      "Current connections: {}".format (len (self.connections)),
                      "Peak connections: {}".format (self.ectr.peak_conns),
                      "Max connections: {}".format (self.maxconns) ]
            ret = [ html.firsttextsection (title, estat) ]
            # Get the executor counters
            ctr = [ ( "{} = ".format (lb), getattr (self.ectr, fn))
                    for fn, lb in self.ectr.nodecounters
                    if fn not in self.ectr.exclude ]
            ret.append (html.section ("Executor counters",
                                      html.dtable (ctr)))
            # Get the list of active nodes (those with traffic)
            anodes = list ()
            for k, n in self.node.nodeinfo_byid.items ():
                if not n.used ():
                    continue
                nc = n.counters
                if nc is self.ectr:
                    continue
                ctr = [ ( "{} = ".format (lb), getattr (nc, fn))
                        for fn, lb in nc.nodecounters ]
                anodes.append ([ shortname (k), n.nodename, ctr ])
            anodes.sort (key = skey)
            hdr = ( "Node ID", "Node name" )
            if anodes:
                ret.append (html.detail_section ("Node counters", hdr, anodes))
            else:
                ret.append (html.textsection ("Node counters",
                                              [ "<em>No active nodes</em>" ]))
            # Note that currently there are no connection counters;
            # traffic on connections is counted in nodes.
            return ret
        # Should not get here...
        return [ "not yet implemented" ]

    def html_conns (self, what):
        # Return an HTML item for the current connections.  For the
        # moment this only applies to status (there are no connection
        # counters).
        title = "Logical links (connections)"
        ret = list ()
        sc = self.parent.session
        if what == "status":
            hdr = ("LLA", "State", "Object", "Node", "RLA", "Remote object")
            for k, c in sorted (self.connections.items ()):
                ret.append ((k, c.state.__name__, sc.html_localuser (c),
                             c.destnode, c.dstaddr, sc.html_remuser (c)))
        if ret:
            return html.tbsection (title, hdr, ret)
        else:
            return html.textsection (title, [ "<em>No connections</em>" ])

    def read_node (self, req, nodeinfo, resp, links = None):
        # Fill in a NICE read node response record with information
        # from nodeinfo, inserting it into resp.  Note that asking for
        # it causes the entry to be created.
        r = resp[nodeinfo]
        r.entity = nicepackets.NodeEntity (nodeinfo)
        # We have a node for which we have some information.  Check
        # the information type request to see what is wanted.
        if req.sumstat ():
            # summary or status
            # Count the connections to this node.  TODO: should this
            # be tracked as state in the NSPNode object?
            if links is None:
                links = 0
                for c in self.connections.values ():
                    if c.destnode == nodeinfo:
                        links += 1
            if links:
                r.active_links = links
            if nodeinfo.delay != 0:
                r.delay = int (nodeinfo.delay) or 1
        elif req.char ():
            # characteristics.  Nothing except for executor or loop
            if nodeinfo.loopnode:
                r.circuit = nodeinfo.circuit.name
            if nodeinfo == self.node.routing.nodeinfo:
                # It's the executor
                r.ecl_version = nspver3[self.nspver]
                r.maximum_links = self.maxconns
                r.delay_factor = int (self.config.nsp_delay * 16)
                r.delay_weight = self.config.nsp_weight
                r.inactivity_timer = self.inact_time
                r.retransmit_factor = self.config.retransmits
                r.incoming_timer = self.conn_timeout
                r.outgoing_timer = self.conn_timeout
        else:
            # counters
            nodeinfo.counters.copy (r)
        
    def nice_read (self, req, resp):
        # We only know about nodes
        if not isinstance (req, nicepackets.NiceReadNode):
            return
        # We know nothing about adjacent nodes
        if req.adj ():
            return
        if req.mult ():
            # Multiple nodes: walk the node table
            for n in self.node.nodeinfo_byid.values ():
                # Add it to the response either if we're doing
                # "known", or the executor, or "active" and there is a
                # link, or "significant" and we have any information.
                if req.known () or \
                   (n == self.node.routing.nodeinfo and not req.loop ()):
                    self.read_node (req, n, resp)
                elif req.loop ():
                    # Loop nodes
                    if n.loopnode:
                        self.read_node (req, n, resp)
                else:
                    # significant or active.  See if we want to
                    # include this.
                    if req.sig () and \
                       ((req.counters () and n.used ()) or \
                        (req.sumstat () and n.delay != 0)):
                        self.read_node (req, n, resp)
                    else:
                        # Active
                        l = 0
                        for c in self.connections.values ():
                            if c.destnode == n:
                                l += 1
                        if l:
                            self.read_node (req, n, resp, l)
        else:
            # Specific node by name or ID.  Name was converted to
            # Nodeinfo entry in node.py
            try:
                n = self.node.nodeinfo_byid[req.entity.value]
            except KeyError:
                try:
                    print ("looking for", req.entity.value.nodename)
                    n = self.node.nodeinfo_byname[req.entity.value.nodename]
                except KeyError:
                    print ("no node", req.entity.value)
                    return
            self.read_node (req, n, resp)

class txqentry (timers.Timer):
    """An entry in the retransmit queue for a subchannel.

    Each entry has a timer, which is the retransmit timer for that
    particular packet.
    """
    __slots__ = ("packet", "txtime", "channel", "tries",
                 "msgnum", "segnum", "sent")
    
    def __init__ (self, packet, channel, segnum = 0, msgnum = 0):
        super ().__init__ ()
        self.packet = packet
        self.channel = channel
        self.tries = self.txtime = 0
        self.sent = False
        self.segnum = segnum
        self.msgnum = msgnum

    def send (self):
        pkt = self.packet
        if isinstance (pkt, ConnInit):
            if self.tries:
                pkt.subtype = NspHdr.RCI
            else:
                pkt.subtype = NspHdr.CI
        elif isinstance (pkt, AckHdr):
            self.channel.set_acks (pkt)
        # TODO: Skip this if phase 2 local node?
        self.channel.node.timers.start (self, self.channel.parent.acktimeout ())
        self.tries += 1
        if self.txtime == 0:
            self.txtime = time.time ()
        self.channel.parent.sendmsg (self.packet)
        self.sent = True

    def ack (self):
        """Handle acknowledgment of packet.  Also used when the packet
        is not going to be transmitted again for some other reason
        (like connection abort).
        """
        self.channel.node.timers.stop (self)
        if self.txtime:
            self.channel.parent.update_delay (self.txtime)

    def dispatch (self, item):
        """Handle timeout for the packet.
        """
        # Count a timeout
        c = self.channel.parent
        c.destnode.counters.timeout += 1
        # See if too many tries.  It's 1 after the first try,
        # incremented in the send operation, so check is >= not >.
        if self.tries >= c.parent.config.retransmits:
            # Limit exceeded, stop retransmitting.  If we're dealing
            # with a Connect Initiate, that's all we do.  For other
            # packets, we disconnect.  The connection is simply closed
            # because it doesn't seem we're getting across to the
            # other end.  The reason disconnect isn't done for CI is
            # that the remote might be a Phase II node, which doesn't
            # send Connect Ack.
            logging.trace ("Retransmit limit on {}", self.packet)
            if isinstance (self.packet, ConnInit):
                # Stop the timer for this packet
                self.channel.node.timers.stop (self)
            else:
                # Not CI, so close due to "destination unreachable"
                disc = DiscInit (reason = UNREACH, data_ctl = b"")
                c.to_sc (Received (self, packet = disc), False)
                c.close ()
                # Mark connection as closed
                c.set_state (c.closed)
            return
        self.sent = False
        # Don't send just yet if flow control forbids it
        if not isinstance (self.packet, DataSeg) or \
          self.channel.flow_ok (self):
            self.send ()
        
class Subchannel (Element, timers.Timer):
    """A subchannel (data or other-data) within an NSP connection.  This
    is where we keep the per-subchannel state: queues, flow control
    parameters, sequence numbers, etc.

    The timer base class is for the ack holdoff timer.  Packet timeout
    is handled on a per-packet basis, by the txqentry class.
    """
    # Holdoff delay
    HOLDOFF = 0.1
    
    def __init__ (self, parent):
        Element.__init__ (self, parent)
        timers.Timer.__init__ (self)
        self.pending_ack = deque ()   # List of pending txqentry items
        self.nextseg = 1              # Next segment number
        self.nextmsg = 1              # Next message number
        self.maxseg = 0               # Max segment number allowed to be sent
        self.maxmsg = 0               # Max message number allowed to be sent
        self.maxseqsent = Seq (0)     # Highest sequence number actually sent
        self.maxackseg = 0            # Highest segment number acked
        self.acknum = Seq (0)         # Outbound ack number
        self.ackpending = False       # No deferred ack
        # The flow control parameters are remote flow control -- we don't
        # do local flow control other than to request another interrupt
        # each time we get one.  So there are no explicit local flow
        # attributes.
        self.xon = True               # Flow on/off switch
        self.flow = ConnMsg.SVC_NONE  # Outbound flow control selected
        self.ooo = dict ()            # Pending received out of order packets

    def dispatch (self, item):
        if isinstance (item, timers.Timeout):
            # Send an explicit ack
            self.send_ack ()
        elif isinstance (item, Received):
            # A packet for this subchannel.
            pkt = item.packet
            # Process any ack number fields -- this is done before we
            # look at the sequence number.
            self.process_ack (pkt.acknum)
            self.process_ack (pkt.acknum2)
            if isinstance (pkt, (AckData, AckOther)):
                # Explicit ACK message, so no data, we're done
                return
            # Check the sequence number against the next expected value.
            num = pkt.segnum
            if num <= self.acknum:
                # Duplicate, send an explicit ack, but otherwise ignore it.
                self.send_ack ()
                return
            elif num != self.acknum + 1:
                # Not next in sequence, save it
                if logging.tracing:
                    logging.trace ("Saving out of order NSP packet {}", pkt)
                self.ooo[num] = item
                return
            # It's in sequence.  Process it, as well as packets waiting
            # in the out of order cache that are now in order.
            while item:
                self.acknum = num
                self.ackpending = True
                self.process_data (item)
                num += 1
                # Remove the packet with the next higher sequence number
                # from the OOO cache, if it is there, and keep going if
                # so.
                item = self.ooo.pop (num, None)
            # Done with in-sequence packets, start the ACK holdoff timer
            # if it isn't already running.
            if self.ackpending and not self.islinked ():
                # ACK holdoff timer is not yet running, start it
                self.node.timers.start (self, self.HOLDOFF)

    def send_ack (self):
        self.node.timers.stop (self)
        ack = self.parent.makepacket (self.Ack)
        self.set_acks (ack, True)
        self.parent.sendmsg (ack)

    def set_acks (self, pkt, explicit = False):
        if explicit or self.ackpending:
            self.ackpending = False
            self.node.timers.stop (self)
            pkt.acknum = AckNum (self.acknum)
        if self.parent.cphase == 4:
            # Phase IV, we can use cross-subchannel ACK.
            other = self.cross
            if other.ackpending:
                other.ackpending = False
                self.node.timers.stop (other)
                pkt.acknum2 = AckNum (other.acknum, AckNum.XACK)
        
    def process_ack (self, num):
        if num is not None:
            if num.is_cross ():
                if self.parent.cphase < 4:
                    logging.debug ("Cross-subchannel ACK/NAK but phase is {}",
                                   self.parent.cphase)
                self.cross.ack (num.num)
            else:
                self.ack (num.num)

    def ack (self, acknum):
        """Handle a received ack on this subchannel.
        """
        try:
            firsttxq = self.pending_ack[0]
        except IndexError:
            return
        if isinstance (firsttxq.packet, (IntMsg, DataSeg)):
            if acknum < firsttxq.packet.segnum or acknum > self.maxseqsent:
                # Duplicate or out of range ack, ignore.
                # Note that various control packets end up in the Data
                # subchannel as well, and those don't have sequence numbers.
                logging.trace ("Ignoring ack, first {} last {}, got {}",
                               firsttxq.packet.segnum, self.maxseqsent, acknum)
                return
            count = acknum - firsttxq.packet.segnum + 1
        else:
            count = 1
        acked = None
        for i in range (count):
            acked = self.pending_ack.popleft ()
            acked.ack ()
            self.maxackseg = acked.segnum
            
    def close (self):
        """Handle connection close actions for this subchannel.  This is
        also used for connection abort, to discard all pending packets and
        stop timers.
        """
        self.node.timers.stop (self)
        for pkt in self.pending_ack:
            pkt.ack ()
        self.pending_ack.clear ()
        self.ooo.clear ()

class Data_Subchannel (Subchannel):
    # Class for ACKs send from this subchannel
    Ack = AckData
    name = "data"

    def __init__ (self, parent):
        super ().__init__ (parent)
        self.qmax = parent.parent.config.qmax
        
    def process_data (self, item):
        """Process a data packet that is next in sequence.
        """
        self.parent.to_sc (item)

    def process_ack (self, num):
        super ().process_ack (num)
        # Some transmits may have been blocked that are now ok, try
        # again.
        self.send_blocked ()
        if self.parent.shutdown and not self.pending_ack:
            self.parent.disc_rej (*self.parent.pending_disc)
            self.parent.set_state (self.parent.di)
            
    def flow_ok (self, qe):
        """Return True if this queue entry can be transmitted now, False
        if not, according to the current flow control state.

        The rule is: this packet can be sent if:
        1. In flight packet count is <= maxq parameter (20 by default), and
        2. Flow is on (xon/xoff state is "xon"), and
        3. One of:
        a. No flow control, or
        b. segment flow ctl, and this segment <= max allowed segment, or
        c. message flow ctl, and this message <= max allowed message

        TODO: add congestion control per DEC-TR-353 (Raj Jain)
        """
        if self.pending_ack:
            maxq = self.pending_ack[0].segnum + self.qmax - 1
            if qe.segnum > maxq:
                return False
        else:
            maxq = self.qmax - 1
        return self.xon and qe.segnum <= maxq and \
               (self.flow == ConnMsg.SVC_NONE or
                (self.flow == ConnMsg.SVC_SEG and qe.segnum <= self.maxseg) or
                (self.flow == ConnMsg.SVC_MSG and qe.msgnum <= self.maxmsg))
                 
    def send (self, pkt):
        """Queue a packet for transmission, and try to send it.

        Note that the data subchannel is used not just for data
        segments, but also for control packets that are retransmitted:
        Connect Init, Connect Confirm, Disconnect Init.
        """
        if isinstance (pkt, DataSeg):
            pkt.segnum = Seq (self.nextseg % Seq.modulus)
            qe = txqentry (pkt, self, self.nextseg, self.nextmsg)
            self.nextseg += 1
            if pkt.eom:
                self.nextmsg += 1
        else:
            qe = txqentry (pkt, self)
        self.pending_ack.append (qe)
        if self.send_qe (qe) and isinstance (pkt, DataSeg):
            self.maxseqsent = max (self.maxseqsent, pkt.segnum)

    def send_qe (self, qe):
        """Attempt to send an item that has previously been put on the
        transmit queue.  Return True if it was sent, False if it cannot
        be sent right now due to flow control or too many unacknowledged
        segments.
        """
        if isinstance (qe.packet, DataSeg):
            # For data segments, check if we can transmit now.  If not,
            # just leave it queued; it will be transmitted when flow
            # control permits.
            if not self.flow_ok (qe):
                # Not allowed to send.
                return False
        # Good to go; send it and start the timeout.
        qe.send ()
        return True
    
    def process_ls (self, pkt):
        """Process a link service packet that updates the data
        subchannel, i.e., a "Data request" packet in the NSP spec
        terminology.
        """
        if self.flow == ConnMsg.SVC_MSG:
            delta = pkt.fcval
            if delta >= 0 and self.maxmsg + delta < self.maxackseg + 128:
                self.maxmsg += delta
            else:
                logging.debug ("Invalid LS (Data Request, message mode) message {}", pkt)
                return
        elif self.flow == ConnMsg.SVC_SEG:
            delta = pkt.fcval
            if self.maxackseg <= self.maxseg + delta < self.maxackseg + 128:
                self.maxseg += delta
            else:
                logging.debug ("Invalid LS (Data Request, segment mode) message {}", pkt)
                return
        if pkt.fcmod:
            self.xon = pkt.fcmod == pkt.XON
        self.send_blocked ()

    def send_blocked (self):
        """Look for not-sent packets in the transmit queue, and retry
        sending them.  Quit when one is refused again.
        """
        for qe in self.pending_ack:
            if not qe.sent:
                if not self.send_qe (qe):
                    break
            if isinstance (qe.packet, DataSeg):
                self.maxseqsent = max (self.maxseqsent, qe.packet.segnum)
        
class Other_Subchannel (Subchannel):
    # Class for ACKs send from this subchannel
    Ack = AckOther
    name = "interrupt"
    
    def __init__ (self, parent):
        super ().__init__ (parent)
        self.seqnum = Seq (1)         # Next transmitted sequence number
        self.maxmsg = 1               # Allowed to send one interrupt
        # Interrupt flow control is different from data flow control, but
        # the closest analog is message flow control.
        self.flow = ConnMsg.SVC_MSG

    def process_data (self, item):
        pkt = item.packet
        if isinstance (pkt, IntMsg):
            # We don't bother checking inbound flow control, i.e.,
            # while we never issue any outbound requests for interrupt
            # messages, we still permit the other end to send more
            # than one.
            return self.parent.to_sc (item)
        # Not interrupt, so it's link service.
        if pkt.fcval_int == pkt.DATA_REQ:
            self.cross.process_ls (pkt)
        else:
            self.process_ls (pkt)

    def process_ls (self, pkt):
        """Process a link service packet that updates the interrupt
        subchannel, i.e., an "Interrupt request" packet in the NSP spec
        terminology.  The only meaningful field is FCVAL, which must be
        non-negative, and the total request count cannot exceed 127.
        """
        delta = pkt.fcval
        if delta >= 0:
            self.maxmsg += delta
        else:
            logging.debug ("Invalid LS (Interrupt Request) message {}", pkt)
        
    def send (self, pkt):
        """Queue a packet for transmission, and send it if we're allowed.

        The check for "allowed" does not apply to link service messages.
        PyDECnet does not send Link Service messages except for the
        no-op Keepalive message.  (If we ever allow more than one
        Interrupt message inbound that would change; there does not
        appear to be any good reason for using data subchannel flow
        control.)
        """
        if not isinstance (pkt, LinkSvcMsg) and self.maxmsg < self.nextmsg:
            # Interrupt sends are refused if we're not allowed to send
            # right now.  
            raise CantSend
        qe = txqentry (pkt, self, msgnum = self.nextmsg)
        self.nextmsg += 1
        self.pending_ack.append (qe)
        # Good to go; send it and start the timeout.
        qe.send ()
        self.maxseqsent = max (self.maxseqsent, pkt.segnum)
        
class Connection (Element, statemachine.StateMachine):
    """An NSP connection object.  This contains the connection state
    machine, the data and other-data subchannel state, and the session
    control API with the exception of the "connect" call.  Arriving
    packets that are mapped onto the connection come into the per-state
    processing function (via "dispatch" in the Statemachine base class).
    Note that packet type validation and address checks have already
    been done as part of the "mapping" of arriving packets onto connections.
    The timer of a Connection is the inactivity timer when in RUN state,
    and the timeout when in CI/CD or CR states.
    
    A note on connection states:

    The NSP spec uses a model where the Session Control layer polls NSP
    for things it needs to know about.  Because of that model, there are
    a number of connection states to represent "waiting for SC to poll
    NSP for something it has to hear".  The implementation we have here
    uses queueing of messages to SC instead of polling.  The result is that
    none of those "waiting for SC to poll" states are needed; instead,
    whatever the NSP spec delivers to SC for a poll in such a state is
    handled instead by a message to SC and an immediate transition to
    the state after.

    Specifically, this means that the O, DN, RJ, NC, and NR states
    do not exist.

    In the same way, states that exist only to model the "waiting for
    SC to close the port" case do not exist either.  Instead, the
    connection is closed immediately.

    This means that the DRC, CN, and DIC states do not exist either.

    Finally, there is no DR state because it does the same thing as DI;
    we use DI state instead.

    Note, though, that CL exists after a fashion.  When a connection is
    closed, NSP no longer knows about it (for example, it is no longer
    listed in the connection address lookup tables) but it is possible for
    some other component still to hold a reference to it.  State CN is
    represented by Connection.state == None.
    """
    def __init__ (self, parent, *, inbound = None, outbound = None):
        Element.__init__ (self, parent)
        statemachine.StateMachine.__init__ (self)
        # srcaddr and dstaddr are the connection identifiers, not
        # node addresses -- this matches the spec terminology
        self.srcaddr = srcaddr = self.parent.get_id ()
        if srcaddr is None:
            raise ConnectionLimit
        # We will add this connection to the dictionary of connections
        # known to NSP.  Don't do that yet, but check the max count.
        ccount = len (self.parent.connections) + 1
        if ccount > self.parent.ectr.peak_conns:
            self.parent.ectr.peak_conns = ccount
        self.dstaddr = 0
        self.shutdown = False
        # Parameters
        self.inact_time = parent.inact_time
        self.conn_timeout = parent.conn_timeout
        # Flags
        self.rstssegbug = False
        # Initialize the data segment reassembly list
        self.asmlist = list ()
        self.data = Data_Subchannel (self)
        # We use the optional "multiple other-data messages allowed at a time"
        # model, rather than the one at a time model that the NSP spec uses.
        # That makes the two subchannels look basically the same -- same data
        # structures, same control machinery.
        self.other = Other_Subchannel (self)
        # Set the "other subchannel" references
        self.other.cross = self.data
        self.data.cross = self.other
        # Now do the correct action for this new connection, depending
        # on whether it was an arriving one (CI packet) or originating
        # (session layer "connect" call). 
        if inbound:
            pkt = inbound.packet
            # Inbound connection.  Save relevant state about the remote
            # node, and send the payload up to session control.
            self.dest = inbound.src
            if isinstance (self.dest, Nodeid):
                self.destnode = self.parent.node.nodeinfo (self.dest)
            else:
                # Circuit, so try to find the loop node.
                if self.dest.loop_node is not None:
                    self.destnode = self.dest.loop_node
                else:
                    self.destnode = self.node.nodeinfo (self.node.nodeid)
            self.destnode.counters.con_rcv += 1
            self.dstaddr = pkt.srcaddr
            self.parent.rconnections[(self.dest, self.dstaddr)] = self
            self.setphase (pkt)
            self.data.flow = pkt.fcopt
            self.segsize = min (pkt.segsize, MSS)
            if self.cphase > 2:
                # If phase 3 or later, send CA
                ca = self.makepacket (AckConn)
                self.sendmsg (ca)
            # Now add the connection to the dictionary of ones we
            # know.  This needs to happen before we send it up to
            # session control.
            self.parent.connections[srcaddr] = self
            # Set the new state, and send the packet up to Session Control
            self.set_state (self.cr)
            self.to_sc (inbound)
        elif outbound:
            dest, payload = outbound
            # Create an outbound connection to the given destination node,
            # with the supplied session control layer payload.
            if dest == Nodeid (0):
                dest = self.parent.node.nodeid
            self.dest = dest
            try:
                self.destnode = self.parent.node.nodeinfo (dest)
            except KeyError:
                raise UnknownNode from None
            dest = self.dest = self.destnode.get_dest ()
            self.destnode.counters.con_xmt += 1
            ci = self.makepacket (ConnInit, payload = payload,
                                  fcopt = ConnMsg.SVC_NONE,
                                  info = self.parent.nspver,
                                  segsize = MSS)
            logging.trace ("Connecting to {}: {}", dest, payload)
            # Do this first otherwise that packet is processed in the
            # wrong state if it is addressed to ourselves.
            self.set_state (self.ci)
            # Now add the connection to the dictionary of ones we
            # know.  That too happens before the message is sent, for
            # the same reason.
            self.parent.connections[srcaddr] = self
            # Send it on the data subchannel
            self.data.send (ci)
        else:
            raise ValueError ("missing inbound or outbound argument")
        # Either way we start a timeout to reject the connection if
        # the other end (outbound) or the local application (inbound)
        # takes too long.  
        self.node.timers.start (self, self.conn_timeout)

    def setphase (self, pkt):
        # Remember the connection version (lower of the local and remote
        # version numbers).  Since the version numbers are not in
        # numeric order, map received version to remote DECnet phase,
        # and save the lower of that and ours.  The field is specified
        # as EX format, but only the bottom 2 bits are defined as the
        # NSP version code.
        self.rphase = nspphase[pkt.info & 3]
        self.cphase = min (self.rphase, self.parent.node.phase)
        
    def s0 (self, item):
        raise InternalError ("S0 state not used")
    
    def closed (self, item):
        raise InternalError ("Closed state should not be reached")
    
    def get_api (self):
        ret = { "local_addr" : self.srcaddr,
                "remote_addr" : self.dstaddr,
                "state" : self.state.name }
        if self.destnode:
            ret["node"] = self.destnode.nodeid
        return ret
            
    def close (self):
        """Get rid of this connection.  This doesn't send anything;
        if messages are needed, that is up to the caller.  
        """
        self.node.timers.stop (self)
        del self.parent.connections[self.srcaddr]
        # dstaddr isn't set yet if we're closing due to timeout after
        # CI, or CI returned to sender.
        if self.dstaddr:
            # The lookup by destination (remote) address in the case
            # of a loop node connection may be either the loop node's
            # circuit (if it was matched when the incoming packet
            # arrived) or the executor's address.  So try it both
            # ways.
            try:
                del self.parent.rconnections[(self.dest, self.dstaddr)]
            except KeyError:
                del self.parent.rconnections[(self.node.nodeid, self.dstaddr)]
        self.parent.ret_id (self.srcaddr)
        # Clean up the subchannels
        self.data.close ()
        self.other.close ()
        logging.trace ("Deleted connection {} to {}", self.srcaddr, self.dest)
        return self.closed

    def setsockopt (self, rstssegbug = False, **kwds):
        """Set connection options or flags not directly related to any
        standard connection API call.
        """
        self.rstssegbug = rstssegbug
        
    def accept (self, payload = b""):
        """Accept an incoming connection, using the supplied payload
        as session control accept data.
        """
        if self.state != self.cr:
            raise WrongState
        cc = self.makepacket (ConnConf, data_ctl = payload,
                              fcopt = ConnMsg.SVC_NONE,
                              info = self.parent.nspver,
                              segsize = MSS)
        logging.trace ("Accepting to {}: {}", self.srcaddr, payload)
        # Send it on the data subchannel as an acknowledged message if
        # phase 3 or later, but send it direct (no ack expected) for
        # phase 2.
        if self.cphase == 2:
            self.set_state (self.run)
            # Stop the connect timer
            self.node.timers.stop (self)
            self.sendmsg (cc)
        else:
            self.set_state (self.cc)
            self.data.send (cc)
        return True
        
    def reject (self, reason = 0, payload = b""):
        """Reject an incoming connection, using the supplied reason
        code and payload as session control reject data.
        """
        if self.state != self.cr:
            raise WrongState
        # Stop the connect timer
        self.node.timers.stop (self)
        # Do this first so the state will be DI if this is the local
        # node where the DiscComp comes back immediately.
        self.set_state (self.di)
        self.disc_rej (reason, payload)
        return True
    
    def disc_rej (self, reason, payload):
        # Common code for reject, disconnect, and abort
        if reason < 0 or reason > 255 or reason in reservedreasons:
            raise RangeError
        self.node.timers.stop (self)
        # Zap the subchannels:
        self.data.close ()
        self.other.close ()
        di = self.makepacket (DiscInit, reason = reason, data_ctl = payload)
        logging.trace ("Disconnecting (or rejecting) to {}: {} {}",
                       self.dest, reason, payload)
        # Send it on the data subchannel
        self.data.send (di)
        
    def disconnect (self, reason = 0, payload = b""):
        """Disconnect an active connection, using the supplied reason
        code and payload as session control disconnect data.  This is
        a "clean shutdown", the connection is closed once pending outbound
        transmits have been acknowledged.
        """
        if self.state != self.run:
            raise WrongState
        if self.data.pending_ack:
            # Data not all acked yet, don't send DI just yet
            # TODO: need to add code to send it later.
            self.shutdown = True
            self.pending_disc = (reason, payload)
        else:
            # Do this first so the state will be DI if this is the local
            # node where the DiscComp comes back immediately.
            self.set_state (self.di)
            self.disc_rej (reason, payload)
        return True
    
    def abort (self, reason = 0, payload = b""):
        """Disconnect an active connection, using the supplied reason
        code and payload as session control disconnect data.  This is
        a "hard shutdown", the connection is closed immediately, any
        pending transmits are discarded.
        """
        if self.state != self.run:
            raise WrongState
        self.disc_rej (reason, payload)
        self.set_state (self.di)
        return True

    def send_data (self, data):
        """Send a message.  Segmentation will be done here, i.e., we
        implement a session control message interface.  Messages are
        queued without limit, but of course are only transmitted if
        flow control rules permit.
        """
        if self.state != self.run or self.shutdown:
            raise WrongState
        self.destnode.counters.byt_xmt += len (data)
        self.destnode.counters.msg_xmt += 1
        bom = 1
        dl = len (data)
        for i in range (0, dl, self.segsize):
            eom = 0
            if dl - i <= self.segsize:
                eom = 1
            pkt = self.makepacket (DataSeg, bom = bom, eom = eom,
                                   payload = data[i:i + self.segsize])
            bom = 0
            self.data.send (pkt)
        return True
    
    def interrupt (self, data):
        """Send an interrupt.  This is accepted only if an interrupt
        message is allowed to be sent right now.  That is true when
        the connection is first opened, and whenever the remote node
        allows another interrupt to be sent.  Typically DECnet nodes
        allow only one interrupt at a time, so when this function is
        called, permission to send another is denied until the remote
        node gets around to sending another flow control message that
        issues another interrupt credit.
        """
        if self.state != self.run or self.shutdown:
            raise WrongState
        if len (data) > 16:
            raise IntLength
        self.destnode.counters.byt_xmt += len (data)
        self.destnode.counters.msg_xmt += 1
        sc = self.other
        pkt = self.makepacket (IntMsg, payload = data,
                               segnum = sc.seqnum)
        sc.send (pkt)
        # It was accepted, so increment the sequence number
        logging.trace ("sent interrupt seq {}", sc.seqnum)
        sc.seqnum += 1
        return True
    
    def to_sc (self, item, reject = False):
        """Send a work item to Session Control.
        """
        pkt = item.packet
        nc = self.destnode.counters
        if isinstance (pkt, DataSeg):
            if self.asmlist:
                # Not first segment
                if pkt.bom:
                    logging.debug ("BOM flag, but not first segment: {}", pkt)
                self.asmlist.append (pkt.payload)
                if not pkt.eom:
                    # Not last segment, nothing to give to SC.
                    return
                # Last segment of several.  Construct a message for the
                # entire payload
                pkt = DataSeg (payload = b''.join (self.asmlist))
                self.asmlist = list ()
                item.packet = pkt
            else:
                if not pkt.bom:
                    if self.rstssegbug:
                        # Workaround requested for this issue.  Some
                        # RSTS (DECnet/E) senders mishandle the flags;
                        # in RSTS this is done in the application, not
                        # in the DECnet core.  Specifically, the event
                        # sender is known to get it wrong.
                        pkt.bom = pkt.eom = 1
                    else:
                        logging.debug ("first segment but no BOM flag: {}", pkt)
                if not pkt.eom:
                    # First of several segments, save it
                    self.asmlist.append (pkt.payload)
                    return
                # Single segment message, pass it up as is.
            nc.byt_rcv += len (pkt.payload)
            nc.msg_rcv += 1
        elif isinstance (pkt, IntMsg):
            nc.byt_rcv += len (pkt.payload)
            nc.msg_rcv += 1
        item.reject = reject
        item.src = self
        item.connection = self
        self.node.addwork (item, self.node.session)
        
    def update_delay (self, txtime):
        if txtime and self.destnode:
            delta = time.time () - txtime
            # If the time estimate is smaller than our timer
            # granularity, round it up to one tick.  Otherwise we may
            # end up with retransmit timeouts that are too short and
            # produce false timeouts.
            if delta < JIFFY:
                delta = JIFFY
            if self.destnode.delay:
                # There is an estimate, do weighted average
                self.destnode.delay += (delta - self.destnode.delay) \
                                       / (self.parent.config.nsp_weight + 1)
            else:
                # No estimate yet, use this one
                self.destnode.delay = delta
            if self.destnode.delay > 5:
                # Cap it at 5 seconds.  Sometimes we have congestion and
                # the algorithm doesn't deal with that sanely.
                self.destnode.delay = 5

    def acktimeout (self):
        if self.destnode.delay:
            return self.destnode.delay * self.parent.config.nsp_delay
        return 2    # Spec says default is 5 but 2 is plenty nowadays
    
    def makepacket (self, cls, **kwds):
        pkt = cls (dstaddr = self.dstaddr, **kwds)
        # Connect Ack doesn't have a source address, so handle that separately
        try:
            pkt.srcaddr = self.srcaddr
        except AttributeError:
            pass
        return pkt
    
    def sendmsg (self, pkt):
        if logging.tracing:
            logging.trace ("NSP sending packet {} to {}", pkt, self.dest)
        self.destnode.counters.t_byt_xmt += len (pkt)
        self.destnode.counters.t_msg_xmt += 1
        self.parent.routing.send (pkt, self.dest,
                                  rqr = isinstance (pkt, ConnInit))

    def validate (self, item):
        if logging.tracing:
            logging.trace ("Processing {} in connection {}", item, self)
        return True
    
    def cr (self, item):
        """Connect Received state.  Mostly we wait here for Session
        Control to decide what to do about an inbound connection.
        We also ACK any retransmitted CI messages.
        """
        if isinstance (item, Received):
            pkt = item.packet
            if isinstance (pkt, ConnInit) and self.cphase > 2:
                # Retransmitted or out of order CI.  Resend the CA.
                ca = self.makepacket (AckConn)
                self.sendmsg (ca)
        elif isinstance (item, timers.Timeout):
            # Timeout waiting for application confirm (or reject).
            # Reject it with reason 38, and also deliver a disconnect up
            # to session control.
            self.reject (OBJ_FAIL)
            disc = DiscInit (reason = OBJ_FAIL, data_ctl = b"")
            self.to_sc (Received (self, packet = disc))

    def ci (self, item):
        """Connect Init sent state.  This just checks for Connect Ack
        and returned Connect Init, everything else is common with the
        CD state.
        """
        if isinstance (item, Received):
            pkt = item.packet
            if isinstance (pkt, AckConn) and self.node.phase > 2:
                # Connect Ack, go to CD state
                self.data.ack (0)    # Process ACK of the CI
                return self.cd
            elif isinstance (pkt, ConnInit):
                # Returned outbound CI.  Report unreachable to Session
                # Control, after substituting a disconnect (reject) message.
                #
                # Note that inbound CI doesn't come here, it comes in via the
                # constructor, or if retransmitted to the CR state handler.
                item.packet = DiscInit (reason = UNREACH, data_ctl = b"")
                self.to_sc (item, True)
                return self.close ()
        return self.cd (item)

    def cd (self, item):
        """Connect Delivered state.  This also serves as common code for
        the Connect Init state since they are nearly identical.
        """
        if isinstance (item, Received):
            pkt = item.packet
            if isinstance (pkt, ConnConf):
                # Connection was accepted.  Save relevant state about the remote
                # node, and send the payload up to session control.
                self.dstaddr = pkt.srcaddr
                # Save connection version information
                self.setphase (pkt)
                self.data.flow = pkt.fcopt
                self.segsize = min (pkt.segsize, MSS)
                self.data.ack (0)    # Treat this as ACK of the CI
                if self.cphase > 2:
                    # If phase 3 or later, send data Ack
                    ack = self.data.send_ack ()
                    self.node.timers.start (self, self.inact_time)
                # Send the accept up to Session Control
                self.to_sc (item)
                # Transition to RUN state
                return self.run
            elif isinstance (pkt, DiscInit):
                # Connect Reject
                self.dstaddr = pkt.srcaddr
                # Send the reject up to Session Control
                self.to_sc (item, True)
                # Ack the reject message
                ack = self.makepacket (DiscComp)
                self.sendmsg (ack)
                return self.close ()
            elif isinstance (pkt, (NoRes, DiscConf)):
                # No resources, or Phase 2 reject.
                if isinstance (pkt, NoRes):
                    # Received a "no resources" reject, count that
                    destnode = self.parent.node.nodeinfo (item.src)
                    if destnode:
                        destnode.counters.no_res_rcv += 1
                # Send the reject up to Session Control
                self.to_sc (item, True)
                return self.close ()
        elif isinstance (item, timers.Timeout):
            # Timeout waiting for confirm (or reject).  We can't send
            # anything to the other end because the protocol makes no
            # provision for disconnect in CR state.  So just deliver
            # failure locally and make the connection go away.
            disc = DiscInit (reason = OBJ_FAIL, data_ctl = b"")
            self.to_sc (Received (self, packet = disc), True)
            return self.close ()

    def cc (self, item):
        """Connect Confirm state.  Accept on an incoming connection
        gets us to this point (except in Phase II where that goes
        straight to RUN state).
        """
        if isinstance (item, Received):
            pkt = item.packet
            if isinstance (pkt, (DataSeg, AckData, IntMsg,
                                 LinkSvcMsg, AckOther)):
                self.data.ack (0)   # Treat ack or data as ACK of CC message
            self.set_state (self.run)
        return self.run (item)

    def run (self, item):
        if isinstance (item, Received):
            pkt = item.packet
            # On any received packet, restart the inactivity timer,
            # if phase 3 or higher
            if self.cphase > 2:
                self.node.timers.start (self, self.inact_time)
            if isinstance (pkt, (DataSeg, AckData)):
                self.data.dispatch (item)
            elif isinstance (pkt, (IntMsg, LinkSvcMsg, AckOther)):
                self.other.dispatch (item)
            elif isinstance (pkt, DiscInit):
                self.to_sc (item)
                ack = self.makepacket (DiscComp)
                self.sendmsg (ack)
                return self.close ()
            elif isinstance (pkt, DiscConf):
                self.to_sc (item)
                return self.close ()
            elif isinstance (pkt, ConnConf):
                # Duplicate confirm
                if self.cphase > 2:
                    # If phase 3 or later, send data Ack
                    ack = self.data.send_ack ()
                    return
        elif isinstance (item, timers.Timeout):
            # Inactivity timeout, send a no-change Link Service message
            pkt = self.makepacket (LinkSvcMsg,
                                   segnum = self.other.seqnum,
                                   fcmod = LinkSvcMsg.DATA_REQ,
                                   fcval_int = LinkSvcMsg.DATA_REQ,
                                   fcval = 0)
            self.other.seqnum += 1
            self.other.send (pkt)

    def di (self, item):
        if isinstance (item, Received):
            pkt = item.packet
            if isinstance (pkt, DiscInit):
                ack = self.makepacket (DiscComp)
                self.sendmsg (ack)
                return self.close ()
            elif isinstance (pkt, DiscConf):
                return self.close ()
                
class ReservedPort (Element):
    """An NSP "reserved port".  This is a descriptive trick to talk about
    error responses not tied to an active connection, things like no such
    connection, or no resources.  We implement a "reserved port" here
    because that makes things simple.
    """
    def dispatch (self, item):
        """Handle a work item for the reserved port.  Typically these
        generate an error response back to the sender; the specific
        response depends on what we're replying to.  Only Received items
        come here.
        """
        pkt = item.packet
        logging.trace ("Processing {} in reserved port", pkt)
        if isinstance (pkt, ConnInit):
            # ConnInit could not be mapped, send No Resources
            t = NoRes
        else:
            # Some other message could not be mapped, send No Link
            t = NoLink
        reply = t (srcaddr = pkt.dstaddr, dstaddr = pkt.srcaddr)
        self.node.routing.send (reply, item.src)

