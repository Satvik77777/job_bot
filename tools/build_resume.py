"""Build the factual ATS PDF; optional authoring tools are not needed by jobbot.

Run only after the PDF skill's artifact-operation marker. The original is never
replaced by this script. Visually inspect output before copying to resumes/.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tailor_resume import generate

profile = json.loads((ROOT/'profile.json').read_text(encoding='utf-8'))
destination = ROOT/'output/pdf/Satvik_Saini_Resume.pdf'
generate(profile, '', destination)
print(destination)
