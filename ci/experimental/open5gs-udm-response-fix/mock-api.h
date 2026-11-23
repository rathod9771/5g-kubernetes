#ifndef L3_MOCK_API
#define L3_MOCK_API
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
typedef int ogs_pool_id_t;
typedef int ogs_sbi_service_type_e;
typedef int OpenAPI_nf_type_e;
typedef struct {int present;} OpenAPI_search_result_t;
typedef struct {int present;} ogs_sbi_discovery_option_t;
typedef struct {int present;} ogs_sbi_nf_instance_t;
typedef struct {int present;} ogs_sbi_stream_t;
typedef struct {int type; ogs_sbi_nf_instance_t *service_type_array[4];} ogs_sbi_object_t;
typedef struct {ogs_sbi_object_t *sbi_object;int service_type,requester_nf_type,sbi_object_id,assoc_stream_id;ogs_sbi_discovery_option_t *discovery_option;} ogs_sbi_xact_t;
typedef struct {OpenAPI_search_result_t *SearchResult;} ogs_sbi_message_t;
typedef struct {int unused;} udm_ue_t;
typedef struct {int udm_ue_id;} udm_sess_t;
#define OGS_INVALID_POOL_ID 0
#define OGS_MIN_POOL_ID 1
#define OGS_MAX_POOL_ID 100
#define OGS_SBI_SERVICE_TYPE_NULL 0
#define OpenAPI_nf_type_NULL 0
#define OGS_SBI_OBJ_UE_TYPE 1
#define OGS_SBI_OBJ_SESS_TYPE 2
#define OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE 503
#define ogs_assert assert
#define ogs_assert_if_reached() assert(0)
#define ogs_expect(x) assert(x)
#define ogs_error(...) ((void)0)
#define ogs_fatal(...) ((void)0)
#define OGS_SBI_SETUP_NF_INSTANCE(a,b) ((a)=(b))
int ogs_sbi_service_type_to_nf_type(int);
udm_ue_t *udm_ue_find_by_id(int);
udm_sess_t *udm_sess_find_by_id(int);
void ogs_nnrf_disc_handle_nf_discover_search_result(OpenAPI_search_result_t *);
ogs_sbi_nf_instance_t *ogs_sbi_nf_instance_find_by_discovery_param(int,int,ogs_sbi_discovery_option_t *);
void ogs_sbi_xact_remove(ogs_sbi_xact_t *);
bool udm_sbi_send_request(ogs_sbi_nf_instance_t *,ogs_sbi_xact_t *);
ogs_sbi_stream_t *ogs_sbi_stream_find_by_id(int);
bool ogs_sbi_server_send_error(ogs_sbi_stream_t *,int,void *,const char *,void *,void *);
void udm_nnrf_handle_nf_discover(ogs_sbi_xact_t *,ogs_sbi_message_t *);
#endif
