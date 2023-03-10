#!

"""Event logging support for DECnet/Python

This is layered on top of the standard "logging" module, by passing along
an additional chunk of data in an Event object.  This can then be formatted
as a text log message, or sent by a new custom logging handler to a DECnet
event listener.

The Event class can also be used as an exception, and the Exception instance
can be caught and then logged, resulting in the same output as for a simple
"logevent" call.
"""

import datetime
import struct
import collections

from .common import *
from . import logging
from .nice_coding import *

# Base date/time for time code in event message (1 Jan 1977)
jbase = datetime.datetime (1977, 1, 1)

class EventBase (packet.Packet):
    """Base class for DECnet events.  This defines the standard header
    through the entity field.
    """
    classindex = { }
    loglevel = logging.INFO
    
    @classmethod
    def classindexkey (cls):
        return cls.instanceindexkey (cls)

    def instanceindexkey (self):
        return self.event_class, self.event_code

    @classmethod
    def defaultclass (cls, idx):
        ecls, ecode = idx
        try:
            return cls.classindex[(ecls, None)]
        except KeyError:
            return cls.classindex[(None, None)]
        
    _layout = (( packet.B, "function", 1 ),
               ( packet.BM,
                 ( "console", 0, 1 ),
                 ( "file", 1, 1 ),
                 ( "monitor", 2, 1 )),
               ( packet.BM,
                 ( "event_code", 0, 5 ),
                 ( "event_class", 6, 9 )),
               ( packet.B, "halfday", 2 ),
               ( packet.B, "seconds", 2 ),
               ( packet.BM,
                 ( "milliseconds", 0, 10 ),
                 ( "ms_absent", 15, 1 )),
               ( NiceNode, "source" ))
    function = 1

    def __new__ (cls, entity = None, *args, **kwargs):
        return super (__class__, cls).__new__ (cls)

    def __init__ (self, entity = None, source = None, **kwds):
        """Construct an Event object.  Arguments are:
        entity: the reporting entity for this event.  Note that
          this works only when creating a subclass of EventBase
          where entity_type is defined in the layout.
        source: source node for the event.  Usually omitted, will be
          set by node.logevent.  If supplied it must be a NiceNode
          instance.
        other keywords are handled as event parameter names with the
          associated value to set in the event.
        """
        super ().__init__ ()
        if entity is not None:
            self.entity_type = entity
        self.source = source
        # Set event creation time to current time.  We use datetime
        # objects because of the need for "naive" rather than "aware"
        # timestamps.  These are timestamps that pay no attention to
        # daylight savings time; that is the case for the time
        # encoding used in events.  Earlier code used methods like
        # time.localtime but these use daylight savings time rules and
        # result in time stamps that are one hour off in the summer
        # when compared to times shown by other implementations like
        # RSTS/E.
        delta = datetime.datetime.now () - jbase
        hd, self.seconds = divmod (delta.seconds, 12 * 60 * 60)
        self.halfday = delta.days * 2 + hd
        self.milliseconds = int (delta.microseconds / 1000)
        self.ms_absent = 0
        self.console = self.file = self.monitor = 1
        self.setparams (**kwds)
        
    def __str__ (self):
        days, hd = divmod (self.halfday, 2)
        delta = datetime.timedelta (days, self.seconds + hd * 12 * 60 * 60)
        ts = jbase + delta
        ts = ts.strftime ("%d-%b-%Y %H:%M:%S")
        if not self.ms_absent:
            ts = "{}.{:03d}".format (ts, self.milliseconds)
        if self.__doc__:
            l1 = "Event type {}.{}, {}".format (self.event_class,
                                                self.event_code,
                                                self.__doc__)
        else:
            l1 = "Event type {}.{}".format (self.event_class, self.event_code)
        ret = [ l1, "  From node {}, occurred {}".format (self.source, ts) ]
        # "s" means "short" for node entity, just plain string for
        # others.
        e = "{:s}".format (self.entity_type)
        if e:
            ret.append ("  {}".format (e))
        ret.append (NICE.format (self))
        return '\n'.join (ret)

    def setparams (self, **kwds):
        for k, v in kwds.items ():
            if v is None:
                continue
            # Map coded value keyword to numeric value, if a mapping is
            # specified for this variable name.
            try:
                vdict = self._values[k]
                v = vdict[v]
            except (AttributeError, KeyError):
                pass
            setattr (self, k, v)

class Event (EventBase):
    event_class = None
    event_code = None
    
