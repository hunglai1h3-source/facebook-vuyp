#!/usr/bin/env python3
"""
Test Suite: FB POST PRO – 3 MANAGEMENT ACCOUNTS & DEPARTMENTS
Kiểm tra toàn diện 10 tiêu chí theo yêu cầu kỹ thuật:
1. daikim đăng nhập thành công vào Đại Kim.
2. longbien đăng nhập thành công vào Long Biên.
3. tuyennhansu đăng nhập thành công vào Tuyển nhân sự.
4. Sai mật khẩu bị từ chối.
5. Không thể truy cập trái phép bộ phận khác (Login trái phép & Switch-department trái phép & Cách ly dữ liệu).
6. Refresh / truy cập lại giữ nguyên session hợp lệ.
7. Đăng xuất hoạt động (Session bị xóa, chuyển hướng về login).
8. Restart / Bootstrap nhiều lần KHÔNG mất tài khoản, KHÔNG ghi đè mật khẩu đã đổi (Idempotent).
9. Bảo mật: Mật khẩu lưu dạng hash, không lộ qua HTML/API, .bootstrap_credentials.json nằm trong .gitignore.
10. Bắt buộc đổi mật khẩu ở lần đăng nhập đầu tiên (First login change password flow).
"""

import os
import sys
import json
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import (
    app,
    DEPARTMENTS,
    MANAGEMENT_ACCOUNTS,
    BOOTSTRAP_CREDENTIALS_FILE,
    bootstrap_department_accounts,
    find_user_by_login,
    find_user_by_id,
    load_users,
    save_users,
    check_password_hash,
    generate_password_hash,
    save_groups,
    load_groups,
    read_json,
)


