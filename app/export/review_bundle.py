"""Build a private portable C review pack without credentials or reviewer identities.

The JSON spec contains fact_manifest and stores=[{data_dir, run_ids}]. Only
selected documents, evidence and results are exported, never a whole DB backup.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import quote
import zipfile

from app.common.contracts import schema, validate_contract
from app.config import Settings
from app.export.review_bundle_verify import verify_bundle
from app.storage.db import Store


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def _evidence(row):
    result = {k: row[k] for k in ('evidence_id', 'document_id', 'page', 'text', 'extraction_method', 'confidence')}
    result['bbox'] = json.loads(row['bbox'])
    if row['table_id'] is not None:
        result.update(table_id=row['table_id'], row=row['row'], column=row['column_index'])
    return result


def build_bundle(spec, output):
    output = Path(output)
    if output.exists() or output.with_suffix('.zip').exists():
        raise ValueError('Use a new output directory; existing review work must not be overwritten')
    manifest_path = Path(spec['fact_manifest'])
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    if manifest.get('version') != 1:
        raise ValueError('Unsupported fact manifest')
    fact_keys = set(schema()['$defs']['Fact']['properties'])
    sanitized = {'version': 1, 'documents': []}
    for bundle in manifest['documents']:
        sanitized['documents'].append({'document_id': bundle['document_id'], 'sha256': bundle['sha256'],
            'facts': [{k: copy.deepcopy(v) for k, v in f.items() if k in fact_keys} for f in bundle.get('facts', [])]})
    connections, locations, evidence_sources, selected_runs = [], {}, {}, []
    try:
        for source in spec['stores']:
            root = Path(source['data_dir']).resolve()
            conn = sqlite3.connect((root/'metadata.sqlite3').as_uri()+'?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row
            connections.append(conn)
            for row in conn.execute('SELECT * FROM documents'):
                document = dict(row)
                previous = locations.get(document['document_id'])
                if previous and previous[2]['sha256'] != document['sha256']:
                    raise ValueError('Document ID collision across stores')
                locations.setdefault(document['document_id'], (root, conn, document))
                evidence_sources.setdefault(document['document_id'], []).append(conn)
            for run_id in source.get('run_ids', []):
                row = conn.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
                if row is None or row['status'] not in {'succeeded', 'needs_review'}:
                    raise ValueError('Selected run is missing or has no reviewable result')
                selected_runs.append((root, dict(row)))
        wanted = {b['document_id'] for b in sanitized['documents']}
        for _, row in selected_runs:
            run = json.loads(row['payload'])
            result = json.loads(row['result'])
            validate_contract('Run', run)
            validate_contract('TaskResult', result)
            if (run['run_id'] != row['run_id'] or result['run_id'] != run['run_id']
                    or result['task_type'] != run['task_type'] or result['input_document_ids'] != run['document_ids']):
                raise ValueError('Run metadata does not match its result')
            wanted.update(run['document_ids'])
        if not wanted <= locations.keys():
            raise ValueError('A source document is not available in the selected stores')
        # Verify all sources and references before creating any destination.
        for bundle in sanitized['documents']:
            if locations[bundle['document_id']][2]['sha256'] != bundle['sha256']:
                raise ValueError('Fact manifest document hash mismatch')
            for fact in bundle['facts']:
                validate_contract('Fact', fact)
                for eid in fact['evidence_ids']:
                    matches = [conn.execute('SELECT document_id FROM evidence WHERE evidence_id=?', (eid,)).fetchone()
                               for conn in evidence_sources[bundle['document_id']]]
                    if not any(e is not None and e['document_id'] == bundle['document_id'] for e in matches):
                        raise ValueError('Fact evidence missing or belongs to another document')
        for document_id in wanted:
            root, _, doc = locations[document_id]
            validate_contract('Document', {k: doc[k] for k in schema()['$defs']['Document']['properties']})
            if '\\' in Path(doc['filename']).name or ':' in Path(doc['filename']).name:
                raise ValueError('Unsupported document filename for a portable bundle')
            source = root/'blobs'/doc['sha256'][:2]/doc['sha256']
            if hashlib.sha256(source.read_bytes()).hexdigest() != doc['sha256']:
                raise ValueError('Original document hash mismatch')
        output.mkdir(parents=True, mode=0o700)
        dest = Store(Settings(data_dir=output/'store'))
        documents, evidence_by_id, run_index = [], {}, []
        try:
            for document_id in sorted(wanted):
                root, conn, row = locations[document_id]
                relative = 'documents/'+document_id+'/'+Path(row['filename']).name
                target = output/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root/'blobs'/row['sha256'][:2]/row['sha256'], target)
                dest.insert_document(row)
                public = {k: row[k] for k in schema()['$defs']['Document']['properties']}
                documents.append({**public, 'relative_file': relative})
                entries = {}
                for connection in evidence_sources[document_id]:
                    for raw in connection.execute('SELECT * FROM evidence WHERE document_id=?', (document_id,)):
                        e = _evidence(dict(raw))
                        if e['evidence_id'] in entries and entries[e['evidence_id']] != e:
                            raise ValueError('Evidence ID collision across stores')
                        entries[e['evidence_id']] = e
                dest.insert_evidence(list(entries.values()))
                evidence_by_id.update(entries)
            for _, row in selected_runs:
                result = json.loads(row['result'])
                validate_contract('TaskResult', result)
                if not set(result['evidence_ids']) <= evidence_by_id.keys():
                    raise ValueError('Run evidence missing from exported store')
                dest.insert_run(row)
                for review in result['review_items']:
                    dest.upsert_review({**review, 'run_id': row['run_id'],
                        'evidence_ids': json.dumps(review['evidence_ids']), 'reviewer': None,
                        'note': None, 'corrected_value': None, 'updated_at': None})
                relative = 'results/'+row['run_id']+'.json'
                _write(output/relative, result)
                run_index.append({'run_id': row['run_id'], 'task_type': row['task_type'],
                                  'status': row['status'], 'relative_file': relative,
                                  'document_ids': result['input_document_ids'],
                                  'task_input': json.loads(row['task_input'])})
                # Freeze blank copies only; no labels, names or gold decisions.
                if row['task_type'] in {'industry_impact', 'investment_memo'}:
                    from eval.annotations import template
                    task_input = json.loads(row['task_input'])
                    owner = task_input['company_code' if row['task_type'] == 'investment_memo' else 'focus_company_code']
                    packet = template(result, 'real-source-development-review', owner, 'development', False)
                    for reviewer in ('a', 'b'):
                        _write(output/('annotations/'+row['run_id']+'_'+reviewer+'_blank.json'), packet)
        finally:
            dest.close()
        _write(output/'store/reviewed_facts.json', sanitized)
        _write(output/'documents.json', documents)
        _write(output/'runs.json', run_index)
        referenced = {e for b in sanitized['documents'] for f in b['facts'] for e in f['evidence_ids']}
        referenced.update(e for _, row in selected_runs for e in json.loads(row['result'])['evidence_ids'])
        _write(output/'evidence.json', [evidence_by_id[e] for e in sorted(referenced)])
        coordinates = []
        for bundle in sanitized['documents']:
            for fact in bundle['facts']:
                for eid in fact['evidence_ids']:
                    e = evidence_by_id[eid]
                    box = e['bbox']
                    coordinates.append({'fact': fact, 'evidence': e,
                        'locator_risk': '框选区域偏小，需优先人工核验' if box[2]-box[0] < 30 or box[3]-box[1] < 4 else '',
                        'annotation': {'reviewer': '', 'decision': '', 'note': '', 'checked_at': ''}})
        _write(output/'annotations/coordinate_review_blank.json', coordinates)
        summary = {'version': 1, 'purpose': 'private source/coordinate review, not a full replay bundle',
            'documents': len(documents), 'pdf_documents': sum(d['media_type']=='application/pdf' for d in documents),
            'facts': sum(len(b['facts']) for b in sanitized['documents']), 'runs': len(run_index),
            'source_fact_manifest_sha256': hashlib.sha256(original).hexdigest(),
            'fact_manifest_redaction': 'Fact values, statuses and evidence retained; personal review history omitted',
            'excluded': ['credentials', 'provider configuration', 'raw model responses', 'reviewer identities', 'original events'],
            'human_gold': False, 'split': 'development', 'offline_replay_included': False}
        _write(output/'bundle.json', summary)
        shutil.copyfile(Path(__file__).with_name('review_bundle_verify.py'), output/'verify_bundle.py')
        links = ''.join('<li><a href="'+html.escape(quote(d['relative_file'], safe='/'))+'">'+html.escape(d['filename'])+'</a></li>' for d in documents)
        rows = ''.join('<li><a href="'+r['relative_file']+'">'+html.escape(r['task_type'])+' '+r['run_id']+'</a></li>' for r in run_index)
        by_doc = {d['document_id']: d for d in documents}
        fact_rows = []
        for entry in coordinates:
            fact, evidence = entry['fact'], entry['evidence']
            doc = by_doc[evidence['document_id']]
            href = quote(doc['relative_file'], safe='/')+'#page='+str(evidence['page'])
            values = [fact['company_code'], fact['metric'], fact['period_start']+' ~ '+fact['period_end'],
                      str(fact['value']), fact['review_status'], str(evidence['bbox']), entry['locator_risk']]
            fact_rows.append('<tr><td><a href="'+html.escape(href)+'">'+html.escape(doc['filename'])+' p.'+str(evidence['page'])+'</a></td>'+''.join('<td>'+html.escape(v)+'</td>' for v in values)+'</tr>')
        (output/'资料入口.html').write_text('<!doctype html><meta charset="utf-8"><title>C真实资料入口</title><style>td,th{border:1px solid #999;padding:6px}table{border-collapse:collapse}</style><h1>真实资料审阅包</h1><p>先读 README_C.md。事实确认与坐标核验分开；不含真人金标。</p><h2>原始文件</h2><ul>'+links+'</ul><h2>原始运行结果</h2><ul>'+rows+'</ul><p><a href="evidence.json">证据</a> · <a href="store/reviewed_facts.json">去身份事实清单</a> · <a href="annotations/coordinate_review_blank.json">空白坐标核验表</a></p><h2>事实与原始 PDF 页码</h2><p>原生 PDF 链接不自动画框；bbox 须独立核对。偏小框优先复核。</p><table><tr>'+''.join('<th>'+x+'</th>' for x in ['文件／页码','公司','指标','期间','原值／元','事实状态','bbox','坐标提示'])+'</tr>'+''.join(fact_rows)+'</table>', encoding='utf-8')
        (output/'README_C.md').write_text('''# C 真实资料交接包

这是实际公开财报、公告和既有真实运行结果，不是合成测试资料。`资料入口.html` 可直接打开；文件与资料对应见 documents.json、runs.json。

1. 解压到本机私有目录，运行 `python verify_bundle.py --restore-blobs`。校验通过后按原文件恢复内容寻址目录。无需密钥或联网，不需先安装项目依赖。
2. 原始 PDF/TXT 在 documents/；事实清单在 store/reviewed_facts.json，证据片段、页码和 bbox 在 evidence.json。页码从 1 开始，bbox 为左上原点的 0—1000 整数。原件字节保持 SHA-256 一致。
3. 坐标核验表位于 annotations/coordinate_review_blank.json。尺寸偏小的框已提示优先检查，不能因为金融数值已确认就认定框选合格。核对行名、金额、期间和单位；填写自己的结论，不修改原始文件。
4. 两份语义空白标注模板按 a/b 分开保存，需两名真人独立填写，分歧再裁决。该公司已用于开发，split=development，不能冒称独立公司留出集。

若需页面联调，在项目最新 main 的根目录执行：
`python -m demo.live --data-dir "解压绝对目录/store" --facts "解压绝对目录/store/reviewed_facts.json"`
首次启动前请在同一终端设 FINSCOPE_MODEL_BACKEND=offline-rule；读取已有结果不需要模型服务。依赖安装按项目运行说明。当前网页没有按 Run ID 加载历史结果的控件：历史结果先从 results/ 阅读，或访问 API 的 /runs/运行ID/result；证据为 /evidence/证据ID，原件为 /documents/文档ID/content。

如需用现有页面核验财务证据，连接团队 API，按 runs.json 中对应财报／估值条目的 document_ids 和 task_input 启动新的纯工具任务。这是当前代码上的新 Run，不是旧 Run 的重放。不要为展示历史语义结果重新调用模型；其空白标注模板已可直接使用。

该包省略个人签字历史、原始事件及模型响应快照，因此不宣称可重放原任务；不要用当前代码替旧 Run 生成新的“原始”结果或导出。本包数据库是供页面核验的副本，页面复核决定仅修改副本，不会回写 B 的原始清单。原始财务事实的金额/状态/证据保留，包中没有代签新的确认。

资料包含财务数值和内部引用，仅通过团队的私有渠道传递。它不在 Git 仓库，C 拉取代码不会自动获得此包。ZIP 和该目录需要移交到 C 的电脑；A/B 本机绝对路径不能作为另一台电脑的可访问地址。checksums 证明文件一致性，不替代来源真实性或人工金融审核。
''', encoding='utf-8')
        files = [p for p in output.rglob('*') if p.is_file()]
        _write(output/'checksums.json', {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)})
        verify_bundle(output)
        archive = output.with_suffix('.zip')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for p in sorted(output.rglob('*')):
                if p.is_file():
                    z.write(p, output.name+'/'+p.relative_to(output).as_posix())
        for p in output.rglob('*'):
            p.chmod(0o700 if p.is_dir() else 0o600)
        archive.chmod(0o600)
        if manifest_path.read_bytes() != original:
            raise ValueError('Source manifest changed while exporting')
        return {**summary, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'archive_bytes': archive.stat().st_size}
    finally:
        for conn in connections:
            conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build_bundle(json.loads(args.spec.read_text(encoding='utf-8')), args.output)))


if __name__ == '__main__':
    main()
