#!

"""DECnet config

"""

import io
import os
import sys
import argparse
import shlex

from .common import *
from . import datalink
from . import datalinks    # All the datalinks we know
from . import logging

class dnparser_message (Exception): pass
class dnparser_error (Exception): pass

class dnparser (argparse.ArgumentParser):
    """A subclass of argparse.ArgumentParser that overrides the
    error handling and program exits in the standard parser so
    control always comes back to the caller.
    """
    def _print_message (self, message, file = None):
        raise dnparser_message (message)

    def error (self, message):
        raise dnparser_error (message)

    def parse_args (self, args, namespace = None):
        """Parse an argument list.  Return value is a tuple consisting
        of the parse output (a Namespace object, or the object supplied
        in the namespace argument if any) and the message generated by
        the parse.  One of these will be None or False: for a successful parse,
        there is no message, and for a failed one or a help request,
        there is no result.  More precisely, the result is None for
        a help message, False for an error message.
        """
        try:
            return super ().parse_args (args, namespace), None
        except dnparser_message as e:
            return None, e.args[0]
        except dnparser_error as e:
            return False, e.args[0]

configparser = dnparser (prog = "", add_help = False)
configparser.add_argument ("-h", action = "help", help = argparse.SUPPRESS)
subparser = configparser.add_subparsers ()
coll_init = set ()
single_init = set ()

class LoggingConfig (argparse.Namespace):

    @property
    def name (self):
        return (self.sink_node, self.type)
    
def config_cmd (name, help, collection = False, namespace = None):
    cp = subparser.add_parser (name, add_help = False)
    cp.add_argument ("-h", action = "help", help = argparse.SUPPRESS)
    cp.set_defaults (collection = collection, attr = name,
                     namespace = namespace)
    if collection:
        coll_init.add (name)
    else:
        single_init.add (name)
    return cp

# Each of the config file entries is defined as a subparser, for a command
# name (the entity being configured) and a set of arguments to configure it.
# As with the way NCP does things, this puts related stuff together without
# paying attention to layering, so for example a "circuit" gives things
# relating to the datalink layer, routing layer, and so on.

datalinks = [ d.__name__ for d in datalink.Datalink.leafclasses () ]
datalinks.sort ()

cp = config_cmd ("circuit", "Circuit configuration", collection = True)
cp.add_argument ("name", help = "Circuit name", type = circname)
cp.add_argument ("type", choices = datalinks, metavar = "type",
                 help = "Datalink type; one of {}.".format (", ".join (datalinks)))
cp.add_argument ("device", help = "Device or connection string")
cp.add_argument ("--cost", type = int, metavar = "N",
                 help = "Circuit cost (range 1..25, default 1)",
                 choices = range (1, 26), default = 1)
cp.add_argument ("--t1", type = int, 
                 help = "Background routing message interval "
                 "(overrides exec setting)")
cp.add_argument ("--t3", type = int,
                 help = "Hello interval (default = 10 for LAN else 60)")
if WIN:
    cp.set_defaults (console = None)
else:
    cp.add_argument ("--console", const = bytes (8), metavar = "V",
                     nargs = "?", type = scan_ver,
                     help = "Enable MOP console (V = verification)")
cp.add_argument ("--single-address", action = "store_true", default = False,
                 help = "Use a single MAC address for all Ethernet"
                 " clients on this circuit (default: use separate MAC address for"
                 " each client)")
agroup = cp.add_mutually_exclusive_group ()
agroup.add_argument ("--random-address", action = "store_true", default = False,
                     help = "Generate random \"hardware address\" (Ethernet only)")
agroup.add_argument ("--hwaddr", type = Macaddr, default = NULLID, metavar = "H",
                     help = "Specify hardware address (Ethernet only)")

# The spec says the valid range is 0..255 but that is wrong, because the list
# of routers has to fit in a field of the router hello message that can at
# most hold 33.7 (!) entries.
cp.add_argument ("--nr", type = int, choices = range (1, 34), metavar = "N",
                 help = "Maximum routers on this LAN (range 1..33)",
                 default = 10)
