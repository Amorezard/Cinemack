import sys
import unittest

from connectors import cmd_coverage, cmd_list, HERE

cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
if cmd == "list":
    cmd_list()
elif cmd == "coverage":
    cmd_coverage()
elif cmd == "test":
    suite = unittest.defaultTestLoader.discover(f"{HERE}/tests", pattern="test_connectors.py", top_level_dir=HERE)
    sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
else:
    print(__import__("connectors").__doc__)
