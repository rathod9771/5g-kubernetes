// SYNTHETIC deterministic boundary tests; no network or real-time accuracy claim.
#ifndef SRSRAN_NGAP_SNAPSHOT_TEST_CLOCK
#error "Freshness executable requires its private test-clock definition"
#endif
#include "lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h"
#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <thread>
#include <utility>

using namespace srsran;
using namespace srsran::srs_cu_cp::ngap_snapshot;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)

namespace {
// One fixture and one thread; clock writes occur only between explicit queue-drain calls.
stamp* active_clock = nullptr;
std::thread::id clock_owner;
constexpr int64_t base_mono = 1000000000LL;
constexpr int64_t base_wall = 1700000000000000000LL;
}
namespace srsran { namespace srs_cu_cp { namespace ngap_snapshot {
stamp l3_ngap_snapshot_test_clock_now() noexcept {
  CHECK(active_clock != nullptr);
  CHECK(std::this_thread::get_id() == clock_owner);
  return *active_clock;
}
} } }

struct owner_mock final : task_executor {
  unique_task pending;
  bool execute(unique_task task) override { return defer(std::move(task)); }
  bool defer(unique_task task) override {
    CHECK(std::this_thread::get_id() == clock_owner);
    if (!pending.is_empty()) return false;
    pending=std::move(task); return true;
  }
  void drain() {
    CHECK(std::this_thread::get_id() == clock_owner);
    CHECK(!pending.is_empty()); auto task=std::move(pending);
    CHECK(pending.is_empty()); task();
  }
};

