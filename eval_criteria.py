"""멈춤 기준 비교: 같은 문의 데이터로 개입률·놓침·헛멈춤을 센다.

정답(gold_human)은 data/inquiries.json 에 사람이 미리 붙여 둔 '사람이 봤어야 하는가' 라벨이다.
실행: python eval_criteria.py   → 표 출력 + results/criteria_comparison.md 저장
"""
import json
from pathlib import Path

import agent
from criteria import stop_reasons

ROOT = Path(__file__).parent

CANDIDATES = [
    ("기준 없음 (전부 자동)", None),
    ("전부 멈춤", "ALL"),
    ("C1 확신도<0.7", {"enabled": ["C1"], "conf_threshold": 0.7}),
    ("C2 인용 없음", {"enabled": ["C2"]}),
    ("C3 위험 표현", {"enabled": ["C3"]}),
    ("C1(0.7)+C2+C3", {"enabled": ["C1", "C2", "C3"], "conf_threshold": 0.7}),
    ("C1(0.7)+C2+C3+C4", {"enabled": ["C1", "C2", "C3", "C4"], "conf_threshold": 0.7}),
    ("C1(0.6)+C2+C3+C4 ← 채택", {"enabled": ["C1", "C2", "C3", "C4"], "conf_threshold": 0.6}),
    ("C1(0.5)+C2+C3+C4", {"enabled": ["C1", "C2", "C3", "C4"], "conf_threshold": 0.5}),
]


def judged_states(inquiries: list[dict]) -> list[dict]:
    """그래프의 판단 단계(classify → retrieve → draft)만 돌려 기준 입력값을 만든다."""
    states = []
    for q in inquiries:
        s = {"inquiry": q}
        for step in (agent.classify, agent.retrieve, agent.draft):
            s.update(step(s))
        states.append(s)
    return states


def evaluate(states: list[dict], config) -> dict:
    stopped, misses, false_stops = [], [], []
    for s in states:
        q = s["inquiry"]
        stop = config == "ALL" or (config is not None and bool(stop_reasons(s, config)))
        if stop:
            stopped.append(q["id"])
            if not q["gold_human"]:
                false_stops.append(q["id"])
        elif q["gold_human"]:
            misses.append(q["id"])
    n = len(states)
    return {"개입": len(stopped), "개입률": len(stopped) / n, "놓침": misses, "헛멈춤": false_stops}


def table(states: list[dict], candidates=CANDIDATES) -> list[dict]:
    rows = []
    for name, cfg in candidates:
        r = evaluate(states, cfg)
        rows.append({"기준": name, "사람에게 온 건": r["개입"], "개입률": f"{r['개입률']:.0%}",
                     "놓침": len(r["놓침"]), "놓친 건": ", ".join(r["놓침"]) or "-",
                     "헛멈춤": len(r["헛멈춤"]), "헛멈춤 건": ", ".join(r["헛멈춤"]) or "-"})
    return rows


def to_markdown(rows: list[dict]) -> str:
    cols = list(rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def per_case(states: list[dict]) -> str:
    lines = ["| ID | 문의 | 분류 | 확신도 | 인용 조항 | 걸린 기준(채택안) | 정답 |", "|---|---|---|---|---|---|---|"]
    for s in states:
        q = s["inquiry"]
        reasons = stop_reasons(s)
        lines.append(
            f"| {q['id']} | {q['text']} | {s['category']} | {s['confidence']:.2f} | "
            f"{', '.join(c['id'] for c in s['citations']) or '-'} | "
            f"{', '.join(r['code'] for r in reasons) or '자동'} | {'사람' if q['gold_human'] else '자동'} |")
    return "\n".join(lines)


if __name__ == "__main__":
    inquiries = json.loads((ROOT / "data" / "inquiries.json").read_text(encoding="utf-8"))
    states = judged_states(inquiries)
    md = to_markdown(table(states))
    cases = per_case(states)
    mode = "LLM" if agent.LLM_MODE else "규칙 기반"
    out = f"# 멈춤 기준 비교 ({len(states)}건, 판단 모드: {mode})\n\n{md}\n\n## 건별 판정 (채택안)\n\n{cases}\n"
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "criteria_comparison.md").write_text(out, encoding="utf-8")
    print(out)
