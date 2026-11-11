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
        self.check('camera_relative_wasd', after['position'][0] > before['position'][0] + 20 and after['position'][1] < before['position'][1] - 10)
        self.key('c')
        self.state(lambda s: s['camera_panel'], '视角面板没有打开')
        for key, fraction in (('fov', .8), ('pitch', .75), ('yaw', .25)):
            previous = self.sample()[key]
            slider = self.sample()['sliders'][key]
            self.click_at([slider['position'][0] + slider['size'][0] * fraction,
                           slider['position'][1] + slider['size'][1] * .5])
            self.state(lambda s: abs(s[key] - previous) > 1, f'{key}滑条点击无效')
        self.key('Home')
        self.state(lambda s: (s['pitch'], s['yaw'], s['fov']) == (38, 30, 35), 'Home复位失败')
        self.key('Escape')
        self.state(lambda s: not s['camera_panel'], 'Esc没有收起视角面板')
        self.drag_camera(100, 80)
        self.state(lambda s: abs(s['yaw'] - 30) > 10 and abs(s['pitch'] - 38) > 5, '中键拖动没有改变视角')
        self.capture('orbit_view')
        self.key('Home')
        self.check('camera_mouse_sliders_orbit_reset', True)
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
        self.state(lambda s: bool(s['fade_groups']), '屋后局部虚化未触发')
        self.capture('inn_local_reveal')
        self.key('F7')
        self.state(lambda s: not s['occlusion_enabled'] and not s['fade_groups'], '关闭虚化没有恢复')
        self.capture('inn_hard_occlusion')
        self.key('F7')
        self.check('local_reveal_and_restore', True)
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
        self.state(lambda s: s['zoom'] == 3 and s['pitch'] == 65 and s['yaw'] == 30,
                   '最外角3×/65°专项未就绪')
        for width, height in [(1440, 900), (1100, 720)]:
            self.xdo('windowsize', self.window, str(width), str(height))
            self.state(lambda s: s['window'] == s['viewport'] == [width, height] and s['hero_on_screen'],
                       '最外角头部被裁或原生视口未同步')
            self.move('d', .4)
            self.move('a', .4)
            state = self.capture(f'outer_corner_3x_65_{width}')
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
            if cycle % 4 == 0:
                self.drag_camera(70, 20)
                self.key('Home')
            cycle += 1
        self.key('F1')
        self.capture('after_sustained_interaction')
        self.completed = True

    def _entry(self) -> None:
        super()._entry()
        report = json.loads((self.output / 'interaction.json').read_text())
        report['scope'] = 'Scenic full Main/HUD + disposable real backend; NPC/mob review excluded; private AMD/X11 OS input'
        write_json(self.output / 'interaction.json', report)