class DefaultEvent (Event):
    # This is the event class used for decoding any event message with a
    # class code we do not know.  It accepts any NICE data but doesn't
    # know any of the parameter codes.
    #
    # Note that this definition must come after any other event base
    # classes or default classes that don't define a particular class or
    # code, so this one ends up as the default class to use for
    # decoding.
    _layout = (( EventEntityBase, "entity_type" ),
               ( NICE, True ))

class NetmanBase (Event):
    event_class = 0
    nicedef = ( NICE, True,
                ( 0, C1, "Service", None, ( "Load", "Dump" )),
                ( 1, CM, "Status", None, ( C1, C2, AI )),
                ( 2, C1, "Operation", None, ( "Initiated", "Terminated" )),
                ( 3, C1, "Reason", None,
                  ( "Receive timeout",
                    "Receive error"
                    "Line state change by higher level",
                    "Unrecognized request",
                    "Line open error" )),
                ( 4, CM, "Qualifier", None, ( C2, AI )),
                ( 5, CMNode, "Node" ),
                ( 6, AI, "DTE" ),
                ( 7, AI, "Filespec" ),
                ( 8, C1, "Software type", None,
                  ( "Secondary loader",
                    "Tertiary loader",
                    "System" )))

class NetmanEvent (NetmanBase):
    _layout = (( NoEntity, "entity_type" ),
               NetmanBase.nicedef ) 
                     
class NetmanDefaultEvent (NetmanBase):
    _layout = (( EventEntityBase, "entity_type" ),
               NetmanBase.nicedef ) 
                     
class events_lost (NetmanEvent):
    "Event records lost"
    event_code = 0
    
class node_ctrs (NetmanBase):
    "Automatic node counters"
    event_code = 1
    _layout = (( NodeEventEntity, "entity_type" ),
               ( NICE, True ) + node_counters )

# TODO: counters
class line_ctrs (NetmanEvent):
    "Automatic line counters"
    event_code = 2

class circ_svc (NetmanBase):
    "Automatic service"
    event_code = 3
    _layout = (( CircuitEventEntity, "entity_type" ),
               NetmanBase.nicedef ) 

# TODO
class line_zero (NetmanEvent):
    "Line counters zeroed"
    event_code = 4

class node_zero (NetmanBase):
    "Node counters zeroed",    
    event_code = 5
    _layout = (( NodeEventEntity, "entity_type" ),
               ( NICE, True ) + node_counters )

class circ_loop (NetmanBase):
    "Passive loopback"
    event_code = 6
    _layout = (( CircuitEventEntity, "entity_type" ),
               NetmanBase.nicedef ) 

class circ_svcabt (circ_loop):
    "Aborted service request"
    event_code = 7

# TODO: entity dependent counters?
class auto_ctrs (NetmanBase):
    "Automatic counters"
    event_code = 8
    _layout = (( EventEntityBase, "entity_type" ),
               NetmanBase.nicedef ) 

class ctrs_zero (auto_ctrs):
    "Counters zeroed"
    event_code = 9
    
se_state = ( "On", "Off", "Shut", "Restricted" )
class SessionEvent (Event):
    event_class = 2
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True,
                 ( 0, C1, "Reason", None,
                   ( "Operator command",
                     "Normal operation" ) ),
                 ( 1, C1, "Old state", None, se_state ),
                 ( 2, C1, "New state", None, se_state ),
                 ( 3, CMNode, "Source node" ),
                 ( 4, CMProc, "Source process" ),
                 ( 5, CMProc, "Destination process" ),
                 ( 6, AI, "User" ),
                 ( 7, C1, "Password", None, ( "Set", ) ),
                 ( 8, AI, "Account" )))
    _values = { "reason" : { "operator_command" : 0,
                             "normal_operation" : 1 },
                "old_state" :  { "on" : 0,
                                 "off" : 1,
                                 "shut" : 2,
                                 "restricted" : 3, },
                "new_state" :  { "on" : 0,
                                 "off" : 1,
                                 "shut" : 2,
                                 "restricted" : 3, } }


class node_state (SessionEvent):
    "Local node state change"
    event_code = 0

class acc_rej (SessionEvent):
    "Access control reject"
    event_code = 1
    loglevel = logging.WARNING
    
class EclEvent (Event):
    event_class = 3
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True,
                 ( 0, CM, "Message", None, ( H1, DU2, DU2, HI ) ),
                 ( 1, DS1, "Current flow control request count",
                   "request_count" ),
                 ( 2, CMNode, "Source node" ))
                 + node_counters)

class inv_msg (EclEvent):
    "Invalid message"
    event_code = 0
    loglevel = logging.DEBUG

