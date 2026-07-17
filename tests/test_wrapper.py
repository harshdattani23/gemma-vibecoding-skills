import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
WRAPPER = ROOT / "gemma-coder.sh"
INSTALLER = ROOT / "install.sh"


class WrapperTests(unittest.TestCase):
    def test_wrapper_help_succeeds(self):
        result = subprocess.run([str(WRAPPER), "help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("worker", result.stdout)
        self.assertIn("batch", result.stdout)

    def test_wrapper_resolves_direct_file_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            link = pathlib.Path(directory) / "gemma-coder"
            link.symlink_to(WRAPPER)
            result = subprocess.run([str(link), "worker", "--help"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--task", result.stdout)

    def test_wrapper_rejects_symlink_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            link = pathlib.Path(directory) / "gemma-coder"
            link.symlink_to(link.name)
            result = subprocess.run(
                ["sh", "-c", '. "$1"', str(link), str(WRAPPER)],
                capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, 2)
            self.assertIn("symlink resolution exceeded 40 hops", result.stderr)

    def test_batch_subcommand_reaches_batch_help(self):
        result = subprocess.run([str(WRAPPER), "batch", "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--manifest", result.stdout)


class InstallerTests(unittest.TestCase):
    def test_copy_mode_replaces_stale_command_with_canonical_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            (home / ".claude").mkdir()
            command = home / ".local/bin/gemma-coder"
            command.parent.mkdir(parents=True)
            command.symlink_to("/definitely/stale")
            env = os.environ.copy()
            env["HOME"] = str(home)
            result = subprocess.run([str(INSTALLER), "--copy"], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(command.is_symlink())
            self.assertIn(".local/share/gemma-coder", str(command.resolve()))
            help_result = subprocess.run([str(command), "help"], env=env,
                                         capture_output=True, text=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)

    def test_unknown_installer_option_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["HOME"] = directory
            result = subprocess.run([str(INSTALLER), "--bogus"], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Usage:", result.stderr)

    def test_installer_creates_path_command_in_fake_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            (home / ".claude").mkdir()
            env = os.environ.copy()
            env["HOME"] = str(home)
            result = subprocess.run([str(INSTALLER)], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            command = home / ".local/bin/gemma-coder"
            self.assertTrue(command.is_symlink())
            help_result = subprocess.run([str(command), "help"], env=env,
                                         capture_output=True, text=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)


if __name__ == "__main__":
    unittest.main()
