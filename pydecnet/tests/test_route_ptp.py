#!/usr/bin/env python3

from tests.dntest import *

import queue

from decnet.routing_packets import *
from decnet import route_ptp
from decnet import routing
from decnet import datalink
from decnet.node import Nodeinfo
from decnet.common import NiceNode

rcount = 5000
rmin = 0
rmax = 30

class rtest (DnTest):

    def setUp (self):
        super ().setUp ()
        self.r = unittest.mock.Mock ()
        self.r.node = self.node
        if self.phase == 4:
            self.r.nodeid = Nodeid (1, 5)
            self.node.ntype = routing.L2ROUTER
        else:
            self.r.nodeid = Nodeid (5)
            self.node.ntype = routing.L1ROUTER
        self.r.homearea, self.r.tid = self.r.nodeid.split ()
        info = Nodeinfo (None, self.r.nodeid)
        info.nodename = "LOCAL"
        info.counters = routing.ExecCounters (info, self.node)
        self.node.addnodeinfo (info)
        self.r.nodeinfo = info
        info = Nodeinfo (None, Nodeid (66))
        info.nodename = "REMOTE"
        info.iverif = b"IVERIF"
        info.overif = b"OVERIF"
        self.node.addnodeinfo (info)
        info = Nodeinfo (None, Nodeid (1, 66))
        info.nodename = "REMOTE"
        info.iverif = b"IVERIF"
        info.overif = b"OVERIF"
        self.node.addnodeinfo (info)
        info = Nodeinfo (None, Nodeid (2))
        info.iverif = b"IVERIF"
        info.overif = b"OVERIF"
        self.node.addnodeinfo (info)
        info = Nodeinfo (None, Nodeid (1, 2))
        info.iverif = b"IVERIF"
        info.overif = b"OVERIF"
        self.node.addnodeinfo (info)
        self.node.enable_dispatcher ()
        self.dl = unittest.mock.Mock ()
        self.cp = unittest.mock.Mock ()
        self.cp.start_works = True
        self.dl.create_port.return_value = self.cp
        self.config = container ()
        self.config.t1 = 300
        self.config.t3 = 10
        self.config.cost = 1
        self.config.latency = None
        self.config.verify = self.verify
        self.r.phase = self.node.phase = self.phase
        self.r.tiver = self.tiver
        self.r.ntype = self.ntype
        self.r.maxnodes = 200
        if self.ntype in { L1ROUTER, L2ROUTER }:
            self.r.minhops, self.r.mincost = routing.allocvecs (self.r.maxnodes)
        self.r.maxarea = 10
        if self.ntype == L2ROUTER:
            self.r.aminhops, self.r.amincost = routing.allocvecs (self.r.maxarea)
        self.r.name = b"TEST"
        if self.ntype in { PHASE2, ENDNODE }:
            cls = routing.PtpEndnodeCircuit
        elif self.ntype == L1ROUTER:
            cls = routing.PtpL1Circuit
        else:
            cls = routing.PtpL2Circuit
        self.c = cls (self.r, "ptp-0", self.dl, self.config)
        self.c.routing = self.r
        self.c.t3 = 15
        self.assertState ("ha")
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        self.c.start ()
        self.assertState ("ds")
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.UP))
        self.assertState ("ri")        

    def tearDown (self):
        self.c.stop ()
        self.assertState ("ha")
        super ().tearDown ()

    def assertState (self, name):
        self.assertEqual (self.c.state.__name__, name, "Circuit state")
    
    def shortpackets (self, pkt):
        for l in range (len (pkt) - 1):
            self.node.addwork (Received (owner = self.c, src = self.c,
                                       packet = pkt[:l]))
            self.assertIn (self.c.state.__name__, {"ds", "ri"}, "Circuit state")
            if self.c.state == self.c.ds:
                self.node.addwork (datalink.DlStatus (owner = self.c,
                                                    status = datalink.DlStatus.UP))
            self.assertState ("ri")

