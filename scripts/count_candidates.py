"""내려받은 트레이스(.local/traces-<환경>.jsonl)에서 문제 후보마다 해당 트레이스를 센다. 숫자와 트레이스 ID 앞 8자리만 출력한다.

후보와 세는 규칙:
  A 날짜 해석 오류   메시지에 요일 이름·주말이 있는데 도구 날짜가 코드 계산(when.mentioned_dates)과 다르거나, 약속이 '이미 지난 시각'으로 실패
  B 결과 있는데 모른다  날씨·미세먼지 조회나 약속 저장이 성공했는데 답이 '모른다/알려 줄 수 없다/못 한다/기능이 아니다' 라고 함
  C 묻지 않은 도구     날씨·공기 단어가 없는 메시지에서 날씨·미세먼지 도구를 부름, 하루를 물었는데 이틀을 조회, 부탁하지 않았는데 약속을 저장
  D 별로예요          user-feedback 점수가 달린 트레이스
사용법: python scripts/count_candidates.py --env staging [--version 95a3422]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.when import mentioned_dates  # noqa: E402

WEATHER_WORDS = re.compile(r"날씨|비 |비와|비 와|우산|더워|추워|기온|미세먼지|공기|마스크|황사|눈 와|맑")
UNKNOWN = re.compile(r"모르|알 수 없|알려줄 수 없|알려 줄 수 없|못 해|못해|기능이 아니|정보는 없|정보가 없|기억에 없")


def tool_args(t):
    for s in t["steps"]:
        if s["name"] == "tool-choice" and s["output"]:
            o = s["output"]
            o = json.loads(o) if isinstance(o, str) else o
            return o.get("tools") or []
    return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="staging")
    p.add_argument("--version", default=None)
    args = p.parse_args()
    rows = [json.loads(l) for l in (ROOT / ".local" / f"traces-{args.env}.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.version:
        rows = [t for t in rows if str(t["version"]).startswith(args.version)]
    hits = defaultdict(list)
    for t in rows:
        msg = str(t["input"] or "")
        reply = str(t["output"] or "")
        start = datetime.fromisoformat(t["start"].replace(" ", "T")).astimezone(ZoneInfo("Asia/Seoul"))
        tools = tool_args(t)
        tool_outputs = [(s["name"], str(s["output"] or "")) for s in t["steps"] if "TOOL" in s["type"].upper()]
        dated = sorted(str(x["args"].get("date", ""))[:10] for x in tools if "date" in (x.get("args") or {}))
        want = mentioned_dates(msg, start)
        if (want and dated and dated != [d.isoformat() for d in want][:2]) or any("이미 지난 시각" in o for _, o in tool_outputs):
            hits["A"].append(t)
        ok_tool = any((n == "get_weather" and "날씨:" in o) or (n == "get_air_quality" and "미세먼지 " in o)
                      or (n == "create_reminder" and "저장했습니다" in o) for n, o in tool_outputs)
        if ok_tool and UNKNOWN.search(reply):
            hits["B"].append(t)
        weather_tools = [x for x in tools if x.get("name") in ("get_weather", "get_air_quality")]
        if weather_tools and not WEATHER_WORDS.search(msg + " "):
            if not re.search(r"거기|그날|그때|일요일|토요일|주말", msg):  # 앞 대화를 잇는 질문은 뺀다
                hits["C"].append(t)
        elif len({x["args"].get("date") for x in weather_tools}) > 1 and not want and "주말" not in msg:
            hits["C"].append(t)
        elif any(x.get("name") == "create_reminder" for x in tools) and not re.search(r"알려|리마인드|잊지 않게|깨워", msg):
            hits["C"].append(t)
        if t["feedback"]:
            hits["D"].append(t)
    print(f"트레이스 {len(rows)}개" + (f" (version {args.version})" if args.version else ""))
    for key, name in (("A", "날짜 해석 오류"), ("B", "결과 있는데 모른다"), ("C", "묻지 않은 도구"), ("D", "별로예요")):
        ids = [t["trace_id"][:8] for t in hits[key]]
        fb = sum(1 for t in hits[key] if t["feedback"])
        print(f"{key} {name}: {len(ids)}건 (그중 별로예요 {fb}) {' '.join(ids)}")


if __name__ == "__main__":
    main()
