"""Exercise real RAG source with deterministic embeddings and a vector-store double."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import math

import pytest


class Vector(list):
    def tolist(self):
        return list(self)


class Encoder:
    vocabulary = ('s3', 'mfa', 'ssh', 'terraform', 'vpc', 'irrelevant')

    def encode(self, texts, **kwargs):
        return Vector([[text.lower().split().count(word) for word in self.vocabulary] for text in texts])


class Collection:
    def __init__(self):
        self.rows = {}
        self.last_query = None

    def upsert(self, ids, documents, metadatas, embeddings):
        for i, identifier in enumerate(ids):
            self.rows[identifier] = {'documents': documents[i], 'metadatas': deepcopy(metadatas[i]), 'embeddings': list(embeddings[i])}

    def get(self, where=None, include=None):
        selected = [(k, v) for k, v in self.rows.items() if not where or all(v['metadatas'].get(key) in value['$in'] if isinstance(value, dict) else v['metadatas'].get(key) == value for key, value in where.items())]
        return {'ids': [k for k, _ in selected], **{key: [deepcopy(v[key]) for _, v in selected] for key in (include if include is not None else ['documents', 'metadatas'])}}

    def delete(self, ids=None, where=None):
        for identifier in ids if ids is not None else self.get(where)['ids']:
            self.rows.pop(identifier, None)

    def count(self):
        return len(self.rows)

    def query(self, query_embeddings, n_results, include, where=None):
        self.last_query = {'where': where, 'n_results': n_results}
        selected = self.get(where, include=['documents', 'metadatas', 'embeddings'])
        def distance(vector):
            query = query_embeddings[0]
            norm = math.sqrt(sum(x*x for x in query) * sum(x*x for x in vector))
            return 1 - sum(x*y for x, y in zip(query, vector)) / norm if norm else 1
        ranked = sorted(range(len(selected['ids'])), key=lambda i: (distance(selected['embeddings'][i]), selected['ids'][i]))[:n_results]
        return {'documents': [[selected['documents'][i] for i in ranked]],
                'metadatas': [[selected['metadatas'][i] for i in ranked]],
                'distances': [[distance(selected['embeddings'][i]) for i in ranked]]}


@pytest.fixture
def kb(tmp_path):
    # Load the source under its own name: HTTP fixtures intentionally stub rag modules.
    spec = importlib.util.spec_from_file_location('tested_knowledge', Path(__file__).parents[1] / 'rag/knowledge_base.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SecurityKnowledgeBase(tmp_path / 'chroma', collection=Collection(), encoder=Encoder())


def test_filter_is_applied_before_nearest_neighbors(kb):
    kb.add_document('distractor', 's3 s3 s3', {'resource_type': 'ec2'})
    kb.add_document('bucket', 's3 mfa', {'resource_type': 's3'})
    assert kb.search('s3', n_results=1)[0]['metadata']['doc_id'] == 'distractor'
    result = kb.search('s3', n_results=1, resource_filter='s3')
    assert result[0]['metadata']['doc_id'] == 'bucket'
    assert 's3' in kb.collection.last_query['where']['resource_type']['$in']
    assert kb.collection.last_query['n_results'] == 1
    assert kb.search('s3', resource_filter='nonexistent') == []


def test_shorter_replacement_has_no_old_tail_or_old_metadata(kb):
    kb.add_document('guide', ' '.join(['s3'] * 900), {'resource_type': 's3'})
    assert kb.get_document_count() == 3
    kb.add_document('guide', 'mfa', {'resource_type': 'iam'})
    assert kb.get_document_count() == 1
    assert kb.search('s3', resource_filter='s3') == []
    assert kb.list_documents() == [{'doc_id': 'guide', 'source': 'unknown', 'resource_type': 'iam', 'chunk_count': 1}]


def test_empty_replacement_and_embedding_failure_leave_original(kb, monkeypatch):
    kb.add_document('guide', 's3', {'resource_type': 's3'})
    snapshot = deepcopy(kb.collection.rows)
    with pytest.raises(ValueError, match='empty'):
        kb.add_document('guide', ' ')
    monkeypatch.setattr(kb.encoder, 'encode', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('encoder failed')))
    with pytest.raises(RuntimeError):
        kb.add_document('guide', 'mfa')
    assert kb.collection.rows == snapshot


def test_failure_after_upsert_rolls_back_all_previous_chunks(kb, monkeypatch):
    kb.add_document('guide', ' '.join(['s3'] * 900), {'resource_type': 's3'})
    snapshot = deepcopy(kb.collection.rows)
    delete = kb.collection.delete
    failed = False
    def fail_once(*args, **kwargs):
        nonlocal failed
        if kwargs.get('ids') and not failed:
            failed = True
            raise RuntimeError('stale removal failed')
        return delete(*args, **kwargs)
    monkeypatch.setattr(kb.collection, 'delete', fail_once)
    with pytest.raises(RuntimeError):
        kb.add_document('guide', 'mfa', {'resource_type': 'iam'})
    assert kb.collection.rows == snapshot
    assert not kb._journal.exists()


def test_crash_journal_is_recovered_before_any_read(kb):
    kb.add_document('guide', 's3', {'resource_type': 's3'})
    old = deepcopy(kb.collection.rows)
    kb._write_journal({'doc_id': 'guide', 'snapshot': kb._snapshot('guide')})
    kb.collection.upsert(ids=['guide__chunk_0'], documents=['mfa'], embeddings=[[0, 1, 0, 0, 0, 0]], metadatas=[{'doc_id': 'guide', 'resource_type': 'iam'}])
    restarted = type(kb)(kb.storage_path, kb.collection, kb.encoder)
    assert restarted.collection.rows == old
    assert not restarted._journal.exists()


def test_recovery_failure_blocks_reads_and_keeps_journal(kb, monkeypatch):
    kb.add_document('guide', 's3')
    kb._write_journal({'doc_id': 'guide', 'snapshot': kb._snapshot('guide')})
    monkeypatch.setattr(kb.collection, 'delete', lambda **_: (_ for _ in ()).throw(RuntimeError('store unavailable')))
    with pytest.raises(RuntimeError):
        kb.search('s3')
    assert kb._journal.exists()


def test_delete_uses_exact_metadata_identity_not_id_prefix(kb):
    kb.add_document('a', 's3')
    kb.add_document('a__chunk_other', 'mfa')
    assert kb.delete_document('a')['deleted_chunks'] == 1
    assert kb.list_documents()[0]['doc_id'] == 'a__chunk_other'


def test_readers_cannot_see_half_replaced_document(kb, monkeypatch):
    kb.add_document('guide', ' '.join(['s3'] * 900))
    upserted, release = Event(), Event()
    upsert = kb.collection.upsert
    def held_upsert(**kwargs):
        upsert(**kwargs)
        upserted.set()
        assert release.wait(5)
    monkeypatch.setattr(kb.collection, 'upsert', held_upsert)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(kb.add_document, 'guide', 'mfa')
        assert upserted.wait(5)
        reader = pool.submit(kb.get_document_count)
        assert not reader.done()
        release.set()
        writer.result(timeout=5)
        assert reader.result(timeout=5) == 1


@pytest.mark.parametrize('limit', [0, -1, 21, True, '3'])
def test_invalid_limits_are_rejected(kb, limit):
    with pytest.raises(ValueError, match='n_results'):
        kb.search('s3', limit)


def test_rag_service_passes_filter_and_has_stable_source_order(kb, monkeypatch):
    import sys
    module = SimpleNamespace(knowledge_base=kb)
    monkeypatch.setitem(sys.modules, 'rag.knowledge_base', module)
    spec = importlib.util.spec_from_file_location('tested_rag_service', Path(__file__).parents[1] / 'rag/rag_service.py')
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    kb.add_document('wrong', 's3', {'resource_type': 'ec2'})
    kb.add_document('right', 's3 mfa', {'resource_type': 's3'})
    result = service.query_knowledge_base('s3', n_results=1, resource_filter='s3')
    assert result['sources'] == ['right'] and result['chunks_used'] == 1
    assert 'right' in result['augmented_prompt']
    assert service.query_knowledge_base('terraform', resource_filter='s3')['chunks_used'] == 0


def test_real_chroma_filter_replacement_and_restart_recovery(kb, tmp_path):
    pytest.importorskip('chromadb', reason='Optional pinned-vector-store acceptance check')
    actual = type(kb)(tmp_path / 'real-chroma', encoder=Encoder())
    actual.add_document('distractor', 's3 s3', {'resource_type': 'ec2'})
    actual.add_document('guide', ' '.join(['s3'] * 900), {'resource_type': 's3'})
    assert actual.search('s3', n_results=1, resource_filter='s3')[0]['metadata']['doc_id'] == 'guide'
    actual.add_document('guide', 'mfa', {'resource_type': 'iam'})
    assert actual.search('s3', resource_filter='s3') == []
    assert actual.get_document_count() == 2
    old = actual._snapshot('guide')
    actual._write_journal({'doc_id': 'guide', 'snapshot': old})
    actual.collection.delete(where={'doc_id': 'guide'})
    restarted = type(kb)(actual.storage_path, encoder=Encoder())
    assert restarted.search('mfa', resource_filter='iam')[0]['text'] == 'mfa'
    assert restarted.delete_document('guide')['deleted_chunks'] == 1
    assert restarted.get_document_count() == 1


def test_evaluation_measures_rank_negatives_and_filter_violations():
    from rag.evaluate import evaluate
    cases = [{'query': 'known', 'resource_filter': 's3', 'expected_sources': ['right']},
             {'query': 'unknown', 'expected_sources': []}]
    def retrieve(query, **kwargs):
        return {'sources': ['wrong', 'right'] if query == 'known' else [],
                'raw_chunks': [{'metadata': {'resource_type': 's3'}}] if query == 'known' else []}
    report = evaluate(cases, retrieve)
    assert report['hit_at_1'] == 0 and report['source_recall_at_3'] == 1
    assert report['mean_reciprocal_rank'] == .5 and report['negative_query_accuracy'] == 1
    assert report['filter_violations'] == 0


def test_real_chroma_cross_process_visibility(kb, tmp_path):
    pytest.importorskip('chromadb', reason='Optional pinned-vector-store acceptance check')
    import os
    import subprocess
    import sys
    actual = type(kb)(tmp_path / 'shared-chroma', encoder=Encoder())
    actual.add_document('guide', 's3', {'resource_type': 's3'})
    code = '''import sys
from rag.knowledge_base import SecurityKnowledgeBase
from test_knowledge import Encoder
kb = SecurityKnowledgeBase(sys.argv[1], encoder=Encoder())
kb.add_document('guide', 'mfa', {'resource_type': 'iam'})
'''
    test_dir = Path(__file__).resolve().parent
    child = subprocess.run([sys.executable, '-c', code, str(actual.storage_path)],
                           env={**os.environ, 'PYTHONPATH': str(test_dir) + os.pathsep + str(test_dir.parent)},
                           capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr
    assert actual.search('s3', resource_filter='s3') == []
    assert actual.search('mfa', resource_filter='iam')[0]['text'] == 'mfa'


def test_ui_resource_labels_match_canonical_and_legacy_documents(kb):
    kb.add_document('new', 's3', {'resource_type': 'S3'})
    assert kb.collection.get(where={'doc_id': 'new'}, include=['metadatas'])['metadatas'][0]['resource_type'] == 's3'
    # Simulate an existing document created by the original uppercase UI.
    kb.collection.rows['new__chunk_0']['metadatas']['resource_type'] = 'S3'
    assert kb.search('s3', resource_filter='S3')[0]['metadata']['resource_type'] == 's3'
    assert kb.search('s3', resource_filter='s3')[0]['metadata']['doc_id'] == 'new'


def test_curated_retrieval_fixture_regression(kb):
    from rag.evaluate import FixtureEncoder, evaluate
    spec = importlib.util.spec_from_file_location('evaluated_rag_service', Path(__file__).parents[1] / 'rag/rag_service.py')
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    fixture = json.loads((Path(__file__).parents[1] / 'rag/evaluation_cases.json').read_text())
    kb._encoder = FixtureEncoder()
    for doc in fixture['documents']:
        kb.add_document(doc['doc_id'], doc['text'], {'resource_type': doc['resource_type']})
    result = evaluate(fixture['queries'], lambda *a, **k: service.query_knowledge_base(*a, **k, knowledge=kb))
    assert result['hit_at_1'] == result['source_recall_at_3'] == result['mean_reciprocal_rank'] == 1
    assert result['negative_query_accuracy'] == 1 and result['filter_violations'] == 0
