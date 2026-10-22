"""E-mail intake: PDFs sent to a workspace's address, read from an IMAP mailbox."""

from email.message import EmailMessage

import pytest
from sqlmodel import Session, select

from app import intake
from app.config import settings
from app.db import Document, Extraction, IntakeMessage, Workspace, engine
from app.workspaces import create_workspace
from tests.test_api import FakeExtractor
from tests.test_external import INVOICE


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    monkeypatch.setattr(settings, "intake_address", "dokumenty@logicflow.cz")
    monkeypatch.setattr(settings, "intake_imap_host", "imap.example.cz")
    monkeypatch.setattr(settings, "intake_imap_user", "dokumenty@logicflow.cz")
    monkeypatch.setattr(settings, "intake_imap_password", "test")
    monkeypatch.setattr(settings, "daily_llm_limit", 100_000)


def firm(template=None, senders=None, quota=None):
    with Session(engine) as session:
        workspace, code = create_workspace(session, "Příjem e-mailem", quota)
        workspace.intake_token = intake.new_token()
        workspace.intake_template_id, workspace.intake_senders = template, senders
        session.add(workspace)
        session.commit()
        return workspace.id, workspace.intake_token, {"X-Access-Code": code}


def mail(token, samples, sender="ucetni@firma.cz", pdfs=("faktura_ok.pdf",), **headers):
    message = EmailMessage()
    message["From"] = f"Účetní <{sender}>"
    message["To"] = f"dokumenty+{token}@logicflow.cz"
    message["Subject"] = "Faktury za září"
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    message.set_content("V příloze.")
    for name in pdfs:
        data = (samples / name).read_bytes() if (samples / name).exists() else b"neni pdf"
        message.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()


def receive(raw, llm=lambda: FakeExtractor(INVOICE)):
    with Session(engine) as session:
        return intake.process_message(session, raw, llm)


def documents(workspace_id):
    with Session(engine) as session:
        return session.exec(select(Document).where(Document.workspace_id == workspace_id)).all()


def test_pdf_lands_in_the_firm_workspace_and_is_extracted(client, samples):
    workspace_id, token, code = firm(template="invoice", quota=10)
    log = receive(mail(token, samples, Message_ID="<zari-1@firma.cz>"))
    assert (log.status, log.documents, log.workspace_id) == ("stored", 1, workspace_id)
    assert log.detail is None and log.sender == "ucetni@firma.cz"
    [doc] = documents(workspace_id)
    assert (doc.source, doc.sender, doc.filename) == ("email", "ucetni@firma.cz", "faktura_ok.pdf")
    with Session(engine) as session:
        extraction = session.exec(select(Extraction).where(Extraction.document_id == doc.id)).one()
    assert extraction.status == "ok" and extraction.template_id == "invoice"

    listed = client.get("/documents", headers=code).json()
    assert listed[0]["source"] == "email" and listed[0]["latest_extraction"]["status"] == "ok"
    access = client.get("/access", headers=code).json()
    assert access["intake_address"] == f"dokumenty+{token}@logicflow.cz"
    assert access["llm_calls_left_today"] == 9  # the extraction used the firm's quota

    # The same message again (e.g. read twice) stores nothing new.
    receive(mail(token, samples, Message_ID="<zari-1@firma.cz>"))
    assert len(documents(workspace_id)) == 1


def test_without_template_the_pdf_is_only_stored(samples):
    workspace_id, token, _ = firm()
    log = receive(mail(token, samples, pdfs=("faktura_ok.pdf", "pojistka.pdf")))
    assert log.documents == 2
    with Session(engine) as session:
        ids = [d.id for d in documents(workspace_id)]
        assert not session.exec(select(Extraction).where(Extraction.document_id.in_(ids))).all()


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"token": "neznamykod1"}, "platný kód"),
        ({"sender": "cizi@jinde.cz"}, "povolenými"),
        ({"pdfs": ()}, "přílohu PDF"),
    ],
)
def test_messages_that_are_not_stored(samples, change, detail):
    workspace_id, token, _ = firm(senders="@firma.cz, sef@jinde.cz")
    log = receive(mail(change.pop("token", token), samples, **change))
    assert log.status == "ignored" and detail in log.detail
    assert documents(workspace_id) == []


def test_allowed_senders_by_domain_and_address(samples):
    _, token, _ = firm(senders="@firma.cz, sef@jinde.cz")
    assert receive(mail(token, samples, sender="kdokoli@firma.cz")).status == "stored"
    assert receive(mail(token, samples, sender="sef@jinde.cz")).status == "stored"


