#!/usr/bin/env python3
"""Pure runner policy tests; no display/backend or real saves are opened."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from hd2d_smoke import renderer_options


class RendererPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / 'project.godot').write_text('[rendering]\nrenderer/rendering_method="gl_compatibility"\n')

    def select(self, renderer=None, game=True, gpu='amd'):
        env = {'__NV_PRIME_RENDER_OFFLOAD': '1', '__GLX_VENDOR_LIBRARY_NAME': 'nvidia', 'DRI_PRIME': '1'}
        flags, expected = renderer_options(SimpleNamespace(renderer=renderer, game=game, gpu_device=gpu), env, self.project)
        return flags, expected, env

    def test_game_default_matches_project_without_override(self):
        flags, expected, env = self.select()
        self.assertEqual(flags, [])
        self.assertEqual(expected, 'gl_compatibility')
        self.assertEqual(env['__GLX_VENDOR_LIBRARY_NAME'], 'mesa')
        self.assertNotIn('__NV_PRIME_RENDER_OFFLOAD', env)

    def test_explicit_project(self):
        self.assertEqual(self.select('project')[0], [])

    def test_forward_plus_explicit(self):
        flags, expected, env = self.select('forward_plus')
        self.assertEqual(flags, ['--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan'])
        self.assertEqual(expected, 'forward_plus')
        self.assertEqual(env, {})

    def test_compatibility_explicit(self):
        self.assertEqual(self.select('gl_compatibility')[0][-1], 'opengl3')

    def test_p1_default_unchanged(self):
        self.assertEqual(self.select(game=False)[1], 'forward_plus')

    def test_nvidia_gl_policy(self):
        self.assertEqual(self.select(gpu='nvidia')[2]['__NV_PRIME_RENDER_OFFLOAD'], '1')

    def test_project_config_not_hardcoded(self):
        (self.project / 'project.godot').write_text('renderer/rendering_method="forward_plus"\n')
        self.assertEqual(self.select()[1], 'forward_plus')

    def test_unknown_project_fails_closed(self):
        (self.project / 'project.godot').write_text('[rendering]\n')
        with self.assertRaises(RuntimeError):
            self.select()


class PerformancePolicy(unittest.TestCase):
    def test_defaults_keep_captures_and_60fps(self):
        from hd2d_game_smoke import performance_environment
        env = performance_environment(SimpleNamespace(max_fps=60, profile=False, no_captures=False))
        self.assertEqual(env, {'XIA_HD2D_MAX_FPS': '60', 'XIA_HD2D_PROFILE': '0', 'XIA_HD2D_NO_CAPTURES': '0'})

    def test_uncapped_zero_and_no_captures_survive(self):
        from hd2d_game_smoke import performance_environment
        env = performance_environment(SimpleNamespace(max_fps=0, profile=True, no_captures=True))
        self.assertEqual(env, {'XIA_HD2D_MAX_FPS': '0', 'XIA_HD2D_PROFILE': '1', 'XIA_HD2D_NO_CAPTURES': '1'})

    def test_profile_cli_rejects_invalid_combinations_before_launch(self):
        from hd2d_smoke import run
        for arguments in (['--max-fps', '-1'], ['--profile'], ['--game', '--no-captures'],
                          ['--resource-probe', '--game'], ['--resource-probe', '--probe'],
                          ['--game', '--profile', '--scene-review'], ['--game', '--max-fps', '1001']):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as raised:
                run(arguments)
            self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
