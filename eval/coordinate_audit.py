"""Read-only technical audit of B's private source/coordinate handoff.

No fact approval, human annotation, model call, or historical replay is performed.
PDF geometry uses optional PyMuPDF; all reports stay in a caller-selected private directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from app.tasks.research_verification.support import validate


def inside(root, relative):
    if not isinstance(relative, str):
        raise ValueError("Bundle path must be a string")
    name = PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts or '\\' in relative or ':' in relative:
        raise ValueError("Unsafe bundle path")
    path = root.joinpath(*name.parts)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("Bundle path escapes its directory")
    return path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def numeric_coverage(words, box, value):
    """Exact whole-word Decimal matching, never a substring in a larger number.

    This is only a geometry hint: no inference about column, period, unit or truth.
    """
    if value is None:
        return None
    target = Decimal(value)
    matched = []
    for x0, y0, x1, y1, text in words:
        text = text.replace(',', '').replace('−', '-')
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)', text):
            continue
        try:
            if Decimal(text) != target:
                continue
        except InvalidOperation:
            continue
        area = max(0, x1-x0) * max(0, y1-y0)
        overlap = max(0, min(x1, box[2])-max(x0, box[0])) * max(0, min(y1, box[3])-max(y0, box[1]))
        matched.append(overlap / area if area else 0)
    return max(matched, default=0)


def audit(bundle, output):
    import fitz
    from PIL import Image, ImageDraw

    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    checks = read(bundle / 'checksums.json')
    if output == bundle or output.is_relative_to(bundle):
        raise ValueError("Write reports outside the original bundle")
    if output.exists():
        raise ValueError("Output already exists; preserve previous review records")
    for relative, expected in checks.items():
        if checksum(inside(bundle, relative)) != expected:
            raise ValueError("Bundle checksum mismatch")
    documents = read(bundle / 'documents.json')
    evidence = read(bundle / 'evidence.json')
    docmap, evmap = {}, {}
    for document in documents:
        validate('Document', {key: value for key, value in document.items() if key != 'relative_file'})
        if document['document_id'] in docmap:
            raise ValueError("Duplicate document")
        if checksum(inside(bundle, document['relative_file'])) != document['sha256']:
            raise ValueError("Original document hash mismatch")
        docmap[document['document_id']] = document
    for item in evidence:
        validate('Evidence', item)
        if item['evidence_id'] in evmap or item['document_id'] not in docmap:
            raise ValueError("Duplicate or out-of-bundle evidence")
        if not (item['bbox'][0] < item['bbox'][2] and item['bbox'][1] < item['bbox'][3]):
            raise ValueError("Invalid coordinate extent")
        evmap[item['evidence_id']] = item
    facts = {}
    for source in read(bundle / 'store/reviewed_facts.json')['documents']:
        if docmap[source['document_id']]['sha256'] != source['sha256']:
            raise ValueError("Fact manifest document hash mismatch")
        for fact in source['facts']:
            validate('Fact', fact)
            if fact['fact_id'] in facts:
                raise ValueError("Duplicate fact")
            if any(evmap[eid]['document_id'] != source['document_id'] for eid in fact['evidence_ids']):
                raise ValueError("Fact evidence belongs to another document")
            facts[fact['fact_id']] = fact
    results = []
    for entry in read(bundle / 'runs.json'):
        result = read(inside(bundle, entry['relative_file']))
        validate('TaskResult', result)
        if any(result[key] != entry[key] for key in ('run_id', 'task_type', 'status')):
            raise ValueError("Run inventory and result disagree")
        if any(evmap[eid]['document_id'] not in result['input_document_ids'] for eid in result['evidence_ids']):
            raise ValueError("Result evidence is outside its fixed documents")
        results.append(result)
    coordinate_rows = read(bundle / 'annotations/coordinate_review_blank.json')
    reviews = []
    pages = {}
    for index, row in enumerate(coordinate_rows, 1):
        fact, ev = row['fact'], row['evidence']
        if fact != facts[fact['fact_id']] or ev != evmap[ev['evidence_id']] or ev['evidence_id'] not in fact['evidence_ids']:
            raise ValueError("Coordinate worksheet differs from fixed facts/evidence")
        doc = docmap[ev['document_id']]
        item = {'index': index, 'fact_id': fact['fact_id'], 'evidence_id': ev['evidence_id'],
                'document_id': doc['document_id'], 'company_code': fact['company_code'],
                'metric': fact['metric'], 'fact_status': fact['review_status'], 'page': ev['page'],
                'original_bbox': ev['bbox'], 'ai_geometry_status': 'non_pdf_not_scored',
                'human_annotation': None}
        if doc['media_type'] == 'application/pdf':
            with fitz.open(inside(bundle, doc['relative_file'])) as pdf:
                if len(pdf) != doc['page_count'] or not 1 <= ev['page'] <= len(pdf):
                    raise ValueError("PDF page count or evidence page mismatch")
                page = pdf[ev['page']-1]
                width, height = page.rect.width, page.rect.height
                box = [ev['bbox'][i] * (width if i % 2 == 0 else height) / 1000 for i in range(4)]
                words = []
                for word in page.get_text('words'):
                    rect = fitz.Rect(word[:4]) * page.rotation_matrix
                    words.append([*rect, word[4]])
                coverage = numeric_coverage(words, box, fact['value'])
                # A conservative screening threshold, not an accuracy metric.
                item.update(numeric_word_coverage=coverage, ai_geometry_status=(
                    'needs_visual_review' if coverage is None or coverage < .6 else 'numeric_span_present_needs_context_review'))
                pages.setdefault((doc['document_id'], ev['page']), []).append(item)
        reviews.append(item)
    output.mkdir(parents=True)
    page_dir = output / 'pages'
    page_dir.mkdir()
    for index, ((did, pageno), items) in enumerate(pages.items(), 1):
        doc = docmap[did]
        with fitz.open(inside(bundle, doc['relative_file'])) as pdf:
            page = pdf[pageno-1]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
            draw = ImageDraw.Draw(image)
            for item in items:
                box = fitz.Rect([item['original_bbox'][i] * (page.rect.width if i % 2 == 0 else page.rect.height) / 1000 for i in range(4)])
                # Draw into the raster only; the original PDF is never saved.
                pixel_box = [coordinate*2 for coordinate in box]
                draw.rectangle(pixel_box, outline='red', width=2)
                draw.text((pixel_box[0], max(0, pixel_box[1]-14)), str(item['index']), fill='red')
                item['page_image'] = f'pages/page-{index:02d}.png'
            image.save(page_dir / f'page-{index:02d}.png')
            del image
    summary = {'checksummed_files': len(checks), 'documents': len(docmap), 'evidence': len(evmap),
               'facts': len(facts), 'results': len(results), 'coordinate_rows': len(reviews),
               'pdf_coordinate_rows': sum('numeric_word_coverage' in row for row in reviews),
               'geometry_attention': sum(row['ai_geometry_status'] == 'needs_visual_review' for row in reviews),
               'fact_statuses': dict(Counter(fact['review_status'] for fact in facts.values())),
               'human_gold': False, 'offline_replay_verified': False, 'split': read(bundle/'bundle.json').get('split'),
               'residual_model_alias_occurrences': sum(len(re.findall(r'ev_model_\d+', json.dumps(result))) for result in results),
               'notice': 'AI technical screening only; numerical presence is not a financial or human sign-off.'}
    report = {'summary': summary, 'source_checksums_sha256': checksum(bundle/'checksums.json'), 'coordinates': reviews}
    (output/'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if any(checksum(inside(bundle, relative)) != expected for relative, expected in checks.items()):
        raise ValueError("Source changed during audit")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.bundle, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
