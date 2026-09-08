# 트레이딩 에이전트 — 서버 이관 가이드 (Oracle Cloud, Docker 방식)

> 최종 수정: 2026-09-08 — SSH 접속 및 서버 실측 기준으로 재작성
> 소스코드: https://github.com/BongRyoungKim/trading-agent (Private) — **2026-09-08부로
> `aphenix-debug/trading-agent`에서 이전됨** (기존 저장소는 삭제하지 않고 그대로 유지)
> **운영 환경이 로컬 Windows PC → Oracle Cloud(OCI) VM으로 이전되었다.** 이 문서는 그 이후 기준이다.
> API 키, `.env`, SSH 개인키는 이 문서에 포함하지 않으며 별도로 안전하게 전달한다.

---

## 0. 현재 운영 환경 요약 (실측, 2026-09-08 SSH 접속 확인)

| 항목 | 값 |
|---|---|
| 클라우드 | Oracle Cloud Infrastructure (OCI), **Always Free** 티어 `VM.Standard.E2.1.Micro` |
| OS | Ubuntu |
| 공인 IP | `129.225.163.29` |
| 접속 | `ssh -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29` |
| 실행 방식 | **Docker Compose** (`~/trading-agent/docker-compose.yml`, 컨테이너 2개: `trading-agent`, `breakout-scanner`) |
| 거래 모드 | `.trading_mode` 파일 (확인 시점 `live`) |
| 전략 | `RegimeAdaptiveStrategy` (`entrypoint.sh`에 고정) |
| 대시보드 | `http://129.225.163.29:8081/` (외부 접속 확인됨, 200 OK) |
| 헬스체크 | `http://129.225.163.29:8080/health` (외부 접속 확인됨, 200 OK) |
| GitHub | `https://github.com/BongRyoungKim/trading-agent.git`, branch `master` (2026-09-08부로 `aphenix-debug/trading-agent`에서 이전) |

> ⚠️ **중요**: `docker-compose.yml`과 `entrypoint.sh`는 서버에서 **git 미반영(uncommitted) 상태**로
> 확인되었다 (`git status` 기준 `docker-compose.yml`은 수정됨, `entrypoint.sh`는 추적되지 않는 파일).
> 즉 이 두 파일은 **GitHub에는 없고 이 서버에만 존재하는 운영 설정**이다. 서버가 사라지면
> `git clone`만으로는 복구되지 않으므로, STEP 1에서 반드시 별도로 백업해야 한다.

> ⚠️ Windows용 `start.ps1` / `start_agent_watchdog.ps1` / `guardian.ps1` / `*.bat` 스크립트는
> 저장소에 그대로 남아있지만 **더 이상 운영에 사용하지 않는다** (로컬 개발/백테스트 참고용 레거시).
> 실제 운영 기동/재기동/모드 전환은 전부 Docker Compose + `entrypoint.sh` 조합으로 이루어진다.

---

## 전체 흐름 (기존 서버 → 새 서버 이관 시)

```
[기존 OCI VM]                              [새 OCI VM]
──────────────────────                     ──────────────────────────────
1. GitHub에 코드 push (커밋된 변경사항)
2. docker compose stop 후 DB + 설정 백업
   (journal.db, positions.db, .env,
    .trading_mode, .strategy_params.json,
    docker-compose.yml, entrypoint.sh ← git 미반영 파일 포함!)
3. 컨테이너 완전 정지                →     4. 새 VM 생성, SSH 키 발급, 보안목록(Security List) 포트 오픈
                                           5. setup_trading_server.sh 실행 (Docker/스왑/ufw/clone)
                                           6. 백업 파일 복원 (.env 포함 docker-compose.yml/entrypoint.sh)
                                           7. docker compose up -d 로 기동 및 검증
```

---

## STEP 1 — 기존 서버: 코드 Push + 설정 백업

### 1-1. SSH 접속

```bash
ssh -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29
```