class test_ph2 (rtest):
    phase = 2
    tiver = tiver_ph2
    ntype = PHASE2
    verify = False
    
    def startup (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, NodeInit)
        self.assertEqual (p.nodename, b"TEST")
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up,
                          adjacent_node = NiceNode (66, "REMOTE"))
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (66))

    def test_noverify (self):
        self.startup ()
        pkt = b"\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, pkt)
        self.assertEqual (spkt.srcnode, Nodeid (66))
        self.assertEqual (spkt.dstnode, self.r.nodeid)
        self.assertEqual (spkt.visit, 1)
        DnTimeout (self.c)
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, NopMsg)
        self.assertRegex (p.payload, b"^\252+$")

    def test_extadr (self):
        # Phase 2 init has address in an EX-2 field, so if it's 128 or above
        # it takes 2 bytes rather than 1.
        pkt = b"\x58\x01\x82\x01\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (130))
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (130))
        # Deliver an incoming packet
        pkt = b"\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, pkt)
        self.assertEqual (spkt.srcnode, Nodeid (130))
        self.assertEqual (spkt.dstnode, self.r.nodeid)
        self.assertEqual (spkt.visit, 1)
        # Deliver an incoming packet, with route header
        pkt = b"\x42\x05LOCAL\x06REMOTE\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (2, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        # Payload will be just the part after the route header
        self.assertEqual (spkt.payload, b"\x08\252\252\252")
        self.assertEqual (spkt.srcnode, Nodeid (130))
        self.assertEqual (spkt.dstnode, self.r.nodeid)
        self.assertEqual (spkt.visit, 1)
        # Try a bad route header
        pkt = b"\x42\xf5LOCAL\x06REMOTE\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.lastdispatch (2, element = self.r)
        self.assertEvent (events.fmt_err, 
                          packet_beginning = b"\x42\xf5LOCA")

    def test_send (self):
        self.startup ()
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (66),
                         srcnode = Nodeid (5), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 2, ptype = bytes)
        self.assertEqual (p, pkt.payload)
        # Try long data
        s = LongData (rqr = 1, rts = 0, ie = 1, dstnode = Nodeid (66),
                      srcnode = Nodeid (5), visit = 1,
                      payload = b"new payload")
        ok = self.c.send (s, s.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 3, ptype = bytes)
        self.assertEqual (p, s.payload)
        # Send to non-neighbor should fail (assuming neighbor does not
        # advertise intercept services)
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (44),
                         srcnode = Nodeid (5), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertFalse (ok)
        self.lastsent (self.cp, 3, ptype = bytes)
        
    def test_verify (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, NodeInit)
        self.assertEqual (p.nodename, b"TEST")
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x01\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up,
                          adjacent_node = NiceNode (66, "REMOTE"))
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (66))
        v, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (v, NodeVerify)
        self.assertEqual (bytes (v), b"\x58\x02\x00OVERIF\x00\x00")
        
    def test_ph3 (self):
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ri")
        self.lastsent (self.cp, 1)
        
    def test_ph4 (self):
        pkt = b"\x01\x02\x00\x02\x10\x02\x02\x00\x00\x20\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ri")
        self.lastsent (self.cp, 1)

    def test_rnd (self):
        self.c.restart = unittest.mock.Mock ()
        self.c.restart.return_value = None
        for i in range (rcount):
            pkt = randpkt (rmin, rmax)
            self.node.addwork (Received (owner = self.c, src = self.c,
                                       packet = pkt))
        
    def test_rndrun (self):
        self.startup ()
        self.test_rnd ()

    def test_short (self):
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.shortpackets (pkt)
        
