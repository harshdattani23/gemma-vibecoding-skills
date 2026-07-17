import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock


WORKER_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "gemma_worker.py"
SPEC = importlib.util.spec_from_file_location("gemma_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class ExtractionTests(unittest.TestCase):
    def test_strip_thinking_supports_think_and_thinking_tags(self):
        text = "<think>hidden</think><thinking>also hidden</thinking>```python\nx = 1\n```"
        self.assertEqual(worker.strip_thinking(text), "```python\nx = 1\n```")

    def test_extract_code_prefers_matching_language(self):
        text = "```javascript\nconsole.log(1)\n```\n```python\nx = 1\n```"
        self.assertEqual(worker.extract_code(text, "python"), "x = 1\n")

    def test_extract_code_accepts_common_language_alias(self):
        text = "```py\nx = 1\n```"
        self.assertEqual(worker.extract_code(text, "python"), "x = 1\n")

    def test_extract_code_rejects_explicitly_mismatched_language(self):
        text = "```javascript\nconsole.log(1)\n```"
        self.assertIsNone(worker.extract_code(text, "python"))

    def test_extract_code_accepts_crlf_fence(self):
        text = "```python\r\nx = 1\r\n```"
        self.assertEqual(worker.extract_code(text, "python"), "x = 1\r\n")

    def test_extract_code_accepts_untagged_fallback(self):
        text = "```\nx = 1\n```"
        self.assertEqual(worker.extract_code(text, "python"), "x = 1\n")


class ValidationTests(unittest.TestCase):
    def test_python_validation_accepts_valid_source(self):
        result = worker.validate_code("x = 1\n", "python", "example.py")
        self.assertEqual(result.status, "passed")

    def test_python_validation_rejects_invalid_source(self):
        result = worker.validate_code("def broken(:\n", "python", "example.py")
        self.assertEqual(result.status, "failed")
        self.assertIn("invalid syntax", result.message)

    def test_unknown_language_is_reported_as_skipped(self):
        result = worker.validate_code("anything", "madeup", "example.unknown")
        self.assertEqual(result.status, "skipped")

    def test_invalid_expect_pattern_is_reported_before_write(self):
        with self.assertRaisesRegex(ValueError, "invalid --expect regex"):
            worker.compile_expect_pattern("[")

    def test_javascript_validation_preserves_module_suffix(self):
        for suffix in (".js", ".mjs", ".cjs"):
            with self.subTest(suffix=suffix), mock.patch.object(
                    worker, "_validate_with_file",
                    return_value=worker.ValidationResult("passed")) as validator:
                worker.validate_code("export default 1\n", "javascript", "module" + suffix)
                self.assertEqual(validator.call_args.args[1], suffix)


class StreamingProtocolTests(unittest.TestCase):
    def test_ollama_ndjson_parser_reads_each_json_line(self):
        lines = [
            b'{"message":{"content":"```python\\n"},"done":false}\n',
            b'{"message":{"content":"x = 1\\n```"},"done":false}\n',
            b'{"done":true,"eval_count":5,"eval_duration":1000000000}\n',
        ]
        events = list(worker.iter_ollama_ndjson(lines))
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["message"]["content"], "```python\n")
        self.assertTrue(events[-1]["done"])

    def test_openai_sse_parser_reads_data_and_stops_at_done(self):
        lines = [
            b': keepalive\n',
            b'data: {"choices":[{"delta":{"content":"x"}}]}\n',
            b'\n',
            b'data: [DONE]\n',
            b'data: {"ignored":true}\n',
        ]
        events = list(worker.iter_openai_sse(lines))
        self.assertEqual(events, [{"choices": [{"delta": {"content": "x"}}]}])

    def test_openai_sse_parser_supports_multiline_data_events(self):
        lines = [
            b'event: message\n',
            b'data: {"choices":[\n',
            b'data: {"delta":{"content":"x"}}]}\n',
            b'\n',
        ]
        events = list(worker.iter_openai_sse(lines))
        self.assertEqual(events, [{"choices": [{"delta": {"content": "x"}}]}])

    def test_provider_errors_are_backend_errors(self):
        with self.assertRaisesRegex(worker.BackendError, "model not found"):
            worker.ollama_event_content({"error": "model not found"})
        with self.assertRaisesRegex(worker.BackendError, "provider failed"):
            worker.openai_event_content({"error": {"message": "provider failed"}})

    def test_empty_openai_choices_are_ignored(self):
        self.assertEqual(worker.openai_event_content({"choices": []}), "")

    def test_interrupt_preserves_only_chunks_seen_before_interrupt(self):
        def events():
            yield {"message": {"content": "before"}}
            raise KeyboardInterrupt()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(worker.StreamInterrupted) as caught:
                worker.collect_stream(events(), worker.ollama_event_content)
        self.assertEqual(caught.exception.partial, "before")

    def test_ollama_stream_reports_final_stats(self):
        final_event = {
            "done": True,
            "eval_count": 5,
            "eval_duration": 1_000_000_000,
            "total_duration": 2_000_000_000,
        }
        with mock.patch.object(
                worker, "_stream_request", return_value=("code", final_event)), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = worker.call_ollama_stream(
                "http://localhost", "gemma", "prompt", {}, 30)
        self.assertEqual(result, ("code", final_event))
        self.assertIn("5 tokens, 5.0 tok/s, 2.0s total", stderr.getvalue())

    def test_interrupted_response_is_saved_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "module.py"
            output.write_text("ORIGINAL\n")
            partial = worker.save_partial_response("```python\nx =", output)
            self.assertEqual(output.read_text(), "ORIGINAL\n")
            self.assertEqual(partial.read_text(), "```python\nx =")
            self.assertEqual(partial.name, "module.py.partial")


