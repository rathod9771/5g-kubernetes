"""Bounded semantic diagnostics. Arbitrary exception/child text never leaves memory."""
import re
import signal

SAFE_MESSAGES = frozenset({
 'Collection cancelled by shutdown', 'Guardian cancellation requested',
 'Governance changed/stop requested', 'Overlapping assurance collectors',
 'Guardian image RSS budget exceeded', 'Approved trial bounds exceeded',
 'Journal visibility unavailable beyond 5s; no health inferred',
 'Protected service degraded', 'Protected NS unhealthy or lifecycle conflict',
 'Protected pod replaced', 'Protected workload not Ready',
 'Protected container changed/restarted', 'F1/NGAP association lost',
 'Independent protected NS check failed', 'OSM operation history changed/conflict', 'Kubernetes subprocess timeout',
 'Kubernetes subprocess RSS budget exceeded', 'Kubernetes subprocess output limit exceeded',
 'Kubernetes read failed; private stderr suppressed', 'Malformed JSON object',
 'RSS unavailable for live process', 'Process-tree budget exceeded',
 'Process-thread traversal budget exceeded', 'Retained process identity budget exceeded',
 'PID reuse detected; refusing new identity', 'PID changed during memory sample',
 'PID changed during traversal', 'Descendant parent changed; refusing unrelated process',
 'Process memory unavailable; watchdog fails closed',
})
CLASSES=frozenset({'AssertionError','RuntimeError','ValueError','TypeError','KeyError','AttributeError',
 'OSError','FileNotFoundError','PermissionError','JSONDecodeError','KubernetesReadError',
 'KeyboardInterrupt','TimeoutExpired','MemoryMeasurementError','SystemExit'})

def diagnostic(error,stage,*,stderr=b'',returncode=None):
    name=type(error).__name__
    message=str(error)
    # Whitelist whole messages; regex redaction alone cannot protect arbitrary secrets.
    safe=message if message in SAFE_MESSAGES else 'Private exception details suppressed'
    excerpt=stderr[-4096:].decode('utf-8','replace')
    classes=[x for x in re.findall(r'^([A-Za-z][A-Za-z0-9_]*):',excerpt,re.M) if x in CLASSES]
    return {'stage':stage,'exception_class':name if name in CLASSES else 'OtherException',
            'message':safe,'exit_code':returncode,'terminating_signal':-returncode if returncode is not None and returncode<0 else None,
            'timeout':stage=='timeout','watchdog':stage if stage in ('rss_watchdog','output_watchdog','memory_measurement') else
                'cpu_limit_signal' if returncode==-signal.SIGXCPU else
                'unverified_child_signal' if returncode is not None and returncode<0 else 'not_triggered',
            'shutdown_reason':'parent_cancellation' if stage=='cancellation' else 'failure',
            'stderr_excerpt':('Python exception class: '+classes[-1]) if classes else '[private stderr omitted]' if stderr else '',
            'stderr_bytes_captured':len(stderr),'stderr_inspection_limit':4096}
