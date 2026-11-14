#!/usr/bin/env python3
"""Isolated P0/P1 runner. Does not contact a server, load real saves or instrument
an editor. --background runs OS-input regression on a private headless display;
the default entry remains a manually operated window on the current desktop.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from smoke_test import PROJECT, stage_project, stop_process
from hd2d_validation import CONTRACT_END, ERROR_LOG, claim_output, probe_errors, run_errors


def stage(destination: Path) -> None:
    stage_project(destination)
    for path in (PROJECT/'tools').glob('hd2d_*.gd'):
        shutil.copy2(path, destination/'tools'/path.name)
    # Reconstruct policy even on fresh checkouts where ignored .import sidecars
    # do not exist. Do not copy cache references or engine-generated UIDs.
    from build_hd2d_assets import write_import_policy
    for path in (destination/'assets/hd2d').rglob('*.png'):
        write_import_policy(path, path.relative_to(destination/'assets/hd2d').as_posix())
    fixture = PROJECT/'tools/hd2d_contract.json'
    if fixture.exists():
        shutil.copy2(fixture, destination/'tools'/fixture.name)


def renderer_options(args, env: dict, project: Path) -> tuple[list[str], str]:
    """Validate the real entry without silently overriding its renderer.

    P1 keeps its historical Forward+ default; --game defaults to project.
    """
    selected = args.renderer or ('project' if args.game else 'forward_plus')
    if selected == 'project':
        match = re.search(r'^renderer/rendering_method="([^"]+)"$',
                          (project / 'project.godot').read_text(), re.MULTILINE)
        if not match:
            raise RuntimeError('Cannot determine project renderer; do not guess')
        expected, options = match.group(1), []
    else:
        expected = selected
        options = ['--rendering-method', selected, '--rendering-driver',
                   'opengl3' if selected == 'gl_compatibility' else 'vulkan']
    # OpenGL ignores VK_DRIVER_FILES: clear inherited PRIME overrides too.
    for key in ('DRI_PRIME', '__NV_PRIME_RENDER_OFFLOAD', '__GLX_VENDOR_LIBRARY_NAME'):
        env.pop(key, None)
    if expected == 'gl_compatibility':
        if args.gpu_device == 'nvidia':
            env.update(__NV_PRIME_RENDER_OFFLOAD='1', __GLX_VENDOR_LIBRARY_NAME='nvidia')
        else:
            env.update(__GLX_VENDOR_LIBRARY_NAME='mesa')
            for device in sorted(Path('/sys/class/drm').glob('card[0-9]/device')):
                if (device / 'vendor').read_text().strip() == '0x1002':
                    env['DRI_PRIME'] = 'pci-' + device.resolve().name.replace(':', '_').replace('.', '_')
                    break
    return options, expected


def float_owned(pid: int) -> dict | None:
    if not shutil.which('mmsg'):
        return None
    try:
        result = subprocess.run(['mmsg', 'get', 'all-clients'],capture_output=True,text=True,timeout=2)
        clients = json.loads(result.stdout).get('clients', [])
        for c in clients:
            if c['pid'] != pid:
                continue
            if not c['is_floating']:
                subprocess.run(['mmsg','dispatch','togglefloating',f"client,{c['id']}"],capture_output=True,timeout=2)
            # Mango dimensions include a 1px border. Resize the owned surface
            # through the compositor, not Window.size (which can desynchronize
            # the Wayland buffer and compositor geometry, or stall XWayland).
            subprocess.run(['mmsg','dispatch','resizewin,1442,902',f"client,{c['id']}"],capture_output=True,timeout=2)
            monitor = json.loads(subprocess.check_output(['mmsg','get','monitor',c['monitor']],text=True,timeout=2))
            x = monitor['x'] + (monitor['width']-1442)//2
            y = monitor['y'] + (monitor['height']-902)//2
            subprocess.run(['mmsg','dispatch',f'movewin,{x},{y}',f"client,{c['id']}"],capture_output=True,timeout=2)
            return json.loads(subprocess.check_output(['mmsg','get','client',str(c['id'])],text=True,timeout=2))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def run(argv: list[str] | None = None, *, private_display: dict | None = None,
        automation_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu-device', choices=['nvidia','amd'], default='nvidia')
    parser.add_argument('--quality', choices=['standard','light'], default='standard')
    parser.add_argument('--renderer', choices=['project', 'gl_compatibility', 'forward_plus'],
                        help='game defaults to project (no override); P1 defaults to forward_plus')
    parser.add_argument('--pitch', type=float, default=38, help='P1 elevation from horizontal, 30..65 degrees')
    parser.add_argument('--yaw', type=float, default=30, help='P1 orbit angle, -180..180 degrees')
    parser.add_argument('--fov', type=float, default=35, help='vertical perspective FOV, 30..40 degrees')
    parser.add_argument('--projection', choices=['perspective', 'orthogonal'], default='perspective',
                        help='P1 projection; orthogonal is retained for same-scene visual comparison')
    parser.add_argument('--display-driver', choices=['wayland','x11'], default='x11')
    parser.add_argument('--duration', type=float, default=0, help='0: interactive until window closes')
    parser.add_argument('--ime', action='store_true', help='opt-in real Rime composition/Esc check for --background --game; private input method only')
    parser.add_argument('--scene-review', action='store_true', help='scene/HUD regression only; use --game, hides NPCs and mobs in disposable harness')
    parser.add_argument('--npc-matrix', action='store_true', help='7 NPC x work/commute/gather x near/far OS input; use --game')
    parser.add_argument('--game', action='store_true', help='P2 real-client regression with a disposable backend; use --background')
    parser.add_argument('--profile', action='store_true', help='real-client repeatable noon route and opt-in subsystem counters; not P5')
    parser.add_argument('--max-fps', type=int, default=60, help='--game frame cap; 0 uncaps engine for diagnosis, default 60')
    parser.add_argument('--no-captures', action='store_true', help='--game --profile: state assertions only, no GPU screenshot readbacks')
    parser.add_argument('--baseline', action='store_true', help='original 2D world under the same P1 diagnostic HUD')
    parser.add_argument('--no-vsync', action='store_true', help='diagnose presentation stalls; caps at 120 FPS and records this separately')
    parser.add_argument('--probe', action='store_true', help='GPU hard geometry + actor-only outline assertions')
    parser.add_argument('--actor-probe', action='store_true', help='GPU eight-way gait gallery and volumetric actor-shadow assertions')
    parser.add_argument('--environment-probe', action='store_true', help='P3 full-map inventory, building occlusion and environment GPU gallery')
    parser.add_argument('--resource-probe', action='store_true', help='R11 two-view resource isolation and native GPU grass checks')
    parser.add_argument('--logic', action='store_true', help='headless frozen 2D contract + projection test')
    parser.add_argument('--background', action='store_true', help='private gamescope/headless GPU + OS-input regression; never touches the foreground desktop')
    parser.add_argument('--compositor-gpu', choices=['amd','nvidia'], default='amd', help='background display compositor GPU (independent of the Godot GPU; default AMD)')
    parser.add_argument('--output', type=Path, help='new or empty result directory; defaults to a fresh /tmp directory')
    args = parser.parse_args(argv)
    if args.max_fps < 0 or args.max_fps > 1000:
        parser.error('--max-fps must be 0..1000 (0 = uncapped)')
    if (args.profile or args.max_fps != 60 or args.no_captures) and not args.game:
        parser.error('--profile/--max-fps/--no-captures require --game')
    if args.profile and (args.scene_review or args.npc_matrix or args.ime):
        parser.error('--profile uses its own repeatable route; run scene/NPC/IME matrices separately')
    if args.no_captures and not args.profile:
        parser.error('--no-captures requires --profile; normal GPU evidence remains mandatory')
    if not math.isfinite(args.pitch) or not 30 <= args.pitch <= 65:
        parser.error('--pitch must be between 30 and 65')
    if not math.isfinite(args.yaw) or not -180 <= args.yaw <= 180:
        parser.error('--yaw must be between -180 and 180')
    if not math.isfinite(args.fov) or not 30 <= args.fov <= 40:
        parser.error('--fov must be between 30 and 40')
    if args.npc_matrix and (not args.game or args.scene_review or args.ime):
        parser.error('--npc-matrix requires --game and excludes --scene-review/--ime')
    if args.scene_review and not args.game:
        parser.error('--scene-review requires --game')
    if args.scene_review and args.ime:
        parser.error('--scene-review excludes dialogue; run --game --ime separately')
    if args.ime and not args.game:
        parser.error('--ime requires --game')
    gpu_probe = args.probe or args.actor_probe or args.environment_probe or args.resource_probe
    if args.probe and args.yaw != 30:
        parser.error('--probe runs its own orbit matrix; omit --yaw')
    if args.duration < 0:
        parser.error('--duration cannot be negative')
    if sum((args.baseline, args.probe, args.actor_probe, args.environment_probe, args.resource_probe, args.logic, args.game)) > 1:
        parser.error('--baseline, GPU probes, --logic and --game are mutually exclusive')
    if args.background:
        if private_display is not None or args.logic or args.baseline:
            parser.error('--background is for the P1 GPU fixture/probe; --logic and --baseline retain their existing entries')
        if args.display_driver != 'x11':
            parser.error('--background currently uses a private X11 display, not Wayland/IME')
        if not gpu_probe and 0 < args.duration < 60:
            parser.error('--background interaction needs --duration >= 60 (default: 90 seconds)')
        from hd2d_background import run_background
        return run_background(args)
    if args.game:
        if private_display is None:
            parser.error('--game requires --background (private GPU display + disposable backend)')
        from hd2d_game_smoke import run_game
        return run_game(args, private_display)
    output = (args.output or Path(tempfile.mkdtemp(prefix='xia-hd2d-'))).resolve()
    if private_display is None:
        claim_output(output)
    # The private worker inherits the directory already claimed by its parent.
    print(f'OUTPUT: {output}', flush=True)
    godot = os.environ.get('GODOT_BIN','/usr/bin/godot')
    with tempfile.TemporaryDirectory(prefix='xia-hd2d-') as scratch:
        project = Path(scratch)/'project'
        stage(project)
        env = dict(os.environ, XIA_HD2D_OUTPUT=str(output), XIA_HD2D_DURATION=str(args.duration),
                   XIA_HD2D_BASELINE=str(int(args.baseline)), XIA_HD2D_QUALITY=args.quality,
                   XIA_HD2D_PITCH=str(args.pitch), XIA_HD2D_YAW=str(args.yaw),
                   XIA_HD2D_FOV=str(args.fov), XIA_HD2D_PROJECTION=args.projection,
                   XIA_HD2D_NO_VSYNC=str(int(args.no_vsync)),
                   XDG_DATA_HOME=str(Path(scratch)/'data'), XDG_CONFIG_HOME=str(Path(scratch)/'config'),
                   XDG_CACHE_HOME=str(Path(scratch)/'cache'), XDG_STATE_HOME=str(Path(scratch)/'state'))
        if private_display is not None:
            if env.get('DISPLAY') != private_display['display']:
                raise RuntimeError('Godot private DISPLAY mismatch; refusing to launch')
            env.update(XIA_HD2D_MAX_FPS='60', XIA_HD2D_CAPTURE_INTERVAL='10',
                       ENABLE_GAMESCOPE_WSI='0')
            env.pop('WAYLAND_DISPLAY', None)
        imported = subprocess.run([godot,'--headless','--path',str(project),'--import','--quit'],
                                  env=env,capture_output=True,text=True,timeout=180)
        (output/'import.log').write_text(imported.stdout+imported.stderr)
        if run_errors(imported.returncode, imported.stdout+imported.stderr):
            print((output/'import.log').read_text())
            return 1
        base = [godot,'--path',str(project),'--resolution','1440x900']
        if private_display is not None:
            base += ['--audio-driver', 'Dummy']
        if not args.logic:
            base += ['--display-driver',args.display_driver]
        if args.logic:
            command = base+['--headless','--script','res://tools/hd2d_contract.gd']
        else:
            # Restrict Vulkan enumeration, then independently validate the device
            # reported by the active RenderingServer. Never infer success from env.
            icd = '/usr/share/vulkan/icd.d/'+('nvidia_icd.json' if args.gpu_device=='nvidia' else 'radeon_icd.json')
            if not Path(icd).is_file():
                raise RuntimeError(f'Missing GPU ICD: {icd}')
            env['VK_DRIVER_FILES'] = icd
            if args.baseline:
                args.renderer = 'gl_compatibility'
            renderer_flags, expected_renderer = renderer_options(args, env, project)
            command = base + renderer_flags
            if gpu_probe:
                script = ('hd2d_resource_probe.gd' if args.resource_probe else
                          'hd2d_environment_probe.gd' if args.environment_probe else
                          'hd2d_actor_probe.gd' if args.actor_probe else 'hd2d_probe.gd')
                command += ['--script', 'res://tools/' + script]
            else:
                command += ['res://scenes/hd2d/prototype.tscn']
        print('RUN:', ' '.join(command),flush=True)
        rss = []
        with (output/'engine.log').open('w') as log:
            process = subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
            print(f'Owned Godot PID: {process.pid}',flush=True)
            floated = False
            automation = None
            started = time.monotonic()
            try:
                if automation_factory is not None:
                    automation = automation_factory(process.pid, env, output, args.duration)
                while process.poll() is None:
                    elapsed = time.monotonic()-started
                    if automation is not None and automation.error is not None:
                        raise RuntimeError(f'后台交互失败：{automation.error}')
                    if not floated and not args.logic and private_display is None:
                        client = float_owned(process.pid)
                        if client:
                            floated = True
                            (output/'window.json').write_text(json.dumps(client,indent=2))
                    status = Path(f'/proc/{process.pid}/status')
                    if status.exists():
                        match = re.search(r'VmRSS:\s+(\d+)',status.read_text())
                        if match:
                            rss.append({'seconds':elapsed,'rss_kib':int(match.group(1))})
                    logtext = (output/'engine.log').read_text()
                    if ERROR_LOG.search(logtext):
                        raise RuntimeError(f'Godot script/shader failed: {output / "engine.log"}')
                    if elapsed > (360 if args.environment_probe else 180 if gpu_probe or args.logic else args.duration+180) and (args.duration>0 or gpu_probe or args.logic):
                        raise RuntimeError('Godot run timed out')
                    time.sleep(0.25)
            finally:
                try:
                    if automation is not None:
                        automation.close()
                finally:
                    stop_process(process)
                    (output/'rss.json').write_text(json.dumps(rss))
        if automation is not None and (automation.error is not None or not automation.completed):
            raise RuntimeError(f'后台交互未完成：{automation.error}')
        logtext = (output/'engine.log').read_text()
        print(logtext)
        if run_errors(process.returncode, logtext, CONTRACT_END if args.logic else None):
            return 1
        if args.logic:
            return 0
        metrics = output/('probe.json' if gpu_probe else 'metrics.json')
        if not metrics.exists():
            raise RuntimeError('Missing GPU report (closing/crashing before completion is not a pass)')
        data = json.loads(metrics.read_text())
        if gpu_probe and (errors := probe_errors(data)):
            raise RuntimeError('; '.join(errors))
        expected = 'nvidia' if args.gpu_device=='nvidia' else 'amd'
        device = data['device'].lower()
        if expected not in device and not (expected=='amd' and 'radeon' in device):
            raise RuntimeError(f'Wrong GPU: requested {expected}, got {device}')
        if data['backend'] != expected_renderer:
            raise RuntimeError(f'Unexpected renderer fallback: {data["backend"]}')
        if not gpu_probe:
            # Monotonic frame intervals are streamed after a 3-second warmup.
            # Godot smooths _process(delta), so it cannot measure real jitter.
            frame_ms = sorted(float(line) for line in (output/'frames.csv').read_text().splitlines())
            summary = {k:data[k] for k in ['device','backend','quality','pitch','yaw','projection','fov','window','viewport','duration','distance','input_events','vsync','rendered_frames','occlusion_mode','scope']}
            summary['display_driver'] = args.display_driver
            summary['display_mode'] = 'private_headless' if private_display is not None else 'desktop'
            summary['frame_measurement'] = 'frame_post_draw callbacks; not physical display presentation'
            if private_display is not None:
                summary['max_fps'] = 60
                summary['scope'] = 'P1 private-display GPU/OS-input regression; NOT Mango, IME or P5 acceptance'
                if automation is not None and (data['distance'] < 500 or data['input_events'] < 50):
                    raise RuntimeError('后台交互量不足，不能用静置绘制当回归通过')
            summary['rendered_fps'] = data['rendered_frames']/max(data['duration'],0.001)
            if summary['rendered_fps'] < 20:
                raise RuntimeError(f'Viewport drawing stalled: {summary["rendered_fps"]:.2f} callbacks/s; process FPS is NOT GPU FPS')
            if data['window'] != [1440,900]:
                raise RuntimeError(f'Unexpected native window size: {data["window"]}')
            if frame_ms:
                summary.update({f'p{p}_ms':frame_ms[min(len(frame_ms)-1,int(len(frame_ms)*p/100))] for p in [50,95,99]})
                summary['frames_over_50ms'] = sum(t>50 for t in frame_ms)
            (output/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
            print(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0

if __name__ == '__main__':
    raise SystemExit(run())
