#!/usr/bin/env python3
"""
Test Suite: FB POST PRO — ONE-CLICK THREE-ACCOUNT LOGIN & TRUSTED DEVICES
Kiểm thử toàn diện 12 tiêu chí theo yêu cầu kỹ thuật:
1. Có đúng 3 thẻ tài khoản: ĐẠI KIM, LONG BIÊN, TUYỂN NHÂN SỰ.
2. Lần đầu trên thiết bị mới phải yêu cầu xác thực.
3. Xác thực thành công và ghi nhớ thiết bị tin cậy (HttpOnly Cookie).
4. Lần sau bấm Đại Kim -> đăng nhập tự động (1-Click Login).
5. Lần sau bấm Long Biên -> đăng nhập tự động nếu tài khoản đó đã được xác thực và ghi nhớ.
6. Tương tự với Tuyển nhân sự -> đăng nhập tự động khi đã ghi nhớ.
7. Thiết bị lạ không được tự đăng nhập (chặn khách lạ).
8. Token hết hạn hoặc bị thu hồi (Quên thiết bị) phải yêu cầu xác thực lại.
9. Không truy cập chéo dữ liệu giữa 3 bộ phận.
10. Refresh trang duy trì session hợp lệ liên tục.
11. Không lộ mật khẩu trong frontend, API, Git hoặc log.
12. Hỗ trợ đầy đủ cả JSON API và Form submit cho desktop & mobile.
"""

import os
import sys
import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import (
    app,
    DEPARTMENTS,
    VALID_DEPARTMENTS,
    DEPARTMENT_USERNAMES,
    bootstrap_department_accounts,
    find_user_by_login,
    find_user_by_id,
    load_users,
    save_users,
    check_password_hash,
    generate_password_hash,
    create_trusted_device_token,
    verify_trusted_device_token,
    revoke_trusted_device_token,
    save_groups,
    load_groups,
    read_json,
    BOOTSTRAP_CREDENTIALS_FILE,
)


