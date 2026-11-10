"""날짜 해석 평가. data/eval/date_resolution.jsonl 의 메시지마다 도구 선택기(router.model_choose)가
정답과 같은 도구와 같은 날짜(묶음)를 고르는지 센다. 문제 항목(problem)과 정상 항목(normal)을 따로 집계한다.

- 날씨·미세먼지: 고른 date 들의 집합이 정답 집합과 같아야 통과(주말은 토·일 두 날).
- 약속: due_at 이 정답 시각(분 단위)과 같아야 통과.
- 공휴일: from 의 연-월이 정답과 같아야 통과.
- 도구 없음: 아무 도구도 고르지 않아야 통과.
모델 답은 매번 달라서 항목마다 --repeat 번(기본 3) 돌린다. 호출 수 = 항목 수 x repeat (비용 발생).

사용법: python scripts/eval_dates.py [--repeat 3] [--out .local/eval-<이름>.json]
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.router import model_choose  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
LOCATION = "서울"


def judge(expect, tools):
    """(통과 여부, 실제로 고른 것 요약)"""
    got = defaultdict(list)
    for t in tools:
        a = t["args"]
        if t["name"] in ("get_weather", "get_air_quality"):
            got[t["name"]].append(str(a.get("date", ""))[:10])
        elif t["name"] == "create_reminder":
            got[t["name"]].append(str(a.get("due_at", ""))[:16])
        elif t["name"] == "get_holidays":
            got[t["name"]].append(str(a.get("from", ""))[:7])
        else:
            got[t["name"]].append("")
    summary = {k: sorted(v) for k, v in got.items()}
    if set(got) != set(expect):
        return False, summary
    for name, want in expect.items():
        if isinstance(want, list):
            if set(got[name]) != set(want):
                return False, summary
        elif want not in got[name]:
            return False, summary
    return True, summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    llm = make_llm(load_settings())
    if not llm.can_reason:
        sys.exit("LLM_PROVIDER=fake 로는 평가할 수 없습니다.")

    rows = [json.loads(l) for l in (ROOT / "data/eval/date_resolution.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    passed = defaultdict(int)
    total = defaultdict(int)
    results = []
    for row in rows:
        now = datetime.fromisoformat(row["now"]).replace(tzinfo=KST)
        ok_count = 0
        seen = []
        for _ in range(args.repeat):
            choice = model_choose(row["message"], row["history"], now, LOCATION, llm)
            ok, summary = judge(row["expect"], choice.tools)
            ok_count += ok
            seen.append(summary)
        passed[row["kind"]] += ok_count
        total[row["kind"]] += args.repeat
        results.append({"id": row["id"], "kind": row["kind"], "passed": ok_count, "repeat": args.repeat,
                        "expect": row["expect"], "seen": seen})
        mark = "통과" if ok_count == args.repeat else f"{ok_count}/{args.repeat}"
        print(f"{row['id']} {mark:5} {row['message'][:28]:28} 정답 {row['expect']}  결과 {seen if ok_count < args.repeat else ''}")

    print()
    for kind in ("problem", "normal"):
        print(f"{kind}: {passed[kind]}/{total[kind]} ({passed[kind] / total[kind]:.0%})")
    if args.out:
        Path(args.out).parent.mkdir(exist_ok=True)
        Path(args.out).write_text(json.dumps({"model": llm.model, "passed": passed, "total": total, "results": results},
                                             ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
