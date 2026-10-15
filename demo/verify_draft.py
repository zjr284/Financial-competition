"""在独立副本核查自编草稿；不修改来源库、不生成真人金标。"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess

from app.config import Settings
from app.container import build_app
from app.models.adapter import OfflineRuleModel
from app.tasks.research_verification.runtime import install_role_c
from app.tasks.research_verification.support import digest, validate
from eval.annotations import template


def write(path, payload):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def verify(source_store, draft, company, source_ids, output, *, dataset, synthetic=False):
    source_store, draft, output = (Path(p).resolve() for p in (source_store, draft, output))
    if not re.fullmatch(r'[0-9]{6}', company) or not dataset.strip():
        raise ValueError('须填写六位公司代码和数据集名称')
    if not source_ids or len(source_ids) != len(set(source_ids)):
        raise ValueError('来源文档不能为空或重复')
    if output.exists() or output.is_relative_to(source_store):
        raise ValueError('使用来源库之外的全新输出目录，保留已有工作')
    facts = source_store / 'reviewed_facts.json'
    database = source_store / 'metadata.sqlite3'
    if not database.is_file() or not facts.is_file() or not draft.is_file():
        raise ValueError('缺少来源数据库、事实清单或草稿')
    blob_root = source_store / 'blobs'
    if blob_root.is_symlink() or any(p.is_symlink() for p in blob_root.rglob('*')):
        raise ValueError('来源快照不可包含符号链接')
    manifest = json.loads(facts.read_text(encoding='utf-8'))
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True,
                                     cwd=Path(__file__).resolve().parents[1]).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True,
                                        cwd=Path(__file__).resolve().parents[1]).strip())
    output.mkdir(parents=True)
    runtime = output / 'runtime'
    runtime.mkdir()
    # SQLite backup 包含 WAL 中已提交内容；不把源库当作可写 App 打开。
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(runtime / 'metadata.sqlite3')) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('数据库副本完整性检查失败')
    shutil.copytree(blob_root, runtime / 'blobs')
    write(runtime / 'reviewed_facts.json', manifest)
    settings = Settings(data_dir=runtime, code_version=revision,
                        model_id='rule-based-offline', model_revision='v1',
                        extraction_backend='rules', extraction_grid=False)
    # 显式注入规则适配器，不受用户环境的远端模型设置影响。
    app = build_app(settings, model=OfflineRuleModel(settings))
    try:
        install_role_c(app)
        for doc_id in source_ids:
            content, document = app.get_document_content(doc_id)
            if hashlib.sha256(content).hexdigest() != document['sha256']:
                raise ValueError('来源快照哈希不匹配')
        report = app.upload_path(str(draft))
        if report['document_id'] in source_ids:
            raise ValueError('草稿不能同时充当核查来源')
        inventory = []
        for kind, task_input in [
            ('research_verification', {'draft_document_id': report['document_id'], 'company_code': company}),
            ('report_quality', {'report_document_ids': [report['document_id']]}),
        ]:
            request = {'api_version': '1.0.0', 'task_type': kind,
                       'document_ids': [*source_ids, report['document_id']], 'task_input': task_input}
            run = app.run_task(request)
            validate('Run', run)
            write(output / f'{kind}-run.json', run)
            if run['status'] not in {'succeeded', 'needs_review'}:
                raise ValueError(f'{kind} 未生成可审阅结果；检查私有运行记录')
            result = app.get_result(run['run_id'])
            validate('TaskResult', result)
            write(output / f'{kind}-result.json', result)
            replay = app.orchestrator.replay(run['run_id'])
            write(output / f'{kind}-replay.json', replay)
            replay_ok = all(replay[key] for key in (
                'input_hash_match', 'result_hash_match', 'trace_match', 'result_original_matches'))
            if not replay_ok:
                raise ValueError(f'{kind} 离线重放不一致；保留私有记录供排查')
            app.export_run(run['run_id'], formats=['json', 'csv', 'html'],
                           out_dir=str(output / f'{kind}-exports'))
            inventory.append({'task_type': kind, 'run_id': run['run_id'],
                              'status': run['status'], 'result_hash': digest(result),
                              'replay_verified': replay_ok})
            if kind == 'research_verification':
                blank = template(result, dataset, company, 'development', synthetic)
                for person in ('a', 'b'):
                    write(output / f'annotation-{person}-blank.json', blank)
        summary = {'dataset_id': dataset, 'synthetic': synthetic, 'split': 'development',
                   'draft_kind': 'self_authored_not_original_disclosure',
                   'human_gold_complete': False, 'holdout_metrics': None,
                   'code_version': revision, 'code_dirty': dirty, 'model_calls': 0,
                   'draft_sha256': report['sha256'], 'runs': inventory}
        write(output / 'summary.json', summary)
        return summary
    finally:
        app.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-store', 'draft', 'company', 'output', 'dataset'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--source', action='append', required=True, help='可重复提供来源 document_id')
    parser.add_argument('--synthetic', action='store_true', help='仅合成资料使用，真实来源不加')
    args = parser.parse_args()
    summary = verify(args.source_store, args.draft, args.company, args.source, args.output,
                     dataset=args.dataset, synthetic=args.synthetic)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
