"""Small, non-secret Facebook identity checks for the existing device binding.

This verifies consistency reported by a trusted connector, not remote attestation.
The connector keeps the Facebook cookie local; only a namespaced digest is sent.
"""
import hashlib
import re
import secrets


def facebook_session_fingerprint(facebook_user_id):
    identity = str(facebook_user_id or "").strip()
    if not re.fullmatch(r"[0-9]{1,30}", identity):
        return ""
    return hashlib.sha256(("fbpostpro:facebook-user:" + identity).encode("utf-8")).hexdigest()


def verify_worker_session(account, device_id, device):
    """Return a safe, user-actionable error, or an empty string when consistent."""
    expected = facebook_session_fingerprint(account.get("facebook_user_id"))
    if not expected:
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
