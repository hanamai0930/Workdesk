import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from researchos.workdesk.professional_tools import computer


class ComputerWorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.counter = 0

    def run_action(self, action, *, version=None, repository=None, **parameters):
        self.counter += 1
        return computer.execute({
            "project_root": str(self.project),
            "output_dir": str(self.base / str(self.counter)),
            "input_versions": [version] if version else [],
            "repository_versions": repository,
            "parameters": {"action": action, **parameters},
        })

    @staticmethod
    def version(path):
        data = path.read_bytes()
        return {"path":path.name, "bytes":len(data), "sha256":hashlib.sha256(data).hexdigest()}

    @unittest.skipUnless(shutil.which("git"), "Git unavailable")
    def test_file_diff_and_commit_reject_repository_drift(self):
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.project, stderr=subprocess.STDOUT)
        git("init", "-b", "main")
        git("config", "user.email", "tests@example.invalid")
        git("config", "user.name", "Local Test")
        path = self.project / "note.txt"
        self.run_action("file.edit", path=path.name, content="old\n")
        first = self.version(path)
        git("add", "note.txt")
        git("commit", "-m", "fixture")
        edited = self.run_action("file.edit", path=path.name, content="new\n", version=first)
        self.assertIn("+new", Path(edited["artifacts"][1]["path"]).read_text(encoding="utf8"))
        with self.assertRaisesRegex(ValueError, "版本"):
            self.run_action("file.edit", path=path.name, content="stale", version=first)
        raw = self.run_action("git.diff", path=path.name, version=self.version(path))
        self.assertIn("-old", Path(raw["artifacts"][1]["path"]).read_text(encoding="utf8"))
        meta = self.project / ".git"
        expected = {name:self.version(meta / name)["sha256"] for name in ("HEAD", "refs/heads/main", "index")}
        self.run_action("git.stage", path=path.name, version=self.version(path), repository=expected)
        with self.assertRaisesRegex(ValueError, "暂存区"):
            self.run_action("git.commit", path=path.name, message="stale", version=self.version(path), repository=expected)
        expected["index"] = self.version(meta / "index")["sha256"]
        self.run_action("git.commit", path=path.name, message="accepted", version=self.version(path), repository=expected)
        self.assertIn(b"accepted", git("log", "-1", "--oneline"))

    def test_project_terminal_stream_and_input(self):
        script = self.project / "local.py"
        script.write_text("import sys\nprint('ready', flush=True)\nprint(input(), file=sys.stderr)\n", encoding="utf8")
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(self.run_action, "terminal.run", version=self.version(script), path=script.name)
            out = self.base / "1"
            transcript = out / "console.txt"
            deadline = time.monotonic() + 8
            while not (transcript.exists() and "ready" in transcript.read_text(encoding="utf8")):
                if work.done():
                    work.result()
                if time.monotonic() > deadline:
                    self.fail("terminal produced no live output")
                time.sleep(.05)
            (out / ".input" / "0001-command.txt").write_bytes("receipt\n".encode())
            result = work.result(timeout=8)
        self.assertEqual(result["state"], "COMPLETED")
        self.assertIn("receipt", transcript.read_text(encoding="utf8"))
        self.assertEqual(result["artifacts"][2]["sha256"], self.version(transcript)["sha256"])


if __name__ == "__main__":
    unittest.main()
