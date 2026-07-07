# oh-my-somnia 사용 가이드 🧬

> **이 문서는** oh-my-somnia를 **실전에서 어떻게 쓰고 · 어디에 붙이고 · 어떻게
> 발전시킬지**를 다룹니다. 개요·설치는 [README](../README.md), 전략적
> 활용 방향은 [ROADMAP](../ROADMAP.md)을 함께 보세요.

---

## 1. 한눈에 보기

oh-my-somnia는 **자기 자신을 개선하는 에이전트 하네스**입니다. 작업을 주면:

```
계획 → 실행 → (실패 시) 원인 분석 → 전략(게놈) 돌연변이 → A/B 검증 → 더 나은 것만 채택
```

진화의 대상은 **프로젝트 코드가 아니라 하네스의 휴리스틱(게놈)**입니다.
쓸수록 게놈이 쌓여 다음 작업이 더 잘 됩니다 — **복리로 똑똑해지는 도구**.

---

## 2. 빠른 시작

```bash
# 사전 조건: Python 3.11+, Claude Code CLI(로그인 상태), git
pip install -e .

cd your-project
somnia init                     # .somnia/config.toml 생성 (선택)

# 객관적 fitness가 있으면 (권장): 종료 코드 0 = 통과
somnia run "결제 모듈의 flaky 테스트 수정" --fitness "python -m pytest -q"

# fitness가 없으면 AI 저지가 성공 기준 대비 채점
somnia run "README에 API 사용 예제 추가"

somnia status                   # 승률·최근 실행·게놈 요약
```

첫 실행의 첫 단추는 **`fitness_command` 설정**입니다 (아래 3.2 참고).

---

## 3. 어떻게 사용하나

### 3.1 두 가지 실행 모드

| 상황 | 명령 | 판정 방식 |
|---|---|---|
| 테스트/빌드/검증 스크립트가 있다 | `somnia run "작업" --fitness "cmd"` | `cmd` 종료 코드 (0=통과) |
| 객관적 기준이 없다 | `somnia run "작업"` | AI 저지가 성공 기준 대비 0–100 채점 |

**항상 fitness가 있는 쪽이 강력합니다.** 진화(SELECT)가 객관적 신호로
판정되기 때문. 주관적 작업("예쁘게", "감동적으로")은 **검증 스크립트로
번역**한 뒤 맡기세요.

### 3.2 설정 파일 (`.somnia/config.toml`)

`somnia init`이 생성하는 템플릿의 핵심 항목:

```toml
fitness_command = "python -m pytest -q"  # 객관적 적합도 판정 (핵심!)
judge = true            # AI 저지 병행 (점수 정밀화)
generations = 3         # 최대 진화 세대 (1이면 재시도 없음)
max_turns = 60          # 실행 에이전트 턴 상한
# max_budget_usd = 2.0  # 에이전트 호출당 비용 상한
# model = "claude-sonnet-4-6"
sandbox = "auto"        # auto / worktree / copy
scope = "global"        # 학습 공유 범위: global(전 프로젝트) / project
```

> ⚠️ **fitness_command는 프로젝트 트리 밖에서도 실행 가능해야 합니다.**
> 샌드박스가 `.venv`·`node_modules`를 복사에서 제외하므로
> `python -m pytest` ⭕, `.venv/bin/pytest` ❌.

### 3.3 샌드박스 (실패가 실제 프로젝트를 오염시키지 않게)

세대마다 프로젝트의 **격리 사본**에서 실행하고, **통과한 세대의 변경분만**
실제 프로젝트에 머지합니다. 사용자가 그 사이 수정한 파일은 절대 덮어쓰지 않음.

| 모드 | 동작 | 언제 |
|---|---|---|
| `auto` (기본) | git 저장소면 worktree, 아니면 copy 자동 선택 | 대부분 이걸로 충분 |
| `worktree` | HEAD + 커밋 안 된 변경분 오버레이, 대형 저장소에서 빠름 | 강제하고 싶을 때 |
| `copy` | 디렉터리 통째 복사 (git 없어도 됨) | git 아닌 프로젝트 |

**🆕 모노레포 하위 디렉터리 지원** — `monorepo/packages/frontend` 같은 하위
폴더에서 실행해도 worktree가 동작합니다:
- 저장소 **전체**를 체크아웃하되, 에이전트는 **그 하위 폴더에서만** 작업하고
  변경분도 그 하위 폴더에만 머지됩니다.
- 하위 폴더 **밖** 파일을 수정하면 머지되지 않고 **경고**로 알려줍니다.
- **사이즈 가드**: 하위 폴더가 거대한 저장소의 작은 조각이면 (예: git으로
  관리되는 홈 디렉터리 밑) `auto`가 통째 체크아웃 대신 **하위 폴더만 copy**로
  폴백합니다. 통째로 강제하려면 `--sandbox worktree`.

### 3.4 비용·안전 통제

- `--generations N` / `generations`: 재시도 세대 상한 (비용 상한의 1차 방어선)
- `max_budget_usd`: 에이전트 호출당 비용 상한
- `--keep-sandboxes`: 디버깅용으로 샌드박스 보존
- `--merge-best`: 전부 실패해도 최고 점수 세대를 머지
- `--in-place`: **샌드박스 없이 실제 프로젝트에서 직접 실행** — 세대가
  누적되고 실패분이 남으니 주의. `bypassPermissions` + `--in-place` 조합은
  실제 프로젝트에 무제한 접근이므로 피하세요.

---

## 4. 어디에 사용하면 빛나는가

세 조건이 겹칠수록 효과가 큽니다:

| 조건 | 이유 |
|---|---|
| ① 객관적 fitness가 있다 | 종료 코드가 pass/fail을 판정해야 진화가 정확 |
| ② 비슷한 작업이 반복된다 | 게놈 학습이 재사용되어 복리 효과 |
| ③ 샌드박스에서 돌릴 수 있다 | 실패가 실제 프로젝트를 오염시키지 않음 |

**구체적 적용 사례** (자세한 로드맵은 [ROADMAP](../ROADMAP.md)):

- **테스트 슈트 확장** — `somnia run "X 기능 테스트 추가" --fitness "pytest -q"`
- **학생 과제 자동 채점기** — 루브릭을 pytest로 작성, 새 과제마다 채점기 확장
- **데이터 분석 CLI** — 픽스처 대비 pytest를 fitness로, 지표를 하나씩 추가
- **문서/교안 QA** — 깨진 링크·용어 일관성·정답 키 누락을 검증 스크립트로

**모노레포에서** — 이제 개별 패키지 디렉터리 안에서 바로 돌릴 수 있으니,
`packages/*` 각각에 fitness를 두고 패키지 단위로 반복 개선하기 좋습니다.

주관적 기준만 있는 일은 AI 저지에만 의존해 약해집니다 — fitness로 번역하세요.

---

## 5. 어떻게 발전시키나

발전에는 **두 축**이 있습니다: 학습(게놈)을 활용하는 것과, 하네스 자체를
개발하는 것.

### 5.1 학습(게놈) 활용 — 도구를 똑똑하게

```bash
somnia status                    # 승률·게놈 상태 추적
somnia genome list               # 유전자 목록
somnia genome show <gene-id>     # 특정 유전자 내용
somnia evolve                    # 누적 히스토리에서 후보 유전자 채굴
somnia genome promote <gene-id>  # 후보를 활성으로 승격
somnia genome rm <gene-id>       # 이상한 유전자 제거
```

- **자연 선택**: 돌연변이(패치)는 A/B 시험을 통과해야만 게놈에 남습니다.
  후보 유전자는 통과 실행에서 스스로를 증명하면 자동 승격됩니다.
- **주기적으로 `somnia evolve`** 를 돌려 히스토리에서 새 휴리스틱을 채굴하세요.
- **학습 공유 범위**: `scope = "global"`(모든 프로젝트 공유) vs `"project"`.
- **머신 간 공유**: 게놈은 `~/.oh-my-somnia/genome/*.md` 마크다운 파일들 —
  README의 멀티 머신 섹션 방식으로 동기화하면 한 사람의 교훈이 모두의
  하네스를 똑똑하게 만듭니다.

### 5.2 하네스 자체 개발 — 도구를 확장하기

이 저장소는 **자기 자신을 테스트로 검증하며 개선**합니다
(`fitness_command = "python -m pytest -q"`). 새 기능을 추가하는 흐름:

1. **설계(spec)** → `docs/superpowers/specs/`
2. **구현 계획(plan)** → `docs/superpowers/plans/`
3. **TDD 구현** — 실패 테스트 먼저 → 최소 구현 → 통과 → 커밋 (태스크 단위)
4. **검증** — `python -m pytest -q` 전부 통과해야 머지
5. **PR → 리뷰 → main 머지**

> 실제 예시: "worktree 모노레포 하위폴더 지원"이 이 흐름으로 추가됐습니다
> (`docs/superpowers/specs|plans/2026-07-07-worktree-monorepo-subdir*`).
> 설계 근거·의사결정 이력까지 문서로 남아 있어 다음 개선의 참고가 됩니다.

**발전 아이디어 (로드맵 요약):**
- Phase 2: 학생 과제 자동 채점 하네스 (파급력 최대)
- Phase 3: 이스포츠 경기 데이터 분석 CLI / GDD 밸런스 시뮬레이터
- Phase 4: 교안 QA 파이프라인
- Phase 5: **커뮤니티 게놈 허브** — 진화된 유전자를 공유하는 공개 레포

---

## 6. 트러블슈팅 · 주의사항

| 증상/상황 | 대응 |
|---|---|
| fitness가 샌드박스에서 실패 | 트리 밖에서도 실행되는 명령인지 확인 (`.venv/bin/...` ❌) |
| 모노레포 하위폴더인데 copy로 폴백됨 | 정상 — 사이즈 가드 동작. 통째 원하면 `--sandbox worktree` |
| 실제 프로젝트가 오염될까 걱정 | 기본이 샌드박스 격리. `--in-place`만 직접 실행 |
| 비용이 걱정 | `generations`↓, `max_budget_usd` 설정, fitness로 빨리 판정 |
| 이상한 유전자가 성능을 해침 | `somnia genome rm <id>` 로 제거 |

---

## 7. 명령·옵션 레퍼런스

**명령**

| 명령 | 설명 |
|---|---|
| `somnia run "작업"` | 진화 루프 실행 |
| `somnia init` | `.somnia/config.toml` 템플릿 생성 |
| `somnia status` | 최근 실행·게놈 요약 |
| `somnia evolve` | 히스토리에서 후보 유전자 채굴 |
| `somnia genome list\|show\|promote\|rm` | 게놈 관리 |

**`run` 옵션**

| 옵션 | 설명 |
|---|---|
| `--fitness "cmd"` | 종료 코드 0=통과인 셸 명령 |
| `--generations N` | 최대 진화 세대 (기본 3) |
| `--model NAME` | 전 에이전트 단계의 모델 |
| `--sandbox {auto,copy,worktree}` | 샌드박스 백엔드 (기본 auto) |
| `--in-place` | 샌드박스 없이 실제 프로젝트에서 직접 실행 |
| `--keep-sandboxes` | 실행 후 샌드박스 보존 (디버깅) |
| `--merge-best` | 전부 실패해도 최고 점수 세대 머지 |

> `darwin` 명령은 레거시 별칭으로 계속 동작합니다.
