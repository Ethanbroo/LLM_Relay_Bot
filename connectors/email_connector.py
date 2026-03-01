"""SMTP email notification connector.

Action: email.notify
Sends a plain-text notification email via Gmail SMTP (TLS port 587).
Read-only from the system's perspective — no rollback needed.
"""

import json
import hashlib
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from connectors.base import BaseConnector, ConnectorRequest, ConnectorContext
from connectors.results import ConnectorResult, RollbackResult, ConnectorStatus, RollbackStatus, VerificationMethod
from connectors.errors import ConnectorError


class EmailConfig:
    """Email connection config resolved from SecretsProvider."""
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addr: str,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addr = to_addr


class EmailConnector(BaseConnector):
    """SMTP email notification connector.

    Supported action: email.notify
    Payload schema:
        subject: str (max 200 chars)
        body:    str (max 4000 chars)
    """

    connector_type = "email"

    def __init__(self):
        self._config: Optional[EmailConfig] = None

    def connect(self, ctx: ConnectorContext) -> None:
        sp = ctx.secrets_provider
        if sp is not None:
            try:
                host = sp.resolve_string("secret:SMTP_HOST")
                port = int(sp.resolve_string("secret:SMTP_PORT"))
                username = sp.resolve_string("secret:SMTP_USERNAME")
                password = sp.resolve_string("secret:SMTP_PASSWORD")
                from_addr = sp.resolve_string("secret:SMTP_FROM")
                to_addr = sp.resolve_string("secret:SMTP_TO")
            except Exception:
                host = username = from_addr = to_addr = ""
                port = 587
                password = ""
        else:
            host = os.environ.get("LLM_RELAY_SECRET_SMTP_HOST", "smtp.gmail.com")
            port = int(os.environ.get("LLM_RELAY_SECRET_SMTP_PORT", "587"))
            username = os.environ.get("LLM_RELAY_SECRET_SMTP_USERNAME", "")
            password = os.environ.get("LLM_RELAY_SECRET_SMTP_PASSWORD", "")
            from_addr = os.environ.get("LLM_RELAY_SECRET_SMTP_FROM", username)
            to_addr = os.environ.get("LLM_RELAY_SECRET_SMTP_TO", username)

        self._config = EmailConfig(
            smtp_host=host,
            smtp_port=port,
            username=username,
            password=password,
            from_addr=from_addr,
            to_addr=to_addr,
        )

    def execute(self, req: ConnectorRequest) -> ConnectorResult:
        action = req.action
        if action != "email.notify":
            return ConnectorResult(
                status=ConnectorStatus.FAILURE,
                connector_type="email",
                idempotency_key=req.idempotency_key,
                error_code="UNKNOWN_ACTION",
                error_message=f"Unknown action: {action}"[:200],
            )

        payload = json.loads(req.payload_canonical)
        subject = str(payload.get("subject", "LLM Relay Notification"))[:200]
        body = str(payload.get("body", ""))[:4000]

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._config.from_addr
            msg["To"] = self._config.to_addr
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(self._config.username, self._config.password)
                server.sendmail(
                    self._config.from_addr,
                    [self._config.to_addr],
                    msg.as_string()
                )

            artifact_data = json.dumps({"to": self._config.to_addr, "subject": subject})
            artifact_hash = hashlib.sha256(artifact_data.encode()).hexdigest()
            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type="email",
                idempotency_key=req.idempotency_key,
                artifacts={"email_sent": artifact_hash},
                side_effect_summary=f"Email sent: {subject}"[:500],
            )
        except Exception as e:
            return ConnectorResult(
                status=ConnectorStatus.FAILURE,
                connector_type="email",
                idempotency_key=req.idempotency_key,
                error_code="SMTP_ERROR",
                error_message=str(e)[:200],
            )

    def rollback(self, req: ConnectorRequest, artifact=None) -> RollbackResult:
        return RollbackResult(
            rollback_status=RollbackStatus.NOT_APPLICABLE,
            verification_method=VerificationMethod.NOT_APPLICABLE,
            notes="Email send is not reversible",
        )

    def disconnect(self) -> None:
        self._config = None
