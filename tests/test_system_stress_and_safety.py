# -*- coding: utf-8 -*-
"""
STRESS & SAFETY TEST SUITE FOR FB POST PRO
Kiểm tra sức chịu tải, độ bền, chống lỗi ngoại lệ (zero-exception policy),
và xử lý các trường hợp biên (edge cases).
"""

import sys
import io
import time
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import app as fb_app
from app import app

def run_stress_and_safety_tests():
    print("=" * 76)
    print("   KIỂM THỬ SỨC BỀN & BẢO VỆ CHỐNG LỖI TOÀN HỆ THỐNG FB POST PRO")
    print("=" * 76)

    client = app.test_client()
    passed = 0
    total = 0

    def assert_test(name, condition, details=""):
        nonlocal passed, total
        total += 1
        icon = "✓" if condition else "✗"
        print(f"  [{icon}] {name}" + (f": {details}" if details else ""))
        if not condition:
            raise AssertionError(f"Stress test failure: {name} ({details})")
        passed += 1

    # Khởi tạo tài khoản
    fb_app.bootstrap_department_accounts()
    dk_user = fb_app.find_user_by_login("daikim")
    users = fb_app.load_users()
    users[dk_user["user_id"]]["password_hash"] = fb_app.generate_password_hash("StressTestPass123!")
    users[dk_user["user_id"]]["must_change_password"] = False
    fb_app.save_users(users)

    # Đăng nhập
    client_auth = app.test_client()
    r_login = client_auth.post("/login", data={
        "login": "daikim",
        "password": "StressTestPass123!",
        "department": "dai_kim"
    }, follow_redirects=True)
    assert_test("Đăng nhập tài khoản Đại Kim thành công", r_login.status_code == 200)

    # 1. KIỂM THỬ UNICODE & EMOJI (TIẾNG VIỆT CÓ DẤU, KÝ TỰ ĐẶC BIỆT)
    print("\n[PHẦN 1] Kiểm tra Unicode, Tiếng Việt có dấu, Emoji & HTML Injection...")
    special_content = "🔥 [KHUYẾN MÃI ĐẶC BIỆT] 🎁 Giảm 50% hôm nay! <html><body>&quot;hello' & <script>alert(1)</script>"
    special_name = "🚀 Chiến Dịch Tết 2026 🎉 (Nhóm 1)"

    r_save = client_auth.post("/save-post", data={
        "campaign_name": special_name,
        "content": special_content,
        "min_delay": 2,
        "max_delay": 5,
    }, follow_redirects=True)
    assert_test("Lưu bài viết chứa Unicode, Emoji, HTML tags thành công", r_save.status_code == 200)

    dk_cid = f"{dk_user['user_id']}__dai_kim"
    loaded_post = fb_app.load_post(dk_cid)
    assert_test("Nội dung bài viết lưu giữ nguyên vẹn ký tự Unicode/Emoji", "🔥 [KHUYẾN MÃI ĐẶC BIỆT]" in loaded_post)
    assert_test("Nội dung không bị cắt xén", "alert(1)" in loaded_post)

    # 2. KIỂM THỬ TRƯỜNG HỢP BIÊN DELAY (NEGATIVE, REVERSED, INVALID)
    print("\n[PHẦN 2] Kiểm tra giá trị biên Delay (Negative, Min > Max, Chuỗi rác)...")
    r_edge_delay = client_auth.post("/save-post", data={
        "campaign_name": "Edge Delay Test",
        "content": "Test delay",
        "min_delay": -10,
        "max_delay": "chữ_không_phải_số",
    }, follow_redirects=True)
    assert_test("Backend xử lý delay âm và chuỗi rác an toàn (không crash)", r_edge_delay.status_code == 200)
    settings = fb_app.load_settings(dk_cid)
    assert_test("Delay âm được chuyển về >= 0", settings.get("min_delay", 0) >= 0)

    # 3. KIỂM THỬ ĐỘ AN TOÀN TRUY CẬP ĐƯỜNG DẪN ẢNH (PATH TRAVERSAL DEFENSE)
    print("\n[PHẦN 3] Kiểm tra chống Path Traversal (Tấn công đọc file hệ thống)...")
    malicious_paths = [
        "../../../../Windows/System32/drivers/etc/hosts",
        "..\\..\\app.py",
        "/etc/passwd",
        "%2e%2e%2f%2e%2e%2fapp.py"
    ]
    for p in malicious_paths:
        r_trav = client_auth.get(f"/customer-image/{p}")
        assert_test(f"Chặn truy cập trái phép file hệ thống ({p[:25]}...)", r_trav.status_code in [400, 404, 403])

    # 4. KIỂM THỬ XỬ LÝ URL GROUP RÁC & CỰC DÀI
    print("\n[PHẦN 4] Kiểm tra xử lý URL Group rác, cực dài & ký tự lạ...")
    weird_urls = [
        "https://www.facebook.com/groups/test_group/about?ref=share&source=mobile_app",
        "https://facebook.com/groups/123456789/members?sorting_setting=CHRONOLOGICAL",
        "https://m.facebook.com/groups/mobile_group/",
        "https://web.facebook.com/groups/web_group#ref=123",
        "facebook.com/groups/no_protocol"
    ]
    bulk_payload = [{"url": u, "name": f"Nhóm {i}"} for i, u in enumerate(weird_urls)]
    r_bulk = client_auth.post("/groups/bulk-save", json={"groups": bulk_payload})
    assert_test("Bulk save các URL định dạng lạ thành công", r_bulk.status_code == 200 and r_bulk.get_json().get("ok") is True)

    # 5. KIỂM THỬ TRUY VẤN ĐỒNG THỜI (CONCURRENCY & BURST SIMULATION)
    print("\n[PHẦN 5] Kiểm tra truy vấn đồng thời tốc độ cao (High Burst Concurrency)...")
    start_t = time.perf_counter()
    burst_count = 50
    statuses = []
    for _ in range(burst_count):
        r = client_auth.get("/campaign-status")
        statuses.append(r.status_code)
    dur = (time.perf_counter() - start_t) * 1000
    assert_test(f"50 lượt truy vấn liên tục /campaign-status trong {dur:.2f}ms", all(s == 200 for s in statuses))
    assert_test("Thời gian trung bình mỗi request cực nhanh (< 5ms)", (dur / burst_count) < 5.0)

    # 6. KIỂM THỬ CONNECTOR API VỚI TOKEN SAI / THIẾU
    print("\n[PHẦN 6] Kiểm tra phòng thủ API Connector (Token giả, Device sai)...")
    r_fake_dev = client.get("/api/agent/job", headers={"X-Device-ID": "fake_dev_999", "X-Agent-Token": "invalid_tok"})
    assert_test("Từ chối Token giả mạo (/api/agent/job trả về 401)", r_fake_dev.status_code == 401)

    r_fake_stat = client.post("/api/agent/status", headers={"X-Device-ID": "fake", "X-Agent-Token": "fake"}, json={"status": "posting"})
    assert_test("Từ chối báo cáo từ Device chưa xác thực", r_fake_stat.status_code == 401)

    # 7. KIỂM THỬ HẸN GIỜ ĐẾN HẠN KHI KHÔNG CÓ THIẾT BỊ ONLINE (GRACEFUL PAUSE)
    print("\n[PHẦN 7] Kiểm tra xử lý lịch hẹn khi Extension chưa online...")
    due_sched = {
        "schedule_id": "sched_stress_test",
        "campaign_name": "Chiến Dịch Lịch Hẹn Offline",
        "scheduled_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "status": "scheduled",
        "created_at": fb_app.now_iso(),
        "groups": ["https://facebook.com/groups/offline_test"],
        "content": "Nội dung offline",
        "min_delay": 1,
        "max_delay": 2,
    }
    # Đảm bảo device trống
    fb_app.save_devices(dk_cid, {})
    fb_app.save_schedule(dk_cid, due_sched)
    fb_app.check_and_trigger_schedules(dk_cid)
    
    camp_state = fb_app.get_campaign_state(dk_cid)
    assert_test("Chuyển trạng thái 'scheduled_paused' an toàn khi Connector chưa online", camp_state.get("status") == "scheduled_paused")
    assert_test("Thông báo rõ ràng cho người dùng về việc Connector chưa online", "chưa online" in camp_state.get("message", ""))

    # Dọn dẹp
    fb_app.save_schedule(dk_cid, None)
    fb_app.update_campaign_state(dk_cid, running=False, status="idle", message="Sẵn sàng")

    # 8. KIỂM THỬ LỊCH HẸN QUÁ HẠN > 24H (AUTOMATIC EXPIRATION CLEANUP)
    print("\n[PHẦN 8] Kiểm tra tự động hủy lịch hẹn quá hạn > 24 giờ...")
    stale_sched = {
        "schedule_id": "sched_stale_3days",
        "campaign_name": "Lịch Hẹn Cũ 3 Ngày Trước",
        "scheduled_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        "status": "scheduled",
        "created_at": fb_app.now_iso(),
        "groups": ["https://facebook.com/groups/stale_group"],
        "content": "Nội dung cũ",
    }
    fb_app.save_schedule(dk_cid, stale_sched)
    fb_app.check_and_trigger_schedules(dk_cid)
    assert_test("Lịch hẹn cũ > 24h được dọn dẹp an toàn", fb_app.load_schedule(dk_cid) is None)
    history = fb_app.load_history(dk_cid)
    assert_test("Hệ thống ghi log cảnh báo lịch hẹn đã quá hạn", any("quá hạn" in str(h) for h in history))

    # TỔNG KẾT
    print("\n" + "=" * 76)
    print(f"KẾT QUẢ KIỂM THỬ SỨC BỀN & AN TOÀN: {passed}/{total} TIÊU CHÍ PASS 100%!")
    print("KHÔNG PHÁT SINH BẤT KỲ LỖI NGOẠI LỆ (ZERO EXCEPTIONS)!")
    print("=" * 76)

if __name__ == "__main__":
    run_stress_and_safety_tests()
