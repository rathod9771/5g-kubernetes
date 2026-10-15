"""Bounded OSM authentication plus allowlisted reads; no lifecycle methods or retries."""
import ssl
import urllib.request
from osm_catalog import Catalog, CatalogError, NoRedirect, parse_response
from runtime_config import private_text

class ReadOnlyOSM:
    def __init__(self,cfg,state,opener=None):
        self.cfg=cfg; self.ids={v for k,v in state.get('osm',{}).items() if k in ('active_instance_id','core_instance_id') and v}
        operation=state.get('osm',{}).get('active_operation_id')
        self.paths={'/nslcm/v1/ns_instances/'+ident for ident in self.ids}
        if operation:self.paths.add('/nslcm/v1/ns_lcm_op_occs/'+operation)
        self.stats={'gets':0,'authentication':0,'failures':0};self.opener=opener;self.client=None
    def _request(self,method,path,data=None,content_type='application/yaml'):
        if method=='GET' and path in self.paths and self.stats['gets']<3:self.stats['gets']+=1
        elif method=='POST' and path=='/admin/v1/tokens' and self.stats['authentication']==0:self.stats['authentication']+=1
        else:raise PermissionError('OSM mutation, retry or request budget forbidden')
        headers={'Accept':'application/json','Content-Type':content_type}
        if self.client.token:headers['Authorization']='Bearer '+self.client.token
        request=urllib.request.Request(self.client.host+'/osm'+path,data=data,headers=headers,method=method)
        try:
            with self.opener.open(request,timeout=4) as response:payload=response.read(2*1024*1024+1)
            if len(payload)>2*1024*1024:raise ValueError('OSM response limit')
            return payload
        except Exception:
            self.stats['failures']+=1;raise CatalogError('Read-only OSM request failed; private details suppressed') from None
    def _get(self,path):
        if path not in self.paths:raise PermissionError('OSM object not in bound state')
        if self.client is None:
            self.client=Catalog(self.cfg['OSM_HOST'],ca_file=self.cfg['OSM_CA_CERT_PATH'])
            self.client.request=self._request
            if self.opener is None:
                context=ssl.create_default_context()
                certificate,_=private_text(self.cfg['OSM_CA_CERT_PATH'],required=True)
                context.load_verify_locations(cadata=certificate)
                self.opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),NoRedirect())
            self.client.authenticate(self.cfg['OSM_USER'],self.cfg['OSM_PASSWORD'],self.cfg['OSM_PROJECT_ID'] or self.cfg['OSM_PROJECT'])
        if not self.client.token:raise CatalogError('No authenticated read-only OSM session; no retry')
        return parse_response(self._request('GET',path))
    def get_ns_instance(self,ident):return self._get('/nslcm/v1/ns_instances/'+ident)
    def get_operation(self,ident):return self._get('/nslcm/v1/ns_lcm_op_occs/'+ident)