cp.add_argument ("--priority", metavar = "P", type = int,
                 choices = range (128), default = 64,
                 help = "Designated router priority (range 0..127)")
cp.add_argument ("--verify", action = "store_true", default = False,
                 help = "Require routing verification (point to point only)")
cp.add_argument ("--mop", action = "store_true", default = False,
                 help = "Enable MOP and LAT (bridge circuit only)")

cp = config_cmd ("http", "HTTP access")
cp.add_argument ("--http-port", metavar = "S", default = 8000,
                 type = int, choices = range (65536),
                 help = "Port number for HTTP access, 0 to disable")
cp.add_argument ("--https-port", metavar = "S", default = 8443,
                 type = int, choices = range (65536),
                 help = "Port number for HTTPS access, 0 to disable")
cp.add_argument ("--certificate", metavar = "C", default = "decnet.pem",
                 help = "Name of certificate file for HTTPS, default = decnet.pem")
cp.add_argument ("--api", action = "store_true", default = False,
                 help = "Enable JSON API, by default over HTTPS only")
cp.add_argument ("--insecure-api", action = "store_true", default = False,
                 help = "Allow JSON API over HTTP")

cp = config_cmd ("routing", "Routing layer configuration")
cp.add_argument ("id", type = Nodeid, metavar = "NodeID",
                 help = "Node address")
cp.add_argument ("--type", default = "l2router",
                 choices = sorted ([ "l2router", "l1router", "endnode",
                                     "phase3router", "phase3endnode",
                                     "phase2" ]))
cp.add_argument ("--maxhops", metavar = "Maxh", type = int, default = 16,
                 choices = range (1, 31), help = "Max L1 hops (range 1..30)")
cp.add_argument ("--maxcost", metavar = "Maxc", type = int, default = 128,
                 choices = range (1, 1023),
                 help = "Max L1 cost (range 1..1022)")
cp.add_argument ("--amaxhops", metavar = "AMaxh", type = int, default = 16,
                 choices = range (1, 31), help = "Max L2 hops (range 1..30)")
cp.add_argument ("--amaxcost", metavar = "AMaxc", type = int, default = 128,
                 choices = range (1, 1023),
                 help = "Max L2 cost (range 1..1022)")
cp.add_argument ("--maxvisits", metavar = "Maxv", type = int, default = 32,
                 choices = range (1, 64), help = "Max visits (range 1..63)")
cp.add_argument ("--maxnodes", metavar = "NN", type = int, default = 1023,
                 choices = range (1, 1024),
                 help = "Max node number in area (range 1..1023)")
cp.add_argument ("--maxarea", metavar = "NA", type = int, default = 63,
                 choices = range (1, 64),
                 help = "Max area number (range 1..63)")
cp.add_argument ("--t1", type = int, default = 600,
                 help = "Non-LAN background routing message interval")
cp.add_argument ("--bct1", type = int, default = 10,
                 help = "LAN background routing message interval")

cp = config_cmd ("node", "DECnet node database", collection = True)
cp.add_argument ("id", choices = range (1, 65536), type = Nodeid,
                 metavar = "id", help = "Node address")
cp.add_argument ("name", type = nodename, help = "Node name")
cp.add_argument ("--outbound-verification", default = None,
                 help = "Verification value to send to this node")
cp.add_argument ("--inbound-verification", default = None,
                 help = "Verification value to require from this node")

cp = config_cmd ("nsp", "NSP layer configuration")
# The choices are given as a list not a set so they will be shown
# in order in the help string:
cp.add_argument ("--max-connections", type = int, default = 4095, metavar = "MC",
                 choices = [ (1 << i) - 1 for i in range (8, 16) ],
                 help = """Maximum number of connections, choice of
                        255, 511, 1023, 2047, 4095, 8191, 16383, 32767""")
cp.add_argument ("--nsp-weight", type = int, default = 3, metavar = "W",
                 choices = range (256),
                 help = "NSP round trip averaging weight (range 0..255)")
