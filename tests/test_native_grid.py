"""Closed native grids preserve multiline cells without borrowing adjacent text."""
from dataclasses import replace

from app.common.contracts import validate_contract
from app.config import Settings
from app.container import build_app
from app.parse.grid import cells_from_geometry
from app.parse.index import DocumentIndex
from app.parse.pdfpos import CharBox
from app.tasks.structured_extraction.model_extract import _field, grid_sources


def geometry():
    # Two columns and three rows: a header, then two separate transactions.
    rules = [(x, 20, x, 200) for x in (10, 110, 210)]
    rules += [(10, y, 210, y) for y in (200, 140, 80, 20)]
    chars = []
    for x, y, text in [(15, 175, '日期'), (115, 175, '质权人'),
                       (15, 120, '2026年9'), (15, 105, '月21日'),
                       (115, 120, '甲证券'), (115, 105, '有限公司'),
                       (15, 55, '2024年11月7日'), (115, 55, '乙证券')]:
        chars.extend(CharBox(c, x + i * 5, y, x + i * 5 + 4, y + 8) for i, c in enumerate(text))
    return chars, rules


def test_multiline_cells_have_independent_text_stable_ids_and_contract_boxes():
    chars, rules = geometry()
    cells = cells_from_geometry('doc_synthetic', 1, 240, 240, chars, rules)
    assert cells == cells_from_geometry('doc_synthetic', 1, 240, 240, chars[::-1], rules[::-1])
    assert [c['text'] for c in cells] == ['日期', '质权人', '2026年9月21日', '甲证券有限公司', '2024年11月7日', '乙证券']
    for cell in cells:
        validate_contract('Evidence', cell)
        assert cell['page'] == 1 and cell['bbox'][0] < cell['bbox'][2] and cell['bbox'][1] < cell['bbox'][3]
    evidence = {c['evidence_id']: c for c in cells}
    date_cell = cells[2]
    candidate = {'raw_value': date_cell['text'], 'evidence_ids': [date_cell['evidence_id']]}
    field = _field('pledge_start_date', candidate, 'date', evidence)
    assert field['value'] == '2026-09-21' and field['status'] == 'needs_review'
    candidate['raw_value'] = '甲证券有限公司'
    assert _field('pledgee', candidate, 'text', evidence)['value'] is None


def test_missing_outer_border_or_only_one_row_does_not_invent_cells():
    chars, rules = geometry()
    assert not cells_from_geometry('doc_synthetic', 1, 240, 240, chars, [r for r in rules if r[1] != 200])
    assert not cells_from_geometry('doc_synthetic', 1, 240, 240, chars, [r for r in rules if r[1] not in (80, 140)])


def test_superscript_footnote_never_prefixes_the_transaction_amount():
    chars, rules = geometry()
    chars = [CharBox(c, 15+i*8, 100, 22+i*8, 109, 9) for i, c in enumerate('200万股')]
    chars += [CharBox('注', 55, 105.2, 59, 109.7, 4.5), CharBox('3', 60, 105.2, 64, 109.7, 4.5)]
    cells = cells_from_geometry('doc_synthetic', 1, 240, 240, chars, rules)
    assert cells[0]['text'] == '200万股注3'
    evidence = {cells[0]['evidence_id']: cells[0]}
    field = {'raw_value': '3200万股', 'evidence_ids': list(evidence)}
    assert _field('pledged_shares', field, 'share', evidence)['value'] is None
    field['raw_value'] = '200万股'
    assert _field('pledged_shares', field, 'share', evidence)['value'] == '2000000'


def test_cell_captions_do_not_leak_positions_or_turn_into_quote_evidence():
    chars, rules = geometry()
    cells = cells_from_geometry('doc_synthetic', 1, 240, 240, chars, rules)
    selected, captions, boxes = grid_sources(DocumentIndex.from_plain_text('doc_synthetic', '证券代码：999999'), cells)
    assert len(selected) == 4 and len(boxes) == 1
    assert captions[0]['text'] == '日期：2026年9月21日'
    assert all(set(c) == {'text', 'evidence_ids'} for c in captions)
    forged = {'raw_value': captions[0]['text'], 'evidence_ids': captions[0]['evidence_ids']}
    assert _field('pledge_start_date', forged, 'date', {c['evidence_id']: c for c in selected})['value'] is None


def test_cumulative_section_and_cell_budget_reject_entire_tables():
    chars, rules = geometry()
    cells = cells_from_geometry('doc_synthetic', 1, 240, 240, chars, rules)
    index = DocumentIndex.from_plain_text('doc_synthetic', '2、股东股份累计被质押的情况')
    assert grid_sources(index, cells)[0] == []
    index = DocumentIndex.from_plain_text('doc_synthetic', '证券代码：999999')
    oversized = [dict(cells[2], row=i+2) for i in range(65)]
    assert grid_sources(index, cells[:2] + oversized)[0] == []


def test_grid_is_opt_in_and_input_hash_distinguishes_model_grid(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert not settings.extraction_grid
    bundles = []
    for backend, grid in [('rules', False), ('rules', True), ('model', False), ('model', True)]:
        app = build_app(replace(settings, extraction_backend=backend, extraction_grid=grid))
        try:
            bundles.append(app.orchestrator._input_bundle([], 'structured_extraction', {'announcement_types': ['pledge']}))
        finally:
            app.close()
    assert bundles[0] == bundles[1]
    assert 'extraction_grid' not in bundles[2]
    assert bundles[3]['extraction_grid'] is True and bundles[3] != bundles[2]