> SSH 개인키(`ssh-key-2026-08-25.key`)는 로컬 저장소 루트에 있다. **git에 커밋되지 않도록
> `.gitignore`에 `ssh-key-2026-08-25.key*` 항목이 있는지 반드시 확인한다** (확인 필요 —
> 이 문서 작성 시점 기준 저장소 `.gitignore`에는 아직 없음, `git status`에도 `??`로 추적되지
> 않는 상태이지만 실수로 `git add .` 하면 그대로 커밋될 수 있다).

### 1-2. GitHub에 반영된 변경사항 Push (서버에서 직접 수정한 코드가 있다면)

```bash
cd ~/trading-agent
git add .
git commit -m "feat: 이관 전 최종 상태"
git push origin master
```

완료 확인:

```bash
git log --oneline -3
git status --short   # docker-compose.yml, entrypoint.sh 등이 남아있는지 확인
```

### 1-3. git으로 관리되지 않는 운영 파일 백업 (필수)

로컬(작업 PC)에서 SCP로 통째로 내려받는다:

```bash
mkdir -p ~/trading_backup_$(date +%Y%m%d)
scp -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29:~/trading-agent/.env                   ~/trading_backup_$(date +%Y%m%d)/
scp -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29:~/trading-agent/.trading_mode           ~/trading_backup_$(date +%Y%m%d)/
scp -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29:~/trading-agent/.strategy_params.json   ~/trading_backup_$(date +%Y%m%d)/
scp -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29:~/trading-agent/docker-compose.yml      ~/trading_backup_$(date +%Y%m%d)/
scp -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29:~/trading-agent/entrypoint.sh           ~/trading_backup_$(date +%Y%m%d)/
scp -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29:~/trading-agent/data/journal.db         ~/trading_backup_$(date +%Y%m%d)/
scp -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29:~/trading-agent/data/positions.db       ~/trading_backup_$(date +%Y%m%d)/
```

> `docker-compose.yml`, `entrypoint.sh`, `.trading_mode`, `.strategy_params.json`은 **GitHub에
> 없는 파일**이므로 이 단계를 건너뛰면 새 서버에서 `git clone`만으로는 절대 복원되지 않는다.
> `.env`는 원래도 gitignore 대상이라 항상 별도 백업이 필요했다.

---

## STEP 2 — 기존 서버: 컨테이너 정지

> ⚠️ 새 서버에서 기동하기 **직전**에 실행한다. 두 서버가 동시에 같은 API 키로 실거래하면
> 중복 주문이 발생한다.

```bash
cd ~/trading-agent
docker compose down
docker ps -a   # trading-agent, breakout-scanner 컨테이너가 사라졌는지 확인
```

---

## STEP 3 — 새 OCI VM 준비

### 3-1. 인스턴스 생성 (OCI 콘솔)

- Shape: `VM.Standard.E2.1.Micro` (Always Free 대상, 메모리 1GB — STEP 4에서 스왑 2GB로 보완)
- 이미지: Ubuntu (최신 LTS)
- SSH 키 페어 새로 생성 또는 기존 공개키(`ssh-key-2026-08-25.key.pub`) 등록
- **Security List(가상 클라우드 네트워크 방화벽)에서 인바운드 규칙 추가**:
  - `22/tcp` (SSH)
  - `8080/tcp` (헬스체크)
  - `8081/tcp` (대시보드) — 기존 서버는 나중에 수동으로 추가한 것으로 보이며,
    `setup_trading_server.sh`에는 8080만 명시되어 있으니 **8081을 빠뜨리지 않도록 주의**한다.

### 3-2. SSH 접속 확인

```bash
ssh -i <새_개인키_파일> ubuntu@<새_서버_공인IP>
```

---

## STEP 4 — 새 서버: `setup_trading_server.sh` 실행

저장소에 포함된 `setup_trading_server.sh`(`~/setup_trading_server.sh`, 기존 서버에 실측 확인된
스크립트)를 새 서버로 복사해 실행한다.

