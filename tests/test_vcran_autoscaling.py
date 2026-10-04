"""Opt-in vC-RAN HPA: safe envelopes, canonical Helm rendering, runtime reporting."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from runtime_images import chart_values
from scenario_registry import load_registry,select_scenarios


class VCRANAutoscalingTests(unittest.TestCase):
    def render(self, scenario='vcran-srsran',role='cu',overrides=None,command='template',check=True):
        selected=select_scenarios(load_registry(),[scenario])[0]
        chart=next(c for c in selected['charts'] if c['kdu']==role)
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'images.json';image.write_text(json.dumps(chart_values(ROOT,chart)))
            extra=Path(d)/'overrides.json';extra.write_text(json.dumps(overrides or {}))
            args=['helm',command]
            if command=='template':args.append(chart['release_name'])
            args.append(str(ROOT/chart['source']))
            for profile in chart.get('values_files',[]):args+=['-f',str(ROOT/profile)]
            args+=['-f',str(image),'-f',str(extra)]
            if command=='lint':args.append('--strict')
            result=subprocess.run(args,capture_output=True,text=True,check=check)
        if not check or command=='lint':return result
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def deployment(self,docs):return next(d for d in docs if d['kind']=='Deployment')

    def test_disabled_profile_exact_oom_fix_and_no_hpa(self):
        docs=self.render();deployment=self.deployment(docs)
        self.assertFalse(any(d['kind']=='HorizontalPodAutoscaler' for d in docs))
        self.assertEqual(deployment['spec']['replicas'],1)
        container=deployment['spec']['template']['spec']['containers'][0]
        self.assertEqual(container['resources'],{'requests':{'cpu':'250m','memory':'1Gi'},'limits':{'cpu':'500m'}})
        self.assertEqual(deployment['spec']['template']['metadata']['labels']['ran-type'],'vcran')

    def test_memory_requests_without_hard_limits_with_hpa_enabled_and_disabled(self):
        for enabled in [False,True]:
            for role,cpu in [('cu','500m'),('du','1')]:
                docs=self.render(role=role,overrides={'autoscaling':{'enabled':enabled}})
                resources=self.deployment(docs)['spec']['template']['spec']['containers'][0]['resources']
                self.assertEqual(resources['requests']['memory'],'1Gi')
                self.assertEqual(resources['limits']['cpu'],cpu)
                self.assertNotIn('memory',resources['limits'])

    def test_enabled_safe_one_replica_cpu_policy_and_correct_target(self):
        docs=self.render(overrides={'autoscaling':{'enabled':True}})
        hpa=next(d for d in docs if d['kind']=='HorizontalPodAutoscaler')
        self.assertEqual(hpa['metadata']['name'],'vcran-srsran-cu')
        self.assertEqual(hpa['spec']['scaleTargetRef'],{'apiVersion':'apps/v1','kind':'Deployment','name':'vcran-srsran-cu'})
        self.assertEqual((hpa['spec']['minReplicas'],hpa['spec']['maxReplicas']),(1,1))
        self.assertEqual(hpa['spec']['metrics'][0]['resource']['target'],{'type':'Utilization','averageUtilization':70})
        self.assertNotIn('replicas',self.deployment(docs)['spec'])

    def test_multi_cu_requires_explicit_state_validation_acknowledgement(self):
        result=self.render(overrides={'autoscaling':{'enabled':True,'maxReplicas':2}},check=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('stateAwareMultiCUValidated',result.stderr)
        docs=self.render(overrides={'autoscaling':{'enabled':True,'maxReplicas':2,'stateAwareMultiCUValidated':True}})
        self.assertEqual(next(d for d in docs if d['kind']=='HorizontalPodAutoscaler')['spec']['maxReplicas'],2)

    def test_bad_replica_or_cpu_bounds_rejected(self):
        for values in [{'maxReplicas':2,'stateAwareMultiCUValidated':'false'},{'minReplicas':0},{'minReplicas':2,'maxReplicas':1},{'targetCPUUtilizationPercentage':0},{'targetCPUUtilizationPercentage':101}]:
            result=self.render(overrides={'autoscaling':dict(enabled=True,**values)},check=False)
            self.assertNotEqual(result.returncode,0)

    def test_cu_lint_enabled_and_disabled(self):
        for enabled in [False,True]:
            self.assertEqual(self.render(overrides={'autoscaling':{'enabled':enabled}},command='lint').returncode,0)

    def test_du_remains_fixed_and_no_hpa_even_with_override(self):
        docs=self.render(role='du',overrides={'autoscaling':{'enabled':True,'maxReplicas':2}})
        self.assertEqual(self.deployment(docs)['spec']['replicas'],1)
        self.assertFalse(any(d['kind']=='HorizontalPodAutoscaler' for d in docs))
        container=self.deployment(docs)['spec']['template']['spec']['containers'][0]
        self.assertEqual(container['resources'],{'requests':{'cpu':'500m','memory':'1Gi'},'limits':{'cpu':'1'}})

    def test_cran_and_cloudran_remain_fixed_without_hpa(self):
        for scenario in ['cran-srsran','cloudran-srsran']:
            docs=self.render(scenario=scenario,overrides={'autoscaling':{'enabled':True,'maxReplicas':2}})
            self.assertEqual(self.deployment(docs)['spec']['replicas'],1)
            self.assertFalse(any(d['kind']=='HorizontalPodAutoscaler' for d in docs))

    def status(self,mode):
        text=(ROOT/'scripts/validate.sh').read_text()
        block=text[text.index('# Optional vC-RAN HPA'):text.index('# ---- 10/11/12/13.')]
        stub='''CORE_NS=test
MODE="$1"
evidence() { :; }
print_check() { echo "$2"; }
k_() {
  if [[ "$*" == *"--raw"* ]]; then [[ "$MODE" != missing ]]; return; fi
  if [[ "$*" == *ScalingActive* ]]; then
    if [[ "$MODE" == pending ]]; then echo False; else echo True; fi
  elif [[ "$MODE" != disabled ]]; then echo vcran-srsran-cu; fi
}
'''
        return subprocess.run(['bash','-c',stub+block,'test',mode],capture_output=True,text=True,check=True).stdout.strip()

    def test_missing_metrics_reports_unavailable_and_not_transport_failure(self):
        self.assertEqual(self.status('missing'),'UNAVAILABLE')
        self.assertEqual(self.status('pending'),'UNAVAILABLE')
        self.assertEqual(self.status('healthy'),'AVAILABLE')
        self.assertEqual(self.status('disabled'),'')


if __name__=='__main__':unittest.main()
