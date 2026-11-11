"""Rule Provider — the V0.1 rule-based decision logic, ported to the V0.2
protocol. It is the control group for LLM experiments (task book section 5).

Differences from V0.1: perception interpretation and belief updates moved
to the Agent Runtime (agent.py); this provider is a pure function of the
DecisionContext plus its own per-agent RNG and action counters' snapshot.
The utility scoring, novelty penalties, repetition scaling and
personality gates are the V0.1 rules, unchanged in substance.
"""
from __future__ import annotations

import random

from ..plan_steps import owner_location_from_perception, step_consistent
from ..protocol import (ActionSpec, AgentDecision, DecisionContext, PlanUpdate,
                        ProviderResult)
from .base import LLMProvider

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
    "probe_source": "{name}：我把话问明白——这两天，谁进过后屋、动过那个抽屉？",
    "denial": ["{name}：我这小酒馆能有什么异常？各位喝酒就是了，别的不归我管。",
               "{name}：问几遍都一样，我什么都不知道。"],
    "bluff": "{name}：（笑）这镇上的秘密啊，我知道的可能比你们想的多一点。",
    "answer_mayor": "{name}：镇长的钱袋子紧不紧，我一个做小买卖的（看局势）……只能说，不太好看。",
    "smalltalk": "{name}：这雨下得，路都不好走了。",
    "discuss_letter": "{name}：那封信，我已经看过了。信里说的事，你打算怎么办？",
}

ADJACENCY = {
    "bar": {"tables"}, "tables": {"bar", "door", "back_room"},
    "door": {"tables", "outside"}, "back_room": {"tables"}, "outside": {"door"},
}

EXPECTED = {
    "investigation_probe": "owner_may_reveal_information",
    "letter_discussion": "owner_may_reveal_information",
    "letter_question": "owner_may_reveal_information",
    "mayor_hint": "others_react_to_rumor",
    "business_proposal": "owner_considers_deal",
    "denial": "questioner_may_back_off",
    "greeting": "conversation_continues",
}

# Plan-layer upgrade: fixed utility bonus for a candidate that
# fulfils the plan's current step (shared predicate, plan_steps.py).
# Large enough to tilt close calls toward the step the plan is
# waiting for, small enough to leave runaway scores (a pending
# question's denial, a letter confrontation) on top.
STEP_ALIGNMENT_BONUS = 0.35