```bash
# 로컬에서: 스크립트를 새 서버로 전송
scp -i <새_개인키_파일> setup_trading_server.sh ubuntu@<새_서버_공인IP>:~/

# 새 서버에서
chmod +x setup_trading_server.sh
./setup_trading_server.sh
```

스크립트가 자동으로 처리하는 것:
1. `apt-get update && upgrade`
2. Docker CE + docker-compose-plugin 설치 (공식 저장소 등록 후 설치, `docker` 그룹에 사용자 추가)
3. 스왑 2GB 생성 (`/swapfile`, `vm.swappiness=10`) — 1GB 메모리 인스턴스 보완용
4. `ufw` 방화벽에서 `22/tcp`, `8080/tcp` 허용 (**8081/tcp는 스크립트에 없으므로 STEP 5에서 직접 추가**)
5. `git clone https://github.com/BongRyoungKim/trading-agent.git ~/trading-agent` (이미 있으면 `git pull`)
   > ⚠️ `setup_trading_server.sh`의 `REPO_URL` 변수는 아직 옛 저장소(`aphenix-debug/trading-agent`)를
   > 가리키고 있을 수 있다 — 새 서버에서 실행하기 전에 새 저장소 주소로 직접 수정할 것.
6. `.env` 파일이 없으면 빈 파일(또는 `.env.example`) 생성 — **API 키 입력은 스크립트가 하지 않으며
   본인이 직접 `nano .env`로 입력해야 한다.**

> Docker 그룹 적용을 위해 스크립트 실행 후 한 번 `exit` 했다가 다시 SSH 접속할 것.

### 4-1. `ufw`에 대시보드 포트(8081) 추가

```bash
sudo ufw allow 8081/tcp
sudo ufw status verbose
# 22, 8080, 8081 모두 ALLOW 상태여야 한다
```

---

## STEP 5 — 새 서버: 백업 파일 복원

### 5-1. `.env` 작성

```bash
cd ~/trading-agent
nano .env
```

`.env` 예시 (실제 값은 STEP 1에서 백업한 파일 참고, API 키는 절대 채팅/문서에 남기지 않는다):

```env
EXCHANGE=upbit
TRADING_MODE=live
UPBIT_ACCESS_KEY=여기에_업비트_액세스키_입력
UPBIT_SECRET_KEY=여기에_업비트_시크릿키_입력

TELEGRAM_BOT_TOKEN=여기에_텔레그램_봇_토큰_입력
TELEGRAM_CHAT_ID=여기에_텔레그램_채팅_ID_입력

MAX_POSITION_RISK=0.05
MAX_DAILY_LOSS=0.99
MAX_OPEN_POSITIONS=1
MAX_DRAWDOWN_HALT=0.99
```

> ⚠️ **하드 게이트**: `TRADING_MODE=live`인데 `EXCHANGE`로 지정한 거래소의 API 키/시크릿이
> 비어 있으면 설정 로드 단계에서 `MissingCredentialsError`로 즉시 실패한다.

### 5-2. git에 없는 운영 파일 복원 (필수)

로컬 백업 폴더에서 새 서버로 SCP:

```bash
BK=~/trading_backup_20260908   # STEP 1에서 백업한 폴더로 변경

scp -i <새_개인키_파일> $BK/docker-compose.yml    ubuntu@<새_서버_공인IP>:~/trading-agent/
scp -i <새_개인키_파일> $BK/entrypoint.sh         ubuntu@<새_서버_공인IP>:~/trading-agent/
scp -i <새_개인키_파일> $BK/.trading_mode          ubuntu@<새_서버_공인IP>:~/trading-agent/
scp -i <새_개인키_파일> $BK/.strategy_params.json  ubuntu@<새_서버_공인IP>:~/trading-agent/
```

새 서버에서 실행 권한 복원:

```bash
chmod +x ~/trading-agent/entrypoint.sh
```

