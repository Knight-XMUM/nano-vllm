"""kvos 命令行入口：python3 -m kvos <子命令>

子命令：
  selftest              九条门禁（G1–G9），Mac 可跑
  freeze <trace>        K1-1：冻结 S(W) 拐点与 r*（写 <trace>.knee.json）
  run <trace> --caps …  网格跑分（须先 freeze；K1 解锁纪律照旧）
  stats <trace>         adapter 体检报告（K0-4 复核材料）
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
    elif cmd in ("freeze", "run"):
        from kvos.grid import main as m
        sys.argv = [sys.argv[0], cmd] + sys.argv[1:]
    elif cmd == "stats":
        import json

        from kvos.adapters.stats import stats_report
        from kvos.trace import load_events
        return lambda: print(json.dumps(stats_report(load_events(sys.argv[1])[0]),
                                        indent=2, sort_keys=True))()
    else:
        print(__doc__)
        return 1
    return m()


if __name__ == "__main__":
    raise SystemExit(main())
