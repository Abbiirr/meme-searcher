from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
from pathlib import Path
from urllib import error, request

sys.path.insert(0, "/app/backend")

from open_webui.models.auths import Auths
from open_webui.models.functions import FunctionForm, FunctionMeta, Functions
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash
from open_webui.utils.plugin import load_function_module_by_id, replace_imports


FUNCTION_ID = "meme_search"
FUNCTION_NAME = "Meme Search"
FUNCTION_DESCRIPTION = (
    "Search the local Phase 0 meme corpus through POST /search and render matching thumbnails inline."
)
FUNCTION_PATH = Path(__file__).resolve().parent / "functions" / "meme_search_pipe.py"
WAIT_TIMEOUT_SECONDS = int(os.environ.get("OPEN_WEBUI_PROVISION_WAIT_SECONDS", "600"))


async def resolve_maybe_async(value):
    if inspect.isawaitable(value):
        return await value
    return value


def extract_users(payload) -> list:
    if isinstance(payload, dict):
        users = payload.get("users")
        if isinstance(users, list):
            return users
        return []

    users = getattr(payload, "users", None)
    if isinstance(users, list):
        return users

    if isinstance(payload, list):
        return payload

    return []


def wait_for_webui() -> None:
    port = os.environ.get("PORT", "8080").strip() or "8080"
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + WAIT_TIMEOUT_SECONDS

    while time.time() < deadline:
        try:
            with request.urlopen(url, timeout=5):
                return
        except Exception:
            time.sleep(2)

    raise RuntimeError(f"Open WebUI did not become healthy within {WAIT_TIMEOUT_SECONDS}s")


async def ensure_admin_user():
    users_payload = await resolve_maybe_async(Users.get_users(limit=100))
    users = extract_users(users_payload)
    admin_user = next((user for user in users if user.role == "admin"), None)
    if admin_user:
        return admin_user

    email = os.environ.get("OPEN_WEBUI_ADMIN_EMAIL", "admin@localhost").strip().lower()
    password = os.environ.get("OPEN_WEBUI_ADMIN_PASSWORD", "admin").strip()
    name = os.environ.get("OPEN_WEBUI_ADMIN_NAME", "Admin").strip() or "Admin"

    if not email or not password:
        raise RuntimeError("OPEN_WEBUI_ADMIN_EMAIL and OPEN_WEBUI_ADMIN_PASSWORD must be set")

    existing = await resolve_maybe_async(Users.get_user_by_email(email))
    if existing:
        return existing

    user = await resolve_maybe_async(
        Auths.insert_new_auth(
            email,
            get_password_hash(password),
            name,
            "/user.png",
            "admin",
        )
    )
    if not user:
        raise RuntimeError("Failed to create the bootstrap Open WebUI admin user")
    return user


async def load_pipe_content() -> tuple[str, dict]:
    content = FUNCTION_PATH.read_text(encoding="utf-8")
    content = replace_imports(content)
    _, function_type, frontmatter = await resolve_maybe_async(
        load_function_module_by_id(FUNCTION_ID, content=content)
    )
    if function_type != "pipe":
        raise RuntimeError(f"{FUNCTION_PATH} did not load as a pipe function")
    return content, frontmatter


async def upsert_function(user_id: str, content: str, frontmatter: dict) -> None:
    form = FunctionForm(
        id=FUNCTION_ID,
        name=FUNCTION_NAME,
        content=content,
        meta=FunctionMeta(
            description=FUNCTION_DESCRIPTION,
            manifest=frontmatter,
        ),
    )

    existing = await resolve_maybe_async(Functions.get_function_by_id(FUNCTION_ID))
    if existing:
        updated = await resolve_maybe_async(
            Functions.update_function_by_id(
                FUNCTION_ID,
                {
                    "name": FUNCTION_NAME,
                    "content": content,
                    "meta": form.meta.model_dump(),
                    "type": "pipe",
                    "user_id": existing.user_id or user_id,
                    "is_active": True,
                },
            )
        )
        if not updated:
            raise RuntimeError("Failed to update the Open WebUI meme-search function")
        return

    created = await resolve_maybe_async(Functions.insert_new_function(user_id, "pipe", form))
    if not created:
        raise RuntimeError("Failed to create the Open WebUI meme-search function")

    activated = await resolve_maybe_async(
        Functions.update_function_by_id(
            FUNCTION_ID,
            {
                "is_active": True,
            },
        )
    )
    if not activated:
        raise RuntimeError("Failed to activate the Open WebUI meme-search function")


async def async_main() -> int:
    admin_user = await ensure_admin_user()
    content, frontmatter = await load_pipe_content()
    await upsert_function(admin_user.id, content, frontmatter)
    print(f"Provisioned Open WebUI pipe `{FUNCTION_ID}` for user `{admin_user.email}`.")
    return 0


def main() -> int:
    wait_for_webui()
    return asyncio.run(async_main())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Open WebUI provisioning failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
