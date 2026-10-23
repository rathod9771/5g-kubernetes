"""Bounded kubectl list adapter: no mutation, retry, shell, or virtual-memory cap."""
import json
import os
import resource
import selectors
import signal
import subprocess
import time


class KubernetesReadError(RuntimeError):
    diagnostic = None


def tree_rss(pid, tracked=None):
    from .memory import tree_memory,MemoryMeasurementError
    try:return tree_memory(pid,tracked)['rss_sum_bytes']
    except MemoryMeasurementError as error:raise KubernetesReadError(str(error)) from None


class KubernetesReader:
    def __init__(self, cfg, namespace, *, timeout=10, max_output=2*1024*1024,
                 max_rss=256*1024*1024, budget=3, executable='kubectl'):
        self.cfg, self.namespace = cfg, namespace
        self.timeout, self.max_output, self.max_rss = timeout, max_output, max_rss
        self.budget, self.executable = budget, executable
        self.stats = {'processes':0, 'request_attempts':0, 'failures':0,
                      'subprocess_seconds':0., 'peak_child_rss_bytes':0,
                      'output_bytes':0}

    def __call__(self, args):
        allowed = [['get','pods','-n',self.namespace,'-o','json'],
                   ['get','nodes','-o','json'],
                   ['get','events','-n',self.namespace,'--field-selector=type=Warning','-o','json']]
        if args not in allowed or self.stats['request_attempts'] >= self.budget:
            raise KubernetesReadError('Read not allowed or budget exhausted')
        self.stats['request_attempts'] += 1
        # A hard inherited limit cannot safely be raised by a child. Reject it;
        # the activation supervisor must bound RSS, not Go virtual reservations.
        if resource.getrlimit(resource.RLIMIT_AS)[0] != resource.RLIM_INFINITY:
            self.stats['failures'] += 1
            raise KubernetesReadError('Finite inherited RLIMIT_AS incompatible with reviewed runner')
        command = [self.executable, '--kubeconfig', self.cfg['OSM_KUBECONFIG_PATH'],
                   '--request-timeout=5s', '--chunk-size=0', *args]
        result = self.run_json(command)
        if not isinstance(result.get('items'), list):
            raise KubernetesReadError('Malformed Kubernetes list')
        return result

    def sctp(self, pod, peers, context, ns_id):
        """Opt-in scoped kernel read; two observations/six subprocesses maximum.

        Revalidate source and peer pod identities around exact read-only cat.
        Not enabled by the default CLI. No RBAC or deployment changes here.
        """
        import re
        from .adapters import Kubernetes
        if self.stats.get('protocol_observations',0)>=2:
            raise KubernetesReadError('Protocol observation budget exhausted')
        if (context.get('namespace')!=self.namespace or pod.get('namespace')!=self.namespace or
                pod.get('labels',{}).get('osm.etsi.org/ns-id')!=ns_id or
                pod.get('labels',{}).get('component') not in ('cu','du') or not peers):
            raise KubernetesReadError('Unbound protocol read')
        candidates=[c['name'] for c in pod.get('containers',[]) if
                    'oai-gnb' in c.get('image','').lower() or 'srsran' in c.get('image','').lower()]
        if len(candidates)!=1:raise KubernetesReadError('Ambiguous RAN container')
        container=candidates[0]
        if not all(re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',x) for x in (self.namespace,pod['name'],container)):
            raise KubernetesReadError('Invalid protocol read identity')
        if resource.getrlimit(resource.RLIMIT_AS)[0]!=resource.RLIM_INFINITY:
            raise KubernetesReadError('Finite inherited RLIMIT_AS incompatible with reviewed runner')
        self.stats['protocol_observations']=self.stats.get('protocol_observations',0)+1
        def inventory():
            self.stats['protocol_request_attempts']=self.stats.get('protocol_request_attempts',0)+1
            return self.run_json([self.executable,'--kubeconfig',self.cfg['OSM_KUBECONFIG_PATH'],
                '--request-timeout=5s','--chunk-size=0','get','pods','-n',self.namespace,'-o','json'])
        def same_inventory(data):
            snap=Kubernetes(lambda _:data).snapshot(self.namespace)
            if snap['provenance']!='LIVE' or snap.get('truncated'):return False
            for expected in [pod,*peers.values()]:
                matches=[p for p in snap['value'] if p['name']==expected['name']]
                if len(matches)!=1:return False
                current=matches[0]
                if any(current.get(k)!=expected.get(k) for k in ('uid','pod_ip','host_network','containers','container_identities','labels','ready','phase')):return False
                # A terminating pod is not stable identity evidence.
                raw=next(p for p in data['items'] if p['metadata']['name']==expected['name'])
                if raw['metadata'].get('deletionTimestamp'):return False
            return True
        if not same_inventory(inventory()):raise KubernetesReadError('Protocol identity changed before read')
        observed_at=time.time()
        self.stats['protocol_request_attempts']+=1
        table=self.run_json([self.executable,'--kubeconfig',self.cfg['OSM_KUBECONFIG_PATH'],
            '--request-timeout=5s','exec','-n',self.namespace,pod['name'],'-c',container,
            '--','cat','/proc/net/sctp/assocs'],_text=True)
        if len(table.encode())>65536:raise KubernetesReadError('SCTP output budget exceeded')
        if not same_inventory(inventory()):raise KubernetesReadError('Protocol identity changed during read')
        return {**context,'ns_id':ns_id,'pod':pod['name'],'pod_uid':pod['uid'],
                'pod_ip':pod['pod_ip'],'container':container,'provenance':'LIVE',
                'observed_at':observed_at,'table':table,
                'peers':{k:{x:p[x] for x in ('uid','pod_ip')} for k,p in peers.items()}}

    def run_json(self, command, stop_event=None, *, _text=False):
        # Shared process boundary; callers construct commands without user shell input.
        started = time.monotonic(); process = None; selector = selectors.DefaultSelector()
        buffers = {}; total = 0; tracked = {}; stage='startup'; failure=None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True, close_fds=True)
            from .memory import birth as process_birth
            try:tracked[process.pid]=process_birth(process.pid)
            except (FileNotFoundError,ProcessLookupError):pass
            self.stats['processes'] += 1
            for pipe in (process.stdout, process.stderr):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
                buffers[pipe] = bytearray()
            stage='supervision'
            while selector.get_map() or process.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    stage='cancellation'
                    raise KubernetesReadError('Collection cancelled by shutdown')
                if time.monotonic()-started > self.timeout:
                    stage='timeout'
                    raise KubernetesReadError('Kubernetes subprocess timeout')
                stage='memory_measurement'
                rss = tree_rss(process.pid, tracked)
                stage='supervision'
                self.stats['peak_child_rss_bytes'] = max(self.stats['peak_child_rss_bytes'], rss)
                if rss > self.max_rss:
                    stage='rss_watchdog'
                    raise KubernetesReadError('Kubernetes subprocess RSS budget exceeded')
                for key, _ in selector.select(0.02):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    total += len(chunk)
                    if total > self.max_output:
                        stage='output_watchdog'
                        raise KubernetesReadError('Kubernetes subprocess output limit exceeded')
                    buffers[key.fileobj].extend(chunk)
            stage='child_exit'
            if process.wait() != 0:
                raise KubernetesReadError('Kubernetes read failed; private stderr suppressed')
            stage='decode_output'
            if _text:return bytes(buffers[process.stdout]).decode('utf-8')
            result = json.loads(buffers[process.stdout])
            if not isinstance(result, dict):
                raise KubernetesReadError('Malformed JSON object')
            return result
        except (Exception, KeyboardInterrupt) as error:
            from .diagnostics import diagnostic
            if isinstance(error,KeyboardInterrupt):stage='cancellation'
            self.stats['failures'] += 1
            failure=diagnostic(error,stage,stderr=bytes(buffers.get(getattr(process,'stderr',None),b'')),
                               returncode=process.poll() if process is not None else None)
            failure['stdout_bytes']=len(buffers.get(getattr(process,'stdout',None),b''))
            failure['child_pid']=process.pid if process is not None else None
            wrapped=KubernetesReadError(failure['message']);wrapped.diagnostic=failure
            raise wrapped from None
        finally:
            if process is not None:
                # Nested kubectl runs have separate sessions. Kill tracked descendants
                # too, checking start time to avoid signaling a reused process ID.
                for pid, birth in reversed(list(tracked.items())):
                    if pid == process.pid: continue
                    try:
                        with open('/proc/%s/stat' % pid) as stream:
                            current=stream.read().rsplit(')',1)[1].split()[19]
                        if current == birth:
                            if os.getpgid(pid)==pid: os.killpg(pid,signal.SIGKILL)
                            else: os.kill(pid,signal.SIGKILL)
                    except (FileNotFoundError, ProcessLookupError): pass
                # Kill the process group even if the leader exited: plugins must not linger.
                try:
                    try:current_birth=process_birth(process.pid)
                    except (FileNotFoundError,ProcessLookupError):current_birth=None
                    expected_birth=tracked.get(process.pid)
                    if current_birth is None or expected_birth is None or current_birth==expected_birth:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait()
                if failure is not None:
                    failure['cleanup_exit_code']=process.returncode
                    failure['cleanup_signal']=-process.returncode if process.returncode<0 else None
                process.stdout.close(); process.stderr.close()
            selector.close()
            if failure is not None:
                self.stats.setdefault('diagnostics',[])
                if len(self.stats['diagnostics'])<8:self.stats['diagnostics'].append(failure)
            self.stats['output_bytes'] += total
            self.stats['subprocess_seconds'] += time.monotonic()-started
