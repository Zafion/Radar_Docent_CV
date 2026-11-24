from datetime import date, datetime, timezone
import unittest

from app.services.selective_inventory import screen_candidate


class InventoryTests(unittest.TestCase):
    def test_candidates_still_require_provenance_and_history_is_held(self):
        kw=dict(canonical_url='https://example.invalid/doc',sha256='a'*64,
                observed_utc=datetime(2026,11,18,tzinfo=timezone.utc),
                official_date=date(2026,11,18),cutover_utc=datetime(2026,11,17,tzinfo=timezone.utc),inventory=set())
        self.assertEqual(screen_candidate(**kw),'candidate_requires_official_provenance')
        self.assertEqual(screen_candidate(**dict(kw,inventory={(kw['canonical_url'],kw['sha256'])})),'known_version')
        self.assertEqual(screen_candidate(**dict(kw,inventory={(kw['canonical_url'],'b'*64)})),'identity_or_version_change_requires_review')
        self.assertEqual(screen_candidate(**dict(kw,official_date=date(2026,11,17))),'publication_date_not_proven_after_cutover')
        self.assertEqual(screen_candidate(**dict(kw,observed_utc=datetime(2026,11,16,tzinfo=timezone.utc))),'observation_not_after_cutover')
        self.assertEqual(screen_candidate(**dict(kw,official_date=None)),'invalid_or_incomplete_evidence')


if __name__ == '__main__':
    unittest.main()
