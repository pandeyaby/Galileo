"""Auth + multi-tenant context for the control plane."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException, Request


@dataclass
class TenantContext:
    tenant_id: str
    api_key_id: str | None = None
    roles: tuple[str, ...] = ("operator",)


class AuthRegistry:
    """
    API-key auth with tenant binding.

    Env:
      DIZZY_API_KEYS=tenantA:key1,tenantB:key2
      DIZZY_AUTH_REQUIRED=1   # default off for local demo; on in prod
    """

    def __init__(self, mapping: dict[str, str] | None = None, *, required: bool | None = None):
        self._key_to_tenant: dict[str, str] = {}
        if mapping:
            for tenant, key in mapping.items():
                self._key_to_tenant[key] = tenant
        env = os.environ.get("DIZZY_API_KEYS", "")
        for part in env.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            tenant, _, key = part.partition(":")
            if tenant and key:
                self._key_to_tenant[key.strip()] = tenant.strip()
        if required is None:
            required = os.environ.get("DIZZY_AUTH_REQUIRED", "").lower() in {"1", "true", "yes"}
        self.required = required

    def issue_demo_key(self, tenant_id: str = "default") -> str:
        key = f"dg_{secrets.token_urlsafe(24)}"
        self._key_to_tenant[key] = tenant_id
        return key

    def resolve(
        self,
        *,
        api_key: str | None,
        tenant_header: str | None,
    ) -> TenantContext:
        if api_key and api_key in self._key_to_tenant:
            return TenantContext(tenant_id=self._key_to_tenant[api_key], api_key_id=_key_id(api_key))
        if self.required:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        # Open mode: honor X-Tenant-Id or default
        return TenantContext(tenant_id=tenant_header or "default")


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def bind_auth(app, registry: AuthRegistry) -> None:
    @app.middleware("http")
    async def tenant_middleware(request: Request, call_next):
        if request.url.path.startswith("/static") or request.url.path in {"/", "/api/health"}:
            request.state.tenant = TenantContext(tenant_id="default")
            return await call_next(request)
        api_key = request.headers.get("x-api-key") or request.query_params.get("api_key")
        tenant_hdr = request.headers.get("x-tenant-id")
        try:
            request.state.tenant = registry.resolve(api_key=api_key, tenant_header=tenant_hdr)
        except HTTPException as exc:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)


def tenant_from_request(request: Request) -> TenantContext:
    return getattr(request.state, "tenant", TenantContext(tenant_id="default"))
