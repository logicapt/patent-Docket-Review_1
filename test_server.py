"""Run reproducible offline endpoint and evidence tests without a live server."""
from pathlib import Path
import unittest


def test():
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    test()