class inv_flow (EclEvent):
    "Invalid flow control"
    event_code = 1
    loglevel = logging.DEBUG

class db_reuse (EclEvent):
    "Data base reused"
    event_code = 2

class RoutingEvent (Event):
    event_class = 4
    _layout = (( CircuitEventEntity, "entity_type" ),
               ( NICE, True,
               ( 0, CM, "Packet header", "eth_packet_header",
                 ( H1, DU1, DU1, HI, DU1, DU1, HI, DU1, DU1, H1, DU1 )),
               ( 0, CM, "Packet header", "ni_packet_header",
                 ( H1, H1, DU2, AI )),
               ( 0, CM, "Packet header", "nv_packet_header",
                 ( H1, H1 )),
               ( 0, CM, "Packet header", "packet_header",
                 ( H1, DUNode, DUNode, DU1 )),
               ( 1, HI, "Packet beginning" ),
               ( 2, DU2, "Highest address" ),
               ( 3, CMNode, "Node" ),
               ( 4, CMNode, "Expected node" ),
               ( 5, C1, "Reason", None,
                 ( "Circuit synchronization lost",
                   "Data errors",
                   "Unexpected packet type",
                   "Routing update checksum error",
                   "Adjacency address change",
                   "Verification receive timeout",
                   "Version skew",
                   "Adjacency address out of range",
                   "Adjacency block size too small",
                   "Invalid verification seed value",
                   "Adjacency listener receive timeout",
                   "Adjacency listener received invalid data",
                   "Call failed",
                   "Verification password require for Phase III node",
                   "Dropped by adjacent node" )),
               ( 6, CMVersion, "Received version" ),
               ( 7, C1, "Status", None, ( "Reachable", "Unreachable" )),
               ( 8, CMNode, "Adjacent node" )))

    _values = { "reason" : { "sync_lost" : 0,
                             "data_errors" : 1,
                             "unexpected_packet_type" : 2,
                             "checksum_error" : 3,
                             "address_change" : 4,
                             "verification_timeout" : 5,
                             "version_skew" : 6,
                             "address_out_of_range" : 7,
                             "block_size_too_small" : 8,
                             "invalid_verification" : 9,
                             "listener_timeout" : 10,
                             "listener_invalid_data" : 11,
                             "call_failed" : 12,
                             "verification_required" : 13,
                             "dropped" : 14 },
                "status" : { "reachable" : 0,
                             "unreachable" : 1 } }

class aged_drop (RoutingEvent):
    "Aged packet loss"
    event_code = 0
    loglevel = logging.DEBUG

class unreach_drop (RoutingEvent):
    "Node unreachable packet loss"
    event_code = 1
    loglevel = logging.DEBUG

class oor_drop (RoutingEvent):
    "Node out-of-range packet loss"
    event_code = 2

class size_drop (RoutingEvent):
    "Oversized packet loss"
    event_code = 3

class fmt_err (RoutingEvent):
    "Packet format error"
    event_code = 4
    loglevel = logging.DEBUG

class rout_upd_loss (RoutingEvent):
    "Partial routing update loss"
    event_code = 5

class ver_rej (RoutingEvent):
    "Verification reject"
    event_code = 6
    loglevel = logging.WARNING

class circ_fault (RoutingEvent):
    "Circuit down, circuit fault"
    event_code = 7
    loglevel = logging.WARNING

class circ_down (RoutingEvent):
    "Circuit down"
    event_code = 8
    loglevel = logging.WARNING

class circ_off (RoutingEvent):
    "Circuit down, operator initiated"
    event_code = 9

class circ_up (RoutingEvent):
    "Circuit up"
    event_code = 10

class init_fault (RoutingEvent):
    "Initialization failure, line fault"
    event_code = 11

class init_swerr (RoutingEvent):
    "Initialization failure, software fault"
    event_code = 12

class init_oper (RoutingEvent):
    "Initialization failure, operator fault"
    event_code = 13

class reach_chg (Event):
    "Node reachability change"
    event_class = 4
    event_code = 14
    _layout = (( NodeEventEntity, "entity_type" ),
               ( NICE, True,
                ( 7, C1, "Status", None, ( "Reachable", "Unreachable" ))))
    _values = RoutingEvent._values
    
class adj_up (RoutingEvent):
    "Adjacency up"
    event_code = 15

class adj_rej (RoutingEvent):
    "Adjacency rejected"
    event_code = 16

