"""Copilot enterprise diagnostic — prints host/endpoint facts, NO secrets.

Run in the environment where the Connection error happens:
    source /workspaces/aelix-ai/.venv/bin/activate
    python scripts/diag_copilot_enterprise.py

Reveals the token's proxy-ep host, the resolved base_url, whether GET /models
works, each model's supported_endpoints, and live /responses vs /chat/completions
results. The bearer token itself is never printed.
"""

import asyncio
import json
import os

import httpx


def _auth_path() -> str:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.expanduser("~/.config")
    for p in (
        os.path.join(base, "aelix", "agent", "auth.json"),
        os.path.expanduser("~/.aelix/agent/auth.json"),
    ):
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    raise SystemExit("no auth.json found")


async def main() -> None:
    from aelix_ai.oauth.github_copilot import (
        get_github_copilot_base_url,
        refresh_github_copilot_token,
    )

    path = _auth_path()
    print(f"auth.json: {path}")
    with open(path) as f:
        gc = json.load(f).get("github-copilot")
    if not gc:
        raise SystemExit("no github-copilot credential in auth.json — /login first")

    ent = gc.get("enterpriseUrl")
    ent_arg = ent if isinstance(ent, str) and ent and ent != "https://github.com/" else None
    print(f"stored enterpriseUrl: {ent!r}")
    creds = await refresh_github_copilot_token(gc["refresh"], ent_arg)
    tok = creds.access
    fields = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in tok.split(";") if "=" in p}
    print(f"sku: {fields.get('sku')} | st: {fields.get('st')}")
    print(f"proxy-ep (from token): {fields.get('proxy-ep')}")
    base = get_github_copilot_base_url(tok)
    print(f"resolved base_url: {base}")
    print(
        "  ^ if this is api.individual.githubcopilot.com but sku/st say "
        "enterprise/business, the completion host is WRONG.\n"
    )

    H = {
        "Authorization": f"Bearer {tok}",
        "User-Agent": "GitHubCopilotChat/0.35.0",
        "Editor-Version": "vscode/1.107.0",
        "Editor-Plugin-Version": "copilot-chat/0.35.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Content-Type": "application/json",
    }
    targets = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5-mini"]
    try:
        r = httpx.get(base + "/models", headers=H, timeout=20.0)
        print(f"GET {base}/models -> {r.status_code}")
        if r.status_code == 200:
            for m in r.json().get("data", []):
                if m.get("id") in targets:
                    print(f"  {m['id']:14s} supported_endpoints={m.get('supported_endpoints', '<absent>')}")
    except Exception as e:  # noqa: BLE001
        print(f"GET /models FAILED: {type(e).__name__}: {str(e)[:200]}")

    print("\nlive endpoint probes:")
    for mid in targets:
        for path_, payload in (
            ("/chat/completions", {"model": mid, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}),
            ("/responses", {"model": mid, "input": "hi", "max_output_tokens": 16}),
        ):
            try:
                r = httpx.post(base + path_, headers=H, json=payload, timeout=25.0)
                try:
                    code = r.json().get("error", {}).get("code") or "OK"
                except Exception:  # noqa: BLE001
                    code = ""
                print(f"  {mid:14s} {path_:20s} -> {r.status_code} {code}")
            except Exception as e:  # noqa: BLE001
                cause = getattr(e, "__cause__", None)
                extra = f" (cause: {type(cause).__name__})" if cause else ""
                print(f"  {mid:14s} {path_:20s} -> CONNECT-FAIL {type(e).__name__}: {str(e)[:90]}{extra}")


if __name__ == "__main__":
    asyncio.run(main())
