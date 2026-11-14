"""Require all preserved and new native cases; no successful skips or expiry fallback."""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def check_components(xml_text, inventory):
    root = ET.fromstring(xml_text)
    if int(root.get('tests', '0')) != 2:
        raise ValueError('Expected exactly two component test registrations')
    for field in ('failures', 'skipped', 'disabled', 'errors'):
        if int(root.get(field, '0')):
            raise ValueError('Component failure, skip or incomplete execution')
    found = {}
    for case in root.findall('testcase'):
        name = case.get('name')
        if name not in inventory or name in found or case.get('status') != 'run':
            raise ValueError('Unexpected or unexecuted component')
        if any(case.find(tag) is not None for tag in ('failure', 'skipped', 'error')):
            raise ValueError('Component not successful')
        output = case.findtext('system-out') or ''
        passed = re.findall(r'^PASS ([a-z_]+)$', output, re.M)
        started = re.findall(r'^RUN ([a-z_]+)$', output, re.M)
        if passed != inventory[name] or started != inventory[name]:
            raise ValueError('Missing, duplicate or incomplete native assertion evidence')
        found[name] = len(passed)
    if set(found) != set(inventory):
        raise ValueError('Missing component result')
    return found


if __name__ == '__main__':
    folder = Path(sys.argv[1])
    inventory = json.loads((Path(__file__).parent/'test-inventory.json').read_text())['components']
    result = check_components((folder/'snapshot.xml').read_text(), inventory)
    for path in folder.iterdir():
        if path.is_file() and (path.name.startswith(('asan.', 'ubsan.', 'tsan.')) or path.name == 'native.log'):
            if re.search(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|WARNING: ThreadSanitizer|runtime error:', path.read_text(errors='replace')):
                raise SystemExit('Sanitizer diagnostic present; inspect preserved evidence')
    (folder/'case-counts.json').write_text(json.dumps(result, indent=2)+'\n')
    print(result)
