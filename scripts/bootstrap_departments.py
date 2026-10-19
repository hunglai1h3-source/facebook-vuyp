#!/usr/bin/env python3
"""
FB POST PRO — Bootstrap & Account Management CLI
Quản lý 3 tài khoản bộ phận: daikim, longbien, tuyennhansu
"""

import sys
import os
import argparse
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import (
    bootstrap_department_accounts,
    MANAGEMENT_ACCOUNTS,
    BOOTSTRAP_CREDENTIALS_FILE,
    find_user_by_login,
    generate_strong_password,
    generate_password_hash,
    save_users,
    load_users,
    postgres_enabled,
    postgres_connect,
    init_users_table,
    read_json,
)


def cmd_bootstrap():
    print("=" * 60)
    print("FB POST PRO — KHỞI TẠO 3 TÀI KHOẢN BỘ PHẬN QUẢN LÝ")
    print("=" * 60)
    print(f"PostgreSQL Enabled: {postgres_enabled()}")

    if postgres_enabled():
        init_users_table()

    info = bootstrap_department_accounts()

    print("\nKết quả kiểm tra / khởi tạo:")
    print("-" * 60)
    for spec in MANAGEMENT_ACCOUNTS:
        uname = spec["username"]
        dept = spec["department"]
        user = find_user_by_login(uname)

        if not user:
            print(f"[!] {uname} ({dept}): Không tìm thấy tài khoản sau bootstrap.")
            continue

        is_created_now = uname in info and info[uname].get("initial_password")
        if is_created_now:
            pwd_info = info[uname]["initial_password"]
            print(f"[+] {uname.upper():<12} | Bộ phận: {spec['display_name']}")
            print(f"    - Username: {uname}")
            print(f"    - Email:    {user.get('email')}")
            print(f"    - Mật khẩu: {pwd_info}")
            print(f"    - Trạng thái: Mới tạo (Yêu cầu đổi mật khẩu lần đầu)")
        else:
            print(f"[*] {uname.upper():<12} | Bộ phận: {spec['display_name']}")
            print(f"    - Username: {uname}")
            print(f"    - Email:    {user.get('email')}")
            print(f"    - Mật khẩu: [Đã tồn tại - không ghi đè]")
            print(f"    - Phân quyền: {user.get('departments')}")
            print(f"    - Bắt buộc đổi MK: {user.get('must_change_password')}")
        print()

    print("-" * 60)
    if BOOTSTRAP_CREDENTIALS_FILE.exists():
        print(f"File thông tin mật khẩu ban đầu được lưu tại: {BOOTSTRAP_CREDENTIALS_FILE}")
        print("Lưu ý: File này đã được thêm vào .gitignore để không bao giờ bị lộ lên git.")
    print("=" * 60)


def cmd_show_credentials():
    if not BOOTSTRAP_CREDENTIALS_FILE.exists():
        print(f"Không tìm thấy file credentials: {BOOTSTRAP_CREDENTIALS_FILE}")
        print("Nếu tài khoản đã được tạo từ trước, mật khẩu ban đầu chỉ hiển thị lúc khởi tạo.")
        return

    data = read_json(BOOTSTRAP_CREDENTIALS_FILE, {})
    print("=" * 60)
    print("THÔNG TIN MẬT KHẨU BAN ĐẦU ĐÃ LƯU TRÊN SERVER")
    print("=" * 60)
    for uname, details in data.items():
        print(f"Tài khoản: {uname}")
        print(f"  • Bộ phận: {details.get('display_name')} ({details.get('department')})")
        print(f"  • Mật khẩu ban đầu: {details.get('initial_password')}")
        print(f"  • Nguồn: {details.get('source')}")
        print(f"  • Thời gian tạo: {details.get('created_at')}")
        print()
    print("=" * 60)


def cmd_reset_password(username, custom_password=None):
    user = find_user_by_login(username)
    if not user:
        print(f"Lỗi: Không tìm thấy người dùng '{username}'!")
        return

    user_id = user["user_id"]
    if custom_password:
        if len(custom_password) < 8:
            print("Lỗi: Mật khẩu mới phải có ít nhất 8 ký tự!")
            return
        new_pass = custom_password
    else:
        new_pass = generate_strong_password(14)

    new_hash = generate_password_hash(new_pass)

    if postgres_enabled():
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_users
                    SET password_hash = %s, must_change_password = TRUE
                    WHERE user_id = %s
                    """,
                    (new_hash, user_id),
                )
            conn.commit()
    else:
        users = load_users()
        if user_id in users:
            users[user_id]["password_hash"] = new_hash
            users[user_id]["must_change_password"] = True
            save_users(users)

    print("=" * 60)
    print(f"ĐẶT LẠI MẬT KHẨU THÀNH CÔNG CHO: @{username}")
    print(f"  • Mật khẩu mới: {new_pass}")
    print(f"  • Yêu cầu đổi mật khẩu ở lần đăng nhập tới: TRUE")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="FB POST PRO Management Accounts CLI")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Khởi tạo 3 tài khoản bộ phận nếu chưa có",
    )
    parser.add_argument(
        "--show-credentials",
        action="store_true",
        help="Xem thông tin mật khẩu ban đầu đã lưu",
    )
    parser.add_argument(
        "--reset-password",
        metavar="USERNAME",
        help="Đặt lại mật khẩu cho một tài khoản",
    )
    parser.add_argument(
        "--password",
        metavar="NEW_PASS",
        help="Mật khẩu mới khi dùng với --reset-password (nếu không cung cấp, hệ thống tự sinh ngẫu nhiên)",
    )

    args = parser.parse_args()

    if args.show_credentials:
        cmd_show_credentials()
    elif args.reset_password:
        cmd_reset_password(args.reset_password, args.password)
    else:
        cmd_bootstrap()


if __name__ == "__main__":
    main()
