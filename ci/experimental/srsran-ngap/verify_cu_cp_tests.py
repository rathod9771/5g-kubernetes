"""Fail closed on empty, missing-suite or skipped native CU-CP test evidence."""
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

EXPECTED = {"cu_cp_connectivity_test", "cu_cp_setup_test"}


def verify_listing(text):
    found = {name: set() for name in EXPECTED}
    current = None
    for line in text.splitlines():
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        if not line[:1].isspace():
            current = value[:-1] if value.endswith(".") and value[:-1] in EXPECTED else None
        elif current is not None:
            found[current].add(value)
    if not all(found.values()):
        raise ValueError("Selected native test filter did not discover both required suites")
    return {name: len(tests) for name, tests in sorted(found.items())}


def verify_results(text):
    root = ET.fromstring(text)
    cases = root.findall(".//testcase")
    found = {name: 0 for name in EXPECTED}
    if root.tag != "testsuites" or int(root.get("tests", "0")) != len(cases):
        raise ValueError("Missing or contradictory native test result totals")
    if int(root.get("failures", "0")) or int(root.get("errors", "0")):
        raise ValueError("Native test report contains failures or errors")
    for case in cases:
        suite = case.get("classname")
        if suite not in EXPECTED:
            raise ValueError("Unexpected suite in filtered native results")
        if (case.find("skipped") is not None or case.find("failure") is not None
                or case.find("error") is not None or case.get("status") != "run"
                or case.get("result") != "completed"):
            raise ValueError("Native test was skipped, failed or not completed")
        found[suite] += 1
    if not all(found.values()):
        raise ValueError("Both required native suites must actually execute")
    return dict(sorted(found.items()))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("listing", "results"):
        raise SystemExit("usage: verify_cu_cp_tests.py listing|results FILE")
    verify = verify_listing if sys.argv[1] == "listing" else verify_results
    print(verify(Path(sys.argv[2]).read_text()))
