"""Scene-only OS interaction in the disposable real client; excludes NPCs/mobs."""
from __future__ import annotations

import json
import subprocess
from hd2d_game_smoke import GameInteraction
from hd2d_background import write_json


class SceneInteraction(GameInteraction):
    def _run(self) -> None:
        self.state(lambda s: s['seconds'] > 3.5 and s['online'] and s['gold'] >= 0,
                   '场景客户端未连到隔离后端', timeout=60)

        def owned():
            try:
                for window in self.xdo('search', '--onlyvisible', '--pid', self.pid).splitlines():
                    if int(self.xdo('getwindowpid', window)) == self.pid:
                        return window
            except subprocess.CalledProcessError:
                pass
            return None

        self.window = self.wait(owned, '未找到自有AMD窗口')
        self.check('private_owned_window', True, {'pid': self.pid, 'display': self.env['DISPLAY']})
        self.key('F1')
        self.sleep(.7)
        initial = self.capture('scenic_hud')
        self.check('full_native_world_no_outer_frame', initial['window'] == initial['viewport'] == [1440, 900])
        self.check('scene_review_excludes_npcs', not initial['npcs'] and not initial['population'])
        self.key('F4')
        before = self.state(lambda s: s['position'] == [832, 624], '诊断落点尚未同步')
        self.move('d', .5)
        after = self.state(lambda s: s['position'] != before['position'], '真实WASD没有移动')
        self.check('screen_axis_wasd', after['position'][0] > before['position'][0] + 20 and abs(after['position'][1] - before['position'][1]) < 2)
        fixed = self.sample()
        self.key('c')
        self.key('Home')
        self.drag_camera(100, 80)
        self.sleep(.3)
        after = self.sample()
        self.check('fixed_camera_ignores_orbit_inputs', (after['pitch'], after['yaw'], after['fov']) == (30, 0, 35)
                   and (fixed['pitch'], fixed['yaw']) == (30, 0) and not after['modal'], {k: after[k] for k in ('pitch', 'yaw', 'fov')})
        for zoom, clicks in ((3, ['4']), (1, ['5', '5']), (2, ['4'])):
            self.focus()
            self.xdo('mousemove', '--window', self.window, 720, 600)
            for button in clicks:
                self.xdo('click', button)
                self.sleep(.2)
            state = self.state(lambda s: s['zoom'] == zoom, f'滚轮{zoom}倍失败')
            self.check(f'foreground_clip_keeps_hero_{zoom}x', 0 <= state['foreground_clip'] < state['camera_depth'] - 8)
            self.capture(f'world_{zoom}x')
        self.key('F2')
        self.state(lambda s: s['outlines'].get('Hero') == 1, '屋后人物描边未触发')
        self.capture('inn_hidden_outline')
        self.key('F7')
        self.state(lambda s: not s['occlusion_enabled'] and not s['outlines'], '关闭人物描边没有恢复')
        self.capture('inn_hard_occlusion')
        self.key('F7')
        self.check('hidden_outline_and_restore', True)
        self.key('F4')
        self.click_button('行囊 B')
        locked = self.state(lambda s: s['modal'] == 'inventory', '行囊未打开')
        self.move('d', .5)
        self.drag_camera(80, 60)
        still = self.sample()
        self.check('modal_blocks_move_and_drag', still['position'] == locked['position'] and still['yaw'] == locked['yaw'] and still['pitch'] == locked['pitch'])
        self.key('Escape')
        self.click_button('舆图 M')
        self.state(lambda s: s['modal'] == 'map', '新版舆图没有打开')
        self.capture('expanded_atlas')
        self.key('Escape')
        self.xdo('windowsize', self.window, '1100', '720')
        self.state(lambda s: s['window'] == s['viewport'] == [1100, 720], '窗口resize没有同步原生视口')
        self.capture('hud_1100x720')
        self.key('i')
        self.state(lambda s: s['modal'] == 'character', '角色面板未打开')
        self.xdo('mousemove', '--window', self.window, 720, 560)
        self.xdo('click', '--repeat', '5', '--delay', '120', '5')
        self.state(lambda s: s['scroll'] > 0, '实际鼠标长面板滚动失败')
        self.check('native_resize_and_panel_scroll', True)
        self.key('Escape')
        self.xdo('windowsize', self.window, '1440', '900')
        self.state(lambda s: s['window'] == s['viewport'] == [1440, 900], '窗口复原没有同步视口')
        self.key('F3')
        self.key('e')
        shop = self.state(lambda s: s['modal'] == 'shop' and not s['busy'], '新药铺门口不能进入商店')
        self.click_button('购入 · 15')
        self.state(lambda s: not s['busy'] and s['gold'] == shop['gold'] - 15, '购买后HUD银两未同步')
        self.check('relocated_shop_real_server_settlement', True)
        self.key('Escape')
        for key, title in [('F8', 'waterside'), ('F10', 'north_terrace'), ('F11', 'hill_path')]:
            self.key(key)
            self.move('d', .7)
            state = self.capture(title)
            self.check(f'{title}_grounded', abs(state['hero_height'] - state['ground_height']) < .04)
        # Real OS input around all seven facades. Geometry checks complement,
        # but do not replace, native GPU screenshots at 1x/2x/3x and dusk/night.
        titles = [('jianyang', '剑阳道馆'), ('xuantie', '玄铁堂'), ('yanyu', '烟雨小筑'),
                  ('clinic', '药铺'), ('smith', '铁匠铺'), ('inn', '客栈'), ('temple', '破庙')]
        for id, title in titles:
            self.key('2')
            self.sleep(.25)
            state = self.capture(f'sign_{id}_2x')
            sign = state['signs'][id]
            self.check(f'sign_{id}_front_clear', sign['text'] == title and sign['clear_samples'] == 9
                       and sign['on_screen'] and sign['depth_test'] and sign['billboard'] == 0
                       and sign['cutout'] and not sign['overlay_overlap'], sign)
            self.key('4')
            self.state(lambda s: all(not entry['visible'] for entry in s['signs'].values()),
                       '功能剪影对照没有隐藏原生文字')
            hidden = self.capture(f'sign_{id}_no_text_2x')
            self.check(f'sign_{id}_text_only_toggle_same_frame',
                       not hidden['signs'][id]['visible'] and hidden['position'] == state['position']
                       and hidden['zoom'] == state['zoom'] and hidden['pitch'] == state['pitch']
                       and hidden['yaw'] == state['yaw'] and hidden['signs'][id]['size'] == sign['size'])
            self.key('4')
            self.state(lambda s: all(entry['visible'] for entry in s['signs'].values()),
                       '功能剪影对照后文字未恢复')
        for zoom, buttons in ((1, ['5']), (3, ['4', '4']), (2, ['5'])):
            self.focus()
            for button in buttons:
                self.xdo('click', button)
                self.sleep(.2)
            self.state(lambda s: s['zoom'] == zoom, '牌匾缩放未同步')
            state = self.capture(f'sign_temple_{zoom}x')
            sign = state['signs']['temple']
            self.check(f'sign_scale_{zoom}x', sign['clear_samples'] == 9 and sign['on_screen']
                       and sign['size'][1] >= 13, sign)
        self.key('3')
        self.state(lambda s: s['position'] == [680, 624], 'north passage start missing')
        self.capture('north_passage_start')
        for _ in range(5):
            self.move('w', .4)
        passage = self.capture('north_passage_end')
        self.check('north_passage_continuous_walk_and_direct_visibility',
                   passage['position'][1] < 340 and abs(passage['position'][0] - 680) < 1
                   and not passage['outlines'].get('Hero', 0), passage['position'])
        self.key('F1')
        self.key('l')
        self.sleep(.5)
        self.capture('town_dusk')
        self.key('l')
        self.sleep(.5)
        self.capture('town_night')
        self.key('l')
        self.check('day_dusk_night_rendered', True)
        self.key('Home')
        for key, title in [('0', 'south_gate'), ('6', 'northwest_wall'), ('7', 'northeast_wall'),
                           ('8', 'southwest_wall'), ('9', 'southeast_wall')]:
            self.key(key)
            self.move('d', .5)
            self.move('a', .5)
            state = self.capture(title)
            self.check(f'{title}_camera_frame', state['camera_ground_enclosed'] and state['hero_on_screen'] and not state['fog_enabled'])
        self.key('1')
        self.state(lambda s: s['zoom'] == 3, '最外角3×专项未就绪')
        for width, height in [(1440, 900), (1100, 720)]:
            self.xdo('windowsize', self.window, str(width), str(height))
            self.state(lambda s: s['window'] == s['viewport'] == [width, height] and s['hero_on_screen'],
                       '最外角头部被裁或原生视口未同步')
            self.move('d', .4)
            self.move('a', .4)
            state = self.capture(f'outer_corner_3x_{width}')
            self.check(f'outer_corner_full_body_{width}', state['hero_on_screen'] and state['camera_ground_enclosed'])
        self.xdo('windowsize', self.window, '1440', '900')
        self.state(lambda s: s['window'] == s['viewport'] == [1440, 900], '专项后窗口未恢复')
        self.key('Home')
        self.focus()
        self.xdo('click', '5')
        self.state(lambda s: s['zoom'] == 2, '专项后缩放未恢复')
        # Keep using OS movement across districts AND the enclosing walls.
        cycle = 0
        while self.sample()['seconds'] < self.duration - 7:
            key = ('F4', 'F8', 'F10', 'F11', 'F1', '0', '6', '7', '8', '9')[cycle % 10]
            self.key(key)
            self.move('d', 1.0)
            self.move('a', 1.0)
            self.move('s', .6)
            self.move('w', .6)
            cycle += 1
        self.key('F1')
        self.capture('after_sustained_interaction')
        self.completed = True

    def _entry(self) -> None:
        super()._entry()
        report = json.loads((self.output / 'interaction.json').read_text())
        report['scope'] = 'Scenic full Main/HUD + disposable real backend; NPC/mob review excluded; private AMD/X11 OS input'
        write_json(self.output / 'interaction.json', report)
