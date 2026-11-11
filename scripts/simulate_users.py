"""가상 사용자 여러 명이 배포된 서비스와 대화해 트레이스를 쌓는다(15번 프로젝트의 "모으기").

- 사용자마다 Supabase 익명 로그인을 새로 해서 서로 다른 user_id 로 대화한다.
- 메시지는 아래 PERSONAS 에 미리 적어 둔 것만 보낸다. 실제 개인정보는 쓰지 않는다.
- --judge 를 주면 답을 받을 때마다 사용자 시뮬레이터(LLM)가 "이 사용자라면 별로예요를 누를까"를 판단하고,
  누른다면 /api/report 로 보낸다. 서버는 그 답의 Langfuse 트레이스에 user-feedback=0 점수를 남긴다.
- 대화 원문이 담긴 결과는 .local/ 에만 저장한다(.gitignore). 저장소에 올리지 않는다.

사용법:
  python scripts/simulate_users.py --url https://friend-agent-staging-....run.app --judge
  python scripts/simulate_users.py --url ... --set check   # 배포 뒤 확인용 새 문장 묶음
필요한 .env 값: SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY (서비스가 쓰는 Supabase 프로젝트), --judge 면 LLM_API_KEY
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import load_dotenv  # noqa: E402

# 사용자 유형마다 한 세션. 도구가 필요한 말, 일상 대화, 기억, 정정, 모호한 말을 섞는다.
PERSONAS = {
    "collect": [
        ("출근하는 직장인", ["좋은 아침! 오늘 서울 날씨 어때?", "우산 챙겨야 할까?", "점심에 밖에서 먹어도 될 만큼 공기 괜찮아?",
                       "아 오늘 회의 너무 많아서 벌써 지친다", "내일 아침 8시에 보고서 제출하라고 알려 줘"]),
        ("부산 사는 대학생", ["나 부산 사는데 오늘 바다 보러 가도 될 날씨야?", "미세먼지는?", "다음 달에 쉬는 날 있어?",
                         "시험 기간이라 너무 스트레스 받아", "나 매운 거 진짜 좋아해. 떡볶이 최고"]),
        ("기억을 확인하는 사용자", ["나는 고양이 두 마리 키워. 이름은 콩이랑 팥이야", "요즘 저녁마다 산책하는 게 낙이야",
                           "내가 키우는 고양이 이름 기억나?", "내가 요즘 저녁에 뭐 한다고 했지?", "고마워 기억해 줘서"]),
        ("말을 바로잡는 사용자", ["대구 날씨 알려 줘", "아니 그게 아니라 대전 말이야", "주말에 비 와?",
                          "아니 토요일 말고 일요일", "알았어 고마워"]),
        ("약속을 관리하는 사용자", ["금요일 저녁 7시에 엄마한테 전화하라고 알려 줘", "내 약속 뭐 있어?",
                           "금요일 전화 약속 취소해 줘", "다음 주 월요일 오전 9시에 운동 가라고 알려 줘", "약속 목록 보여 줘"]),
        ("짧게 말하는 사용자", ["ㅎㅇ", "심심", "뭐해?", "ㅇㅋ", "날씨"]),
        ("고민 상담하는 사용자", ["요즘 잠을 잘 못 자", "누워도 생각이 많아져서 그래", "회사 일 때문에 그런 것 같아",
                          "너는 이럴 때 어떻게 하면 좋겠다고 생각해?", "얘기 들어줘서 좀 나아졌어"]),
        ("여행 계획하는 사용자", ["이번 주말에 인천 가려고 하는데 날씨 어때?", "거기 공기는 괜찮아?", "연휴가 언제 있더라?",
                          "여행 가기 전날 밤 10시에 짐 싸라고 알려 줘", "여행 가면 뭐 먹을지 추천해 줘"]),
    ],
    # 배포 뒤 확인용: 수집 묶음과 표현을 바꾼 새 문장들
    "check": [
        ("확인-직장인", ["안녕 오늘 수원 날씨는?", "비 올 것 같아?", "요즘 야근이 너무 많아", "모레 오후 3시에 치과 가라고 알려 줘"]),
        ("확인-기억", ["나 강아지 키워. 이름은 보리야", "주말마다 등산 다니는 게 취미야", "내 강아지 이름 뭐였지?", "내 취미 기억해?"]),
        ("확인-정정", ["광주 미세먼지 어때?", "아니 광주 말고 울산", "내일 날씨는?", "아니 모레"]),
        ("확인-짧은말", ["하이", "졸려", "배고파", "뭐 먹지"]),
        ("확인-약속", ["내일 아침 7시에 일어나라고 알려 줘", "알림 뭐 있어?", "아까 그거 취소", "고마워"]),
        ("확인-상담", ["요즘 친구랑 사이가 좀 멀어진 것 같아", "먼저 연락하기가 어색해", "어떻게 말을 꺼내면 좋을까?"]),
        ("확인-주말", ["이번 주말에 대구 놀러 가는데 날씨 괜찮을까?", "주말 내내 비 오는 거 아니지?", "다음 주말 부산 미세먼지는 어때?",
                    "토요일 밤 11시에 일찍 자라고 알려 줘", "일요일 오후 2시에 장 보라고 알려 줘"]),
        ("확인-요일", ["목요일에 우산 필요해?", "수요일 저녁에 헬스장 가라고 알려 줘", "아니 수요일 말고 금요일로 해 줘",
                    "금요일 서울 공기 어때?", "다음 주 화요일 아침 7시에 병원 예약 확인하라고 알려 줘"]),
    ],
}

JUDGE_PROMPT = """너는 AI 친구 앱을 쓰는 사용자 역할이다. 사용자 유형: {persona}
방금 보낸 메시지와 받은 답을 보고, 이 사용자라면 답 옆의 '별로예요' 버튼을 누를지 판단한다.
누르는 경우: 물은 것에 답하지 않음, 사실이 틀림, 앞에서 말한 내용을 잊음, 말을 바로잡았는데 반영 안 함,
너무 길거나 딱딱함, 묻지 않은 정보를 늘어놓음, 공감 없이 기계적임.
괜찮으면 누르지 않는다. 실제 사용자는 웬만하면 누르지 않는다는 점을 기억한다.
JSON 하나로만 답한다: {{"thumbs_down": true 또는 false, "reason": "누른다면 한 문장"}}"""


def anon_token(client, supabase_url, publishable):
    r = client.post(f"{supabase_url}/auth/v1/signup", headers={"apikey": publishable}, json={})
    r.raise_for_status()
    return r.json()["access_token"]


def send(client, url, token, message):
    started = time.time()
    reply, message_id, events = "", None, []
    with client.stream("POST", f"{url}/api/chat", headers={"Authorization": f"Bearer {token}"},
                       json={"message": message}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            e = json.loads(line)
            events.append(e["type"])
            if e["type"] == "delta":
                reply += e["text"]
            elif e["type"] == "done":
                message_id = e["message_id"]
            elif e["type"] == "error":
                reply = f"[error] {e['message']}"
    return reply, message_id, round(time.time() - started, 2)


def judge(llm_client, persona, message, reply):
    r = llm_client.chat.completions.create(
        model="gpt-4o-mini", response_format={"type": "json_object"},
        messages=[{"role": "system", "content": JUDGE_PROMPT.format(persona=persona)},
                  {"role": "user", "content": f"보낸 메시지: {message}\n받은 답: {reply}"}])
    return json.loads(r.choices[0].message.content)


def main():
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--set", default="collect", choices=list(PERSONAS))
    p.add_argument("--judge", action="store_true")
    p.add_argument("--repeat", type=int, default=1, help="묶음 전체를 몇 번 돌릴지(사용자는 매번 새로)")
    args = p.parse_args()
    url = args.url.rstrip("/")
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    publishable = os.environ["SUPABASE_PUBLISHABLE_KEY"]

    llm_client = None
    if args.judge:
        from openai import OpenAI
        llm_client = OpenAI(api_key=os.environ["LLM_API_KEY"])

    out_dir = ROOT / ".local"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"sim-{args.set}-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    version = httpx.get(f"{url}/version", timeout=30).json()
    print(f"서비스 버전: commit={version['commit'][:7]} revision={version['revision']}")

    turns = reports = 0
    with httpx.Client(timeout=120) as client, out.open("w", encoding="utf-8") as f:
        for round_no in range(args.repeat):
            for persona, messages in PERSONAS[args.set]:
                token = anon_token(client, supabase_url, publishable)
                for message in messages:
                    reply, message_id, seconds = send(client, url, token, message)
                    row = {"round": round_no, "persona": persona, "message": message, "reply": reply,
                           "message_id": message_id, "seconds": seconds, "commit": version["commit"][:7]}
                    if llm_client and message_id:
                        verdict = judge(llm_client, persona, message, reply)
                        row["thumbs_down"] = bool(verdict.get("thumbs_down"))
                        if row["thumbs_down"]:
                            row["reason"] = verdict.get("reason", "")
                            client.post(f"{url}/api/report", headers={"Authorization": f"Bearer {token}"},
                                        json={"message_id": message_id, "reason": row["reason"]}).raise_for_status()
                            reports += 1
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    turns += 1
                    print(f"[{turns}] {persona} | {seconds}s | {'별로예요' if row.get('thumbs_down') else '-'}")
    print(f"끝: 턴 {turns}개, 별로예요 {reports}개. 결과 파일 {out.relative_to(ROOT)} (저장소에 올리지 않음)")


if __name__ == "__main__":
    main()