def test_invalid_pdf_and_exhausted_quota_are_logged(samples):
    _, token, _ = firm(template="invoice", quota=1)
    log = receive(mail(token, samples, pdfs=("faktura_ok.pdf", "faktura_chyby.pdf", "zlomene.pdf")))
    assert log.status == "stored" and log.documents == 2
    assert "zlomene.pdf" in log.detail and "Denní limit" in log.detail


def test_revoked_workspace_receives_nothing(samples):
    workspace_id, token, _ = firm()
    with Session(engine) as session:
        workspace = session.get(Workspace, workspace_id)
        workspace.revoked_at = workspace.created_at
        session.add(workspace)
        session.commit()
    assert receive(mail(token, samples)).status == "ignored"


def test_forwarded_mail_is_routed_by_delivered_to(samples):
    workspace_id, token, _ = firm()
    raw = mail(token, samples, Delivered_To=f"dokumenty+{token}@logicflow.cz")
    raw = raw.replace(f"dokumenty+{token}@logicflow.cz".encode(), b"ucetni@firma.cz", 1)
    assert receive(raw).workspace_id == workspace_id


class FakeImap:
    """The part of imaplib.IMAP4 the poller uses, over a dict of unread messages."""

    def __init__(self, messages):
        self.messages, self.seen, self.fetched, self.logged_out = messages, [], [], False

    def __call__(self, host, port, timeout):
        assert (host, port, timeout) == ("imap.example.cz", 993, 30)
        return self

    def login(self, user, password):
        assert user == "dokumenty@logicflow.cz"

    def select(self, folder):
        assert folder == "INBOX"

    def uid(self, command, *args):
        if command == "search":
            assert args[1:3] == ("UNSEEN", "SINCE")
            unread = [u for u in self.messages if u not in self.seen]
            return "OK", [b" ".join(unread)]
        if command == "fetch":
            self.fetched.append((args[0], "HEADER" in args[1]))
            return "OK", [(b"1 (BODY[] {n}", self.messages[args[0]]), b")"]
        if command == "store":
            self.seen.append(args[0])
            return "OK", []

    def logout(self):
        self.logged_out = True


def test_poller_reads_unread_messages_and_marks_them_read(samples, monkeypatch):
    workspace_id, token, _ = firm()
    broken = f"To: dokumenty+{token}@logicflow.cz\r\n\r\n\x00 neni e-mail".encode()
    imap = FakeImap({b"7": mail(token, samples), b"8": broken})
    assert intake.poll_mailbox(imap) == 2
    assert imap.seen == [b"7", b"8"] and imap.logged_out
    assert len(documents(workspace_id)) == 1
    assert intake.poll_mailbox(imap) == 0  # nothing unread any more

    monkeypatch.setattr(settings, "intake_imap_host", None)
    assert intake.poll_mailbox(imap) == 0  # intake switched off


def test_intake_log_keeps_no_message_text(samples):
    _, token, _ = firm()
    receive(mail(token, samples, Message_ID="<bez-textu@firma.cz>"))
    with Session(engine) as session:
        log = session.exec(
            select(IntakeMessage).where(IntakeMessage.message_id == "<bez-textu@firma.cz>")
        ).one()
    assert "V příloze" not in str(log.model_dump())


def test_own_mail_in_a_shared_mailbox_is_left_alone(samples):
    """The intake can share the owner's mailbox: other mail stays unread and unlogged."""
    _, token, _ = firm()
    own = EmailMessage()
    own["From"], own["To"], own["Subject"] = "klient@firma.cz", "dokumenty@logicflow.cz", "Nabídka"
    own["Message-ID"] = "<vlastni-posta@firma.cz>"
    own.set_content("Soukromá zpráva.")
    other_plus = mail(token, samples).replace(b"dokumenty+", b"newsletter+", 1)
    imap = FakeImap({b"1": own.as_bytes(), b"2": other_plus, b"3": mail(token, samples)})
    assert intake.poll_mailbox(imap) == 1
    assert imap.seen == [b"3"]
    assert (b"1", False) not in imap.fetched and (b"2", False) not in imap.fetched  # no bodies
    with Session(engine) as session:
        logged = session.exec(
            select(IntakeMessage).where(IntakeMessage.message_id == "<vlastni-posta@firma.cz>")
        ).first()
    assert logged is None


def test_imap_date_uses_english_months():
    from datetime import date

    assert intake.imap_date(date(2026, 10, 2)) == "2-Oct-2026"