### 5-3. DB 복원

```bash
mkdir -p ~/trading-agent/data ~/trading-agent/logs ~/trading-agent/reports

scp -i <새_개인키_파일> $BK/journal.db   ubuntu@<새_서버_공인IP>:~/trading-agent/data/
scp -i <새_개인키_파일> $BK/positions.db ubuntu@<새_서버_공인IP>:~/trading-agent/data/
```

> 신규 설치(거래 이력 초기화)라면 DB 복사를 생략해도 된다 — 컨테이너 시작 시 자동 생성된다.
> `positions.db`가 있으면 이전 오픈 포지션을 자동 복원하니, 초기화하려면 이 파일을 지운다.

### 5-4. `.trading_mode` 기본 동작 주의 (중요, Windows 워치독과 다름)

`entrypoint.sh`는 `.trading_mode` 파일이 **없으면 `live`로 기본 설정하고 파일을 새로 만든다**
(Windows용 `start_agent_watchdog.ps1`은 반대로 `paper`가 기본값이었다). 신규 설치 시 실수로
바로 실거래가 시작되지 않도록, **원치 않으면 컨테이너 기동 전에 `.trading_mode`를 직접
`paper`로 만들어 둔다**:

```bash
echo -n "paper" > ~/trading-agent/.trading_mode
```

---

## STEP 6 — 새 서버: 기동 및 검증

### 6-1. 컨테이너 빌드 및 기동

```bash
cd ~/trading-agent
docker compose up -d --build
docker compose ps
# trading-agent, breakout-scanner 두 컨테이너가 "Up (healthy)" 상태여야 한다
```

### 6-2. 로그 확인

```bash
docker compose logs -f trading-agent
# "Starting trading agent (mode=...)" 로그와 정상 Tick 로그가 보이면 성공
```

> `entrypoint.sh`는 `--skip-checks`를 붙여 실행하도록 구성되어 있어, 컨테이너 로그에는
> `src/utils/startup_check.py`의 `[PASS]` 프리플라이트 로그가 표시되지 않는다 (의도된 설정 —
> 리스크 하드 리밋(`RiskManager`)이나 서킷 브레이커 자체는 이 플래그와 무관하게 항상 동작한다).

### 6-3. 헬스체크 / 대시보드 확인

```bash
curl http://localhost:8080/health   # 200 이면 정상
curl http://localhost:8081/         # 대시보드 HTML 응답

# 로컬(외부)에서
curl http://<새_서버_공인IP>:8080/health
curl http://<새_서버_공인IP>:8081/
```

브라우저에서 `http://<새_서버_공인IP>:8081/` 접속해 포지션/모드/전략 통계가 이관 전 데이터
기준으로 표시되는지 확인한다.

### 6-4. 텔레그램 확인

`/status` 명령으로 엔진 모드/서킷 브레이커 상태를 확인한다 (사용 가능한 전체 명령은
`usage-guide.md` 참고).

---

## 이관 완료 체크리스트

```
[기존 서버]
□ (서버에서 직접 수정한 코드가 있다면) git add/commit/push 완료
□ docker compose down 으로 컨테이너 완전 정지
□ 아래 파일 전부 로컬로 백업 완료
  - .env
  - .trading_mode
  - .strategy_params.json
  - docker-compose.yml   (git 미반영!)
  - entrypoint.sh        (git 미반영!)
  - data/journal.db
  - data/positions.db

[새 서버]
□ OCI 인스턴스 생성 (VM.Standard.E2.1.Micro 또는 상위 사양)
□ Security List에 22/8080/8081 인바운드 규칙 추가
□ setup_trading_server.sh 실행 (Docker/스왑/ufw/clone 완료)
□ sudo ufw allow 8081/tcp 추가 실행 (스크립트에는 없음!)
□ .env 작성 완료 (API 키, TRADING_MODE, MAX_* 값)
□ docker-compose.yml / entrypoint.sh 복원 및 chmod +x entrypoint.sh
□ .trading_mode 복원 또는 의도적으로 paper 로 초기화
□ .strategy_params.json 복원
□ data/journal.db, data/positions.db 복원 (이관 시) 또는 생략 (초기화 시)
□ docker compose up -d --build 실행
□ docker compose ps → 두 컨테이너 모두 Up (healthy)
□ curl http://<새IP>:8080/health → 200
□ curl http://<새IP>:8081/ → 200
□ 브라우저에서 대시보드 데이터 확인
□ 텔레그램 /status 응답 확인
□ 기존 서버가 완전히 정지된 상태인지 재확인 (중복 실거래 방지)
```

