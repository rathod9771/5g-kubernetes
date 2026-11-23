/* Test-only caller of the preserved image's real SBI selection library.
 * No UE context, daemon injection, subscriber identifier, protocol body or socket.
 * Discovery metadata is deliberately a local fixture, not NRF discovery.
 */
#include "ogs-sbi.h"
#include <stdio.h>
#include <string.h>
#define REQUIRE(x) do { if (!(x)) { fprintf(stderr,"driver assertion line %d\n",__LINE__); return 1; } } while (0)
int main(int argc, char **argv)
{
    ogs_sbi_nf_instance_t nf = {0};
    ogs_sbi_nf_service_t service = {0};
    ogs_sbi_client_t fallback = {0}, valid = {0}, refreshed = {0};
    ogs_sbi_client_t *selected = NULL;
    char name[] = "nudr-dr", other[] = "nnrf-nfm", v1[] = "v1", v9[] = "v9";
    char host[] = "172.29.249.2";
    int port = 0, prior = 0;
    REQUIRE(argc == 2 && strlen(argv[1]) == 1 && argv[1][0] >= 'A' && argv[1][0] <= 'F');
    ogs_core_initialize();
    ogs_log_install_domain(&__ogs_sbi_domain, "sbi", OGS_LOG_ERROR);
    fallback.scheme = valid.scheme = refreshed.scheme = OpenAPI_uri_scheme_http;
    fallback.fqdn = valid.fqdn = refreshed.fqdn = host;
    fallback.fqdn_port = 80; valid.fqdn_port = 7777; refreshed.fqdn_port = 8888;
    ogs_list_init(&nf.nf_service_list); nf.client = &fallback;
    service.name = name; service.num_of_version = 1;
    service.version[0].in_uri = v1; service.client = &valid;
    ogs_list_add(&nf.nf_service_list, &service);
    switch (argv[1][0]) {
    case 'A':
        selected = ogs_sbi_client_find_by_service_name(&nf, name, v1);
        REQUIRE(selected == &valid); break;
    case 'B':
        ogs_list_remove(&nf.nf_service_list, &service);
        selected = ogs_sbi_client_find_by_service_name(&nf, name, v1);
        REQUIRE(selected == NULL); break;
    case 'C':
        service.version[0].in_uri = v9;
        selected = ogs_sbi_client_find_by_service_name(&nf, name, v1);
        REQUIRE(selected == NULL); break;
    case 'D':
        service.client = &fallback;
        selected = ogs_sbi_client_find_by_service_name(&nf, name, v1);
        REQUIRE(selected == &fallback); break;
    case 'E':
        selected = ogs_sbi_client_find_by_service_name(&nf, name, v1);
        REQUIRE(selected == &valid); prior = selected->fqdn_port;
        service.client = &refreshed;
        selected = ogs_sbi_client_find_by_service_name(&nf, name, v1);
        REQUIRE(selected == &refreshed);
        service.version[0].in_uri = v9;
        REQUIRE(ogs_sbi_client_find_by_service_name(&nf, name, v1) == NULL);
        service.version[0].in_uri = v1;
        REQUIRE(ogs_sbi_client_find_by_service_name(&nf, name, v1) == &refreshed);
        break;
    case 'F':
        REQUIRE(ogs_sbi_client_find_by_service_name(&nf, other, v1) == &fallback);
        service.name = other;
        selected = ogs_sbi_client_find_by_service_name(&nf, other, v1);
        REQUIRE(selected == &valid); break;
    default: return 1;
    }
    if (selected) {
        char *base = ogs_sbi_client_apiroot(selected);
        char expected[80];
        port = selected->fqdn_port;
        snprintf(expected, sizeof(expected), "http://%s:%d", host, port);
        REQUIRE(base != NULL && strcmp(base, expected) == 0);
        ogs_free(base);
    }
    printf("{\"case\":\"%c\",\"status\":\"PASS\",\"port\":%d,\"prior_port\":%d,\"kind\":\"actual-library-driver\"}\n",
           argv[1][0], port, prior);
    ogs_core_terminate();
    return 0;
}