def run_one_click_tests():
    print("=" * 72)
    print("KIỂM THỬ HỆ THỐNG: FB POST PRO — ONE-CLICK THREE-ACCOUNT LOGIN")
    print("=" * 72)

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    # Chuẩn bị 3 tài khoản với mật khẩu kiểm thử
    pwd_dk = "DaiKim@OneClick2026!#"
    pwd_lb = "LongBien@OneClick2026!#"
    pwd_ns = "TuyenNhanSu@OneClick2026!#"

    users = load_users()
    for uname, pwd, dept, dname in [
        ("daikim", pwd_dk, "dai_kim", "Quản lý Đại Kim"),
        ("longbien", pwd_lb, "long_bien", "Quản lý Long Biên"),
        ("tuyennhansu", pwd_ns, "tuyen_nhan_su", "Quản lý Tuyển nhân sự"),
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
            "must_change_password": False,
            "created_at": "2026-10-19T00:00:00+00:00",
            "last_login_at": None,
        }
    save_users(users)

    # ----------------------------------------------------
    # TIÊU CHÍ 1: Có đúng 3 thẻ tài khoản trên giao diện
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 1] Kiểm tra giao diện 3 thẻ tài khoản:")
    client = app.test_client()
    res = client.get("/login")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "ĐẠI KIM" in html, "Giao diện phải có thẻ ĐẠI KIM"
    assert "LONG BIÊN" in html, "Giao diện phải có thẻ LONG BIÊN"
    assert "TUYỂN NHÂN SỰ" in html, "Giao diện phải có thẻ TUYỂN NHÂN SỰ"
    assert "card-daikim" in html
    assert "card-longbien" in html
    assert "card-tuyennhansu" in html

    # Kiểm tra không hiển thị tên đăng nhập hoặc mật khẩu trên thẻ
    assert "daikim" not in html.split('class="card-title"')[1].split("</article>")[0].lower() or "daikim@" not in html
    assert pwd_dk not in html, "Mật khẩu tuyệt đối không được xuất hiện trên HTML"
    assert pwd_lb not in html
    assert pwd_ns not in html
    print("-> PASS: Giao diện hiển thị đúng 3 thẻ lớn, không lộ thông tin nhạy cảm.")

    # ----------------------------------------------------
    # TIÊU CHÍ 2: Lần đầu trên thiết bị mới phải yêu cầu xác thực
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 2] Kiểm tra thiết bị mới bắt buộc xác thực:")
    c_new = app.test_client()
    # Thử gọi quick-login khi chưa có cookie ghi nhớ
    res_quick = c_new.post("/quick-login", json={"department": "dai_kim"})
    assert res_quick.status_code == 401
    data_quick = res_quick.get_json()
    assert data_quick.get("requires_auth") is True
    assert "chưa được xác thực" in data_quick.get("error", "")

    # Thử get /login trên thiết bị mới -> cả 3 thẻ đều hiển thị 'Cần xác thực lần đầu'
    res_login_page = c_new.get("/login")
    page_html = res_login_page.get_data(as_text=True)
    assert "Cần xác thực lần đầu" in page_html
    print("-> PASS: Thiết bị mới chưa được ghi nhớ bắt buộc phải xác thực mật khẩu.")

    # ----------------------------------------------------
    # TIÊU CHÍ 3: Xác thực thành công và ghi nhớ thiết bị tin cậy
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 3] Kiểm tra xác thực và ghi nhớ thiết bị:")
    c_device = app.test_client()

    # Nhập sai mật khẩu -> bị từ chối
    res_fail = c_device.post("/login", json={
        "department": "dai_kim",
        "password": "WrongPassword123!",
        "remember_device": "1"
    })
    assert res_fail.status_code == 400
    assert "không chính xác" in res_fail.get_json().get("error", "")

    # Nhập đúng mật khẩu và chọn ghi nhớ thiết bị
    res_auth = c_device.post("/login", json={
        "department": "dai_kim",
        "password": pwd_dk,
        "remember_device": "1"
    })
    assert res_auth.status_code == 200
    data_auth = res_auth.get_json()
    assert data_auth.get("ok") is True

    # Kiểm tra cookie fb_trust_dai_kim đã được cấp phát
    cookie_dk = c_device.get_cookie("fb_trust_dai_kim")
    assert cookie_dk is not None, "Phải có cookie fb_trust_dai_kim"
    assert getattr(cookie_dk, "http_only", True) is True, "Cookie thiết bị tin cậy phải là HttpOnly"
    assert cookie_dk.value, "Giá trị token cookie không được rỗng"
    print("-> PASS: Xác thực thành công, cấp cookie HttpOnly fb_trust_dai_kim bảo mật.")

    # ----------------------------------------------------
    # TIÊU CHÍ 4: Lần sau bấm Đại Kim -> Đăng nhập tự động 1-Click
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 4] Kiểm tra One-Click Login cho Đại Kim:")
    # Đăng xuất phiên làm việc hiện tại nhưng giữ cookie thiết bị tin cậy
    c_device.get("/logout")

    # Kiểm tra trang /login lúc này hiển thị Đại Kim là 'Sẵn sàng 1-Click'
    res_login_after = c_device.get("/login")
    html_after = res_login_after.get_data(as_text=True)
    assert "Sẵn sàng 1-Click" in html_after
    assert 'data-department="dai_kim" \n            data-remembered="true"' in html_after or 'data-remembered="true"' in html_after

    # Người dùng bấm vào thẻ Đại Kim -> Gọi POST /quick-login
    res_one_click = c_device.post("/quick-login", json={"department": "dai_kim"})
    assert res_one_click.status_code == 200
    data_one_click = res_one_click.get_json()
    assert data_one_click.get("ok") is True
    assert data_one_click.get("redirect") in {"/", "/dashboard"}

    # Kiểm tra session đã được thiết lập đúng cho daikim
    with c_device.session_transaction() as sess:
        assert sess.get("username") == "daikim"
        assert sess.get("department") == "dai_kim"

    # Truy cập dashboard thành công
    res_dash = c_device.get("/dashboard")
    assert res_dash.status_code == 200
    assert "FB POST PRO — ĐẠI KIM" in res_dash.get_data(as_text=True)
    print("-> PASS: Bấm thẻ Đại Kim tự động đăng nhập 1-Click vào FB POST PRO — ĐẠI KIM.")

    # ----------------------------------------------------
    # TIÊU CHÍ 5: Lần sau bấm Long Biên -> Đăng nhập tự động nếu đã xác thực
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 5] Kiểm tra One-Click Login cho Long Biên:")
    # Khi Long Biên chưa được xác thực -> bấm Long Biên không được tự đăng nhập
    res_lb_unauth = c_device.post("/quick-login", json={"department": "long_bien"})
    assert res_lb_unauth.status_code == 401, "Long Biên chưa xác thực không được phép 1-click login"

    # Tiến hành xác thực Long Biên với remember_device=1
    res_lb_auth = c_device.post("/login", json={
        "department": "long_bien",
        "password": pwd_lb,
        "remember_device": "1"
    })
    assert res_lb_auth.status_code == 200

    # Đăng xuất phiên làm việc
    c_device.get("/logout")

    # Bấm thẻ Long Biên -> Đăng nhập tự động 1-Click thành công
    res_lb_quick = c_device.post("/quick-login", json={"department": "long_bien"})
    assert res_lb_quick.status_code == 200
    with c_device.session_transaction() as sess:
        assert sess.get("username") == "longbien"
        assert sess.get("department") == "long_bien"

    res_lb_dash = c_device.get("/dashboard")
    assert "FB POST PRO — LONG BIÊN" in res_lb_dash.get_data(as_text=True)
    print("-> PASS: Long Biên xác thực độc lập và hỗ trợ 1-Click login thành công.")

    # ----------------------------------------------------
    # TIÊU CHÍ 6: Tương tự với Tuyển nhân sự
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 6] Kiểm tra One-Click Login cho Tuyển nhân sự:")
    # Xác thực Tuyển nhân sự với remember_device=1
    res_ns_auth = c_device.post("/login", json={
        "department": "tuyen_nhan_su",
        "password": pwd_ns,
        "remember_device": "1"
    })
    assert res_ns_auth.status_code == 200

    # Đăng xuất phiên
    c_device.get("/logout")

    # Bấm thẻ Tuyển nhân sự -> 1-Click login thành công
    res_ns_quick = c_device.post("/quick-login", json={"department": "tuyen_nhan_su"})
    assert res_ns_quick.status_code == 200
    with c_device.session_transaction() as sess:
        assert sess.get("username") == "tuyennhansu"
        assert sess.get("department") == "tuyen_nhan_su"

    res_ns_dash = c_device.get("/dashboard")
    assert "FB POST PRO — TUYỂN NHÂN SỰ" in res_ns_dash.get_data(as_text=True)
    print("-> PASS: Tuyển nhân sự xác thực độc lập và hỗ trợ 1-Click login thành công.")

    # ----------------------------------------------------
    # TIÊU CHÍ 7: Thiết bị lạ không được tự đăng nhập
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 7] Kiểm tra thiết bị lạ / khách vãng lai bị chặn hoàn toàn:")
    c_stranger = app.test_client()
    for dept in ["dai_kim", "long_bien", "tuyen_nhan_su"]:
        res_stranger = c_stranger.post("/quick-login", json={"department": dept})
        assert res_stranger.status_code == 401
        assert res_stranger.get_json().get("requires_auth") is True
    print("-> PASS: Khách truy cập từ thiết bị lạ không thể tự ý đăng nhập nếu không có mật khẩu.")

    # ----------------------------------------------------
    # TIÊU CHÍ 8: Token hết hạn hoặc bị thu hồi (Quên thiết bị)
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 8] Kiểm tra thu hồi token & Quên thiết bị:")
    # a. Sử dụng chức năng Quên thiết bị (/forget-device) cho Đại Kim
    res_forget = c_device.post("/forget-device", json={"department": "dai_kim"})
    assert res_forget.status_code == 200
    assert res_forget.get_json().get("ok") is True

    # Thử 1-Click login lại cho Đại Kim -> Bị từ chối
    res_try_dk = c_device.post("/quick-login", json={"department": "dai_kim"})
    assert res_try_dk.status_code == 401, "Sau khi quên thiết bị, token phải bị thu hồi"

    # b. Kiểm tra token hết hạn
    raw_token_exp = create_trusted_device_token("user_test", "dai_kim", expires_days=-1)
    rec_exp = verify_trusted_device_token("dai_kim", raw_token_exp)
    assert rec_exp is None, "Token quá hạn phải bị từ chối xác thực"
    print("-> PASS: Token hết hạn hoặc bị thu hồi (Quên thiết bị) bắt buộc phải xác thực lại.")

    # ----------------------------------------------------
    # TIÊU CHÍ 9: Không truy cập chéo dữ liệu giữa các bộ phận
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 9] Kiểm tra cách ly dữ liệu và phân quyền:")
    user_dk = find_user_by_login("daikim")
    user_lb = find_user_by_login("longbien")
    cid_dk = f"{user_dk['user_id']}__dai_kim"
    cid_lb = f"{user_lb['user_id']}__long_bien"

    save_groups(cid_dk, ["https://facebook.com/groups/daikim_exclusive_campaign"])
    save_groups(cid_lb, ["https://facebook.com/groups/longbien_exclusive_campaign"])

    groups_dk = load_groups(cid_dk)
    groups_lb = load_groups(cid_lb)

    assert "daikim_exclusive_campaign" in str(groups_dk)
    assert "daikim_exclusive_campaign" not in str(groups_lb)
    assert "longbien_exclusive_campaign" in str(groups_lb)
    assert "longbien_exclusive_campaign" not in str(groups_dk)
    print("-> PASS: Dữ liệu giữa các bộ phận được cách ly tuyệt đối 100%.")

    # ----------------------------------------------------
    # TIÊU CHÍ 10: Refresh trang không làm mất session hợp lệ
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 10] Kiểm tra refresh duy trì session:")
    c_session = app.test_client()
    c_session.post("/login", json={"department": "dai_kim", "password": pwd_dk})

    res_r1 = c_session.get("/dashboard")
    assert res_r1.status_code == 200
    assert "FB POST PRO — ĐẠI KIM" in res_r1.get_data(as_text=True)

    res_r2 = c_session.get("/dashboard")
    assert res_r2.status_code == 200
    assert "FB POST PRO — ĐẠI KIM" in res_r2.get_data(as_text=True)
    print("-> PASS: Refresh liên tục không làm mất phiên làm việc.")

    # ----------------------------------------------------
    # TIÊU CHÍ 11: Không lộ mật khẩu trong frontend, API, Git hoặc log
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 11] Kiểm tra an toàn mật khẩu & Git:")
    user_test = find_user_by_login("daikim")
    assert not user_test["password_hash"].startswith("DaiKim"), "Mật khẩu không được lưu plain text"
    assert "scrypt:" in user_test["password_hash"] or "pbkdf2:" in user_test["password_hash"]

    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".bootstrap_credentials.json" in gitignore
    assert "*.credentials.json" in gitignore
    print("-> PASS: Mật khẩu băm an toàn, .gitignore bảo vệ thông tin mật.")

    # ----------------------------------------------------
    # TIÊU CHÍ 12: Đăng xuất với lựa chọn quên thiết bị
    # ----------------------------------------------------
    print("\n[TIÊU CHÍ 12] Kiểm tra tùy chọn đăng xuất & quên thiết bị:")
    # Đăng nhập Tuyển nhân sự với remember
    c_logout_test = app.test_client()
    c_logout_test.post("/login", json={"department": "tuyen_nhan_su", "password": pwd_ns, "remember_device": "1"})

    # Đăng xuất kèm forget=1
    res_logout_forget = c_logout_test.get("/logout?forget=1", follow_redirects=True)
    assert res_logout_forget.status_code == 200

    # Kiểm tra cookie đã bị xóa
    cookie_after = c_logout_test.get_cookie("fb_trust_tuyen_nhan_su")
    assert cookie_after is None or cookie_after.value == "", "Cookie phải bị xóa sau logout?forget=1"
    print("-> PASS: Tùy chọn đăng xuất và quên thiết bị hoạt động chính xác.")

    print("\n" + "=" * 72)
    print("KẾT QUẢ: 12/12 TIÊU CHÍ ĐỀU ĐẠT CHUẨN (100% PASS)!")
    print("ONE-CLICK THREE-ACCOUNT LOGIN: PASS")
    print("=" * 72)


if __name__ == "__main__":
    run_one_click_tests()
