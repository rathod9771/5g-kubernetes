"""Bounded offline engineering microbenchmark; no live Kubernetes or RF."""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import resource
import time

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--iterations',type=int,default=100);parser.add_argument('--output',required=True);a=parser.parse_args()
    if not 1<=a.iterations<=1000:raise SystemExit('Iteration budget')
    root=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location('offline_exporter',root/'monitoring/ran-exporter/ran_exporter.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    now=[1000.]
    class API:
        def discover(self,_):return [{'metadata':{'namespace':'test','uid':'fixture','name':'fran-oai-du-fixture','labels':{'component':'du','ran-type':'fran'}},'spec':{'containers':[{'name':'du','image':'oai-gnb:fixture'}]}}]
        def logs(self,_):
            stamp=dt.datetime.fromtimestamp(now[0],dt.timezone.utc).isoformat().replace('+00:00','Z')
            return '\n'.join(stamp+' UE '+format(40960+i,'x')+': ulsch_rounds 100/2/1/0, ulsch_errors 0 BLER 0.02 SNR 12.5' for i in range(32))
    observer=module.Observer(API(),'test',clock=lambda:now[0]);start=time.perf_counter();cpu=time.process_time()
    for _ in range(a.iterations):observer.cycle();observer.render();now[0]+=.01
    status={k:line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if (k:=line.split(':',1)[0]) in ('VmRSS','VmHWM')}
    if len(observer.values)!=32*6:raise RuntimeError('Benchmark did not retain the expected32 UE x6 real parser outputs')
    result={'process_current_rss_bytes':int(status['VmRSS'])*1024,'process_current_image_hwm_bytes':int(status['VmHWM'])*1024,'kind':'OFFLINE_ENGINEERING_MICROBENCHMARK','input_provenance':'SYNTHETIC_TEST','live_calls':0,'iterations':a.iterations,'ues_per_cycle':32,'wall_seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu,'legacy_ru_maxrss_kib_not_current_image_memory':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'series_retained':len(observer.values),'not_a_radio_or_cluster_benchmark':True}
    with open(a.output,'x') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
