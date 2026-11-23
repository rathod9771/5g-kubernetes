"""Controlled NRF discovery + UDR error fixture. No raw paths/bodies/logs retained."""
import asyncio,datetime,json,pathlib,time,uuid,secrets

EVENTS=[];ERRORS={};CONNECTIONS=0;LIMIT=240
STATE=pathlib.Path('/control/state.json');OUTPUT=pathlib.Path('/tmp/events.json')
UDR_ID='34bf34f5-21c6-4b20-a47f-7e66d9b81b31'
SERVICE_ID='77d2bc2b-c7bd-4c6c-9dc1-097ce4879612'

def profile(case,port):
    p={'nfInstanceId':UDR_ID,'nfType':'UDR','nfStatus':'REGISTERED','ipv4Addresses':['172.29.250.2'],
       'heartBeatTimer':60,'allowedNfTypes':['UDM']}
    if case!='B':
        p['nfServices']=[{'serviceInstanceId':SERVICE_ID,'serviceName':'nudr-dr',
            'versions':[{'apiVersionInUri':'v9' if case=='C' else 'v1','apiFullVersion':'1.0.0'}],
            'scheme':'http','nfServiceStatus':'REGISTERED',
            'ipEndPoints':[{'ipv4Address':'172.29.250.2','transport':'TCP','port':port}],
            'allowedNfTypes':['UDM']}]
    return p

def dispatch(port,method,path,body,state):
    # Returns only allowlisted operation name; never export path, headers or body.
    if port in (80,7777,8888):
        ok=method=='GET' and path.startswith('/nudr-dr/v1/subscription-data/') and path.endswith('/authentication-data/authentication-subscription')
        if ok and state['case']=='A':
            # Disposable synthetic material exists only in process memory and SBI.
            return ('authentication-subscription',200,{
                'authenticationMethod':'5G_AKA','encPermanentKey':secrets.token_hex(16),
                'encOpcKey':secrets.token_hex(16),'authenticationManagementField':'8000',
                'sequenceNumber':{'sqnScheme':'NON_TIME_BASED','sqn':'000000000001'}},{})
        return ('authentication-subscription' if ok else 'rejected-operation',404,
                {'status':404,'cause':'USER_NOT_FOUND'},{'content-type':'application/problem+json'})
    if method=='GET' and path.startswith('/nnrf-disc/v1/nf-instances'):
        return 'discovery',200,{'validityPeriod':60,'nfInstances':[profile(state['case'],state['port'])]},{}
    if method=='PUT' and path.startswith('/nnrf-nfm/v1/nf-instances/'):
        p=json.loads(body);p['heartBeatTimer']=60
        return 'nf-register',201,p,{'location':'http://172.29.250.2:9000/nnrf-nfm/v1/nf-instances/'+p['nfInstanceId']}
    if method=='PATCH' and path.startswith('/nnrf-nfm/v1/nf-instances/'):
        return 'nf-heartbeat',204,None,{}
    if method=='POST' and path=='/nnrf-nfm/v1/subscriptions':
        data=json.loads(body);sid=str(uuid.uuid4());data['subscriptionId']=sid
        data['validityTime']=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=120)).isoformat().replace('+00:00','Z')
        return 'nf-subscribe',201,data,{'location':'http://172.29.250.2:9000/nnrf-nfm/v1/subscriptions/'+sid}
    if method=='DELETE':return 'nf-delete',204,None,{}
    return 'rejected-operation',404,{'status':404,'cause':'RESOURCE_NOT_FOUND'},{}

def record(operation,port,method,status,peer,started):
    if len(EVENTS)>=LIMIT:raise ValueError('event limit')
    EVENTS.append({'source_nf':'UDM' if peer=='172.29.250.3' else 'other',
        'source_ip':peer,'destination_nf':'UDR-fixture' if port!=9000 else 'NRF-fixture',
        'destination_ip':'172.29.250.2','destination_port':port,'operation':operation,
        'service':'nudr-dr' if port!=9000 else 'nnrf','api_version':'v1','method':method,
        'http_version':'2','http_status':status,'listener_processing_seconds':round(time.monotonic()-started,6),
        'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'transport_error':False})
    # Only sanitized projections ever reach storage.
    tmp=OUTPUT.with_suffix('.pending');tmp.write_text(json.dumps({'events':EVENTS,'complete':not ERRORS,'errors':ERRORS}));tmp.replace(OUTPUT)

async def handle(reader,writer):
    import h2.config,h2.connection,h2.events
    global CONNECTIONS
    if CONNECTIONS>=24:writer.close();return
    CONNECTIONS+=1;streams={};requests=0
    c=h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False,header_encoding='utf-8'))
    try:
        c.initiate_connection();writer.write(c.data_to_send());await writer.drain()
        while True:
            data=await asyncio.wait_for(reader.read(32768),timeout=25)
            if not data:break
            for event in c.receive_data(data):
                if isinstance(event,h2.events.RequestReceived):
                    if len(streams)>=8 or requests>=32:raise ValueError('stream limit')
                    streams[event.stream_id]=[dict(event.headers),bytearray(),time.monotonic()]
                elif isinstance(event,h2.events.DataReceived):
                    stream=streams[event.stream_id];stream[1].extend(event.data)
                    if len(stream[1])>32768:raise ValueError('body limit')
                    c.acknowledge_received_data(event.flow_controlled_length,event.stream_id)
                elif isinstance(event,h2.events.StreamEnded):
                    headers,body,started=streams.pop(event.stream_id);requests+=1
                    state=json.loads(STATE.read_text());port=writer.get_extra_info('sockname')[1]
                    operation,status,payload,extra=dispatch(port,headers[':method'],headers[':path'],bytes(body),state)
                    raw=b'' if payload is None else json.dumps(payload).encode()
                    response=[(':status',str(status)),('content-type','application/json'),('content-length',str(len(raw)))]
                    response=[x for x in response if x[0] not in extra]+list(extra.items())
                    c.send_headers(event.stream_id,response,end_stream=not raw)
                    if raw:c.send_data(event.stream_id,raw,end_stream=True)
                    record(operation,port,headers[':method'],status,writer.get_extra_info('peername')[0],started)
            writer.write(c.data_to_send());await writer.drain()
    except (asyncio.TimeoutError,ConnectionResetError,BrokenPipeError,asyncio.CancelledError):
        pass  # Idle/closed peers; completed requests already have receipts.
    except Exception as e:
        # Class name only; never exception text, headers, URI or body.
        name=type(e).__name__;ERRORS[name]=ERRORS.get(name,0)+1
        OUTPUT.write_text(json.dumps({'events':EVENTS,'complete':False,'errors':ERRORS}))
    finally:
        CONNECTIONS-=1;writer.close()
        try:await writer.wait_closed()
        except Exception:pass

async def main():
    servers=[await asyncio.start_server(handle,'0.0.0.0',port,limit=65536) for port in (80,7777,8888,9000)]
    OUTPUT.write_text('{"events":[],"complete":true,"errors":{}}')
    await asyncio.gather(*(s.serve_forever() for s in servers))
if __name__=='__main__':asyncio.run(main())
