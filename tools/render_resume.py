"""Render the resume for visual review, using free PDFium when Poppler is absent."""
from pathlib import Path
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
destination = ROOT/'output/pdf/Satvik_Saini_Resume.pdf'
out = ROOT/'tmp/pdfs'
out.mkdir(parents=True, exist_ok=True)
with pdfium.PdfDocument(destination) as document:
    for i, page in enumerate(document):
        bitmap = page.render(scale=1.8)
        bitmap.to_pil().save(out/f'resume-page-{i+1}.png')
        bitmap.close()
        page.close()
    print(f'Rendered {len(document)} pages to {out}')
