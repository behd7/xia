"""OS-input NPC matrix on private display and a disposable synchronized clock."""
import json
import subprocess
from hd2d_background import write_json
from hd2d_game_smoke import GameInteraction


class NpcMatrixInteraction(GameInteraction):
    def _run(self):
        self.state(lambda s: s['seconds'] > 3.5 and s['online'] and len(s['npcs']) == 7,
                   'NPC matrix client not ready', timeout=60)
        def owned():
            try:
                return next((w for w in self.xdo('search', '--onlyvisible', '--pid', self.pid).splitlines()
                             if int(self.xdo('getwindowpid', w)) == self.pid), None)
            except subprocess.CalledProcessError:
                return None
        self.window = self.wait(owned, 'NPC matrix owned window missing')
        self.check('private_owned_window', True)
        revision = 0
        slugs = list(self.sample()['npcs'])
        for phase, seconds in [('work', 36000), ('commute', 39900), ('gather', 43200)]:
            for slug in slugs:
                revision += 1
                (self.output / 'clock.override').write_text(str(seconds))
                write_json(self.output / 'matrix.request', dict(revision=revision, seconds=seconds, slug=slug, near=True))
                self.state(lambda s: s['matrix_revision'] == revision and s['target'] == 'npc/' + slug,
                           f'{phase}/{slug} near seat not selected')
                self.key('e')
                before = self.state(lambda s: s['modal'] == 'dialogue' and s['dialogue_slug'] == slug,
                                    f'{phase}/{slug} E dialogue failed')
                self.move('d', .15)
                self.click_button('打招呼')
                after = self.state(lambda s: not s['busy'] and '山路清静，慢行即可。' in s['text'],
                                   f'{phase}/{slug} backend SSE settlement missing')
                self.check(f'{phase}_{slug}_near', before['position'] == after['position']
                           and before['npcs'][slug] == after['npcs'][slug])
                self.capture(f'{phase}_{slug}')
                self.key('Escape')
                self.state(lambda s: not s['modal'], 'dialogue close failed')
                revision += 1
                write_json(self.output / 'matrix.request', dict(revision=revision, seconds=seconds+60, slug=slug, near=False))
                self.state(lambda s: s['matrix_revision'] == revision and s['position'] == [8, 8], 'far seat failed')
                self.key('e')
                self.sleep(.1)
                after = self.sample()
                self.check(f'{phase}_{slug}_far', not after['modal'] and not after['dialogue_slug'] and not after['busy'])
        self.key('F4')
        while self.sample()['seconds'] < self.duration - 7:
            self.move('d', .8)
            self.move('a', .8)
            self.move('s', .4)
            self.move('w', .4)
        self.completed = True