struct fixture {
  stamp clock{base_wall,base_mono};
  owner_mock owner;
  std::unique_ptr<source> producer;
  text incarnation;
  fixture() {
    CHECK(active_clock==nullptr); active_clock=&clock; clock_owner=std::this_thread::get_id();
    config c; c.enabled=true; CHECK(c.local_nf_id.set("SYNTHETIC_CU")); c.peer_count=2;
    for(unsigned i=0;i<2;++i) {
      c.peers[i].amf_index=i;
      CHECK(c.peers[i].peer_id.set(i ? "SYNTHETIC_B" : "SYNTHETIC_A"));
      CHECK(c.peers[i].expected_amf_name.set("SYNTHETIC_AMF"));
    }
    producer=source::create(c,owner); CHECK(producer); incarnation=producer->incarnation();
    for(unsigned i=0;i<2;++i) {
      auto generation=producer->begin(i); CHECK(generation==1);
      producer->setup_result(i,true,"SYNTHETIC_AMF",true,generation);
    }
  }
  ~fixture() {
    CHECK(owner.pending.is_empty()); producer.reset(); active_clock=nullptr;
  }
  void at(int64_t delta) { clock={base_wall+delta,base_mono+delta}; }
  std::shared_ptr<ticket> publish() {
    auto result=producer->request(); CHECK(result); owner.drain(); return result;
  }
};
void invalid_unknown(const snapshot& value) {
  CHECK(!value.valid); CHECK(!value.complete);
  for(const auto& row:value.peers) CHECK(row.application_state==state::unknown);
}
void valid_up(const snapshot& value,const fixture& f) {
  CHECK(value.valid && value.complete); CHECK(value.process_incarnation==f.incarnation);
  CHECK(value.peer_count==2); CHECK(value.sequence==1);
  for(unsigned i=0;i<2;++i) {
    CHECK(value.peers[i].binding.amf_index==i);
    CHECK(value.peers[i].application_state==state::up);
    CHECK(value.peers[i].association_generation==1);
    CHECK(!value.peers[i].setup_pending);
    CHECK(value.peers[i].why==reason::setup_success);
  }
}
void publication_boundary(int64_t delta,bool expected_valid) {
  fixture f; auto result=f.producer->request(); CHECK(result);
  f.at(delta); f.owner.drain(); auto value=result->inspect(f.incarnation);
  if(expected_valid) {
    valid_up(value,f);
    CHECK(value.observed_at.monotonic_ns==base_mono+delta);
    CHECK(value.observed_at.wall_ns==base_wall+delta);
  } else {
    invalid_unknown(value); CHECK(value.sequence==0);
    CHECK(value.observed_at.monotonic_ns==0 && value.observed_at.wall_ns==0);
  }
}
void inspection_boundary(int64_t delta,bool expected_valid) {
  fixture f; auto result=f.publish(); valid_up(result->inspect(f.incarnation),f);
  f.at(delta); auto value=result->inspect(f.incarnation);
  if(expected_valid) valid_up(value,f); else invalid_unknown(value);
  // Inspection changes validity only; it does not create a new producer heartbeat.
  CHECK(value.sequence==1);
  CHECK(value.observed_at.monotonic_ns==base_mono && value.observed_at.wall_ns==base_wall);
}
void publication_minus_one() { publication_boundary(-1,false); }
void publication_zero() { publication_boundary(0,true); }
void publication_below_limit() { publication_boundary(9999999LL,true); }
void publication_at_limit() { publication_boundary(10000000LL,true); }
void publication_above_limit() { publication_boundary(10000001LL,false); }
void inspection_minus_one() { inspection_boundary(-1,false); }
void inspection_zero() { inspection_boundary(0,true); }
void inspection_below_limit() { inspection_boundary(29999999999LL,true); }
void inspection_at_limit() { inspection_boundary(30000000000LL,true); }
void inspection_above_limit() { inspection_boundary(30000000001LL,false); }
void retained_timestamp_immutable() {
  fixture f; auto result=f.publish(); const auto before=result->inspect(f.incarnation); valid_up(before,f);
  for(const int64_t delta:{1LL,1000000000LL,30000000000LL}) {
    f.at(delta); const auto value=result->inspect(f.incarnation); valid_up(value,f);
    CHECK(value.sequence==before.sequence && value.state_revision==before.state_revision);
    CHECK(value.observed_at.wall_ns==before.observed_at.wall_ns);
    CHECK(value.observed_at.monotonic_ns==before.observed_at.monotonic_ns);
    for(unsigned p=0;p<value.peer_count;++p) {
      CHECK(value.peers[p].last_transition.wall_ns==before.peers[p].last_transition.wall_ns);
      CHECK(value.peers[p].last_transition.monotonic_ns==before.peers[p].last_transition.monotonic_ns);
    }
  }
}
void revision_invalidates() {
  fixture f; auto result=f.publish(); const auto before=result->inspect(f.incarnation); valid_up(before,f);
  f.at(1); f.producer->lost(0); invalid_unknown(result->inspect(f.incarnation));
  result.reset(); auto next=f.publish(); const auto current=next->inspect(f.incarnation);
  CHECK(current.valid && current.complete); CHECK(current.process_incarnation==f.incarnation);
  CHECK(current.state_revision>before.state_revision);
  CHECK(current.sequence==before.sequence+1);
  CHECK(current.observed_at.monotonic_ns==base_mono+1);
  CHECK(current.peers[0].association_generation>before.peers[0].association_generation);
  CHECK(current.peers[0].application_state==state::down && current.peers[1].application_state==state::up);
}
void shutdown_invalidates() {
  fixture f; auto result=f.publish(); valid_up(result->inspect(f.incarnation),f);
  f.producer->close(); invalid_unknown(result->inspect(f.incarnation)); CHECK(!f.producer->request());
}
void incarnation_mismatch() {
  fixture f; auto result=f.publish(); text other; CHECK(other.set("SYNTHETIC_WRONG_INCARNATION"));
  invalid_unknown(result->inspect(other)); valid_up(result->inspect(f.incarnation),f);
}
void default_off() {
  fixture f; config c; CHECK(!c.enabled);
  CHECK(c.local_nf_id.set("SYNTHETIC_DISABLED_CU")); c.peer_count=1;
  CHECK(c.peers[0].peer_id.set("SYNTHETIC_DISABLED_PEER"));
  CHECK(c.peers[0].expected_amf_name.set("SYNTHETIC_AMF"));
  CHECK(!source::create(c,f.owner));
}
int main() {
  struct test_case { const char* name; void (*run)(); };
  const test_case cases[]={
    {"publication_minus_one",publication_minus_one},{"publication_zero",publication_zero},
    {"publication_below_limit",publication_below_limit},{"publication_at_limit",publication_at_limit},
    {"publication_above_limit",publication_above_limit},{"inspection_minus_one",inspection_minus_one},
    {"inspection_zero",inspection_zero},{"inspection_below_limit",inspection_below_limit},
    {"inspection_at_limit",inspection_at_limit},{"inspection_above_limit",inspection_above_limit},
    {"retained_timestamp_immutable",retained_timestamp_immutable},{"revision_invalidates",revision_invalidates},
    {"shutdown_invalidates",shutdown_invalidates},{"incarnation_mismatch",incarnation_mismatch},
    {"default_off",default_off}
  };
  for(const auto& test:cases) {
    std::printf("RUN %s\n",test.name); std::fflush(stdout); test.run(); std::printf("PASS %s\n",test.name);
  }
  std::puts("PASS 15 synthetic deterministic freshness cases; not a concurrent or live protocol test");
}
