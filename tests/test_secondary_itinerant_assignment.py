import unittest
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
class SecondaryItinerantAssignmentTests(unittest.TestCase):
 def test_both_known_itinerant_suffixes_are_assignment_starts(self):
  s=FinalAwardListingSecundariaParserService()
  for kind in ('SUBSTITUCIÓ DETERMINADA Itinerante','SUBSTITUCIÓ INDETERMINADA Itinerante'):
   with self.subTest(kind=kind):self.assertTrue(s._is_entry_start(['No adjudicat','23',kind],1))
 def test_unknown_suffix_is_not_accepted(self):
  s=FinalAwardListingSecundariaParserService();self.assertFalse(s._is_entry_start(['No adjudicat','23','SUBSTITUCIÓ DETERMINADA OTHER'],1))
if __name__=='__main__':unittest.main()
