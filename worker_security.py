import hashlib
import os
import re
import secrets


def is_simple_mode():
    return os.environ.get("SIMPLE_MODE", "1").strip().lower() not in {"0", "false", "no"}


def facebook_session_fingerprint(facebook_user_id):
    identity = str(facebook_user_id or "").strip()
    if not re.fullmatch(r"[0-9]{1,30}", identity):
        return ""
    return hashlib.sha256(("fbpostpro:facebook-user:" + identity).encode("utf-8")).hexdigest()


def verify_worker_session(account, device_id, device, simple_mode=None):
    """Return a safe, user-actionable error, or an empty string when consistent."""
    if simple_mode is None:
        simple_mode = is_simple_mode()

    if account is None:
        account = {}
    if device is None:
        device = {}

    # In Simple Mode, if user is not logged into Facebook
    if simple_mode:
        if device.get("facebook_logged_in") is False or device.get("status") == "needs_login":
            return "Vui lòng đăng nhập Facebook trong Chrome."
        dev_fp = str(device.get("facebook_session_fingerprint") or "").strip()
        acc_fp = facebook_session_fingerprint(account.get("facebook_user_id"))
        if acc_fp and dev_fp and acc_fp != dev_fp:
            return "Facebook đang đăng nhập không đúng account được gán. Phát hiện thay đổi tài khoản trên Chrome."
        acc_uid = str(account.get("facebook_user_id") or "").strip()
        dev_uid = str(device.get("facebook_user_id") or "").strip()
        if acc_uid and dev_uid and acc_uid != dev_uid:
            return "Facebook đang đăng nhập không đúng account được gán. Phát hiện thay đổi tài khoản trên Chrome."
        if device.get("facebook_logged_in") is True:
            return ""

    expected = facebook_session_fingerprint(account.get("facebook_user_id"))
    if not expected:
        if simple_mode and device.get("facebook_logged_in"):
            return ""
        return "Facebook account cần Facebook user ID dạng số để xác minh đúng phiên đăng nhập."
    profile = f"chrome-profile:{device_id}"
    if account.get("device_id") != device_id or account.get("browser_profile_id") != profile:
        return "Facebook account chưa được gắn đúng desktop worker/Chrome profile."
    if device.get("session_verification_version") != 1:
        return "Hãy cập nhật Connector và gửi heartbeat để xác minh Facebook session trước khi chạy."
    if device.get("browser_profile_id") != profile or device.get("session_context") != profile:
        return "Chrome profile/session không khớp với mapping đã lưu."
    actual = str(device.get("facebook_session_fingerprint", ""))
    if not re.fullmatch(r"[a-f0-9]{64}", actual) or not secrets.compare_digest(actual, expected):
        return "Facebook đang đăng nhập không đúng account được gán. Hãy kiểm tra Chrome profile trước khi tiếp tục."
    return ""
