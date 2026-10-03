"""Regression for the default native runtime with no optional heavy CV installed."""
import unittest,sys,importlib.util
from pathlib import Path
root=Path(__file__).resolve().parents[1]
if '--require-minimal' in sys.argv:
    assert all(importlib.util.find_spec(name) is None for name in ('numpy','cv2','scenedetect')), 'Optional heavy CV must be absent in minimal validation'
    print('Minimal environment: numpy/cv2/scenedetect absent',flush=True)
all_tests=unittest.defaultTestLoader.discover(str(root/'tests'))
def cases(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from cases(item)
        else:yield item
native=unittest.TestSuite(t for t in cases(all_tests) if not t.id().endswith('test_adaptive_backend_remains_explicit_and_does_not_reuse_native'))
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(native).wasSuccessful())
