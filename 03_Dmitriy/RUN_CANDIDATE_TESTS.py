from pathlib import Path
import sys,unittest
ROOT=Path(__file__).resolve().parent
for rel in ['source', 'solution', 'reference', 'reference/source', 'reference/f125', 'acceptance']:
 sys.path.insert(0,str(ROOT/rel))
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_candidate_*.py',top_level_dir=str(ROOT/'tests'))
r=unittest.TextTestRunner(verbosity=2).run(suite)
print('CANDIDATE_GATE',r.testsRun,'skipped',len(r.skipped),'expectedFailures',len(r.expectedFailures))
raise SystemExit(0 if r.testsRun>0 and r.wasSuccessful() and not r.skipped and not r.expectedFailures else 1)
