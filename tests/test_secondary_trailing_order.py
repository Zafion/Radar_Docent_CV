import unittest
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
class SecondaryTrailingOrderTests(unittest.TestCase):
 def test_non_award_with_trailing_order(self):
  s=FinalAwardListingSecundariaParserService();lines=['EXEMPLO, PERSONA 12','No adjudicat'];self.assertTrue(s._is_entry_start(lines,0));r=s._parse_block(block_lines=lines,body_name=None,specialty_code=None,specialty_name=None);self.assertEqual((r.order_number,r.person_display_name,r.status),(12,'EXEMPLO, PERSONA','No adjudicat'))
 def test_petition_cannot_become_new_person(self):
  s=FinalAwardListingSecundariaParserService();self.assertFalse(s._is_entry_start(['EXEMPLO, PERSONA Petición: 12','Adjudicat'],0));self.assertFalse(s._is_entry_start(['EXEMPLO, PERSONA 12','Adjudicat'],0))
if __name__=='__main__':unittest.main()
