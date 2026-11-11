"""V0.1 key tests, mapped to section 八 of the spec.

Run:  python3 -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent_brain import AgentBrain            # noqa: E402
from src.event_broker import deliver              # noqa: E402
from src.world_engine import WorldEngine          # noqa: E402
from src.orchestrator import run_simulation       # noqa: E402


def profiles():
    return {a: json.loads((ROOT / "characters" / a / "profile.json").read_text(encoding="utf-8"))
            for a in "ABC"}


def make_brain(agent: str, seed: int = 42) -> AgentBrain:
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "characters" / agent, tmp / agent)
    return AgentBrain(tmp / agent, seed=seed)


def perc(agent, tick=1, location="bar", events=None, colocated=None):
    return {"agent_id": agent, "tick": tick, "time": "21:05", "location": location,
            "colocated": colocated or [], "visible_objects": [],
            "events": events or [], "muffled": [], "world_public": {"weather": "clear"}}


HINT_EVENT = {"event_id": "E900", "tick": 1, "type": "speak", "actors": ["A"],
              "location": "bar", "topic": "mayor_hint", "claim": "mayor_hint",
              "text": "奥伦：（压低声音）镇上那笔公共资金……数目好像对不上。", "interrupted": False}


class TestIndependentCognition(unittest.TestCase):
    """测试1：同一句话，B 与 C 形成不同的理解。"""

    def test_same_words_different_minds(self):
        b, c = make_brain("B"), make_brain("C")
        b.receive(perc("B", events=[HINT_EVENT]), None)
        c.receive(perc("C", events=[HINT_EVENT]), None)
        self.assertIn("mayor_deal_risky", b.memory.state["beliefs"])       # 商人：风险/机会
        self.assertNotIn("mayor_deal_risky", c.memory.state["beliefs"])
        self.assertIn("investigation_widening", c.memory.state["beliefs"])  # 士兵：调查线索
        self.assertNotIn("investigation_widening", b.memory.state["beliefs"])
        self.assertGreater(c.memory.state["emotions"]["suspicion"],
                           0.35)  # C 的怀疑显著上升，B 没有同样的反应


class TestInformationIsolation(unittest.TestCase):
    """测试2：不在场的人不会知道现场发生了什么。"""

    def test_broker_delivers_only_to_present_agents(self):
        w = WorldEngine(profiles(), seed=1)
        w.state["agents"]["B"]["location"] = "bar"      # B 与 A 同在吧台
        w.state["agents"]["C"]["location"] = "outside"  # C 在街上
        events = [dict(HINT_EVENT, event_id="E901")]
        perceptions, records = deliver(w.snapshot(), events, ["A", "B", "C"])
        by_agent = {r["agent_id"]: r for r in records}
        self.assertIn("E901", by_agent["B"]["delivered_event_ids"])   # B 在吧台，听得到
        self.assertNotIn("E901", by_agent["C"]["delivered_event_ids"])  # C 在街上，听不到
        self.assertEqual(perceptions["C"]["events"], [])


class TestCausalPropagation(unittest.TestCase):
    """测试3：A 的话改变 B，B 的话再改变 C（且置信度衰减）。"""

    def test_chain_a_to_b_to_c(self):
        b, c = make_brain("B"), make_brain("C")
        self.assertNotIn("mayor_embezzled", c.memory.state["beliefs"])
        b.receive(perc("B", events=[HINT_EVENT]), None)
        self.assertIn("mayor_embezzled", b.memory.state["beliefs"])
        from_b = dict(HINT_EVENT, event_id="E902", actors=["B"],
                      claim="mayor_hint", topic="mayor_hint",
                      text="米拉：（压低声音）镇上那笔公共资金……数目好像对不上。")
        c.receive(perc("C", tick=2, events=[from_b]), None)
        self.assertIn("mayor_embezzled", c.memory.state["beliefs"])
        self.assertLessEqual(c.memory.state["beliefs"]["mayor_embezzled"]["confidence"],
                             b.memory.state["beliefs"]["mayor_embezzled"]["confidence"])


class TestAutonomy(unittest.TestCase):
    """测试4：行动来自效用评分而非固定脚本——情境变，行动变。"""

    def test_actions_vary_with_situation(self):
        b = make_brain("B")
        alone = b.decide(perc("B", location="tables"))
        self.assertIn(alone["verb"], {"move", "work", "observe", "listen", "wait"})
        with_a = perc("B", location="bar",
                      colocated=[{"id": "A", "name": "奥伦", "role": "酒馆老板"}])
        verbs = {b.decide(with_a)["verb"] for _ in range(1)}
        # 与老板同处时，商人至少会认真考虑开口谈生意（评分最高项为言语类的概率结构）
        scores = []
        for _ in range(6):
            bb = make_brain("B")
            scores.append(bb.decide(with_a)["verb"])
        self.assertTrue(any(v in ("speak", "ask") for v in scores),
                        f"co-located decisions never chose speech: {scores}")
        self.assertNotEqual(verbs, {"move"})


class TestMemoryConsistencyIntegration(unittest.TestCase):
    """测试5+6+7：真实多进程运行后的一致性、人格稳定与涌现链。"""

    @classmethod
    def setUpClass(cls):
        cls.run_dir = run_simulation(ticks=12, seed=42, run_name="test_run")
        cls.log = json.loads((cls.run_dir / "world_log.json").read_text(encoding="utf-8"))

    def test_memory_only_contains_delivered_events(self):
        delivered = {}
        for rec in self.log:
            for d in rec["deliveries"]:
                delivered.setdefault(d["agent_id"], set()).update(
                    d["delivered_event_ids"] + d["muffled_event_ids"])
        for a in "ABC":
            st = json.loads((self.run_dir / "characters" / a / "state.json").read_text(encoding="utf-8"))
            remembered = set(st["perceived_event_ids"])
            # 事件在其发生后的下一个周期才被投递，所以允许最后一周期事件尚未入账
            self.assertTrue(remembered <= delivered.get(a, set()) | {e["event_id"] for r in self.log for e in r["events"]},
                            f"{a} remembers undelivered events: {remembered - delivered.get(a, set())}")
            # 关键隔离断言：凡是被记住的事件，必须是投递记录里出现过的
            leaked = remembered & {e["event_id"] for r in self.log for e in r["events"]}
            self.assertTrue(leaked <= delivered.get(a, set()) | set(),
                            f"{a} leaked events: {leaked - delivered.get(a, set())}")

    def test_personality_unchanged_emotions_bounded(self):
        for a in "ABC":
            orig = profiles()[a]["personality"]
            runp = json.loads((self.run_dir / "characters" / a / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(orig, runp["personality"])
            st = json.loads((self.run_dir / "characters" / a / "state.json").read_text(encoding="utf-8"))
            for v in st["emotions"].values():
                self.assertTrue(0.0 <= v <= 1.0)

    def test_emergent_causal_chain_exists(self):
        # 链条定义：某人听到一句带 claim 的话（投递记录可查），之后在后续周期做出
        # 与该话题相关的言语/搜查/移动行动。不是脚本指定，是日志里的因果痕迹。
        heard = {}  # agent -> set of topics heard
        chain_found = False
        for rec in self.log:
            for d in rec["deliveries"]:
                for eid in d["delivered_event_ids"]:
                    for r2 in self.log:
                        for e in r2["events"]:
                            if e["event_id"] == eid and e["type"] in ("speak", "ask", "confront"):
                                heard.setdefault(d["agent_id"], set()).add(e.get("claim"))
            for a, intent in rec["intents"].items():
                topics = heard.get(a, set())
                if not topics:
                    continue
                if intent["verb"] == "search" and a == "C" and "investigation_probe" not in topics:
                    chain_found = True  # C 听了镇长相关的话之后去搜查
                if intent.get("topic") in ("denial", "mayor_hint", "letter_question",
                                           "answer_mayor", "accusation"):
                    chain_found = True  # 对听到的内容做出了话题相关的回应
        self.assertTrue(chain_found, "no hear->react chain found in 12 ticks")

    def test_interruptions_can_happen(self):
        any_interrupt = any(e.get("interrupted") for r in self.log for e in r["events"])
        self.assertTrue(any_interrupt, "expected at least one interruption in 12 ticks")


class TestResolverRules(unittest.TestCase):
    def test_illegal_long_move_rejected(self):
        w = WorldEngine(profiles(), seed=3)
        events = w.step({"A": {"verb": "move", "target_location": "outside"},  # bar->outside 不相邻
                         "B": {"verb": "wait"}, "C": {"verb": "wait"}})
        self.assertEqual(w.state["agents"]["A"]["location"], "bar")
        self.assertFalse(w.state["last_action_results"]["A"]["ok"])

    def test_speak_to_absent_target_fails(self):
        w = WorldEngine(profiles(), seed=3)
        w.step({"A": {"verb": "speak", "target": "C", "topic": "greeting", "claim": "greeting", "text": "x"},
                "B": {"verb": "wait"}, "C": {"verb": "wait"}})  # C 在 door，不在 bar
        self.assertFalse(w.state["last_action_results"]["A"]["ok"])


if __name__ == "__main__":
    unittest.main()
