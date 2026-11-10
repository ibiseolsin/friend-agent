"""Langfuse 에서 한 환경의 대화 트레이스를 내려받아 .local/ 에 저장하고, 버전별 숫자를 요약한다.

- 저장 파일(.local/traces-<환경>.jsonl)에는 대화 원문이 들어가므로 저장소에 올리지 않는다(.gitignore).
- 화면에는 개수, 지연 시간, 비용, 별로예요 수만 출력한다.

사용법: python scripts/export_traces.py --env staging [--since 2026-11-10T00:00:00]
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import load_dotenv  # noqa: E402


def as_dict(o):
    return o if isinstance(o, dict) else o.dict()


def main():
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="staging")
    p.add_argument("--since", default=None)
    args = p.parse_args()
    from langfuse import Langfuse

    lf = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                  base_url=os.environ["LANGFUSE_HOST"], tracing_enabled=False)
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None

    observations = []
    for page in lf.iter_observation_pages(environment=args.env, from_start_time=since, limit=100,
                                          fields="core,basic,time,io,metadata,model,usage,metrics",
                                          expand_metadata="metadata"):
        observations += [as_dict(o) for o in page.data]

    scores = defaultdict(list)
    cursor = None
    while True:
        r = lf.api.scores.get_many(name="user-feedback", environment=args.env, limit=100, cursor=cursor,
                                   from_timestamp=since, fields="details,subject")
        for s in r.data:
            s = as_dict(s)
            subject = s.get("subject") or {}
            scores[subject.get("traceId") or subject.get("id")].append(s.get("comment"))
        cursor = getattr(r.meta, "cursor", None) if getattr(r, "meta", None) else None
        if not cursor or not r.data:
            break

    traces = defaultdict(lambda: {"steps": []})
    for o in observations:
        t = traces[o["traceId"]]
        if o.get("name") == "chat-turn":
            t.update(trace_id=o["traceId"], version=o.get("version"), start=str(o["startTime"]),
                     user=o.get("userId"), input=o.get("input"), output=o.get("output"),
                     metadata=o.get("metadata"), latency=o.get("latency"), level=str(o.get("level")))
        else:
            t["steps"].append({"name": o.get("name"), "type": str(o.get("type")), "cost": o.get("totalCost") or 0,
                               "latency": o.get("latency"), "ttft": o.get("timeToFirstToken"),
                               "input": o.get("input") if o.get("name") in ("tool-choice",) else None,
                               "output": o.get("output") if str(o.get("type")).upper().endswith("TOOL")
                               or o.get("name") == "tool-choice" else None})
    rows = []
    for tid, t in traces.items():
        if "version" not in t:
            continue
        t["cost"] = round(sum(s["cost"] for s in t["steps"]), 6)
        t["feedback"] = scores.get(tid, [])
        rows.append(t)
    rows.sort(key=lambda t: t["start"])

    out = ROOT / ".local" / f"traces-{args.env}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for t in rows:
            f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")

    by_version = defaultdict(list)
    for t in rows:
        by_version[t["version"]].append(t)
    print(f"환경 {args.env}: 트레이스 {len(rows)}개, 별로예요 {sum(len(t['feedback']) for t in rows)}개 → {out.relative_to(ROOT)}")
    for v, ts in by_version.items():
        lat = [t["latency"] for t in ts if t["latency"]]
        print(f"  version {v}: 트레이스 {len(ts)}, 별로예요 {sum(1 for t in ts if t['feedback'])}, "
              f"지연 중앙값 {statistics.median(lat):.1f}s, p90 {sorted(lat)[int(len(lat) * 0.9) - 1 if len(lat) > 1 else 0]:.1f}s, "
              f"턴당 비용 ${statistics.mean(t['cost'] for t in ts):.5f}")


if __name__ == "__main__":
    main()
