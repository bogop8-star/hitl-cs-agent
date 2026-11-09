"""고객 문의 답변 승인 데스크 (Streamlit).

실행: streamlit run app.py
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import agent
import store
from criteria import CRITERIA, DEFAULT_CONFIG
from policy import CATEGORIES

ROOT = Path(__file__).parent
STALE_DAYS = 3  # 운영 규칙: 대기 3일 초과 → 팀장 에스컬레이션 대상

st.set_page_config(page_title="CS 답변 승인 데스크", page_icon="📮", layout="wide")


@st.cache_resource
def get_app():
    return agent.build_app()


@st.cache_data
def load_inquiries():
    return json.loads((ROOT / "data" / "inquiries.json").read_text(encoding="utf-8"))


app = get_app()
inquiries = load_inquiries()
pending = agent.list_pending(app)

# ───────────── 사이드바 ─────────────
st.sidebar.title("📮 CS 답변 승인 데스크")
st.sidebar.caption(f"판단 모드: **{'Claude (' + agent.MODEL + ')' if agent.LLM_MODE else '규칙 기반 (API 키 없이 동작)'}**")
page = st.sidebar.radio("메뉴", ["⏳ 승인 대기", "📥 문의 접수", "📤 처리 결과", "📊 멈춤 기준 비교"],
                        format_func=lambda p: f"{p}  ({len(pending)})" if p.startswith("⏳") else p)
with st.sidebar.expander("현재 멈춤 기준"):
    for code in DEFAULT_CONFIG["enabled"]:
        c = CRITERIA[code]
        st.markdown(f"**{code} {c['name']}**  \n`{c['rule']}`")
    st.caption(f"C1 임계값 = {DEFAULT_CONFIG['conf_threshold']}")
if st.sidebar.button("🗑 데모 데이터 초기화"):
    store.reset()
    st.toast("처리 기록을 지웠습니다. (체크포인트는 남지만 목록에서 빠집니다)")
    st.rerun()

if msg := st.session_state.pop("flash", None):
    st.success(msg)


def elapsed(created: str) -> tuple[str, bool]:
    d = datetime.now() - datetime.fromisoformat(created)
    text = f"{d.days}일 전" if d.days else (f"{d.seconds // 3600}시간 전" if d.seconds >= 3600 else f"{max(1, d.seconds // 60)}분 전")
    return text, d.days >= STALE_DAYS


def run_batch(items):
    stopped = 0
    for q in items:
        _, out = agent.start(app, q)
        stopped += "__interrupt__" in out
    st.session_state["flash"] = f"{len(items)}건 처리: 자동 발송 {len(items) - stopped}건 · 승인 대기 {stopped}건"
    st.rerun()


# ───────────── ⏳ 승인 대기 ─────────────
if page.startswith("⏳"):
    st.header("승인 대기")
    if not pending:
        st.info("승인을 기다리는 건이 없습니다. **📥 문의 접수**에서 문의를 처리해 보세요.")
        st.stop()

    left, right = st.columns([1, 2.4], gap="large")
    ids = [p["thread_id"] for p in pending]
    if st.session_state.get("sel") not in ids:
        st.session_state["sel"] = ids[0]

    with left:
        st.caption(f"{len(pending)}건 대기 중 · 오래된 순")
        for p in pending:
            v, pl = p["values"], p["payload"]
            ago, stale = elapsed(p["created_at"])
            codes = " ".join(f"`{r['code']}`" for r in pl["멈춘 이유"])
            with st.container(border=True):
                st.markdown(f"**{v['inquiry']['id']} · {v['inquiry']['customer']}** {'⚠️' if stale else ''}  \n"
                            f"{v['inquiry']['text'][:38]}{'…' if len(v['inquiry']['text']) > 38 else ''}  \n"
                            f"{codes} · {ago}")
                if st.button("열기", key=f"open-{p['thread_id']}", width="stretch",
                             type="primary" if p["thread_id"] == st.session_state["sel"] else "secondary"):
                    st.session_state["sel"] = p["thread_id"]
                    st.rerun()

    with right:
        cur = next(p for p in pending if p["thread_id"] == st.session_state["sel"])
        pl, v, tid = cur["payload"], cur["values"], cur["thread_id"]
        ago, stale = elapsed(cur["created_at"])
        st.subheader(f"{v['inquiry']['id']} · {pl['고객']}")
        if stale:
            st.warning(f"대기 {STALE_DAYS}일 초과 — 운영 규칙에 따라 팀장 에스컬레이션 대상입니다.")

        st.markdown("##### ① 고객 요청 원문")
        st.info(f"“{pl['요청']}”")

        st.markdown("##### ② 멈춘 이유")
        for r in pl["멈춘 이유"]:
            st.error(f"**{r['code']} {r['label']}** — {r['detail']}")

        st.markdown("##### ③ AI 판단과 근거")
        c1, c2, c3 = st.columns(3)
        c1.metric("분류", pl["분류"])
        c2.metric("확신도", f"{pl['확신도']:.2f}")
        c3.metric("판정 방식", {"rule": "규칙", "llm": "Claude", "human": "담당자 지정"}.get(v.get("classified_by"), "-"))
        if pl["근거 규정"]:
            for c in pl["근거 규정"]:
                st.markdown(f"> **[{c['id']}]** {c['text']}")
        else:
            st.markdown("> ⚠️ 인용할 규정을 찾지 못했습니다. 아래 초안은 규정 근거가 없습니다.")
        with st.container(border=True):
            st.caption("답변 초안")
            st.text(pl["답변 초안"])

        st.markdown("##### ④ 승인하면")
        st.warning(pl["통과시키면"])

        if v.get("history"):
            with st.expander(f"이전 응답 기록 ({len(v['history'])})"):
                for h in v["history"]:
                    st.write(h)

        st.markdown("##### 응답")
        t1, t2, t3, t4 = st.tabs(["✅ 승인", "✏️ 수정 후 승인", "⛔ 반려", "🔁 다시 판정"])
        with t1:
            st.caption("초안을 그대로 발송합니다.")
            if st.button("승인하고 발송", type="primary", key="approve"):
                agent.resume(app, tid, {"action": "approve"})
                st.session_state["flash"] = f"{v['inquiry']['id']} 승인 → 발송 완료"
                st.rerun()
        with t2:
            with st.form("edit"):
                text = st.text_area("발송할 본문", pl["답변 초안"], height=200)
                if st.form_submit_button("수정본으로 발송", type="primary"):
                    agent.resume(app, tid, {"action": "edit", "text": text})
                    st.session_state["flash"] = f"{v['inquiry']['id']} 수정 후 승인 → 발송 완료"
                    st.rerun()
        with t3:
            with st.form("reject"):
                reason = st.text_input("반려 사유 (필수)", placeholder="예: 개인정보 사고 의심, 보안팀 이관")
                st.caption("반려하면 발송하지 않고 '담당자 직접 처리' 목록으로 넘깁니다.")
                if st.form_submit_button("반려"):
                    if not reason.strip():
                        st.error("반려 사유를 적어 주세요. 기준을 고칠 때 근거가 됩니다.")
                    else:
                        agent.resume(app, tid, {"action": "reject", "reason": reason})
                        st.session_state["flash"] = f"{v['inquiry']['id']} 반려 → 직접 처리 목록으로 이동"
                        st.rerun()
        with t4:
            left_redo = pl["다시 판정 남은 횟수"]
            with st.form("redo"):
                instr = st.text_area("AI에게 줄 지시", placeholder="예: 차액 보상은 규정에 없으니 어렵다고 정중히 안내해")
                cat = st.selectbox("분류 바로잡기 (선택)", ["그대로"] + CATEGORIES)
                st.caption(f"남은 횟수: {left_redo} / {agent.MAX_REDO} · 다시 판정한 결과는 다시 이 목록으로 돌아옵니다.")
                if st.form_submit_button("다시 판정", disabled=left_redo <= 0):
                    d = {"action": "redo", "instruction": instr}
                    if cat != "그대로":
                        d["category"] = cat
                    agent.resume(app, tid, d)
                    st.session_state["flash"] = f"{v['inquiry']['id']} 다시 판정 완료 → 결과를 확인하세요"
                    st.rerun()

# ───────────── 📥 문의 접수 ─────────────
elif page.startswith("📥"):
    st.header("문의 접수")
    st.caption("문의를 넣으면 에이전트가 분류 → 규정 검색 → 답변 작성 → 멈춤 기준 검사를 거쳐, "
               "기준에 걸리지 않은 건은 바로 발송하고 걸린 건은 승인 대기로 보냅니다.")
    c1, c2 = st.columns([1, 1])
    if c1.button(f"샘플 문의 {len(inquiries)}건 모두 처리", type="primary"):
        run_batch(inquiries)
    with st.form("new"):
        st.markdown("**새 문의 직접 넣기**")
        name = st.text_input("고객 이름", "테스트 고객")
        text = st.text_area("문의 내용", placeholder="예: 바지 기장이 너무 길어요. 교환되나요?")
        if st.form_submit_button("처리") and text.strip():
            run_batch([{"id": f"N{datetime.now():%H%M%S}", "customer": name,
                        "email": "customer@example.com", "text": text.strip()}])
    st.markdown("##### 샘플 문의")
    st.dataframe(pd.DataFrame([{"ID": q["id"], "고객": q["customer"], "문의": q["text"],
                                "정답(사람이 봐야 하나)": "사람" if q["gold_human"] else "자동"} for q in inquiries]),
                 hide_index=True, width="stretch")

# ───────────── 📤 처리 결과 ─────────────
elif page.startswith("📤"):
    st.header("처리 결과")
    out, manual = store.outbox(), store.manual_queue()
    threads = store.list_threads()
    modes = pd.Series([r["mode"] for r in out]).value_counts().to_dict() if out else {}
    m = st.columns(5)
    m[0].metric("처리한 문의", len(threads))
    m[1].metric("자동 발송", modes.get("auto", 0))
    m[2].metric("승인 후 발송", modes.get("human-approved", 0) + modes.get("human-edited", 0),
                help=f"그대로 승인 {modes.get('human-approved', 0)} · 수정 후 승인 {modes.get('human-edited', 0)}")
    m[3].metric("반려(직접 처리)", len(manual))
    m[4].metric("승인 대기", len(pending))
    if threads:
        stopped = len(threads) - modes.get("auto", 0)
        st.caption(f"개입률 {stopped / len(threads):.0%} — 처리한 {len(threads)}건 중 {stopped}건이 사람을 거쳤습니다.")

    st.markdown("##### 발송함 (고객에게 나간 답변)")
    if out:
        label = {"auto": "🤖 자동", "human-approved": "✅ 승인", "human-edited": "✏️ 수정 승인"}
        st.dataframe(pd.DataFrame([{"ID": r["inquiry_id"], "방식": label[r["mode"]], "받는 사람": r["to_addr"],
                                    "본문": r["body"], "발송 시각": r["sent_at"]} for r in out]),
                     hide_index=True, width="stretch")
    else:
        st.caption("아직 없습니다.")
    st.markdown("##### 담당자 직접 처리 (반려)")
    if manual:
        st.dataframe(pd.DataFrame([{"ID": r["inquiry_id"], "반려 사유": r["reason"], "시각": r["at"]} for r in manual]),
                     hide_index=True, width="stretch")
    else:
        st.caption("아직 없습니다.")
    with st.expander("담당자 알림 기록"):
        for n in store.notifications():
            st.text(f"{n['at']}  {n['message']}")

# ───────────── 📊 멈춤 기준 비교 ─────────────
else:
    import eval_criteria as ev

    st.header("멈춤 기준 비교")
    st.caption("같은 17건에 기준을 바꿔 적용해 개입률·놓침·헛멈춤을 셉니다. "
               "놓침 = 사람이 봤어야 하는데 자동 발송된 건, 헛멈춤 = 볼 필요 없는데 사람에게 온 건.")
    states = ev.judged_states(inquiries)
    st.dataframe(pd.DataFrame(ev.table(states)), hide_index=True, width="stretch")

    st.markdown("##### 직접 조합해 보기")
    c1, c2 = st.columns([2, 1])
    picked = c1.multiselect("켤 기준", list(CRITERIA), default=DEFAULT_CONFIG["enabled"],
                            format_func=lambda c: f"{c} {CRITERIA[c]['name']}")
    th = c2.slider("C1 확신도 임계값", 0.0, 1.0, DEFAULT_CONFIG["conf_threshold"], 0.05)
    r = ev.evaluate(states, {"enabled": picked, "conf_threshold": th})
    k = st.columns(3)
    k[0].metric("개입률", f"{r['개입률']:.0%}", f"{r['개입']}건", delta_color="off")
    k[1].metric("놓침", len(r["놓침"]), ", ".join(r["놓침"]) or "-", delta_color="off")
    k[2].metric("헛멈춤", len(r["헛멈춤"]), ", ".join(r["헛멈춤"]) or "-", delta_color="off")
