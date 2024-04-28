import contextlib
import pathlib
import shutil
import tempfile
import unittest
import zipfile
from typing import Generator


@contextlib.contextmanager
def temp_path() -> Generator[pathlib.Path, None, None]:
    d = tempfile.mkdtemp()
    try:
        yield pathlib.Path(d)
    finally:
        shutil.rmtree(d)


class TestNormalize(unittest.TestCase):
    def _callFUT(self, *args, **kwargs):
        from micropi import normalize

        return normalize(*args, **kwargs)

    def test_it(self):
        name = ""
        normalized = ""
        for name, normalized in [
            ("", ""),
            ("a", "a"),
            ("a.b", "a-b"),
            ("a.b.c", "a-b-c"),
            ("AB_C", "ab-c"),
        ]:
            with self.subTest(name):
                result = self._callFUT(name)
                self.assertEqual(result, normalized)


class Test_load_metadata(unittest.TestCase):
    def _callFUT(self, *args, **kwargs):
        from micropi import load_metadata

        return load_metadata(*args, **kwargs)

    def test_it(self):
        with temp_path() as t:
            wh = t / "dist-1.0-none-any.whl"
            with zipfile.ZipFile(wh, "w") as zf:
                with zf.open(f"dist-1.0.dist-info/METADATA", "w") as m:
                    m.write(b"test metadata file")
            result = self._callFUT(wh)
            self.assertEqual(result, b"test metadata file")
