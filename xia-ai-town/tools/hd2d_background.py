#!/usr/bin/env python3
"""Private gamescope/headless display and OS-input regression for the P1 fixture.

No XTEST call may inherit the user's DISPLAY. No nested/DRM fallback is allowed.
The helper is normally reached via hd2d_smoke.py --background, not run directly.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ICD_DIR = Path('/usr/share/vulkan/icd.d')
DESKTOP_ENV = (
    'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'SWAYSOCK', 'MANGOSOCK',
    'DBUS_SESSION_BUS_ADDRESS', 'PULSE_SERVER', 'PULSE_RUNTIME_PATH', 'PIPEWIRE_REMOTE',
)


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(path)


def private_environment(runtime: Path, compositor_gpu: str) -> dict:
    env = dict(os.environ)
    for key in DESKTOP_ENV:
        env.pop(key, None)
    env['XDG_RUNTIME_DIR'] = str(runtime)
    for name in ('DATA', 'CONFIG', 'CACHE', 'STATE'):
        folder = runtime / name.lower()
        folder.mkdir()
        env[f'XDG_{name}_HOME'] = str(folder)
    env['VK_DRIVER_FILES'] = str(ICD_DIR / ('radeon_icd.json' if compositor_gpu == 'amd' else 'nvidia_icd.json'))
    return env


def stop_group(process: subprocess.Popen) -> None:
    """Only the new process group created by this invocation, including Xwayland."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        pass
    # The group can still contain children after its leader exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def run_background(args) -> int:
    from shutil import which
    for tool in ('gamescope', 'Xwayland', 'xdotool', 'nice'):
        if not which(tool):
            raise RuntimeError(f'后台测试缺少 {tool}；不会退回前台或自动安装依赖')
    # Image decoding verifies real nonempty readbacks, not just file existence.
    from PIL import Image  # noqa: F401
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='xia-hd2d-background-'))
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError('后台测试请使用空的 --output 目录，避免旧截图/报告被误判为本轮结果')
    duration = args.duration or 90
    worker_args = [
        '--gpu-device', args.gpu_device, '--quality', args.quality, '--pitch', str(args.pitch), '--yaw', str(args.yaw),
        '--fov', str(args.fov), '--projection', args.projection,
        '--display-driver', 'x11', '--no-vsync', '--output', str(output),
    ]
    if args.game:
        worker_args += ['--game']
    if args.ime:
        worker_args += ['--ime']
    if args.environment_probe:
        worker_args += ['--environment-probe']
    elif args.actor_probe:
        worker_args += ['--actor-probe']
    elif args.probe:
        worker_args += ['--probe']
    else:
        worker_args += ['--duration', str(duration)]
    with tempfile.TemporaryDirectory(prefix='xia-hd2d-display-') as folder:
        runtime = Path(folder)
        env = private_environment(runtime, args.compositor_gpu)
        if not Path(env['VK_DRIVER_FILES']).is_file():
            raise RuntimeError(f'Missing compositor GPU ICD: {env["VK_DRIVER_FILES"]}')
        manifest = {
            'runtime': str(runtime), 'host_display': os.environ.get('DISPLAY'),
            'host_wayland_display': os.environ.get('WAYLAND_DISPLAY'),
            'compositor_gpu': args.compositor_gpu,
        }
        guard = runtime / 'isolation.json'
        write_json(guard, manifest)
        command = [
            'nice', '-n', '10', 'gamescope', '--backend', 'headless',
            '-W', '1440', '-H', '900', '-w', '1440', '-h', '900', '-r', '60',
            '--', sys.executable, str(Path(__file__).resolve()),
            '--worker', str(guard), *worker_args,
        ]
        print('BACKGROUND: 私有 gamescope/headless，1440×900，限60 FPS；不操作前台桌面', flush=True)
        print(f'后台日志与截图：{output}', flush=True)
        with (output / 'display.log').open('w') as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=duration + 300)
            finally:
                stop_group(process)
        report = output / 'background.json'
        if not report.exists():
            raise RuntimeError(f'虚拟显示/worker未完成（退出码{code}）；见 {output / "display.log"}')
        data = json.loads(report.read_text())
        data['compositor_exit_code'] = code
        data['passed'] = data['passed'] and code == 0
        write_json(report, data)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data['passed'] else 1


