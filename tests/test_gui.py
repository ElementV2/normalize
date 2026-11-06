"""Test de l'interface graphique et du glisser-déposer.

Ignoré si Tkinter ou un écran ne sont pas disponibles. Sous Linux sans écran :
    xvfb-run python -m unittest tests.test_gui
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import tkinter as tk
    from normalize_video import gui
except ImportError:  # pragma: no cover
    gui = None

HAS_DISPLAY = os.name == "nt" or bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(gui is not None and HAS_DISPLAY, "Tkinter et un écran requis")
class DropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        sub = self.tmp / "mon dossier"
        sub.mkdir()
        for p in (self.tmp / "a.mp4", self.tmp / "b.MKV", sub / "c d é.mov",
                  self.tmp / "notes.txt"):
            p.write_bytes(b"")
        try:
            self.app = gui.App()
        except tk.TclError as exc:  # pragma: no cover
            self.skipTest(f"pas d'écran : {exc}")
        self.app.withdraw()

    def tearDown(self):
        self.app.destroy()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drop(self, *paths):
        data = self.app.tk.call("list", *map(str, paths))
        return self.app._on_drop(SimpleNamespace(data=data, action="copy"))

    def test_drop_files_and_folder(self):
        self.drop(self.tmp / "a.mp4", self.tmp / "b.MKV", self.tmp / "mon dossier",
                  self.tmp / "notes.txt")
        self.assertEqual([f.name for f in self.app.files], ["a.mp4", "b.MKV", "c d é.mov"])
        self.assertIn("1 fichier(s) ignoré(s)", self.app.status.get())
        self.assertEqual(len(self.app.tree.get_children()), 3)

    def test_drop_twice_does_not_duplicate(self):
        self.drop(self.tmp / "a.mp4")
        self.drop(self.tmp / "a.mp4", self.tmp)
        self.assertEqual(len(self.app.files), 3)

    def test_dnd_enabled_when_tkinterdnd2_installed(self):
        if gui.TkinterDnD is None:
            self.skipTest("tkinterdnd2 non installé")
        self.assertTrue(self.app.dnd)


if __name__ == "__main__":
    unittest.main()
