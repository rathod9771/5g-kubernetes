#pragma once
#include "lib/cu_cp/cu_cp_impl.h"
#include <string>
#include <cstdint>
namespace l3_d1 {
// Only the outer experiment lifetime owner may call this object; never the CU executor.
class snapshot_observer {
public:
  snapshot_observer(srsran::srs_cu_cp::cu_cp_impl& cu, const std::string& path);
  ~snapshot_observer();
  snapshot_observer(const snapshot_observer&) = delete;
  snapshot_observer& operator=(const snapshot_observer&) = delete;
  void sample(const char* phase);
  void after_stop();
private:
  void record(const srsran::srs_cu_cp::ngap_snapshot::snapshot& value, const char* phase);
  srsran::srs_cu_cp::cu_cp_impl& cu;
  srsran::srs_cu_cp::ngap_snapshot::text incarnation;
  std::shared_ptr<srsran::srs_cu_cp::ngap_snapshot::ticket> current;
  int fd = -1;
  uint64_t bytes = 0;
  unsigned records = 0;
};
}
