import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from voiceqa.gitflow import export_snapshot


class GitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        self.git('init', '-q')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], env=self.env, text=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_uses_index_even_when_worktree_is_fixed(self):
        p = self.root/'case.yaml'
        p.write_text('invalid staged version')
        self.git('add', 'case.yaml')
        p.write_text('fixed but unstaged version')
        output = Path(self.temp.name)/'snapshot'
        output.mkdir()
        # Isolate Git environment injected by a real hook during this test.
        from unittest.mock import patch
        with patch.dict(os.environ, self.env, clear=True):
            export_snapshot(self.root, output)
        self.assertEqual((output/'case.yaml').read_text(), 'invalid staged version')

    @unittest.skipIf(os.name == 'nt', 'Windows symlink privileges vary; index-mode guard is platform independent')
    def test_symlink_in_snapshot_is_rejected(self):
        (self.root/'link').symlink_to('/etc/passwd')
        self.git('add', 'link')
        output = Path(self.temp.name)/'snapshot'
        output.mkdir()
        from unittest.mock import patch
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(ValueError):
                export_snapshot(self.root, output)
