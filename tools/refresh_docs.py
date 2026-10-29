"""Check key README sections without overwriting maintained documentation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / 'README.md'
REQUIRED_SECTIONS = (
    '## Ubuntu Quick Start',
    '## Windows Scheduling',
    '## Checks and operations',
)

content = README.read_text(encoding='utf-8')
missing = [section for section in REQUIRED_SECTIONS if section not in content]
if missing:
    raise SystemExit('README is missing required sections: ' + ', '.join(missing))

print('README sections are present; maintained documentation was not overwritten.')