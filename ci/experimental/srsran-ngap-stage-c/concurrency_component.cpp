// SYNTHETIC functional concurrency cases, NOT a load/stress or live protocol test.
#include "lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h"
#include "srsran/support/executors/task_worker.h"
#include "srsran/support/executors/strand_executor.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <future>
#include <thread>
#include <utility>

using namespace srsran;
using namespace srsran::srs_cu_cp::ngap_snapshot;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while (false)

template <typename T> T bounded_get(std::future<T>& value) {
  CHECK(value.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
  return value.get();
}
void gate_wait(const std::shared_future<void>& gate) {
  CHECK(gate.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
  gate.get();
}
void unknown(const snapshot& value) {
  CHECK(!value.valid);
  CHECK(!value.complete);
  for (const auto& peer : value.peers) CHECK(peer.application_state == state::unknown);
}

struct environment {
  // Same strand type as approved cu_cp_executor_mapper.cpp; a real worker thread.
  task_worker worker{"stage_c_owner", 128};
  task_worker_executor worker_executor{worker};
  task_strand<task_executor*, concurrent_queue_policy::lockfree_mpmc> owner{static_cast<task_executor*>(&worker_executor), 64};
  std::unique_ptr<source> producer; // Access/destruction on owner, except documented request() admission.
  text incarnation;
  std::atomic<unsigned> active{0};

  template <typename F> std::future<void> submit(F action) {
    auto done = std::make_shared<std::promise<void>>();
    auto result = done->get_future();
    CHECK(owner.defer([this, done, fn=std::move(action)]() mutable {
      CHECK(std::this_thread::get_id() == worker.get_id());
      CHECK(active.fetch_add(1) == 0);
      fn();
      CHECK(active.fetch_sub(1) == 1);
      done->set_value();
    }));
    return result;
  }
  template <typename F> void on(F action) { auto done = submit(std::move(action)); bounded_get(done); }
  void fence() { on([]{}); }
  void worker_fence() {
    // Bypass the intentionally full strand only for a drain sentinel, never lifecycle writes.
    auto done=std::make_shared<std::promise<void>>(); auto result=done->get_future();
    CHECK(worker_executor.defer([done] { done->set_value(); })); bounded_get(result);
  }
  environment() {
    on([this] {
      config c; c.enabled = true; CHECK(c.local_nf_id.set("SYNTHETIC_CU")); c.peer_count = 2;
      for (unsigned i=0; i<2; ++i) {
        c.peers[i].amf_index=i;
        CHECK(c.peers[i].peer_id.set(i ? "SYNTHETIC_B" : "SYNTHETIC_A"));
        CHECK(c.peers[i].expected_amf_name.set("SYNTHETIC_AMF"));
      }
      producer=source::create(c,owner); CHECK(producer);
      incarnation=producer->incarnation();
    });
  }
  ~environment() {
    on([this] { if(producer) { producer->close(); producer.reset(); } });
    worker.stop(); // All users/admitted tasks joined/fenced before executor teardown.
  }
  uint64_t up(unsigned peer=0) { // Invoke ONLY inside on()/submit().
    auto generation=producer->begin(peer,true);
    CHECK(generation != 0);
    producer->setup_result(peer,true,"SYNTHETIC_AMF",true,generation);
    return generation;
  }
  std::shared_ptr<ticket> read() {
    std::shared_ptr<ticket> result;
    on([&] { result=producer->request(); CHECK(result); });
    fence(); // FIFO: deferred copy ran before this task, no polling or sleeps.
    return result;
  }
  snapshot fresh() {
    auto result=read(); auto value=result->inspect(incarnation);
    CHECK(value.valid); CHECK(value.complete); return value;
  }
};

void queued_then_loss() {
  environment e; std::shared_ptr<ticket> result;
  e.on([&] { e.up(); result=e.producer->request(); CHECK(result); e.producer->lost(0); });
  e.fence();
  auto value=result->inspect(e.incarnation);
  CHECK(value.valid); CHECK(value.peers[0].application_state==state::down);
  result.reset(); CHECK(e.fresh().peers[0].application_state==state::down);
}
void queued_then_reconnect() {
  environment e; std::shared_ptr<ticket> result;
  e.on([&] { e.up(); result=e.producer->request(); CHECK(result); e.producer->lost(0); e.up(); });
  e.fence(); auto value=result->inspect(e.incarnation);
  CHECK(value.valid); CHECK(value.peers[0].application_state==state::up);
  result.reset(); CHECK(e.fresh().peers[0].application_state==state::up);
}
void stale_completion() {
  environment e; uint64_t old=0;
  e.on([&] { old=e.up(); e.producer->lost(0); e.up(); });
  auto before=e.fresh();
  // Completion supplied by another thread is dispatched to owner, never written cross-thread.
  std::thread completion([&] { e.on([&] { e.producer->setup_result(0,false,{},true,old); }); });
  completion.join(); auto after=e.fresh();
  CHECK(after.peers[0].application_state==state::up);
  CHECK(after.state_revision==before.state_revision);
}
void shutdown_queued() {
  environment e; std::shared_ptr<ticket> result;
  e.on([&] { e.up(); result=e.producer->request(); CHECK(result); e.producer->close(); });
  e.fence(); auto value=result->inspect(e.incarnation); unknown(value); CHECK(value.sequence==0);
}
void retained_ticket_destruction() {
  environment e; e.on([&] { e.up(); }); auto result=e.read();
  const auto before=result->inspect(e.incarnation);
  CHECK(before.valid && before.complete); CHECK(before.peer_count==2);
  CHECK(before.process_incarnation==e.incarnation);
  CHECK(before.peers[0].application_state==state::up);
  std::promise<void> start, owner_completed;
  auto gate=start.get_future().share(); auto completed=owner_completed.get_future().share();
  std::thread reader([&] {
    gate_wait(gate);
    for(unsigned i=0;i<16;++i) {
      auto value=result->inspect(e.incarnation);
      // An inspection overlapping the owner transition may linearize before or after invalidation.
      if(value.valid) {
        CHECK(value.complete); CHECK(value.process_incarnation==e.incarnation);
        CHECK(value.local_nf_id==before.local_nf_id);
        CHECK(value.state_revision==before.state_revision); CHECK(value.sequence==before.sequence);
        CHECK(value.observed_at.wall_ns==before.observed_at.wall_ns);
        CHECK(value.observed_at.monotonic_ns==before.observed_at.monotonic_ns);
        CHECK(value.peer_count==before.peer_count);
        for(unsigned p=0;p<value.peer_count;++p) {
          CHECK(value.peers[p].binding.amf_index==before.peers[p].binding.amf_index);
          CHECK(value.peers[p].binding.peer_id==before.peers[p].binding.peer_id);
          CHECK(value.peers[p].binding.expected_amf_name==before.peers[p].binding.expected_amf_name);
          CHECK(value.peers[p].observed_amf_name==before.peers[p].observed_amf_name);
          CHECK(value.peers[p].association_generation==before.peers[p].association_generation);
          CHECK(value.peers[p].application_state==before.peers[p].application_state);
          CHECK(value.peers[p].setup_pending==before.peers[p].setup_pending);
          CHECK(value.peers[p].transition_known==before.peers[p].transition_known);
          CHECK(value.peers[p].why==before.peers[p].why);
          CHECK(value.peers[p].last_transition.wall_ns==before.peers[p].last_transition.wall_ns);
          CHECK(value.peers[p].last_transition.monotonic_ns==before.peers[p].last_transition.monotonic_ns);
        }
      } else unknown(value);
    }
    gate_wait(completed); // Owner completion establishes happens-before for strict invalidation checks.
    for(unsigned i=0;i<16;++i) unknown(result->inspect(e.incarnation));
  });
  auto destroyed=e.submit([&] { gate_wait(gate); e.producer.reset(); owner_completed.set_value(); });
  start.set_value(); bounded_get(destroyed); reader.join();
  unknown(result->inspect(e.incarnation)); // Retained ticket inspection never dereferences the source.
}
void competing_requests() {
  environment e; std::shared_ptr<ticket> result; source* stable=nullptr;
  std::promise<void> queued,release; auto queued_done=queued.get_future(); auto release_gate=release.get_future().share();
  auto owner_done=e.submit([&] {
    e.up(0); e.up(1); stable=e.producer.get(); result=stable->request(); CHECK(result);
    queued.set_value(); gate_wait(release_gate); // Bounded test rendezvous only.
  });
  bounded_get(queued_done);
  std::promise<void> a_ready,b_ready; auto af=a_ready.get_future(); auto bf=b_ready.get_future();
  std::atomic<unsigned> rejected{0};
  auto attempt=[&](std::promise<void>& ready) {
    CHECK(!stable->request()); ++rejected; unknown(result->inspect(e.incarnation));
    ready.set_value(); gate_wait(release_gate);
    // Actual reader threads contend with the real owner's deferred publication.
    for(unsigned i=0;i<15;++i) {
      CHECK(!stable->request()); ++rejected;
      auto value=result->inspect(e.incarnation);
      if(value.valid) {
        CHECK(value.peer_count==2);
        CHECK(value.peers[0].application_state==state::up && value.peers[1].application_state==state::up);
      } else unknown(value);
    }
  };
  std::thread a([&] { attempt(a_ready); }),b([&] { attempt(b_ready); });
  bounded_get(af); bounded_get(bf); release.set_value();
  a.join(); b.join(); bounded_get(owner_done); e.fence(); CHECK(rejected.load()==32);
  auto published=result->inspect(e.incarnation); CHECK(published.valid); CHECK(published.peer_count==2);
  // Retained ticket still bounds admission after the publisher completes.
  CHECK(!stable->request()); result.reset(); CHECK(e.fresh().valid);
}
void executor_rejection() {
  environment e; bool full=false;
  e.on([&] {
    // Fill only this test's 64-slot strand while its owner task is active.
    for(unsigned i=0;i<128;++i) { if(!e.owner.defer([]{})) { full=true; break; } }
    CHECK(full); CHECK(!e.producer->request());
  });
  e.worker_fence(); e.fence(); CHECK(e.fresh().valid); // Full strand drained before resubmission.
}
void revision_invalidation() {
  environment e; e.on([&] { e.up(); }); auto result=e.read();
  const auto before=result->inspect(e.incarnation);
  CHECK(before.valid && before.complete); CHECK(before.peer_count==2);
  CHECK(before.process_incarnation==e.incarnation);
  CHECK(before.peers[0].application_state==state::up);
  std::promise<void> start, owner_completed;
  auto gate=start.get_future().share(); auto completed=owner_completed.get_future().share();
  std::thread reader([&] {
    gate_wait(gate);
    for(unsigned i=0;i<16;++i) {
      auto value=result->inspect(e.incarnation);
      // An inspection overlapping the owner transition may linearize before or after invalidation.
      if(value.valid) {
        CHECK(value.complete); CHECK(value.process_incarnation==e.incarnation);
        CHECK(value.local_nf_id==before.local_nf_id);
        CHECK(value.state_revision==before.state_revision); CHECK(value.sequence==before.sequence);
        CHECK(value.observed_at.wall_ns==before.observed_at.wall_ns);
        CHECK(value.observed_at.monotonic_ns==before.observed_at.monotonic_ns);
        CHECK(value.peer_count==before.peer_count);
        for(unsigned p=0;p<value.peer_count;++p) {
          CHECK(value.peers[p].binding.amf_index==before.peers[p].binding.amf_index);
          CHECK(value.peers[p].binding.peer_id==before.peers[p].binding.peer_id);
          CHECK(value.peers[p].binding.expected_amf_name==before.peers[p].binding.expected_amf_name);
          CHECK(value.peers[p].observed_amf_name==before.peers[p].observed_amf_name);
          CHECK(value.peers[p].association_generation==before.peers[p].association_generation);
          CHECK(value.peers[p].application_state==before.peers[p].application_state);
          CHECK(value.peers[p].setup_pending==before.peers[p].setup_pending);
          CHECK(value.peers[p].transition_known==before.peers[p].transition_known);
          CHECK(value.peers[p].why==before.peers[p].why);
          CHECK(value.peers[p].last_transition.wall_ns==before.peers[p].last_transition.wall_ns);
          CHECK(value.peers[p].last_transition.monotonic_ns==before.peers[p].last_transition.monotonic_ns);
        }
      } else unknown(value);
    }
    gate_wait(completed); // Owner completion establishes happens-before for strict invalidation checks.
    for(unsigned i=0;i<16;++i) unknown(result->inspect(e.incarnation));
  });
  auto changed=e.submit([&] { gate_wait(gate); e.producer->lost(0); owner_completed.set_value(); });
  start.set_value(); bounded_get(changed); reader.join();
  unknown(result->inspect(e.incarnation)); // Retained ticket inspection never dereferences the source.
  result.reset(); const auto current=e.fresh();
  CHECK(current.peers[0].application_state==state::down);
  CHECK(current.state_revision>before.state_revision);
  CHECK(current.peers[0].association_generation>before.peers[0].association_generation);
}
void independent_peers() {
  environment e; e.on([&] { e.up(0); e.up(1); });
  std::thread loss([&] { e.on([&] { e.producer->lost(0); }); }); loss.join();
  auto value=e.fresh(); CHECK(value.peer_count==2);
  CHECK(value.peers[0].application_state==state::down); CHECK(value.peers[1].application_state==state::up);
}
void publication_ordering() {
  environment e; std::shared_ptr<ticket> result;
  std::promise<void> queued,release; auto queued_done=queued.get_future(); auto gate=release.get_future().share();
  auto owner_done=e.submit([&] {
    e.up(0); e.up(1); result=e.producer->request(); CHECK(result);
    queued.set_value(); gate_wait(gate);
  });
  bounded_get(queued_done);
  // Main thread observes not-ready while worker is held before deferred publication.
  unknown(result->inspect(e.incarnation)); release.set_value(); bounded_get(owner_done); e.fence();
  auto value=result->inspect(e.incarnation);
  CHECK(value.valid);
  CHECK(value.sequence==1); CHECK(value.peer_count==2);
  CHECK(value.peers[0].application_state==state::up && value.peers[1].application_state==state::up);
  CHECK(value.observed_at.monotonic_ns>0);
  result.reset(); auto confirmed=e.fresh();
  CHECK(confirmed.peers[0].application_state==state::up && confirmed.peers[1].application_state==state::up);
}
int main() {
  struct test_case { const char* name; void (*run)(); };
  const test_case cases[]={
    {"queued_then_loss",queued_then_loss},{"queued_then_reconnect",queued_then_reconnect},
    {"stale_completion",stale_completion},{"shutdown_queued",shutdown_queued},
    {"retained_ticket_destruction",retained_ticket_destruction},{"competing_requests",competing_requests},
    {"executor_rejection",executor_rejection},{"revision_invalidation",revision_invalidation},
    {"independent_peers",independent_peers},{"publication_ordering",publication_ordering}
  };
  for(const auto& test:cases) { std::printf("RUN %s\n",test.name); std::fflush(stdout); test.run(); std::printf("PASS %s\n",test.name); }
  std::puts("PASS 10 functional native concurrency cases; no universal race-freedom or timing guarantee");
}
