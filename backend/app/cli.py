"""Administrative command-line utilities."""

import argparse
import asyncio
import getpass
import re

from .auth import AuthRepository, hash_password, normalize_username
from .database import SessionFactory


async def create_admin(username: str, display_name: str) -> None:
    normalized = normalize_username(username)
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{3,64}", normalized):
        raise SystemExit("用户名必须为 3 到 64 个字母、数字或 _.@+- 字符")
    normalized_display_name = display_name.strip()
    if not 1 <= len(normalized_display_name) <= 80:
        raise SystemExit("显示名称长度必须为 1 到 80 个字符")
    password = getpass.getpass("管理员密码（至少 8 个字符）：")
    confirmation = getpass.getpass("再次输入管理员密码：")
    if password != confirmation:
        raise SystemExit("两次输入的密码不一致")
    if len(password) < 8 or len(password) > 128:
        raise SystemExit("密码长度必须为 8 到 128 个字符")

    async with SessionFactory() as session:
        repository = AuthRepository(session)
        if await repository.get_user_by_username(normalized) is not None:
            raise SystemExit("该用户名已经存在")
        user = await repository.create_user(
            username=normalized,
            display_name=normalized_display_name,
            password_hash=hash_password(password),
            role="admin",
        )
        await repository.mark_bootstrap_completed()
        await repository.add_audit_log(
            actor_user_id=user.id,
            target_user_id=user.id,
            action="system.admin_bootstrapped",
            details={},
            ip_address=None,
        )
    print(f"管理员 {normalized} 创建成功。")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="交互式创建管理员账户")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", default="管理员")
    args = parser.parse_args()
    if args.command == "create-admin":
        asyncio.run(create_admin(args.username, args.display_name))


if __name__ == "__main__":
    main()