class RuleProvider(LLMProvider):
    name = "rule"
    model = "rule-v0.1-port"

    def __init__(self, config, seed: int = 42):
        super().__init__(config, seed)
        self.rng = random.Random(seed + config.seed_offset)

    # ---------- utterance construction (from own knowledge only) ----------
    def _line(self, key: str, claim: str | None, counts: dict) -> str:
        val = TOPIC_LINES[key]
        if isinstance(val, list):
            n = sum(v for k, v in counts.items() if claim and k.endswith(f":{claim}"))
            val = val[n % len(val)]
        return val.format(name=self.config.name)

    def _speak(self, verb, target, line_key, claim, counts):
        return {"verb": verb, "target": target, "topic": claim, "claim": claim,
                "text": self._line(line_key, claim, counts)}

    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        cfg = self.config
        me = cfg.character_id
        P = cfg.personality
        loc = context.location
        colocated = [c["id"] for c in context.perception.get("colocated", [])]
        beliefs = {b.key: b for b in context.beliefs}
        emotions = context.emotional_state
        trust_map: dict = getattr(context, "trust", {}) or {}
        counts: dict = dict(getattr(context, "action_counts", {}) or {})
        pending = getattr(context, "pending_question", None)
        # Phase C put the plan's current step into the context; the
        # plan-layer upgrade makes this provider actually consume it
        # (candidate generation for the owner's source-hunt below, and
        # the alignment bonus before the final sort).
        plan_step = getattr(context, "plan_step", None)

        def trust_in(other: str) -> float:
            return trust_map.get(other, 0.3)

        cands: list[tuple[float, dict, str]] = []

        def sig_of(intent: dict) -> str:
            return (f"{intent.get('verb')}:{intent.get('target') or intent.get('target_location') or ''}"
                    f":{intent.get('topic') or intent.get('object') or ''}")

        def add(score: float, intent: dict, why: str):
            sig = sig_of(intent)
            n = counts.get(sig, 0)
            score -= (0.22 * n) if intent.get("verb") in ("speak", "ask", "confront", "show", "search") \
                else (0.05 * n)
            cands.append((score + self.rng.random() * 0.18, intent, why))

        # ---- movement ----
        letter_b = beliefs.get("anonymous_letter_exists")
        # sorted(): ADJACENCY values are sets, and set iteration order over
        # strings depends on PYTHONHASHSEED — that made the RNG jitter land
        # on different destinations in different processes and flipped
        # close-score decisions between runs (diagnosed in Phase B).
        # Deterministic enumeration fixes it (approved by Hu 2026-11-10).
        for dest in sorted(ADJACENCY.get(loc, ())):
            s = 0.15
            if me == "soldier" and dest in ("tables", "bar"):
                s += 0.35
            if me == "soldier" and dest == "back_room" and emotions.get("suspicion", 0) > 0.5:
                if not (letter_b and letter_b.kind == "experienced"):
                    s += 0.55
            if me == "soldier" and dest in ("bar", "tables"):
                if letter_b and letter_b.kind == "experienced" and letter_b.confidence >= 1.0:
                    s += 0.45
            if me == "merchant" and dest == "bar":
                s += 0.3 * P["sociability"]
            if me == "owner" and dest == "back_room" and context.tick % 5 == 3:
                s += 0.4
            if me == "owner" and dest != "bar":
                s -= 0.25 * P["caution"]
            add(s, {"verb": "move", "target_location": dest}, f"向 {dest} 移动更符合当前目标")

        # ---- staying ----
        add(0.30 + 0.3 * P["caution"], {"verb": "observe",
            "target": colocated[0] if colocated else None}, "谨慎使我先观察")
        add(0.22, {"verb": "listen"}, "先听听动静")
        if me == "owner" and loc == "bar":
            add(0.45, {"verb": "work", "topic": "tend_bar"}, "守着吧台是我的本分")
        if me == "merchant" and loc == "tables":
            add(0.3, {"verb": "work", "topic": "count_coins"}, "盘算一下账目")
        add(0.08, {"verb": "wait"}, "什么都不做")

        # ---- search ----
        if loc == "back_room":
            already_read = bool(letter_b and letter_b.kind == "experienced"
                                and letter_b.confidence >= 1.0)
            if me == "soldier":
                add(-0.5 if already_read else 0.5 + 0.5 * emotions.get("suspicion", 0),
                    {"verb": "search", "target_location": "back_room"},
                    "已经读到信，不必再搜" if already_read else "怀疑驱使我搜查后屋")
            elif me == "owner":
                add(0.55, {"verb": "search", "target_location": "back_room"}, "确认信还在原处")
            elif me == "merchant":
                add(-0.5 if already_read else 0.2 + 0.4 * P["curiosity"],
                    {"verb": "search", "target_location": "back_room"}, "好奇心驱使我翻翻看")

        # ---- speech ----
        mayor_b = beliefs.get("mayor_embezzled")
        for other in colocated:
            trust = trust_in(other)
            add(0.25 + 0.35 * P["sociability"],
                self._speak("speak", other, "greeting", "greeting", counts), "打个招呼维持关系")
            if pending and pending.get("from") == other:
                if me == "owner":
                    if trust > cfg.disclosure_threshold:
                        add(0.85, self._speak("speak", other, "mayor_hint", "mayor_hint", counts),
                            "我信任对方，可以暗示一点")
                    else:
                        add(0.9, self._speak("speak", other, "denial", "denial", counts),
                            "谨慎和低信任让我否认")
                elif me == "soldier":
                    add(0.7, self._speak("speak", other, "smalltalk", "smalltalk", counts), "先闲聊")
                else:
                    add(0.7, self._speak("speak", other, "answer_mayor", "answer_mayor", counts),
                        "顺着话题说镇长的付款")
            if me == "merchant":
                add(0.55 + 0.3 * P["sociability"],
                    self._speak("speak", other, "business", "business_proposal", counts),
                    "推销货物符合我的目标")
                if trust > 0.45 and beliefs.get("mayor_business_deal"):
                    add(0.6, self._speak("speak", other, "mayor_business", "mayor_late_payment", counts),
                        "信任够了，透露镇长拖款的事")
                if P["curiosity"] > 0.8 and not letter_b:
                    add(0.35, self._speak("speak", other, "bluff", "bluff_hint", counts),
                        "虚张声势钓一点消息")
            if me == "soldier" and other == "owner":
                add(0.55 + 0.4 * P["impulsiveness"],
                    self._speak("ask", other, "investigate", "investigation_probe", counts),
                    "调查任务要求我盘问老板")
                if letter_b and letter_b.confidence >= 0.9:
                    if letter_b.kind == "experienced":
                        add(0.95, self._speak("confront", other, "discuss_letter", "letter_discussion", counts),
                            "我亲读过信，直接摊牌")
                    else:
                        add(0.95, self._speak("ask", other, "letter_question", "letter_question", counts),
                            "我听说了信的存在，追问老板")
                if mayor_b and mayor_b.confidence >= 0.55:
                    add(0.5 + 0.5 * P["impulsiveness"],
                        self._speak("confront", other, "accusation", "mayor_hint", counts),
                        "线索指向镇长，当面指控")
            if me != "soldier" and mayor_b and mayor_b.confidence >= 0.45:
                thresh = cfg.disclosure_threshold
                if trust > thresh - 0.25 or P["impulsiveness"] > 0.7:
                    add(0.5, self._speak("speak", other, "mayor_hint", "mayor_hint", counts),
                        "隐晦地提一嘴镇长的钱")
            if letter_b and me != "owner" and letter_b.confidence >= 0.9 and other == "owner":
                if letter_b.kind == "experienced":
                    add(0.85, self._speak("speak", other, "discuss_letter", "letter_discussion", counts),
                        "亲眼看过信，摊牌讨论")
                else:
                    add(0.8, self._speak("ask", other, "letter_question", "letter_question", counts),
                        "追问信的下落")

        # ---- plan-driven probing: the owner hunting the letter's source ----
        # Interpretation can set find_letter_source / find_who_talked,
        # but the base candidate set gave the owner no questioning
        # actions at all — the step-1 audit's finding 3: an intent with
        # no behaviour able to execute it (its M1a was 0.240). While
        # one of those steps is the plan's current step, the owner
        # questions whoever is with him, with existing verbs only.
        if me == "owner" and plan_step and plan_step.get("step") in (
                "find_letter_source", "find_who_talked"):
            for other in colocated:
                add(0.55, self._speak("ask", other, "probe_source",
                                      "letter_question", counts),
                    "查清是谁把信的风声漏出去的")
                if plan_step["step"] == "find_who_talked":
                    add(0.5, self._speak("confront", other, "probe_source",
                                         "letter_question", counts),
                        "质问到底是谁在外头传的话")

        # ---- show the letter (owner only, principle-gated) ----
        if me == "owner" and letter_b:
            for other in colocated:
                if trust_in(other) > cfg.disclosure_threshold:
                    add(0.7, {"verb": "show", "object": "anonymous_letter", "target": other},
                        "信任超过了我的披露门槛")

        # ---- refuse ----
        if pending and P["caution"] > 0.7:
            add(0.45, {"verb": "refuse", "target": pending.get("from"),
                       "topic": pending.get("topic")}, "我不想再继续这个话题")

        # ---- plan-step alignment bonus (selection side of the upgrade) ----
        # The pointer is advanced by the agent from action RESULTS (a
        # fact about the world); this is the matching selection-side
        # half: a candidate the shared predicate (plan_steps.py — the
        # same table the audit scores with) judges consistent with the
        # plan's current step gets a fixed utility bonus, so the choice
        # itself leans into the step the plan is waiting for. Steps the
        # predicate cannot judge here (None) earn nothing. Applied to
        # the finished candidate list, before the final sort; the bonus
        # is a pure function of context, so determinism is untouched.
        if plan_step and plan_step.get("step"):
            owner_loc = owner_location_from_perception(
                context.perception, loc)
            bumped: list[tuple[float, dict, str]] = []
            for score, intent, why in cands:
                action = {"type": intent.get("verb"),
                          "target": intent.get("target"),
                          "target_location": intent.get("target_location"),
                          "object": intent.get("object"),
                          "topic": intent.get("topic"),
                          "claim": intent.get("claim")}
                if step_consistent(plan_step["step"], action,
                                   agent_loc=loc,
                                   owner_loc=owner_loc) is True:
                    score += STEP_ALIGNMENT_BONUS
                bumped.append((score, intent, why))
            cands = bumped

        cands.sort(key=lambda x: -x[0])
        score, intent, why = cands[0]
        verb = intent["verb"]
        claim = intent.get("claim")
        # V0.2-A: report the goal this decision serves — the active
        # plan's goal when it is one of the character's configured
        # goals, else the highest-priority active goal (the context
        # presents goals priority-first). Always a real goal key, so
        # the runtime's ownership validation passes by construction.
        goal_keys = {g.key for g in context.active_goals}
        selected_goal = None
        if plan_step and plan_step.get("goal") in goal_keys:
            selected_goal = plan_step["goal"]
        else:
            for g in context.active_goals:
                if g.status == "active":
                    selected_goal = g.key
                    break
        decision = AgentDecision(
            intent=f"{verb}:{claim or intent.get('topic') or intent.get('target_location') or intent.get('target') or ''}",
            target=intent.get("target"),
            action=ActionSpec(type=verb, target=intent.get("target"),
                              target_location=intent.get("target_location"),
                              object=intent.get("object"), content=intent.get("text"),
                              claim=claim, topic=intent.get("topic")),
            reasoning_summary=f"[rule] {why}（效用评分 {score:.2f}，含人格与新颖性调整）",
            expected_outcome=EXPECTED.get(claim or "", "situation_develops"),
            confidence=round(min(0.95, max(0.05, score)), 2),
            plan_update=PlanUpdate(status="continue"),
            selected_goal_id=selected_goal,
        )
        return ProviderResult(decision=decision, provider=self.name, model=self.model,
                              raw_output=decision.model_dump_json())