class test_ph3 (rtest):
    phase = 3
    tiver = tiver_ph3
    ntype = L1ROUTER
    verify = False
    
    def startup (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit3)
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph3)
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (2))
        self.assertEqual (self.c.rphase, 3)
        self.assertEqual (self.c.id, Nodeid (2))
        self.assertEqual (self.c.ntype, 2)

    def test_noverify (self):
        self.startup ()
        pkt = b"\x02\x03\x00\x01\x00\x11abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, b"abcdef payload")
        self.assertEqual (spkt.srcnode, Nodeid (1))
        self.assertEqual (spkt.dstnode, Nodeid (3))
        self.assertEqual (spkt.visit, 17)
        DnTimeout (self.c)
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, PtpHello)
        self.assertEqual (p.srcnode, Nodeid (5))
        self.assertRegex (p.testdata, b"^\252+$")
        # Send some packets
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (2),
                         srcnode = Nodeid (1, 1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 3)
        self.assertIs (p, pkt)
        self.assertEqual (p.dstnode, Nodeid (2))
        self.assertEqual (p.srcnode, Nodeid (1, 1))
        # Try long data
        s = LongData (rqr = 1, rts = 0, ie = 1, dstnode = Nodeid (2),
                      srcnode = Nodeid (4, 1), visit = 1,
                      payload = b"new payload")
        ok = self.c.send (s, s.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 4)
        self.assertIsInstance (p, ShortData)
        self.assertEqual (p.rqr, s.rqr)
        self.assertEqual (p.rts, s.rts)
        self.assertEqual (p.dstnode, Nodeid (2))
        self.assertEqual (p.srcnode, Nodeid (4, 1))
        self.assertEqual (p.visit, s.visit)
        self.assertEqual (p.payload, s.payload)
        self.assertFalse (p.ie)
        # Send to non-neighbor should succeed since neighbor is router
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (44),
                         srcnode = Nodeid (1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 5)
        self.assertIs (p, pkt)
        # test listen timeout
        DnTimeout (self.c.adj)
        self.assertState ("ds")
        self.assertEqual (self.c.datalink.counters.cir_down, 1)
        self.assertEvent (events.circ_down, reason = "listener_timeout",
                          adjacent_node = NiceNode (2))
        
    def test_verify (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit3)
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph3)
        pkt = b"\x01\x02\x00\x07\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (2))
        self.assertEqual (self.c.rphase, 3)
        self.assertEqual (self.c.id, Nodeid (2))
        self.assertEqual (self.c.ntype, ENDNODE)  # 3
        v, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (v, PtpVerify)
        self.assertEqual (v.srcnode, Nodeid (5))
        self.assertEqual (v.fcnval, b"OVERIF")
        # Send some packets
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (2),
                         srcnode = Nodeid (1, 1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 3)
        self.assertIs (p, pkt)
        self.assertEqual (p.dstnode, pkt.dstnode)
        self.assertEqual (p.srcnode, Nodeid (1, 1))
        # Try long data
        s = LongData (rqr = 1, rts = 0, ie = 1, dstnode = Nodeid (2),
                      srcnode = Nodeid (4, 1), visit = 1,
                      payload = b"new payload")
        ok = self.c.send (s, s.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 4)
        self.assertIsInstance (p, ShortData)
        self.assertEqual (p.rqr, s.rqr)
        self.assertEqual (p.rts, s.rts)
        self.assertEqual (p.dstnode, s.dstnode)
        self.assertEqual (p.srcnode, Nodeid (4, 1))
        self.assertEqual (p.visit, s.visit)
        self.assertEqual (p.payload, s.payload)
        self.assertFalse (p.ie)
        # Send to non-neighbor should fail
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (44),
                         srcnode = Nodeid (1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertFalse (ok)
        self.lastsent (self.cp, 4)

    def test_ph2 (self):
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, NodeInit)
        self.assertEqual (p.nodename, b"TEST")
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        self.assertState ("ru")
        self.assertEvent (events.circ_up,
                          adjacent_node = NiceNode (66, "REMOTE"))
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (66))
        # Deliver an incoming packet
        pkt = b"\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, pkt)
        self.assertEqual (spkt.srcnode, Nodeid (66))
        self.assertEqual (spkt.dstnode, self.r.nodeid)
        self.assertEqual (spkt.visit, 1)
        # Deliver an incoming packet, with route header
        pkt = b"\x42\x05LOCAL\x06REMOTE\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (2, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, b"\x08\252\252\252")
        self.assertEqual (spkt.srcnode, Nodeid (66))
        self.assertEqual (spkt.dstnode, self.r.nodeid)
        self.assertEqual (spkt.visit, 1)
        # Hello timer expiration
        DnTimeout (self.c)
        p, x = self.lastsent (self.cp, 3)
        self.assertIsInstance (p, NopMsg)
        self.assertRegex (p.payload, b"^\252+$")
        # Send some packets
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (66),
                         srcnode = Nodeid (5), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 4, ptype = bytes)
        self.assertEqual (p, pkt.payload)
        # Try long data
        s = LongData (rqr = 1, rts = 0, ie = 1, dstnode = Nodeid (66),
                      srcnode = Nodeid (5), visit = 1,
                      payload = b"new payload")
        ok = self.c.send (s, s.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 5, ptype = bytes)
        self.assertEqual (p, s.payload)
        # Send to non-neighbor should fail (for now)
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (44),
                         srcnode = Nodeid (5), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertFalse (ok)
        self.lastsent (self.cp, 5, ptype = bytes)
        
    def test_ph4 (self):
        pkt = b"\x01\x02\x00\x02\x10\x02\x02\x00\x00\x20\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ri")
        self.lastsent (self.cp, 1)
        
    def test_rnd (self):
        self.c.restart = unittest.mock.Mock ()
        self.c.restart.return_value = None
        for i in range (rcount):
            pkt = randpkt (rmin, rmax)
            self.node.addwork (Received (owner = self.c, src = self.c,
                                       packet = pkt))

    def test_rndrun (self):
        self.startup ()
        self.test_rnd ()
        
    def test_short (self):
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.shortpackets (pkt)
        
