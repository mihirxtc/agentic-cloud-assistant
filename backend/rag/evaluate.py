"""Repeatable isolated retrieval evaluation; never opens the operator's knowledge DB.

Default hashed-token fixtures test retrieval mechanics, not embedding/model quality.
Use --embedding-model /local/model/path for the real locally cached encoder.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile


class _Array(list):
    def tolist(self):
        return list(self)


class FixtureEncoder:
    def encode(self, texts, **_):
        vectors = []
        for text in texts:
            vector = [0.] * 256
            for token in re.findall(r'\w+', text.lower()):
                vector[int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], 'big') % len(vector)] += 1
            vectors.append(vector)
        return _Array(vectors)


def evaluate(cases, retrieve):
    outcomes, hits, recalls, ranks, negatives, filter_violations = [], [], [], [], [], 0
    for case in cases:
        result = retrieve(case['query'], n_results=3, resource_filter=case.get('resource_filter'))
        sources = result['sources']
        expected = set(case['expected_sources'])
        for chunk in result['raw_chunks']:
            if case.get('resource_filter') and chunk['metadata'].get('resource_type') != case['resource_filter']:
                filter_violations += 1
        if expected:
            hits.append(bool(sources and sources[0] in expected))
            recalls.append(len(set(sources) & expected) / len(expected))
            ranks.append(next((1 / (i + 1) for i, source in enumerate(sources) if source in expected), 0))
        else:
            negatives.append(not sources)
        outcomes.append({'query': case['query'], 'filter': case.get('resource_filter'),
                         'expected_sources': sorted(expected), 'retrieved_sources': sources})
    mean = lambda values: sum(values) / len(values) if values else None
    return {'queries': len(cases), 'hit_at_1': mean(hits), 'source_recall_at_3': mean(recalls),
            'mean_reciprocal_rank': mean(ranks), 'negative_query_accuracy': mean(negatives),
            'filter_violations': filter_violations, 'cases': outcomes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).with_name('evaluation_cases.json'))
    encoder_group = parser.add_mutually_exclusive_group()
    encoder_group.add_argument('--embedding-model', type=Path, help='Existing local SentenceTransformer directory; no downloads')
    encoder_group.add_argument('--onnx-cache', type=Path, help='Opt-in MiniLM ONNX evaluation; downloads the checksum-verified model here if absent')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.onnx_cache:
        import numpy as np
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        embedding = ONNXMiniLM_L6_V2(preferred_providers=['CPUExecutionProvider'])
        embedding.DOWNLOAD_PATH = args.onnx_cache.resolve()
        class OnnxEncoder:
            def encode(self, texts, **_):
                return np.asarray(embedding(texts))
        encoder, label = OnnxEncoder(), 'all-MiniLM-L6-v2_ONNX_CPU'
    elif args.embedding_model:
        if not args.embedding_model.is_dir():
            parser.error('Provide an existing local model directory')
        import os
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        from sentence_transformers import SentenceTransformer
        encoder = SentenceTransformer(str(args.embedding_model.resolve()))
        label = str(args.embedding_model.resolve())
    else:
        encoder, label = FixtureEncoder(), 'deterministic_token_fixture_only'
    from rag.knowledge_base import SecurityKnowledgeBase
    from rag.rag_service import query_knowledge_base
    fixture = json.loads(args.cases.read_text())
    with tempfile.TemporaryDirectory(prefix='aca-retrieval-eval-') as directory:
        kb = SecurityKnowledgeBase(directory, encoder=encoder)
        for document in fixture['documents']:
            kb.add_document(document['doc_id'], document['text'], {'resource_type': document['resource_type']})
        result = evaluate(fixture['queries'], lambda *a, **k: query_knowledge_base(*a, **k, knowledge=kb))
    result.update(encoder=label, dataset=str(args.cases),
                  limitation='Fixture scores validate this small corpus; they do not establish production retrieval quality or answer grounding')
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
