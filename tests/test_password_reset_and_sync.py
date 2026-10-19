#!/usr/bin/env python3
"""
Test Suite: FB POST PRO — DEPARTMENT PASSWORD SYNC & EMERGENCY RESET
Kiểm thử các cơ chế giải quyết lỗi 'Sai mật khẩu':
1. Tự động đồng bộ mật khẩu từ biến môi trường (DAIKIM_PASSWORD, LONGBIEN_PASSWORD, TUYENNHANSU_PASSWORD) cho tài khoản đã tồn tại.
2. Tự động fallback về ADMIN_PASSWORD cho các tài khoản khởi tạo chưa đổi mật khẩu.
3. API đặt lại mật khẩu ban đầu an toàn qua Web (/api/auth/reset-department-password) bằng ADMIN_PASSWORD.
4. Chặn đứng các hành vi brute-force, sai khóa quản trị, sai tài khoản.
5. Sau khi đổi mật khẩu, đăng nhập thành công và kích hoạt One-Click Login 1-chạm hoàn hảo.
"""

import os
import sys
import json
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Giả lập biến môi trường
os.environ["ADMIN_PASSWORD"] = "RenderMasterKey#2026@"
os.environ["DAIKIM_PASSWORD"] = "DaiKimEnvPass#2026!"

from app import (
    app,
    bootstrap_department_accounts,
    find_user_by_login,
    load_users,
    save_users,
    generate_password_hash,
    check_password_hash,
    MANAGEMENT_ACCOUNTS,
    ADMIN_PASSWORD,
)


