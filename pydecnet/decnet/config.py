#!

"""DECnet config

"""

import io
import os
import sys
import argparse
import shlex

from .common import *
from .apiserver import dnparser
from . import datalink
from . import datalinks    # All the datalinks we know
from . import logging

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

cp = config_cmd ("circuit", "Circuit configuration", collection = True)
cp.add_argument ("name", help = "Circuit name", type = circname)
cp.add_argument ("--cost", type = int, metavar = "N",
                 help = "Circuit cost (range 1..25, default 1)",
                 choices = range (1, 26), default = 1)
cp.add_argument ("--t1", type = int, 
                 help = "Background routing message interval "
                 "(overrides exec setting)")
cp.add_argument ("--t3", type = int,
                 help = "Hello interval (default = 10 for LAN else 60)")
cp.add_argument ("--console", const = bytes (8), metavar = "V",
                 nargs = "?", type = scan_ver,
                 help = "Enable MOP console (V = verification)")
datalinks = [ d.__name__ for d in datalink.Datalink.leafclasses () ]
datalinks.append ("Ethernet")
datalinks.sort ()
cp.add_argument ("--type", default = "Ethernet", choices = datalinks,
                 help = "Datalink type (default: Ethernet)")
cp.add_argument ("--device",
                 help = "Device or connection string (default: same as name)")
cp.add_argument ("--random-address", action = "store_true", default = False,
                 help = "Generate random \"hardware address\" (Ethernet only)")
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

cp = config_cmd ("system", "Overall system configuration")
cp.add_argument ("--api-socket", metavar = "S", default = DEFAPISOCKET,
                 help = "Unix socket name for DECnet API")
cp.add_argument ("--http-port", metavar = "S", default = 8000,
                 type = int, choices = range (65536),
                 help = "Port number for HTTP monitoring, 0 to disable")
cp.add_argument ("--https-port", metavar = "S", default = 8001,
                 type = int, choices = range (65536),
                 help = "Port number for HTTPS monitoring/control, "
                 "0 to disable")

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
cp.add_argument ("--max-connections", type = int, default = 4095,
                 choices = [ (1 << i) - 1 for i in range (8, 16) ],
                 help = "Maximum number of connections")
cp.add_argument ("--nsp-weight", type = int, default = 3,
                 choices = range (256),
                 help = "NSP round trip averaging weight (range 0..255)")
cp.add_argument ("--nsp-delay", type = float, default = 2.0,
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

cp = config_cmd ("bridge", "LAN bridge layer")
cp.add_argument ("name", type = str, help = "Bridge name")
#cp.add_argument ("circuit", nargs = "+",
#                 help = "Circuit names assigned to this bridge")

class Config (object):
    """Container for configuration data.
    """
    def __init__ (self, f = None):
        if not f:
            f = open (DEFCONFIG, "rt")
        logging.debug ("Reading config %s", f.name)

        # Remove routing and bridge from single_init set, because we
        # handle those separately.
        single_init.discard ("routing")
        single_init.discard ("bridge")
        
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
                logging.error ("Config file parse error in %s:\n %s\n %s",
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
                    logging.error ("Missing config element: %s", name)
                    ok = False
            if not hasattr (self, "bridge") and not hasattr (self, "routing"):
                logging.error ("Either routing or bridge elements required")
                ok = False
            if not ok:
                sys.exit (1)
        return ok
        
            
