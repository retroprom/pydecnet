# This file was automatically generated by SWIG (http://www.swig.org).
# Version 2.0.9
#
# Do not make changes to this file unless you know what you are doing--modify
# the SWIG interface file instead.



from sys import version_info
if version_info >= (2,6,0):
    def swig_import_helper():
        from os.path import dirname
        import imp
        fp = None
        try:
            fp, pathname, description = imp.find_module('_pcap', [dirname(__file__)])
        except ImportError:
            import _pcap
            return _pcap
        if fp is not None:
            try:
                _mod = imp.load_module('_pcap', fp, pathname, description)
            finally:
                fp.close()
            return _mod
    _pcap = swig_import_helper()
    del swig_import_helper
else:
    import _pcap
del version_info
try:
    _swig_property = property
except NameError:
    pass # Python < 2.2 doesn't have 'property'.
def _swig_setattr_nondynamic(self,class_type,name,value,static=1):
    if (name == "thisown"): return self.this.own(value)
    if (name == "this"):
        if type(value).__name__ == 'SwigPyObject':
            self.__dict__[name] = value
            return
    method = class_type.__swig_setmethods__.get(name,None)
    if method: return method(self,value)
    if (not static):
        self.__dict__[name] = value
    else:
        raise AttributeError("You cannot add attributes to %s" % self)

def _swig_setattr(self,class_type,name,value):
    return _swig_setattr_nondynamic(self,class_type,name,value,0)

def _swig_getattr(self,class_type,name):
    if (name == "thisown"): return self.this.own()
    method = class_type.__swig_getmethods__.get(name,None)
    if method: return method(self)
    raise AttributeError(name)

def _swig_repr(self):
    try: strthis = "proxy of " + self.this.__repr__()
    except: strthis = ""
    return "<%s.%s; %s >" % (self.__class__.__module__, self.__class__.__name__, strthis,)

try:
    _object = object
    _newclass = 1
except AttributeError:
    class _object : pass
    _newclass = 0


pcap_doc = _pcap.pcap_doc
pcapObject_open_live_doc = _pcap.pcapObject_open_live_doc
pcapObject_open_dead_doc = _pcap.pcapObject_open_dead_doc
pcapObject_open_offline_doc = _pcap.pcapObject_open_offline_doc
pcapObject_dump_open_doc = _pcap.pcapObject_dump_open_doc
pcapObject_close_doc = _pcap.pcapObject_close_doc
pcapObject_setnonblock_doc = _pcap.pcapObject_setnonblock_doc
pcapObject_getnonblock_doc = _pcap.pcapObject_getnonblock_doc
pcapObject_inject_doc = _pcap.pcapObject_inject_doc
pcapObject_setfilter_doc = _pcap.pcapObject_setfilter_doc
pcapObject_loop_doc = _pcap.pcapObject_loop_doc
pcapObject_dispatch_doc = _pcap.pcapObject_dispatch_doc
pcapObject_next_doc = _pcap.pcapObject_next_doc
pcapObject_datalink_doc = _pcap.pcapObject_datalink_doc
pcapObject_datalinks_doc = _pcap.pcapObject_datalinks_doc
pcapObject_snapshot_doc = _pcap.pcapObject_snapshot_doc
pcapObject_is_swapped_doc = _pcap.pcapObject_is_swapped_doc
pcapObject_major_version_doc = _pcap.pcapObject_major_version_doc
pcapObject_minor_version_doc = _pcap.pcapObject_minor_version_doc
pcapObject_stats_doc = _pcap.pcapObject_stats_doc
pcapObject_fileno_doc = _pcap.pcapObject_fileno_doc
lookupdev_doc = _pcap.lookupdev_doc
lookupnet_doc = _pcap.lookupnet_doc
findalldevs_doc = _pcap.findalldevs_doc
__doc__ = _pcap.__doc__
for dltname, dltvalue in _pcap.DLT.items():
  globals()[dltname] = dltvalue
del dltname, dltvalue


class pcapObject(_object):
    __swig_setmethods__ = {}
    __setattr__ = lambda self, name, value: _swig_setattr(self, pcapObject, name, value)
    __swig_getmethods__ = {}
    __getattr__ = lambda self, name: _swig_getattr(self, pcapObject, name)
    __repr__ = _swig_repr
    def __init__(self): 
        this = _pcap.new_pcapObject()
        try: self.this.append(this)
        except: self.this = this
    __swig_destroy__ = _pcap.delete_pcapObject
    __del__ = lambda self : None;
    def open_live(self, *args): return _pcap.pcapObject_open_live(self, *args)
    def open_dead(self, *args): return _pcap.pcapObject_open_dead(self, *args)
    def open_offline(self, *args): return _pcap.pcapObject_open_offline(self, *args)
    def dump_open(self, *args): return _pcap.pcapObject_dump_open(self, *args)
    def close(self): return _pcap.pcapObject_close(self)
    def setnonblock(self, *args): return _pcap.pcapObject_setnonblock(self, *args)
    def getnonblock(self): return _pcap.pcapObject_getnonblock(self)
    def inject(self, *args): return _pcap.pcapObject_inject(self, *args)
    def setfilter(self, *args): return _pcap.pcapObject_setfilter(self, *args)
    def loop(self, *args): return _pcap.pcapObject_loop(self, *args)
    def dispatch(self, *args): return _pcap.pcapObject_dispatch(self, *args)
    def next(self): return _pcap.pcapObject_next(self)
    def datalink(self): return _pcap.pcapObject_datalink(self)
    def datalinks(self): return _pcap.pcapObject_datalinks(self)
    def snapshot(self): return _pcap.pcapObject_snapshot(self)
    def is_swapped(self): return _pcap.pcapObject_is_swapped(self)
    def major_version(self): return _pcap.pcapObject_major_version(self)
    def minor_version(self): return _pcap.pcapObject_minor_version(self)
    def stats(self): return _pcap.pcapObject_stats(self)
    def fileno(self): return _pcap.pcapObject_fileno(self)
pcapObject_swigregister = _pcap.pcapObject_swigregister
pcapObject_swigregister(pcapObject)


def lookupdev():
  return _pcap.lookupdev()
lookupdev = _pcap.lookupdev

def findalldevs(unpack=1):
  return _pcap.findalldevs(unpack)
findalldevs = _pcap.findalldevs

def lookupnet(*args):
  return _pcap.lookupnet(*args)
lookupnet = _pcap.lookupnet

def aton(*args):
  return _pcap.aton(*args)
aton = _pcap.aton

def ntoa(*args):
  return _pcap.ntoa(*args)
ntoa = _pcap.ntoa
# This file is compatible with both classic and new-style classes.


