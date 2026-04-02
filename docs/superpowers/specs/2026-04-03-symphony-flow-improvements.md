# Symphony Flow Improvements — PR Quality, Self-Decisions, Verification

**Date**: 2026-04-03  
**Based on**: [consensus] 분석 from issue whatap/go-apm#27 + PR#28  
**Status**: Approved via 3-way consensus (Claude + Gemini + Codex)

---

## Problem Statement

whatap/go-apm#27 실행 결과 다음 3가지 문제점이 발견됨:

1. **PR body에 에이전트 raw thinking이 그대로 노출** — "Now let me read...", "Now add new fields..." 같은 작업 로그가 PR 본문에 들어감
2. **Self-decisions가 이슈에 검토 요청으로 안 올라옴** — 에이전트가 `<!-- self-decisions -->` 마커를 따르지 않아 추출 실패
3. **Verification Report 표시 버그** — Health: FAILED, Coverage: 6/6 (0%)로 모순적 표시. manifest 파싱 실패 + max_turns 부족이 근본 원인

---

## Change 1: PR Body Formatting (프롬프트 + 생성 로직)

### 현재 동작
- `_collect_agent_response()`가 `message.delta` 이벤트를 join → `analysis_text`로 PR body에 직접 삽입
- `_read_docs_files()`로 docs/ 폴더 markdown도 읽지만, analysis_text와 동등하게 배치

### 변경
1. **`_build_dev_prompt()`에 `docs/analysis.md` 작성 지시 추가**:
   - 에이전트가 작업 완료 후 `docs/analysis.md`에 구조화된 분석 보고서 작성
   - 필수 섹션: `## Summary`, `## Changes Made`, `## Affected Files` (테이블), `## Self-Decisions`

2. **`_do_pr_flow()` PR body 우선순위 변경**:
   - `docs_content`가 있으면 → "Analysis & Implementation Plan" 섹션에 docs_content 사용
   - `analysis_text`(raw output) → `<details>` 태그로 접어서 하단에 참고용 첨부
   - `docs_content`가 없으면 → 기존대로 analysis_text 사용 (fallback)

### 대상 파일
- `src/jhsymphony/orchestrator/dispatcher.py`: `_build_dev_prompt()`, `_do_pr_flow()`

---

## Change 2: Self-Decisions 추출 및 게시 강화

### 현재 동작
- 프롬프트에 `<!-- self-decisions -->` HTML 주석 블록 안에 `SELF-DECISION:` 작성 지시
- `_SELF_DECISION_RE`로 `SELF-DECISION: <text>` 패턴만 매칭
- 에이전트가 마커를 잘 안 따름 → 빈 리스트 반환 → 이슈에 검토 요청 미게시

### 변경
1. **프롬프트 변경**: `<!-- self-decisions -->` 마커 → `## Self-Decisions` 헤딩 + `- SELF-DECISION:` 리스트 형태
2. **추출 로직 보강 (`_extract_self_decisions()`)**:
   - 기존: `SELF-DECISION:` 정규식만
   - 추가 fallback: `## Self-Decisions` 섹션 아래 리스트 항목 파싱
   - docs/analysis.md에서도 Self-Decisions 섹션 추출
3. **게시 로직**: self_decisions가 비어있어도, `docs/analysis.md`의 Self-Decisions 섹션이 있으면 해당 내용을 이슈에 게시

### 대상 파일
- `src/jhsymphony/orchestrator/dispatcher.py`: `_build_dev_prompt()`, `_extract_self_decisions()`, post-loop 로직

---

## Change 3: Verification Report 버그 수정

### 현재 버그
```python
# _build_verification_report() line 1235-1236
f"Coverage: {len(result.changed_files)}/{len(result.changed_files) + len(result.missing_files)} required files ({result.coverage_ratio:.0%})"
```
- `changed_files=6, missing_files=0` → 표시: "6/6"
- `coverage_ratio=0.0` (manifest=None → UNKNOWN) → 표시: "0%"
- 6/6 (0%)는 모순

### 근본 원인
- `_parse_plan_manifest()`이 None 반환 (에이전트가 `<!-- plan-manifest -->` JSON도 `## Affected Files` 테이블도 출력 안 함)
- `_check_completeness()`에서 manifest=None → `(UNKNOWN, 0.0, [])` 반환
- report에서 changed_files 수와 coverage_ratio 소스가 다름

### 변경
1. **`_build_verification_report()`**: completeness가 UNKNOWN일 때 coverage 대신 "N/A (no manifest)" 표시
2. **coverage 표시 통일**: manifest 기반 covered/total 표시 (manifest 있을 때만)
3. **`_check_completeness()`**: manifest=None이면 changed_files 수를 참고해서 UNKNOWN이라도 파일 수 정보는 보존

### 대상 파일
- `src/jhsymphony/orchestrator/dispatcher.py`: `_build_verification_report()`, `_check_completeness()`

---

## Change 4: max_turns 및 Budget 조정

### 현재 값
- `max_turns: 50` (yaml), default `30` (ClaudeProvider)
- `per_run_limit_usd: 5.0`

### 변경
- `max_turns: 50 → 100` (복잡한 이슈에서 max_turns 도달로 인한 비정상 종료 방지)
- `per_run_limit_usd: 5.0 → 8.0` (max_turns 증가에 맞춘 예산 확대)
- `ClaudeProvider` default `30 → 50` (yaml 미설정 시에도 합리적 기본값)

### 대상 파일
- `jhsymphony.yaml`
- `jhsymphony.yaml.example`
- `src/jhsymphony/providers/claude.py`: default 파라미터

---

## Out of Scope

- LLM summarizer 후처리 (비용 대비 효과 불명확, 프롬프트 개선으로 충분)
- Verification pipeline의 Gate 구조 변경 (기존 3-gate 유지)
- Dashboard/UI 변경
