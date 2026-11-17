import json

from companyscout import config, gmail


def test_message_carries_recipient_subject_and_message_id():
    m = gmail.build_message("Larry Wang", "me@example.com", "dana@acme.com", "Hello", "Body text", "<id1@example.com>")
    assert (m["To"], m["Subject"], m["Message-ID"]) == ("dana@acme.com", "Hello", "<id1@example.com>")
    assert m["From"] == "Larry Wang <me@example.com>" and m.get_content().strip() == "Body text"
    assert m["In-Reply-To"] is None


def test_follow_up_message_threads_onto_the_original():
    m = gmail.build_message("L", "me@example.com", "dana@acme.com", "Re: Hello", "Following up", "<id2@example.com>",
                            in_reply_to="<id1@example.com>")
    assert m["In-Reply-To"] == "<id1@example.com>" and m["References"] == "<id1@example.com>"


def test_mock_gmail_persists_between_instances(home):
    a = gmail.MockGmail()
    sent = a.send(gmail.build_message("L", a.address, "dana@acme.com", "Hello", "Body", "<id1@example.com>"))
    b = gmail.MockGmail()
    assert b.find_sent("<id1@example.com>")["id"] == sent["id"] and b.sent_to("dana@acme.com")


def test_connect_keeps_token_in_keychain_and_off_disk(home, cfg, monkeypatch):
    vault = {}
    monkeypatch.setattr(gmail.keyring, "set_password", lambda s, u, p: vault.__setitem__((s, u), p))
    monkeypatch.setattr(gmail.keyring, "get_password", lambda s, u: vault.get((s, u)))
    monkeypatch.setattr(gmail.keyring, "delete_password", lambda s, u: vault.pop((s, u)))

    class Creds:
        def to_json(self):
            return json.dumps({"refresh_token": "SECRET-REFRESH"})

    class Flow:
        def run_local_server(self, **kw):
            return Creds()

    seen = {}
    gmail.connect(cfg, "client.json", flow_factory=lambda path, scopes: seen.update(scopes=scopes) or Flow())
    assert "SECRET-REFRESH" in vault[(gmail.SERVICE, "token")] and seen["scopes"] == cfg["gmail_scopes"]
    assert not any("SECRET-REFRESH" in p.read_text(errors="ignore") for p in config.home().rglob("*") if p.is_file())
    gmail.disconnect(revoke=lambda token: None)
    assert vault == {}