class test_ph4 (rtest):
    phase = 4
    tiver = tiver_ph4
    ntype = L2ROUTER
    verify = False
    
    def startup (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))

    def test_noverify (self):
        self.startup ()
        pkt = b"\x02\x03\x04\x01\x08\x11abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, b"abcdef payload")
        self.assertEqual (spkt.srcnode, Nodeid (2, 1))
        self.assertEqual (spkt.dstnode, Nodeid (1, 3))
        self.assertEqual (spkt.visit, 17)
        # ditto but with padding
        pkt = b"\x88Testing\x02\x03\x04\x01\x08\x11abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (2, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, b"abcdef payload")
        self.assertEqual (spkt.srcnode, Nodeid (2, 1))
        self.assertEqual (spkt.dstnode, Nodeid (1, 3))
        self.assertEqual (spkt.visit, 17)
        # Long data is accepted
        pkt = b"\x26\x00\x00\xaa\x00\x04\x00\x03\x04" \
              b"\x00\x00\xaa\x00\x04\x00\x01\x08\x00\x11\x00\x00" \
              b"abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (3, element = self.r)
        self.assertIsInstance (spkt, LongData)
        self.assertEqual (spkt.payload, b"abcdef payload")
        self.assertEqual (spkt.srcnode, Nodeid (2, 1))
        self.assertEqual (spkt.dstnode, Nodeid (1, 3))
        self.assertEqual (spkt.visit, 17)
        # ditto but with padding
        pkt = b"\x88Testing\x26\x00\x00\xaa\x00\x04\x00\x03\x04" \
              b"\x00\x00\xaa\x00\x04\x00\x01\x08\x00\x11\x00\x00" \
              b"abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (4, element = self.r)
        self.assertIsInstance (spkt, LongData)
        self.assertEqual (spkt.payload, b"abcdef payload")
        self.assertEqual (spkt.srcnode, Nodeid (2, 1))
        self.assertEqual (spkt.dstnode, Nodeid (1, 3))
        self.assertEqual (spkt.visit, 17)
        # test hello transmission
        DnTimeout (self.c)
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, PtpHello)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertRegex (p.testdata, b"^\252+$")
        # Send some packets
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (2, 2),
                         srcnode = Nodeid (1, 1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 3)
        self.assertIs (p, pkt)
        # Try long data
        s = LongData (rqr = 1, rts = 0, ie = 1, dstnode = Nodeid (2, 2),
                      srcnode = Nodeid (1, 1), visit = 1,
                      payload = b"new payload")
        ok = self.c.send (s, s.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 4)
        self.assertIsInstance (p, ShortData)
        self.assertEqual (p.rqr, s.rqr)
        self.assertEqual (p.rts, s.rts)
        self.assertEqual (p.srcnode, s.srcnode)
        self.assertEqual (p.dstnode, s.dstnode)
        self.assertEqual (p.visit, s.visit)
        self.assertEqual (p.payload, s.payload)
        self.assertFalse (p.ie)
        # Send to non-neighbor should succeed since neighbor is router
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (9, 44),
                         srcnode = Nodeid (1, 1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 5)
        self.assertIs (p, pkt)
        # test listen timeout
        DnTimeout (self.c.adj)
        self.assertState ("ds")
        self.assertEqual (self.c.datalink.counters.cir_down, 1)
        self.assertEvent (events.circ_down, reason = "listener_timeout",
                          adjacent_node = NiceNode (1026))
        # test restart after circuit down
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        DnTimeout (self.c)
        self.assertState ("ds")
        
    def test_verify (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x07\x10\x02\x02\x00\x00\x10\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        self.assertEqual (self.c.ntype, ENDNODE)  # 3
        v, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (v, PtpVerify)
        self.assertEqual (v.srcnode, Nodeid (1, 5))
        self.assertEqual (v.fcnval, b"OVERIF")
        # Send some packets
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (1, 2),
                         srcnode = Nodeid (1, 1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 3)
        self.assertIs (p, pkt)
        # Try long data
        s = LongData (rqr = 1, rts = 0, ie = 1, dstnode = Nodeid (1, 2),
                      srcnode = Nodeid (1, 1), visit = 1,
                      payload = b"new payload")
        ok = self.c.send (s, s.dstnode)
        self.assertTrue (ok)
        p, dest = self.lastsent (self.cp, 4)
        self.assertIsInstance (p, ShortData)
        self.assertEqual (p.rqr, s.rqr)
        self.assertEqual (p.rts, s.rts)
        self.assertEqual (p.srcnode, s.srcnode)
        self.assertEqual (p.dstnode, s.dstnode)
        self.assertEqual (p.visit, s.visit)
        self.assertEqual (p.payload, s.payload)
        self.assertFalse (p.ie)
        # Send to non-neighbor should fail
        pkt = ShortData (rqr = 1, rts = 0, dstnode = Nodeid (9, 44),
                         srcnode = Nodeid (1, 1), visit = 1,
                         payload = b"new payload")
        ok = self.c.send (pkt, pkt.dstnode)
        self.assertFalse (ok)
        self.lastsent (self.cp, 4)

    def test_ph2 (self):
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, NodeInit)
        self.assertEqual (p.nodename, b"TEST")
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        self.assertState ("ru")
        self.assertEvent (events.circ_up,
                          adjacent_node = NiceNode (1090, "REMOTE"))
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (1, 66))
        # Deliver an incoming packet
        pkt = b"\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, pkt)
        self.assertEqual (spkt.srcnode, Nodeid (1, 66))
        self.assertEqual (spkt.dstnode, self.r.nodeid)
        self.assertEqual (spkt.visit, 1)
        # Deliver an incoming packet, with route header
        pkt = b"\x42\x05LOCAL\x06REMOTE\x08\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (2, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, b"\x08\252\252\252")
        self.assertEqual (spkt.srcnode, Nodeid (1, 66))
        self.assertEqual (spkt.dstnode, self.r.nodeid)
        self.assertEqual (spkt.visit, 1)
        # Hello timer expiration
        DnTimeout (self.c)
        p, x = self.lastsent (self.cp, 3)
        self.assertIsInstance (p, NopMsg)
        self.assertRegex (p.payload, b"^\252+$")
        
    def test_ph3 (self):
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, PtpInit3)
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph3)
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 3)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        pkt = b"\x02\x03\x00\x01\x00\x11abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertIsInstance (spkt, ShortData)
        self.assertEqual (spkt.payload, b"abcdef payload")
        self.assertEqual (spkt.srcnode, Nodeid (1, 1))
        self.assertEqual (spkt.dstnode, Nodeid (1, 3))
        self.assertEqual (spkt.visit, 17)
        # Hello timer expiration
        DnTimeout (self.c)
        p, x = self.lastsent (self.cp, 3)
        self.assertIsInstance (p, PtpHello)
        self.assertEqual (p.srcnode, Nodeid (5))
        self.assertRegex (p.testdata, b"^\252+$")
        # Packet with padding should be ignored
        pkt = b"\x88Testing\x02\x03\x04\x01\x08\x11abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.lastdispatch (1, element = self.r)
        self.assertEvent (events.fmt_err, 
                          packet_beginning = b"\x88Testi")
        # Long data with padding
        pkt = b"\x88Testing\x26\x00\x00\xaa\x00\x04\x00\x03\x04" \
              b"\x00\x00\xaa\x00\x04\x00\x01\x08\x00\x11\x00\x00" \
              b"abcdef payload"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        spkt = self.lastdispatch (1, element = self.r)
        self.assertEvent (events.fmt_err, 
                          packet_beginning = b"\x88Testi")

    def test_phx (self):
        pkt = b"\x01\x02\x00\x02\x10\x02\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ri")
        self.lastsent (self.cp, 1)
        
    def test_rnd (self):
        self.c.restart = unittest.mock.Mock ()
        self.c.restart.return_value = None
        for i in range (rcount):
            pkt = randpkt (rmin, rmax)
            self.node.addwork (Received (owner = self.c, src = self.c,
                                       packet = pkt))
        
    def test_rndrun (self):
        self.startup ()
        self.test_rnd ()
        
    def test_short (self):
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.shortpackets (pkt)

