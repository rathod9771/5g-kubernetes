/* Isolated synthetic client metadata; linked to the actual patched SBI library. */
#include "ogs-sbi.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
int main(void) {
 ogs_core_initialize();
 ogs_log_install_domain(&__ogs_sbi_domain,"sbi",OGS_LOG_INFO);
 ogs_sbi_nf_instance_t n={0};
 ogs_sbi_nf_service_t s={0},other={0};
 ogs_sbi_client_t fallback={0},valid={0},second={0};
 fallback.scheme=valid.scheme=second.scheme=OpenAPI_uri_scheme_http;
 fallback.fqdn="127.0.0.1";fallback.fqdn_port=80;
 valid.fqdn="127.0.0.1";valid.fqdn_port=7777;
 second.fqdn="127.0.0.1";second.fqdn_port=8888;
 ogs_list_init(&n.nf_service_list);n.client=&fallback;
 s.name="nudr-dr";s.num_of_version=1;s.version[0].in_uri="v1";s.client=&valid;
 ogs_list_add(&n.nf_service_list,&s);
 assert(ogs_sbi_client_find_by_service_name(&n,"nudr-dr","v1")==&valid);puts("valid7777 PASS");
 assert(ogs_sbi_client_find_by_service_name(&n,"nudr-dr","v9")==NULL);puts("version_mismatch PASS");
 s.num_of_version=0;assert(ogs_sbi_client_find_by_service_name(&n,"nudr-dr","v1")==NULL);puts("missing_version PASS");s.num_of_version=1;
 ogs_list_remove(&n.nf_service_list,&s);assert(ogs_sbi_client_find_by_service_name(&n,"nudr-dr","v1")==NULL);puts("missing_cached_service PASS");ogs_list_add(&n.nf_service_list,&s);
 other.name="other";other.num_of_version=1;other.version[0].in_uri="v1";other.client=&second;ogs_list_add(&n.nf_service_list,&other);
 assert(ogs_sbi_client_find_by_service_name(&n,"other","v1")==&second);assert(ogs_sbi_client_find_by_service_name(&n,"nudr-dr","v1")==&valid);puts("multiple_services PASS");
 s.client=NULL;assert(ogs_sbi_client_find_by_service_name(&n,"nudr-dr","v1")==NULL);puts("null_client PASS");
 s.client=&fallback;assert(ogs_sbi_client_find_by_service_name(&n,"nudr-dr","v1")==&fallback);puts("explicit_wrong_client_UNRESOLVED PASS");s.client=&valid;
 assert(ogs_sbi_client_find_by_service_name(&n,"legacy","v1")==&fallback);puts("unrelated_fallback PASS");
 char *uri=ogs_sbi_client_apiroot(&valid);assert(strstr(uri,":7777")!=NULL);ogs_free(uri);puts("actual_uri7777 PASS");
 ogs_core_terminate();return 0;
}
