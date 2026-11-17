import pytest
from sqlalchemy.exc import DatabaseError

from companyscout import db


def test_migrates_v1_database_keeping_rows(home):
    eng = db.make_engine(home / "old.db")
    with eng.begin() as c:
        db.Base.metadata.create_all(c)
        c.exec_driver_sql("ALTER TABLE companies DROP COLUMN user_rank")
        c.exec_driver_sql("INSERT INTO companies (name, domain, state) VALUES ('Acme', 'acme.com', 'discovered')")
        c.exec_driver_sql("PRAGMA user_version=1")
    db.migrate(eng)
    with eng.connect() as c:
        assert c.exec_driver_sql("PRAGMA user_version").scalar() == len(db.MIGRATIONS)
        assert c.exec_driver_sql("SELECT user_rank, name FROM companies").one() == (None, "Acme")


def test_illegal_transition_raises(session):
    c = db.Company(name="A", domain="a.com")
    session.add(c)
    with pytest.raises(db.InvalidTransition):
        db.transition(c, "sent")
    db.transition(c, "researched")
    assert c.state == "researched"


def test_shortlist_persists_across_connections(session):
    session.add(db.Company(name="A", domain="a.com", shortlisted=True))
    session.commit()
    session.close()
    again = db.connect()
    assert again.query(db.Company).filter_by(shortlisted=True).one().domain == "a.com"
    again.close()


def test_sent_message_is_immutable(session):
    m = db.SentMessage(to_email="x@a.com", subject="s", body="b", idempotency_key="k", status="pending")
    session.add(m)
    session.commit()
    m.status = "sent"  # pending -> sent is the one allowed update
    session.commit()
    m.body = "tampered"
    with pytest.raises(DatabaseError):
        session.commit()
    session.rollback()
    session.delete(m)
    with pytest.raises(DatabaseError):
        session.commit()


def test_kv_roundtrip(session):
    assert db.kv_get(session, "paused", False) is False
    db.kv_set(session, "paused", True)
    assert db.kv_get(session, "paused") is True