class test_upd1 (rtest):
    phase = 4
    tiver = tiver_ph4
    ntype = L1ROUTER
    verify = False
    
    def startup (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        # Neighbor 1.2, L2 router
        pkt = b"\x01\x02\x04\x01\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))

    def test_upd_inarea (self):
        self.startup ()
        # Run L1 Update
        DnTimeout (self.c.update)
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, L1Routing)
        self.assertTrue (self.c.update.islinked ())
        if self.ntype == L2ROUTER:
            # Run L2 Update
            DnTimeout (self.c.aupdate)
            p, x = self.lastsent (self.cp, 3)
            self.assertIsInstance (p, L2Routing)
            self.assertTrue (self.c.aupdate.islinked ())

class test_upd2 (test_upd1):
    ntype = L2ROUTER

    def test_upd_outarea (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        # Node 2.2, L2 router
        pkt = b"\x01\x02\x08\x01\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (2050))
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (2, 2))
        # Run L1 Update
        DnTimeout (self.c.update)
        # Nothing should be sent
        p, x = self.lastsent (self.cp, 1)
        # Timer is not (re)started
        self.assertFalse (self.c.update.islinked ())
        # Run L2 Update
        DnTimeout (self.c.aupdate)
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, L2Routing)
        self.assertTrue (self.c.aupdate.islinked ())
        
    def test_upd_endnode (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        # Node 1.2, endnode
        pkt = b"\x01\x02\x04\x03\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        # Run L1 Update
        DnTimeout (self.c.update)
        # Nothing should be sent
        p, x = self.lastsent (self.cp, 1)
        # Update timer is not (re)started
        self.assertFalse (self.c.update.islinked ())
        # Run L2 Update
        DnTimeout (self.c.aupdate)
        # Nothing should be sent
        p, x = self.lastsent (self.cp, 1)
        # Update timer is not (re)started
        self.assertFalse (self.c.aupdate.islinked ())

    def test_upd_l1router (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        # Node 1.2, l1 router
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        # Run L1 Update
        DnTimeout (self.c.update)
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, L1Routing)
        self.assertTrue (self.c.update.islinked ())
        # Run L2 Update
        DnTimeout (self.c.aupdate)
        # Nothing should be sent
        p, x = self.lastsent (self.cp, 2)
        # Update timer is not (re)started
        self.assertFalse (self.c.aupdate.islinked ())

    def test_upd_phase3 (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        # Node 2, phase 3 router
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, PtpInit3)
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 3)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        # Run L1 Update
        DnTimeout (self.c.update)
        p, x = self.lastsent (self.cp, 3)
        self.assertIsInstance (p, PhaseIIIRouting)
        self.assertTrue (self.c.update.islinked ())
        # Run L2 Update
        DnTimeout (self.c.aupdate)
        # Nothing should be sent
        p, x = self.lastsent (self.cp, 3)
        # Update timer is not (re)started
        self.assertFalse (self.c.aupdate.islinked ())

    def test_upd_phase2 (self):
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, NodeInit)
        self.assertEqual (p.nodename, b"TEST")
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 0)
        self.assertState ("ru")
        self.assertEvent (events.circ_up,
                          adjacent_node = NiceNode (1090, "REMOTE"))
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (1, 66))
        # Run L1 Update
        DnTimeout (self.c.update)
        # Nothing should be sent
        p, x = self.lastsent (self.cp, 2)
        self.assertFalse (self.c.update.islinked ())
        # Run L2 Update
        DnTimeout (self.c.aupdate)
        # Nothing should be sent
        p, x = self.lastsent (self.cp, 2)
        # Update timer is not (re)started
        self.assertFalse (self.c.aupdate.islinked ())
        
