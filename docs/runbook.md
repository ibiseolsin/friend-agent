# 운영 절차 (runbook)

문제가 생겼을 때 누가 봐도 같은 순서로 따라 할 수 있게 적는다. 명령은 실제로 실행해 본 것만 적고, 실행한 날짜를 남긴다.

## 이전 버전으로 되돌리기

| 순서 | 할 일 | 명령 또는 화면 | 확인할 것 |
| --- | --- | --- | --- |
| 1 | 지금 요청을 받는 리비전 이름 적기 | `gcloud run services describe <서비스> --region us-central1 --format="value(status.traffic[0].revisionName,status.traffic[0].percent)"` | 이름과 100 |
| 2 | 되돌아갈 리비전 고르기 | `gcloud run revisions list --service <서비스> --region us-central1 --limit 5` | 직전 리비전, 상태 True |
| 3 | 요청 100%를 되돌아갈 리비전으로 옮기기 | `gcloud run services update-traffic <서비스> --region us-central1 --to-revisions <되돌아갈 리비전>=100` | 명령 성공. 오래 쉬던 리비전은 인스턴스가 0개라 새로 시작하는 동안(약 10초) 요청이 대기한다 |
| 4 | 되돌린 뒤 확인 | `curl <서비스 주소>/version` | `/version` 의 리비전과 커밋 |

- 마지막으로 실행해 본 날: 2026-11-10 (friend-agent-staging, 00007-bak → 00006-h5l)
- 걸린 시간: 명령 8초, 첫 응답까지 약 15초. `watch_requests.py` 요청 84건 중 4건 ReadTimeout(2초 초과) — 되돌아간 리비전이 인스턴스 0개에서 새로 시작(15:43:02 시작 → 15:43:12 `/health` 성공)
- 줄이려면: 되돌리기 전에 그 리비전에 태그를 붙여 한 번 요청해 깨우거나, 배포 시간대에는 `--min-instances 1` 로 둔다

## 새 버전을 요청 0%로 올려 확인한 뒤 전환하기 (블루그린)

| 순서 | 할 일 | 명령 또는 화면 | 확인할 것 |
| --- | --- | --- | --- |
| 1 | 지금 리비전(blue) 이름 적기 | 되돌리기 1번과 같은 명령 | blue 이름 |
| 2 | 새 리비전(green)을 요청 0%로 올리고 태그 붙이기 | `gcloud run services update <서비스> --region us-central1 --update-env-vars BANNER=green-test --no-traffic --tag green` (이미지 배포는 `gcloud run deploy ... --image <이미지> --no-traffic --tag green`) | "serving 0 percent of traffic", 태그 주소 출력 |
| 3 | 태그 주소로 green 만 확인 | `curl https://green---<서비스 주소의 호스트>/version` | 태그 주소는 green 리비전·banner, 원래 주소는 계속 blue |
| 4 | 요청 100%를 green 으로 전환 | `gcloud run services update-traffic <서비스> --region us-central1 --to-tags green=100` | `/version` 이 green. 문제가 있으면 되돌리기 3번(blue=100) |

- 마지막으로 실행해 본 날: 2026-11-10 (friend-agent-staging, 00006-h5l → 00007-bak)
- 걸린 시간: green 0% 배포 20초, 전환 명령 6초. `watch_requests.py` 요청 133건 중 실패 0건(green 은 0% 배포 때 이미 시작 확인을 마친 상태)

## 배포 중 끊김 측정 기록

| 날짜 | 측정한 배포 | 전체 요청 | 실패 | 실패 시각과 원인 |
| --- | --- | --- | --- | --- |
| 2026-11-10 | 노트북 Docker: v1 컨테이너 삭제 직후 v2 실행(그냥 바꿔치기) | 214 | 4 | 14:51:11.7~13.4(약 1.6초). 옛 컨테이너가 없고 새 컨테이너가 아직 포트를 열지 않은 사이 연결 끊김(RemoteProtocolError) |
| 2026-11-10 | 노트북 Docker: v2 삭제 직후 v3 실행, `SLOW_START_SECONDS=20` | 215 | 100 | 14:52:08~14:52:29(약 21초). 97건이 503 — 포트는 열렸지만 준비 전. 포트 열림 ≠ 요청 받을 준비 |
| 2026-11-10 | 노트북 Docker: nginx 프록시 뒤 blue 유지, green(`SLOW_START_SECONDS=20`)을 옆에 띄워 `/health` 200 확인 후 `nginx -s reload` 로 전환, 3초 뒤 blue 삭제 | 243 | 0 | 없음. 준비 22초 동안 blue 가 계속 응답(blue 130 / green 113) |
| 2026-11-10 | staging 블루그린: green(0%, 태그) → `--to-tags green=100` | 133 | 0 | 없음 |
| 2026-11-10 | staging 되돌리기: `--to-revisions 00006-h5l=100` | 84 | 4 | 15:43:03~15:43:10 ReadTimeout. 되돌아간 리비전이 인스턴스 0개에서 새로 시작하는 약 10초 동안 요청 대기 |
