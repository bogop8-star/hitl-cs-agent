"""고객 문의 자동 답변 에이전트 (LangGraph + Human-in-the-Loop).

흐름: classify → retrieve → draft → gate ─(기준 통과)→ send
                                       └(기준 걸림)→ notify → review ⏸ interrupt
review 이후: 승인/수정 승인 → send, 반려 → reject, 다시 판정 → retrieve (재작성 후 다시 review)

판단 단계(classify, draft)는 ANTHROPIC_API_KEY 가 있고 HITL_LLM=1 이면 Claude 가 맡고,
아니면 규칙 기반으로 동작한다(키 없이 실행, 결과 재현 가능).
"""
from __future__ import annotations

import json
import operator
import os
import re
import sqlite3
import uuid
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import store
from criteria import stop_reasons
from policy import CATEGORIES, CATEGORY_KEYWORDS, POLICY

load_dotenv()

MODEL = os.getenv("HITL_MODEL", "claude-haiku-5-5")
LLM_MODE = bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("HITL_LLM", "0") == "1"
MAX_REDO = 2  # '다시 판정' 상한. 넘으면 승인/수정/반려 중에서 고르게 한다.


class State(TypedDict, total=False):
    inquiry: dict            # 고객 요청 원문 {id, customer, email, text}
    category: str            # 분류 결과
    confidence: float        # 분류 확신도
    classified_by: str       # rule | llm | human
    citations: list          # 근거 규정 [{id, text}]
    draft: str               # 답변 초안
    reasons: list            # 멈춘 이유 (빈 리스트면 자동 발송)
    redo_count: int          # 다시 판정 횟수
    instruction: str         # 다시 판정 때 담당자 지시
    decision: dict           # 담당자 답 {action, text?, reason?, instruction?}
    final: str               # 실제 발송된 본문
    status: str              # sent | rejected
    stopped_at: str          # 승인 요청 시각
    history: Annotated[list, operator.add]  # 담당자 응답 기록 (누적)


# ───────────────────────── 판단 단계 (규칙 / LLM) ─────────────────────────

def _rule_classify(text: str) -> tuple[str, float]:
    scores = {c: sum(1 for k in kws if k in text) for c, kws in CATEGORY_KEYWORDS.items()}
    top_cat, top = max(scores.items(), key=lambda kv: kv[1])
    total = sum(scores.values())
    if top == 0:
        return "기타", 0.3
    # 한 카테고리에 몰릴수록, 근거 키워드가 많을수록 확신도가 높다.
    conf = (top / total) * min(1.0, 0.6 + 0.2 * top)
    return top_cat, round(conf, 2)


_client = None


def _llm(prompt: str, system: str, max_tokens: int = 800) -> str:
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    msg = _client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def _llm_classify(text: str) -> tuple[str, float]:
    out = _llm(
        f"고객 문의:\n{text}\n\n카테고리 후보: {', '.join(CATEGORIES)}\n"
        '다음 JSON 한 줄로만 답하세요: {"category": "...", "confidence": 0.0~1.0}\n'
        "confidence 는 이 문의가 그 카테고리 하나로 깔끔하게 답할 수 있는 정도입니다. "
        "요청이 여러 개 섞였거나 애매하면 낮게 주세요.",
        system="당신은 쇼핑몰 고객센터 문의 분류기입니다.", max_tokens=100,
    )
    m = re.search(r"\{.*\}", out, re.S)
    try:
        d = json.loads(m.group(0))
        cat = d["category"] if d["category"] in CATEGORIES else "기타"
        return cat, round(float(d["confidence"]), 2)
    except Exception:
        return _rule_classify(text)


def _retrieve(category: str, text: str) -> list[dict]:
    return [{"id": p["id"], "text": p["text"]}
            for p in POLICY if p["cat"] == category and any(k in text for k in p["kw"])]


def _rule_draft(state: State) -> str:
    name = state["inquiry"]["customer"]
    cites = state.get("citations", [])
    lines = [f"안녕하세요, {name} 고객님. 문의해 주셔서 감사합니다.", ""]
    if cites:
        lines += [f"{c['text']} [{c['id']}]" for c in cites]
    else:
        lines.append("문의하신 내용은 담당 부서에서 확인한 뒤 다시 안내드리겠습니다.")
    if state.get("instruction"):
        lines += ["", f"(담당자 지시 반영: {state['instruction']})"]
    lines += ["", "추가로 궁금하신 점이 있으면 언제든 문의해 주세요."]
    return "\n".join(lines)


def _llm_draft(state: State) -> str:
    cites = state.get("citations", [])
    rules = "\n".join(f"[{c['id']}] {c['text']}" for c in cites) or "(해당 규정 없음)"
    extra = f"\n\n담당자 지시(반드시 반영): {state['instruction']}" if state.get("instruction") else ""
    return _llm(
        f"고객 이름: {state['inquiry']['customer']}\n문의: {state['inquiry']['text']}\n\n"
        f"사용 가능한 규정:\n{rules}{extra}",
        system=(
            "당신은 쇼핑몰 고객센터 상담원입니다. 고객에게 보낼 답변 메일 본문만 쓰세요.\n"
            "- 주어진 규정 내용만 근거로 답하고, 근거로 쓴 문장 끝에 [규정명 N조] 형식으로 표기합니다.\n"
            "- 규정에 없는 보상·예외·일정은 약속하지 않습니다. 규정이 없으면 '확인 후 다시 안내드리겠습니다'로 답합니다.\n"
            "- 정중하고 간결하게, 5문장 이내."
        ),
    )


# ───────────────────────── 노드 ─────────────────────────

def classify(state: State) -> dict:
    text = state["inquiry"]["text"]
    cat, conf = _llm_classify(text) if LLM_MODE else _rule_classify(text)
    return {"category": cat, "confidence": conf, "classified_by": "llm" if LLM_MODE else "rule"}


