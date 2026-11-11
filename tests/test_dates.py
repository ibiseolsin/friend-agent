"""요일 이름·주말 표현을 코드로 날짜로 바꾸는 안전장치(when.mentioned_dates, router.fix_mentioned_dates)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.router import ToolChoice, fix_mentioned_dates
from app.when import mentioned_dates

TUE = datetime(2026, 11, 10, 9, tzinfo=ZoneInfo("Asia/Seoul"))  # 화요일
SUN = datetime(2026, 11, 15, 9, tzinfo=ZoneInfo("Asia/Seoul"))


def test_weekend_and_weekdays():
    assert mentioned_dates("이번 주말에 인천 날씨 어때?", TUE) == [date(2026, 11, 14), date(2026, 11, 15)]
    assert mentioned_dates("다음 주말에 캠핑", TUE) == [date(2026, 11, 21), date(2026, 11, 22)]
    assert mentioned_dates("이번 주말 날씨", SUN) == [date(2026, 11, 15)]
    assert mentioned_dates("토요일 아침 8시에 알려 줘", TUE) == [date(2026, 11, 14)]
    assert mentioned_dates("다음 주 월요일 9시", TUE) == [date(2026, 11, 16)]
    assert mentioned_dates("아니 토요일 말고 일요일", TUE) == [date(2026, 11, 15)]


def test_unclear_expressions_are_left_to_model():
    assert mentioned_dates("오늘 날씨", TUE) is None
    assert mentioned_dates("토요일이나 일요일", TUE) is None
    assert mentioned_dates("주말 전날 저녁에", TUE) is None


def test_fix_rebuilds_weekend_weather_and_keeps_reminder_time():
    choice = ToolChoice([{"name": "get_weather", "args": {"location": "인천", "date": "2026-11-13"}}], 0.9, "", "model")
    fix_mentioned_dates(choice, "이번 주말 인천 날씨", TUE)
    assert [t["args"]["date"] for t in choice.tools] == ["2026-11-14", "2026-11-15"]
    assert choice.guards

    choice = ToolChoice([{"name": "create_reminder", "args": {"text": "등산", "due_at": "2026-11-12T08:00:00+09:00"}}],
                        0.9, "", "model")
    fix_mentioned_dates(choice, "토요일 아침 8시에 등산 가라고 알려 줘", TUE)
    assert choice.tools[0]["args"]["due_at"] == "2026-11-14T08:00:00+09:00"
