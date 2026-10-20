import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from retrieval.index import chunks, load_corpus, source_allowed
from retrieval.hybrid import rrf, search
from retrieval.topics import tokenize

class RetrievalTests(unittest.TestCase):
    def test_chunks_preserve_every_sentence_without_overlap(self):
        body='#标题\n##范围\n'+('最短租期不得被随意改写。'*120)
        result=list(chunks(body,'文档标题'))
        self.assertTrue(all(len(text)<=500 for _,text in result))
        self.assertEqual(''.join(text.split('\n',2)[2] for _,text in result),'最短租期不得被随意改写。'*120)

    def test_real_corpus_has_substantial_reviewed_markdown(self):
        c=load_corpus();self.assertEqual(len(c['documents']),8)
        self.assertGreaterEqual(len(c['chunks']),40)
        for doc in c['documents']:
            self.assertTrue(all(source_allowed(s,doc['scope']) for s in doc['sources']))
        with self.assertRaisesRegex(ValueError,'expired'):load_corpus(today='2028-01-01')
        self.assertEqual(c['generation'],load_corpus()['generation'])

    def test_source_allowlist_rejects_phishing_and_credentials(self):
        self.assertFalse(source_allowed({'url':'https://www.hdb.gov.sg.evil.test/x'},'policy_guidance'))
        self.assertFalse(source_allowed({'url':'https://user@www.hdb.gov.sg/x'},'policy_guidance'))
        self.assertFalse(source_allowed({'url':'project://knowledge/rental/x'},'policy_guidance'))

    def test_rrf_uses_ranks_and_deduplicates(self):
        result,scores=rrf([['a','a','b'],['b','c']]);self.assertEqual(result[0],'b');self.assertAlmostEqual(scores['a'],1/61)
        self.assertAlmostEqual(scores['b'],1/62+1/61)

    def test_synonyms_are_lexical_features_not_intent_routing(self):
        self.assertIn('押金',tokenize('Security deposit refund'))
        self.assertIn('印花税',tokenize('Stamp duty deadline'))

    def test_stale_or_wrong_vector_space_is_never_silently_searched(self):
        from retrieval.hybrid import snapshot
        with self.assertRaisesRegex(ValueError,'MODEL_MISMATCH'):snapshot(json.dumps({'embedding_model':'fake'}))

    def test_vector_outage_is_explicit_and_bm25_remains_usable(self):
        class Broken:
            metadata={'generation':load_corpus()['generation']}
            def query(self,**kwargs):raise OSError('offline')
        with patch('retrieval.hybrid.client') as c:
            c.return_value.get_collection.return_value=Broken()
            r=search(['印花税税率'])
        self.assertIn('vector_unavailable',r['degraded']);self.assertFalse(r['vector_enabled']);self.assertTrue(r['bm25_enabled']);self.assertTrue(r['chunks'])

    def test_chroma_connection_failure_before_query_still_allows_bm25(self):
        with patch('retrieval.hybrid.client',side_effect=OSError('connection refused')):
            r=search(['印花税税率'])
        self.assertFalse(r['vector_enabled']);self.assertTrue(r['bm25_enabled']);self.assertTrue(r['chunks'])

if __name__=='__main__':unittest.main()