def validate_display(guard: Path) -> dict:
    manifest = json.loads(guard.read_text())
    runtime = Path(manifest['runtime']).resolve()
    display = os.environ.get('DISPLAY', '')
    if guard.resolve().parent != runtime or os.environ.get('XDG_RUNTIME_DIR') != str(runtime):
        raise RuntimeError('Private display runtime guard mismatch')
    if not display.startswith(':') or display == manifest['host_display']:
        raise RuntimeError('拒绝向前台/未知DISPLAY发送输入')
    if os.environ.get('ENABLE_GAMESCOPE_WSI') != '1':
        raise RuntimeError('Worker was not launched inside gamescope')
    manifest.update(display=display, backend='gamescope/headless', max_fps=60)
    return manifest


class BackgroundInteraction:
    """Drive XTEST on the owned private window; assert resulting native UI state."""
    def __init__(self, pid: int, env: dict, output: Path, duration: float, display: str):
        if not display or env.get('DISPLAY') != display:
            raise RuntimeError('XTEST DISPLAY isolation mismatch')
        self.pid, self.env, self.output, self.duration = pid, dict(env), output, duration
        self.window = ''
        self.checks: list[dict] = []
        self.screenshots: dict[str, str] = {}
        self.held: set[str] = set()
        self.error: BaseException | None = None
        self.completed = False
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._entry, name='xia-private-input', daemon=True)
        self.thread.start()

    def xdo(self, *args) -> str:
        return subprocess.check_output(['xdotool', *map(str, args)], env=self.env, text=True,
                                       stderr=subprocess.STDOUT, timeout=4).strip()

    def sleep(self, seconds: float) -> None:
        if self.cancelled.wait(seconds):
            raise RuntimeError('后台输入已取消')

    def wait(self, predicate, message: str, timeout: float = 8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            self.sleep(.1)
        raise AssertionError(message)

    def sample(self) -> dict:
        path = self.output / 'samples.jsonl'
        if path.exists():
            for line in reversed(path.read_text().splitlines()):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue  # A partially written last line is not a new sample.
        return {}

    def state(self, predicate, message: str, timeout: float = 8) -> dict:
        return self.wait(lambda: (s if predicate(s) else None) if (s := self.sample()) else None,
                         message, timeout)

    def focus(self) -> None:
        if int(self.xdo('getwindowpid', self.window)) != self.pid:
            raise RuntimeError('拒绝操作不属于本次Godot进程的窗口')
        self.xdo('mousemove', '--window', self.window, 720, 785)
        self.xdo('windowfocus', '--sync', self.window)

    def key(self, name: str) -> None:
        self.focus()
        # XTEST, not --window/XSendEvent. Never touches the parent's X server.
        self.xdo('key', '--clearmodifiers', name)
        self.sleep(.15)

    def move(self, name: str, seconds: float = .65) -> None:
        self.focus()
        self.held.add(name)
        self.xdo('keydown', name)
        try:
            self.sleep(seconds)
        finally:
            self.xdo('keyup', name)
            self.held.discard(name)
        self.sleep(.15)

    def check(self, name: str, condition: bool, detail=None) -> None:
        self.checks.append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(f'{name}: {detail}')

    def latest_snapshot(self) -> dict:
        paths = sorted(self.output.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths:
            if not path.name.startswith(('view-', 'manual-')):
                continue
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
        return {}

    def capture(self, name: str) -> dict:
        from PIL import Image
        pattern = 'manual-*.png'
        old = set(self.output.glob(pattern))
        self.key('F12')
        def new_image():
            for path in sorted(set(self.output.glob(pattern)) - old, reverse=True):
                metadata = path.with_suffix('.json')
                try:
                    data = json.loads(metadata.read_text())
                    if data['panel']:
                        continue
                    # One native readback sanity check suffices for this fixed-size
                    # run. Real geometry visibility is covered by --probe, not
                    # a repeated 'nonblack image' check on the paper HUD.
                    if not self.screenshots:
                        with Image.open(path) as image:
                            image.load()
                            self.check('native_gpu_capture',
                                       image.size == (1440, 900) and image.convert('RGB').getbbox() is not None,
                                       list(image.size))
                    self.screenshots[name] = path.name
                    return data
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
            return None
        return self.wait(new_image, f'截图未完成: {name}', timeout=12)

    def click_at(self, center) -> None:
        self.focus()
        self.xdo('mousemove', '--window', self.window, *map(round, center))
        self.xdo('click', '1')

    def drag_camera(self, dx: int, dy: int) -> None:
        self.focus()
        self.xdo('mousemove', '--window', self.window, 720, 600)
        self.xdo('mousedown', '2')
        try:
            self.sleep(.1)
            # Several actual motions also exercise the continuous update path.
            for step in range(1, 7):
                self.xdo('mousemove', '--window', self.window,
                         round(720 + dx * step / 6), round(600 + dy * step / 6))
                self.sleep(.05)
        finally:
            self.xdo('mouseup', '2')
        self.sleep(.3)

    def camera_checks(self) -> None:
        self.key('Home')
        self.state(lambda s: s['pitch'] == 38 and s['yaw'] == 30 and s['fov'] == 35, '视角复位失败')
        self.key('c')
        controls = self.capture('camera_panel')['camera_controls']
        self.check('camera_panel_visible', controls['visible'])
        perspective = self.sample()['projection'] == 'perspective'
        sliders = [('pitch', .25), ('yaw', .625)] + ([('fov', .75)] if perspective else [])
        for key, fraction in sliders:
            x, y, width, height = controls[key]
            self.click_at((x + width * fraction, y + height / 2))
        adjusted = self.state(lambda s: 36 <= s['pitch'] <= 41 and 38 <= s['yaw'] <= 52 and (not perspective or 36 <= s['fov'] <= 39),
                              '实际鼠标调节视角滑条失败')
        self.check('mouse_camera_sliders', True, {k: adjusted[k] for k in ('pitch', 'yaw', 'fov')})
        self.key('Escape')
        self.capture('oblique_day')
        self.key('f')
        self.capture('oblique_without_post')
        self.key('f')
        before = self.sample()
        self.drag_camera(220, 100)
        dragged = self.state(lambda s: s['yaw'] < before['yaw'] - 50 and s['pitch'] > before['pitch'] + 12,
                             '中键拖动未改变方位/俯角')
        self.check('middle_drag_orbit', True, {k: dragged[k] for k in ('pitch', 'yaw')})
        self.capture('oblique_dragged')
        # A real OS key at the rotated view must move right on that view, with
        # the same east animation row after releasing it.
        before_walk = self.capture('orbit_before_walk')
        self.move('d', .65)
        after_walk = self.capture('orbit_after_walk')
        angle = math.radians(after_walk['yaw'])
        dx, dy = (after_walk['position'][i] - before_walk['position'][i] for i in (0, 1))
        side, back = dx * math.cos(angle) - dy * math.sin(angle), dx * math.sin(angle) + dy * math.cos(angle)
        self.check('camera_relative_os_walking', side > 30 and abs(back) < 3 and after_walk['actor_direction'] == 6,
                   {'right': side, 'back': back, 'frame': after_walk['actor_frame']})
        self.key('Home')
        self.state(lambda s: s['pitch'] == 38 and s['yaw'] == 30 and s['fov'] == 35, '拖动后视角复位失败')
        self.check('camera_reset', True)

    def click_zoom(self, value: int) -> None:
        buttons = self.latest_snapshot()['buttons']
        center = next(b['center'] for b in buttons if b['text'] == f'{value}×')
        self.click_at(center)
        self.state(lambda s: s['zoom'] == value, f'实际鼠标缩放{value}×失败')
        self.check(f'mouse_zoom_{value}', True, center)
        self.sleep(.3)

    def _run(self) -> None:
        self.state(lambda s: s['seconds'] > 3.5, 'Godot样板未就绪', timeout=45)
        def owned_window():
            try:
                candidates = self.xdo('search', '--onlyvisible', '--pid', self.pid).splitlines()
                for candidate in candidates:
                    if int(self.xdo('getwindowpid', candidate)) == self.pid:
                        return candidate
            except subprocess.CalledProcessError:
                pass
            return None
        self.window = self.wait(owned_window, '找不到私有显示里的Godot窗口')
        self.check('private_owned_window', True, {'pid': self.pid, 'window': self.window, 'display': self.env['DISPLAY']})
        self.key('Home')
        self.capture('front_day_2x')
        self.camera_checks()
        before = self.sample()
        self.move('d')
        after = self.state(lambda s: s['seconds'] > before['seconds'] and s['position'] != before['position'], 'OS按键未推动人物')
        self.check('os_key_walking', True, {'before': before['position'], 'after': after['position']})
        self.key('F2')
        self.key('o')
        self.state(lambda s: not s['occlusion_enabled'], '关闭渐隐失败')
        self.capture('rear_opaque')
        self.key('o')
        self.state(lambda s: s['occlusion_enabled'] and bool(s['fade_groups']), '屋后局部虚化未开启')
        self.capture('rear_fade')
        self.key('t')
        self.state(lambda s: s['protect_target'], '双目标保护未开启')
        self.capture('rear_dual')
        self.check('local_translucency_and_dual_target', True)
        self.key('t')
        self.key('F1')
        for value in (1, 3, 2):
            self.click_zoom(value)
            if value != 2:
                self.capture(f'front_{value}x')
            self.move('d', .4)
            self.move('a', .4)
        for spot, direction in (('F1', 'd'), ('F2', 'a'), ('F3', 'w'), ('F4', 's')):
            self.key(spot)
            self.move(direction)
        self.key('F4')
        self.move('s', 1.8)
        expanded = self.capture('expanded_south_path')
        self.check('expanded_walk_crosses_old_invisible_wall', expanded['position'][1] > 520, expanded['position'])
        self.key('F1')
        self.key('l')
        self.state(lambda s: 18 <= s['hour'] < 19, '黄昏切换失败')
        self.capture('front_dusk')
        self.key('l')
        self.state(lambda s: 22 <= s['hour'] < 23, '夜间切换失败')
        self.capture('front_night')
        self.key('l')
        self.check('day_dusk_night', True)
        self.key('b')
        before = self.state(lambda s: s['panel'], '面板未打开')
        self.move('Right', 1.1)
        self.drag_camera(100, 80)
        after = self.state(lambda s: s['seconds'] > before['seconds'] + 3, '面板样本未更新')
        self.check('modal_blocks_walking', after['position'] == before['position'], after['position'])
        self.check('modal_blocks_camera', (after['pitch'], after['yaw'], after['fov']) == (before['pitch'], before['yaw'], before['fov']))
        self.key('Escape')
        self.state(lambda s: not s['panel'], 'Esc未关闭面板')
        before = self.sample()
        self.move('d')
        self.state(lambda s: s['position'] != before['position'], '关闭面板后移动未恢复')
        self.check('modal_close_resumes_walking', True)
        # Sustained input instead of loading and then idling until timeout.
        while self.sample()['seconds'] < self.duration - 6:
            self.key('F1')
            self.move('d', .8)
            self.move('a', .8)
            self.key('F2')
            self.move('a', .5)
            self.move('d', .5)
        self.key('F1')
        self.completed = True

    def _entry(self) -> None:
        try:
            self._run()
        except BaseException as error:
            self.error = error
        finally:
            for name in tuple(self.held):
                try:
                    self.xdo('keyup', name)
                except Exception:
                    pass
            write_json(self.output / 'interaction.json', {
                'passed': self.completed and self.error is None,
                'error': None if self.error is None else str(self.error),
                'checks': self.checks, 'screenshots': self.screenshots,
                'scope': 'P1 private-display OS input; NOT Mango presentation, IME or P5 acceptance',
            })

    def close(self) -> None:
        self.cancelled.set()
        self.thread.join(timeout=6)
        if self.thread.is_alive():
            raise RuntimeError('后台输入线程未停止')


def worker(guard: Path, argv: list[str]) -> int:
    output = Path(argv[argv.index('--output') + 1])
    scope = 'P2/P3 real-client isolated regression' if '--game' in argv else 'P3 environment GPU probe' if '--environment-probe' in argv else 'P1 isolated background regression'
    result = {'passed': False, 'scope': scope + '; NOT physical-display/P5 acceptance'}
    try:
        isolation = validate_display(guard)
        result['isolation'] = isolation
        from hd2d_smoke import run
        factory = None if any(flag in argv for flag in ('--probe', '--actor-probe', '--environment-probe', '--game')) else lambda pid, env, out, duration: BackgroundInteraction(
            pid, env, out, duration, isolation['display'])
        code = run(argv, private_display=isolation, automation_factory=factory)
        result.update(passed=code == 0, worker_exit_code=code)
    except Exception as error:
        result['error'] = str(error)
        traceback.print_exc()
        code = 1
    write_json(output / 'background.json', result)
    return code


if __name__ == '__main__':
    if len(sys.argv) < 4 or sys.argv[1] != '--worker':
        raise SystemExit('请用 hd2d_smoke.py --background 启动，不要直接运行此内部worker')
    raise SystemExit(worker(Path(sys.argv[2]), sys.argv[3:]))
