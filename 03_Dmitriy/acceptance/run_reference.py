import sys,unittest
from common import ROOT
suite=unittest.defaultTestLoader.discover(str(ROOT/"reference/tests"),pattern="test_*.py")
r=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if r.testsRun>0 and r.wasSuccessful() and not r.skipped and not r.expectedFailures else 1)
