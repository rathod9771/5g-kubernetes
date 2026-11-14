// Offline mock only: no socket, service, or live AMF.
#include "lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h"
#include <cstdio>
#include <cstdlib>
using namespace srsran;
using namespace srsran::srs_cu_cp::ngap_snapshot;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while (false)
struct owner_mock final : task_executor {
  unique_task pending;
  bool execute(unique_task t) override { return defer(std::move(t)); }
  bool defer(unique_task t) override { if(pending) return false; pending=std::move(t); return true; }
  void drain() { CHECK(bool(pending)); auto t=std::move(pending); t(); }
};
int main() {
 owner_mock owner; config c;
 CHECK(!source::create(c,owner));
 c.enabled=true; CHECK(c.local_nf_id.set("SYNTHETIC_CU")); c.peer_count=2;
 for(unsigned i=0;i<2;++i) { c.peers[i].amf_index=i; CHECK(c.peers[i].peer_id.set(i?"PEER_B":"PEER_A")); CHECK(c.peers[i].expected_amf_name.set("MOCK_AMF")); }
 auto s=source::create(c,owner); CHECK(s);
 auto take=[&](){ auto t=s->request(); CHECK(t); CHECK(!s->request()); owner.drain(); return t->inspect(s->incarnation()); };
 CHECK(take().peers[0].application_state==state::unknown);
 auto g=s->begin(0); s->setup_result(0,true,"MOCK_AMF",true,g);
 CHECK(take().peers[0].application_state==state::up);
 s->setup_result(0,true,"MOCK_AMF",true,g); CHECK(take().peers[0].application_state==state::up);
 s->lost(0); s->setup_result(0,true,"MOCK_AMF",true,g); CHECK(take().peers[0].application_state==state::down);
 g=s->begin(0,true); s->setup_result(0,false,"",true,g); CHECK(take().peers[0].application_state==state::down);
 g=s->begin(0,true); s->setup_result(0,true,"MOCK_AMF",true,g);
 auto b=s->begin(1); s->setup_result(1,true,"MOCK_AMF",true,b); s->lost(0);
 auto snap=take(); CHECK(snap.peers[0].application_state==state::down); CHECK(snap.peers[1].application_state==state::up);
 auto r=s->removing(1); s->removed(1,true,r); CHECK(take().peers[1].application_state==state::down);
 auto t=s->request(); CHECK(t); owner.drain(); text wrong; CHECK(wrong.set("WRONG_PROCESS")); CHECK(!t->inspect(wrong).valid); t.reset();
 t=s->request(); CHECK(t); s->close(); owner.drain(); CHECK(!t->inspect(s->incarnation()).valid); CHECK(!s->request());
 CHECK(sizeof(snapshot)<=65536);
 std::puts("PASS native snapshot mock lifecycle; no concurrency/runtime proof");
}
