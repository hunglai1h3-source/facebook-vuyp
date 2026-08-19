const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

let FBPOST_LOCK = null;

function visible(el) {
    if (!el) return false;

    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);

    return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none"
    );
}

async function waitFor(fn, timeout = 20000, step = 250) {
    const end = Date.now() + timeout;

    while (Date.now() < end) {
        try {
            const value = fn();
            if (value) return value;
        } catch (e) {}

        await sleep(step);
    }

    return null;
}

function pageNeedsLogin() {
    const url = location.href.toLowerCase();

    if (url.includes("/checkpoint")) {
        return "checkpoint";
    }

    if (
        url.includes("/login") ||
        document.querySelector('input[name="email"], input[name="pass"]')
    ) {
        return "login";
    }

    return "";
}

function textOf(el) {
    return (el?.innerText || el?.textContent || "")
        .replace(/\s+/g, " ")
        .trim();
}

// ============================================================
// KHÓA THAO TÁC NGƯỜI DÙNG KHI ĐANG ĐĂNG
// ============================================================

function lockUserInteraction() {
    if (FBPOST_LOCK) return;

    const events = [
        "pointerdown",
        "pointerup",
        "mousedown",
        "mouseup",
        "click",
        "dblclick",
        "contextmenu",
        "wheel",
        "touchstart",
        "touchmove",
        "keydown",
        "keyup"
    ];

    const blocker = event => {
        // Không chặn click do code tạo ra.
        if (!event.isTrusted) return;

        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
    };

    for (const eventName of events) {
        window.addEventListener(eventName, blocker, true);
    }

    const overlay = document.createElement("div");
    overlay.id = "fbpostpro-running-lock";

    Object.assign(overlay.style, {
        position: "fixed",
        inset: "0",
        zIndex: "2147483647",
        background: "rgba(0, 0, 0, 0.02)",
        pointerEvents: "auto",
        cursor: "wait"
    });

    const notice = document.createElement("div");
    notice.innerHTML = `
        <div style="font-weight:700;font-size:15px;margin-bottom:5px;">
            FB POST PRO đang đăng bài
        </div>
        <div style="font-size:13px;opacity:.85;">
            Bạn có thể xem tab này. Thao tác chuột tạm thời được khóa để tránh làm gián đoạn chiến dịch.
        </div>
    `;

    Object.assign(notice.style, {
        position: "fixed",
        top: "18px",
        left: "50%",
        transform: "translateX(-50%)",
        width: "min(520px, calc(100% - 32px))",
        padding: "14px 18px",
        borderRadius: "14px",
        color: "#fff",
        background: "rgba(25,25,30,.94)",
        boxShadow: "0 8px 30px rgba(0,0,0,.25)",
        fontFamily: "Arial, sans-serif",
        textAlign: "center",
        pointerEvents: "none"
    });

    overlay.appendChild(notice);
    document.documentElement.appendChild(overlay);

    FBPOST_LOCK = {
        events,
        blocker,
        overlay
    };
}

function unlockUserInteraction() {
    if (!FBPOST_LOCK) return;

    for (const eventName of FBPOST_LOCK.events) {
        window.removeEventListener(
            eventName,
            FBPOST_LOCK.blocker,
            true
        );
    }

    try {
        FBPOST_LOCK.overlay?.remove();
    } catch (e) {}

    FBPOST_LOCK = null;
}

// ============================================================
// TÌM VÀ MỞ HỘP TẠO BÀI
// ============================================================

function findComposerTrigger() {
    const elements = [
        ...document.querySelectorAll(
            '[role="button"], div[tabindex="0"]'
        )
    ].filter(visible);

    const needles = [
        "bạn viết gì đi",
        "bạn viết gì",
        "viết gì đó",
        "write something",
        "create a public post",
        "tạo bài viết"
    ];

    for (const el of elements) {
        const text = textOf(el).toLowerCase();

        if (needles.some(needle => text.includes(needle))) {
            return el;
        }
    }

    return null;
}

