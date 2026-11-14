#!/usr/bin/env python3
"""Run isolated HD-2D correctness checks and optional real fault injections.

server/.venv/bin/python xia-ai-town/tools/hd2d_verify.py --negative --output /tmp/xia-verify
No GPU/performance claim; use hd2d_smoke.py --background for real rendering/input.
Every Godot process is bounded. Faults affect only a disposable staged project.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from hd2d_smoke import PROJECT, stage
from hd2d_validation import CONTRACT_END, claim_output, run_errors

CHECKS = (
    ('scripts', 'hd2d_script_check.gd', 'HD2D_SCRIPT_CHECKS:'),
    ('route', 'hd2d_route_check.gd', 'HD2D_ROUTE_PASS:'),
    ('controller', 'hd2d_controller_check.gd', 'HD2D_CONTROLLER_PASS:'),
    ('camera', 'hd2d_camera_check.gd', 'HD2D_CAMERA_CHECKS:'),
    ('npc', 'hd2d_npc_check.gd', 'HD2D_NPC_PASS:'),
    ('ui', 'hd2d_ui_check.gd', 'HD2D_UI_CHECKS:'),
    ('font', 'hd2d_font_check.gd', 'HD2D_FONT_CHECKS:'),
    ('query', 'hd2d_query_check.gd', 'HD2D_QUERY_CHECKS:'),
    ('resource', 'hd2d_resource_probe.gd', 'HD2D_RESOURCE_CHECKS:'),
    ('art', 'hd2d_art_check.gd', 'HD2D_ART_PASS'),
    ('contract', 'hd2d_contract.gd', CONTRACT_END),
)


def source_hashes() -> dict[str, str]:
    """Sources/assets, not caches, .import or generated .uid sidecars."""
    files = [PROJECT / 'project.godot']
    for folder in ('scripts', 'scenes', 'assets', 'tools'):
        files.extend((PROJECT / folder).rglob('*'))
    return {str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.is_file() and '__pycache__' not in p.parts
            and p.suffix not in ('.import', '.uid', '.pyc')}


@contextmanager
def patched(project: Path, relative: str, old: str | None, new: str = ''):
    """A caller cannot redirect a fault to the live checkout via .. or symlinks."""
    path = (project / relative).resolve()
    if path.is_relative_to(PROJECT.resolve()) or not path.is_relative_to(project.resolve()):
        raise ValueError('fault injection requires an isolated project path')
    data = path.read_bytes()
    try:
        if old is None:
            path.unlink()
        else:
            text = data.decode()
            if text.count(old) != 1:
                raise ValueError(f'fault target no longer unique: {relative}')
            path.write_text(text.replace(old, new, 1))
        yield
    finally:
        path.write_bytes(data)


def execute(project: Path, output: Path, env: dict, name: str,
            script: str | None = None, marker: str | None = None, seconds: float = 90) -> dict:
    godot = os.environ.get('GODOT_BIN', '/usr/bin/godot')
    command = ['timeout', '--kill-after=3', str(seconds), godot, '--headless',
               '--audio-driver', 'Dummy', '--path', str(project)]
    command += ['--script', f'res://tools/{script}'] if script else ['--import', '--quit']
    started = time.monotonic()
    # timeout(1) bounds Godot; Python's deadline also bounds the timeout wrapper.
    try:
        process = subprocess.run(command, env=env, capture_output=True, text=True, timeout=seconds + 8)
        log, code = process.stdout + process.stderr, process.returncode
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        log, code = decode(exc.stdout) + decode(exc.stderr), 124
    (output / f'{name}.log').write_text(log)
    errors = run_errors(code, log, marker)
    return {'name': name, 'engine_exit': code, 'exit_code': 1 if errors else 0,
            'errors': errors, 'seconds': round(time.monotonic() - started, 3),
            'command': command, 'log': str(output / f'{name}.log')}


def negative_cases(project: Path, output: Path, env: dict) -> list[dict]:
    results = []

    def expect(name, script, marker, evidence, *, seconds=30, engine_exit=None):
        result = execute(project, output, env, name, script, marker, seconds)
        log = Path(result['log']).read_text()
        result['expected_failure'] = True
        result['passed'] = (result['exit_code'] == 1 and evidence in log
                            and (engine_exit is None or result['engine_exit'] == engine_exit))
        results.append(result)

    with patched(project, 'scripts/hd2d/scenic_layout.gd',
                 '"gu_changfeng": {"home_to_work": [Vector2(128, 120)',
                 '"gu_changfeng": {"home_to_work": [Vector2(-100, -100)'):
        expect('bad-route', 'hd2d_route_check.gd', 'HD2D_ROUTE_PASS:', 'ROUTE_FAIL gu_changfeng')
    with patched(project, 'assets/ui/body_font.tres', None):
        expect('missing-resource', 'hd2d_font_check.gd', 'HD2D_FONT_CHECKS:', 'body_font.tres')
    with patched(project, 'scripts/hd2d/town_view.gd',
                 '\t\tpitch = Projector.GAME_PITCH', '\t\tpitch = 45.0'):
        expect('wrong-camera', 'hd2d_camera_check.gd', 'HD2D_CAMERA_CHECKS:', 'CAMERA_FAIL: fixed gameplay pitch')

    with patched(project, 'tools/hd2d_probe.gd', 'var output: String', 'var output: ImaginaryR12Type'):
        expect('bad-gpu-probe-type', 'hd2d_script_check.gd', 'HD2D_SCRIPT_CHECKS:', 'ImaginaryR12Type')
    with patched(project, 'scripts/hd2d/shaders/actor.gdshader',
                 'uniform bool unshaded = false;', 'uniform deliberately_broken_type unshaded = false;'):
        expect('bad-shader-type', 'hd2d_script_check.gd', 'HD2D_SCRIPT_CHECKS:', 'SHADER ERROR')

    fixture = project / 'tools/hd2d_negative_fixture.gd'
    fixture.write_text('extends SceneTree\nfunc _initialize() -> void:\n'
                       '\t_broken()\n\tprint("HD2D_NEGATIVE_FINISH")\n\tquit(0)\n'
                       'func _broken() -> void:\n\tassert(false, "R12 intended assertion")\n')
    expect('assert-exits-zero', fixture.name, 'HD2D_NEGATIVE_FINISH', 'Assertion failed', engine_exit=0)
    fixture.write_text('extends SceneTree\nfunc _initialize() -> void:\n'
                       '\tprint("HD2D_CONTRACT_PASS: deliberately incomplete")\n\tquit(0)\n')
    expect('premature-contract', fixture.name, CONTRACT_END, 'deliberately incomplete', engine_exit=0)
    fixture.write_text('extends SceneTree\nfunc _initialize() -> void:\n'
                       '\tprint("HD2D_NEGATIVE_FINISH")\n')
    expect('never-quits', fixture.name, 'HD2D_NEGATIVE_FINISH', 'HD2D_NEGATIVE_FINISH', seconds=2, engine_exit=124)
    fixture.unlink()
    return results


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--negative', action='store_true', help='prove real faults fail; verify restoration afterwards')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=float, default=90, help='per-check timeout in seconds')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    if not shutil.which('timeout'):
        parser.error('GNU timeout is required by this Linux correctness runner')
    output = (args.output or Path(tempfile.mkdtemp(prefix='xia-hd2d-checks-'))).resolve()
    if output.is_relative_to(PROJECT.resolve()):
        parser.error('--output must be outside the live Godot project')
    claim_output(output)
    report = {'scope': 'isolated headless correctness and fault-gate verification; NOT GPU/performance/P5',
              'passed': False, 'checks': [], 'negative': [], 'restored': []}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    before = source_hashes()
    try:
        with tempfile.TemporaryDirectory(prefix='xia-hd2d-verify-') as temporary:
            scratch = Path(temporary)
            project = scratch / 'project'
            stage(project)
            env = {k: v for k, v in os.environ.items() if not k.startswith('XIA_')}
            for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
                directory = scratch / key.lower()
                directory.mkdir()
                env[key] = str(directory)
            env.update(XIA_HD2D_OUTPUT=str(output / 'resources'), XIA_HD2D_FONT_REPORT=str(output / 'font.json'))
            result = execute(project, output, env, 'import', seconds=180)
            report['checks'].append(result)
            if result['exit_code']:
                raise RuntimeError('fresh import failed')
            for name, script, marker in CHECKS:
                result = execute(project, output, env, name, script, marker, args.timeout)
                report['checks'].append(result)
                print(f'{name}: {"PASS" if not result["exit_code"] else "FAIL"}', flush=True)
            # Exercise both physics threading settings; production config is unchanged.
            with patched(project, 'project.godot', '3d/physics_engine="Jolt Physics"',
                         '3d/physics_engine="Jolt Physics"\n3d/run_on_separate_thread=true'):
                report['checks'].append(execute(project, output, env, 'query-threaded', 'hd2d_query_check.gd', 'HD2D_QUERY_CHECKS:', args.timeout))
            if args.negative:
                if any(check['exit_code'] for check in report['checks']):
                    raise RuntimeError('positive checks must pass before testing negative cases')
                report['negative'] = negative_cases(project, output, env)
                for name, script, marker in CHECKS:
                    if name in ('scripts', 'route', 'camera', 'font'):
                        report['restored'].append(execute(project, output, env, f'restored-{name}', script, marker, args.timeout))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        after = source_hashes()
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        report['source_files_hashed'] = len(before)
        report['changed_live_sources'] = changed
        report['passed'] = (not report.get('error') and not changed
                            and all(item['exit_code'] == 0 for item in report['checks'] + report['restored'])
                            and all(item['passed'] for item in report['negative']))
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'HD2D_VERIFY_{"PASS" if report["passed"] else "FAIL"}: {output / "report.json"}')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(run())
