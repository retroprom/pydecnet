#!/usr/bin/env python3

"""Unit test for the NSP layer.
"""

from tests.dntest import *
from decnet import nsp
from decnet import routing
from decnet import logging

class ntest (DnTest):
    phase = 4
    
    def setUp (self):
        super ().setUp ()
        self.node.phase = 4
        self.config = container ()
        self.config.nsp = container ()
        self.config.nsp.max_connections = 511
        self.config.nsp.nsp_delay = 3
        self.config.nsp.nsp_weight = 3
        self.node.routing = unittest.mock.Mock ()
        self.node.session = unittest.mock.Mock ()
        self.nsp = nsp.NSP (self.node, self.config)
        #self.setloglevel (logging.TRACE)
        self.nsp.start ()

class inbound_base (ntest):
    services = b'\x01'   # Services, which carries FCOPT in bits 2-3
    info = b'\x02'       # Info, which carries NSP version in bits 0-1
    remnode = Nodeid (1, 42)
    cdadj = 1            # Outbound packet adjustment because of CD
    
    def setUp (self):
        super ().setUp ()
        r = self.node.routing
        s = self.node.session
        # Connect Init, flow control and version from class attributes
        # Sender's link address is 3, segsize is 0x204, i.e., 516.
        p = b"\x18\x00\x00\x03\x00" + self.services + self.info + \
            b"\x04\x02payload"
        rla = 3
        w = Received (owner = self.nsp, src = self.remnode,
                      packet = p, rts = False)
        self.nsp.dispatch (w)
        # Check reply
        self.assertEqual (r.send.call_count, self.cdadj)
        if self.cdadj:
            args, kwargs = r.send.call_args
            ack, dest = args
            self.assertEqual (dest, self.remnode)
            self.assertEqual (ack.dstaddr, rla)
        # Check data to Session Control
        self.assertEqual (self.node.addwork.call_count, 1)
        args, kwargs = self.node.addwork.call_args
        w, owner = args
        pkt = w.packet
        self.assertIsInstance (pkt, nsp.ConnInit)
        self.assertEqual (pkt.payload, b"payload")
        # Check connection state
        nc = w.connection
        self.assertIs (self.nsp.rconnections[(self.remnode, rla)], nc)
        self.assertEqual (nc.dstaddr, rla)
        lla = nc.srcaddr
        self.assertIs (self.nsp.connections[lla], nc)
        self.assertEqual (nc.state, nc.cr)
        # Remember the connection
        self.nspconn = nc
        # SC send accept
        nc.accept (b"excellent")
        # Verify confirm went out
        r = self.node.routing
        self.assertEqual (r.send.call_count, 1 + self.cdadj)
        args, kwargs = r.send.call_args
        cc, dest = args
        self.assertIsInstance (cc, nsp.ConnConf)
        self.assertEqual (dest, self.remnode)
        self.assertEqual (cc.srcaddr, nc.srcaddr)
        self.assertEqual (cc.dstaddr, 3)
        self.assertEqual (cc.data_ctl, b"excellent")
        # Check new connection state
        self.assertEqual (nc.state, nc.cc)
        
    def test_normalconn (self):
        # Basic good inbound connection (accept, data, disconnect)
        nc = self.nspconn
        lla = nc.srcaddr
        rla = 3
        r = self.node.routing
        s = self.node.session        
        # Send a data segment
        d = b"\x60" + lla.to_bytes (2, "little") + b"\x03\x00\x01\x00data payload"
        w = Received (owner = self.nsp, src = self.remnode,
                      packet = d, rts = False)
        self.nsp.dispatch (w)
        # Check data to Session Control
        self.assertEqual (self.node.addwork.call_count, 2)
        args, kwargs = self.node.addwork.call_args
        w, owner = args
        pkt = w.packet
        self.assertIsInstance (pkt, nsp.DataSeg)
        self.assertEqual (pkt.payload, b"data payload")
        # Check new connection state
        self.assertEqual (nc.state, nc.run)
        # Check inactivity timer active, if Phase 3 or later
        if self.cdadj:
            self.assertTrue (nc.islinked ())
        else:
            self.assertFalse (nc.islinked ())
        # No reply yet
        self.assertEqual (r.send.call_count, 1 + self.cdadj)
        # Send a data message
        nc.send_data (b"hello world")
        # Verify data was sent, with piggyback ack
        self.assertEqual (r.send.call_count, 2 + self.cdadj)
        args, kwargs = r.send.call_args
        ds, dest = args
        self.assertIsInstance (ds, nsp.DataSeg)
        self.assertEqual (dest, self.remnode)
        self.assertEqual (ds.srcaddr, lla)
        self.assertEqual (ds.dstaddr, rla)
        self.assertTrue (ds.bom)
        self.assertTrue (ds.eom)
        self.assertEqual (ds.acknum, nsp.AckNum (1))
        self.assertFalse (hasattr (ds, "acknum2"))
        self.assertEqual (ds.payload, b"hello world")
        # Send a big data message (11 * 80 bytes, two segments)
        nc.send_data (b"hello world" * 80)
        # Verify data was sent, without piggyback ack
        self.assertEqual (r.send.call_count, 4 + self.cdadj)
        d1, d2 = r.send.call_args_list[-2:]
        args, kwargs = d1
        ds, dest = args
        self.assertIsInstance (ds, nsp.DataSeg)
        self.assertEqual (dest, self.remnode)
        self.assertEqual (ds.srcaddr, lla)
        self.assertEqual (ds.dstaddr, rla)
        self.assertTrue (ds.bom)
        self.assertFalse (ds.eom)
        self.assertFalse (hasattr (ds, "acknum"))
        self.assertFalse (hasattr (ds, "acknum2"))
        args, kwargs = d2
        ds2, dest = args
        self.assertIsInstance (ds2, nsp.DataSeg)
        self.assertEqual (dest, self.remnode)
        self.assertEqual (ds2.srcaddr, lla)
        self.assertEqual (ds2.dstaddr, rla)
        self.assertFalse (ds2.bom)
        self.assertTrue (ds2.eom)
        self.assertFalse (hasattr (ds2, "acknum"))
        self.assertFalse (hasattr (ds2, "acknum2"))
        self.assertEqual (ds.payload + ds2.payload, b"hello world" * 80)
        # Inbound disconnect
        disc = b"\x38" + lla.to_bytes (2, "little") + \
               b"\x03\x00\x05\x00\x07payload"
        w = Received (owner = self.nsp, src = self.remnode,
                      packet = disc, rts = False)
        self.nsp.dispatch (w)
        # Check data to Session Control
        self.assertEqual (self.node.addwork.call_count, 3)
        args, kwargs = self.node.addwork.call_args
        w, owner = args
        pkt = w.packet
        self.assertIsInstance (pkt, nsp.DiscInit)
        self.assertEqual (pkt.data_ctl, b"payload")
        self.assertEqual (pkt.reason, 5)
        # Check new connection state, and that there no longer is an
        # NSP connection in its database.
        self.assertEqual (nc.state, nc.closed)
        self.assertEqual (len (self.nsp.connections), 0)
        self.assertEqual (len (self.nsp.rconnections), 0)

