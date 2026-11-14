#!/usr/bin/env python3
"""Summarize R10 real-client samples without hiding long frames.

Full records remain the primary result. route_walk is an explicitly named subset,
not a replacement for full-session metrics. Godot TIME_PROCESS is a once-per-second
maximum, not a per-frame CPU measurement. GPU/submit counters cover the world
SubViewport, not the native HUD or physical presentation.
"""
from __future__ import annotations
import argparse
import bisect
import csv
import json
from pathlib import Path
import statistics


def stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {"count": len(values), "avg": statistics.fmean(values),
            **{f"p{p}": ordered[min(len(ordered)-1, int(len(ordered)*p/100))] for p in (50, 95, 99)},
            "max": ordered[-1], "over_16_667": sum(v > 1000/60 for v in values),
            "over_50": sum(v > 50 for v in values)}


def rss_trend(samples: list[dict]) -> dict:
    """Compare warm process-time windows, not the pre-exec RSS with game RSS."""
    if not samples:
        return {"comparable": False}
    end = samples[-1]["seconds"]
    first = [s["rss_kib"]/1024 for s in samples if 30 <= s["seconds"] < 60]
    last = [s["rss_kib"]/1024 for s in samples if end-30 <= s["seconds"] <= end]
    if end < 90 or not first or not last:
        return {"comparable": False, "process_seconds": end}
    before, after = statistics.median(first), statistics.median(last)
    return {"comparable": True, "first_window_seconds": [30, 60],
            "last_window_seconds": [end-30, end],
            "first_median_mib": before, "last_median_mib": after,
            "delta_mib": after-before}


def summarize(folder: Path) -> dict:
    summary = json.loads((folder / "summary.json").read_text())
    samples = [json.loads(line) for line in (folder / "samples.jsonl").read_text().splitlines()]
    frames = list(csv.DictReader((folder / "profile_frames.csv").open()))
    interaction = json.loads((folder / "interaction.json").read_text())
    rss = json.loads((folder / "rss.json").read_text())
    first = next(s for s in samples if s['seconds'] >= 5)
    last = samples[-1]
    a, b = first['profile'], last['profile']
    frame_count = b['main_frames'] - a['main_frames']
    view_count = b['view_frames'] - a['view_frames']
    oa, ob = a['occlusion'], b['occlusion']
    batches = ob['batches'] - oa['batches']
    stamps = [s['seconds'] for s in samples]
    walk_frames = [f for f in frames if samples[max(0, bisect.bisect_right(stamps, float(f['seconds']))-1)]['profile_stage'] == 'route_walk']
    elapsed = last['seconds'] - first['seconds']
    events_path = folder / "profile_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    event_names = sorted({event["name"] for event in events})
    result = {
        'directory': str(folder), 'summary': summary,
        'checks': {'passed': interaction['passed'], 'count': len(interaction['checks'])},
        'full_frame_ms': stats([float(f['interval_ms']) for f in frames]),
        'world_submit_ms': stats([float(f['submit_ms']) for f in frames]),
        'world_gpu_ms': stats([float(f['gpu_ms']) for f in frames]),
        'draw_calls': stats([float(f['draw_calls']) for f in frames]),
        'hitches_over_25ms': [dict(f, stage=samples[max(0, bisect.bisect_right(stamps, float(f['seconds']))-1)]['profile_stage'])
                             for f in frames if float(f['interval_ms']) > 25],
        'route_walk_frame_ms': stats([float(f['interval_ms']) for f in walk_frames]),
        'route_walk_gpu_ms': stats([float(f['gpu_ms']) for f in walk_frames]),
        'main_script_mean_usec': (b['main_usec']-a['main_usec']) / max(1, frame_count),
        'view_stage_mean_usec': {key: (value-a['view_usec'].get(key, 0))/max(1, view_count) for key, value in b['view_usec'].items()},
        'runtime_rays_per_second': (ob['rays']-oa['rays'])/elapsed,
        'runtime_query_mean_usec': (ob['batch_usec']-oa['batch_usec'])/max(1, batches),
        'runtime_query_cpu_ms_per_second': (ob['batch_usec']-oa['batch_usec'])/elapsed/1000,
        'runtime_queries_outside_physics': ob['outside_physics']-oa['outside_physics'],
        'diagnostic_rays': ob['diagnostic_rays'],
        'ui_event_ms_after_25s': {name: stats([event['usec']/1000 for event in events if event['name'] == name and event['seconds'] >= 25]) for name in event_names},
        'capture_count': b['capture_count'], 'capture_usec': b['capture_usec'],
        'snapshot_cpu_ms_per_second': (b['snapshot_usec']-a['snapshot_usec'])/elapsed/1000,
        'pipeline_after_5s': {key: last['pipeline_compilations'][key]-first['pipeline_compilations'][key] for key in last['pipeline_compilations']},
        'physics_separate_thread': last['physics_separate_thread'],
        'rss_mib': {'startup_first': rss[0]['rss_kib']/1024, 'last': rss[-1]['rss_kib']/1024,
                    'peak': max(s['rss_kib'] for s in rss)/1024},
        'rss_warm_trend': rss_trend(rss),
        'frame_120s_windows': [dict(start_seconds=start, end_seconds=min(start+120, float(frames[-1]['seconds'])),
                                  **stats([float(f['interval_ms']) for f in frames
                                           if start <= float(f['seconds']) < start+120]))
                               for start in range(0, int(float(frames[-1]['seconds']))+1, 120)],
        'rss_30s_buckets_mib': [statistics.fmean(s['rss_kib']/1024 for s in rss if start <= s['seconds'] < start+30)
                              for start in range(0, int(rss[-1]['seconds']), 30)
                              if any(start <= s['seconds'] < start+30 for s in rss)],
        'limits': ['editor/debug binary in isolated real GPU client, not release export or physical display',
                   'world GPU and submit timings exclude native HUD',
                   'after moving queries to physics, Main timing excludes that measured query cost; it is not eliminated',
                   'first 3s excluded by acquisition; no other full-frame samples removed',
                   'query/sign diagnostic counts distinct; standalone capture mode recorded explicitly',
                   'pipeline counters may be unavailable (all zero) on Compatibility',
                   'RSS uses process elapsed time; warm medians are not a proof of no memory leak'],
    }
    if summary['no_captures'] and (b['capture_count'] != 0 or list(folder.glob('manual-*.png'))):
        raise ValueError('no-captures run contains GPU readback evidence')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = [summarize(folder) for folder in args.directories]
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
    for r in results:
        print(json.dumps({key: r[key] for key in ('directory', 'checks', 'full_frame_ms', 'world_gpu_ms', 'main_script_mean_usec', 'view_stage_mean_usec', 'runtime_query_mean_usec', 'runtime_queries_outside_physics', 'rss_mib')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
