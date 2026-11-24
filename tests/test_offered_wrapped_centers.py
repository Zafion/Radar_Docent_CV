import unittest
from app.services.offered_positions_parser import OfferedPositionsParserService
class WrappedCentersTests(unittest.TestCase):
 def test_split_before_center_code(self):
  s=OfferedPositionsParserService();lines=['23 LOCALIDAD -','12000856 - CENTRO','NO 512148 Sust. Ind.'];self.assertEqual(s._join_wrapped_center_lines(lines),['23 LOCALIDAD - 12000856 - CENTRO','NO 512148 Sust. Ind.'])
 def test_split_after_center_code(self):
  s=OfferedPositionsParserService();lines=['23LOCALIDAD - 12005970 -','CENTRO','NO 777210 Sust. Ind.'];self.assertEqual(s._join_wrapped_center_lines(lines),['23LOCALIDAD - 12005970 - CENTRO','NO 777210 Sust. Ind.'])
 def test_does_not_join_independent_rows(self):
  s=OfferedPositionsParserService();lines=['23 LOCALIDAD - 12005970 - CENTRO -','OTRA - 12000856 - OTRO'];self.assertEqual(s._join_wrapped_center_lines(lines),lines)
 def test_does_not_join_header(self):
  s=OfferedPositionsParserService();lines=['LOCALIDAD - 12005970 -','PROVÍNCIA/PROVINCIA: CASTELLÓN'];self.assertEqual(s._join_wrapped_center_lines(lines),lines)
if __name__=='__main__':unittest.main()