class test_inbound_noflow_phase4 (inbound_base):
    def test_interrupt (self):
        nc = self.nspconn
        lla = nc.srcaddr
        rla = 3
        r = self.node.routing
        s = self.node.session        
        # Try to send an interrupt outbound
        with self.assertRaises (nsp.WrongState):
            nc.interrupt (b"frob")
        # Incoming ACK (of the connect confirm)
        ack = b"\x04" + lla.to_bytes (2, "little") + b"\x03\x00\x00\x80"
        w = Received (owner = self.nsp, src = self.remnode,
                      packet = ack, rts = False)
        self.nsp.dispatch (w)
        # That should get us into RUN state
        self.assertEqual (nc.state, nc.run)
        nc.interrupt (b"hello decnet")
        # Verify data was sent
        self.assertEqual (r.send.call_count, 2 + self.cdadj)
        args, kwargs = r.send.call_args
        ds, dest = args
        self.assertIsInstance (ds, nsp.IntMsg)
        self.assertEqual (dest, self.remnode)
        self.assertEqual (ds.srcaddr, lla)
        self.assertEqual (ds.dstaddr, rla)
        self.assertTrue (ds.int)
        self.assertFalse (hasattr (ds, "acknum"))
        self.assertFalse (hasattr (ds, "acknum2"))
        self.assertEqual (ds.payload, b"hello decnet")
        # Incoming interrupt, with piggyback ACK
        p = b"\x30" + lla.to_bytes (2, "little") + \
            b"\x03\x00\x01\x80\x01\x00payload"
        w = Received (owner = self.nsp, src = self.remnode,
                      packet = p, rts = False)
        self.nsp.dispatch (w)
        self.assertEqual (self.node.addwork.call_count, 2)
        args, kwargs = self.node.addwork.call_args
        w, owner = args
        pkt = w.packet
        self.assertIsInstance (pkt, nsp.IntMsg)
        self.assertEqual (pkt.payload, b"payload")
        # Try sending another interrupt
        with self.assertRaises (nsp.CantSend):
            nc.interrupt (b"frob again")
        # Incoming Link Service to ask for more interrupts
        p = b"\x10" + lla.to_bytes (2, "little") + \
            b"\x03\x00\x02\x00\x06\x02"
        w = Received (owner = self.nsp, src = self.remnode,
                      packet = p, rts = False)
        self.nsp.dispatch (w)
        # Not delivered to Session Control
        self.assertEqual (self.node.addwork.call_count, 2)
        # Send a second interrupt
        nc.interrupt (b"interrupt 2")
        # Verify data was sent
        self.assertEqual (r.send.call_count, 3 + self.cdadj)
        args, kwargs = r.send.call_args
        ds, dest = args
        self.assertIsInstance (ds, nsp.IntMsg)
        self.assertEqual (dest, self.remnode)
        self.assertEqual (ds.srcaddr, lla)
        self.assertEqual (ds.dstaddr, rla)
        self.assertTrue (ds.int)
        self.assertEqual (ds.acknum, nsp.AckNum (2))
        self.assertFalse (hasattr (ds, "acknum2"))
        self.assertEqual (ds.payload, b"interrupt 2")
        # Send a third interrupt
        nc.interrupt (b"interrupt 3")
        # Verify data was sent
        self.assertEqual (r.send.call_count, 4 + self.cdadj)
        args, kwargs = r.send.call_args
        ds, dest = args
        self.assertIsInstance (ds, nsp.IntMsg)
        self.assertEqual (dest, self.remnode)
        self.assertEqual (ds.srcaddr, lla)
        self.assertEqual (ds.dstaddr, rla)
        self.assertTrue (ds.int)
        self.assertFalse (hasattr (ds, "acknum"))
        self.assertFalse (hasattr (ds, "acknum2"))
        self.assertEqual (ds.payload, b"interrupt 3")
        # A fourth one should be refused
        with self.assertRaises (nsp.CantSend):
            nc.interrupt (b"frob again")

class test_inbound_noflow_phase3 (test_inbound_noflow_phase4):
    info = b'\x00'       # NSP 3.2 (phase 3)
    remnode = Nodeid (42)

class test_inbound_noflow_phase2 (test_inbound_noflow_phase3):
    info = b'\x01'       # NSP 3.1 (phase 2)
    cdadj = 0
    
class test_random (ntest):
    def test_random (self):
        src = Nodeid (1, 42)
        for i in range (5000):
            pkt = randpkt (8, 64)
            w = Received (owner = self.nsp, src = src,
                          packet = pkt, rts = False)
            self.nsp.dispatch (w)
            
if __name__ == "__main__":
    unittest.main ()
