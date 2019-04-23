#!

"""DECnet/Python access via HTTP

"""

import http.server
import socketserver
import json
import traceback
import io
import os.path
from urllib.parse import urlparse, parse_qs
import re
import ssl
import mimetypes
import time
from datetime import timedelta
import subprocess

from .common import *
from . import logging
from . import html

resourcedir = os.path.join (os.path.dirname (__file__), "resources")

# We want to show the correct rev information for pydecnet in the HTTP
# monitor page footer.  Unfortunately, these two keyword substitutions
# are not adequate because they give the information for this file --
# which may not be changed in the latest change overall.  So we use
# that as the starting point, but get the real data from rev.txt in
# the resource directory.  It is put there when you run the program in
# a Subversion workspace.  From there it ends up in the source kit
# tarball.
DNREV = "$LastChangedRevision$".split ()[1]
CYEAR = "$Date: 2019-xxx".split ()[1].split ("-")[0]
AUTHORS = "Paul Koning"

# See if we have a resouces/rev.txt file.
fn = os.path.join (resourcedir, "rev.txt")
try:
    with open (fn, "rt") as f:
        DNREV = int (f.readline ())
        CYEAR = int (f.readline ())
except (OSError, ValueError):
    pass

# See if this is a Subversion workspace, if so update rev.txt from its
# information.
try:
    cmd = "svn info -r HEAD --show-item last-changed-revision".split ()
    cmd2 = "svn info -r HEAD --show-item last-changed-date".split ()
    DNREV2 = int (subprocess.check_output (cmd, stderr = subprocess.DEVNULL,
                                           universal_newlines = True))
    revdate = subprocess.check_output (cmd2, stderr = subprocess.DEVNULL,
                                       universal_newlines = True)
    CYEAR2 = int (revdate.split ("-")[0])
    if DNREV != DNREV2 or CYEAR != CYEAR2:
        with open (fn, "wt") as f:
            print (DNREV2, file = f)
            print (CYEAR2, file = f)
        DNREV = DNREV2
        CYEAR = CYEAR2
except Exception:
    pass

bottom = "{}-{} &copy; 2013-{} by {}".format (DNVERSION, DNREV, CYEAR, AUTHORS)
bottom = html.footer (bottom)

class DNJsonDecoder (json.JSONDecoder):
    def __init__ (self):
        super ().__init__ (strict = False)

    def decode (self, s):
        if isinstance (s, (bytes, bytearray)):
            s = str (s, encoding = "latin1")
        return super ().decode (s)
    
class DNJsonEncoder (json.JSONEncoder):
    def __init__ (self):
        super ().__init__ (allow_nan = False, separators = (',', ':'))
        
    def default (self, o):
        # Encode bytes and bytearray as latin-1 strings -- but not
        # their subclasses which are expected to supply their own
        # formatting mechanisms.  Macaddr is an example.
        if type (o) in { bytes, bytearray }:
            return str (o, encoding = "latin1")
        try:
            return str (o)
        except Exception:
            pass
        return super ().default (o)

    def encode (self, o):
        return bytes (super ().encode (o), encoding = "latin1")
    
dnDecoder = DNJsonDecoder ()
dnEncoder = DNJsonEncoder ()

class Monitor:
    def __init__ (self, config):
        self.config = config

    def start (self, nodelist):
        global start_time
        start_time = time.time ()
        ports = list ()
        config = self.config.http
        if config.http_port:
            if config.https_port:
                # Both are defined, start https server in a thread
                t = StopThread (target = self.serverstart, name = "https",
                                args = (nodelist, config, True))
                t.start ()
            self.serverstart (nodelist, config, False)
        else:
            self.serverstart (nodelist, config, True)

    def serverstart (self, nodelist, config, secure):
        if secure:
            server_address = (config.source, config.https_port)
            logging.debug ("Starting https server on port {}", config.https_port)
        else:
            server_address = (config.source, config.http_port)
            logging.debug ("Starting http server on port {}", config.http_port)
        httpd = DECnetMonitor (server_address, DECnetMonitorRequest, nodelist, config, secure)
        if secure:
            httpd.socket = ssl.wrap_socket (httpd.socket,
                                            certfile = config.certificate,
                                            server_side = True)
        httpd.serve_forever ()
        
