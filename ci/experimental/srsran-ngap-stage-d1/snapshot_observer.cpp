#include "snapshot_observer.h"
#include <chrono>
#include <thread>
#include <sstream>
#include <stdexcept>
#include <cstring>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cerrno>
#include <filesystem>
namespace l3_d1 {
namespace ns = srsran::srs_cu_cp::ngap_snapshot;
static std::string quote(const ns::text& t)
{
  std::string s="\"";
  for (char c:t.bytes) { if (!c) break; if (c=='"' || c=='\\') s+='\\'; if (c<32 || c>126) throw std::runtime_error("invalid snapshot text"); s+=c; }
  return s+'"';
}
static const char* state(ns::state s) { switch(s) { case ns::state::up:return "UP";case ns::state::down:return "DOWN";default:return "UNKNOWN";} }
static const char* reason(ns::reason r) {
  switch(r) {
#define R(v) case ns::reason::v:return #v
    R(no_evidence);R(setup_started);R(setup_success);R(setup_failed);R(authority_mismatch);R(peer_mismatch);
    R(association_lost);R(removal_started);R(removed);R(reconnect_started);R(reconnect_failed);R(contradictory_result);
#undef R
  }
  return "UNKNOWN_REASON";
}
snapshot_observer::snapshot_observer(srsran::srs_cu_cp::cu_cp_impl& c,const std::string& path):cu(c),incarnation(c.l3_experiment_snapshot_incarnation())
{
  if (incarnation.empty()) throw std::runtime_error("snapshot activation unavailable");
  std::filesystem::path p(path);
  if (!p.is_absolute() || p.filename()=="." || p.filename()=="..") throw std::runtime_error("invalid output path");
  int parent=::open(p.parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  struct stat d{};
  if (parent<0) throw std::runtime_error("private output directory unavailable");
  if (fstat(parent,&d) || d.st_uid!=getuid() || (d.st_mode&0077)) {::close(parent);throw std::runtime_error("unsafe output directory");}
  fd=::openat(parent,p.filename().c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);::close(parent);
  if(fd<0) throw std::runtime_error("exclusive evidence creation failed");
}
snapshot_observer::~snapshot_observer(){current.reset();if(fd>=0)::close(fd);}
void snapshot_observer::sample(const char* phase)
{
  // Drop the preceding ticket before admission; a queued producer may still retain it.
  current.reset();current=cu.l3_experiment_request_snapshot();ns::snapshot v{};
  if(current){
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(20);
    do {v=current->inspect(incarnation);if(v.valid&&v.complete)break;
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }while(std::chrono::steady_clock::now()<deadline);
  }
  record(v,phase);
}
void snapshot_observer::after_stop()
{
  ns::snapshot v{};if(current)v=current->inspect(incarnation);
  if(v.valid)throw std::runtime_error("retained ticket valid after shutdown");
  record(v,"AFTER_STOP");current.reset();if(::fsync(fd))throw std::runtime_error("evidence sync failed");
}
void snapshot_observer::record(const ns::snapshot& v,const char* phase)
{
  if(++records>5000)throw std::runtime_error("record budget exceeded");
  const auto now=ns::now();
  const bool trusted=v.valid&&v.complete;
  ns::text nf,peer,name;nf.set("l3-d0-cucp-01");peer.set("l3-d0-amf-01@n2-loopback");name.set("l3-d0-amf-01");
  if(trusted && (!(v.process_incarnation==incarnation)||!(v.local_nf_id==nf)||v.peer_count!=1||
     v.peers[0].binding.amf_index!=0||!(v.peers[0].binding.peer_id==peer)||!(v.peers[0].binding.expected_amf_name==name)))
    throw std::runtime_error("snapshot identity mismatch");
  const ns::row& r=v.peers[0];
  std::ostringstream o;o<<std::boolalpha;
  o<<"{\"schema_version\":\"l3-d1-producer-v1\",\"phase\":\""<<phase<<"\",\"valid\":"<<v.valid<<",\"complete\":"<<v.complete
   <<",\"process_incarnation\":"<<quote(incarnation)<<",\"local_nf_id\":\"l3-d0-cucp-01\",\"peer_id\":\"l3-d0-amf-01@n2-loopback\",\"amf_index\":0"
   <<",\"expected_amf_name\":\"l3-d0-amf-01\",\"observed_amf_name\":"<<quote(r.observed_amf_name)
   <<",\"application_state\":\""<<(trusted?state(r.application_state):"UNKNOWN")<<"\",\"reason\":\""<<reason(r.why)<<"\",\"generation\":"<<r.association_generation
   <<",\"sequence\":"<<v.sequence<<",\"revision\":"<<v.state_revision<<",\"producer_wall_ns\":"<<v.observed_at.wall_ns
   <<",\"producer_monotonic_ns\":"<<v.observed_at.monotonic_ns<<",\"observer_wall_ns\":"<<now.wall_ns<<",\"observer_monotonic_ns\":"<<now.monotonic_ns
   <<",\"transition_known\":"<<r.transition_known<<",\"transition_wall_ns\":";
  if(r.transition_known)o<<r.last_transition.wall_ns;else o<<"null";
  o<<",\"transition_monotonic_ns\":";if(r.transition_known)o<<r.last_transition.monotonic_ns;else o<<"null";
  o<<"}\n";const auto text=o.str();
  if(text.size()>8192||bytes+text.size()>8*1024*1024)throw std::runtime_error("evidence byte budget exceeded");
  size_t at=0;while(at<text.size()) {ssize_t n=::write(fd,text.data()+at,text.size()-at);if(n<0&&errno==EINTR)continue;if(n<=0)throw std::runtime_error("evidence write failed");at+=n;}bytes+=text.size();
}
}