class AtomicWriteTests(unittest.TestCase):
    def test_invalid_code_does_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "module.py"
            output.write_text("ORIGINAL\n")
            result = worker.validate_and_write(
                "def broken(:\n", output, "python", None, make_backup=True
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(output.read_text(), "ORIGINAL\n")
            self.assertFalse(output.with_suffix(".py.bak").exists())

    def test_valid_code_replaces_output_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "module.py"
            output.write_text("ORIGINAL\n")
            result = worker.validate_and_write(
                "x = 1\n", output, "python", None, make_backup=True
            )
            self.assertEqual(result.status, "passed")
            self.assertEqual(output.read_text(), "x = 1\n")
            self.assertEqual(output.with_suffix(".py.bak").read_text(), "ORIGINAL\n")

    def test_valid_code_preserves_existing_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "tool.py"
            output.write_text("ORIGINAL\n")
            output.chmod(0o755)
            result = worker.validate_and_write(
                "print('ok')\n", output, "python", None, make_backup=True
            )
            self.assertEqual(result.status, "passed")
            self.assertEqual(output.stat().st_mode & 0o777, 0o755)

    def test_backup_symlink_is_rejected_without_touching_victim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / "module.py"
            victim = root / "victim"
            backup = output.with_suffix(".py.bak")
            output.write_text("ORIGINAL\n")
            victim.write_text("SECRET\n")
            backup.symlink_to(victim)
            with self.assertRaisesRegex(ValueError, "backup path must not be a symlink"):
                worker.validate_and_write(
                    "x = 1\n", output, "python", None, make_backup=True
                )
            self.assertEqual(output.read_text(), "ORIGINAL\n")
            self.assertEqual(victim.read_text(), "SECRET\n")

    def test_output_symlink_is_rejected_without_touching_victim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            victim = root / "victim.py"
            output = root / "module.py"
            victim.write_text("SECRET\n")
            output.symlink_to(victim)
            with self.assertRaisesRegex(ValueError, "output path must not be a symlink"):
                worker.validate_and_write(
                    "x = 1\n", output, "python", None, make_backup=True
                )
            self.assertEqual(victim.read_text(), "SECRET\n")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO support required")
    def test_non_regular_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "module.py"
            os.mkfifo(output)
            with self.assertRaisesRegex(ValueError, "output path must be a regular file"):
                worker.validate_and_write(
                    "x = 1\n", output, "python", None, make_backup=True
                )

    def test_expect_failure_does_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "module.py"
            output.write_text("ORIGINAL\n")
            pattern = worker.compile_expect_pattern(r"class TokenParser")
            result = worker.validate_and_write(
                "x = 1\n", output, "python", pattern, make_backup=True
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(output.read_text(), "ORIGINAL\n")


class CliErrorTests(unittest.TestCase):
    def run_main_with_response(self, response, output_error=None):
        stderr = io.StringIO()
        argv = ["gemma_worker.py", "--task", "task.md", "--out", "module.py"]
        patches = [
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(worker, "load_config", return_value={
                "model": "gemma", "base_url": "http://localhost", "api": "ollama"}),
            mock.patch.object(worker, "build_prompt", return_value="prompt"),
            mock.patch.object(worker, "call_ollama", return_value=response),
        ]
        if output_error is not None:
            patches.append(mock.patch.object(
                worker, "validate_and_write", side_effect=output_error))
        with patches[0], patches[1], patches[2], patches[3], redirect_stderr(stderr):
            if output_error is None:
                return worker.main(), stderr.getvalue()
            with patches[4]:
                return worker.main(), stderr.getvalue()

    def test_output_value_error_exits_two_without_traceback(self):
        exit_code, stderr = self.run_main_with_response(
            "```python\nx = 1\n```", ValueError("unsafe output"))
        self.assertEqual(exit_code, 2)
        self.assertIn("output failed: unsafe output", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_extraction_failure_includes_truncated_raw_response(self):
        response = "model explanation " + "x" * 2100
        exit_code, stderr = self.run_main_with_response(response)
        self.assertEqual(exit_code, 1)
        self.assertIn("raw response:\nmodel explanation", stderr)
        self.assertIn("[truncated]", stderr)
        self.assertNotIn("x" * 2100, stderr)

    def test_malformed_config_exits_two_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / "config.json"
            config.write_text("{bad")
            env = os.environ.copy()
            env["GEMMA_CODER_CONFIG"] = str(config)
            result = subprocess.run(
                [sys.executable, str(WORKER_PATH), "--task", "unused.md",
                 "--out", "unused.py"],
                env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid configuration", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
