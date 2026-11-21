"""V0.3 story persistence: a SEPARATE story.sqlite3 per run
(migration plan §4). The frozen acp.sqlite3 schema is untouched;
story state is derived bookkeeping and can always be rebuilt by
replaying world_log through the Story Engine.

SQLAlchemy 2.x ORM, same stack as v02.persistence. Schema versioning
via a schema_version table (create_all + version row); Alembic is
deferred per Hu's Phase 0 decision.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import (Column, Float, Integer, String, Text,
                        create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

from v03.story.models import StoryState

SCHEMA_VERSION = 2
Base = declarative_base()


class SchemaVersionRow(Base):
    __tablename__ = "schema_version"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)


class StoryRow(Base):
    __tablename__ = "stories"
    story_id = Column(String, primary_key=True)
    title = Column(String)
    genre = Column(String)
    theme = Column(String)
    central_question = Column(Text)
    state_json = Column(Text)      # full StoryState snapshot


class ThreadRow(Base):
    __tablename__ = "story_threads"
    thread_id = Column(String, primary_key=True)
    story_id = Column(String, index=True)
    title = Column(String)
    status = Column(String, index=True)
    progress = Column(Float)
    last_developed_at = Column(Integer)
    thread_json = Column(Text)


class ThreadDependencyRow(Base):
    __tablename__ = "thread_dependencies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, index=True)
    depends_on = Column(String)


class ConflictRow(Base):
    __tablename__ = "character_conflicts"
    conflict_id = Column(String, primary_key=True)
    story_id = Column(String, index=True)
    conflict_type = Column(String)
    status = Column(String, index=True)
    severity = Column(Float)
    escalation = Column(Float)
    conflict_json = Column(Text)


class CandidateRow(Base):
    __tablename__ = "event_candidates"
    candidate_id = Column(String, primary_key=True)
    story_id = Column(String, index=True)
    source_type = Column(String)
    kind = Column(String)
    created_at = Column(Integer)
    passed = Column(Integer)
    candidate_json = Column(Text)


class StoryStore:
    def __init__(self, sqlite_path: str | Path):
        self.path = Path(sqlite_path)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as s:
            if not s.query(SchemaVersionRow).first():
                s.add(SchemaVersionRow(id=1, version=SCHEMA_VERSION))
                s.commit()

    def save(self, state: StoryState) -> None:
        with self.Session() as s:
            s.merge(StoryRow(
                story_id=state.story_id, title=state.title,
                genre=state.genre, theme=state.theme,
                central_question=state.central_question,
                state_json=state.model_dump_json()))
            for t in state.threads.values():
                s.merge(ThreadRow(
                    thread_id=t.thread_id, story_id=state.story_id,
                    title=t.title, status=t.status,
                    progress=t.progress,
                    last_developed_at=t.last_developed_at,
                    thread_json=t.model_dump_json()))
                s.query(ThreadDependencyRow).filter_by(
                    thread_id=t.thread_id).delete()
                for dep in t.dependencies:
                    s.add(ThreadDependencyRow(thread_id=t.thread_id,
                                              depends_on=dep))
            for c in state.conflicts.values():
                s.merge(ConflictRow(
                    conflict_id=c.conflict_id, story_id=state.story_id,
                    conflict_type=c.conflict_type, status=c.status,
                    severity=c.severity, escalation=c.escalation,
                    conflict_json=c.model_dump_json()))
            s.commit()

    def save_candidates(self, story_id: str, candidates) -> None:
        with self.Session() as s:
            for cand in candidates:
                s.merge(CandidateRow(
                    candidate_id=cand.candidate_id, story_id=story_id,
                    source_type=cand.source_type, kind=cand.kind,
                    created_at=cand.created_at,
                    passed=1 if cand.validation.get("passed") else 0,
                    candidate_json=cand.model_dump_json()))
            s.commit()

    def load(self, story_id: str) -> StoryState | None:
        with self.Session() as s:
            row = s.get(StoryRow, story_id)
            if row is None:
                return None
            return StoryState(**json.loads(row.state_json))