def retrieve(state: State) -> dict:
    return {"citations": _retrieve(state["category"], state["inquiry"]["text"])}


def draft(state: State) -> dict:
    return {"draft": _llm_draft(state) if LLM_MODE else _rule_draft(state)}


def gate(state: State) -> dict:
    reasons = stop_reasons(state)
    if state.get("redo_count", 0) > 0:
        # 사람이 다시 판정을 시킨 건은 결과도 사람이 다시 본다.
        reasons.append({"code": "R", "label": "재판정 결과 확인",
                        "detail": f"담당자 지시로 {state['redo_count']}회 재작성"})
    return {"reasons": reasons}


def notify(state: State, config) -> dict:
    """담당자 알림. review 와 분리해 두어야 재개할 때 알림이 두 번 나가지 않는다."""
    labels = ", ".join(r["label"] for r in state["reasons"])
    store.notify(config["configurable"]["thread_id"],
                 f"[승인 요청] {state['inquiry']['id']} · {labels}")
    return {"stopped_at": store.now()}


def review(state: State) -> dict:
    """사람에게 묻는 단계. interrupt 앞에 바깥으로 나가는 동작을 두지 않는다
    (재개하면 이 함수가 처음부터 다시 실행되기 때문)."""
    inq = state["inquiry"]
    decision = interrupt({
        "요청": inq["text"],
        "고객": f"{inq['customer']} <{inq['email']}>",
        "분류": state["category"],
        "확신도": state["confidence"],
        "근거 규정": state.get("citations", []),
        "답변 초안": state["draft"],
        "멈춘 이유": state["reasons"],
        "통과시키면": f"{inq['customer']} 고객({inq['email']})에게 위 답변이 메일로 발송되고 "
                     "문의가 '답변 완료'로 종결됩니다. 발송 후에는 회수할 수 없습니다.",
        "다시 판정 남은 횟수": MAX_REDO - state.get("redo_count", 0),
    })
    update: dict = {"decision": decision,
                    "history": [{"at": store.now(), **decision}]}
    if decision["action"] == "redo" and state.get("redo_count", 0) < MAX_REDO:
        update["redo_count"] = state.get("redo_count", 0) + 1
        update["instruction"] = decision.get("instruction", "")
        if decision.get("category"):
            update.update(category=decision["category"], confidence=1.0, classified_by="human")
    return update


def send(state: State, config) -> dict:
    d = state.get("decision") if state.get("reasons") else None
    body = d["text"] if d and d["action"] == "edit" else state["draft"]
    mode = "auto" if d is None else ("human-edited" if d["action"] == "edit" else "human-approved")
    inq = state["inquiry"]
    store.send_email(config["configurable"]["thread_id"], inq["id"], inq["email"], body, mode)
    return {"final": body, "status": "sent"}


def reject(state: State, config) -> dict:
    store.to_manual(config["configurable"]["thread_id"], state["inquiry"]["id"],
                    state["decision"].get("reason", ""))
    return {"status": "rejected"}


def after_gate(state: State) -> str:
    return "notify" if state["reasons"] else "send"


def after_review(state: State) -> str:
    action = state["decision"]["action"]
    if action in ("approve", "edit"):
        return "send"
    if action == "reject":
        return "reject"
    redos = sum(1 for h in state.get("history", []) if h["action"] == "redo")
    return "retrieve" if redos <= MAX_REDO else "review"  # 상한을 넘은 재판정 요청은 다시 묻는다


# ───────────────────────── 그래프 ─────────────────────────

CHECKPOINT_DB = store.DB_DIR / "checkpoints.db"


def build_app():
    g = StateGraph(State)
    for name, fn in [("classify", classify), ("retrieve", retrieve), ("draft", draft), ("gate", gate),
                     ("notify", notify), ("review", review), ("send", send), ("reject", reject)]:
        g.add_node(name, fn)
    g.add_edge(START, "classify")
    g.add_edge("classify", "retrieve")
    g.add_edge("retrieve", "draft")
    g.add_edge("draft", "gate")
    g.add_conditional_edges("gate", after_gate, ["notify", "send"])
    g.add_edge("notify", "review")
    g.add_conditional_edges("review", after_review, ["send", "reject", "retrieve", "review"])
    g.add_edge("send", END)
    g.add_edge("reject", END)
    # 파일 기반 체크포인터: 프로그램을 껐다 켜도 승인 대기 건이 남는다.
    saver = SqliteSaver(sqlite3.connect(CHECKPOINT_DB, check_same_thread=False))
    return g.compile(checkpointer=saver)


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def start(app, inquiry: dict) -> tuple[str, dict]:
    """새 문의 처리. 요청 한 건마다 새 thread_id."""
    tid = f"{inquiry['id']}-{uuid.uuid4().hex[:6]}"
    store.add_thread(tid, inquiry["id"])
    out = app.invoke({"inquiry": inquiry, "redo_count": 0, "history": []}, _cfg(tid))
    return tid, out


def resume(app, thread_id: str, decision: dict) -> dict:
    """담당자 답으로 멈춘 자리부터 이어간다."""
    return app.invoke(Command(resume=decision), _cfg(thread_id))


def snapshot(app, thread_id: str):
    return app.get_state(_cfg(thread_id))


def list_pending(app) -> list[dict]:
    """다음 단계가 남아 있고 interrupt 로 멈춘 건 = 승인 대기."""
    out = []
    for row in store.list_threads():
        snap = snapshot(app, row["thread_id"])
        if snap.next and snap.interrupts:
            out.append({"thread_id": row["thread_id"], "created_at": row["created_at"],
                        "payload": snap.interrupts[0].value, "values": snap.values})
    return out