def run_tests():
    print("=" * 72)
    print("KIỂM THỬ: ĐỒNG BỘ MẬT KHẨU MÔI TRƯỜNG & ĐẶT LẠI MẬT KHẨU AN TOÀN")
    print("=" * 72)

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    # BƯỚC 1: Mô phỏng tình huống trên Render:
    # Tài khoản daikim, longbien, tuyennhansu đã được tạo với mật khẩu ngẫu nhiên cũ không rõ
    print("\n[TEST 1] Mô phỏng tài khoản Render với mật khẩu ngẫu nhiên cũ:")
    users = load_users()
    for spec in MANAGEMENT_ACCOUNTS:
        uname = spec["username"]
        u = find_user_by_login(uname)
        uid = u["user_id"] if u else "user_" + uname
        # Đặt hash là một mật khẩu ngẫu nhiên không ai biết
        users[uid] = {
            "user_id": uid,
            "username": uname,
            "display_name": spec["display_name"],
            "email": spec["email"],
            "phone": "",
            "password_hash": generate_password_hash("UnknownRandomPass_12345!"),
            "is_active": True,
            "role": "user",
            "departments": [spec["department"]],
            "must_change_password": True,
            "created_at": "2026-10-19T00:00:00+00:00",
            "last_login_at": None,
        }
    save_users(users)
    print("-> Đã thiết lập trạng thái ban đầu: 3 tài khoản tồn tại nhưng mật khẩu cũ không rõ.")

    # Thử đăng nhập bằng mật khẩu sai -> Bị từ chối "Sai mật khẩu"
    res = client.post("/login", json={"department": "dai_kim", "password": "CompletelyWrongPassword#999"})
    assert res.status_code == 400
    assert "Mật khẩu xác thực không chính xác" in res.get_json()["error"]
    print("-> Xác nhận từ chối mật khẩu sai chính xác.")

    # BƯỚC 2: Kiểm tra hàm bootstrap_department_accounts() tự động đồng bộ mật khẩu từ ENV
    print("\n[TEST 2] Kiểm tra bootstrap tự động đồng bộ mật khẩu từ biến môi trường:")
    # daikim có DAIKIM_PASSWORD = "DaiKimEnvPass#2026!"
    # longbien và tuyennhansu không có biến riêng -> fallback về ADMIN_PASSWORD = "RenderMasterKey#2026@"
    bootstrap_department_accounts()

    user_dk = find_user_by_login("daikim")
    assert check_password_hash(user_dk["password_hash"], "DaiKimEnvPass#2026!"), "daikim phải được đồng bộ theo DAIKIM_PASSWORD"
    print("-> PASS: daikim đã tự động đồng bộ theo DAIKIM_PASSWORD.")

    user_lb = find_user_by_login("longbien")
    assert check_password_hash(user_lb["password_hash"], "RenderMasterKey#2026@"), "longbien phải được đồng bộ theo ADMIN_PASSWORD"
    print("-> PASS: longbien đã tự động đồng bộ theo ADMIN_PASSWORD.")

    user_ns = find_user_by_login("tuyennhansu")
    assert check_password_hash(user_ns["password_hash"], "RenderMasterKey#2026@"), "tuyennhansu phải được đồng bộ theo ADMIN_PASSWORD"
    print("-> PASS: tuyennhansu đã tự động đồng bộ theo ADMIN_PASSWORD.")

    # BƯỚC 3: Đăng nhập thử sau khi đồng bộ
    print("\n[TEST 3] Kiểm tra đăng nhập thành công sau khi đồng bộ:")
    res_dk = client.post("/login", json={"department": "dai_kim", "password": "DaiKimEnvPass#2026!"})
    assert res_dk.status_code == 200 and res_dk.get_json()["ok"] is True
    print("-> PASS: Đại Kim đăng nhập thành công!")

    res_lb = client.post("/login", json={"department": "long_bien", "password": "RenderMasterKey#2026@"})
    assert res_lb.status_code == 200 and res_lb.get_json()["ok"] is True
    print("-> PASS: Long Biên đăng nhập thành công bằng ADMIN_PASSWORD!")

    res_ns = client.post("/login", json={"department": "tuyen_nhan_su", "password": "RenderMasterKey#2026@"})
    assert res_ns.status_code == 200 and res_ns.get_json()["ok"] is True
    print("-> PASS: Tuyển nhân sự đăng nhập thành công bằng ADMIN_PASSWORD!")

    # BƯỚC 4: Kiểm tra API Web Đặt lại mật khẩu ban đầu bằng ADMIN_PASSWORD
    print("\n[TEST 4] Kiểm tra API Web /api/auth/reset-department-password:")
    # 4.1: Sai admin_key -> 403 Forbidden
    res = client.post("/api/auth/reset-department-password", json={
        "department": "dai_kim",
        "admin_key": "WrongMasterKey!",
        "new_password": "NewPass123456@",
        "confirm_password": "NewPass123456@",
    })
    assert res.status_code == 403
    assert "không chính xác" in res.get_json()["error"]
    print("-> PASS: Khóa ADMIN sai bị từ chối chính xác (403 Forbidden).")

    # 4.2: Thiếu admin_key -> 403 Forbidden
    res = client.post("/api/auth/reset-department-password", json={
        "department": "dai_kim",
        "admin_key": "",
        "new_password": "NewPass123456@",
        "confirm_password": "NewPass123456@",
    })
    assert res.status_code == 403
    print("-> PASS: Thiếu khóa ADMIN bị từ chối.")

    # 4.3: Không trùng khớp mật khẩu mới -> 400 Bad Request
    res = client.post("/api/auth/reset-department-password", json={
        "department": "dai_kim",
        "admin_key": "RenderMasterKey#2026@",
        "new_password": "NewPass123456@",
        "confirm_password": "DifferentPass@",
    })
    assert res.status_code == 400
    assert "không trùng khớp" in res.get_json()["error"]
    print("-> PASS: Mật khẩu mới không khớp bị từ chối (400 Bad Request).")

    # 4.4: Mật khẩu quá ngắn -> 400 Bad Request
    res = client.post("/api/auth/reset-department-password", json={
        "department": "dai_kim",
        "admin_key": "RenderMasterKey#2026@",
        "new_password": "123",
        "confirm_password": "123",
    })
    assert res.status_code == 400
    assert "ít nhất 6 ký tự" in res.get_json()["error"]
    print("-> PASS: Mật khẩu ngắn < 6 ký tự bị từ chối.")

    # 4.5: Tài khoản ngoài 3 bộ phận -> 400 Bad Request
    res = client.post("/api/auth/reset-department-password", json={
        "username": "hacker_account",
        "admin_key": "RenderMasterKey#2026@",
        "new_password": "NewPass123456@",
        "confirm_password": "NewPass123456@",
    })
    assert res.status_code == 400
    assert "Chỉ được phép đặt lại mật khẩu cho 3 bộ phận" in res.get_json()["error"]
    print("-> PASS: Không cho phép đổi mật khẩu tài khoản ngoài 3 bộ phận.")

    # 4.6: Đổi mật khẩu thành công cho Long Biên
    new_lb_pass = "LongBienCustomPass#999"
    res = client.post("/api/auth/reset-department-password", json={
        "department": "long_bien",
        "admin_key": "RenderMasterKey#2026@",
        "new_password": new_lb_pass,
        "confirm_password": new_lb_pass,
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    print(f"-> PASS: Đặt lại mật khẩu thành công: {data['message']}")

    # Kiểm tra đăng nhập ngay bằng mật khẩu mới của Long Biên
    res_lb_new = client.post("/login", json={
        "department": "long_bien",
        "password": new_lb_pass,
        "remember_device": "1",
    })
    assert res_lb_new.status_code == 200 and res_lb_new.get_json()["ok"] is True
    print("-> PASS: Long Biên đăng nhập thành công bằng mật khẩu mới và kích hoạt 1-Click!")

    # BƯỚC 5: Kiểm tra giao diện HTML có chứa chức năng đặt lại mật khẩu
    print("\n[TEST 5] Kiểm tra giao diện login.html:")
    client.get("/logout")
    res_html = client.get("/login")
    assert res_html.status_code == 200
    html = res_html.get_data(as_text=True)
    assert "openResetPasswordModal" in html
    assert "resetModal" in html
    assert "ADMIN_PASSWORD" in html
    print("-> PASS: Giao diện web tích hợp đầy đủ nút và modal Đặt lại mật khẩu bằng ADMIN_PASSWORD.")

    # BƯỚC 6: Kiểm tra tình huống thực tế người dùng nhập trực tiếp 'DAIKIM_PASSWORD'
    print("\n[TEST 6] Kiểm tra nhập trực tiếp 'DAIKIM_PASSWORD' (như ảnh thực tế của người dùng):")
    client.get("/logout")
    # Đặt daikim về trạng thái ban đầu chưa đổi mật khẩu với mật khẩu bất kỳ
    users = load_users()
    u_dk = find_user_by_login("daikim")
    users[u_dk["user_id"]]["password_hash"] = generate_password_hash("SomeOldRandom#123")
    users[u_dk["user_id"]]["must_change_password"] = True
    users[u_dk["user_id"]]["last_login_at"] = None
    save_users(users)

    # Người dùng nhập trực tiếp "DAIKIM_PASSWORD" vào form
    res_direct = client.post("/login", json={
        "department": "dai_kim",
        "password": "DAIKIM_PASSWORD",
        "remember_device": "1",
    })
    assert res_direct.status_code == 200, f"Đăng nhập bằng DAIKIM_PASSWORD phải thành công, nhận được {res_direct.status_code}: {res_direct.get_data(as_text=True)}"
    assert res_direct.get_json()["ok"] is True
    assert res_direct.get_json()["redirect"] == "/dashboard"
    print("-> PASS: Nhập trực tiếp 'DAIKIM_PASSWORD' xác thực thành công và chuyển hướng tới /dashboard!")

    # Kiểm tra database đã cập nhật hash và mở khóa tài khoản
    u_after = find_user_by_login("daikim")
    assert check_password_hash(u_after["password_hash"], "DAIKIM_PASSWORD")
    assert u_after["must_change_password"] is False
    print("-> PASS: Database tự động lưu hash 'DAIKIM_PASSWORD' và set must_change_password=False.")

    print("\n" + "=" * 72)
    print("KẾT QUẢ: TOÀN BỘ 6 BƯỚC KIỂM THỬ ĐỀU THÀNH CÔNG (PASS 100%)!")
    print("DEPARTMENT PASSWORD SYNC & EMERGENCY RESET: PASS")
    print("=" * 72)


if __name__ == "__main__":
    run_tests()