class test_ph4verify (rtest):
    phase = 4
    tiver = tiver_ph4
    ntype = L2ROUTER
    verify = True
    
    def test_noverify (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 1)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("rv")
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        pkt = b"\x03\x02\x04\x06IVERIF"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        
    def test_wrongverify (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 1)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("rv")
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        pkt = b"\x03\x02\x04\x06ZVERIF"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")
        self.assertEvent (events.ver_rej, reason = "invalid_verification",
                          adjacent_node = NiceNode (1026))
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        DnTimeout (self.c)
        self.assertState ("ds")
        
    def test_verify_timeout (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 1)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("rv")
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        DnTimeout (self.c)
        self.assertState ("ds")
        self.assertEvent (events.init_fault, reason = "verification_timeout",
                          adjacent_node = NiceNode (1026))
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        self.assertState ("ds")

    def test_verify (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 1)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x06\x10\x02\x02\x00\x00\x10\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("rv")
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        v, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (v, PtpVerify)
        self.assertEqual (v.srcnode, Nodeid (1, 5))
        self.assertEqual (v.fcnval, b"OVERIF")
        pkt = b"\x03\x02\x04\x06IVERIF"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))

    def test_ph2 (self):
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, NodeInit)
        self.assertEqual (p.nodename, b"TEST")
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 1)
        self.assertState ("rv")
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (1, 66))
        pkt = b"\x58\x02\x00IVERIF\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up,
                          adjacent_node = NiceNode (1090, "REMOTE"))
        
    def test_ph3 (self):
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        p, x = self.lastsent (self.cp, 2)
        self.assertIsInstance (p, PtpInit3)
        self.assertEqual (p.srcnode, 5)
        self.assertEqual (p.verif, 1)
        self.assertEqual (p.tiver, tiver_ph3)
        self.assertState ("rv")
        self.assertEqual (self.c.rphase, 3)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        pkt = b"\x03\x02\x00\x06IVERIF"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))

    def test_rndrv (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 1)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("rv")
        self.c.restart = unittest.mock.Mock ()
        self.c.restart.return_value = None
        for i in range (rcount):
            pkt = randpkt (rmin, rmax)
            self.node.addwork (Received (owner = self.c, src = self.c,
                                       packet = pkt))
        
