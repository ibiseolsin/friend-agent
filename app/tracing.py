"""대화 한 턴을 Langfuse 트레이스 하나로 남긴다.

LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 가 있을 때만 켜진다. 없으면 모든 함수가 아무것도 하지 않는다.
트레이스에는 대화 원문이 들어간다. 그래서 Cloud Logging 로그가 아니라 접근이 제한된 관측 도구에만 보낸다.

- version: 배포한 커밋(COMMIT_SHA). 고치기 전과 후의 트레이스를 이 값으로 나눠 비교한다.
- 한 턴 = 트레이스(chat-turn) 하나. 그 아래 모델 호출(도구 선택, 답, 기억 정리, 요약)과 도구 실행이 붙는다.
- 화면의 신고(별로예요) 버튼은 그 답의 트레이스에 점수 user-feedback=0 으로 남는다.

스트리밍 응답은 이벤트마다 다른 실행 맥락에서 이어지므로, "현재 span" 에 기대지 않고 부모를 직접 넘겨 만든다.
"""
import os
from datetime import datetime, timezone

from app.logs import log

_client = None
_attrs = {}


def setup(settings):
    """앱 시작 때 한 번. 키가 없으면 끈 채로 둔다."""
    global _client, _attrs
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not (public and secret):
        return
    from langfuse import Langfuse

    # 백그라운드 자동 전송(기본 5초마다)을 사실상 끄고, 턴 끝의 flush() 로만 보낸다.
    # Cloud Run 은 응답이 끝나면 CPU 를 거의 주지 않아서, 백그라운드 전송이 도중에 멈추면 그 묶음이 사라졌다
    # (2026-11-10 staging: 40턴 중 23턴 유실, 한 턴 걸러 하나씩. 이 설정 뒤 10/10 도착).
    _client = Langfuse(public_key=public, secret_key=secret,
                       base_url=os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com").strip(),
                       environment=settings.env.lower(), release=settings.commit,
                       flush_interval=float(os.environ.get("LANGFUSE_FLUSH_INTERVAL", "3600")),
                       flush_at=int(os.environ.get("LANGFUSE_FLUSH_AT", "512")))
    _attrs = {"version": settings.commit[:12], "environment": settings.env.lower()}
    log("INFO", "Langfuse 트레이스를 켭니다.", version=_attrs["version"])


def enabled():
    return _client is not None


class Turn:
    """트레이스 하나. 꺼져 있으면 모든 메서드가 그냥 넘어간다."""

    def __init__(self, uid=None, message=None):
        self.root = None
        self.trace_id = None
        self.uid = uid
        if _client is None:
            return
        from langfuse import propagate_attributes

        with propagate_attributes(user_id=uid, session_id=uid, trace_name="chat-turn", **_attrs):
            self.root = _client.start_observation(name="chat-turn", as_type="agent", input=message)
        self.trace_id = self.root.trace_id

    def child(self, name, as_type="span", **fields):
        if self.root is None:
            return None
        from langfuse import propagate_attributes

        with propagate_attributes(user_id=self.uid, session_id=self.uid, trace_name="chat-turn", **_attrs):
            return self.root.start_observation(name=name, as_type=as_type, **fields)

    def tool(self, name, args, ok, summary):
        obs = self.child(name, as_type="tool", input=args, output=summary, level=None if ok else "WARNING")
        if obs is not None:
            obs.end()

    def record(self, output, metadata):
        """답과 이번 턴의 판단(도구, 확신도, 행동)을 남겨 둔다. 보내는 것은 finish 때."""
        self.output, self.metadata = output, metadata

    def finish(self, error=None):
        if self.root is None:
            return
        fields = {"output": getattr(self, "output", None), "metadata": getattr(self, "metadata", None)}
        if error is not None:
            fields.update(level="ERROR", status_message=type(error).__name__)
        self.root.update(**{k: v for k, v in fields.items() if v is not None})
        self.root.end()
        # Cloud Run 은 응답이 끝나면 CPU 를 거의 주지 않아서, 응답을 닫기 전에 보낸다.
        _client.flush()


class TracedLLM:
    """한 턴 동안 모델 호출을 감싸 generation 으로 남긴다. stage 는 호출하는 쪽이 바꿔 둔다."""

    def __init__(self, llm, turn):
        self.llm = llm
        self.turn = turn
        self.stage = "llm"
        self.can_reason = getattr(llm, "can_reason", False)
        self.model = getattr(llm, "model", "")

    def _kwargs(self, usage):
        return {"usage": usage} if getattr(self.llm, "reports_usage", False) else {}

    def _start(self, input):
        return self.turn.child(self.stage, as_type="generation", model=self.model, input=input)

    @staticmethod
    def _end(gen, output, usage, error=None, first_token=None):
        if gen is None:
            return
        fields = {"output": output}
        if usage:
            fields["usage_details"] = usage
        if first_token:
            fields["completion_start_time"] = first_token
        if error is not None:
            fields.update(level="ERROR", status_message=type(error).__name__)
        gen.update(**fields)
        gen.end()

    def complete_json(self, system, user):
        usage = {}
        gen = self._start([{"role": "system", "content": system}, {"role": "user", "content": user}])
        try:
            result = self.llm.complete_json(system, user, **self._kwargs(usage))
        except Exception as error:
            self._end(gen, None, usage, error)
            raise
        self._end(gen, result, usage)
        return result

    def complete_text(self, messages):
        usage = {}
        gen = self._start(messages)
        try:
            text = self.llm.complete_text(messages, **self._kwargs(usage))
        except Exception as error:
            self._end(gen, None, usage, error)
            raise
        self._end(gen, text, usage)
        return text

    def chat_stream(self, messages):
        usage = {}
        gen = self._start(messages)
        text, first = "", None
        try:
            for chunk in self.llm.chat_stream(messages, **self._kwargs(usage)):
                if first is None:
                    first = datetime.now(timezone.utc)
                text += chunk
                yield chunk
        except Exception as error:
            self._end(gen, text, usage, error, first)
            raise
        self._end(gen, text, usage, first_token=first)


def feedback(trace_id, reason=""):
    """화면의 신고(별로예요). 그 답의 트레이스에 점수 0 을 남긴다."""
    if _client is None or not trace_id:
        return False
    _client.create_score(name="user-feedback", value=0, trace_id=trace_id, data_type="NUMERIC",
                         comment=(reason or "")[:500] or None)
    _client.flush()
    return True

