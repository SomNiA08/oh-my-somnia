# oh-my-somnia 🧬

**자기 자신을 개선하는 에이전트 하네스.**
계획하고 → 실행하고 → 실패를 분석하고 → 자신의 전략(게놈)에 패치를 제안하고 →
샌드박스에서 A/B 검증하고 → **실제로 더 잘 굴러가는 것만 살아남습니다.**

Claude Agent SDK 기반의 범용 CLI로, 어떤 프로젝트에서든 사용할 수 있습니다.
진화의 대상은 프로젝트 코드가 아니라 **하네스 자신의 휴리스틱**입니다 —
실행할수록 하네스가 똑똑해집니다.

## 동작 원리

```
somnia run "작업 설명"
│
├─ 세대(generation) 0 ──────────────────────────────┐
│   1. SANDBOX   프로젝트를 격리 복사               │
│   2. PLAN      읽기 전용 에이전트가 계획 수립     │  ← 게놈(학습된
│   3. EXECUTE   실행 에이전트가 작업 수행          │     휴리스틱) 주입
│   4. EVALUATE  fitness 명령 + AI 저지로 점수화    │
├───────────────────────────────────────────────────┘
│   통과? ──▶ 변경사항을 실제 프로젝트에 머지. 끝.
│   실패?
│   5. DIAGNOSE  근본 원인 분석 (표면 증상 X)
│   6. MUTATE    게놈 패치 제안 (새 휴리스틱 유전자)
│
├─ 세대 1: 패치된 게놈으로 새 샌드박스에서 재시도
│   7. SELECT    이전 세대보다 적합도가 올랐으면
│                패치를 게놈에 영구 반영, 아니면 폐기
└─ ... generations 한도까지 반복
```

- **게놈(genome)**: `~/.oh-my-somnia/genome/*.md` — 유전자(gene) 하나가
  마크다운 파일 하나. 모든 에이전트 프롬프트에 주입되는 재사용 휴리스틱.
- **자연 선택**: 돌연변이(패치)는 반드시 A/B 시험을 통과해야 살아남음 —
  같은 작업을 이전 게놈 vs 패치된 게놈으로 실행해 적합도가 오른 경우에만 채택.
- **샌드박스**: 세대마다 프로젝트의 격리 복사본에서 실행. git 저장소면
  `git worktree`(HEAD + 커밋 안 된 변경분 오버레이, 대형 저장소에서 빠름),
  아니면 디렉터리 복사를 자동 선택. 통과한 세대의 변경분만 실제 프로젝트에
  머지되고, 사용자가 그 사이에 수정한 파일은 절대 덮어쓰지 않음.
  모노레포 하위 디렉터리에서 실행해도 worktree가 동작한다 — 저장소 전체를
  체크아웃하되 에이전트는 그 하위 디렉터리에서만 작업하고, 변경분도 그
  하위 디렉터리에만 머지된다.

## 설치

```bash
# 사전 조건: Python 3.11+, Claude Code CLI (로그인 상태)
pip install -e .
```

> **oh-my-darwin에서 개명됨**: `darwin` 명령은 레거시 별칭으로 계속 동작하고,
> 기존 `~/.oh-my-darwin` 상태(게놈·히스토리)는 첫 실행 시
> `~/.oh-my-somnia`로 자동 이관되며, 프로젝트의 기존 `.darwin/` 디렉터리도
> 그대로 인식합니다.

## 사용법

> 📖 실전 사용법·적용처·발전 방향은 **[사용 가이드](docs/USAGE.md)** 를 참고하세요.

```bash
cd your-project
somnia init                          # .somnia/config.toml 생성 (선택)

# fitness 명령의 종료 코드가 pass/fail 판정
somnia run "결제 모듈의 flaky 테스트 수정" --fitness "python -m pytest -q"

# fitness 명령이 없으면 AI 저지가 성공 기준 대비 채점
somnia run "README에 API 사용 예제 추가"

somnia status                        # 승률, 최근 실행, 게놈 요약
somnia genome list                   # 유전자 목록
somnia genome show verify-before-done
somnia evolve                        # 누적 히스토리에서 후보 유전자 채굴
```

### 주요 옵션

| 옵션 | 설명 |
|---|---|
| `--fitness "cmd"` | 종료 코드 0 = 통과인 셸 명령 (예: `python -m pytest -q`) |
| `--generations N` | 최대 진화 세대 수 (기본 3, 1이면 재시도 없음) |
| `--sandbox MODE` | `auto`(기본) / `worktree` / `copy` — auto는 git 저장소면 worktree |
| `--in-place` | 샌드박스 없이 프로젝트에서 직접 실행 |
| `--merge-best` | 전부 실패해도 최고 점수 세대를 머지 |
| `--keep-sandboxes` | 실행 후 샌드박스 보존 (디버깅용) |

## 설정 (.somnia/config.toml)