class test_ph4err (rtest):
    phase = 4
    tiver = tiver_ph4
    ntype = L2ROUTER
    verify = False

    def test_zero (self):
        pkt = b"\x01\x00\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_padinit (self):
        pkt = b"\x88Testing\x01\x00\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.fmt_err, 
                          packet_beginning = b"\x88Testi")
        self.assertState ("ri")

    def test_oor (self):
        if self.ntype != ENDNODE:
            pkt = b"\x01\xc9\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
            self.node.addwork (Received (owner = self.c,
                                       src = self.c, packet = pkt))
            self.assertState ("ds")

    def test_azero (self):
        pkt = b"\x01\x02\x00\x01\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_aoor (self):
        pkt = b"\x01\x02\x29\x01\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_wrongareal1 (self):
        pkt = b"\x01\x02\x08\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_wrongarea_end (self):
        pkt = b"\x01\x02\x08\x03\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_ph3zero (self):
        pkt = b"\x01\x00\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_ph3oor (self):
        if self.ntype != ENDNODE:
            pkt = b"\x01\xc9\x00\x02\x10\x02\x01\x03\x00\x00"
            self.node.addwork (Received (owner = self.c,
                                       src = self.c, packet = pkt))
            self.assertState ("ds")

    def test_ph3smlblk (self):
        pkt = b"\x01\xc0\x00\x01\x80\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_ph3area (self):
        pkt = b"\x01\x00\x04\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_ntype (self):
        pkt = b"\x01\x03\x04\x00\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")
        
    def test_ph3ntype (self):
        pkt = b"\x01\x03\x00\x01\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

# For L1 router, all the above errors are also errors, plus any area mismatch
class test_ph4l1err (test_ph4err):
    phase = 4
    tiver = tiver_ph4
    ntype = L1ROUTER
    verify = False
    
    def test_wrongarea (self):
        pkt = b"\x01\x02\x08\x01\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

# Endnode error cases are the same as L1 router
class test_ph4end_err (test_ph4l1err):
    phase = 4
    tiver = tiver_ph4
    ntype = ENDNODE
    verify = False

class test_ph3err (rtest):
    phase = 3
    tiver = tiver_ph3
    ntype = L1ROUTER
    verify = False

    def test_zero (self):
        pkt = b"\x01\x00\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_oor (self):
        if self.ntype != ENDNODE:
            pkt = b"\x01\xc9\x00\x02\x10\x02\x01\x03\x00\x00"
            self.node.addwork (Received (owner = self.c,
                                       src = self.c, packet = pkt))
            self.assertState ("ds")

    def test_ph3smlblk (self):
        pkt = b"\x01\xc0\x00\x01\x80\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_area (self):
        pkt = b"\x01\x00\x04\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_ntype (self):
        pkt = b"\x01\x03\x04\x01\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_padinit (self):
        pkt = b"\x88Testing\x01\x00\x04\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.fmt_err, 
                          packet_beginning = b"\x88Testi")
        self.assertState ("ri")

class test_ph3end_err (test_ph3err):
    phase = 3
    tiver = tiver_ph3
    ntype = ENDNODE
    verify = False

class test_ph2err (rtest):
    phase = 2
    tiver = tiver_ph2
    ntype = PHASE2
    verify = False
    
    def test_zero (self):
        pkt = b"\x58\x01\x00\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

    def test_oor (self):
        pkt = b"\x58\x01\x80\x39\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")