class area_chg (Event):
    "Area reachability change"
    event_class = 4
    event_code = 17
    _layout = (( AreaEventEntity, "entity_type" ),
               ( NICE, True,
                 ( 7, C1, "Status", None, ( "Reachable", "Unreachable" ))))
    _values = RoutingEvent._values

class adj_down (RoutingEvent):
    "Adjacency down"
    event_code = 18
    loglevel = logging.WARNING

class adj_oper (RoutingEvent):
    "Adjacency down, operator initiated"
    event_code = 19

dl_state = ( "Halted", "IStrt", "AStrt", "Running", "Maintenance" )
dl_state11 = ( "On", "Off", "Shut" )

class DlEvent (Event):
    event_class = 5
    _layout = (( CircuitEventEntity, "entity_type" ),
               ( NICE, True,
                 ( 0, C1, "Old state", None, dl_state ),
                 ( 1, C1, "New state", None, dl_state ),
                 ( 2, HI, "Header" ),
                 ( 3, DU1, "Selected tributary" ),
                 ( 4, DU1, "Previous tributary" ),
                 ( 5, C1, "Tributary status", None,
                   ( "Streaming",
                     "Continued send after timeout",
                     "Continued send after deselect",
                     "Ended streaming" )),
                 ( 6, DU1, "Received tributary" ),
                 ( 7, DU2, "Block length" ),
                 ( 8, DU2, "Buffer length" ),
                 ( 9, AI, "DTE" ),
                 ( 10, C1, "Reason", None,
                   ( "Operator command", "Normal operation" )),
                 ( 11, C1, "Old state", "old_state_11", dl_state11 ),
                 ( 12, C1, "New state", "new_state_11", dl_state11 ),
                 ( 13, C2, "Parameter type", None, () ),
                 ( 14, DU1, "Cause" ),
                 ( 15, DU1, "Diagnostic" ),
                 ( 16, C1, "Failure reason", None,
                   ( "Excessive collisions",
                     "Carrier check failed",
                     "(OBSOLETE)",
                     "Short circuit",
                     "Open circuit",
                     "Frame too long",
                     "Remote failure to defer",
                     "Block check error",
                     "Framing error",
                     "Data overrun",
                     "System buffer unavailable",
                     "User buffer unavailable",
                     "Unrecognized frame destination")),
                 ( 17, DU2, "Distance" ),
                 ( 18, CM3, "Ethernet header", None, ( HI, HI, HI )),
                 ( 19, H1, "Hardware status" )))

    _values = { "old_state" :  { "halted" : 0,
                                 "istrt" : 1,
                                 "astrt" : 2,
                                 "running" : 3,
                                 "maintenance" : 4 },
                "new_state" :  { "halted" : 0,
                                 "istrt" : 1,
                                 "astrt" : 2,
                                 "running" : 3,
                                 "maintenance" : 4 } }
                                 
class circ_lcl (DlEvent):
    "Locally initiated state change"
    event_code = 0

class circ_rem (DlEvent):
    "Remotely initiated state change"
    event_code = 1

class circ_maint (DlEvent):
    "Protocol restart received in maintenance mode"
    event_code = 2

class circ_xerr (DlEvent):
    "Send error threshold"
    event_code = 3

class circ_rerr (DlEvent):
    "Receive error threshold"
    event_code = 4

class circ_sel (DlEvent):
    "Select error threshold"
    event_code = 5

class circ_bherr (DlEvent):
    "Block header format error"
    event_code = 6

class circ_addr (DlEvent):
    "Selection address error"
    event_code = 7

class circ_trib (DlEvent):
    "Streaming tributary"
    event_code = 8

class circ_bufsz (DlEvent):
    "Local buffer too small"
    event_code = 9

class mod_restart (DlEvent):
    "Restart"
    event_code = 10

class mod_state (DlEvent):
    "State change"
    event_code = 11

class mod_stmax (DlEvent):
    "Retransmit maximum exceeded"
    event_code = 12

class line_initfail (DlEvent):
    "Initialization failure"
    event_code = 13

class line_xfail (DlEvent):
    "Send failed"
    event_code = 14

class line_rfail (DlEvent):
    "Receive failed"
    event_code = 15

class line_coll (DlEvent):
    "Collision detect check failed"
    event_code = 16

class mod_dteup (Event):
    "DTE up"
    event_class = 5
    event_code = 17
    _layout = (( ModuleEventEntity, "entity_type" ),
               ( NICE, True,
                  ( 9, AI, "DTE" )))

class mod_dtedown (mod_dteup):
    "DTE down"
    event_code = 18