class DECnetMonitor (socketserver.ThreadingMixIn, http.server.HTTPServer):
    def __init__ (self, addr, rclass, nodelist, config, secure):
        self.nodelist = nodelist
        self.api = config.api
        self.secure = secure or config.insecure_api
        super ().__init__ (addr, rclass)
        dont_close (self.socket)

#psplit_re = re.compile (r"/([^/\s]*)(?:/(\S*))?")
class DECnetMonitorRequest (http.server.BaseHTTPRequestHandler):
    def setup (self):
        super ().setup ()
        self.wtfile = io.TextIOWrapper (self.wfile)
        
    def log_message (self, fmt, *args):
        logging.trace (fmt % (args))

    def findnode (self):
        # Identify the node addressed by the request's query argument,
        # required if there is more than one.  Return the node index,
        # node object, and the split-apart path string.
        p = urlparse (self.path)
        if p.scheme or p.netloc or p.params or p.fragment:
            logging.trace ("Invalid path: {}", self.path)
            self.send_error (400, "Invalid request")
            return None, None
        logging.trace ("http from {} get {}", self.client_address, p.path)
        parts = os.path.realpath (p.path).split ("/")
        if not parts or parts[0]:
            self.send_error (400, "Invalid request")
            logging.trace ("Invalid path: {}", self.path)
            return None, None
        parts = parts[1:]
        nodelist = self.server.nodelist
        if len (nodelist) > 1:
            if not p.query:
                return 0, None, parts
            q = parse_qs (p.query)
            node = q["system"][0].upper ()
            for i, n in enumerate (nodelist):
                if n.nodename and n.nodename.upper () == node:
                    return i, n, parts
            self.send_error (400, "System not found")
            return 0, None, None
        return 0, nodelist[0], parts

    msg_500 = "Exception during server processing.<p><pre>{}</pre>"
    def handle_exception (self, op):
        logging.exception ("Exception handling http {} of {}", op, self.path)
        # Replace the "explanation" part of the message for code 500
        self.responses[500] = [self.responses[500][0],
                               self.msg_500.format (traceback.format_exc ())]
        self.send_error (500)

    def http_title (self, title):
        now = time.time ()
        uptime = str (timedelta (int (now - start_time) / 86400.))
        now = time.strftime ("%d-%b-%Y %H:%M:%S %Z", time.localtime (now))
        return html.top (title, "Reported {}, up {}".format (now, uptime))
    
    def do_GET (self):
        try:
            nodeidx, tnode, parts = self.findnode ()
            if parts is None:
                return
            if parts[0] == "robots.txt":
                parts = [ "resources", "robots.txt" ]
            if parts[0] == "api" and self.server.api:
                if not self.server.secure:
                    self.send_error (401, "API access requires HTTPS")
                    return
                ret = self.json_get (parts[1:], tnode)
                if not ret:
                    return
                ctype = "application/json"
            elif parts[0] == "resources":
                # Fetching a resource (a constant file)
                fn = os.path.join (resourcedir, *parts[1:])
                ctype = mimetypes.guess_type (fn, False)[0]
                try:
                    with open (fn, "rt") as f:
                        ret = f.read ()
                except OSError:
                    self.send_error (404, "File not found")
                    return
            else:
                ctype = "text/html"
                if not tnode:
                    if parts != ['']:
                        logging.trace ("Missing system parameter")
                        self.send_error (400, "Missing system parameter")
                        return
                    ret = self.node_list ()
                else:
                    ret = tnode.http_get (parts)
                    if not ret:
                        self.send_error (404, "File not found")
                        return
                    title, sb, body = ret
                    if len (self.server.nodelist) > 1:
                        sb.insert (0, self.node_sidebar (nodeidx))
                    sb = html.sidebar (*sb)
                    top = self.http_title (title)
                    ret = html.doc (title, top, html.middle (sb, body), bottom)
            ret = str (ret).encode ("utf-8", "ignore")
            self.send_response (200)
            self.send_header ("Content-type", ctype)
            self.send_header ("Content-Length", str (len (ret)))
            self.end_headers ()
            self.wfile.write (ret)
        except Exception:
            self.handle_exception ("GET")

    def do_POST (self):
        try:
            nodeidx, tnode, parts = self.findnode ()
            if parts is None:
                return
            if not tnode:
                logging.trace ("Missing system parameter")
                self.send_error (400, "Missing system parameter")
                return
            if parts[0] == "api" and self.server.api:
                if not self.server.secure:
                    self.send_error (401, "API access requires HTTPS")
                    return
                ret = self.json_post (parts[1:], tnode)
                if not ret:
                    return
                ctype = "application/json"
            else:
                self.send_error (405, "No such object or not supported with POST")
                return
            if isinstance (ret, str):
                ret = ret.encode ("utf-8", "ignore")                
            self.send_response (200)
            self.send_header ("Content-type", ctype)
            self.send_header ("Content-Length", str(len (ret)))
            self.end_headers ()
            self.wfile.write (ret)
        except Exception:
            self.handle_exception ("POST")

    def node_sidebar (self, idx = -1):
        return html.sbelement (html.sblabel ("Systems"),
                               *[ (html.sbbutton_active if idx == i
                                       else html.sbbutton) (n.description ())
                                  for i, n in enumerate (self.server.nodelist) ])

    def node_list (self):
        title = "DECnet/Python monitoring"
        top = self.http_title (title)
        return html.doc (title, top,
                         html.sidebar (self.node_sidebar ()), bottom)
    
    def getapientity (self, what, tnode):
        logging.trace ("getentity node {} path {}", tnode, what)
        for ent in what:
            if ent:
                # Ignore empty entries in the path
                logging.trace ("current entity {} looking for {}", tnode, ent)
                tnode = tnode.getentity (ent)
        logging.trace ("getentity: found {}", tnode)
        return tnode
    
    def json_get (self, what, tnode):
        logging.trace ("API GET request for {}, node {}", what, tnode)
        data = dict ()
        if not what or what == ['']:
            for n in self.server.nodelist:
                data.update (n.json_description ())
            return dnEncoder.encode (data)
        elif not tnode:
            self.send_error (404, "No such API object")
            return
        try:
            ent = self.getapientity (what, tnode)
            handler = ent.get_api
        except (KeyError, AttributeError):
            logging.trace ("API GET handler lookup error", exc_info = True)
            self.send_error (404, "No such API object")
            return None
        try:
            handler = ent.get_api
        except AttributeError:
            self.send_error (405, "No such object or not supported with GET")
            return None
        try:
            data = handler ()
        except Exception:
            logging.debug ("API GET handler error", exc_info = True)
            data = { "status" : "exception", "exception" : traceback.format_exc () }
        return dnEncoder.encode (data)

    def json_post (self, what, tnode):
        logging.trace ("API POST request for {}, node {}", what, tnode)
        nbytes = 0
        length = self.headers.get ("content-length")
        if length:
            nbytes = int (length)
        data = None
        if nbytes:
            data = dnDecoder.decode (self.rfile.read (nbytes))
        logging.trace ("POST input data: {}", str (data))
        try:
            ent = self.getapientity (what, tnode)
        except (KeyError, AttributeError):
            logging.trace ("API POST handler lookup error", exc_info = True)
            self.send_error (404, "No such API object")
            return None
        try:
            handler = ent.post_api
        except AttributeError:
            self.send_error (405, "No such object or not supported with POST")
            return None
        try:
            ret = handler (data)
        except Exception:
            logging.debug ("API POST handler error", exc_info = True)
            ret = { "status" : "exception", "exception" : traceback.format_exc () }
        return dnEncoder.encode (ret)