function findDialog() {
    const dialogs = [
        ...document.querySelectorAll('div[role="dialog"]')
    ].filter(visible);

    for (const dialog of dialogs) {
        if (
            dialog.querySelector('[contenteditable="true"]') ||
            dialog.querySelector('[role="textbox"]')
        ) {
            return dialog;
        }
    }

    return dialogs[dialogs.length - 1] || null;
}

async function openComposer() {
    let dialog = findDialog();

    if (dialog) {
        return dialog;
    }

    const trigger = await waitFor(
        findComposerTrigger,
        15000,
        250
    );

    if (!trigger) {
        throw new Error(
            "Không tìm thấy ô tạo bài viết trong Group."
        );
    }

    trigger.click();

    dialog = await waitFor(
        findDialog,
        15000,
        250
    );

    if (!dialog) {
        throw new Error(
            "Không mở được cửa sổ Tạo bài viết."
        );
    }

    return dialog;
}

// ============================================================
// TÌM Ô NHẬP NỘI DUNG
// ============================================================

function findPostTextbox(dialog) {
    const selectors = [
        '[role="textbox"][contenteditable="true"]',
        '[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"][data-lexical-editor="true"]',
        'div[contenteditable="true"]'
    ];

    const roots = [];

    if (dialog) {
        roots.push(dialog);
    }

    roots.push(document);

    for (const root of roots) {
        for (const selector of selectors) {
            const boxes = [
                ...root.querySelectorAll(selector)
            ].filter(visible);

            for (const box of boxes) {
                const aria = (
                    box.getAttribute("aria-label") || ""
                ).toLowerCase();

                const placeholder = (
                    box.getAttribute("data-placeholder") || ""
                ).toLowerCase();

                // Loại các ô không phải ô viết bài.
                if (
                    aria.includes("bình luận") ||
                    aria.includes("comment") ||
                    aria.includes("tìm kiếm") ||
                    aria.includes("search")
                ) {
                    continue;
                }

                // Ưu tiên các nhãn thường gặp của Facebook.
                if (
                    aria.includes("bạn viết gì") ||
                    aria.includes("write something") ||
                    aria.includes("tạo bài") ||
                    aria.includes("create post") ||
                    placeholder.includes("bạn viết gì") ||
                    placeholder.includes("write something")
                ) {
                    return box;
                }

                // Nếu nằm trong dialog tạo bài thì ưu tiên luôn.
                if (dialog && dialog.contains(box)) {
                    return box;
                }

                if (
                    box.getAttribute("contenteditable") === "true" &&
                    box.getAttribute("role") === "textbox"
                ) {
                    return box;
                }
            }
        }
    }

    return null;
}

async function fillText(dialog, content) {
    const box = await waitFor(
        () => findPostTextbox(dialog),
        20000,
        300
    );

    if (!box) {
        throw new Error(
            "Không tìm thấy ô nhập nội dung Facebook."
        );
    }

    box.scrollIntoView({
        block: "center",
        inline: "nearest"
    });

    box.focus();
    await sleep(400);

    // Xóa nội dung cũ trong chính textbox.
    try {
        const selection = window.getSelection();
        const range = document.createRange();

        range.selectNodeContents(box);
        selection.removeAllRanges();
        selection.addRange(range);

        document.execCommand(
            "delete",
            false,
            null
        );
    } catch (e) {}

    await sleep(200);

    let inserted = false;

    // Cách Facebook/Lexical thường nhận tốt nhất.
    try {
        inserted = document.execCommand(
            "insertText",
            false,
            content
        );
    } catch (e) {}

    // Fallback nếu execCommand không nhập được.
    if (
        !inserted ||
        !(box.innerText || box.textContent || "").trim()
    ) {
        try {
            box.textContent = content;

            box.dispatchEvent(
                new InputEvent(
                    "input",
                    {
                        bubbles: true,
                        inputType: "insertText",
                        data: content
                    }
                )
            );
        } catch (e) {
            box.textContent = content;

            box.dispatchEvent(
                new Event(
                    "input",
                    {
                        bubbles: true
                    }
                )
            );
        }
    }

    box.dispatchEvent(
        new Event(
            "change",
            {
                bubbles: true
            }
        )
    );

    await sleep(1000);

    const finalText = (
        box.innerText ||
        box.textContent ||
        ""
    ).trim();

    if (!finalText && String(content || "").trim()) {
        throw new Error(
            "Đã tìm thấy ô nhập nhưng Facebook chưa nhận nội dung."
        );
    }
}

