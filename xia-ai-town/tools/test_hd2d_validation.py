"""The verifier must reject a failed Godot run even if the engine exits zero."""
from pathlib import Path
import tempfile
import unittest

from hd2d_validation import CONTRACT_END, ERROR_LOG, claim_output, probe_errors, run_errors
from hd2d_verify import PROJECT, patched


class FailurePolicy(unittest.TestCase):
    def test_complete_clean_pass(self):
        self.assertEqual(run_errors(0, CONTRACT_END + ' all checked', CONTRACT_END), [])

    def test_nonzero_and_timeout_fail(self):
        for code in [1, 124, 137, None]:
            with self.subTest(code=code):
                self.assertTrue(run_errors(code, CONTRACT_END, CONTRACT_END))

    def test_intermediate_marker_is_not_completion(self):
        self.assertTrue(run_errors(0, 'HD2D_CONTRACT_PASS: early phase', CONTRACT_END))

    def test_errors_fail_even_after_success_marker(self):
        for line in ['SCRIPT ERROR: Assertion failed', 'SHADER ERROR: invalid token',
                     'Parse Error: unknown type', 'Shader compilation failed',
                     'ERROR: missing resource', 'HD2D_FAIL: wrong depth',
                     'HD2D_ROUTE_FAIL 1', 'CAMERA_FAIL: pose', 'SMOKE FAIL: input',
                     'HD2D_UI_CHECKS: 1624, failures=1',
                     'Traceback (most recent call last):']:
            with self.subTest(line=line):
                self.assertTrue(run_errors(0, CONTRACT_END + '\n' + line, CONTRACT_END))

    def test_normal_warning_and_zero_count_are_not_errors(self):
        self.assertIsNone(ERROR_LOG.search('WARNING: optional debug info; failures=0'))

    def test_probe_requires_true_and_nonempty_assertions(self):
        for value in [None, {}, {'passed': False}, {'passed': True},
                      {'passed': True, 'results': []},
                      {'passed': 1, 'results': [{'passed': True}]},
                      {'passed': True, 'results': [{'passed': False}]},
                      {'passed': True, 'results': [{}]},
                      {'passed': True, 'results': [True]}]:
            with self.subTest(report=value):
                self.assertTrue(probe_errors(value))
        self.assertEqual(probe_errors({'passed': True, 'results': [{'passed': True}]}), [])
        self.assertEqual(probe_errors({'passed': True, 'checks': [{'passed': True}]}), [])
        self.assertTrue(probe_errors({'passed': True, 'checks': [{'passed': False}]}))
        self.assertTrue(probe_errors({'passed': True, 'results': [], 'checks': [{'passed': True}]}))


class OutputIsolation(unittest.TestCase):
    def test_fresh_directory_is_claimed_once(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "new"
            claim_output(output)
            self.assertTrue((output / ".hd2d-run").is_file())
            with self.assertRaises(ValueError):
                claim_output(output)

    def test_existing_evidence_is_rejected_not_erased(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)
            report = output / "probe.json"
            report.write_text('{"passed":true,"results":[{"passed":true}]}')
            before = report.read_bytes()
            with self.assertRaises(ValueError):
                claim_output(output)
            self.assertEqual(report.read_bytes(), before)
            self.assertFalse((output / ".hd2d-run").exists())

    def test_empty_directory_accepts_only_one_concurrent_runner(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as root:
            def claim(_):
                try:
                    claim_output(Path(root))
                    return True
                except (ValueError, FileExistsError):
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sum(pool.map(claim, range(2))), 1)


class FaultIsolation(unittest.TestCase):
    def test_restore_after_exception(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            path = project / 'fixture.gd'
            path.write_text('original')
            with self.assertRaises(RuntimeError):
                with patched(project, 'fixture.gd', 'original', 'broken'):
                    self.assertEqual(path.read_text(), 'broken')
                    raise RuntimeError('test interrupted')
            self.assertEqual(path.read_text(), 'original')

    def test_missing_resource_restored(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            path = project / 'fixture.tres'
            path.write_bytes(b'resource')
            with patched(project, 'fixture.tres', None):
                self.assertFalse(path.exists())
            self.assertEqual(path.read_bytes(), b'resource')

    def test_live_and_parent_escape_rejected(self):
        with self.assertRaises(ValueError):
            with patched(PROJECT, 'project.godot', None):
                self.fail('entered live project')
        with self.assertRaises(ValueError):
            with patched(PROJECT / 'scripts', 'main.gd', None):
                self.fail('entered live project subdirectory')
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError):
                with patched(Path(name), '../outside', None):
                    self.fail('escaped fixture')


if __name__ == '__main__':
    unittest.main()
