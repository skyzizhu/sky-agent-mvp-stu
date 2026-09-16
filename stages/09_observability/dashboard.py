"""
Stage 9：观测面板生成器。
读 logs/runs.jsonl（+ 可选的 failure_report.json），生成静态 HTML 大盘。
用法: .venv/bin/python stages/09_observability/dashboard.py
产出: observatory/index.html（浏览器直接打开）
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # 让脚本能找到项目根（直接运行时的标准引导）

RUNS = ROOT / "logs" / "runs.jsonl"
FAIL = ROOT / "logs" / "failure_report.json"
OUT = ROOT / "observatory" / "index.html"


def main():
    from common import observability
    runs = observability.load_runs()  # 直接复用统一读取入口
    if not runs:
        print("logs/runs.jsonl 为空——先跑几次 agent。")
        return

    total = len(runs)
    ok = sum(1 for r in runs if r.get("stop_reason") == "model_done"
             and not r.get("errors"))
    total_tokens = sum(r.get("tokens", 0) for r in runs)
    max_tok = max(r.get("tokens", 0) for r in runs) or 1

    # 失败分布：优先用归类报告，否则按 stop_reason
    fail_dist = {}
    if FAIL.exists():
        fail_dist = json.loads(FAIL.read_text(encoding="utf-8")).get("dist", {})
    else:
        for r in runs:
            if r.get("stop_reason") != "model_done":
                fail_dist[r.get("stop_reason", "unknown")] = \
                    fail_dist.get(r.get("stop_reason", "unknown"), 0) + 1

    def bar(label, count, color):
        pct = count / total * 100
        return (f'<div class="row"><span class="lb">{label}</span>'
                f'<div class="bar"><div style="width:{pct:.0f}%;background:{color}"></div></div>'
                f'<span class="ct">{count}</span></div>')

    rows = "".join(
        f"<tr><td>{r['ts'][5:]}</td><td>{r.get('impl','')}</td>"
        f"<td class='q'>{r.get('question','')[:46]}</td>"
        f"<td>{r.get('steps','')}</td><td>{r.get('tokens',0):,}</td>"
        f"<td><span class='tag {'ok' if r.get('stop_reason')=='model_done' else 'warn'}'>"
        f"{r.get('stop_reason','')}</span></td>"
        f"<td>{len(r.get('errors') or [])}</td>"
        f"<td>{len((r.get('extra') or {}).get('new_preferences', []) if isinstance((r.get('extra') or {}).get('new_preferences', []), list) else [r.get('extra',{}).get('new_preferences',0)])}</td></tr>"
        for r in reversed(runs))

    dist_html = "".join(bar(k, v, "#c93c37" if k not in
                            ("无失败", "审批拒绝(HITL)") else "#188554")
                        for k, v in sorted(fail_dist.items(), key=lambda x: -x[1])) \
                or '<div class="row"><span class="lb">暂无失败记录</span></div>'

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Agent Observatory · 运行观测面板</title>
<style>
  :root {{ --line:#e3e6ec; --dim:#69718a; --acc:#2563eb; }}
  body {{ margin:0; background:#f6f7f9; color:#1c2333;
         font:14px/1.6 -apple-system,"PingFang SC",sans-serif; }}
  header {{ padding:14px 22px; background:#fff; border-bottom:1px solid var(--line);
            display:flex; align-items:baseline; gap:12px; }}
  h1 {{ font-size:17px; margin:0; }}
  header span {{ color:var(--dim); font-size:12px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; padding:16px 22px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px; }}
  .card .n {{ font-size:26px; font-weight:700; }}
  .card .t {{ font-size:12px; color:var(--dim); }}
  section {{ background:#fff; border:1px solid var(--line); border-radius:10px;
             margin:0 22px 16px; padding:14px; }}
  h2 {{ font-size:13px; color:var(--dim); margin:0 0 10px; text-transform:uppercase; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:7px 9px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--dim); font-size:12px; }}
  .q {{ color:#333; }}
  .tag {{ padding:2px 8px; border-radius:99px; font-size:11px; }}
  .tag.ok {{ background:#e6f4ea; color:#188554; }}
  .tag.warn {{ background:#fff3e0; color:#9a6700; }}
  .row {{ display:flex; align-items:center; gap:10px; margin:6px 0; }}
  .lb {{ width:170px; font-size:12px; color:#333c52; }}
  .bar {{ flex:1; background:#f2f4f8; border-radius:6px; height:16px; overflow:hidden; }}
  .bar div {{ height:100%; }}
  .ct {{ width:30px; text-align:right; font-size:12px; color:var(--dim); }}
</style></head><body>
<header><h1>🔭 Agent Observatory</h1>
<span>数据源: logs/runs.jsonl · 刷新方式: 重新运行 dashboard.py</span>
<span style="margin-left:auto">生成于 {time.strftime('%Y-%m-%d %H:%M')}</span></header>
<div class="cards">
  <div class="card"><div class="n">{total}</div><div class="t">总运行次数</div></div>
  <div class="card"><div class="n">{ok/total*100:.0f}%</div><div class="t">正常收尾率</div></div>
  <div class="card"><div class="n">{total_tokens:,}</div><div class="t">累计 token</div></div>
  <div class="card"><div class="n">{total_tokens//total:,}</div><div class="t">平均 token/次</div></div>
</div>
<section><h2>失败 / 异常分布</h2>{dist_html}</section>
<section><h2>运行历史（最新在前）</h2>
<table><tr><th>时间</th><th>实现</th><th>问题</th><th>步数</th><th>tokens</th>
<th>停止原因</th><th>错误</th><th>新偏好</th></tr>{rows}</table></section>
</body></html>"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"📊 面板已生成: {OUT}\n   浏览器打开即可查看（{total} 次运行）")


if __name__ == "__main__":
    main()