cp.add_argument ("--nsp-delay", type = float, default = 2.0, metavar = "D",
                 help = "NSP round trip delay factor (range 1..15.94)")

cp = config_cmd ("logging", "Event logging configuration", collection = True,
                 namespace = LoggingConfig)
cp.add_argument ("type", choices = ("console", "file", "monitor"),
                 help = "Sink type")
cp.add_argument ("--sink-node", type = str,
                 help = "Remote sink node (default: local)")
cp.add_argument ("--sink-file", type = str, default = "events.dat",
                 help = "File name for File sink")
cp.add_argument ("--events", type = str, default = "",
                 help = "Events to enable (default: known events for"
                 " local console, none otherwise")

cp = config_cmd ("session", "Session Control layer configuration")
cp.add_argument ("--default-user", metavar = "DEF",
                 help = """Default username for objects with default
                        authentication enabled""")

cp = config_cmd ("object", "Session Control object", collection = True)
cp.add_argument ("--name", help = "Object name")
cp.add_argument ("--number", type = int, choices = range (1, 256),
                 default = 0, metavar = "N", help = "Object number")
ogroup = cp.add_mutually_exclusive_group ()
ogroup.add_argument ("--file", metavar = "FN",
                     help = "Program file name to execute")
ogroup.add_argument ("--module", metavar = "M",
                     help = "Python module identifier to execute")
cp.add_argument ("--argument", metavar = "A",
                 help = "Optional argument to pass to application when started")
cp.add_argument ("--authentication", choices = ("on", "off"), default = "on",
                 help = """'on' to have PyDECnet verify username/password,
                        'off' to ignore username/password.  Default: on.""")
cp.add_argument ("--disable", action = "store_true", default = False,
                 help = "Disable built-in object")

cp = config_cmd ("bridge", "LAN bridge layer")
cp.add_argument ("name", type = str, help = "Bridge name")

class Config (object):
    """Container for configuration data.
    """
    def __init__ (self, f = None):
        if not f:
            f = open (DEFCONFIG, "rt")
        logging.debug ("Reading config {}", f.name)
        self.configfilename = f.name
        
        # Remove routing, bridge,and http from single_init set, because we
        # handle those separately.
        single_init.discard ("routing")
        single_init.discard ("bridge")
        single_init.discard ("http")
        
        # First supply empty dicts for each collection config component
        for name in coll_init:
            setattr (self, name, dict ())
        # Also set defaults for non-collections:
        for name in single_init:
            p, msg = configparser.parse_args ([ name ])
            if p:
                setattr (self, name, p)
        self.scanconfig (f)

    def scanconfig (self, f, nested = False):
        ok = True
        for l in f:
            l = l.rstrip ("\n").strip ()
            if not l or l[0] == "#":
                continue
            if l[0] == '@':
                # Indirect file, read it recursively.  The supplied file
                # name is relative to the current file.
                fn = os.path.join (os.path.dirname (f.name), l[1:])
                ok = self.scanconfig (open (fn, "rt"), True) and ok
                continue
            p, msg = configparser.parse_args (shlex.split (l))
            if not p:
                logging.error ("Config file parse error in {}:\n {}\n {}",
                               f, msg, l)
                ok = False
            else:
                if p.namespace:
                    p.__class__ = p.namespace
                if p.collection:
                    getattr (self, p.attr)[p.name] = p
                else:
                    setattr (self, p.attr, p)
        f.close ()
        if not nested:
            if not ok:
                sys.exit (1)
            # See if anything is missing.  The only required elements
            # are single-instance elements (the layers), and then only
            # if they have at least one required argument.  For example,
            # "routing" is required because node address is required,
            # but "system" is optional because it has no required arguments.
            for name in single_init:
                p = getattr (self, name, None)
                if not p:
                    logging.error ("Missing config element: {}", name)
                    ok = False
            if hasattr (self, "bridge") + hasattr (self, "routing") + \
                hasattr (self, "http") != 1:
                logging.error ("Exactly one of routing, bridge, or http required, config file {}",
                               self.configfilename)
                ok = False
            if not ok:
                sys.exit (1)
        return ok
        
            