class PhyEvent (Event):
    event_class = 6
    _layout = (( LineEventEntity, "entity_type" ),
               ( NICE, True,
                 ( 0, O2, "Device register" ),
                 ( 1, C1, "New state", None, ( "Off", "On" ))))

class line_dsr (PhyEvent):
    "Data set ready transition"
    event_code = 0

class line_ring (PhyEvent):
    "Ring indicator transition"
    event_code = 1

class line_carr (PhyEvent):
    "Unexpected carrier transition"
    event_code = 2

class line_mem (PhyEvent):
    "Memory access error"
    event_code = 3

class line_comm (PhyEvent):
    "Communications interface error"
    event_code = 4

class line_perf (PhyEvent):
    "Performance error"
    event_code = 5

# DECnet/E (RSTS) specific events

class RstsAppEvent (Event):
    event_class = 33
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True,
                 ( 0, C1, "Access", None, ( "Local", "Remote" )),
                 ( 1, C1, "Function", None,
                   ( "Illegal",
                     "Open/read",
                     "Open/write",
                     "Rename",
                     "Delete",
                     "Reserved",
                     "Directory",
                     "Submit",
                     "Execute")),
                 ( 3, CMNode, "Remote node" ),
                 ( 4, CMProc, "Remote process" ),
                 ( 5, CMProc, "Local process" ),
                 ( 6, AI, "User" ),
                 ( 7, C1, "Password", None, ( "Set", ) ),
                 ( 8, AI, "Account" ),
                 ( 9, AI, "File accessed" )))
        
class rsts_fal (RstsAppEvent):
    "Remote file access"
    event_code = 0

class RstsSessionEvent (Event):
    event_class = 34
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True,
                 ( 0, C1, "Reason", None,
                   ( "I/O error on Object database",
                     "Spawn Directive failed",
                     "Unknown Object identification" )),
                 ( 3, CMNode, "Source node" ),
                 ( 4, CMProc, "Source process" ),
                 ( 5, CMProc, "Destination process", "dest_process" ),
                 ( 6, AI, "User" ),
                 ( 7, C1, "Password", None, ( "Set", ) ),
                 ( 8, AI, "Account" )))

class rsts_spawn (RstsSessionEvent):
    "Object spawned"
    event_code = 0

class rsts_spawn_fail (RstsSessionEvent):
    "Object spawn failure"
    event_code = 1
    loglevel = logging.WARNING

# RSX specific events.  These are just event codes, not the
# associated parameters.  I found only that much in RSX manuals.
# TODO: look for more information.

class RsxEvent (Event):
    event_class = 64
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True ))

class rsx_rdb_corrupt (RsxEvent):
    "Routing database corrupt"
    event_code = 1

class rsx_rdb_restored (RsxEvent):
    "Routing database restored"
    event_code = 2

class rsx_93 (Event):
    event_class = 93
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True ))

class rsx_state_change (rsx_93):
    "State change"
    event_code = 0

class rsx_94 (Event):
    event_class = 94
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True ))
    
class rsx_dce_err (rsx_94):
    "DCE detected packet error"
    event_code = 0


# VMS specific events.  These are just event codes, not the
# associated parameters.  I found only that much in VMS manuals.

class VmsEvent (Event):
    event_class = 128
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True ))

class vms_dap_crc (VmsEvent):
    "DAP CRC error detected"
    event_code = 1

class vms_dup_ph2 (VmsEvent):
    "Duplicate PHASE 2 address error"
    event_code = 2

class vms_proc_create (VmsEvent):
    "Process created"
    event_code = 3

class vms_proc_term (VmsEvent):
    "Process terminated"
    event_code = 4

class VmsDnsEvent (Event):
    event_class = 353
    _layout = (( NoEntity, "entity_type" ),
               ( NICE, True ))

class vms_dns_comm (VmsDnsEvent):
    "DECdns clerk unable to communicate with server"
    event_code = 5

class vms_dns_advert (VmsDnsEvent):
    "Local DECdns Advertiser error"
    event_code = 20

# Documentation tool, intended to be called interactively when needed
# for updating the description in config.txt.
def printEventLevels ():
    levels = collections.defaultdict (list)
    for k, v in EventBase.classindex.items ():
        ec, en = k
        if en is None:
            continue
        levels[v.loglevel].append (v)
    for l, elist in sorted (levels.items ()):
        print ("\nLevel {}".format (logging.logging.getLevelName (l)))
        for v in elist:
            print ("{}.{} ({})".format (v.event_class, v.event_code,
                                        v.__doc__))
