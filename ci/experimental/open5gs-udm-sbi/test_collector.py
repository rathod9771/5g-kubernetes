import pathlib,tempfile,unittest
from collect_elf import collect
class CollectorTests(unittest.TestCase):
 def test_symbols_excluded_and_elf_included(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);s=p/'src';d=p/'dst';s.mkdir();d.mkdir()
   (s/'libx.so.2.7.2').write_bytes(b'\x7fELFfixture');(s/'libx.so.2.7.2.symbols').write_text('symbols')
   self.assertEqual(list(collect([s],d)),['libx.so.2.7.2'])
   self.assertFalse((d/'libx.so.2.7.2.symbols').exists())
 def test_bad_selected_elf_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t);(p/'libbad.so.2').write_text('not ELF')
   with self.assertRaises(ValueError):collect([p],p)
 def test_empty_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(ValueError):collect([pathlib.Path(t)],pathlib.Path(t))
if __name__=='__main__':unittest.main(verbosity=2)
