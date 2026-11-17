# Added only to the disposable, fully rebuilt srsRAN experiment source tree.
add_executable(l3_ngap_protocol_runtime runtime_driver.cpp snapshot_observer.cpp)
target_include_directories(l3_ngap_protocol_runtime PRIVATE "${CMAKE_SOURCE_DIR}" "${CMAKE_SOURCE_DIR}/include")
target_link_libraries(l3_ngap_protocol_runtime PRIVATE srsran_cu_cp srsran_ngap srsran_pcap srsran_support srslog Threads::Threads)
# No clock overlay or private clock macro. Do not register a CTest runtime test.
