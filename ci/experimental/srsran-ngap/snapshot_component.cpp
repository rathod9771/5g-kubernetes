// SYNTHETIC native component assertions. No socket, service, or live AMF.
// Single-thread owner model: this is NOT thread-safety or runtime validation.
#include "lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h"
#include <cstdio>
#include <cstdlib>
#include <utility>

using namespace srsran;
using namespace srsran::srs_cu_cp::ngap_snapshot;

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (false)

struct owner_mock final : task_executor {
  unique_task pending;
  bool accept = true;
  unsigned runs = 0;

  bool execute(unique_task task) override { return defer(std::move(task)); }
  bool defer(unique_task task) override {
    if (!pending.is_empty()) return false;
    if (!accept) return false;
    pending = std::move(task);
    return true;
  }
  void drain() {
    CHECK(!pending.is_empty());
    auto task = std::move(pending);
    CHECK(pending.is_empty());
    ++runs;
    task();
  }
};

config enabled_config() {
  config value;
  value.enabled = true;
  CHECK(value.local_nf_id.set("SYNTHETIC_CU"));
  value.peer_count = 2;
  for (unsigned i = 0; i < value.peer_count; ++i) {
    value.peers[i].amf_index = i;
    CHECK(value.peers[i].peer_id.set(i ? "SYNTHETIC_PEER_B" : "SYNTHETIC_PEER_A"));
    CHECK(value.peers[i].expected_amf_name.set("SYNTHETIC_AMF"));
  }
  return value;
}

struct fixture {
  owner_mock owner;
  std::unique_ptr<source> producer;

  fixture() : producer(source::create(enabled_config(), owner)) { CHECK(producer); }
  std::shared_ptr<ticket> published() {
    auto result = producer->request();
    CHECK(result);
    CHECK(!producer->request()); // One outstanding ticket, including after publication.
    owner.drain();
    return result;
  }
  snapshot take() {
    auto result = published();
    auto value = result->inspect(producer->incarnation());
    CHECK(value.valid);
    CHECK(value.complete);
    return value;
  }
  uint64_t up(uint32_t index = 0, bool reconnect = false) {
    auto generation = producer->begin(index, reconnect);
    CHECK(generation != 0);
    producer->setup_result(index, true, "SYNTHETIC_AMF", true, generation);
    return generation;
  }
};

void check_unknown(const snapshot& value) {
  CHECK(!value.valid);
  CHECK(!value.complete);
  for (const auto& peer : value.peers) CHECK(peer.application_state == state::unknown);
}