// ============================================================
// XỬ LÝ ẢNH
// ============================================================

function b64ToFile(item) {
    const binary = atob(item.base64);
    const bytes = new Uint8Array(binary.length);

    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }

    return new File(
        [bytes],
        item.name || "image.jpg",
        {
            type: item.mime || "image/jpeg"
        }
    );
}

async function attachImages(dialog, images) {
    if (!images?.length) {
        return;
    }

    let input = dialog.querySelector(
        'input[type="file"]'
    );

    if (!input) {
        const controls = [
            ...dialog.querySelectorAll(
                '[role="button"], button, div[tabindex="0"]'
            )
        ].filter(visible);

        const button = controls.find(el =>
            /ảnh\/?video|photo\/?video/i.test(textOf(el))
        );

        if (button) {
            button.click();
            await sleep(900);

            input = dialog.querySelector(
                'input[type="file"]'
            );
        }
    }

    if (!input) {
        throw new Error(
            "Không tìm thấy ô upload ảnh."
        );
    }

    const transfer = new DataTransfer();

    for (const item of images) {
        transfer.items.add(
            b64ToFile(item)
        );
    }

    input.files = transfer.files;

    input.dispatchEvent(
        new Event(
            "input",
            {
                bubbles: true
            }
        )
    );

    input.dispatchEvent(
        new Event(
            "change",
            {
                bubbles: true
            }
        )
    );

    await sleep(
        Math.max(
            3500,
            images.length * 1800
        )
    );
}

// ============================================================
// BẤM NÚT ĐĂNG
// ============================================================

async function clickPost(dialog) {
    const candidates = [
        ...dialog.querySelectorAll(
            '[role="button"], button'
        )
    ].filter(visible);

    let button = candidates.find(el =>
        /^(đăng|post)$/i.test(textOf(el))
    );

    if (!button) {
        button = candidates.find(el =>
            /\bđăng\b|\bpost\b/i.test(textOf(el))
        );
    }

    if (!button) {
        throw new Error(
            "Không tìm thấy nút Đăng."
        );
    }

    const ready = await waitFor(
        () =>
            !button.hasAttribute("aria-disabled") &&
            button.getAttribute("aria-disabled") !== "true" &&
            !button.disabled,
        30000,
        300
    );

    if (!ready) {
        throw new Error(
            "Nút Đăng chưa sẵn sàng."
        );
    }

    button.click();

    const gone = await waitFor(
        () =>
            !document.contains(dialog) ||
            !visible(dialog),
        60000,
        500
    );

    if (!gone) {
        throw new Error(
            "Đã bấm Đăng nhưng cửa sổ tạo bài chưa đóng."
        );
    }
}

// ============================================================
// CHẠY 1 GROUP
// ============================================================

async function postCurrentGroup(payload) {
    const state = pageNeedsLogin();

    if (state === "checkpoint") {
        return {
            ok: false,
            code: "checkpoint",
            error: "Facebook yêu cầu checkpoint/xác minh."
        };
    }

    if (state === "login") {
        return {
            ok: false,
            code: "login",
            error: "Facebook chưa đăng nhập."
        };
    }

    lockUserInteraction();

    try {
        await sleep(1800);

        const dialog = await openComposer();

        await fillText(
            dialog,
            payload.content || ""
        );

        await attachImages(
            dialog,
            payload.images || []
        );

        await clickPost(dialog);

        return {
            ok: true
        };
    } finally {
        unlockUserInteraction();
    }
}

// ============================================================
// NHẬN LỆNH TỪ SERVICE WORKER
// ============================================================

chrome.runtime.onMessage.addListener(
    (msg, sender, sendResponse) => {
        if (msg?.type !== "FBPOST_POST") {
            return;
        }

        postCurrentGroup(msg)
            .then(sendResponse)
            .catch(error =>
                sendResponse({
                    ok: false,
                    error:
                        error?.message ||
                        String(error)
                })
            );

        return true;
    }
);