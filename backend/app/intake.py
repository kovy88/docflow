"""E-mail intake: a firm sends or forwards PDFs to its own address, plus-addressing on one
IMAP mailbox (dokumenty+<token>@…). Every INTAKE_POLL_S seconds the mailbox is read; PDF
attachments land in the firm's workspace and, if the workspace has an intake template,
are extracted right away, so they wait in the app as "Ke kontrole". IMAP keeps it free of
any e-mail provider's API; each message is logged in IntakeMessage and read only once."""

import email
import hashlib
import imaplib
import re
import secrets
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from email.message import Message
from email.policy import default
from email.utils import getaddresses, parseaddr

from fastapi import HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.db import Document, IntakeMessage, Workspace, engine, utcnow
from app.extractor import Extractor
from app.runtime import (
    consume_llm_quota,
    get_extractor,
    logger,
    new_extraction,
    process_extraction,
    store_pdf,
)
from app.templates import load_templates
from app.workspaces import custom_templates

# Where the original recipient can be: forwarding rules keep it in Delivered-To and co.
ADDRESS_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To")
TOKEN = re.compile(r"\+([a-z0-9]{6,64})@")
IMAP_TIMEOUT_S = 30  # a hanging mail server must not stall the polling loop
LOOKBACK_DAYS = 7  # older unread mail is left alone


def new_token() -> str:
    # Lowercase hex: mail servers may change the case of the local part.
    return secrets.token_hex(6)


def intake_address(workspace: Workspace) -> str | None:
    if not settings.intake_enabled or not workspace.intake_token:
        return None
    local, _, domain = settings.intake_address.partition("@")
    return f"{local}+{workspace.intake_token}@{domain}"


def find_workspace(session: Session, message: Message) -> Workspace | None:
    for header in ADDRESS_HEADERS:
        for _, address in getaddresses([str(v) for v in message.get_all(header, [])]):
            match = TOKEN.search(address.lower())
            if not match:
                continue
            workspace = session.exec(
                select(Workspace).where(Workspace.intake_token == match.group(1))
            ).first()
            if workspace is not None and workspace.revoked_at is None:
                return workspace
    return None


def sender_allowed(workspace: Workspace, sender: str | None) -> bool:
    if not workspace.intake_senders:
        return True
    if not sender:
        return False
    allowed = [s.strip().lower() for s in workspace.intake_senders.split(",") if s.strip()]
    return any(sender.endswith(a) if a.startswith("@") else sender == a for a in allowed)


def pdf_attachments(message: Message) -> Iterator[tuple[str, bytes]]:
    for part in message.walk():
        if part.is_multipart():
            continue
        name = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or name.lower().endswith(".pdf"):
            data = part.get_payload(decode=True)
            if data:
                yield name or "priloha.pdf", data


def process_message(
    session: Session, raw: bytes, llm: Callable[[], Extractor] = get_extractor
) -> IntakeMessage:
    message = email.message_from_bytes(raw, policy=default)
    message_id = str(message.get("Message-ID") or "").strip()
    message_id = (message_id or "sha256:" + hashlib.sha256(raw).hexdigest())[:300]
    seen = session.exec(select(IntakeMessage).where(IntakeMessage.message_id == message_id))
    if (logged := seen.first()) is not None:
        return logged
    sender = parseaddr(str(message.get("From") or ""))[1].lower() or None
    log = IntakeMessage(
        message_id=message_id,
        sender=sender,
        subject=str(message.get("Subject") or "")[:200],
        status="ignored",
    )
    workspace = find_workspace(session, message)
    docs: list[Document] = []
    problems: list[str] = []
    if workspace is None:
        log.detail = "Adresa neobsahuje platný kód pracovního prostoru."
    elif not sender_allowed(workspace, sender):
        log.workspace_id, log.detail = workspace.id, "Odesílatel není mezi povolenými."
    else:
        log.workspace_id = workspace.id
        attachments = list(pdf_attachments(message))
        if not attachments:
            log.detail = "E-mail neobsahuje přílohu PDF."
        for name, data in attachments:
            try:
                docs.append(
                    store_pdf(session, workspace.id, data, name, source="email", sender=sender)
                )
            except HTTPException as exc:
                problems.append(f"{name}: {exc.detail}")
        if attachments:
            log.status = "stored" if docs else "failed"
            log.documents = len(docs)
    # Logged before the extraction: a restart meanwhile must not store the PDFs again.
    log.detail = "; ".join(problems) or log.detail
    session.add(log)
    session.commit()
    if docs and workspace.intake_template_id:
        problems += extract(session, workspace, docs, llm)
        log.detail = "; ".join(problems)[:1000] or None
        session.add(log)
        session.commit()
    session.refresh(log)
    return log


