from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import imaplib
from collections import defaultdict, deque
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field


APP_ACCESS_PASSWORD = os.getenv("APP_ACCESS_PASSWORD", "").strip()
MAX_EMAILS = 200
RATE_LIMIT = 30

PROVIDERS = {
    "gmail": ("imap.gmail.com", 993),
    "outlook": ("outlook.office365.com", 993),
    "yahoo": ("imap.mail.yahoo.com", 993),
    "icloud": ("imap.mail.me.com", 993),
}

FIELDS = {
    "from": "From",
    "sender": "Sender",
    "subject": "Subject",
    "to": "To",
    "cc": "Cc",
    "date": "Date",
    "message_id": "Message-ID",
    "return_path": "Return-Path",
    "content_type": "Content-Type",
    "reply_to": "Reply-To",
    "client_ip": "Client-IP",
    "received": "Received",
    "authentication_results": "Authentication-Results",
    "dkim": "DKIM",
    "spf": "SPF",
    "dmarc": "DMARC",
}

RATE_BUCKETS: defaultdict[str, deque[float]] = defaultdict(deque)

app = FastAPI(
    title="Mail Header Extractor",
    version="4.2.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)


class Mailbox(BaseModel):
    email: str
    password: str
    provider: str = "auto"
    host: str | None = None
    port: int = Field(993, ge=1, le=65535)
    ssl: bool = True


class ExtractRequest(Mailbox):
    folder: str = "INBOX"
    limit: int = Field(50, ge=1, le=MAX_EMAILS)
    fields: list[str] = Field(default_factory=lambda: list(FIELDS))
    newest_first: bool = True


class ExportRequest(BaseModel):
    format: str
    fields: list[str]
    rows: list[dict[str, Any]]


def auth_guard(value: str | None) -> None:
    if APP_ACCESS_PASSWORD and value != APP_ACCESS_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid app access password.")


def rate_limit(request: Request) -> None:
    # Health/config are intentionally not rate-limited so deployment checks
    # cannot get blocked by repeated browser refreshes.
    if request.url.path in {"/api/health", "/api/config"}:
        return

    key = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = RATE_BUCKETS[key]

    while bucket and bucket[0] < now - 60:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a minute and try again.",
        )

    bucket.append(now)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    rate_limit(request)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def detect_provider(email: str) -> str:
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""

    if domain in {"gmail.com", "googlemail.com"}:
        return "gmail"
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com", "office365.com"}:
        return "outlook"
    if domain in {"yahoo.com", "yahoo.fr", "yahoo.co.uk", "ymail.com"}:
        return "yahoo"
    if domain in {"icloud.com", "me.com", "mac.com"}:
        return "icloud"

    return "custom"


def resolve_mailbox(mailbox: Mailbox) -> tuple[str, str, int]:
    provider = mailbox.provider.lower().strip()

    if provider == "auto":
        provider = detect_provider(mailbox.email)

    # A custom host always takes priority over the provider preset.
    if mailbox.host and mailbox.host.strip():
        return provider, mailbox.host.strip(), mailbox.port

    if provider in PROVIDERS:
        host, port = PROVIDERS[provider]
        return provider, host, port

    raise HTTPException(
        status_code=400,
        detail="Unknown IMAP server. Select a provider or enter a Custom IMAP Host.",
    )


def connect(mailbox: Mailbox):
    provider, host, port = resolve_mailbox(mailbox)

    if not mailbox.email.strip():
        raise HTTPException(status_code=400, detail="Mailbox email is required.")

    if not mailbox.password:
        raise HTTPException(status_code=400, detail="Mailbox app password is required.")

    try:
        if mailbox.ssl:
            client = imaplib.IMAP4_SSL(host, port, timeout=20)
        else:
            client = imaplib.IMAP4(host, port, timeout=20)

        client.login(mailbox.email, mailbox.password)
        return client, provider, host, port

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"IMAP connection failed: {exc}",
        ) from exc


