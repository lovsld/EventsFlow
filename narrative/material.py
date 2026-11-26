"""Narrative Material Adapter (Phase 1 / V1.0, task 2).

The Writer must never read raw event material directly. This
adapter classifies every NarrativeEvent's source observation into
a material class and renders the ONLY text the Writer may use:

- DIALOGUE   (speak/ask/confront with real text): quotable,
             verbatim text preserved.
- ACTION     (move/search/observe/listen/work/speak_failed):
             NOT quotable; rendered as a natural-language action
             description from structured fields. The raw template
             summary ("search @ oncall") never reaches the Writer.
- OBSERVATION(ambient texture): not quotable as dialogue; usable
             as scene texture by paraphrase.
- STATE      (session_start/session_end): not quotable; rendered
             as an arrival/departure description.
- INFERENCE / SYSTEM / DEBUG: never produced from observations;
             reserved classes that the Writer input filter drops
             unconditionally (defence in depth).

Every material carries provenance: SOURCE_BASED (dialogue/fact),
RENDERED (adapter-generated description of a source event), and
its source event id. Classification is deterministic and derived
from fields the Observer already produces (type / text /
features / fields) — no new event taxonomy is invented.
"""
from __future__ import annotations

DIALOGUE = "DIALOGUE"
ACTION = "ACTION"
OBSERVATION = "OBSERVATION"
STATE = "STATE"
INFERENCE = "INFERENCE"
SYSTEM = "SYSTEM"
DEBUG = "DEBUG"

SPEECH_TYPES = {"speak", "ask", "confront"}
ACTION_TYPES = {"move", "search", "observe", "listen", "work",
                "speak_failed"}
STATE_TYPES = {"session_start", "session_end"}

_ACTION_VERB = {
    "move": "来到",
    "observe": "观察四周",
    "listen": "侧耳倾听",
    "work": "忙碌着",
    "search": "搜寻",
}


def classify(obs: dict) -> str:
    """Material class for one observation (deterministic)."""
    t = obs.get("type", "")
    if t in SPEECH_TYPES and (obs.get("text") or "").strip():
        return DIALOGUE
    if t == "ambient" and (obs.get("text") or "").strip():
        return OBSERVATION
    if t in STATE_TYPES:
        return STATE
    if t in ACTION_TYPES or t in SPEECH_TYPES:
        # speech type without text (muffled/unrecorded) degrades
        # to ACTION: it happened, but there is nothing to quote.
        return ACTION
    return SYSTEM  # unknown type: quarantined, never quoted


def _names_for(obs: dict, names: dict) -> str:
    actors = obs.get("actors") or []
    return "、".join(names.get(a, a) for a in actors) or "有人"


def render_action(obs: dict, names: dict) -> str:
    """Natural-language description of a non-dialogue event.

    Built only from structured fields; location and object ids
    are kept verbatim (the Writer localises them, as it already
    does for scene settings) — the adapter invents nothing.
    """
    t = obs.get("type", "")
    who = _names_for(obs, names)
    loc = obs.get("location") or ""
    fields = obs.get("fields") or {}
    if t == "move":
        dest = fields.get("to") or loc
        return f"{who}来到{dest}"
    if t == "search":
        found = obs.get("found") or fields.get("found") or []
        base = f"{who}在{loc}搜寻"
        if found:
            base += f"，找到了{'、'.join(found)}"
        return base
    if t == "speak_failed":
        target = fields.get("target")
        base = f"{who}试图开口"
        if target:
            base += f"，想对{names.get(target, target)}说话"
        base += "，却没能说出话"
        if fields.get("reason") == "target_left":
            base += "（对方已经离开）"
        return base
    if t in _ACTION_VERB:
        return f"{who}在{loc}{_ACTION_VERB[t]}"
    if t in SPEECH_TYPES:
        return f"{who}在{loc}说话，但内容没有被记录下来"
    return f"{who}在{loc}有所动作"


def render_state(obs: dict, names: dict) -> str:
    who = _names_for(obs, names)
    loc = obs.get("location") or ""
    if obs.get("type") == "session_start":
        return f"{who}在{loc}聚到一起"
    return f"{who}离开{loc}，各自散去"


def adapt_event(ne: dict, obs: dict, names: dict) -> dict:
    """One Writer-ready material record for a NarrativeEvent."""
    cls = classify(obs)
    event_id = ne["source_event_ids"][0]
    if cls == DIALOGUE:
        text = (obs.get("text") or "").strip()
        provenance = "SOURCE_BASED"
    elif cls == ACTION:
        text = render_action(obs, names)
        provenance = "RENDERED"
    elif cls == STATE:
        text = render_state(obs, names)
        provenance = "RENDERED"
    elif cls == OBSERVATION:
        text = (obs.get("text") or "").strip()
        provenance = "SOURCE_BASED"
    else:  # SYSTEM / INFERENCE / DEBUG: quarantined
        text = ""
        provenance = "QUARANTINED"
    return {"ne_id": ne["narrative_event_id"], "event_id": event_id,
            "tick": ne.get("tick"), "material_class": cls,
            "quotable": cls == DIALOGUE, "text": text,
            "provenance": provenance,
            "importance": ne.get("narrative_importance"),
            "functions": ne.get("dramatic_function", [])}


def writer_materials(nes: list[dict], obs_by_id: dict,
                     names: dict) -> list[dict]:
    """Adapt a list of NarrativeEvents; quarantined material is
    dropped (never handed to the Writer)."""
    out = []
    for ne in nes:
        obs = obs_by_id[ne["source_event_ids"][0]]
        m = adapt_event(ne, obs, names)
        if m["provenance"] != "QUARANTINED":
            out.append(m)
    return out


def template_summaries(observations: list[dict]) -> set[str]:
    """The log-style template strings build.py used to emit for
    text-less events ("search @ oncall") — the leakage signature.
    """
    return {f"{o.get('type')} @ {o.get('location') or ''}"
            for o in observations if not (o.get("text") or "")}


def detect_log_leakage(paragraphs: list[dict],
                       observations: list[dict]) -> list[dict]:
    """Quoted spans in the novel that are system log strings.

    A leak is a quotation span (「」/“”/"") that equals a template
    summary or matches the `word @ word` log pattern. Returns one
    record per leaked span.
    """
    import re
    templates = template_summaries(observations)
    quote_re = re.compile(r"「([^」]+)」|“([^”]+)”|\"([^\"]+)\"")
    log_re = re.compile(r"^[a-z][a-z_]+ @ [a-z_0-9]+$")
    leaks = []
    for p in paragraphs:
        for m in quote_re.finditer(p.get("text", "")):
            span = next(g for g in m.groups() if g)
            if span in templates or log_re.match(span.strip()):
                leaks.append({"span": span,
                              "chapter_id": p.get("chapter_id"),
                              "event_ids": p.get("event_ids", [])})
    return leaks
