"""CLI 데모: 17건을 처리하고, 멈춘 건에 담당자 답을 넣어 이어간 뒤 결과를 기록한다.

실행: python demo_run.py   → results/run_log.md
(별도 DB 폴더 runtime_demo/ 를 써서 웹 데모의 데이터와 섞이지 않는다.)
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DEMO_DIR = ROOT / "runtime_demo"
shutil.rmtree(DEMO_DIR, ignore_errors=True)
os.environ["HITL_DB_DIR"] = str(DEMO_DIR)

import json  # noqa: E402

import agent  # noqa: E402
import store  # noqa: E402

# 담당자가 실제로 내렸다고 가정한 답 (건별 판단 근거는 note 에)
HUMAN = {
    "Q08": [{"action": "edit", "note": "사과 + 택배사 사고 접수 + 오늘 중 재발송 약속으로 고쳐서 발송",
             "text": "임재원 고객님, 배송이 2주나 지연되어 진심으로 죄송합니다. 택배사에 분실 사고를 접수했고, "
                     "같은 상품을 오늘 중 재발송하겠습니다. 운송장 번호는 출고 즉시 문자로 보내드리겠습니다."}],
    "Q09": [{"action": "reject", "reason": "개인정보 사고 의심. 보안 담당자에게 이관해 직접 연락"}],
    "Q10": [{"action": "redo", "instruction": "가격 보상 규정이 없어 차액 환불은 어렵다고 정중히 안내하고, 다음 구매 때 쓸 수 있는 쿠폰 안내는 하지 말 것"},
            {"action": "approve"}],
    "Q11": [{"action": "edit", "note": "입금 확인 + 배송지 변경 방법 두 가지를 모두 답하도록 수정",
             "text": "서유준 고객님, 무통장 입금은 확인까지 최대 1시간이 걸리며 입금자명이 다르면 지연될 수 있습니다 [결제규정 2조]. "
                     "배송지는 출고 전까지 마이페이지 > 주문내역에서 변경할 수 있습니다."}],
    "Q13": [{"action": "reject", "reason": "하자/세탁 수축 판단에 실물 검수 필요. 회수 후 판정으로 이관"}],
    "Q15": [{"action": "approve", "note": "초안이 맞음 (헛멈춤)"}],
    "Q16": [{"action": "edit", "note": "규정 답변 앞에 사과 문장 추가, 보상 약속은 하지 않음",
             "text": "문채원 고객님, 불편을 드려 정말 죄송합니다. 얼룩은 상품 하자에 해당하여 왕복 배송비 없이 무상으로 교환 또는 반품해 드립니다 [교환반품규정 3조]. "
                     "회수 기사 방문 일정을 오늘 중 문자로 안내드리겠습니다."}],
}

inquiries = json.loads((ROOT / "data" / "inquiries.json").read_text(encoding="utf-8"))
app = agent.build_app()
log = ["# 실행 결과 로그", "", f"판단 모드: {'LLM' if agent.LLM_MODE else '규칙 기반'} · 처리 {len(inquiries)}건", "",
       "## 1차 처리", "", "| ID | 분류(확신도) | 결과 | 멈춘 이유 |", "|---|---|---|---|"]
threads = {}
for q in inquiries:
    tid, out = agent.start(app, q)
    threads[q["id"]] = tid
    stopped = "__interrupt__" in out
    reasons = "; ".join(f"{r['code']} {r['detail']}" for r in out.get("reasons", [])) or "-"
    log.append(f"| {q['id']} | {out['category']} ({out['confidence']:.2f}) | {'⏸ 승인 대기' if stopped else '✅ 자동 발송'} | {reasons} |")

pending = agent.list_pending(app)
log += ["", f"자동 발송 {len(inquiries) - len(pending)}건 · 승인 대기 {len(pending)}건", "",
        "## 담당자 처리", "", "| ID | 담당자 답 | 메모 | 최종 상태 |", "|---|---|---|---|"]
for qid, answers in HUMAN.items():
    tid = threads[qid]
    for a in answers:
        decision = {k: v for k, v in a.items() if k != "note"}
        out = agent.resume(app, tid, decision)
        state = "⏸ 다시 대기" if "__interrupt__" in out else out.get("status")
        memo = a.get("note") or a.get("reason") or a.get("instruction") or "-"
        log.append(f"| {qid} | {a['action']} | {memo} | {state} |")

modes = {}
for r in store.outbox():
    modes[r["mode"]] = modes.get(r["mode"], 0) + 1
log += ["", "## 최종 집계", "",
        f"- 발송: {len(store.outbox())}건 ({', '.join(f'{k} {v}' for k, v in modes.items())})",
        f"- 반려 → 담당자 직접 처리: {len(store.manual_queue())}건",
        f"- 남은 승인 대기: {len(agent.list_pending(app))}건",
        f"- 담당자 알림 발송: {len(store.notifications())}회 (재개 시 중복 발송 없음)"]

(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results" / "run_log.md").write_text("\n".join(log) + "\n", encoding="utf-8")
print("\n".join(log))