def decode_value(value: str | None) -> str:
    if not value:
        return ""

    output: list[str] = []

    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            try:
                output.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                output.append(part.decode("utf-8", errors="replace"))
        else:
            output.append(part)

    return "".join(output).strip()


def header_values(message, name: str) -> list[str]:
    return [decode_value(value) for value in message.get_all(name, [])]


def authentication_values(value: str) -> dict[str, str]:
    result: dict[str, str] = {}

    for key in ("dkim", "spf", "dmarc"):
        match = re.search(
            rf"\b{key}\s*=\s*([^\s;]+)",
            value or "",
            flags=re.IGNORECASE,
        )
        result[key] = match.group(1) if match else ""

    return result


def extract_client_ip(authentication_results: str, received: list[str]) -> str:
    match = re.search(
        r"\bclient-ip\s*[:=]\s*(?:\[([^\]]+)\]|([0-9a-fA-F:.]+))",
        authentication_results or "",
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1) or match.group(2) or ""

    ip_pattern = re.compile(
        r"(?<![\w:])"
        r"(?:(?:\d{1,3}\.){3}\d{1,3}"
        r"|[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{0,4}){2,7})"
        r"(?![\w:])"
    )

    for value in received:
        found = ip_pattern.search(value)
        if found:
            return found.group(0)

    return ""


def parse_headers(raw: bytes, selected_fields: list[str]) -> dict[str, str]:
    message = BytesParser(policy=policy.default).parsebytes(raw)

    received = header_values(message, "Received")
    authentication_results = header_values(message, "Authentication-Results")
    auth_text = "\n".join(authentication_results)
    auth = authentication_values(auth_text)

    values = {
        "from": decode_value(message.get("From")),
        "sender": decode_value(message.get("Sender")),
        "subject": decode_value(message.get("Subject")),
        "to": decode_value(message.get("To")),
        "cc": decode_value(message.get("Cc")),
        "date": decode_value(message.get("Date")),
        "message_id": decode_value(message.get("Message-ID")),
        "return_path": decode_value(message.get("Return-Path")),
        "content_type": decode_value(message.get("Content-Type")),
        "reply_to": decode_value(message.get("Reply-To")),
        "client_ip": extract_client_ip(auth_text, received),
        "received": "\n".join(received),
        "authentication_results": "\n".join(authentication_results),
        "dkim": auth["dkim"],
        "spf": auth["spf"],
        "dmarc": auth["dmarc"],
    }

    return {field: values.get(field, "") for field in selected_fields}


def list_folders(client) -> list[str]:
    status, data = client.list()

    if status != "OK":
        raise RuntimeError("Could not list IMAP folders.")

    result: list[str] = []

    for item in data or []:
        if not item:
            continue

        line = item.decode("utf-8", "replace")

        # Common IMAP LIST form:
        # (\HasNoChildren) "/" "INBOX"
        quoted = re.findall(r'"([^"]*)"', line)
        if quoted:
            result.append(quoted[-1])
            continue

        parts = line.split(" ", 2)
        if len(parts) == 3:
            result.append(parts[2].strip('"'))

    unique = sorted(
        set(result),
        key=lambda name: (name.upper() != "INBOX", name.lower()),
    )
    return unique


def close_client(client) -> None:
    try:
        client.close()
    except Exception:
        pass

    try:
        client.logout()
    except Exception:
        pass


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": "4.2.0",
        "platform": "vercel-fastapi",
    }


@app.get("/api/config")
def config():
    return {
        "app_password_required": bool(APP_ACCESS_PASSWORD),
        "max_emails": MAX_EMAILS,
        "version": "4.2.0",
        "providers": list(PROVIDERS.keys()),
    }


@app.post("/api/test-connection")
def test_connection(
    request: Request,
    mailbox: Mailbox,
    x_app_password: str | None = Header(default=None),
):
    del request
    auth_guard(x_app_password)

    client, provider, host, port = connect(mailbox)

    try:
        return {
            "ok": True,
            "provider": provider,
            "host": host,
            "port": port,
            "message": "IMAP connection successful.",
        }
    finally:
        close_client(client)


