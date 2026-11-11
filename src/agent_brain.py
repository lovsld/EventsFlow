"""Agent Brain — runs INSIDE one agent process, for ONE agent only.

DecisionProvider interface:
    decide(perception, memory) -> intent dict

Providers:
- RuleBasedProvider (default, used in V0.1 runs): personality-weighted
  utility scoring over candidate actions. Every agent process constructs
  its own provider with its own profile, its own rng (seed = run seed +
  per-agent offset) and its own MemoryManager. No state is shared between
  agent processes; they cannot even address each other's memory.
- LLMProvider: plug point for a real model endpoint. Not used in V0.1
  because this sandbox has no model API credentials; the interface is the
  same dict-in/dict-out contract, so swapping it in changes nothing else.
  (See REPORT.md, "Limitations".)

The brain only ever sees: its profile, its private state, and the
perception dict the Event Broker delivered. It has no reference to the
World Engine, the world log, or other agents' files.
"""
from __future__ import annotations

import random
from pathlib import Path

from .memory_manager import MemoryManager

TOPIC_LINES = {
    "greeting": "{name}：晚上好，雨夜里酒馆可是个好去处。",
    "business": ["{name}：老板，我手里有批货，镇上要是有销路，咱们都有赚头。",
                 "{name}：那批货的事，老板真不考虑？价格好商量。",
                 "{name}：我也不绕了——货压在手里，镇上只有你这儿能走量。"],
    "mayor_business": "{name}：说句实在话，镇长那笔粮食生意，钱拖了我两个月才结清。",
    "mayor_hint": "{name}：（压低声音）镇上那笔公共资金……数目好像对不上。",
    "accusation": "{name}：别绕弯子了！镇长是不是吞了那笔钱？你是不是知道什么？",
    "investigate": ["{name}：老板，我例行问一句——这几天酒馆有没有什么异常的人或事？",
                    "{name}：再问一次，这几天后屋那边，有人动过什么吗？",
                    "{name}：老板，你越是不说，我越得查清楚。"],
    "letter_question": "{name}：我听说……有一封信？一封关于镇长的信，是不是在你这儿？",
    "denial": ["{name}：我这小酒馆能有什么异常？各位喝酒就是了，别的不归我管。",
               "{name}：问几遍都一样，我什么都不知道。"],
    "bluff": "{name}：（笑）这镇上的秘密啊，我知道的可能比你们想的多一点。",
    "answer_mayor": "{name}：镇长的钱袋子紧不紧，我一个做小买卖的（看局势）……只能说，不太好看。",
    "smalltalk": "{name}：这雨下得，路都不好走了。",
    "discuss_letter": "{name}：那封信，我已经看过了。信里说的事，你打算怎么办？",
}


class LLMProvider:  # pragma: no cover - documented plug point
    """Contract for a future per-agent model backend.

    A real implementation must accept the same context dict (profile +
    private state summary + perception) and return one intent dict, and it
    must be instantiated separately per agent process (ideally with
    independent sessions). It must never receive other agents' state.
    """

    def __init__(self, *a, **kw):
        raise NotImplementedError(
            "No model endpoint configured in this environment; "
            "RuleBasedProvider is the V0.1 default."
        )


