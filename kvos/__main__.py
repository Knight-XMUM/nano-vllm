"""kvos 命令行入口：python3 -m kvos <子命令>

子命令：
  selftest                    门禁全集（G1–G10），Mac 可跑
  convert --adapter A in out  原始源文件 → canonical JSONL（adapter 直通）
  stats <trace>               adapter 体检报告（K0-4 复核材料）
  freeze <trace>              K1-1：冻结 S(W) 拐点与 r*（写 <trace>.knee.json）
  run <trace> --caps …        网格跑分（须先 freeze；K1 解锁纪律照旧）
  judge <trace> --caps …      死刑判官：freeze+run+evaluate 一键判定表
"""

import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    if cmd == "selftest":
        from kvos.selftest import main as m
        return m()
    if cmd in ("freeze", "run"):
        from kvos.grid import main as m
        sys.argv = [sys.argv[0], cmd] + sys.argv[1:]
        return m()
    if cmd == "stats":
        import json
        from kvos.adapters.stats import stats_report
        from kvos.trace import load_events
        events, _ = load_events(sys.argv[1])
        print(json.dumps(stats_report(events), indent=2, sort_keys=True))
        return 0
    if cmd == "convert":
        from kvos.adapters import get
        from kvos.trace import dump_events
        src = dst = name = None
        args = sys.argv[1:]
        i = 0
        while i < len(args):
            if args[i] == "--adapter":
                name = args[i + 1]
                i += 2
            elif src is None:
                src = args[i]
                i += 1
            else:
                dst = args[i]
                i += 1
        if not (name and src and dst):
            print("用法: convert --adapter <源名> <in> <out.jsonl>")
            return 1
        events, _stats = get(name).parse_file(src)
        dump_events(events, dst)
        print("converted %d events -> %s" % (len(events), dst))
        return 0
    if cmd == "judge":
        import json
        from kvos import grid, judge
        from kvos.analysis import kneedle, sw_curve
        from kvos.trace import load_events
        path = sys.argv[1]
        caps = []
        if "--caps" in sys.argv:
            i = sys.argv.index("--caps")
            caps = [int(x) for x in sys.argv[i + 1:] if x.isdigit()]
        caps = caps or [64, 128, 256, 512, 1024]
        events, _io_stats = load_events(path)
        n_sess = len({e.session_id for e in events})
        curve = sw_curve(events)
        W, r, det = kneedle(curve)
        knee_rec = {"curve": [{"W": w, "W_set": ws, "S": s}
                              for w, ws, s in curve],
                    "W_knee": W, "r_star": r,
                    "no_knee": W is None, "detail": det}
        rows = grid.run_grid(events, caps)
        v = judge.evaluate(rows, knee_rec, n_sess)
        print(json.dumps(v, indent=2, ensure_ascii=False))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