@app.post("/api/folders")
def get_folders(
    request: Request,
    mailbox: Mailbox,
    x_app_password: str | None = Header(default=None),
):
    del request
    auth_guard(x_app_password)

    client, _, _, _ = connect(mailbox)

    try:
        return {"folders": list_folders(client)}
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not load folders: {exc}",
        ) from exc
    finally:
        close_client(client)


@app.post("/api/extract")
def extract(
    request: Request,
    payload: ExtractRequest,
    x_app_password: str | None = Header(default=None),
):
    del request
    auth_guard(x_app_password)

    selected = [field for field in payload.fields if field in FIELDS]

    if not selected:
        raise HTTPException(status_code=400, detail="Select at least one field.")

    client, _, _, _ = connect(payload)

    try:
        status, _ = client.select(payload.folder, readonly=True)

        if status != "OK":
            raise HTTPException(
                status_code=400,
                detail=f"Could not open folder: {payload.folder}",
            )

        status, data = client.uid("SEARCH", None, "ALL")

        if status != "OK":
            raise HTTPException(status_code=400, detail="IMAP search failed.")

        uids = (data[0] or b"").split()

        if payload.newest_first:
            uids.reverse()

        requested_uids = uids[: payload.limit]
        rows: list[dict[str, str]] = []

        for uid in requested_uids:
            try:
                status, fetched = client.uid(
                    "FETCH",
                    uid,
                    "(BODY.PEEK[HEADER])",
                )

                if status != "OK":
                    continue

                raw = b"".join(
                    part[1]
                    for part in fetched or []
                    if isinstance(part, tuple)
                    and len(part) > 1
                    and isinstance(part[1], bytes)
                )

                if raw:
                    rows.append(parse_headers(raw, selected))

            except Exception:
                # One malformed message must not abort the whole extraction.
                continue

        return {
            "status": "done",
            "total_requested": len(requested_uids),
            "extracted": len(rows),
            "fields": selected,
            "rows": rows,
        }

    finally:
        close_client(client)


@app.post("/api/export")
def export_results(
    request: Request,
    payload: ExportRequest,
    x_app_password: str | None = Header(default=None),
):
    del request
    auth_guard(x_app_password)

    fields = [field for field in payload.fields if field in FIELDS]
    fmt = payload.format.lower().strip()

    if not fields:
        raise HTTPException(status_code=400, detail="No export fields selected.")

    if fmt == "json":
        content = json.dumps(
            payload.rows,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        return Response(
            content,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="mail_headers.json"'
            },
        )

    if fmt == "txt":
        buffer = io.StringIO()

        for index, row in enumerate(payload.rows, start=1):
            buffer.write(f"--- Email {index} ---\n")
            for field in fields:
                buffer.write(
                    f"{FIELDS[field]}: {row.get(field, '')}\n"
                )
            buffer.write("\n")

        return Response(
            buffer.getvalue().encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="mail_headers.txt"'
            },
        )

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=fields,
            extrasaction="ignore",
        )

        writer.writerow({field: FIELDS[field] for field in fields})
        writer.writerows(payload.rows)

        return Response(
            buffer.getvalue().encode("utf-8-sig"),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="mail_headers.csv"'
            },
        )

    if fmt == "xlsx":
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Headers"

        worksheet.append([FIELDS[field] for field in fields])

        for row in payload.rows:
            worksheet.append([row.get(field, "") for field in fields])

        for column in worksheet.columns:
            letter = column[0].column_letter
            max_length = max(
                len(str(cell.value or ""))
                for cell in column
            )
            worksheet.column_dimensions[letter].width = min(
                60,
                max(12, max_length + 2),
            )

        buffer = io.BytesIO()
        workbook.save(buffer)

        return Response(
            buffer.getvalue(),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": 'attachment; filename="mail_headers.xlsx"'
            },
        )

    raise HTTPException(
        status_code=400,
        detail="Unsupported export format.",
    )
