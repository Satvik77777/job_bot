"""Initialize local private configuration without overwriting existing data."""
import json
from pathlib import Path
import secrets
from shutil import copyfile

root = Path(__file__).resolve().parent
for name in ['profile', 'sources']:
    destination = root / f'{name}.json'
    if not destination.exists():
        copyfile(root / f'{name}.example.json', destination)
env = root / '.env'
if not env.exists():
    copyfile(root / '.env.example', env)
content = env.read_text(encoding='utf-8')
if '\nJOBBOT_API_TOKEN=\n' in content:
    env.write_text(content.replace('\nJOBBOT_API_TOKEN=\n', '\nJOBBOT_API_TOKEN=' + secrets.token_urlsafe(32) + '\n'), encoding='utf-8')
for folder in ['data', 'output', 'resumes']:
    (root / folder).mkdir(exist_ok=True)
roles = ['software engineer', 'software developer', 'backend developer', 'full stack developer', 'SWE OR SDE']
cities = ['Noida', 'Delhi', 'Gurugram OR Gurgaon', 'Hyderabad', 'Bengaluru OR Bangalore']
queries = [f'({role}) ({city}) (fresher OR junior OR "0-2 years" OR "1 year") ("send resume" OR "send your resume" OR "email your resume" OR "apply by email")'
           for role in roles for city in cities]
(root / 'alerts_queries.txt').write_text('\n'.join(queries) + '\n', encoding='utf-8')
print('Initialized missing files without replacing existing settings. Check python jobbot.py health before enabling sending.')
