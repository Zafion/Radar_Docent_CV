import unittest

from app.api.main import enrich_document_provenance,mask_unaccredited_availability


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return [None]


class Connection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        return Result(self.rows)


class DocumentApiProvenanceTests(unittest.TestCase):
    def test_unknown_history_is_not_reported_as_currently_available(self):
        rows=[{'historical_status':'unknown','availability_status':'available'},
              {'historical_status':'accredited','availability_status':'available'},
              {'historical_status':'unknown','availability_status':'closed'}]
        mask_unaccredited_availability(rows)
        self.assertEqual([r['availability_status'] for r in rows],['unknown','available','closed'])
        self.assertEqual(rows[0]['availability_reason'],'vigencia_no_acreditada')

    def test_current_evidence_is_attached_and_stale_evidence_is_downgraded(self):
        rows = [
            {'id': 1, 'sha256': 'a'*64, 'validation_status': 'validated', 'validation_run': '2',
             'latest_success': '2', 'historical_status': 'unknown', 'publication_date_quality': 'unknown'},
            {'id': 2, 'sha256': 'b'*64, 'validation_status': 'validated_with_limitations', 'validation_run': '4',
             'latest_success': '4', 'historical_status': 'unknown', 'publication_date_quality': 'unknown'},
            {'id': 3, 'sha256': 'c'*64, 'validation_status': 'validated', 'validation_run': '5',
             'latest_success': '6', 'historical_status': None, 'publication_date_quality': None},
        ]
        items = [{'document_id': 1}, {'document_id': 2}, {'document_id': 3}]
        enrich_document_provenance(Connection(rows), items)
        self.assertEqual([x['validation_status'] for x in items], ['validated', 'validated_with_limitations', 'needs_validation'])
        self.assertTrue(all(x['historical_status'] == 'unknown' for x in items))
        self.assertTrue(all(x['publication_date_quality'] == 'unknown' for x in items))
        self.assertEqual(items[2]['source_document_sha256'], 'c'*64)


if __name__ == '__main__':
    unittest.main()