void default_off() {
  owner_mock owner;
  config disabled;
  CHECK(!disabled.enabled);
  CHECK(!source::create(disabled, owner));
  auto configured_but_disabled = enabled_config();
  configured_but_disabled.enabled = false;
  CHECK(!source::create(configured_but_disabled, owner));
  CHECK(owner.pending.is_empty());
  CHECK(owner.runs == 0);
}
void setup_success() {
  fixture f;
  auto initial = f.take();
  CHECK(initial.peers[0].application_state == state::unknown);
  CHECK(!initial.peers[0].transition_known);
  auto generation = f.up();
  auto value = f.take();
  CHECK(value.peers[0].application_state == state::up);
  CHECK(value.peers[0].why == reason::setup_success);
  CHECK(value.peers[0].association_generation == generation);
  CHECK(value.peers[0].transition_known);
}
void setup_failure() {
  fixture f;
  auto generation = f.producer->begin(0);
  f.producer->setup_result(0, false, {}, true, generation);
  auto value = f.take();
  CHECK(value.peers[0].application_state == state::down);
  CHECK(value.peers[0].why == reason::setup_failed);
}
void independent_amfs() {
  fixture f;
  f.up(0);
  f.up(1);
  f.producer->lost(0);
  auto value = f.take();
  CHECK(value.peer_count == 2);
  CHECK(value.peers[0].binding.amf_index == 0);
  CHECK(value.peers[1].binding.amf_index == 1);
  CHECK(value.peers[0].application_state == state::down);
  CHECK(value.peers[1].application_state == state::up);
}
void stale_after_disconnect() {
  fixture f;
  auto old = f.producer->begin(0);
  f.producer->lost(0);
  auto before = f.take();
  f.producer->setup_result(0, true, "SYNTHETIC_AMF", true, old);
  auto after = f.take();
  CHECK(after.peers[0].application_state == state::down);
  CHECK(after.state_revision == before.state_revision);
}
void stale_after_reconnect() {
  fixture f;
  auto old = f.up();
  f.producer->lost(0);
  auto current = f.producer->begin(0, true);
  CHECK(current > old);
  f.producer->setup_result(0, true, "SYNTHETIC_AMF", true, old);
  CHECK(f.take().peers[0].application_state == state::unknown);
  f.producer->setup_result(0, true, "SYNTHETIC_AMF", true, current);
  auto before = f.take();
  f.producer->setup_result(0, false, {}, true, old);
  auto after = f.take();
  CHECK(after.peers[0].application_state == state::up);
  CHECK(after.state_revision == before.state_revision);
}
void duplicate_success() {
  fixture f;
  auto generation = f.up();
  auto before = f.take();
  f.producer->setup_result(0, true, "SYNTHETIC_AMF", true, generation);
  auto after = f.take();
  CHECK(after.peers[0].application_state == state::up);
  CHECK(after.state_revision == before.state_revision);
  CHECK(after.peers[0].association_generation == before.peers[0].association_generation);
  CHECK(after.peers[0].last_transition.monotonic_ns == before.peers[0].last_transition.monotonic_ns);
}
void duplicate_failure() {
  fixture f;
  auto generation = f.producer->begin(0);
  f.producer->setup_result(0, false, {}, true, generation);
  auto before = f.take();
  f.producer->setup_result(0, false, {}, true, generation);
  auto after = f.take();
  CHECK(after.peers[0].application_state == state::down);
  CHECK(after.state_revision == before.state_revision);
}
void contradictory_success_then_failure() {
  fixture f;
  auto generation = f.up();
  f.producer->setup_result(0, false, {}, true, generation);
  auto value = f.take();
  CHECK(value.peers[0].application_state == state::unknown);
  CHECK(value.peers[0].why == reason::contradictory_result);
  CHECK(value.peers[0].association_generation > generation);
  f.producer->setup_result(0, true, "SYNTHETIC_AMF", true, generation);
  CHECK(f.take().peers[0].application_state == state::unknown);
}
void contradictory_failure_then_success() {
  fixture f;
  auto generation = f.producer->begin(0);
  f.producer->setup_result(0, false, {}, true, generation);
  f.producer->setup_result(0, true, "SYNTHETIC_AMF", true, generation);
  auto value = f.take();
  CHECK(value.peers[0].application_state == state::unknown);
  CHECK(value.peers[0].why == reason::contradictory_result);
}
void incorrect_peer() {
  fixture f;
  auto generation = f.producer->begin(0);
  f.producer->setup_result(0, true, "SYNTHETIC_WRONG_AMF", true, generation);
  auto value = f.take();
  CHECK(value.peers[0].application_state == state::unknown);
  CHECK(value.peers[0].why == reason::peer_mismatch);
}
void superseded_snapshot() {
  fixture f;
  f.up();
  auto result = f.published();
  CHECK(result->inspect(f.producer->incarnation()).valid);
  f.producer->lost(0);
  check_unknown(result->inspect(f.producer->incarnation()));
}
void rejected_defer() {
  fixture f;
  f.owner.accept = false;
  CHECK(!f.producer->request());
  CHECK(f.owner.pending.is_empty());
  CHECK(f.owner.runs == 0);
  f.owner.accept = true;
  auto value = f.take(); // Failed admission must release the busy guard.
  CHECK(value.sequence == 1);
}
void shutdown_queued() {
  fixture f;
  f.up();
  auto result = f.producer->request();
  CHECK(result);
  CHECK(!result->inspect(f.producer->incarnation()).valid);
  f.producer->close();
  CHECK(!f.producer->request());
  f.owner.drain();
  auto value = result->inspect(f.producer->incarnation());
  check_unknown(value);
  CHECK(value.sequence == 0); // No fabricated heartbeat from queued work after close.
}
void shutdown_published() {
  fixture f;
  f.up();
  auto result = f.published();
  CHECK(result->inspect(f.producer->incarnation()).valid);
  f.producer->close();
  check_unknown(result->inspect(f.producer->incarnation()));
  CHECK(!f.producer->request());
}
void incarnation_mismatch() {
  fixture f;
  f.up();
  auto result = f.published();
  text wrong;
  CHECK(wrong.set("SYNTHETIC_WRONG_PROCESS"));
  check_unknown(result->inspect(wrong));
  CHECK(result->inspect(f.producer->incarnation()).valid);
}
void bounded_size() {
  static_assert(sizeof(snapshot) <= 65536, "Native snapshot exceeds approved bound");
  CHECK(sizeof(snapshot) <= 65536);
  owner_mock owner;
  auto invalid = enabled_config();
  invalid.peer_count = max_peers + 1;
  CHECK(!source::create(invalid, owner));
  CHECK(owner.pending.is_empty());
}
void deferred_ownership() {
  fixture f;
  auto result = f.producer->request();
  CHECK(result);
  CHECK(f.owner.runs == 0);
  CHECK(!f.owner.pending.is_empty());
  CHECK(!result->inspect(f.producer->incarnation()).valid);
  CHECK(!f.producer->request());
  f.owner.drain();
  CHECK(f.owner.runs == 1);
  CHECK(f.owner.pending.is_empty());
  CHECK(result->inspect(f.producer->incarnation()).valid);
  CHECK(!f.producer->request());
  result.reset();
  CHECK(f.take().sequence == 2);
}
void removal_generation() {
  fixture f;
  f.up();
  auto generation = f.producer->removing(0);
  CHECK(f.take().peers[0].application_state == state::unknown);
  f.producer->removed(0, true, generation);
  CHECK(f.take().peers[0].application_state == state::down);
  f.up(0, true);
  f.producer->removed(0, true, generation);
  CHECK(f.take().peers[0].application_state == state::up);
}
void publication_metadata() {
  fixture f;
  auto result = f.published();
  auto first = result->inspect(f.producer->incarnation());
  auto second = result->inspect(f.producer->incarnation());
  CHECK(first.valid && second.valid);
  CHECK(first.observed_at.wall_ns > 0);
  CHECK(first.observed_at.monotonic_ns > 0);
  CHECK(first.sequence == second.sequence);
  CHECK(first.observed_at.monotonic_ns == second.observed_at.monotonic_ns);
  // No sleep/clock substitution: this does NOT test 10ms/30s expiry boundaries.
}

