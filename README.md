# 📮 CS 답변 승인 데스크: 사람이 승인하는 고객 문의 자동 답변 에이전트

쇼핑몰 고객 문의를 **분류 → 규정 검색 → 답변 작성**까지 자동으로 처리하고,
위험한 건만 **고객에게 메일을 보내기 직전에 멈춰** 담당자 승인을 받는 LangGraph 에이전트입니다.
기준에 걸리지 않은 건은 사람을 거치지 않고 바로 발송됩니다.

- 멈춤/재개: LangGraph `interrupt` + `Command(resume=...)`
- 대기 건 보관: `SqliteSaver` (파일 기반 체크포인터라서 앱을 껐다 켜도 대기 건이 남음)
- 웹 데모: Streamlit (승인 대기 목록 → 상세 → 승인 / 수정 후 승인 / 반려 / 다시 판정)

## 실행 방법

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 1) 웹 데모

```bash
streamlit run app.py
```

브라우저에서 `http://localhost:8501` →
**📥 문의 접수**에서 「샘플 문의 17건 모두 처리」 → **⏳ 승인 대기**에서 한 건씩 열어 응답 →
**📤 처리 결과**에서 발송함과 반려 목록 확인 → **📊 멈춤 기준 비교**에서 기준을 바꿔 보기.

### 2) CLI로 전체 흐름 재현 (결과가 `results/run_log.md`에 저장됨)

```bash
python demo_run.py
```

### 3) 멈춤 기준 비교표 (결과가 `results/criteria_comparison.md`에 저장됨)

```bash
python eval_criteria.py
```

## LLM 모드 (선택)

기본값은 **규칙 기반 판단**이라 API 키 없이 실행되고 결과도 매번 같습니다.
Claude로 분류와 답변 작성을 하려면 `.env.example`을 `.env`로 복사한 뒤 키를 넣고 `HITL_LLM=1`로 바꿉니다.

```
ANTHROPIC_API_KEY=sk-ant-...
HITL_LLM=1
HITL_MODEL=claude-haiku-5-5
```

`.env`는 `.gitignore`에 들어 있어 저장소에 올라가지 않습니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `agent.py` | LangGraph 그래프 (노드, 라우팅, interrupt/resume, SqliteSaver) |
| `criteria.py` | 멈춤 기준 C1~C4 (계산 가능한 조건) |
| `policy.py` | 고객센터 규정 조항, 분류 키워드, 상충 조항 쌍 |
| `store.py` | 발송함·반려 목록·알림 기록 (바깥 세계를 흉내 낸 SQLite) |
| `app.py` | Streamlit 승인 데스크 |
| `eval_criteria.py` | 기준별 개입률·놓침·헛멈춤 비교 |
| `demo_run.py` | 17건 처리 + 담당자 응답 시나리오 재현 |
| `data/inquiries.json` | 샘플 문의 17건 + 정답 라벨(사람이 봐야 하는가) |
| `REPORT.md` | 프로젝트 보고서 |

실행 중 생기는 `runtime/`, `runtime_demo/` 폴더는 데이터베이스 파일이라 저장소에 올리지 않습니다.
