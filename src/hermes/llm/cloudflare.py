"""Cloudflare Workers AI provider (direct REST `ai/run`).

Endpoint: POST https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}
Body: {"messages": [{"role":"system","content":...},{"role":"user","content":...}]}
Resp: {"success": true, "result": {"response": "..."}} (text models)

Secrets via env only: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN.
"""
from __future__ import annotations

import httpx


class CloudflareError(Exception):
    pass


class CloudflareLLM:
    def __init__(self, account_id: str, api_token: str, model: str,
                 timeout: int = 60, system: str = ""):
        if not account_id or not api_token:
            raise ValueError("Missing CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN")
        self.account_id = account_id
        self.api_token = api_token
        self.model = model
        self.timeout = timeout
        self.system = system

    @property
    def url(self) -> str:
        return (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/ai/run/{self.model}"
        )

    def complete(self, prompt: str, system: str = "") -> str:
        sys = system or self.system
        msgs = []
        if sys:
            msgs.append({"role": "system", "content": sys})
        msgs.append({"role": "user", "content": prompt})
        try:
            r = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_token}"},
                json={"messages": msgs},
                timeout=self.timeout,
            )
        except Exception as e:
            raise CloudflareError(f"Workers AI request failed: {e}") from e
        try:
            data = r.json()
        except Exception as e:
            raise CloudflareError(f"Workers AI bad JSON (http {r.status_code}): {r.text[:300]}") from e
        if r.status_code >= 400 or not data.get("success", True):
            errs = data.get("errors", r.text[:500])
            raise CloudflareError(f"Workers AI error http={r.status_code}: {errs}")
        result = data.get("result", {})
        if isinstance(result, dict):
            for k in ("response", "output_text", "text"):
                if result.get(k):
                    return str(result[k])
            # some models return {"output": [...]} — flatten best-effort
            if result.get("output"):
                return str(result["output"])[:4000]
            return str(result)[:4000]
        return str(result)[:4000]

    def __call__(self, prompt: str) -> str:
        return self.complete(prompt)
