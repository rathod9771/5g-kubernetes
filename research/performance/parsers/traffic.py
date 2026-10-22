"""Import previously approved traffic results; never sends traffic."""
import argparse
import json
from pathlib import Path
from .events import iperf,ping
from ..schema import write

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--format',choices=['iperf3','ping'],required=True);p.add_argument('--input',required=True);p.add_argument('--metadata',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    source=Path(a.input);meta=Path(a.metadata)
    if source.stat().st_size>2*1024*1024 or meta.stat().st_size>16384:raise SystemExit('Input budget')
    data=json.loads(meta.read_text())
    if data.get('provenance') not in ('RECORDED_LIVE','SYNTHETIC_TEST') or not data.get('labels',{}).get('evidence_ref') or 'at' not in data:raise SystemExit('Explicit provenance/timestamp/evidence reference required')
    rows=iperf(json.loads(source.read_text()),data) if a.format=='iperf3' else ping(source.read_text(),data)
    write(a.output,rows,{'collection_kind':'RECORDED_TRAFFIC_IMPORT','traffic_generated':False})
if __name__=='__main__':main()
