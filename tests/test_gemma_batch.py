import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


BATCH_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "gemma_batch.py"
SPEC = importlib.util.spec_from_file_location("gemma_batch", BATCH_PATH)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


class ManifestTests(unittest.TestCase):
    def test_manifest_uses_project_relative_output_paths(self):
        data = {
            "tasks": [
                {"id": "types", "spec": "01-types.md", "output": "src/types.py"}
            ]
        }
        manifest = batch.parse_manifest(data)
        self.assertEqual(manifest[0].output, pathlib.Path("src/types.py"))

    def test_manifest_rejects_duplicate_ids(self):
        data = {"tasks": [
            {"id": "same", "spec": "a.md", "output": "a.py"},
            {"id": "same", "spec": "b.md", "output": "b.py"},
        ]}
        with self.assertRaisesRegex(ValueError, "duplicate task id"):
            batch.parse_manifest(data)

    def test_manifest_rejects_invalid_path_types_and_empty_paths(self):
        for value in (None, 42, [], {}, "", "."):
            data = {"tasks": [{"id": "x", "spec": value, "output": "x.py"}]}
            with self.assertRaisesRegex(ValueError, "non-empty project-relative"):
                batch.parse_manifest(data)

    def test_manifest_rejects_duplicate_output_paths(self):
        data = {"tasks": [
            {"id": "a", "spec": "a.md", "output": "same.py"},
            {"id": "b", "spec": "b.md", "output": "same.py"},
        ]}
        with self.assertRaisesRegex(ValueError, "duplicate output path"):
            batch.parse_manifest(data)

    def test_manifest_rejects_absolute_or_parent_paths(self):
        for output in ("/tmp/x.py", "../x.py"):
            data = {"tasks": [{"id": "x", "spec": "x.md", "output": output}]}
            with self.assertRaisesRegex(ValueError, "project-relative"):
                batch.parse_manifest(data)


class DependencyTests(unittest.TestCase):
    def test_topological_order_places_dependencies_first(self):
        tasks = batch.parse_manifest({"tasks": [
            {"id": "app", "spec": "app.md", "output": "src/app.py", "depends_on": ["types"]},
            {"id": "types", "spec": "types.md", "output": "src/types.py"},
        ]})
        self.assertEqual([task.id for task in batch.topological_order(tasks)], ["types", "app"])

    def test_unknown_dependency_is_rejected(self):
        tasks = batch.parse_manifest({"tasks": [
            {"id": "app", "spec": "app.md", "output": "app.py", "depends_on": ["missing"]}
        ]})
        with self.assertRaisesRegex(ValueError, "unknown dependency"):
            batch.topological_order(tasks)

    def test_dependency_cycle_is_rejected(self):
        tasks = batch.parse_manifest({"tasks": [
            {"id": "a", "spec": "a.md", "output": "a.py", "depends_on": ["b"]},
            {"id": "b", "spec": "b.md", "output": "b.py", "depends_on": ["a"]},
        ]})
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            batch.topological_order(tasks)


