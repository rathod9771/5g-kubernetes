#!/usr/bin/env python3
"""Stage5Z diagnostic-only pre/post-NGSetup CU selection and cell ingress gap.

Only reads the bounded Stage5R allowlisted event JSON. It does not infer
packet non-delivery from a missing log category, change the real Stage5P
verdict, or make a production authorization decision. No raw logs or IDs.
"""
import argparse
import json
from pathlib import Path

CELLS = {
    'macro': {'ue_role':'ue','cu_cell':3804001,'amf_cell':0xe0101},
    'small': {'ue_role':'ue-small','cu_cell':3804002,'amf_cell':0xe0102},
}
ALLOWED_CU = ('cu_rrc_setup_complete','cu_ul_nas',
              'cu_amf_selection_no_match','cu_amf_selection_selected')
ALLOWED_UE = ('nas_service_request','rrc_setup_complete','nas_service_reject',
              'nas_retries_exhausted','nas_pdu_accept')
ALLOWED_AMF = ('amf_initial_ue_cell',)
NAME = 'stage5z-amf-selection-boundary.json'


def _counts(rows, role, keys, *, cell_id=None):
    return {key:sum(1 for e in rows if e.get('source')==role and
                    e.get('event')==key and
                    (cell_id is None or e.get('cell_id')==cell_id))
            for key in keys}


def build_report(evidence):
    evidence = Path(evidence)
    report = {
        'schema':'hcran-stage5z-cu-amf-selection-boundary-v1',
        'scope':'disposable-dual-UE-2DU-RFsim-bounded-observation',
        'status':'INCOMPLETE_OBSERVATION',
        'limitations':[
            'CU AMF selection is associated with a cell by bounded log adjacency only',
            'Absent log markers do not prove absence of NGAP packets',
            'This diagnostic cannot establish subscriber-bound AMF delivery',
        ],
        'production_decision':'NO_GO', 'production_authorized':False,
        'cycles':[],
    }
    try:
        trace = json.loads((evidence/'stage5r-protocol-timeline.json').read_text())
        if trace.get('schema')!='hcran-stage5r-bounded-protocol-observation-v1':
            raise ValueError('Wrong timeline schema')
        events = trace['events']
        if not isinstance(events,list):
            raise ValueError('Missing event list')
        complete = (trace.get('collection_issues')==[] and
                    trace.get('collection_limit_reached') is False)
        starts = [e for e in events if e.get('source')=='harness' and
                  e.get('event')=='milestone' and
                  e.get('stage')=='STAGE5P-CYCLE-START' and
                  type(e.get('cycle')) is int]
        starts.sort(key=lambda e:e['elapsed_seconds'])
        report['observation_complete'] = complete
        report['status'] = 'DIAGNOSTIC_ONLY' if starts and complete else 'INCOMPLETE_OBSERVATION'
        for i, start in enumerate(starts):
            next_start = starts[i+1]['elapsed_seconds'] if i+1<len(starts) else float('inf')
            segment = [e for e in events if start['elapsed_seconds'] <= e.get('elapsed_seconds',-1) < next_start]
            def boundary(name):
                return next((e['elapsed_seconds'] for e in segment if
                             e.get('source')=='harness' and e.get('event')=='milestone' and
                             e.get('stage')==name and e.get('status')=='PASS'),None)
            stopped = boundary('T31-AMF-actually-stopped')
            restarted = boundary('T33-AMF-process-restarted')
            ng = boundary('T34-new-NGSetupResponse-after-AMF-restart')
            ending = next((e.get('status') for e in reversed(segment) if
                           e.get('source')=='harness' and e.get('event')=='milestone' and
                           e.get('stage')=='STAGE5P-CYCLE'), 'NOT_COMPLETE')
            phase_rows = {}
            if stopped is None or ng is None or not stopped < ng:
                phases = {'amf_unavailable_or_reconnecting':[], 'after_ngsetup':[]}
                phase_ready = False
            else:
                phases = {
                    'amf_unavailable_or_reconnecting': [e for e in segment if stopped<=e.get('elapsed_seconds',-1)<ng],
                    'after_ngsetup': [e for e in segment if ng<=e.get('elapsed_seconds',-1)],
                }
                phase_ready = restarted is not None and stopped < restarted <= ng
            for phase, window in phases.items():
                roles = {}
                for cell, identifiers in CELLS.items():
                    roles[cell] = {
                        'ue_markers': _counts(window,identifiers['ue_role'],ALLOWED_UE),
                        'cu_cell_markers': _counts(window,'cu',ALLOWED_CU,cell_id=identifiers['cu_cell']),
                        'amf_cell_markers': _counts(window,'core',ALLOWED_AMF,cell_id=identifiers['amf_cell']),
                    }
                phase_rows[phase] = {
                    'cells':roles,
                    'cu_no_association_unscoped': _counts(window,'cu',('cu_amf_no_association',))['cu_amf_no_association'],
                    'cu_no_match_unattributed': sum(1 for e in window if
                        e.get('source')=='cu' and e.get('event')=='cu_amf_selection_no_match' and 'cell_id' not in e),
                }
            report['cycles'].append({
                'cycle':start['cycle'],
                'amf_shutdown_observed':stopped is not None,
                'amf_restart_observed':restarted is not None,
                'new_ngsetup_observed':ng is not None,
                'phases_complete':phase_ready,
                'genuine_recovery_status':ending,
                'phase_observations':phase_rows,
            })
    except (OSError, ValueError, TypeError, KeyError) as e:
        report['status']='INCOMPLETE_OBSERVATION'
        report['error_type']=type(e).__name__
    (evidence/NAME).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'stage':'STAGE5Z-AMF-SELECTION','status':report['status'],
                      'cycles':len(report['cycles']),'production_decision':'NO_GO'}),flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('evidence',type=Path)
    build_report(p.parse_args().evidence)
    return 0   # Never mask the authentic Stage5P recovery status.


if __name__=='__main__':
    raise SystemExit(main())