```toml
fitness_command = "python -m pytest -q"  # 객관적 적합도 판정
judge = true            # AI 저지 병행 (점수 정밀화)
generations = 3
max_turns = 60
# max_budget_usd = 2.0  # 에이전트 호출당 비용 상한
# model = "claude-sonnet-4-6"
scope = "global"        # 학습을 전 프로젝트 공유("global") 또는 "project"
```

> **주의**: 샌드박스는 `.venv`, `node_modules` 등을 복사에서 제외하므로
> `fitness_command`는 프로젝트 트리 밖에서도 실행 가능해야 합니다
> (`python -m pytest` ⭕, `.venv/bin/pytest` ❌).

## 진화의 두 경로

1. **런타임 돌연변이** (`somnia run` 중 자동): 실패 → 진단 → 패치 →
   다음 세대에서 A/B 시험 → 개선 시에만 영구 반영.
2. **오프라인 진화** (`somnia evolve`): 누적된 히스토리에서 반복 패턴을
   찾아 **후보(candidate)** 유전자 제안. 후보는 이후 실행에 "provisional"
   표시로 주입되며, 통과 실행에서 2회 검증되면 자동 승격.
   `somnia genome promote/rm`으로 수동 관리도 가능.

## 멀티 머신 워크플로 (회사 ↔ 집)

원격: https://github.com/SomNiA08/oh-my-somnia — 양쪽 머신에서 push/pull 하며 쓰는 흐름:

```bash
# ── 다른 머신 최초 설정 ──
git clone https://github.com/SomNiA08/oh-my-somnia.git
cd oh-my-somnia && pip install -e .       # Python 3.11+ / Claude Code CLI 로그인 필요

# ── 일상 루틴 (양쪽 공통) ──
git pull          # 시작할 때
# ... somnia 사용/개발 ...
git add -A && git commit -m "..." && git push   # 끝날 때
```

### 학습(게놈)도 머신 간에 공유하려면

기본 `scope = "global"`은 게놈을 `~/.oh-my-somnia/genome`에 저장하므로 **머신마다
따로** 쌓입니다. 진화된 학습까지 동기화하려면 둘 중 하나:

1. **프로젝트 단위 공유** — 각 프로젝트의 `.somnia/config.toml`에
   `scope = "project"`: 유전자가 `.somnia/genome/`에 저장되어 그 프로젝트
   저장소와 함께 push/pull됨.
2. **전역 학습 공유** — `~/.oh-my-somnia` 자체를 git 저장소로:
   ```bash
   cd ~/.oh-my-somnia && git init -b main
   printf "sandboxes/\n" > .gitignore     # 샌드박스는 제외, genome/history만 공유
   git add -A && git commit -m "somnia brain" && git remote add origin <BRAIN_URL> && git push -u origin main
   ```
   양쪽 머신에서 주기적으로 pull/push하면 하네스의 "뇌"가 함께 진화합니다.

## 저장소 구조

```
~/.oh-my-somnia/
├── genome/          # 유전자 (진화하는 휴리스틱)
├── history.jsonl    # 모든 실행 기록 (적합도, 진단, 채택/폐기된 패치)
└── sandboxes/       # 실행 중 임시 샌드박스
<project>/.somnia/
├── config.toml      # 프로젝트별 설정
└── genome/          # scope = "project"일 때의 프로젝트 전용 유전자
```

## 한계 (v0.1)

- worktree 샌드박스는 HEAD + 커밋 안 된 변경분으로 구성되므로, gitignore된
  빌드 산출물(`.venv`, `node_modules` 등)은 샌드박스에 없음 —
  `fitness_command`는 프로젝트 트리 밖에서도 실행 가능해야 함.
- `--in-place`는 세대 간 리셋 없이 **실제 프로젝트에 누적** 실행됨 (실행 시
  경고 출력, 이전 세대의 잔여 변경이 있다는 컨텍스트를 에이전트에 전달).
  안전한 기본값은 샌드박스 모드.
- 샌드박스 경로가 `~/.oh-my-somnia/sandboxes/...` 아래 깊게 중첩되므로 경로가
  긴 프로젝트에서는 Windows MAX_PATH(260자) 제한에 걸릴 수 있음 — 이 경우
  `OH_MY_SOMNIA_HOME=C:\sm` 처럼 짧은 경로로 옮기거나 Windows LongPaths를
  활성화.
- 샌드박스 ignore는 이름 단위(모든 깊이) 매칭 — 프로젝트에 `build`/`dist`
  같은 이름의 실제 소스 디렉터리가 있다면 `unignore = ["build"]`로 복구.
- 돌연변이 A/B는 표본 1회 비교라 통계적으로 노이즈가 있음 — 그래서
  후보 유전자의 다회 검증(`uses`/`wins`) 경로를 병행.
- 에이전트 실행 비용이 발생하므로 `max_budget_usd`와 `generations`로 통제 권장.
