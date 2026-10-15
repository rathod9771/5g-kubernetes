"""Research-only installation plan. No host, service or infrastructure mutations."""
import argparse
import json

def plan():
    return {'mode':'research-only','execution':'PLAN_ONLY',
            'reuse':['Kubernetes','Rancher','OSM v19','Prometheus','Grafana'],
            'reference_workloads':False,'automatic_installation':False,
            'required_files':['TELECOM_AUTH_FILE','TELECOM_EXECUTION_POLICY'],
            'private_directories':['TELECOM_RUNTIME_DIR','TELECOM_WORKSPACE'],
            'start':'python3 docs/telecom/p6/research_only.py --port 5099',
            'bind':'127.0.0.1','production_service_restart':False,
            'buildkit_required_for_prebuilt_images':False,
            'platform_only_profile':{'excludes_reference_ran_core':True,
                'installer_entry_point':'./install.sh --platform-only-plan',
                'installer_mutation':False,'description':'Prints this plan and exits; never provisions or removes reference RAN/Core workloads.'},
            'lifecycle':['initial-instantiation: explicit independent approval',
                         'update: OFFLINE_TESTED guarded adapter; live execution NOT_CERTIFIED by default',
                         'rollback: OFFLINE_TESTED guarded adapter, limited to the immediately prior deployed release; live execution NOT_CERTIFIED by default']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['research-only'],required=True)
    parser.add_argument('--reuse-existing',action='store_true',required=True)
    parser.add_argument('--plan',action='store_true',required=True)
    parser.parse_args();print(json.dumps(plan(),indent=2))

if __name__=='__main__':main()
