"""Optional dependency-based local document extractors. No cloud document upload."""
from pathlib import Path

def extract_text(path: Path, *, ocr=False) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in ('.txt', '.md', '.csv', '.json'):
        return path.read_text(encoding='utf-8', errors='replace'), 'text'
    if suffix == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError('Install package extra: pip install -e ".[documents]"') from e
        reader = PdfReader(str(path))
        text = '\n'.join(f'## Page {i+1}\n{page.extract_text() or ""}' for i, page in enumerate(reader.pages))
        if text.strip() and any(ch.isalpha() for ch in text):
            return text, 'pdf-text'
        if ocr:
            # Pdf rasterization is deliberately not silently assumed or faked.
            return '', 'ocr-pending: scanned PDF requires a PDF-to-image renderer'
        return '', 'scanned-pdf-detected'
    if suffix == '.docx':
        try:
            from docx import Document
        except ImportError as e:
            raise RuntimeError('Install package extra: pip install -e ".[documents]"') from e
        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            paragraphs += [' | '.join(cell.text for cell in row.cells) for row in table.rows]
        return '\n'.join(paragraphs), 'docx-text'
    if suffix == '.pptx':
        try:
            from pptx import Presentation
        except ImportError as e:
            raise RuntimeError('Install package extra: pip install -e ".[documents]"') from e
        pres = Presentation(str(path))
        return '\n'.join(f'## Slide {idx}\n' + '\n'.join(s.text for s in slide.shapes if s.has_text_frame)
                         for idx, slide in enumerate(pres.slides, 1)), 'pptx-text'
    if suffix == '.xlsx':
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise RuntimeError('Install package extra: pip install -e ".[documents]"') from e
        book = load_workbook(filename=str(path), read_only=True, data_only=True)
        lines = []
        try:
            for sheet in book:
                lines.append(f'## Sheet {sheet.title}')
                for row in sheet.iter_rows(values_only=True):
                    lines.append(' | '.join(str(v) if v is not None else '' for v in row))
        finally:
            book.close()
        return '\n'.join(lines), 'xlsx-text'
    if suffix in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
        if not ocr:
            return '', 'ocr-pending'
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(path)), 'ocr-tesseract'
        except (ImportError, FileNotFoundError) as e:
            return '', 'ocr-unavailable: install pytesseract/Pillow and Tesseract executable'
    return '', 'unsupported-format'
