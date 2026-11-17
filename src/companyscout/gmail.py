"""Gmail connector: MIME construction, OAuth (token in the macOS Keychain), a real client and a local mock."""
import base64
import json
import mimetypes
import uuid
from email.message import EmailMessage
from pathlib import Path
from email.utils import formataddr, formatdate, parseaddr

import keyring

from . import config

SERVICE = "companyscout-gmail"
HEADERS = ["From", "Subject", "Message-ID", "Auto-Submitted", "X-Failed-Recipients"]


class GmailError(RuntimeError):
    pass


def build_message(from_name, from_email, to_email, subject, body, message_id, in_reply_to=None, attachment=None) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = formataddr((from_name, from_email)), to_email, subject
    m["Message-ID"], m["Date"] = message_id, formatdate(localtime=True)
    if in_reply_to:  # RFC 5322 threading; Gmail also needs the threadId and a matching subject
        m["In-Reply-To"], m["References"] = in_reply_to, in_reply_to
    m.set_content(body)
    if attachment:
        path = Path(attachment).expanduser()
        maintype, _, subtype = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").partition("/")
        m.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return m


def _raw(msg) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class MockGmail:
    """Local stand-in used by tests and OUTREACH_GMAIL=mock. Nothing leaves the machine."""
    address = "me@example.com"

    def __init__(self):
        self.path = config.home() / "mock_gmail.json"
        self.messages = json.loads(self.path.read_text()) if self.path.exists() else []

    def _add(self, label, msg, thread_id, sender):
        entry = {"id": uuid.uuid4().hex[:16], "threadId": thread_id or uuid.uuid4().hex[:16], "label": label,
                 "from": sender, "to": msg["To"], "subject": msg["Subject"], "rfc_id": msg["Message-ID"],
                 "snippet": msg.get_body(("plain",)).get_content().strip()[:300], "headers": {k: v for k, v in msg.items()},
                 "attachments": [p.get_filename() for p in msg.iter_attachments()]}
        self.messages.append(entry)
        self.path.write_text(json.dumps(self.messages, indent=1))
        return entry

    def box(self, label):
        return [m for m in self.messages if m["label"] == label]

    def send(self, msg, thread_id=None):
        return self._add("sent", msg, thread_id, self.address)

    def create_draft(self, msg, thread_id=None):
        return self._add("draft", msg, thread_id, self.address)

    def find_sent(self, rfc_id):
        return next((m for m in self.box("sent") if m["rfc_id"] == rfc_id), None)

    def sent_to(self, email) -> bool:
        return any(m["to"].lower() == email.lower() for m in self.box("sent"))

    def thread(self, thread_id):
        return [m for m in self.messages if m["threadId"] == thread_id and m["label"] != "draft"]

    def add_reply(self, thread_id, from_email, text, subject="Re:", headers=None):
        """Simulate an incoming message on a thread."""
        m = build_message("", from_email, self.address, subject, text, f"<{uuid.uuid4().hex}@mock>")
        for k, v in (headers or {}).items():
            m[k] = v
        return self._add("inbox", m, thread_id, from_email)


class RealGmail:
    def __init__(self, credentials):
        from googleapiclient.discovery import build
        self.users = build("gmail", "v1", credentials=credentials, cache_discovery=False).users()
        self.address = self.users.getProfile(userId="me").execute()["emailAddress"]

    def _body(self, msg, thread_id):
        return {"raw": _raw(msg), **({"threadId": thread_id} if thread_id else {})}

    def send(self, msg, thread_id=None):
        return self.users.messages().send(userId="me", body=self._body(msg, thread_id)).execute()

    def create_draft(self, msg, thread_id=None):
        return self.users.drafts().create(userId="me", body={"message": self._body(msg, thread_id)}).execute()

    def _first(self, query):
        found = self.users.messages().list(userId="me", q=query, maxResults=1).execute().get("messages", [])
        return found[0] if found else None

    def find_sent(self, rfc_id):
        return self._first(f"rfc822msgid:{rfc_id.strip('<>')}")

    def sent_to(self, email) -> bool:  # the one narrowly scoped mailbox search: prevents duplicate outreach
        return self._first(f"in:sent to:{email}") is not None

    def thread(self, thread_id):
        """Only ever called with thread ids this tool created."""
        t = self.users.threads().get(userId="me", id=thread_id, format="metadata", metadataHeaders=HEADERS).execute()
        out = []
        for m in t.get("messages", []):
            headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "from": parseaddr(headers.get("From", ""))[1].lower(),
                        "subject": headers.get("Subject", ""), "snippet": m.get("snippet", ""), "headers": headers})
        return out


def _default_flow(path, scopes):
    from google_auth_oauthlib.flow import InstalledAppFlow
    return InstalledAppFlow.from_client_secrets_file(path, scopes)


def connect(cfg, credentials_file, flow_factory=_default_flow) -> None:
    """OAuth 2.0 desktop flow in the user's browser. Only the resulting token is kept, and only in the Keychain."""
    creds = flow_factory(str(credentials_file), cfg["gmail_scopes"]).run_local_server(port=0)
    keyring.set_password(SERVICE, "token", creds.to_json())


def load_credentials(cfg):
    stored = keyring.get_password(SERVICE, "token")
    if not stored:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_info(json.loads(stored), cfg["gmail_scopes"])
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        keyring.set_password(SERVICE, "token", creds.to_json())
    return creds


def _revoke(token):
    import urllib.parse
    import ssl
    import urllib.request

    import certifi
    data = urllib.parse.urlencode({"token": token}).encode()
    urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/revoke", data=data), timeout=15,
                           context=ssl.create_default_context(cafile=certifi.where()))


def disconnect(revoke=_revoke) -> bool:
    stored = keyring.get_password(SERVICE, "token")
    if not stored:
        return False
    info = json.loads(stored)
    try:
        revoke(info.get("refresh_token") or info.get("token"))
    except Exception:
        pass  # token is deleted locally either way; it can also be revoked at myaccount.google.com/permissions
    keyring.delete_password(SERVICE, "token")
    return True


def get_client(cfg):
    if cfg["gmail"] == "mock":
        return MockGmail()
    creds = load_credentials(cfg)
    if creds is None:
        raise GmailError("Gmail is not connected. Run: outreach gmail connect --credentials <client_secret.json>")
    return RealGmail(creds)