int main() {
  struct test_case { const char* name; void (*run)(); };
  const test_case cases[] = {
    {"default_off", default_off}, {"setup_success", setup_success},
    {"setup_failure", setup_failure}, {"independent_amfs", independent_amfs},
    {"stale_after_disconnect", stale_after_disconnect}, {"stale_after_reconnect", stale_after_reconnect},
    {"duplicate_success", duplicate_success}, {"duplicate_failure", duplicate_failure},
    {"contradictory_success_then_failure", contradictory_success_then_failure},
    {"contradictory_failure_then_success", contradictory_failure_then_success},
    {"incorrect_peer", incorrect_peer}, {"superseded_snapshot", superseded_snapshot},
    {"rejected_defer", rejected_defer}, {"shutdown_queued", shutdown_queued},
    {"shutdown_published", shutdown_published}, {"incarnation_mismatch", incarnation_mismatch},
    {"bounded_size", bounded_size}, {"deferred_ownership", deferred_ownership},
    {"removal_generation", removal_generation}, {"publication_metadata", publication_metadata}
  };
  for (const auto& test : cases) {
    std::printf("RUN %s\n", test.name);
    std::fflush(stdout);
    test.run();
    std::printf("PASS %s\n", test.name);
  }
  std::puts("PASS 20 SYNTHETIC native component cases; no thread-safety or real NGAP accuracy claim");
}
