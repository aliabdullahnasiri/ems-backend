from app.core.exceptions import (
    InvalidFileSizeError,
    InvalidFileTypeError,
    InvalidFinancialValueError,
    InvalidRetryKeyError,
)
from app.core.i18n.types import T

"""Reusable tenant boundaries for API actions, exports, jobs and financial posting.

Methods flush but do not commit: callers own the transaction. Financial writes
must run through execute_once, with their complete posting inside its callback.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import PurePath
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context.organization import organization_scope, posting_callback
from app.core.exceptions import AppValidationError, AuthorizationError, ConflictError
from app.models import AuditEvent, FinancialRetry, Organization, PrivateAttachment
from app.services.organization import OrganizationService
from app.services.organization_context import OrganizationContext


class TenantOperations:
    @staticmethod
    async def authorize(
        db: AsyncSession, context: OrganizationContext, *permissions: str
    ):
        verified = await OrganizationContext.resolve(
            db,
            context.user_id,
            context.organization_id,
            membership_id=context.membership_id,
            membership_uuid=context.membership_uuid,
        )
        await OrganizationService.require_permissions(
            db, verified.user_id, verified.organization_id, permissions
        )
        return verified

    @staticmethod
    async def audit(
        db: AsyncSession,
        context: OrganizationContext,
        action: str,
        record_type: str,
        record_id: int,
        reason: str | None = None,
    ) -> AuditEvent:
        await TenantOperations.authorize(db, context)
        if (
            not action
            or len(action) > 100
            or not record_type
            or len(record_type) > 100
            or record_id < 1
        ):
            raise AppValidationError()
        if reason is not None and len(reason) > 500:
            raise AppValidationError()
        event = AuditEvent(
            **context.audit_values(),
            action=action,
            record_type=record_type,
            record_id=record_id,
            reason=reason,
        )
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def audit_log(
        db: AsyncSession, context: OrganizationContext, offset=0, limit=50
    ):
        await TenantOperations.authorize(db, context, "audit:read")
        if offset < 0 or not 1 <= limit <= 100:
            raise AppValidationError()
        return list(
            await db.scalars(
                context.query(AuditEvent)
                .order_by(AuditEvent.id)
                .offset(offset)
                .limit(limit)
            )
        )

    @staticmethod
    def payload_hash(payload: dict) -> str:
        # Decimal values retain exact text; binary floats are rejected so money
        # cannot silently be rounded during hashing or result persistence.
        def validate(value):
            if isinstance(value, float):
                raise InvalidFinancialValueError()
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise AppValidationError()
                    validate(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    validate(item)

        validate(payload)

        def encode_decimal(value):
            if isinstance(value, Decimal) and value.is_finite():
                return str(value)
            raise InvalidFinancialValueError()

        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
                default=encode_decimal,
            )
        except (TypeError, ValueError) as exc:
            raise AppValidationError() from exc
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    async def execute_once(
        db: AsyncSession,
        context: OrganizationContext,
        operation: str,
        retry_key: str,
        payload: dict,
        permission: str,
        execute: Callable[[], Awaitable[dict]],
        references: tuple[tuple[type, int, int | None], ...] = (),
    ) -> dict:
        """Lock one tenant, validate references, post and persist the retry atomically.

        Each reference is (model, ID, expected shop ID or None). The callback must
        use the same session, must not commit, and returns a JSON-safe response.
        A changed actor cannot replay another member's financial result.
        """
        if (
            not retry_key
            or len(retry_key) > 128
            or not retry_key.strip()
            or not operation
            or len(operation) > 100
        ):
            raise InvalidRetryKeyError()
        digest = TenantOperations.payload_hash(payload)
        # Organization rows are stable and shared by every cashier. PostgreSQL
        # serializes same-tenant postings while different tenants remain independent.
        await db.scalar(
            select(Organization.id)
            .where(Organization.id == context.organization_id)
            .with_for_update()
        )
        if db.bind.dialect.name == "sqlite":
            await db.execute(
                update(Organization)
                .where(Organization.id == context.organization_id)
                .values(rbac_version=Organization.rbac_version)
            )
        await TenantOperations.authorize(db, context, permission)
        existing = await db.scalar(
            context.query(FinancialRetry).where(
                FinancialRetry.operation == operation,
                FinancialRetry.retry_key == retry_key,
            )
        )
        if existing:
            if existing.payload_hash != digest:
                raise ConflictError(message=T("validation:retry_payload_conflict"))
            if existing.membership_id != context.membership_id:
                raise AuthorizationError()
            return existing.result
        for model, record_id, shop_id in references:
            await context.reference(db, model, record_id, shop_id=shop_id)
        with organization_scope(context):
            async with db.begin_nested():
                with posting_callback(db):
                    result = await execute()
                # Round-trip to ensure the stored response and first response agree.
                TenantOperations.payload_hash(result)
                result = json.loads(
                    json.dumps(result, ensure_ascii=False, allow_nan=False)
                )
                retry = FinancialRetry(
                    **context.audit_values(),
                    operation=operation,
                    retry_key=retry_key,
                    payload_hash=digest,
                    result=result,
                )
                db.add(retry)
                await db.flush()
                await TenantOperations.audit(
                    db, context, operation, "financial_retry", retry.id
                )
        return result

    @staticmethod
    async def run_job(
        db: AsyncSession,
        payload: dict,
        permissions: tuple[str, ...],
        execute: Callable[[OrganizationContext], Awaitable[Any]],
    ):
        context = await OrganizationContext.from_job(db, payload)
        await TenantOperations.authorize(db, context, *permissions)
        with organization_scope(context):
            async with db.begin_nested():
                with posting_callback(db):
                    result = await execute(context)
                await db.flush()
                return result

    @staticmethod
    async def cache_get(
        db: AsyncSession,
        redis: Redis,
        context: OrganizationContext,
        permission: str,
        namespace: str,
        identifier: str,
    ) -> Any | None:
        await TenantOperations.authorize(db, context, permission)
        try:
            raw = await redis.get(
                context.key(
                    namespace,
                    f"{context.membership_id}:{context.membership_uuid}:{identifier}",
                )
            )
            return json.loads(raw) if raw is not None else None
        except RedisError, ValueError, TypeError:
            return None

    @staticmethod
    async def cache_set(
        db: AsyncSession,
        redis: Redis,
        context: OrganizationContext,
        permission: str,
        namespace: str,
        identifier: str,
        value: Any,
        ttl: int = 60,
    ):
        await TenantOperations.authorize(db, context, permission)
        try:
            await redis.set(
                context.key(
                    namespace,
                    f"{context.membership_id}:{context.membership_uuid}:{identifier}",
                ),
                json.dumps(value),
                ex=ttl,
            )
        except RedisError:
            pass

    @staticmethod
    async def upload(
        db: AsyncSession,
        context: OrganizationContext,
        shop_id: int,
        file_name: str,
        content_type: str,
        content: bytes,
    ) -> PrivateAttachment:
        await TenantOperations.authorize(db, context, "attachment:manage")
        shop = await context.shop(db, shop_id)
        if not content or len(content) > 5 * 1024 * 1024:
            raise InvalidFileSizeError()
        signatures = {
            "application/pdf": b"%PDF-",
            "image/png": b"\x89PNG\r\n\x1a\n",
            "image/jpeg": b"\xff\xd8\xff",
        }
        if content_type not in signatures or not content.startswith(
            signatures[content_type]
        ):
            raise InvalidFileTypeError()
        if (
            not file_name
            or len(file_name) > 200
            or PurePath(file_name).name != file_name
            or any(c in file_name for c in ("\\", "\r", "\n", "\x00"))
        ):
            raise AppValidationError()
        attachment = context.new(
            PrivateAttachment,
            shop_id=shop.id,
            file_name=file_name,
            content_type=content_type,
            content=content,
        )
        db.add(attachment)
        await db.flush()
        await TenantOperations.audit(
            db, context, "attachment:create", "private_attachment", attachment.id
        )
        return attachment

    @staticmethod
    async def download(
        db: AsyncSession, context: OrganizationContext, attachment_id: int
    ):
        await TenantOperations.authorize(db, context, "attachment:read")
        attachment = await context.get(db, PrivateAttachment, attachment_id)
        await context.shop(db, attachment.shop_id)
        from app.services.contact import ContactService

        await ContactService.authorize_attachment(db, context, attachment.id)
        return attachment
