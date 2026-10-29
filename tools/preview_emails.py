"""Write reviewable sample templates without queueing or sending any email."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jobbot

profile = jobbot.load_json('profile.json')
resume = jobbot.profile_ready(profile)
folder = ROOT/'output/previews'
folder.mkdir(parents=True, exist_ok=True)
seen = set()
for i in range(100):
    variant = int(jobbot.digest(str(i))[:8], 16) % 3
    if variant in seen:
        continue
    job = dict(id=str(i), title='Software Engineer', company='Example Employer (preview only)',
               email='careers@example.com', url='https://example.com/jobs/preview-only',
               matching_skills=['JavaScript', 'Node.js', 'Express.js', 'MongoDB'])
    msg = jobbot.email_message(job, profile, resume)
    (folder/f'template-{variant+1}.eml').write_bytes(msg.as_bytes())
    (folder/f'template-{variant+1}.txt').write_text(
        'PREVIEW ONLY - fictitious employer, not a verified opening; no email sent.\n\n'
        + 'Subject: ' + msg['Subject'] + '\n\n' + msg.get_body().get_content(), encoding='utf-8')
    seen.add(variant)
    if len(seen) == 3:
        break
print('Wrote three sample templates under output/previews. No jobs queued; no emails sent.')
