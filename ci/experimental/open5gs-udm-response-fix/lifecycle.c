#include "sbi-path.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int removed,errors,timers,active,mode,closed;
static ogs_sbi_xact_t *current;
static ogs_sbi_nf_instance_t nf;
static ogs_sbi_stream_t stream;
static udm_ue_t ue;
static udm_sess_t session={1};
int ogs_sbi_service_type_to_nf_type(int t){return t;}
udm_ue_t *udm_ue_find_by_id(int id){assert(id==1);return &ue;}
udm_sess_t *udm_sess_find_by_id(int id){assert(id==1);return &session;}
void ogs_nnrf_disc_handle_nf_discover_search_result(OpenAPI_search_result_t *r){assert(r);}
ogs_sbi_nf_instance_t *ogs_sbi_nf_instance_find_by_discovery_param(int a,int b,ogs_sbi_discovery_option_t *d){assert(a==1&&b==1);(void)d;return mode==1?NULL:&nf;}
void ogs_sbi_xact_remove(ogs_sbi_xact_t *x){assert(x==current&&active==1&&timers==1);removed++;active--;timers--;current=NULL;free(x);}
bool udm_sbi_send_request(ogs_sbi_nf_instance_t *n,ogs_sbi_xact_t *x){assert(n==&nf);if(mode==2){ogs_sbi_xact_remove(x);return false;}return true;}
ogs_sbi_stream_t *ogs_sbi_stream_find_by_id(int id){assert(id==7);return closed?NULL:&stream;}
bool ogs_sbi_server_send_error(ogs_sbi_stream_t *s,int status,void *m,const char *t,void *d,void *c){assert(s==&stream&&status==503&&!m&&!d&&!c);assert(strcmp(t,"Required UDR service unavailable")==0);assert(!active&&!timers);errors++;assert(errors==1);return true;}
int main(void){
 const char *names[]={"successful-send","missing-service","version-send-failure","missing-result","closed-stream","session-owner","duplicate-late-event","subsequent-transaction"};
 for(int i=0;i<8;i++){
  removed=errors=0;active=timers=1;mode=(i==0)?0:(i==1?1:2);closed=(i==4);
  ogs_sbi_object_t object={.type=i==5?2:1};
  current=calloc(1,sizeof(*current));assert(current);
  current->sbi_object=&object;current->service_type=current->requester_nf_type=current->sbi_object_id=1;current->assoc_stream_id=7;
  OpenAPI_search_result_t result={1};ogs_sbi_message_t message={.SearchResult=i==3?NULL:&result};
  udm_nnrf_handle_nf_discover(current,&message);
  if(i==0){assert(!errors&&!removed&&active&&timers);ogs_sbi_xact_remove(current);}
  else{assert(removed==1&&errors==(closed?0:1)&&!active&&!timers&&!current);}
  /* A late response is dispatched only after transaction-ID lookup succeeds.
   * Never invoke the handler with an already-freed transaction pointer. */
  if(i==6){if(current)udm_nnrf_handle_nf_discover(current,&message);assert(errors==1&&removed==1);}
  printf("%s PASS\n",names[i]);
 }
 return 0;
}