class RuleBasedProvider:
    def __init__(self, profile: dict, memory: MemoryManager, seed: int):
        self.p = profile
        self.m = memory
        self.rng = random.Random(seed + profile["seed_offset"])
        self.last_action_sig = ""
        self.repeat_count = 0

    # ---------------- perception -> memory / beliefs / emotion ----------------
    def receive(self, perception: dict, action_result: dict | None) -> None:
        st = self.m.state
        tick = perception["tick"]
        st["location"] = perception["location"]

        if action_result and action_result.get("found"):
            for obj in action_result["found"]:
                if obj == "anonymous_letter" and self.p["id"] != "A":
                    self.m.remember(tick, "experienced",
                                    "我在后屋的抽屉里发现并读到一封匿名信，信中称镇长私吞了公共资金。", None)
                    self.m.update_belief("anonymous_letter_exists", 1.0, "experienced",
                                         "匿名信真实存在（我亲眼读到）")
                    self.m.update_belief("mayor_embezzled", 0.6, "heard",
                                         "信中称镇长私吞公共资金")
                    st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.25)
                    st["plan"] = ["decide_what_to_do_about_letter"]
                elif obj == "anonymous_letter":
                    self.m.remember(tick, "experienced", "我检查了抽屉，匿名信还在原处。", None)

        for ev in perception["events"]:
            self._perceive_event(ev, tick)
        for mf in perception.get("muffled", []):
            st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.05)
            self.m.remember(tick, "inferred",
                            f"隔墙隐约听到 {','.join(mf['actors'])} 在{mf['location']}说话，听不清内容。", mf["event_id"])
        self.m.save_state()

    def _perceive_event(self, ev: dict, tick: int) -> None:
        st = self.m.state
        actor = ev["actors"][0] if ev.get("actors") else None
        etype = ev["type"]
        if actor == self.p["id"]:
            return  # own actions were already chosen deliberately; outcome comes via action_result

        if etype == "move":
            self.m.remember(tick, "experienced", f"{actor} 来到了/去到了 {ev.get('to')}。", ev["event_id"])
        elif etype in ("speak", "ask", "confront"):
            text = ev.get("text") or f"（{actor} 谈论 {ev.get('topic')}）"
            kind = "experienced"  # heard it with own ears; the CONTENT is only 'heard'
            self.m.remember(tick, kind, f"{actor} 说：{text}", ev["event_id"])
            st.setdefault("utterances_heard", []).append(
                {"tick": tick, "from": actor, "topic": ev.get("topic"),
                 "claim": ev.get("claim"), "kind": etype})
            if etype in ("ask", "confront"):
                st["pending_question"] = {"from": actor, "topic": ev.get("topic"), "tick": tick}
            self._interpret(ev)
        elif etype == "show" and ev.get("object") == "anonymous_letter":
            self.m.remember(tick, "experienced", f"{actor} 把一封匿名信拿给我看了。", ev["event_id"])
            self.m.update_belief("anonymous_letter_exists", 1.0, "experienced", "我亲眼见到了匿名信")
            self.m.update_belief("mayor_embezzled", 0.55, "heard", "信中称镇长私吞公共资金")
            self.m.adjust_trust(actor, +0.1)
        elif etype == "search":
            self.m.remember(tick, "experienced", f"{actor} 在 {ev.get('location')} 翻找着什么。", ev["event_id"])
            st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.1)
        elif etype == "refuse":
            self.m.remember(tick, "experienced", f"{actor} 拒绝继续谈 {ev.get('topic')}。", ev["event_id"])
            self.m.adjust_trust(actor, -0.05)
        elif etype == "ambient":
            self.m.remember(tick, "experienced", f"环境：{ev.get('text')}", ev["event_id"])

    # -------- the same words, understood differently (personality filter) --------
    def _interpret(self, ev: dict) -> None:
        st = self.m.state
        claim = ev.get("claim")
        speaker = ev["actors"][0]
        me = self.p["id"]
        P = self.p["personality"]
        if claim in ("mayor_hint", "mayor_late_payment", "answer_mayor"):
            base = 0.5 if claim != "mayor_hint" else 0.45
            self.m.update_belief("mayor_embezzled", base, "heard",
                                 f"{speaker} 的话暗示镇长财务有问题")
            if me == "B":
                # 商人视角：先算风险与机会
                self.m.update_belief("mayor_deal_risky", 0.65, "inferred",
                                     "和镇长做生意可能有回款风险，但消息本身值钱")
                st["plan"] = ["probe_more_mayor_news", "protect_wealth"]
                self.m.remember(st["tick_now"] if "tick_now" in st else 0, "inferred",
                                "镇长若真出事，货款要小心；这条消息也许能换人情。", None)
            elif me == "C":
                # 士兵视角：这是调查线索，且动摇忠诚
                st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.2)
                self.m.update_belief("investigation_widening", 0.6, "inferred",
                                     "事情可能不止酒馆老板，镇长本人也值得查")
                st["plan"] = ["press_for_evidence"]
            elif me == "A":
                # 老板视角：别人在谈镇长的钱 = 风声可能走漏
                st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.3)
                self.m.update_belief("rumor_spreading", 0.55, "inferred",
                                     "关于镇长资金的风声可能正在扩散")
                st["plan"] = ["find_letter_source", "stay_calm"]
            self.m.adjust_trust(speaker, +0.03 * P["sociability"])
        elif claim == "investigation_probe":
            if me == "A":
                n = sum(1 for u in st.get("utterances_heard", [])
                        if u["from"] == speaker and u["claim"] == claim)
                scale = 1.0 / max(1, n)  # 同样的问题问第二遍，冲击减半
                st["emotions"]["arousal"] = min(1.0, st["emotions"]["arousal"] + 0.25 * scale)
                st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.2 * scale)
                self.m.update_belief("being_investigated", 0.8, "inferred",
                                     "镇长的卫兵在查我，可能和那封信有关")
                st["plan"] = ["deny_and_observe", "protect_tavern"]
            self.m.adjust_trust(speaker, -0.02)
        elif claim == "letter_question":
            if me == "A":
                st["emotions"]["arousal"] = min(1.0, st["emotions"]["arousal"] + 0.35)
                self.m.update_belief("letter_known_by_others", 0.85, "inferred",
                                     "已经有人知道信在我这里（或在诈我）")
                st["plan"] = ["deny_letter", "find_who_talked"]
            else:
                self.m.update_belief("letter_with_A", 0.5, "inferred", "信可能在老板手里")
        elif claim == "business_proposal":
            if me == "A":
                n = sum(1 for u in st.get("utterances_heard", [])
                        if u["from"] == speaker and u["claim"] == claim)
                self.m.adjust_trust(speaker, +0.05 / max(1, n))  # 同样的推销，越听越不加分
                self.m.update_belief("business_opportunity", 0.5, "heard", "商人想谈一笔合作")
            elif me == "C":
                st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.05)
        elif claim == "denial":
            if me == "C":
                # 正直的士兵对搪塞的反应取决于已掌握的线索
                ev_belief = self.m.belief("mayor_embezzled")
                if ev_belief and ev_belief["confidence"] >= 0.5:
                    st["emotions"]["suspicion"] = min(1.0, st["emotions"]["suspicion"] + 0.2)
                    self.m.update_belief("A_hiding_something", 0.65, "inferred",
                                         "老板的否认和已知线索对不上，他在藏事")
                else:
                    self.m.adjust_trust(speaker, +0.04)
            elif me == "B":
                self.m.update_belief("A_cautious", 0.7, "inferred", "老板口风很紧，是个谨慎人")
        elif claim == "letter_discussion":
            if me == "B":
                # 旁观者听见"信我看过了"——只获得传闻级认知，没有亲见
                self.m.update_belief("anonymous_letter_exists", 0.7, "heard",
                                     f"{speaker} 当众说他已经看过一封关于镇长的信")
                st["plan"] = ["assess_letter_rumor_value"]
            if me == "A":
                st["emotions"]["arousal"] = min(1.0, st["emotions"]["arousal"] + 0.2)
                self.m.update_belief("letter_known_by_others", 0.9, "inferred",
                                     "对方已经亲眼看过那封信了")
                st["plan"] = ["assess_damage", "protect_tavern"]
        elif claim == "bluff_hint":
            # 听者无法知道这是不是虚张声势，只能按各自性格估
            conf = 0.3 + 0.3 * P["curiosity"]
            self.m.update_belief("speaker_knows_secret", round(conf, 2), "inferred",
                                 f"{speaker} 暗示自己知道镇上的秘密（真假未知）")

    # ---------------- decision: utility scoring, no script ----------------
    def decide(self, perception: dict) -> dict:
        st = self.m.state
        st["tick_now"] = perception["tick"]
        P = self.p["personality"]
        me = self.p["id"]
        loc = perception["location"]
        colocated = [c["id"] for c in perception["colocated"]]
        cands: list[tuple[float, dict]] = []

        counts = st.setdefault("action_counts", {})

        def sig_of(intent: dict) -> str:
            return (f"{intent.get('verb')}:{intent.get('target') or intent.get('target_location') or ''}"
                    f":{intent.get('topic') or intent.get('object') or ''}")

        def add(score: float, intent: dict):
            sig = sig_of(intent)
            n = counts.get(sig, 0)
            # 累计新颖性惩罚：同一件事做得越多越不想再做（言语/展示/搜查尤其如此）
            score -= (0.22 * n) if intent.get("verb") in ("speak", "ask", "confront", "show", "search") else (0.05 * n)
            cands.append((score + self.rng.random() * 0.18, intent))

        # ---- movement: go where my goals point ----
        from .perception import ADJACENCY
        for dest in ADJACENCY.get(loc, set()):
            s = 0.15
            if me == "C" and dest in ("tables", "bar"):
                s += 0.35  # toward the investigation target
            if me == "C" and dest == "back_room" and st["emotions"]["suspicion"] > 0.5:
                lb = self.m.belief("anonymous_letter_exists")
                if not (lb and lb["kind"] == "experienced"):
                    s += 0.55  # 还没找到证据时才想去后屋；找到了就该回去对质
            if me == "C" and dest in ("bar", "tables"):
                lb = self.m.belief("anonymous_letter_exists")
                if lb and lb["kind"] == "experienced" and lb["confidence"] >= 1.0:
                    s += 0.45  # 手里有了信的内容，回到人前去摊牌
            if me == "B" and dest == "bar":
                s += 0.3 * P["sociability"]  # toward the owner / people
            if me == "A" and dest == "back_room" and perception["tick"] % 5 == 3:
                s += 0.4  # periodically check the letter is safe
            if me == "A" and dest != "bar":
                s -= 0.25 * P["caution"]  # owner prefers staying at the bar
            add(s, {"verb": "move", "target_location": dest})

        # ---- staying actions ----
        add(0.30 + 0.3 * P["caution"], {"verb": "observe", "target": colocated[0] if colocated else None})
        add(0.22, {"verb": "listen"})
        if me == "A" and loc == "bar":
            add(0.45, {"verb": "work", "topic": "tend_bar"})
        if me == "B" and loc == "tables":
            add(0.3, {"verb": "work", "topic": "count_coins"})
        add(0.08, {"verb": "wait"})

        # ---- search (only meaningful in back_room) ----
        letter_b = self.m.belief("anonymous_letter_exists")
        if loc == "back_room":
            already_read = bool(letter_b and letter_b["kind"] == "experienced"
                                and letter_b["confidence"] >= 1.0)
            if me == "C":
                # 已经亲眼读到信的人不会反复搜同一个抽屉——该回去对质了
                add(-0.5 if already_read else 0.5 + 0.5 * st["emotions"]["suspicion"],
                    {"verb": "search", "target_location": "back_room"})
            elif me == "A":
                add(0.55, {"verb": "search", "target_location": "back_room"})
            elif me == "B":
                add(-0.5 if already_read else 0.2 + 0.4 * P["curiosity"],
                    {"verb": "search", "target_location": "back_room"})

        # ---- speech with co-located agents ----
        mayor_b = self.m.belief("mayor_embezzled")
        pending = st.pop("pending_question", None)
        for other in colocated:
            trust = self.m.trust_in(other)
            add(0.25 + 0.35 * P["sociability"],
                self._speak(other, "greeting", "greeting"))
            if pending and pending.get("from") == other:
                if me == "A":
                    # 谨慎的老板默认否认，除非已非常信任对方
                    if trust > self.p["disclosure_threshold"]:
                        add(0.85, self._speak(other, "mayor_hint", "mayor_hint"))
                    else:
                        add(0.9, self._speak(other, "denial", "denial"))
                elif me == "C":
                    add(0.7, self._speak(other, "smalltalk", "smalltalk"))
                else:
                    add(0.7, self._speak(other, "answer_mayor", "answer_mayor"))
            if me == "B":
                add(0.55 + 0.3 * P["sociability"], self._speak(other, "business", "business_proposal"))
                if trust > 0.45 and self.m.belief("mayor_business_deal"):
                    add(0.6, self._speak(other, "mayor_business", "mayor_late_payment"))
                if P["curiosity"] > 0.8 and not letter_b:
                    add(0.35, self._speak(other, "bluff", "bluff_hint"))  # B may bluff to fish for info
            if me == "C" and other == "A":
                add(0.55 + 0.4 * P["impulsiveness"], self._ask(other, "investigate", "investigation_probe"))
                if letter_b and letter_b["confidence"] >= 0.9:
                    if letter_b["kind"] == "experienced":
                        add(0.95, self._confront(other, "discuss_letter", "letter_discussion"))
                    else:
                        add(0.95, self._ask(other, "letter_question", "letter_question"))
                if mayor_b and mayor_b["confidence"] >= 0.55:
                    add(0.5 + 0.5 * P["impulsiveness"], self._confront(other, "accusation", "mayor_hint"))
            if me != "C" and mayor_b and mayor_b["confidence"] >= 0.45:
                # 隐晦地提一嘴，前提是没那么怕；A 的门槛最高（原则过滤）
                thresh = self.p["disclosure_threshold"]
                if trust > thresh - 0.25 or P["impulsiveness"] > 0.7:
                    add(0.5, self._speak(other, "mayor_hint", "mayor_hint"))
            if letter_b and me != "A" and letter_b["confidence"] >= 0.9 and other == "A":
                if letter_b["kind"] == "experienced":
                    # 亲眼看过信的人不再问"有没有信"，而是摊牌讨论
                    add(0.85, self._speak(other, "discuss_letter", "letter_discussion"))
                else:
                    add(0.8, self._ask(other, "letter_question", "letter_question"))

        # ---- show the letter (A only; principle-gated) ----
        if me == "A" and letter_b:
            for other in colocated:
                if self.m.trust_in(other) > self.p["disclosure_threshold"]:
                    add(0.7, {"verb": "show", "object": "anonymous_letter", "target": other})

        # ---- refuse to continue a probing topic ----
        if pending and P["caution"] > 0.7:
            add(0.45, {"verb": "refuse", "target": pending.get("from"), "topic": pending.get("topic")})

        cands.sort(key=lambda x: -x[0])
        score, intent = cands[0]
        sig = sig_of(intent)
        counts[sig] = counts.get(sig, 0) + 1
        self.last_action_sig = sig
        st["plan"] = [f"{intent.get('verb')}:{intent.get('topic') or intent.get('target_location') or intent.get('target') or ''}"]
        self.m.save_state()
        return intent

    # ---------------- utterance construction (from own knowledge only) ----------------
    def _line(self, key: str, claim: str | None = None) -> str:
        val = TOPIC_LINES[key]
        if isinstance(val, list):
            counts = self.m.state.get("action_counts", {})
            n = sum(v for k, v in counts.items() if claim and k.endswith(f":{claim}"))
            val = val[n % len(val)]  # 同一个话题再开口，措辞会变
        return val.format(name=self.p["name"])

    def _speak(self, target, line_key, claim):
        return {"verb": "speak", "target": target, "topic": claim, "claim": claim,
                "text": self._line(line_key, claim)}

    def _ask(self, target, line_key, claim):
        return {"verb": "ask", "target": target, "topic": claim, "claim": claim,
                "text": self._line(line_key, claim)}

    def _confront(self, target, line_key, claim):
        return {"verb": "confront", "target": target, "topic": claim, "claim": claim,
                "text": self._line(line_key, claim)}


class AgentBrain:
    """Thin wrapper used by agent_process; keeps provider swappable."""

    def __init__(self, char_dir: Path, seed: int):
        import json
        profile = json.loads((Path(char_dir) / "profile.json").read_text(encoding="utf-8"))
        self.profile = profile
        self.memory = MemoryManager(Path(char_dir))
        self.provider = RuleBasedProvider(profile, self.memory, seed)

    def receive(self, perception, action_result):
        self.provider.receive(perception, action_result)

    def decide(self, perception):
        return self.provider.decide(perception)
