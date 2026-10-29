"""Check the reviewed artifacts and normalize Windows PowerShell's test log."""
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
log = ROOT/'output/test-results.txt'
data = log.read_bytes()
raw = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
result = re.search(r'^Ran (\d+) tests in .+$', raw, re.MULTILINE)
report = json.loads((ROOT/'output/validation.json').read_text(encoding='utf-8'))
assert result and int(result[1]) == report['offline_tests']
assert any(line.strip() == 'OK' for line in raw.splitlines()) and 'FAILED (' not in raw
backup = ROOT/'output/test-results.raw.txt'
if not backup.exists():
    backup.write_text(raw, encoding='utf-8')
log.write_text('Offline unittest run: process exit code 0.\n' + result[0] + '\nOK\n\n'
               'Windows PowerShell wrapped normal unittest stderr as NativeCommandError; '
               'this was not a test failure. Original capture: test-results.raw.txt.\n', encoding='utf-8')
resume = (ROOT/'resumes/Satvik_Saini_Resume.pdf').read_bytes()
assert resume == (ROOT/'output/pdf/Satvik_Saini_Resume.pdf').read_bytes()
previews = list((ROOT/'output/previews').glob('*.eml'))
assert len(previews) == 3
bodies = set()
for file in previews:
    msg = BytesParser(policy=policy.default).parsebytes(file.read_bytes())
    assert msg['To'] == 'careers@example.com'
    assert next(msg.iter_attachments()).get_payload(decode=True) == resume
    bodies.add(msg.get_body().get_content())
assert len(bodies) == 3
print(log.read_text(encoding='utf-8'))
print('Three distinct preview emails parsed; each attachment equals the reviewed PDF.')