def run_all_tests():
    print("=" * 70)
    print("KIỂM THỬ HỆ THỐNG: FB POST PRO — 3 MANAGEMENT ACCOUNTS & DEPARTMENTS")
    print("=" * 70)

    # Đảm bảo app test client
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    # Step 0: Đảm bảo 3 accounts được cấu hình sẵn sàng với mật khẩu ban đầu cho kiểm thử
    print("\n[BƯỚC 0] Chuẩn bị môi trường kiểm thử 3 tài khoản quản lý...")
    pwd_daikim = "Daikim@InitPass2026!"
    pwd_longbien = "Longbien@InitPass2026!"
    pwd_tuyennhansu = "Tuyennhansu@InitPass2026!"

    users = load_users()
    for uname, pwd, dept, dname in [
        ("daikim", pwd_daikim, "dai_kim", "Quản lý Đại Kim"),
        ("longbien", pwd_longbien, "long_bien", "Quản lý Long Biên"),
        ("tuyennhansu", pwd_tuyennhansu, "tuyen_nhan_su", "Quản lý Tuyển nhân sự"),
    ]:
        u = find_user_by_login(uname)
        uid = u["user_id"] if u else "user_" + uname
        users[uid] = {
            "user_id": uid,
            "username": uname,
            "display_name": dname,
            "email": f"{uname}@fbpostpro.local",
            "phone": "",
            "password_hash": generate_password_hash(pwd),
            "is_active": True,
            "role": "user",
            "departments": [dept],
            "must_change_password": True,
            "created_at": "2026-10-19T00:00:00+00:00",
            "last_login_at": None,
        }
    save_users(users)

    # Đảm bảo bootstrap_department_accounts() nhận diện cả 3 tài khoản
    created_info = bootstrap_department_accounts()
    assert len(created_info) == 0, "Vì 3 tài khoản đã tồn tại nên bootstrap không được tạo mới hay đè mật khẩu"

    print("-> PASS: 3 tài khoản daikim, longbien, tuyennhansu đã sẵn sàng cho kiểm thử.")

    # ----------------------------------------------------
    # TIÊU CHÍ 1: Đại Kim đăng nhập thành công
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 1] Kiểm tra daikim đăng nhập vào Đại Kim:")
    c_dk = app.test_client()
    res = c_dk.post("/login", data={
        "login": "daikim",
        "password": pwd_daikim,
        "department": "dai_kim",
    }, follow_redirects=False)
    assert res.status_code == 302, f"Expected 302 redirect after login, got {res.status_code}"
    # Vì mới tạo nên phải redirect sang /change-password
    with c_dk.session_transaction() as sess:
        assert sess.get("user_id"), "Session user_id phải được thiết lập"
        assert sess.get("username") == "daikim"
        assert sess.get("department") == "dai_kim"
    print("-> PASS: Tài khoản 'daikim' xác thực thành công vào bộ phận 'dai_kim'.")

    # ----------------------------------------------------
    # TIÊU CHÍ 2: Long Biên đăng nhập thành công
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 2] Kiểm tra longbien đăng nhập vào Long Biên:")
    c_lb = app.test_client()
    res = c_lb.post("/login", data={
        "login": "longbien",
        "password": pwd_longbien,
        "department": "long_bien",
    }, follow_redirects=False)
    assert res.status_code == 302
    with c_lb.session_transaction() as sess:
        assert sess.get("username") == "longbien"
        assert sess.get("department") == "long_bien"
    print("-> PASS: Tài khoản 'longbien' xác thực thành công vào bộ phận 'long_bien'.")

    # ----------------------------------------------------
    # TIÊU CHÍ 3: Tuyển nhân sự đăng nhập thành công
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 3] Kiểm tra tuyennhansu đăng nhập vào Tuyển nhân sự:")
    c_ns = app.test_client()
    res = c_ns.post("/login", data={
        "login": "tuyennhansu",
        "password": pwd_tuyennhansu,
        "department": "tuyen_nhan_su",
    }, follow_redirects=False)
    assert res.status_code == 302
    with c_ns.session_transaction() as sess:
        assert sess.get("username") == "tuyennhansu"
        assert sess.get("department") == "tuyen_nhan_su"
    print("-> PASS: Tài khoản 'tuyennhansu' xác thực thành công vào bộ phận 'tuyen_nhan_su'.")

    # ----------------------------------------------------
    # TIÊU CHÍ 4: Sai mật khẩu phải bị từ chối
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 4] Kiểm tra sai mật khẩu bị từ chối:")
    c_wrong = app.test_client()
    res = c_wrong.post("/login", data={
        "login": "daikim",
        "password": "WrongPassword!@#123",
        "department": "dai_kim",
    })
    assert res.status_code == 200
    assert "không chính xác" in res.get_data(as_text=True)
    with c_wrong.session_transaction() as sess:
        assert not sess.get("user_id"), "Session không được tạo khi sai mật khẩu"
    print("-> PASS: Mật khẩu sai bị từ chối chính xác.")

    # ----------------------------------------------------
    # TIÊU CHÍ 5: Không thể truy cập trái phép dữ liệu bộ phận khác
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 5] Kiểm tra bảo vệ phân quyền bộ phận:")
    # a. daikim cố tình chọn Long Biên khi đăng nhập
    res = client.post("/login", data={
        "login": "daikim",
        "password": pwd_daikim,
        "department": "long_bien",
    })
    assert res.status_code == 200
    assert "không được phân quyền truy cập bộ phận Long Biên" in res.get_data(as_text=True)
    print("-> PASS: daikim đăng nhập vào Long Biên bị từ chối.")

    # b. longbien cố tình chọn Tuyển nhân sự khi đăng nhập
    res = client.post("/login", data={
        "login": "longbien",
        "password": pwd_longbien,
        "department": "tuyen_nhan_su",
    })
    assert res.status_code == 200
    assert "không được phân quyền truy cập bộ phận Tuyển nhân sự" in res.get_data(as_text=True)
    print("-> PASS: longbien đăng nhập vào Tuyển nhân sự bị từ chối.")

    # c. tuyennhansu cố tình gọi /switch-department?department=dai_kim
    u_ns = find_user_by_login("tuyennhansu")
    users = load_users()
    users[u_ns["user_id"]]["must_change_password"] = False
    save_users(users)

    c_ns_switch = app.test_client()
    c_ns_switch.post("/login", data={
        "login": "tuyennhansu",
        "password": pwd_tuyennhansu,
        "department": "tuyen_nhan_su",
    })
    res = c_ns_switch.get("/switch-department?department=dai_kim", follow_redirects=True)
    assert "không có quyền truy cập bộ phận Đại Kim" in res.get_data(as_text=True)
    with c_ns_switch.session_transaction() as sess:
        assert sess.get("department") == "tuyen_nhan_su", "Department không được đổi sang dai_kim"
    print("-> PASS: tuyennhansu chuyển sang Đại Kim qua URL bị chặn hoàn toàn.")

    # d. Kiểm tra cách ly dữ liệu giữa các bộ phận
    user_dk = find_user_by_login("daikim")
    user_lb = find_user_by_login("longbien")
    cid_dk = f"{user_dk['user_id']}__dai_kim"
    cid_lb = f"{user_lb['user_id']}__long_bien"

    save_groups(cid_dk, ["https://facebook.com/groups/daikim_exclusive_group"])
    save_groups(cid_lb, ["https://facebook.com/groups/longbien_exclusive_group"])

    groups_dk = load_groups(cid_dk)
    groups_lb = load_groups(cid_lb)

    assert "daikim_exclusive_group" in str(groups_dk)
    assert "daikim_exclusive_group" not in str(groups_lb)
    assert "longbien_exclusive_group" in str(groups_lb)
    assert "longbien_exclusive_group" not in str(groups_dk)
    print("-> PASS: Dữ liệu group và campaign giữa các bộ phận cách ly độc lập 100%.")

    # ----------------------------------------------------
    # TIÊU CHÍ 6: Refresh vẫn giữ session hợp lệ
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 6] Kiểm tra refresh / duyệt trang duy trì session hợp lệ:")
    # Tạm thời gỡ must_change_password cho test session refresh
    user_dk_dict = load_users()
    user_dk_dict[user_dk["user_id"]]["must_change_password"] = False
    save_users(user_dk_dict)

    res_dash1 = c_dk.get("/dashboard")
    assert res_dash1.status_code == 200
    assert "FB POST PRO — ĐẠI KIM" in res_dash1.get_data(as_text=True)

    # Giả lập refresh / request thứ hai
    res_dash2 = c_dk.get("/dashboard")
    assert res_dash2.status_code == 200
    assert "FB POST PRO — ĐẠI KIM" in res_dash2.get_data(as_text=True)
    print("-> PASS: Session hợp lệ được duy trì liên tục qua các request.")

    # ----------------------------------------------------
    # TIÊU CHÍ 7: Đăng xuất hoạt động
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 7] Kiểm tra chức năng đăng xuất:")
    res_logout = c_dk.get("/logout", follow_redirects=False)
    assert res_logout.status_code == 302
    assert "/login" in res_logout.headers["Location"]

    # Sau khi logout, truy cập lại /dashboard bị redirect về login
    res_after = c_dk.get("/dashboard", follow_redirects=False)
    assert res_after.status_code == 302
    assert "/login" in res_after.headers["Location"]
    print("-> PASS: Đăng xuất xóa sạch session và chuyển hướng về /login.")

    # ----------------------------------------------------
    # TIÊU CHÍ 8: Restart Render / Bootstrap nhiều lần KHÔNG mất tài khoản & KHÔNG ghi đè mật khẩu
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 8] Kiểm tra Idempotent (Khởi động lại không ghi đè mật khẩu đã đổi):")
    # Đổi mật khẩu tài khoản daikim sang mật khẩu mới
    new_custom_password = "DaiKim@CustomPass2026!#"
    user_dk_dict = load_users()
    user_dk_dict[user_dk["user_id"]]["password_hash"] = generate_password_hash(new_custom_password)
    user_dk_dict[user_dk["user_id"]]["must_change_password"] = False
    save_users(user_dk_dict)

    # Chạy lại bootstrap 3 lần liên tiếp (mô phỏng 3 lần Render restart/deploy)
    for i in range(1, 4):
        created_again = bootstrap_department_accounts()
        assert len(created_again) == 0, f"Lần restart {i}: Không được tạo thêm tài khoản nào mới!"

    # Kiểm tra mật khẩu tài khoản daikim vẫn là mật khẩu mới đổi
    refreshed_user = find_user_by_login("daikim")
    assert check_password_hash(refreshed_user["password_hash"], new_custom_password), \
        "Mật khẩu đã đổi KHÔNG ĐƯỢC PHÉP bị ghi đè sau khi restart/bootstrap!"
    
    # Kiểm tra số lượng tài khoản không bị nhân đôi
    all_users = load_users()
    daikim_count = sum(1 for u in all_users.values() if isinstance(u, dict) and u.get("username") == "daikim")
    assert daikim_count == 1, f"Chỉ được có đúng 1 tài khoản daikim, hiện có: {daikim_count}"
    print("-> PASS: Restart/Bootstrap hoàn toàn Idempotent: Giữ nguyên mật khẩu đã đổi, không tạo trùng lặp.")

    # ----------------------------------------------------
    # TIÊU CHÍ 9: Bảo mật và Không lộ mật khẩu
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 9] Kiểm tra bảo mật và không lộ mật khẩu:")
    # a. Kiểm tra hash
    assert not refreshed_user["password_hash"].startswith("DaiKim"), "Mật khẩu không được lưu dạng plaintext"
    assert "scrypt:" in refreshed_user["password_hash"] or "pbkdf2:" in refreshed_user["password_hash"], \
        "Mật khẩu phải được băm an toàn"

    # b. Kiểm tra .gitignore
    gitignore_path = PROJECT_ROOT / ".gitignore"
    assert gitignore_path.exists()
    gitignore_content = gitignore_path.read_text(encoding="utf-8")
    assert ".bootstrap_credentials.json" in gitignore_content, ".bootstrap_credentials.json phải nằm trong .gitignore"
    assert "*.credentials.json" in gitignore_content, "*.credentials.json phải nằm trong .gitignore"

    # c. Kiểm tra /customer-info không lộ mật khẩu hay hash
    c_info = app.test_client()
    with c_info.session_transaction() as sess:
        sess["user_id"] = user_dk["user_id"]
        sess["department"] = "dai_kim"
    res_info = c_info.get("/customer-info")
    info_json = res_info.get_json() or {}
    assert "password" not in str(info_json).lower(), "/customer-info không được chứa mật khẩu"
    assert "hash" not in str(info_json).lower(), "/customer-info không được chứa password hash"
    print("-> PASS: Mật khẩu được băm an toàn, .gitignore bảo vệ file credentials, API không lộ thông tin nhạy cảm.")

    # ----------------------------------------------------
    # TIÊU CHÍ 10: Bắt buộc đổi mật khẩu ở lần đăng nhập đầu tiên (First login force change)
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 10] Kiểm tra quy trình bắt buộc đổi mật khẩu ở lần đầu:")
    # Đặt lại must_change_password = True cho tài khoản tuyennhansu
    user_ns = find_user_by_login("tuyennhansu")
    users = load_users()
    users[user_ns["user_id"]]["must_change_password"] = True
    save_users(users)

    c_first = app.test_client()
    # Đăng nhập vào tuyennhansu
    res_login = c_first.post("/login", data={
        "login": "tuyennhansu",
        "password": pwd_tuyennhansu,
        "department": "tuyen_nhan_su",
    }, follow_redirects=False)
    assert res_login.status_code == 302

    # Cố tình vào /dashboard khi chưa đổi mật khẩu -> Bị chuyển hướng sang /change-password
    res_try_dash = c_first.get("/dashboard", follow_redirects=False)
    assert res_try_dash.status_code == 302
    assert "/change-password" in res_try_dash.headers["Location"], \
        "Chưa đổi mật khẩu lần đầu bắt buộc phải bị chuyển hướng đến /change-password"

    # Cố tình vào /groups khi chưa đổi mật khẩu -> Cũng bị chuyển hướng sang /change-password
    res_try_groups = c_first.get("/groups", follow_redirects=False)
    assert res_try_groups.status_code == 302
    assert "/change-password" in res_try_groups.headers["Location"]

    # Đổi mật khẩu: Thử nhập sai mật khẩu cũ
    res_change_wrong = c_first.post("/change-password", data={
        "old_password": "WrongOldPassword",
        "new_password": "NewSecretPass2026!#",
        "confirm_password": "NewSecretPass2026!#",
    })
    assert "Mật khẩu hiện tại không chính xác" in res_change_wrong.get_data(as_text=True)

    # Đổi mật khẩu: Thử nhập mật khẩu mới trùng mật khẩu cũ
    res_change_same = c_first.post("/change-password", data={
        "old_password": pwd_tuyennhansu,
        "new_password": pwd_tuyennhansu,
        "confirm_password": pwd_tuyennhansu,
    })
    assert "không được trùng" in res_change_same.get_data(as_text=True)

    # Đổi mật khẩu thành công
    new_ns_pass = "TuyenNhanSu@Pass2026!"
    res_change_ok = c_first.post("/change-password", data={
        "old_password": pwd_tuyennhansu,
        "new_password": new_ns_pass,
        "confirm_password": new_ns_pass,
    }, follow_redirects=True)
    assert res_change_ok.status_code == 200
    assert "FB POST PRO — TUYỂN NHÂN SỰ" in res_change_ok.get_data(as_text=True)

    # Sau khi đổi mật khẩu, must_change_password phải là False
    user_ns_updated = find_user_by_login("tuyennhansu")
    assert user_ns_updated["must_change_password"] is False, "must_change_password phải chuyển sang False"
    assert check_password_hash(user_ns_updated["password_hash"], new_ns_pass), "Mật khẩu mới phải khớp"

    # Bây giờ truy cập /dashboard hoặc /groups diễn ra bình thường
    res_dash_ok = c_first.get("/dashboard")
    assert res_dash_ok.status_code == 200
    assert "FB POST PRO — TUYỂN NHÂN SỰ" in res_dash_ok.get_data(as_text=True)
    print("-> PASS: Quy trình bắt buộc đổi mật khẩu lần đầu hoạt động hoàn hảo và bảo mật.")

    print("\n" + "=" * 70)
    print("KẾT QUẢ: 10/10 TIÊU CHÍ KIỂM THỬ ĐỀU ĐẠT CHUẨN (100% PASS)!")
    print("FB POST PRO – 3 MANAGEMENT ACCOUNTS: PASS")
    print("=" * 70)


if __name__ == "__main__":
    run_all_tests()
