"""사람의 승인을 받을지 정하는 멈춤 기준.

모든 기준은 계산 가능한 조건이다. 값을 바꿔 가며 eval_criteria.py 로 비교할 수 있다.
"""
import re

from policy import CONFLICT_PAIRS

DEFAULT_CONFIG = {
    "conf_threshold": 0.6,   # C1: 분류 확신도가 이 값보다 낮으면 멈춤
    "enabled": ["C1", "C2", "C3", "C4"],
}

# C3: 분쟁·평판·개인정보 위험 신호. 답변 한 통이 분쟁으로 번질 수 있는 표현들.
RISK_TERMS = ["소비자원", "신고", "고소", "소송", "법적", "변호사", "유출",
              "언론", "보상", "배상", "리뷰에", "피해"]

CITATION_RE = re.compile(r"\[([^\[\]]+?\d+조)\]")

CRITERIA = {
    "C1": {
        "name": "분류 확신도 낮음",
        "rule": "분류 확신도 < conf_threshold",
        "risk": "문의를 잘못 분류하면 엉뚱한 규정으로 답해 고객이 잘못된 안내를 받는다.",
    },
    "C2": {
        "name": "규정 인용 없음",
        "rule": "검색된 규정 조항이 0개이거나, 답변 본문에 [OO규정 N조] 인용이 없음",
        "risk": "근거 없는 답변은 규정에 없는 약속(차액 보상, 특별 처리 등)이 될 수 있다.",
    },
    "C3": {
        "name": "분쟁·평판 위험 표현",
        "rule": "문의 원문에 RISK_TERMS(소비자원·신고·유출·보상·리뷰에 …) 중 하나라도 포함",
        "risk": "정형 답변 한 통이 신고·소송·악성 리뷰로 번질 수 있다. 사람이 어조와 후속 조치를 정해야 한다.",
    },
    "C4": {
        "name": "상충 규정 동시 적용",
        "rule": "인용 후보에 결론이 반대인 조항 쌍(CONFLICT_PAIRS)이 함께 들어 있음",
        "risk": "'하자면 무상 반품'과 '세탁 흔적이면 반품 제한'처럼 해석이 갈리는 건을 AI가 한쪽으로 정해 버린다.",
    },
}


def signals(state: dict) -> dict:
    """멈춤 기준에 들어가는 원재료 값을 상태에서 뽑는다."""
    text = state["inquiry"]["text"]
    cited_ids = {c["id"] for c in state.get("citations", [])}
    draft = state.get("draft", "")
    return {
        "confidence": float(state.get("confidence", 0.0)),
        "n_citations": len(cited_ids),
        "draft_cites": bool(CITATION_RE.search(draft)),
        "risk_hits": [t for t in RISK_TERMS if t in text],
        "conflicts": [f"{a} ↔ {b}" for a, b in CONFLICT_PAIRS if a in cited_ids and b in cited_ids],
    }


def stop_reasons(state: dict, config: dict | None = None) -> list[dict]:
    """걸린 기준 목록을 돌려준다. 빈 리스트면 자동 발송."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    s = signals(state)
    on = set(cfg["enabled"])
    out = []
    if "C1" in on and s["confidence"] < cfg["conf_threshold"]:
        out.append({"code": "C1", "label": CRITERIA["C1"]["name"],
                    "detail": f"확신도 {s['confidence']:.2f} < {cfg['conf_threshold']:.2f}"})
    if "C2" in on and (s["n_citations"] == 0 or not s["draft_cites"]):
        detail = "검색된 규정 조항 없음" if s["n_citations"] == 0 else "답변 본문에 규정 인용 표기가 없음"
        out.append({"code": "C2", "label": CRITERIA["C2"]["name"], "detail": detail})
    if "C3" in on and s["risk_hits"]:
        out.append({"code": "C3", "label": CRITERIA["C3"]["name"],
                    "detail": "문의에 '" + "', '".join(s["risk_hits"]) + "' 포함"})
    if "C4" in on and s["conflicts"]:
        out.append({"code": "C4", "label": CRITERIA["C4"]["name"],
                    "detail": ", ".join(s["conflicts"])})
    return out
