# -*- coding: utf-8 -*-
"""
KIỂM THỬ TOÀN DIỆN MỌI CHỨC NĂNG CỦA HỆ THỐNG FB POST PRO
(FULL SYSTEM FUNCTIONALITY & REGRESSION AUDIT)
"""

import os
import sys
import io
import time
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import app as fb_app
from app import (
    app,
    parse_scheduled_time,
    format_vietnam_time,
    load_schedule,
    save_schedule,
    get_schedule_info,
    check_and_trigger_schedules,
    load_jobs,
    save_jobs,
    load_groups,
    save_groups,
    load_post,
    save_post_content,
    load_settings,
    save_settings,
    load_devices,
    save_devices,
    get_active_device,
    update_campaign_state,
    get_campaign_state,
    get_facebook_state,
    save_facebook_state,
    now_iso,
    bootstrap_department_accounts,
    find_user_by_login,
)

def run_comprehensive_tests():
    print("=" * 76)
    print("   KIỂM THỬ TOÀN DIỆN MỌI CHỨC NĂNG HỆ THỐNG FB POST PRO")
    print("=" * 76)

    passed_count = 0
    total_count = 0
    failed_tests = []

    def check(name, cond, details=""):
        nonlocal passed_count, total_count
        total_count += 1
        icon = "✓" if cond else "✗"
        print(f"  [{icon}] {name}" + (f": {details}" if details else ""))
        if not cond:
            failed_tests.append(f"{name} ({details})")
            raise AssertionError(f"Failing assertion: {name} - {details}")
        passed_count += 1

    client = app.test_client()

    # Ensure accounts bootstrapped
    bootstrap_department_accounts()

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. KIỂM THỬ PUBLIC ENDPOINTS & GIAO DIỆN CHÍNH
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 1] Kiểm tra Endpoint Công khai & Khởi động...")
    r = client.get("/health")
    check("GET /health trả về 200 OK", r.status_code == 200)
    health_data = r.get_json()
    check("Health status là 'ok'", health_data.get("status") == "ok")

    r = client.get("/login")
    check("GET /login trả về 200 OK", r.status_code == 200)
    html_login = r.data.decode("utf-8")
    check("Giao diện /login có thẻ ĐẠI KIM", "ĐẠI KIM" in html_login)
    check("Giao diện /login có thẻ LONG BIÊN", "LONG BIÊN" in html_login)
    check("Giao diện /login có thẻ TUYỂN NHÂN SỰ", "TUYỂN NHÂN SỰ" in html_login)

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. KIỂM THỬ XÁC THỰC, PHÂN QUYỀN 3 BỘ PHẬN & CÁCH LY DỮ LIỆU
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 2] Kiểm tra Đăng nhập, 3 Bộ phận & Cách ly dữ liệu...")

    dk_user = find_user_by_login("daikim")
    lb_user = find_user_by_login("longbien")
    users = fb_app.load_users()
    users[dk_user["user_id"]]["password_hash"] = fb_app.generate_password_hash("Daikim@2026_Secure")
    users[dk_user["user_id"]]["must_change_password"] = False
    users[lb_user["user_id"]]["password_hash"] = fb_app.generate_password_hash("Longbien@2026_Secure")
    users[lb_user["user_id"]]["must_change_password"] = False
    fb_app.save_users(users)
    
    # 2.1 Từ chối mật khẩu sai
    r_bad = client.post("/login", data={
        "login": "daikim",
        "password": "WrongPassword123",
        "department": "dai_kim"
    }, follow_redirects=True)
    check("Từ chối mật khẩu không chính xác", "Sai tài khoản hoặc mật khẩu" in r_bad.data.decode("utf-8") or "không chính xác" in r_bad.data.decode("utf-8"))

    # 2.2 Đăng nhập Đại Kim thành công
    client_daikim = app.test_client()
    r_dk = client_daikim.post("/login", data={
        "login": "daikim",
        "password": "Daikim@2026_Secure",
        "department": "dai_kim"
    }, follow_redirects=True)
    check("Đăng nhập tài khoản @daikim thành công", r_dk.status_code == 200)
    with client_daikim.session_transaction() as sess:
        check("Session lưu trữ user_id hợp lệ của Đại Kim", sess.get("user_id") == dk_user["user_id"])
        check("Session nhận diện đúng bộ phận dai_kim", sess.get("department") == "dai_kim")
    check("Dashboard nhận diện bộ phận ĐẠI KIM", "ĐẠI KIM" in r_dk.data.decode("utf-8"))

    # 2.3 Đăng nhập Long Biên thành công
    client_lb = app.test_client()
    r_lb = client_lb.post("/login", data={
        "login": "longbien",
        "password": "Longbien@2026_Secure",
        "department": "long_bien"
    }, follow_redirects=True)
    check("Đăng nhập tài khoản @longbien thành công", r_lb.status_code == 200)
    with client_lb.session_transaction() as sess:
        check("Session lưu trữ user_id hợp lệ của Long Biên", sess.get("user_id") == lb_user["user_id"])
        check("Session nhận diện đúng bộ phận long_bien", sess.get("department") == "long_bien")
    check("Dashboard nhận diện bộ phận LONG BIÊN", "LONG BIÊN" in r_lb.data.decode("utf-8"))

    # 2.4 Kiểm tra cách ly dữ liệu: Thêm group ở Đại Kim không xuất hiện ở Long Biên
    dk_cid = f"{dk_user['user_id']}__dai_kim"
    lb_cid = f"{lb_user['user_id']}__long_bien"

    save_groups(dk_cid, ["https://facebook.com/groups/only_daikim_secret"])
    save_groups(lb_cid, ["https://facebook.com/groups/only_longbien_secret"])

    dk_groups = load_groups(dk_cid)
    lb_groups = load_groups(lb_cid)
    check("Cách ly dữ liệu Groups giữa các bộ phận 100%", 
          any("only_daikim_secret" in g for g in dk_groups) and not any("only_daikim_secret" in g for g in lb_groups))

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. KIỂM THỬ TOÀN BỘ CÁC TRANG QUẢN TRỊ (VIEW RENDERING)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 3] Kiểm tra tải tất cả các trang quản trị...")
    users = fb_app.load_users()
    users[dk_user["user_id"]]["must_change_password"] = False
    fb_app.save_users(users)

    views = [
        ("/dashboard", "Tổng quan"),
        ("/compose", "Tạo chiến dịch"),
        ("/groups", "Quản lý"),
        ("/history", "Lịch sử"),
        ("/settings", "Cài đặt"),
        ("/change-password", "Đổi mật khẩu"),
    ]
    for path, expected_text in views:
        res = client_daikim.get(path)
        check(f"Trang {path} phản hồi 200 OK", res.status_code == 200)
        check(f"Trang {path} render đầy đủ nội dung", expected_text in res.data.decode("utf-8"))

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. KIỂM THỬ QUẢN LÝ GROUPS (THÊM, XÓA, EXCEL, CSV, BULK SAVE)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 4] Kiểm tra toàn diện Quản lý Groups...")
    # 4.1 Thêm 1 group lẻ
    r_add = client_daikim.post("/add-group", data={
        "group_url": "https://facebook.com/groups/test_group_alpha",
        "group_name": "Test Group Alpha"
    }, follow_redirects=True)
    check("POST /add-group thành công", r_add.status_code == 200)
    check("Group mới đã được lưu trong database", any("test_group_alpha" in g for g in load_groups(dk_cid)))

    # 4.2 Lưu hàng loạt (Bulk Save)
    bulk_data = [
        {"url": f"https://facebook.com/groups/bulk_{i}", "name": f"Nhóm {i}"}
        for i in range(1, 21)
    ]
    r_bulk = client_daikim.post("/groups/bulk-save", json={"groups": bulk_data})
    check("POST /groups/bulk-save phản hồi 200 OK", r_bulk.status_code == 200)
    bulk_res = r_bulk.get_json()
    check("Bulk save ghi nhận kết quả thành công", bulk_res.get("ok") is True)
    check("Số lượng Groups trong database được cập nhật", len(load_groups(dk_cid)) >= 20)

    # 4.3 Xóa 1 group
    r_del = client_daikim.post("/delete-group/0", follow_redirects=True)
    check("POST /delete-group/<index> xóa thành công", r_del.status_code == 200)

    # 4.4 Xuất file XLSX và CSV
    r_xlsx = client_daikim.get("/groups/export")
    check("Xuất danh sách Groups định dạng Excel (XLSX)", r_xlsx.status_code == 200 and len(r_xlsx.data) > 100)

    r_csv = client_daikim.get("/groups/export?format=csv")
    check("Xuất danh sách Groups định dạng CSV", r_csv.status_code == 200 and b"http" in r_csv.data)

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. KIỂM THỬ SOẠN BÀI & QUẢN LÝ ẢNH
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 5] Kiểm tra Soạn bài viết & Quản lý Ảnh...")
    # 5.1 Lưu nội dung bài viết và cấu hình delay
    r_save = client_daikim.post("/save-post", data={
        "campaign_name": "Chiến Dịch Kiểm Thử Toàn Diện",
        "content": "Nội dung bài viết kiểm thử hệ thống FB POST PRO.",
        "min_delay": 2,
        "max_delay": 5,
    }, follow_redirects=True)
    check("POST /save-post lưu thông tin chiến dịch thành công", r_save.status_code == 200)
    check("Nội dung bài viết được lưu trữ chính xác", "Nội dung bài viết kiểm thử" in load_post(dk_cid))
    settings_loaded = load_settings(dk_cid)
    check("Delay min_delay và max_delay được lưu đúng", settings_loaded.get("min_delay") == 2 and settings_loaded.get("max_delay") == 5)

    # 5.2 Upload ảnh giả lập
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    file_tuple = (io.BytesIO(fake_png), "test_image.png")
    r_img = client_daikim.post("/save-post", data={
        "campaign_name": "Chiến Dịch Kèm Ảnh",
        "content": "Nội dung kèm ảnh kiểm thử.",
        "min_delay": 1,
        "max_delay": 3,
        "images": file_tuple
    }, content_type="multipart/form-data", follow_redirects=True)
    check("Upload ảnh bài đăng thành công", r_img.status_code == 200)
    settings_with_img = load_settings(dk_cid)
    uploaded_images = settings_with_img.get("post_images", [])
    check("Ảnh được lưu trong post_images của settings", len(uploaded_images) >= 1)

    # 5.3 Tải xem lại ảnh
    img_name = uploaded_images[0]
    r_view_img = client_daikim.get(f"/customer-image/{img_name}")
    check("Xem lại ảnh tải lên /customer-image/<filename> trả về 200 OK", r_view_img.status_code == 200)

    # 5.4 Xóa toàn bộ ảnh
    r_del_all = client_daikim.post("/delete-all-post-images", follow_redirects=True)
    check("POST /delete-all-post-images xóa toàn bộ ảnh thành công", r_del_all.status_code == 200)
    check("Danh sách post_images sau khi xóa là rỗng", len(load_settings(dk_cid).get("post_images", [])) == 0)

    # ═══════════════════════════════════════════════════════════════════════════
    # 6. KIỂM THỬ KẾT NỐI CONNECTOR, CHẠY & DỪNG CHIẾN DỊCH
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 6] Kiểm tra Vòng đời Connector Chrome & Chạy/Dừng Chiến dịch...")
    # 6.1 Tạo mã ghép nối Extension
    r_code = client_daikim.post("/api/extension/pair-code")
    check("Tạo mã liên kết Extension (/api/extension/pair-code)", r_code.status_code == 200)
    code_val = r_code.get_json().get("code")
    check("Mã liên kết 8 ký tự hợp lệ", code_val and len(code_val) == 8)

    # 6.2 Extension kết nối bằng mã
    r_pair = client.post("/api/extension/pair", json={
        "code": code_val,
        "device_name": "Chrome Comprehensive Test",
        "extension_version": "1.2.0"
    })
    check("Extension ghép nối thành công", r_pair.status_code == 200)
    pair_res = r_pair.get_json()
    conn_dev_id = pair_res.get("device_id")
    conn_token = pair_res.get("token")
    ext_headers = {"X-Device-ID": conn_dev_id, "X-Agent-Token": conn_token}

    # 6.3 Heartbeat & Trạng thái Facebook
    r_hb = client.post("/api/agent/heartbeat", headers=ext_headers, json={
        "device_name": "Chrome Comprehensive Test",
        "extension_version": "1.2.0",
        "facebook_logged_in": True
    })
    check("Connector gửi Heartbeat thành công", r_hb.status_code == 200)
    save_facebook_state(dk_cid, status="connected")

    # 6.4 Bấm Chạy Chiến dịch
    update_campaign_state(dk_cid, running=False, status="idle")
    r_run = client_daikim.post("/run-campaign", data={
        "campaign_name": "Chiến Dịch Chạy Thử",
        "min_delay": 1,
        "max_delay": 2,
    }, follow_redirects=True)
    check("Bấm Chạy chiến dịch (/run-campaign) thành công", r_run.status_code == 200)

    # 6.5 Connector nhận Job
    r_job = client.get("/api/agent/job", headers=ext_headers)
    check("Connector thăm dò nhận được Job (/api/agent/job)", r_job.status_code == 200 and r_job.get_json().get("has_job") is True)
    curr_job = r_job.get_json().get("job", {})
    check("Job có đúng tên chiến dịch", curr_job.get("campaign_name") == "Chiến Dịch Chạy Thử")

    # 6.6 Connector báo cáo tiến độ (1 thành công)
    r_stat_rep = client.post("/api/agent/status", headers=ext_headers, json={
        "status": "posting",
        "event": "group_success",
        "post_status": "SUCCESS",
        "message": "Đăng thành công nhóm 1",
        "group_url": "https://facebook.com/groups/bulk_1",
        "processed": 1,
        "success": 1,
        "errors": 0
    })
    check("Connector báo cáo tiến độ thành công (/api/agent/status)", r_stat_rep.status_code == 200)

    # 6.7 Web kiểm tra trạng thái /campaign-status
    r_camp_stat = client_daikim.get("/campaign-status")
    c_stat_json = r_camp_stat.get_json()
    check("Trạng thái /campaign-status cập nhật processed=1", c_stat_json.get("processed") == 1)
    check("Trạng thái /campaign-status cập nhật success=1", c_stat_json.get("success") == 1)

    # 6.8 Dừng chiến dịch
    r_stop = client_daikim.post("/stop-campaign", follow_redirects=True)
    check("POST /stop-campaign dừng chiến dịch thành công", r_stop.status_code == 200)
    r_ctl = client.get("/api/agent/control", headers=ext_headers)
    check("Lệnh dừng truyền tới Connector (/api/agent/control stop_requested=True)", r_ctl.get_json().get("stop_requested") is True)

    # Cleanup job
    save_jobs(dk_cid, {})
    update_campaign_state(dk_cid, running=False, status="idle")

    # ═══════════════════════════════════════════════════════════════════════════
    # 7. KIỂM THỬ HẸN GIỜ ĐĂNG BÀI (SCHEDULED CAMPAIGN LIFECYCLE)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 7] Kiểm tra Toàn diện Hẹn giờ Đăng bài (Scheduled Posting)...")
    save_schedule(dk_cid, None)

    # 7.1 Từ chối giờ quá khứ
    r_past = client_daikim.post("/schedule-campaign", data={
        "campaign_name": "Lịch Quá Khứ",
        "scheduled_at": "2020-05-10T10:00",
    }, headers={"Accept": "application/json"})
    check("Từ chối đặt lịch thời gian trong quá khứ", load_schedule(dk_cid) is None)

    # 7.2 Đặt lịch tương lai hợp lệ
    future_time_str = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r_sched_ok = client_daikim.post("/schedule-campaign", data={
        "campaign_name": "Lịch Hẹn Tương Lai Test",
        "scheduled_at": future_time_str,
        "min_delay": 2,
        "max_delay": 5,
    }, headers={"Accept": "application/json"})
    check("Đặt lịch hẹn tương lai thành công", r_sched_ok.status_code == 200 and r_sched_ok.get_json().get("ok") is True)

    # 7.3 Tra cứu trạng thái lịch hẹn /schedule-status
    r_s_stat = client_daikim.get("/schedule-status")
    s_stat_json = r_s_stat.get_json()
    check("API /schedule-status trả về status 'scheduled'", s_stat_json.get("status") == "scheduled")
    check("API /schedule-status có thông tin đếm ngược time_remaining_str", bool(s_stat_json.get("time_remaining_str")))

    # 7.4 Hủy lịch hẹn
    r_cancel = client_daikim.post("/cancel-schedule", headers={"Accept": "application/json"})
    check("Hủy lịch hẹn (/cancel-schedule) thành công", r_cancel.status_code == 200 and r_cancel.get_json().get("ok") is True)
    check("Lịch hẹn đã được dọn khỏi hệ thống sau khi hủy", load_schedule(dk_cid) is None)

    # 7.5 Kích hoạt tự động khi đến giờ hẹn
    due_sched = {
        "schedule_id": "sched_auto_trigger_test",
        "campaign_name": "Lịch Hẹn Kích Hoạt Tự Động",
        "scheduled_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
        "status": "scheduled",
        "created_at": now_iso(),
        "groups": ["https://facebook.com/groups/auto_due_1"],
        "content": "Nội dung bung tự động",
        "min_delay": 1,
        "max_delay": 2,
    }
    save_schedule(dk_cid, due_sched)
    triggered_count = check_and_trigger_schedules(dk_cid)
    check("Scheduler tự động kích hoạt chiến dịch đã đến giờ", triggered_count >= 1)

    # Connector thăm dò và nhận được job tự động bung
    r_due_job = client.get("/api/agent/job", headers=ext_headers)
    check("Connector nhận được Job do Scheduler tự bung", r_due_job.get_json().get("has_job") is True)
    check("Job bung đúng tên chiến dịch hẹn giờ", r_due_job.get_json().get("job", {}).get("campaign_name") == "Lịch Hẹn Kích Hoạt Tự Động")

    # Dọn dẹp
    save_jobs(dk_cid, {})
    update_campaign_state(dk_cid, running=False, status="idle")

    # ═══════════════════════════════════════════════════════════════════════════
    # 8. KIỂM THỬ ĐỘ NHẤT QUÁN CỦA BỘ NHỚ ĐỆM IN-MEMORY CACHE
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n[CHỨC NĂNG 8] Kiểm tra Tính nhất quán của Bộ nhớ đệm (Cache Consistency)...")
    # Verify save_settings immediately updates get_campaign_state / settings
    save_settings(dk_cid, {"campaign_name": "Cache Test Campaign", "min_delay": 7})
    check("load_settings đọc đúng giá trị vừa ghi vào cache", load_settings(dk_cid).get("min_delay") == 7)

    # Verify update_campaign_state updates cache
    update_campaign_state(dk_cid, message="Cache Message Live")
    check("get_campaign_state đọc đúng message từ cache", get_campaign_state(dk_cid).get("message") == "Cache Message Live")

    # Verify save_post_content updates cache
    save_post_content(dk_cid, "Cache Post Content 12345")
    check("load_post đọc đúng nội dung từ cache", load_post(dk_cid) == "Cache Post Content 12345")

    # ═══════════════════════════════════════════════════════════════════════════
    # 9. TỔNG KẾT
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 76)
    print(f"KẾT QUẢ KIỂM THỬ TỔNG THỂ: {passed_count}/{total_count} TIÊU CHÍ PASS 100%!")
    print("KHÔNG PHÁT HIỆN BẤT KỲ LỖI LOGIC, LỖI PHÂN QUYỀN HOẶC LỖI TÍNH NĂNG NÀO!")
    print("=" * 76)

if __name__ == "__main__":
    run_comprehensive_tests()