class ExecutionTests(unittest.TestCase):
    def _project(self, manifest):
        temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(temp.name)
        tasks_dir = root / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "manifest.json").write_text(json.dumps(manifest))
        for item in manifest["tasks"]:
            (tasks_dir / item["spec"]).write_text("spec for " + item["id"])
        return temp, root, tasks_dir

    def test_spec_symlink_outside_tasks_directory_is_rejected(self):
        manifest = {"tasks": [
            {"id": "x", "spec": "escape.md", "output": "x.py"}
        ]}
        temp, root, tasks_dir = self._project(manifest)
        self.addCleanup(temp.cleanup)
        outside = root / "outside.md"
        outside.write_text("secret")
        (tasks_dir / "escape.md").unlink()
        (tasks_dir / "escape.md").symlink_to(outside)
        calls = []

        def runner(*args):
            calls.append(args)
            return batch.AttemptResult(0, "")

        results = batch.execute_batch(
            batch.parse_manifest(manifest), tasks_dir, root, runner, retries=0)
        self.assertEqual(calls, [])
        self.assertEqual(results["x"].status, "failed")
        self.assertIn("escapes", results["x"].message)

    def test_output_symlink_parent_outside_project_is_rejected(self):
        manifest = {"tasks": [
            {"id": "x", "spec": "x.md", "output": "escape/pwn.py"}
        ]}
        temp, root, tasks_dir = self._project(manifest)
        self.addCleanup(temp.cleanup)
        outside = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside))
        (root / "escape").symlink_to(outside, target_is_directory=True)
        calls = []

        def runner(*args):
            calls.append(args)
            return batch.AttemptResult(0, "")

        results = batch.execute_batch(
            batch.parse_manifest(manifest), tasks_dir, root, runner, retries=0)
        self.assertEqual(calls, [])
        self.assertEqual(results["x"].status, "failed")
        self.assertFalse((outside / "pwn.py").exists())

    def test_runner_success_requires_output_file(self):
        manifest = {"tasks": [
            {"id": "x", "spec": "x.md", "output": "x.py"}
        ]}
        temp, root, tasks_dir = self._project(manifest)
        self.addCleanup(temp.cleanup)

        def runner(*args):
            return batch.AttemptResult(0, "")

        results = batch.execute_batch(
            batch.parse_manifest(manifest), tasks_dir, root, runner, retries=0)
        self.assertEqual(results["x"].status, "failed")
        self.assertIn("did not create", results["x"].message)

    def test_failed_dependency_blocks_dependent_task(self):
        manifest = {"tasks": [
            {"id": "types", "spec": "types.md", "output": "src/types.py"},
            {"id": "app", "spec": "app.md", "output": "src/app.py", "depends_on": ["types"]},
        ]}
        temp, root, tasks_dir = self._project(manifest)
        self.addCleanup(temp.cleanup)
        calls = []

        def runner(task, spec_path, output, context, feedback):
            calls.append(task.id)
            return batch.AttemptResult(1, "generation failed")

        results = batch.execute_batch(
            batch.parse_manifest(manifest), tasks_dir, root, runner, retries=0)
        self.assertEqual(calls, ["types"])
        self.assertEqual(results["app"].status, "blocked")

    def test_retry_receives_previous_error_as_feedback(self):
        manifest = {"tasks": [
            {"id": "app", "spec": "app.md", "output": "src/app.py"}
        ]}
        temp, root, tasks_dir = self._project(manifest)
        self.addCleanup(temp.cleanup)
        feedbacks = []

        def runner(task, spec_path, output, context, feedback):
            feedbacks.append(feedback)
            if len(feedbacks) == 1:
                return batch.AttemptResult(3, "SyntaxError: broken")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("x = 1\n")
            return batch.AttemptResult(0, "")

        results = batch.execute_batch(
            batch.parse_manifest(manifest), tasks_dir, root, runner, retries=1)
        self.assertEqual(feedbacks, [None, "SyntaxError: broken"])
        self.assertEqual(results["app"].status, "passed")
        self.assertEqual(results["app"].attempts, 2)

    def test_dependency_outputs_are_passed_as_context(self):
        manifest = {"tasks": [
            {"id": "types", "spec": "types.md", "output": "src/types.py"},
            {"id": "app", "spec": "app.md", "output": "src/app.py", "depends_on": ["types"]},
        ]}
        temp, root, tasks_dir = self._project(manifest)
        self.addCleanup(temp.cleanup)
        contexts = {}

        def runner(task, spec_path, output, context, feedback):
            contexts[task.id] = context
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(task.id)
            return batch.AttemptResult(0, "")

        batch.execute_batch(batch.parse_manifest(manifest), tasks_dir, root, runner, retries=0)
        self.assertEqual(contexts["types"], [])
        self.assertEqual(contexts["app"], [root.resolve() / "src/types.py"])


class SubprocessRunnerTests(unittest.TestCase):
    def test_worker_timeout_precedes_subprocess_kill(self):
        runner = batch.make_subprocess_runner(["--stream"], timeout=600)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                batch.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            root = pathlib.Path(directory)
            spec = root / "task.md"
            spec.write_text("task")
            runner(None, spec, root / "out.py", [], None)

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--timeout") + 1], "600")
        self.assertEqual(
            run.call_args.kwargs["timeout"], 600 + batch.WORKER_SHUTDOWN_GRACE)


class CliTests(unittest.TestCase):
    def test_missing_project_root_exits_two_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = root / "manifest.json"
            spec = root / "x.md"
            manifest.write_text(json.dumps({"tasks": [
                {"id": "x", "spec": "x.md", "output": "x.py"}
            ]}))
            spec.write_text("spec")
            result = subprocess.run(
                [sys.executable, str(BATCH_PATH), "--manifest", str(manifest),
                 "--project-root", str(root / "missing")],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
