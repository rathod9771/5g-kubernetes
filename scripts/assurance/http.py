"""Bounded GET-only monitoring transport. No credential forwarding or mutation methods."""
import json
import ssl
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class ReadOnlyHTTP:
    def __init__(self, *, token=None, auth_origin=None, ca_file=None, timeout=4, ttl=30, budget=16,
                 clock=time.monotonic, opener=None):
        if not 0<timeout<=8 or not 0<=ttl<=120 or not 1<=budget<=64:
            raise ValueError('Invalid monitoring bounds')
        if token and (not auth_origin or urllib.parse.urlsplit(auth_origin).scheme!='https'):
            raise ValueError('Bearer origin must be explicitly bound to HTTPS')
        self.auth_origin=urllib.parse.urlsplit(auth_origin).netloc if auth_origin else None
        self.token,self.timeout,self.ttl,self.budget=token,timeout,ttl,budget
        self.clock=clock;self.cache=OrderedDict();self.lock=threading.Lock()
        self.opener=opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)),NoRedirect())
        self.stats={'requests':0,'cache_hits':0,'failures':0,'latency_seconds':[]}
    def __call__(self,url):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Invalid monitoring URL')
        if self.token and (parsed.scheme!='https' or parsed.netloc!=self.auth_origin):
            raise ValueError('Bearer authentication origin mismatch')
        # Serialize to keep one request in flight and coalesce identical queries.
        with self.lock:
            now=self.clock();cached=self.cache.get(url)
            if cached and cached[0]>now:
                self.stats['cache_hits']+=1
                return json.loads(cached[1])
            if self.stats['requests']>=self.budget:raise RuntimeError('Monitoring request budget exhausted')
            headers={'Accept':'application/json'}
            if self.token:headers['Authorization']='Bearer '+self.token
            req=urllib.request.Request(url,headers=headers,method='GET')
            self.stats['requests']+=1;started=self.clock()
            try:
                with self.opener.open(req,timeout=self.timeout) as response:
                    data=response.read(2*1024*1024+1)
                if len(data)>2*1024*1024:raise ValueError('Monitoring response exceeds limit')
                value=json.loads(data)
                self.cache[url]=(self.clock()+self.ttl,data)
                self.cache.move_to_end(url)
                while len(self.cache)>32:self.cache.popitem(last=False)
                return value
            except Exception:
                self.stats['failures']+=1
                raise RuntimeError('Monitoring GET failed; private details suppressed') from None
            finally:self.stats['latency_seconds'].append(self.clock()-started)