---

## 문제 해결

### SSH 접속이 안 될 때

```bash
ssh -v -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29
# Permission denied (publickey): 키 파일 권한 확인
chmod 600 ./ssh-key-2026-08-25.key
```

### `docker compose up` 시 8080/8081 포트 충돌

```bash
sudo ss -tlnp | grep -E ":8080|:8081"
docker compose down   # 이전 컨테이너가 남아있는 경우
```

### 외부에서 대시보드/헬스체크에 접속이 안 될 때

두 단계 방화벽을 모두 확인해야 한다:
1. OS 방화벽: `sudo ufw status verbose` → 22/8080/8081 ALLOW 확인
2. OCI Security List(VCN 인바운드 규칙): OCI 콘솔에서 해당 서브넷의 Security List에 동일 포트가
   열려 있는지 확인 (ufw만 열고 Security List를 빠뜨리는 경우가 흔한 원인이다)

### `MissingCredentialsError`로 컨테이너가 즉시 재시작을 반복하는 경우

`TRADING_MODE=live`인데 `.env`의 거래소 API 키가 비어 있는 경우다. `docker compose logs
trading-agent`로 원인을 확인하고 `.env`를 채운 뒤 `docker compose up -d --build`로 재기동한다.

### 실수로 `.trading_mode` 없이 기동해 바로 `live`로 시작된 경우

`entrypoint.sh`는 `.trading_mode`가 없으면 `live`로 기본 설정한다(위 STEP 5-4 참고). 즉시 정지:

```bash
docker compose stop trading-agent
echo -n "paper" > ~/trading-agent/.trading_mode
docker compose up -d trading-agent
```

### `positions.db` 복원 후 포지션 중복 오류

엔진 시작 시 `positions.db`의 포지션과 실제 거래소 보유 현황을 자동으로 비교한다. 중복으로
잡히면 컨테이너를 정지하고 `data/positions.db`를 삭제한 뒤 재기동하면 거래소 잔고 기준으로
자동 재등록된다.

```bash
docker compose stop trading-agent
rm ~/trading-agent/data/positions.db
docker compose up -d trading-agent
```

### 컨테이너가 `unhealthy`로 표시될 때

```bash
docker compose logs --tail 100 trading-agent
docker inspect --format='{{json .State.Health}}' trading-agent-trading-agent-1 | python3 -m json.tool
```

### 메모리 부족(OOM)으로 컨테이너가 죽는 경우

`VM.Standard.E2.1.Micro`는 메모리 1GB라 스왑(2GB)에 의존한다. 스왑이 없다면
`setup_trading_server.sh`의 3단계를 참고해 수동 생성한다.

```bash
free -h   # Swap 2.0G 확인
```

---

## 참고 — 레거시: 로컬 Windows 실행 (개발/백테스트 전용)

과거 로컬 PC 운영 시절에 쓰던 `start.ps1` / `start_agent_watchdog.ps1` / `guardian.ps1` /
`start_agent.bat` 등은 저장소에 여전히 남아 있으며 **로컬 개발·디버깅·백테스트 실행 시에만**
사용한다. 실거래 운영은 위 Oracle Cloud + Docker 절차를 따른다. 로컬 실행 방법은
`usage-guide.md`의 "레거시" 절 참고.
