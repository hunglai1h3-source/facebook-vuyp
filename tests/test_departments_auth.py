import json
import os
import sys
from werkzeug.security import generate_password_hash

# Đảm bảo đường dẫn import
sys.path.insert(0, os.path.abspath("."))

from app import (
    app,
    DEPARTMENTS,
    VALID_DEPARTMENTS,
    load_users,
    save_users,
    find_user_by_login,
    get_user_departments,
    user_has_department_access,
    get_customer_id,
    sanitize_customer_id,
    save_groups,
    load_groups,
)

def run_tests():
    print("=" * 60)
    print("BAT DAU KIEM THU PHAN QUYEN 3 BO PHAN & DANG NHAP")
    print("=" * 60)

    # 1. Kiểm tra cấu hình 3 bộ phận
    print("\n[TEST 1] Kiem tra dinh nghia 3 bo phan:")
    assert "dai_kim" in DEPARTMENTS, "Thieu Dai Kim"
    assert "long_bien" in DEPARTMENTS, "Thieu Long Bien"
    assert "tuyen_nhan_su" in DEPARTMENTS, "Thieu Tuyen Nhan Su"
    assert len(DEPARTMENTS) == 3, f"Phai co dung 3 bo phan, hien tai: {len(DEPARTMENTS)}"
    print("-> PASS: Dung 3 bo phan: Dai Kim, Long Bien, Tuyen nhan su.")

    # 2. Tạo test users với các quyền khác nhau
    users = load_users()

    # User 1: Chỉ có quyền Đại Kim
    uid_dk = "test_user_dai_kim_only"
    users[uid_dk] = {
        "username": "user_daikim",
        "email": "daikim@test.local",
        "phone": "0901111111",
        "display_name": "Can Bo Dai Kim",
        "password_hash": generate_password_hash("password123"),
        "is_active": True,
        "role": "user",
        "departments": ["dai_kim"],
    }

    # User 2: Chỉ có quyền Long Biên
    uid_lb = "test_user_long_bien_only"
    users[uid_lb] = {
        "username": "user_longbien",
        "email": "longbien@test.local",
        "phone": "0902222222",
        "display_name": "Can Bo Long Bien",
        "password_hash": generate_password_hash("password123"),
        "is_active": True,
        "role": "user",
        "departments": ["long_bien"],
    }

    # User 3: Chỉ có quyền Tuyển nhân sự
    uid_ns = "test_user_nhansu_only"
    users[uid_ns] = {
        "username": "user_nhansu",
        "email": "nhansu@test.local",
        "phone": "0903333333",
        "display_name": "Chuyen Vien Nhan Su",
        "password_hash": generate_password_hash("password123"),
        "is_active": True,
        "role": "user",
        "departments": ["tuyen_nhan_su"],
    }

    # User 4: Admin có toàn quyền cả 3 bộ phận
    uid_admin = "test_admin_user"
    users[uid_admin] = {
        "username": "super_admin",
        "email": "admin@test.local",
        "phone": "0909999999",
        "display_name": "Tong Quan Tri Vien",
        "password_hash": generate_password_hash("adminpass123"),
        "is_active": True,
        "role": "admin",
        "departments": ["dai_kim", "long_bien", "tuyen_nhan_su"],
    }

    save_users(users)
    print("-> Da khoi tao 4 tai khoan kiem thu thanh cong.")

    client = app.test_client()

    # 3. Kiểm tra trang đăng nhập hiển thị đủ 3 bộ phận
    print("\n[TEST 2] Kiem tra UI trang dang nhap:")
    res = client.get("/login")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Đăng nhập quản trị" in html
    assert "Đại Kim" in html
    assert "Long Biên" in html
    assert "Tuyển nhân sự" in html
    assert "card-daikim" in html
    assert "card-longbien" in html
    assert "card-tuyennhansu" in html
    print("-> PASS: UI trang dang nhap day du 3 the tai khoan: Dai Kim, Long Bien, Tuyen nhan su.")

    # 4. Kiểm tra đăng nhập bằng số điện thoại
    print("\n[TEST 3] Kiem tra dang nhap bang so dien thoai:")
    user_by_phone = find_user_by_login("0901111111")
    assert user_by_phone is not None
    assert user_by_phone["username"] == "user_daikim"
    print("-> PASS: find_user_by_login nhan dien dung so dien thoai 0901111111.")

    # 5. Kiểm tra mật khẩu sai bị từ chối
    print("\n[TEST 4] Kiem tra mat khau sai bi tu choi:")
    res = client.post("/login", data={
        "login": "daikim@test.local",
        "password": "wrong_password",
        "department": "dai_kim"
    })
    assert res.status_code == 200
    assert "không chính xác" in res.get_data(as_text=True)
    print("-> PASS: Mat khau sai bi tu choi.")

    # 6. Kiểm tra tài khoản KHÔNG CÓ QUYỀN vào bộ phận bị từ chối
    print("\n[TEST 5] Kiem tra tai khoan khong co quyen bo phan bi tu choi:")
    # user_daikim cố gắng đăng nhập vào Long Biên
    res = client.post("/login", data={
        "login": "daikim@test.local",
        "password": "password123",
        "department": "long_bien"
    })
    assert res.status_code == 200
    assert "không được phân quyền truy cập bộ phận Long Biên" in res.get_data(as_text=True)
    print("-> PASS: User Dai Kim co dang nhap vao Long Bien bi tu choi chinh xac tai backend.")

    # user_longbien cố gắng đăng nhập vào Tuyển nhân sự
    res = client.post("/login", data={
        "login": "0902222222", # dung so dien thoai
        "password": "password123",
        "department": "tuyen_nhan_su"
    })
    assert res.status_code == 200
    assert "không được phân quyền truy cập bộ phận Tuyển nhân sự" in res.get_data(as_text=True)
    print("-> PASS: User Long Bien co dang nhap vao Tuyen Nhan Su bi tu choi.")

    # 7. Đăng nhập đúng vào bộ phận tương ứng
    print("\n[TEST 6] Kiem tra dang nhap dung bo phan:")
    # Đăng nhập Đại Kim
    res_dk = client.post("/login", data={
        "login": "daikim@test.local",
        "password": "password123",
        "department": "dai_kim"
    }, follow_redirects=True)
    assert res_dk.status_code == 200
    dashboard_html = res_dk.get_data(as_text=True)
    assert "FB POST PRO — ĐẠI KIM" in dashboard_html
    print("-> PASS: User Dai Kim dang nhap thanh cong, Dashboard hien thi 'FB POST PRO — ĐẠI KIM'.")

    # 8. Kiểm tra phân tách dữ liệu tuyệt đối giữa các bộ phận
    print("\n[TEST 7] Kiem tra cach ly du lieu giua cac bo phan:")
    with client.session_transaction() as sess:
        sess["user_id"] = uid_admin
        sess["department"] = "dai_kim"
    
    # Lưu group vào không gian Đại Kim
    cid_dk = f"{uid_admin}__dai_kim"
    save_groups(cid_dk, ["https://www.facebook.com/groups/group_daikim_01"])

    # Chuyển session sang Long Biên
    with client.session_transaction() as sess:
        sess["department"] = "long_bien"
    cid_lb = f"{uid_admin}__long_bien"
    save_groups(cid_lb, ["https://www.facebook.com/groups/group_longbien_01", "https://www.facebook.com/groups/group_longbien_02"])

    # Đọc lại ở không gian Đại Kim
    with client.session_transaction() as sess:
        sess["department"] = "dai_kim"
    groups_dk = load_groups(cid_dk)
    assert len(groups_dk) == 1
    assert "group_daikim_01" in groups_dk[0]

    # Đọc lại ở không gian Long Biên
    groups_lb = load_groups(cid_lb)
    assert len(groups_lb) == 2
    assert "group_daikim_01" not in str(groups_lb)
    print(f"-> PASS: Du lieu cach ly hoan toan! Dai Kim co {len(groups_dk)} group, Long Bien co {len(groups_lb)} group khong he bi lan lon.")

    # 9. Kiểm tra chuyển bộ phận (Switch Department)
    print("\n[TEST 8] Kiem tra chuc nang chuyen bo phan:")
    # User chỉ có quyền Đại Kim cố tình switch sang Long Biên
    with client.session_transaction() as sess:
        sess["user_id"] = uid_dk
        sess["department"] = "dai_kim"
    
    res_switch_fail = client.get("/switch-department?department=long_bien", follow_redirects=True)
    assert "không có quyền truy cập bộ phận Long Biên" in res_switch_fail.get_data(as_text=True)
    print("-> PASS: Backend chan thanh cong user co tinh chuyen sang bo phan khong co quyen.")

    # Admin có quyền cả 3 bộ phận switch sang Tuyển nhân sự
    with client.session_transaction() as sess:
        sess["user_id"] = uid_admin
        sess["department"] = "dai_kim"
    
    res_switch_ok = client.get("/switch-department?department=tuyen_nhan_su", follow_redirects=True)
    assert res_switch_ok.status_code == 200
    assert "FB POST PRO — TUYỂN NHÂN SỰ" in res_switch_ok.get_data(as_text=True)
    print("-> PASS: Admin switch thanh cong sang Tuyển nhân sự, Dashboard cap nhat 'FB POST PRO — TUYỂN NHÂN SỰ'.")

    # 10. Kiểm tra Đăng xuất (Logout)
    print("\n[TEST 9] Kiem tra dang xuat:")
    res_logout = client.get("/logout", follow_redirects=False)
    assert res_logout.status_code == 302
    assert "/login" in res_logout.headers["Location"]
    # Truy cập lại dashboard khi đã logout -> bị chuyển về login
    res_dash = client.get("/dashboard", follow_redirects=False)
    assert res_dash.status_code == 302
    assert "/login" in res_dash.headers["Location"]
    print("-> PASS: Dang xuat thanh cong, session bi huy, bao ve an toan.")

    print("\n" + "=" * 60)
    print("TAT CA 9 TEST CASES DEU PASS 100%!")
    print("FB POST PRO ADMIN & 3 DEPARTMENTS: PASS")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