def extract(
    session: Session, workspace: Workspace, docs: list[Document], llm: Callable[[], Extractor]
) -> list[str]:
    """Extract the e-mailed documents one by one; returns what could not be done."""
    template_id = workspace.intake_template_id
    template = (load_templates() | custom_templates(session, workspace.id)).get(template_id)
    if template is None:
        return [f"Šablona {template_id!r} neexistuje, extrakce nebyla spuštěna."]
    try:
        extractor = llm()
    except HTTPException as exc:
        return [f"Extrakce nebyla spuštěna: {exc.detail}"]
    problems = []
    for doc in docs:
        if not any(doc.pages):
            problems.append(f"{doc.filename}: PDF neobsahuje text.")
            continue
        try:
            consume_llm_quota(session, "intake_extraction", workspace)
        except HTTPException:
            problems.append("Denní limit modelu je vyčerpaný; zbylé dokumenty vytěžte ručně.")
            break
        extraction = new_extraction(session, doc, template, extractor)
        process_extraction(extraction.id, doc.pages, doc.ocr_pages or [], template, extractor)
    return problems


def addressed_to_intake(headers: Message) -> bool:
    """Only messages for <local>+<token>@<domain> belong to the intake; the rest of a shared
    mailbox (the owner's own mail) is neither processed, logged nor marked read."""
    local, _, domain = settings.intake_address.lower().partition("@")
    pattern = re.compile(rf"^{re.escape(local)}\+[a-z0-9]+@{re.escape(domain)}$")
    for header in ADDRESS_HEADERS:
        for _, address in getaddresses([str(v) for v in headers.get_all(header, [])]):
            if pattern.match(address.lower()):
                return True
    return False


def imap_date(day: date) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{day.day}-{months[day.month - 1]}-{day.year}"  # IMAP wants English months


def poll_mailbox(connect: Callable[..., imaplib.IMAP4] = imaplib.IMAP4_SSL) -> int:
    """Process unread intake messages of the last LOOKBACK_DAYS and mark them read. Returns
    how many were processed."""
    if not settings.intake_enabled:
        return 0
    conn = connect(settings.intake_imap_host, settings.intake_imap_port, timeout=IMAP_TIMEOUT_S)
    processed = 0
    try:
        conn.login(settings.intake_imap_user, settings.intake_imap_password)
        conn.select(settings.intake_imap_folder)
        since = imap_date(utcnow().date() - timedelta(days=LOOKBACK_DAYS))
        _, found = conn.uid("search", None, "UNSEEN", "SINCE", since)
        for uid in found[0].split() if found and found[0] else []:
            # Headers first (PEEK keeps the message unread), so foreign mail is not downloaded.
            _, fetched = conn.uid(
                "fetch", uid, f"(BODY.PEEK[HEADER.FIELDS ({' '.join(ADDRESS_HEADERS)})])"
            )
            header = next((item[1] for item in fetched if isinstance(item, tuple)), None)
            if header is None or not addressed_to_intake(
                email.message_from_bytes(header, policy=default)
            ):
                continue
            _, fetched = conn.uid("fetch", uid, "(BODY.PEEK[])")
            raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
            if raw is not None:
                with Session(engine) as session:
                    try:
                        process_message(session, raw)
                    except Exception:  # marked read anyway, so one bad e-mail cannot loop
                        logger.exception("Intake of message %s failed", uid)
            conn.uid("store", uid, "+FLAGS", "(\\Seen)")
            processed += 1
        return processed
    finally:
        try:
            conn.logout()
        except Exception:
            pass
