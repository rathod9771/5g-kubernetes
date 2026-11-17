// Genuine CU-CP + real SCTP N2 client. No fake AMF or synthetic protocol callbacks.
#include "snapshot_observer.h"
#include "srsran/cu_cp/cu_cp_configuration_helpers.h"
#include "srsran/ngap/gateways/n2_connection_client_factory.h"
#include "srsran/pcap/dlt_pcap.h"
#include "srsran/support/executors/task_worker.h"
#include "srsran/support/io/io_broker_factory.h"
#include "srsran/support/io/io_timer_source.h"
#include "srsran/support/timers.h"
#include <atomic>
#include <cstdlib>
#include <iostream>
#include <thread>
#include <chrono>
#include <sys/stat.h>
#include <net/if.h>
#include <unistd.h>
#include <cstring>
#include <stdexcept>
using namespace srsran;
using namespace srsran::srs_cu_cp;
static void isolation(const char* host_inode)
{
  if(!host_inode[0])throw std::runtime_error("missing host namespace identity");
  for(const char* p=host_inode;*p;++p)if(*p<'0'||*p>'9')throw std::runtime_error("invalid host namespace identity");
  struct stat n{};if(getuid()==0||stat("/proc/self/ns/net",&n)||std::to_string(n.st_ino)==host_inode)
    throw std::runtime_error("not an unprivileged isolated namespace");
  struct if_nameindex* interfaces=if_nameindex();if(!interfaces)throw std::runtime_error("interface inventory unavailable");
  bool ok=true,lo=false;
  for(auto* p=interfaces;p->if_index;++p){if(std::strcmp(p->if_name,"lo"))ok=false;else lo=true;}
  if_freenameindex(interfaces);if(!ok||!lo)throw std::runtime_error("non-loopback interface");
}
int main(int argc,char** argv)
{
  // No run by default. Namespace/approval supervisor is separately reviewed and must provide confinement.
  if(argc!=8||std::strcmp(argv[1],"--execute-isolated")||std::strcmp(argv[2],"--host-netns")||
     std::strcmp(argv[4],"--seconds")||std::strcmp(argv[6],"--output"))return 64;
  try{
    isolation(argv[3]);char* end=nullptr;long seconds=std::strtol(argv[5],&end,10);if(!end||*end||seconds<1||seconds>120)return 64;
    umask(0077);srslog::init();srslog::fetch_basic_logger("NGAP").set_level(srslog::basic_levels::info);
    srslog::fetch_basic_logger("NGAP").set_hex_dump_max_size(0);
    srslog::fetch_basic_logger("CU-CP").set_level(srslog::basic_levels::info);
    task_worker owner_worker("d1_cu",1024),io_worker("d1_io",256),timer_worker("d1_timer",256);
    task_worker_executor owner(owner_worker),io_exec(io_worker),timer_exec(timer_worker);
    auto broker=create_io_broker(io_broker_type::epoll);if(!broker)throw std::runtime_error("IO broker unavailable");timer_manager timers(64);
    io_timer_source ticks(timers,*broker,timer_exec,std::chrono::milliseconds(1));
    null_dlt_pcap pcap;
    n2_connection_client_config::network net{*broker,io_exec};
    net.bind_address="127.0.0.10";net.amf_address="127.0.0.20";net.amf_port=38412;
    auto gateway=create_n2_connection_client(n2_connection_client_config{pcap,net});
    if(!gateway)throw std::runtime_error("real N2 gateway creation failed");
    auto cfg=config_helpers::make_default_cu_cp_config();
    cfg.services.cu_cp_executor=&owner;cfg.services.cu_cp_e2_exec=&owner;cfg.services.timers=&timers;
    cfg.node.gnb_id={411,22};cfg.node.ran_node_name="l3-d0-cucp";
    cfg.admission.max_nof_ues=8;cfg.ngap.no_core=false;cfg.ngap.ngaps.clear();
    s_nssai_t slice{slice_service_type{1},slice_differentiator{}};
    auto plmn=plmn_identity::parse("00101");if(!plmn)throw std::runtime_error("synthetic PLMN invalid");
    plmn_item item{*plmn,{slice}};supported_tracking_area ta{7,{item}};
    cu_cp_configuration::ngap_config nc;nc.n2_gw=gateway.get();nc.supported_tas.push_back(ta);cfg.ngap.ngaps.push_back(nc);
    ngap_snapshot::config sc;sc.enabled=true;sc.peer_count=1;sc.peers[0].amf_index=0;
    if(!sc.local_nf_id.set("l3-d0-cucp-01")||!sc.peers[0].peer_id.set("l3-d0-amf-01@n2-loopback")||!sc.peers[0].expected_amf_name.set("l3-d0-amf-01"))return 65;
    cu_cp_impl cu(cfg,sc);l3_d1::snapshot_observer observer(cu,argv[7]);
    observer.sample("PRE_START");
    // start() blocks on its promise: run on an outer lifecycle thread, never on CU owner.
    std::atomic<bool> finished{false}, start_success{false};std::thread starter([&]{start_success.store(cu.start(),std::memory_order_relaxed);finished.store(true,std::memory_order_release);});
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(seconds);
    try {while(std::chrono::steady_clock::now()<deadline){observer.sample("OBSERVE");std::this_thread::sleep_for(std::chrono::milliseconds(100));}}
    catch(...){std::cerr<<"D1_OBSERVER_ABORT\n";std::_Exit(76);}
    if(!finished.load(std::memory_order_acquire)){std::cerr<<"D1_START_INCOMPLETE\n";std::_Exit(75);}
    starter.join();std::cout<<(start_success.load()?"D1_START_RETURNED_TRUE\n":"D1_START_RETURNED_FALSE\n");cu.stop();observer.after_stop();
    ticks.wait_for_stop();srslog::flush();
    std::cout<<"D1_DRIVER_COMPLETED_NO_PROTOCOL_VERDICT\n";
    // Workers stay alive until CU/gateway/timers destruct, in reverse construction order.
    return 0;
  }catch(const std::exception&){std::cerr<<"D1_SAFE_ABORT\n";return 70;}
}
