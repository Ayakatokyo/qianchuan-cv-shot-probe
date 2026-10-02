import sys, unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
suite=unittest.defaultTestLoader.discover(str(root/"tests"))
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
