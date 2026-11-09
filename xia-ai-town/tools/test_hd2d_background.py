#!/usr/bin/env python3
"""Safety/negative tests for the private-display runner (no desktop input)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hd2d_background import BackgroundInteraction, private_environment, validate_display, write_json
from hd2d_smoke import run


class IsolationTests(unittest.TestCase):
    def test_strip_host_connections_without_mutating_parent(self):
        host = {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0', 'SWAYSOCK': '/host/sway',
                'MANGOSOCK': '/host/mango', 'XAUTHORITY': '/host/auth',
                'DBUS_SESSION_BUS_ADDRESS': 'host-bus', 'PULSE_SERVER': 'host-audio'}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, host, clear=True):
            result = private_environment(Path(folder), 'amd')
            for key in host:
                self.assertNotIn(key, result)
                self.assertEqual(os.environ[key], host[key])
            self.assertEqual(result['XDG_RUNTIME_DIR'], folder)
            self.assertTrue(result['VK_DRIVER_FILES'].endswith('/radeon_icd.json'))

    def guarded_display(self, folder):
        guard = Path(folder) / 'isolation.json'
        write_json(guard, {'runtime': folder, 'host_display': ':0'})
        return guard

    def test_private_display_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = self.guarded_display(folder)
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR': folder, 'DISPLAY': ':97',
                                         'ENABLE_GAMESCOPE_WSI': '1'}, clear=True):
                self.assertEqual(validate_display(guard)['display'], ':97')

    def test_host_display_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = self.guarded_display(folder)
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR': folder, 'DISPLAY': ':0',
                                         'ENABLE_GAMESCOPE_WSI': '1'}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'DISPLAY'):
                    validate_display(guard)

    def test_unknown_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = self.guarded_display(folder)
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR': '/host', 'DISPLAY': ':97',
                                         'ENABLE_GAMESCOPE_WSI': '1'}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'runtime guard'):
                    validate_display(guard)

    def test_worker_outside_gamescope_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = self.guarded_display(folder)
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR': folder, 'DISPLAY': ':97'}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'gamescope'):
                    validate_display(guard)

    def test_input_never_inherits_host_display(self):
        with patch('hd2d_background.threading.Thread.start'):
            with self.assertRaisesRegex(RuntimeError, 'DISPLAY isolation'):
                BackgroundInteraction(123, {'DISPLAY': ':0'}, Path('/tmp/unused'), 90, ':97')
            controller = BackgroundInteraction(123, {'DISPLAY': ':97'}, Path('/tmp/unused'), 90, ':97')
        with patch('hd2d_background.subprocess.check_output', return_value='1440 900') as xdo:
            controller.xdo('getdisplaygeometry')
            self.assertEqual(xdo.call_args.kwargs['env']['DISPLAY'], ':97')

    def test_unowned_window_is_rejected_before_focus_or_pointer_move(self):
        with patch('hd2d_background.threading.Thread.start'):
            controller = BackgroundInteraction(123, {'DISPLAY': ':97'}, Path('/tmp/unused'), 90, ':97')
        controller.window = '456'
        with patch.object(controller, 'xdo', return_value='999') as xdo:
            with self.assertRaisesRegex(RuntimeError, 'Godot'):
                controller.focus()
            self.assertEqual(xdo.call_count, 1)
            self.assertEqual(xdo.call_args.args, ('getwindowpid', '456'))

    def test_partial_last_sample_does_not_become_state(self):
        with tempfile.TemporaryDirectory() as folder, patch('hd2d_background.threading.Thread.start'):
            output = Path(folder)
            (output / 'samples.jsonl').write_text('{"seconds": 4, "zoom": 2}\n{"seconds":')
            controller = BackgroundInteraction(123, {'DISPLAY': ':97'}, output, 90, ':97')
            self.assertEqual(controller.sample()['seconds'], 4)

    def test_invalid_background_options_fail_before_launch(self):
        for args in (['--ime'], ['--game'], ['--game', '--logic'], ['--background', '--duration', '10'], ['--background', '--logic'],
                     ['--background', '--display-driver', 'wayland'], ['--pitch', '66'],
                     ['--pitch', 'nan'], ['--yaw', '181'], ['--yaw', 'nan'],
                     ['--probe', '--yaw', '90'], ['--fov', '29'], ['--fov', '41'], ['--fov', 'nan'],
                     ['--projection', 'unknown']):
            with self.subTest(args=args), patch('hd2d_smoke.subprocess.Popen') as popen:
                with self.assertRaises(SystemExit) as error:
                    run(args)
                self.assertEqual(error.exception.code, 2)
                popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