class test_ph4restart (rtest):
    phase = 4
    tiver = tiver_ph4
    ntype = L2ROUTER
    verify = False
    
    def startup (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 0)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEvent (events.circ_up, adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))

    def test_dlrestart (self):
        self.startup ()
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        self.assertState ("ds")
        self.assertEvent (events.circ_fault, reason = "sync_lost",
                          adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.datalink.counters.cir_down, 1)
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        DnTimeout (self.c)
        self.assertState ("ds")
        
    def test_init (self):
        self.startup ()
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x20\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.init_swerr,
                          reason = "unexpected_packet_type",
                          packet_header = [ 1, 1026 ],
                          adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.datalink.counters.cir_down, 1)
        self.assertState ("ds")
        
    def test_shortinit (self):
        self.startup ()
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x20\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.fmt_err,
                          packet_beginning = b"\x01\x02\x04\x02\x10\x02")
        self.assertEqual (self.c.datalink.counters.cir_down, 1)
        self.assertState ("ds")
        
    def test_init3 (self):
        self.startup ()
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.init_swerr,
                          reason = "unexpected_packet_type",
                          packet_header = [ 1, 2 ],
                          adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.datalink.counters.cir_down, 1)
        self.assertState ("ds")
        
    def test_init2 (self):
        self.startup ()
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.init_swerr,
                          reason = "unexpected_packet_type",
                          ni_packet_header = [ 0x58, 1, 66, "REMOTE" ],
                          adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.datalink.counters.cir_down, 1)
        self.assertState ("ds")
        
    def test_init_workaround (self):
        self.cp.start_works = False
        self.startup ()
        pkt = b"\x05\x02\x04\x05\252\252\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x20\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        self.assertEqual (self.c.datalink.counters.cir_down, 1)

    def test_init3_workaround (self):
        self.cp.start_works = False
        self.startup ()
        pkt = b"\x05\x02\x04\x05\252\252\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEqual (self.c.rphase, 3)
        self.assertEqual (self.c.id, Nodeid (1, 2))
        self.assertEqual (self.c.datalink.counters.cir_down, 1)

    def test_init2_workaround (self):
        self.cp.start_works = False
        self.startup ()
        pkt = b"\x05\x02\x04\x05\252\252\252\252\252"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ru")
        self.assertEqual (self.c.rphase, 2)
        self.assertEqual (self.c.id, Nodeid (1, 66))
        self.assertEqual (self.c.datalink.counters.cir_down, 1)

    def test_ri_restart (self):
        pkt = b"\x03\x02\x04\x06IVERIF"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.init_swerr, reason = "unexpected_packet_type",
                          packet_header = [ 3, Nodeid (1, 2) ])
        self.assertState ("ds")

    def test_ri_restart_ph2 (self):
        pkt = b"\x58\x02\x00IVERIF\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.init_swerr, reason = "unexpected_packet_type",
                          nv_packet_header = [ 0x58, 2 ])
        self.assertState ("ds")

    def test_badhello (self):
        self.startup ()
        pkt = b"\x05\x02\x04\x05\252\252\252\252\251"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertEvent (events.circ_down, reason = "listener_invalid_data",
                          adjacent_node = NiceNode (1026),
                          packet_header = [ 5, 1026 ])
        self.assertState ("ds")
        
class test_ph4restart_rv (rtest):
    phase = 4
    tiver = tiver_ph4
    ntype = L2ROUTER
    verify = True

    def startup (self):
        p, x = self.lastsent (self.cp, 1)
        self.assertIsInstance (p, PtpInit)
        self.assertEqual (p.srcnode, Nodeid (1, 5))
        self.assertEqual (p.verif, 1)
        self.assertEqual (p.tiver, tiver_ph4)
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x0a\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("rv")
        self.assertEqual (self.c.rphase, 4)
        self.assertEqual (self.c.id, Nodeid (1, 2))

    def test_dlrestart (self):
        self.startup ()
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        self.assertEvent (events.init_fault, reason = "sync_lost",
                          adjacent_node = NiceNode (Nodeid (1, 2)))
        self.assertEqual (self.c.datalink.counters.init_fail, 1)
        self.assertState ("ds")
        self.node.addwork (datalink.DlStatus (owner = self.c,
                                            status = datalink.DlStatus.DOWN))
        DnTimeout (self.c)
        self.assertState ("ds")
        
    def test_init (self):
        self.startup ()
        pkt = b"\x01\x02\x04\x02\x10\x02\x02\x00\x00\x20\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")
        self.assertEvent (events.init_swerr, reason = "unexpected_packet_type",
                          packet_header = [ 1, 1026 ],
                          adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.datalink.counters.init_fail, 1)
        
    def test_init3 (self):
        self.startup ()
        pkt = b"\x01\x02\x00\x02\x10\x02\x01\x03\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")
        self.assertEvent (events.init_swerr, reason = "unexpected_packet_type",
                          packet_header = [ 1, 2 ],
                          adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.datalink.counters.init_fail, 1)
        
    def test_init2 (self):
        self.startup ()
        pkt = b"\x58\x01\x42\x06REMOTE\x00\x00\x04\x02\x01\x02\x40\x00" \
              b"\x00\x00\x00\x03\x01\x00\x00"
        self.node.addwork (Received (owner = self.c, src = self.c, packet = pkt))
        self.assertState ("ds")
        self.assertEvent (events.init_swerr, reason = "unexpected_packet_type",
                          ni_packet_header = [ 0x58, 1, 66, "REMOTE" ],
                          adjacent_node = NiceNode (1026))
        self.assertEqual (self.c.datalink.counters.init_fail, 1)
        
if __name__ == "__main__":
    unittest.main ()
