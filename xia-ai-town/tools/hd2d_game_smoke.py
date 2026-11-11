#!/usr/bin/env python3
"""P2 real-client GPU/OS regression; called by hd2d_smoke.py --background --game.

The real backend handles game state, shops and battles in a disposable database.
Only model dialogue/greetings use a deterministic test fixture, avoiding LLM calls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import time

import httpx

from hd2d_background import BackgroundInteraction, write_json
from smoke_test import SERVER, ERROR_LOG, fixture, reserve_listener, stop_process


class GameInteraction(BackgroundInteraction):
    def focus(self) -> None:
        if int(self.xdo('getwindowpid', self.window)) != self.pid:
            raise RuntimeError('拒绝操作非自有Godot窗口')
        self.xdo('mousemove', '--window', self.window, 720, 600)
        self.xdo('windowfocus', '--sync', self.window)

    def capture(self, name: str) -> dict:
        old = set(self.output.glob('manual-*.json'))
        # A capture request must not consume or commit an active IME preedit.
        (self.output / 'capture.request').write_text(name)

        def ready():
            for path in sorted(set(self.output.glob('manual-*.json')) - old):
                try:
                    data = json.loads(path.read_text())
                    from PIL import Image
                    with Image.open(path.with_suffix('.png')) as image:
                        image.load()
                        if image.size != tuple(data['window']):
                            raise AssertionError('GPU screenshot is not native window size')
                    self.screenshots[name] = path.with_suffix('.png').name
                    return data
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
            return None
        return self.wait(ready, f'完整客户端截图失败：{name}', timeout=15)

    def click_button(self, prefix: str) -> dict:
        state = self.state(lambda s: any(b['text'].startswith(prefix) for b in s['buttons']), f'找不到按钮：{prefix}')
        center = next(b['center'] for b in state['buttons'] if b['text'].startswith(prefix))
        self.click_at(center)
        self.sleep(.35)
        return state

    def _run(self) -> None:
        self.state(lambda s: s['seconds'] > 3.5 and s['online'] and s['gold'] >= 0, '完整客户端未连到隔离后端', timeout=60)

        def owned():
            try:
                for window in self.xdo('search', '--onlyvisible', '--pid', self.pid).splitlines():
                    if int(self.xdo('getwindowpid', window)) == self.pid:
                        return window
            except subprocess.CalledProcessError:
                pass
            return None
        self.window = self.wait(owned, '未找到自有GPU窗口')
        self.check('private_owned_window', True, {'pid': self.pid, 'display': self.env['DISPLAY']})
        initial = self.capture('main_hud')
        self.check('real_main_native_viewport', initial['window'] == [1440, 900] and initial['viewport'] == [1440, 900])
        self.check('all_server_actors_present', len(initial['npcs']) == 7 and len(initial['population']) > 7)
        required_assets = set(initial['actor_assets'].get(key, '') for key in initial['actor_assets'])
        self.check('p3_actor_sheets_loaded', {'gu_changfeng', 'tie_wushuang', 'su_wanqing', 'chen_bansong', 'wang_dazhu', 'liu_xiaodie', 'zhang_mantou', 'wolf', 'bandit', 'boss'} <= required_assets)
        if self.env.get('XIA_HD2D_IME') == '1':
            self.key('F5')
            self.key('e')
            self.state(lambda s: s['modal'] == 'dialogue', 'IME专项没有打开真实对话输入框')
            self.ime_checks()
            self.key('Escape')
            self.state(lambda s: not s['modal'], 'IME结束后Esc未关闭对话')
        self.key('F4')
        before = self.state(lambda s: s['position'] == [832, 624], '诊断落点尚未同步')
        self.move('d', .5)
        after = self.state(lambda s: s['position'] != before['position'], '真实WASD没有移动')
        self.check('camera_relative_wasd', after['position'][0] > before['position'][0] + 20 and after['position'][1] < before['position'][1] - 10)
        # Schedule pacing can put Zhang outside 38px of the fixed town spawn.
        # Inspect the bubble as the actual nearby target; ordinary occluded NPCs
        # correctly keep their labels hidden and must not make this flaky.
        self.key('F5')
        self.state(lambda s: s['target'] == 'npc/zhang_mantou', '气泡检查没有接近真实NPC目标')
        self.key('F9')
        self.state(lambda s: s['labels'].get('npc/zhang_mantou/bubble') == '清溪路远，客官慢行。', '原生NPC气泡没有显示')
        self.capture('native_npc_bubble')
        self.state(lambda s: 'npc/zhang_mantou/bubble' not in s['labels'], 'tick时钟气泡没有正常到期')
        self.check('native_bubble_and_monotonic_expiry', True)
        self.key('F4')
        self.key('c')
        self.state(lambda s: s['camera_panel'], '视角面板没有打开')
        for key, fraction in (('fov', .8), ('pitch', .75), ('yaw', .25)):
            before_value = self.sample()[key]
            slider = self.sample()['sliders'][key]
            center = [slider['position'][0] + slider['size'][0] * fraction,
                      slider['position'][1] + slider['size'][1] * .5]
            self.click_at(center)
            self.state(lambda s: abs(s[key] - before_value) > 1, f'原生{key}滑条实际点击无效')
        self.key('Home')
        self.state(lambda s: s['pitch'] == 38 and s['yaw'] == 30 and s['fov'] == 35, '滑条吞掉Home复位键')
        self.check('native_camera_mouse_sliders_and_keyboard_focus', True)
        self.key('Escape')
        self.state(lambda s: not s['camera_panel'], 'Esc没有收起视角面板')
        self.drag_camera(100, 80)
        self.state(lambda s: abs(s['yaw'] - 30) > 10 and abs(s['pitch'] - 38) > 5, '中键拖动没有改变视角')
        self.key('Home')
        self.state(lambda s: s['pitch'] == 38 and s['yaw'] == 30 and s['fov'] == 35, '视角复位失败')
        self.check('native_camera_controls', True)
        for zoom, clicks in ((3, ['4']), (1, ['5', '5']), (2, ['4'])):
            self.focus()
            self.xdo('mousemove', '--window', self.window, 720, 600)
            for button in clicks:
                self.xdo('click', button)
                self.sleep(.15)
            self.state(lambda s: s['zoom'] == zoom, f'滚轮{zoom}×失败')
        self.check('os_wheel_1_2_3x', True)
        self.key('F2')
        self.state(lambda s: bool(s['fade_groups']), '正式客户端屋后局部虚化没有触发')
        self.capture('inn_local_reveal')
        self.key('F7')
        self.state(lambda s: not s['occlusion_enabled'] and not s['fade_groups'], '关闭局部虚化没有恢复')
        self.capture('inn_hard_occlusion')
        self.key('F7')
        self.key('F4')
        self.key('b')
        locked = self.state(lambda s: s['modal'] == 'inventory', '行囊未打开')
        self.move('d', .5)
        self.drag_camera(80, 60)
        self.sleep(.3)
        still = self.sample()
        self.check('modal_blocks_move_and_drag', still['position'] == locked['position'] and still['yaw'] == locked['yaw'] and still['pitch'] == locked['pitch'])
        self.key('Escape')
        self.state(lambda s: not s['modal'], 'Esc未关闭行囊')
        self.xdo('windowsize', self.window, '1100', '720')
        self.state(lambda s: s['window'] == [1100, 720] and s['viewport'] == [1100, 720], '实际窗口resize没有同步原生视口')
        self.key('i')
        self.state(lambda s: s['modal'] == 'character', '角色面板未打开')
        self.xdo('mousemove', '--window', self.window, 720, 560)
        self.xdo('click', '--repeat', '5', '--delay', '120', '5')
        self.state(lambda s: s['scroll'] > 0, '实际鼠标长面板滚动失败')
        self.check('native_mouse_panel_scroll', True)
        self.key('Escape')
        self.xdo('windowsize', self.window, '1440', '900')
        self.state(lambda s: s['window'] == [1440, 900] and s['viewport'] == [1440, 900], '窗口复原没有同步视口')
        self.key('F3')
        self.key('e')
        shop = self.state(lambda s: s['modal'] == 'shop' and not s['busy'], '门口交互未进入药铺')
        self.click_button('购入 · 15')
        self.state(lambda s: not s['busy'] and s['gold'] == shop['gold'] - 15, '真实后端购买未同步到HUD')
        self.check('shop_os_click_and_server_settlement', True)
        self.capture('shop_purchase')
        self.key('Escape')
        self.key('F5')
        self.key('e')
        dialogue = self.state(lambda s: s['modal'] == 'dialogue' and s['target'] == 'npc/zhang_mantou', '当前NPC交互未接入双目标保护')
        self.sleep(1)
        self.check('dialogue_freezes_current_npc', self.sample()['npcs']['zhang_mantou'] == dialogue['npcs']['zhang_mantou'])
        # ASCII is sufficient to test the native LineEdit. No Chinese key injection.
        self.xdo('type', '--clearmodifiers', '--delay', '70', 'p2-stream-check')
        self.click_button('作 揖')
        partial = self.state(lambda s: s['busy'] and '山路' in s['text'], 'SSE token没有在done前显示')
        done = self.state(lambda s: not s['busy'] and '山路清静，慢行即可。' in s['text'], 'SSE完成态没有恢复UI')
        self.check('sse_token_reset_done', '这段需要重试' not in done['text'] and partial['target'] == 'npc/zhang_mantou')
        self.capture('streaming_dialogue')
        self.key('Escape')
        self.state(lambda s: not s['modal'], '关闭对话失败', timeout=8)
        self.key('F1')
        self.state(lambda s: s['target'] != 'npc/zhang_mantou', '离开后未解除当前目标')
        self.key('F6')
        self.move('w', .6)
        self.state(lambda s: s['modal'] == 'battle' and s['battle'].get('active') and s['target'] == 'mob/wolf_1', '自然接近怪物没有触发真实战斗')
        self.capture('battle_active')
        for _ in range(8):
            if not self.sample()['battle'].get('active'):
                break
            self.click_button('普攻')
            self.state(lambda s: not s['busy'], '战斗请求未结束')
        result = self.state(lambda s: s['battle'].get('result') == 'win', '真实战斗未结束为胜利')
        self.click_button('收剑')
        self.state(lambda s: not s['modal'] and 'mob/wolf_1' not in s['population'], '击杀怪物未从3D展示移除')
        self.check('battle_os_input_server_result_and_actor_removal', result['battle']['result'] == 'win')
        while self.sample()['seconds'] < self.duration - 4:
            self.key('F4')
            self.move('d', .65)
            self.move('a', .65)
        self.capture('main_after_interaction')
        self.completed = True

    def ime_checks(self) -> None:
        for args in (('-s', 'rime'), ('-o',)):
            subprocess.run(['fcitx5-remote', *args], env=self.env, check=True, timeout=5)
        self.sleep(1)
        controls = {arg: subprocess.check_output(['fcitx5-remote', *arg.split()], env=self.env, text=True, timeout=5).strip() for arg in ('-n', '')}
        write_json(self.output / 'ime-control.json', controls)
        # Physical ASCII pinyin goes through the real, privately launched Rime.
        self.xdo('type', '--clearmodifiers', '--delay', '120', 'nihao')
        self.sleep(.5)
        self.capture('real_rime_preedit')
        preedit = self.state(lambda s: bool(s['ime']) and s['modal'] == 'dialogue', 'Rime没有产生真实组合输入', timeout=20)
        self.xdo('key', '--clearmodifiers', 'Escape')
        self.sleep(.2)
        self.state(lambda s: not s['ime'] and s['modal'] == 'dialogue', 'Esc没有取消组合输入并保留对话')
        self.check('real_rime_escape_cancels_preedit_only', True, preedit['ime'])
        self.xdo('key', 'ctrl+a', 'BackSpace')
        self.state(lambda s: not s['input_text'], '清理IME预热首键失败')
        self.xdo('type', '--clearmodifiers', '--delay', '120', 'nihao')
        self.state(lambda s: s['ime'].replace(' ', '') == 'nihao', 'Rime拼音组合尚未就绪')
        self.xdo('key', '--clearmodifiers', 'space')
        self.state(lambda s: '你好' in s['input_text'], 'Rime没有提交中文到原生LineEdit')
        self.capture('real_rime_commit')
        self.check('real_rime_commits_chinese', True)
        self.xdo('key', 'ctrl+a', 'BackSpace')
        subprocess.run(['fcitx5-remote', '-c'], env=self.env, check=True, timeout=5)

    def _entry(self) -> None:
        super()._entry()
        report = json.loads((self.output / 'interaction.json').read_text())
        report['scope'] = 'P2 real Main + disposable real backend; model-only SSE fixture; ' + ('includes private X11/Rime composition/Esc/commit; NOT physical-display/P5' if self.env.get('XIA_HD2D_IME') == '1' else 'NOT physical-display/IME/P5')
        write_json(self.output / 'interaction.json', report)


# Only imported by a new isolated uvicorn process; production modules/files stay untouched.
MODEL_FIXTURE = '''import asyncio
from app import main

async def talk_fixture(db, mem_store, npc_id, message, **kwargs):
    yield "start", {"npc": "张馒头"}
    yield "token", {"text": "这段需要重试。"}
    await asyncio.sleep(0.35)
    yield "reset", {}
    yield "token", {"text": "山路"}
    await asyncio.sleep(1.2)
    yield "token", {"text": "清静，慢行即可。"}
    await asyncio.sleep(0.35)
    yield "done", {"reply": "山路清静，慢行即可。", "end": False, "acts": []}

async def greet_fixture(*args, **kwargs):
    return {"greet": True, "reply": "清溪路远，客官慢行。", "affinity": 0}

main.dialogue_mod.talk_stream = talk_fixture
main.greet_mod.greet = greet_fixture
app = main.app
'''


def run_game(args, isolation: dict) -> int:
    from hd2d_smoke import stage
    output = args.output.resolve()
    duration = args.duration or 120
    with tempfile.TemporaryDirectory(prefix='xia-hd2d-game-') as folder:
        scratch = Path(folder)
        project = scratch / 'project'
        stage(project)
        env = dict(os.environ, XIA_SMOKE_ROOT=str(scratch), XIA_WORLD_VIEW='hd2d',
                   DB_PATH=str(scratch / 'xia.db'), CHROMA_DIR=str(scratch / 'chroma'),
                   SCHEDULER_ENABLED='false', GODOT_SILENCE_ROOT_WARNING='1', PYTHONDONTWRITEBYTECODE='1',
                   XIA_HD2D_OUTPUT=str(output), XIA_HD2D_SCENE_REVIEW='1' if args.scene_review else '0', XIA_HD2D_DURATION=str(duration), XIA_HD2D_QUALITY=args.quality,
                   XIA_HD2D_PITCH=str(args.pitch), XIA_HD2D_YAW=str(args.yaw), XIA_HD2D_FOV=str(args.fov),
                   XIA_HD2D_PROJECTION=args.projection, NO_PROXY='localhost,127.0.0.1', no_proxy='localhost,127.0.0.1',
                   ENABLE_GAMESCOPE_WSI='0')
        for name in ('DATA', 'CONFIG', 'CACHE', 'STATE'):
            env[f'XDG_{name}_HOME'] = str(scratch / name.lower())
        if env.get('DISPLAY') != isolation['display']:
            raise RuntimeError('P2 private display mismatch')
        env.pop('WAYLAND_DISPLAY', None)
        env['VK_DRIVER_FILES'] = '/usr/share/vulkan/icd.d/' + ('nvidia_icd.json' if args.gpu_device == 'nvidia' else 'radeon_icd.json')
        godot = os.environ.get('GODOT_BIN', '/usr/bin/godot')
        imported = subprocess.run([godot, '--headless', '--path', str(project), '--import', '--quit'],
                                  env=env, capture_output=True, text=True, timeout=180)
        (output / 'import.log').write_text(imported.stdout + imported.stderr)
        if imported.returncode or ERROR_LOG.search(imported.stdout + imported.stderr):
            raise RuntimeError(f'P2 asset import failed: {output / "import.log"}')
        (scratch / 'model_fixture.py').write_text(MODEL_FIXTURE)
        backend = None
        process = None
        automation = None
        rss = []
        ime_process = dbus_process = None
        ime_log = None
        with reserve_listener() as listener, (output / 'backend.log').open('w') as backend_log:
            env['XIA_API_URL'] = f'http://127.0.0.1:{listener.getsockname()[1]}'
            env['PYTHONPATH'] = str(SERVER) + os.pathsep + str(scratch)
            def start_backend():
                owned = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'model_fixture:app', '--fd', str(listener.fileno()),
                                          '--log-level', 'warning', '--no-access-log'], cwd=scratch, env=env,
                                         pass_fds=(listener.fileno(),), stdout=backend_log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 30
                with httpx.Client(base_url=env['XIA_API_URL'], trust_env=False, timeout=2) as client:
                    while True:
                        if owned.poll() is not None:
                            raise RuntimeError('Disposable backend exited at startup')
                        try:
                            client.get('/api/game/state').raise_for_status()
                            return owned
                        except httpx.HTTPError:
                            if time.monotonic() > deadline:
                                stop_process(owned)
                                raise RuntimeError('Disposable backend readiness timeout')
                            time.sleep(.1)
            try:
                backend = start_backend()
                with httpx.Client(base_url=env['XIA_API_URL'], trust_env=False, timeout=5) as client:
                    client.post('/api/game/new', json={'name': '接入测试侠客'}).raise_for_status()
                stop_process(backend)
                backend = None
                fixture(scratch / 'xia.db', 'advanced')
                backend = start_backend()
                if args.ime:
                    from shutil import which
                    if not all(which(name) for name in ('fcitx5', 'fcitx5-remote', 'dbus-daemon')) or not Path('/usr/lib/fcitx5/librime.so').exists():
                        raise RuntimeError('专项IME测试需要现有fcitx5/Rime/dbus；不会自动安装')
                    config = Path(env['XDG_CONFIG_HOME']) / 'fcitx5'
                    config.mkdir(parents=True, exist_ok=True)
                    (config / 'profile').write_text('[Groups/0]\nName=Default\nDefault Layout=us\nDefaultIM=rime\n\n[Groups/0/Items/0]\nName=keyboard-us\nLayout=\n\n[Groups/0/Items/1]\nName=rime\nLayout=\n\n[GroupOrder]\n0=Default\n')
                    (config / 'conf').mkdir()
                    (config / 'conf/xim.conf').write_text('UseOnTheSpot=True\n')
                    rime = Path(env['XDG_DATA_HOME']) / 'fcitx5/rime'
                    rime.mkdir(parents=True, exist_ok=True)
                    (rime / 'default.custom.yaml').write_text('patch:\n  schema_list:\n    - schema: luna_pinyin_simp\n')
                    dbus_process = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1'], env=env, stdout=subprocess.PIPE, text=True)
                    if not select.select([dbus_process.stdout], [], [], 5)[0]:
                        raise RuntimeError('私有IME D-Bus启动超时')
                    env['DBUS_SESSION_BUS_ADDRESS'] = dbus_process.stdout.readline().strip()
                    env.update(XMODIFIERS='@im=fcitx', XIA_HD2D_IME='1')
                    ime_log = (output / 'ime.log').open('w')
                    ime_process = subprocess.Popen(['fcitx5', '--disable=wayland,notifications,clipboard'], env=env, stdout=ime_log, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 45
                    while True:
                        ready = subprocess.run(['fcitx5-remote'], env=env, capture_output=True, text=True, timeout=5)
                        if ready.returncode == 0 and ready.stdout.strip() in {'0', '1', '2'}:
                            break
                        if ime_process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('私有Rime未就绪；见ime.log')
                        time.sleep(.2)
                command = [godot, '--path', str(project), '--resolution', '1440x900', '--display-driver', 'x11',
                           '--audio-driver', 'Dummy', '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan',
                           '--script', 'res://tools/hd2d_game_probe.gd']
                with (output / 'engine.log').open('w') as engine_log:
                    process = subprocess.Popen(command, cwd=scratch, env=env, stdout=engine_log, stderr=subprocess.STDOUT)
                    if args.scene_review:
                        from hd2d_scene_interaction import SceneInteraction
                        automation_class = SceneInteraction
                    else:
                        automation_class = GameInteraction
                    automation = automation_class(process.pid, env, output, duration, isolation['display'])
                    started = time.monotonic()
                    while process.poll() is None:
                        if automation.error:
                            raise RuntimeError(f'P2 OS regression failed: {automation.error}')
                        status = Path(f'/proc/{process.pid}/status')
                        if status.exists():
                            match = re.search(r'VmRSS:\s+(\d+)', status.read_text())
                            if match:
                                rss.append({'seconds': time.monotonic() - started, 'rss_kib': int(match.group(1))})
                        if time.monotonic() - started > duration + 150:
                            raise RuntimeError('P2 GPU regression timeout')
                        time.sleep(.2)
                if process.returncode or not automation.completed or ERROR_LOG.search((output / 'engine.log').read_text() + (output / 'backend.log').read_text()):
                    raise RuntimeError(f'P2 engine/interaction failed; see {output / "engine.log"}')
                data = json.loads((output / 'metrics.json').read_text())
                expected = 'nvidia' if args.gpu_device == 'nvidia' else 'radeon'
                if expected not in data['device'].lower() or data['backend'] != 'forward_plus':
                    raise RuntimeError(f'P2 unexpected GPU/renderer: {data["device"]}/{data["backend"]}')
                intervals = sorted(float(line) for line in (output / 'frames.csv').read_text().splitlines())
                summary = {key: data[key] for key in ('device', 'backend', 'quality', 'duration', 'distance', 'rendered_frames', 'input_events', 'window', 'viewport', 'scope')}
                summary.update({f'p{p}_ms': intervals[min(len(intervals)-1, int(len(intervals)*p/100))] for p in (50, 95, 99)})
                summary.update(frames_over_50ms=sum(value > 50 for value in intervals), rendered_fps=data['rendered_frames']/data['duration'])
                if data['distance'] < 500 or data['input_events'] < 50 or summary['rendered_fps'] < 20:
                    raise RuntimeError('P2 insufficient sustained OS input or GPU draw callbacks')
                write_json(output / 'summary.json', summary)
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0
            finally:
                try:
                    if automation:
                        automation.close()
                finally:
                    try:
                        if process:
                            stop_process(process)
                    finally:
                        try:
                            if backend:
                                stop_process(backend)
                        finally:
                            try:
                                if ime_process:
                                    stop_process(ime_process)
                            finally:
                                if dbus_process:
                                    stop_process(dbus_process)
                                if ime_log:
                                    ime_log.close()
                                write_json(output / 'rss.json', rss)
