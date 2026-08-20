const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function visible(el) {
  if (!el) return false;

  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);

  return (
    rect.width > 0 &&
    rect.height > 0 &&
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    style.opacity !== "0"
  );
}

async function waitFor(fn, timeout = 25000, step = 300) {
  const end = Date.now() + timeout;

  while (Date.now() < end) {
    try {
      const result = fn();

      if (result) {
        return result;
      }
    } catch (e) {}

    await sleep(step);
  }

  return null;
}

function textOf(el) {
  return (
    el?.innerText ||
    el?.textContent ||
    ""
  )
    .replace(/\s+/g, " ")
    .trim();
}

function pageNeedsLogin() {
  const url = location.href.toLowerCase();

  if (url.includes("/checkpoint")) {
    return "checkpoint";
  }

  if (
    url.includes("/login") ||
    document.querySelector(
      'input[name="email"], input[name="pass"]'
    )
  ) {
    return "login";
  }

  return "";
}

function findComposerTrigger() {
  const keywords = [
    "bạn viết gì đi",
    "bạn viết gì",
    "bạn đang nghĩ gì",
    "viết gì đó",
    "tạo bài viết",
    "write something",
    "what's on your mind",
    "create post",
    "create a public post"
  ];

  const elements = [
    ...document.querySelectorAll(
      '[role="button"], button, div[tabindex="0"]'
    )
  ].filter(visible);

  for (const el of elements) {
    const value = (
      textOf(el) +
      " " +
      (el.getAttribute("aria-label") || "")
    ).toLowerCase();

    if (
      keywords.some(keyword =>
        value.includes(keyword)
      )
    ) {
      return el;
    }
  }

  return null;
}

function getEditors(root) {
  if (!root) return [];

  const selectors = [
    '[data-lexical-editor="true"][contenteditable="true"]',
    '[role="textbox"][contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
    '[contenteditable="true"]'
  ];

  const result = [];
  const seen = new Set();

  for (const selector of selectors) {
    let elements = [];

    try {
      elements = [
        ...root.querySelectorAll(selector)
      ];
    } catch (e) {
      continue;
    }

    for (const el of elements) {
      if (seen.has(el)) continue;

      seen.add(el);

      if (!visible(el)) continue;

      const rect = el.getBoundingClientRect();

      if (
        rect.width < 120 ||
        rect.height < 18
      ) {
        continue;
      }

      result.push(el);
    }
  }

  return result;
}

function scoreEditor(el) {
  let score = 0;

  const rect = el.getBoundingClientRect();

  const aria = (
    el.getAttribute("aria-label") ||
    ""
  ).toLowerCase();

  const placeholder = (
    el.getAttribute("data-placeholder") ||
    ""
  ).toLowerCase();

  if (
    el.getAttribute("data-lexical-editor") ===
    "true"
  ) {
    score += 100000;
  }

  if (
    el.getAttribute("role") ===
    "textbox"
  ) {
    score += 50000;
  }

  if (
    /bạn đang nghĩ gì|bạn viết gì|viết gì đó|bài viết|what's on your mind|write something|create post/.test(
      aria + " " + placeholder
    )
  ) {
    score += 100000;
  }

  score += Math.min(
    rect.width * rect.height,
    300000
  );

  if (rect.height < 35) {
    score -= 50000;
  }

  return score;
}

function findBestEditor(root) {
  const editors = getEditors(root);

  if (!editors.length) {
    return null;
  }

  editors.sort(
    (a, b) =>
      scoreEditor(b) -
      scoreEditor(a)
  );

  return editors[0];
}

function findComposerDialog() {
  const dialogs = [
    ...document.querySelectorAll(
      '[role="dialog"]'
    )
  ].filter(visible);

  if (!dialogs.length) {
    return null;
  }

  const candidates = [];

  for (const dialog of dialogs) {
    const editor =
      findBestEditor(dialog);

    if (!editor) continue;

    const text = (
      textOf(dialog).slice(0, 600) +
      " " +
      (
        dialog.getAttribute(
          "aria-label"
        ) || ""
      )
    ).toLowerCase();

    let score = 0;

    if (
      /tạo bài viết|create post|bài viết/.test(
        text
      )
    ) {
      score += 10000;
    }

    score += scoreEditor(editor);

    candidates.push({
      dialog,
      score
    });
  }

  candidates.sort(
    (a, b) =>
      b.score -
      a.score
  );

  return (
    candidates[0]?.dialog ||
    null
  );
}

async function openComposer() {
  let dialog =
    findComposerDialog();

  if (dialog) {
    return dialog;
  }

  const trigger =
    await waitFor(
      findComposerTrigger,
      25000,
      400
    );

  if (!trigger) {
    throw new Error(
      "Không tìm thấy ô tạo bài viết trong Group."
    );
  }

  trigger.scrollIntoView({
    block: "center",
    inline: "center"
  });

  await sleep(500);

  try {
    trigger.click();
  } catch (e) {}

  await sleep(1000);

  dialog =
    await waitFor(
      findComposerDialog,
      20000,
      400
    );

  if (dialog) {
    return dialog;
  }

  const editor =
    await waitFor(
      () =>
        findBestEditor(document),
      10000,
      400
    );

  if (editor) {
    return (
      editor.closest(
        '[role="dialog"]'
      ) ||
      document.body
    );
  }

  throw new Error(
    "Không mở được cửa sổ Tạo bài viết."
  );
}

async function fillText(
  dialog,
  content
) {
  let editor =
    await waitFor(
      () =>
        findBestEditor(dialog),
      15000,
      300
    );

  if (!editor) {
    editor =
      await waitFor(
        () =>
          findBestEditor(document),
        10000,
        300
      );
  }

  if (!editor) {
    console.error(
      "FB POST PRO DEBUG",
      {
        url:
          location.href,

        dialogs:
          document.querySelectorAll(
            '[role="dialog"]'
          ).length,

        editable:
          document.querySelectorAll(
            '[contenteditable="true"]'
          ).length,

        textbox:
          document.querySelectorAll(
            '[role="textbox"]'
          ).length
      }
    );

    throw new Error(
      "Không tìm thấy ô nhập nội dung Facebook."
    );
  }

  console.log(
    "FB POST PRO: tìm thấy editor",
    editor
  );

  editor.scrollIntoView({
    block: "center"
  });

  await sleep(300);

  try {
    editor.click();
  } catch (e) {}

  editor.focus();

  await sleep(400);

  /*
   * Xóa nội dung cũ.
   */
  try {
    const selection =
      window.getSelection();

    const range =
      document.createRange();

    range.selectNodeContents(
      editor
    );

    selection.removeAllRanges();

    selection.addRange(
      range
    );

    document.execCommand(
      "delete",
      false,
      null
    );
  } catch (e) {}

  await sleep(250);

  /*
   * Cách chính để nhập vào
   * Facebook Lexical Editor.
   */
  try {
    editor.focus();

    document.execCommand(
      "insertText",
      false,
      content
    );
  } catch (e) {
    console.warn(
      "insertText lỗi",
      e
    );
  }

  await sleep(500);

  let current =
    textOf(editor);

  /*
   * Nếu insertText lần đầu
   * chưa hoạt động.
   */
  if (
    content &&
    !current.includes(
      content.slice(
        0,
        Math.min(
          10,
          content.length
        )
      )
    )
  ) {
    try {
      editor.focus();

      const selection =
        window.getSelection();

      const range =
        document.createRange();

      range.selectNodeContents(
        editor
      );

      range.collapse(false);

      selection.removeAllRanges();

      selection.addRange(
        range
      );

      document.execCommand(
        "insertText",
        false,
        content
      );
    } catch (e) {}
  }

  /*
   * Trigger event cho React/Lexical.
   */
  try {
    editor.dispatchEvent(
      new InputEvent(
        "beforeinput",
        {
          bubbles: true,
          cancelable: true,
          inputType:
            "insertText",
          data:
            content
        }
      )
    );
  } catch (e) {}

  try {
    editor.dispatchEvent(
      new InputEvent(
        "input",
        {
          bubbles: true,
          inputType:
            "insertText",
          data:
            content
        }
      )
    );
  } catch (e) {}

  try {
    editor.dispatchEvent(
      new Event(
        "change",
        {
          bubbles: true
        }
      )
    );
  } catch (e) {}

  await sleep(1000);

  current =
    textOf(editor);

  if (
    content &&
    !current.includes(
      content.slice(
        0,
        Math.min(
          8,
          content.length
        )
      )
    )
  ) {
    throw new Error(
      "Đã tìm thấy ô nhập nhưng Facebook không nhận nội dung."
    );
  }

  console.log(
    "FB POST PRO: nhập nội dung thành công"
  );

  return editor;
}

function b64ToFile(item) {
  const binary =
    atob(item.base64);

  const bytes =
    new Uint8Array(
      binary.length
    );

  for (
    let i = 0;
    i < binary.length;
    i++
  ) {
    bytes[i] =
      binary.charCodeAt(i);
  }

  return new File(
    [bytes],
    item.name ||
      "image.jpg",
    {
      type:
        item.mime ||
        "image/jpeg"
    }
  );
}

async function attachImages(
  dialog,
  images
) {
  if (
    !images ||
    !images.length
  ) {
    return;
  }

  let input =
    dialog.querySelector(
      'input[type="file"]'
    );

  if (!input) {
    input =
      document.querySelector(
        'input[type="file"][accept*="image"]'
      );
  }

  if (!input) {
    const buttons = [
      ...dialog.querySelectorAll(
        '[role="button"], button, div[tabindex="0"]'
      )
    ].filter(visible);

    const photoButton =
      buttons.find(el => {
        const text = (
          textOf(el) +
          " " +
          (
            el.getAttribute(
              "aria-label"
            ) || ""
          )
        ).toLowerCase();

        return (
          text.includes("ảnh/video") ||
          text.includes("ảnh") ||
          text.includes("photo/video") ||
          text.includes("photo")
        );
      });

    if (photoButton) {
      try {
        photoButton.click();
      } catch (e) {}

      await sleep(1500);

      input =
        dialog.querySelector(
          'input[type="file"]'
        ) ||
        document.querySelector(
          'input[type="file"][accept*="image"]'
        );
    }
  }

  if (!input) {
    throw new Error(
      "Không tìm thấy ô upload ảnh."
    );
  }

  const dataTransfer =
    new DataTransfer();

  for (const item of images) {
    dataTransfer.items.add(
      b64ToFile(item)
    );
  }

  input.files =
    dataTransfer.files;

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
      5000,
      images.length * 2000
    )
  );
}

async function clickPost(
  dialog
) {
  const buttons = [
    ...dialog.querySelectorAll(
      '[role="button"], button'
    )
  ].filter(visible);

  let postButton =
    buttons.find(el => {
      const text =
        textOf(el)
          .trim()
          .toLowerCase();

      return (
        text === "đăng" ||
        text === "post"
      );
    });

  if (!postButton) {
    postButton =
      buttons.find(el => {
        const label = (
          el.getAttribute(
            "aria-label"
          ) || ""
        )
          .trim()
          .toLowerCase();

        return (
          label === "đăng" ||
          label === "post"
        );
      });
  }

  if (!postButton) {
    throw new Error(
      "Không tìm thấy nút Đăng."
    );
  }

  const ready =
    await waitFor(
      () =>
        postButton.getAttribute(
          "aria-disabled"
        ) !== "true" &&
        !postButton.hasAttribute(
          "disabled"
        ),
      30000,
      500
    );

  if (!ready) {
    throw new Error(
      "Nút Đăng chưa sẵn sàng."
    );
  }

  postButton.scrollIntoView({
    block: "center"
  });

  await sleep(400);

  postButton.click();

  await sleep(1500);

  const closed =
    await waitFor(
      () =>
        !document.contains(
          dialog
        ) ||
        !visible(dialog),
      60000,
      500
    );

  if (!closed) {
    throw new Error(
      "Đã bấm Đăng nhưng cửa sổ tạo bài chưa đóng."
    );
  }
}

async function postCurrentGroup(
  payload
) {
  const loginState =
    pageNeedsLogin();

  if (
    loginState ===
    "checkpoint"
  ) {
    return {
      ok: false,
      code:
        "checkpoint",
      error:
        "Facebook yêu cầu checkpoint/xác minh."
    };
  }

  if (
    loginState ===
    "login"
  ) {
    return {
      ok: false,
      code:
        "login",
      error:
        "Facebook chưa đăng nhập."
    };
  }

  /*
   * Chờ Facebook render
   * sau khi trang vừa load.
   */
  await sleep(4000);

  const dialog =
    await openComposer();

  await fillText(
    dialog,
    payload.content || ""
  );

  await attachImages(
    dialog,
    payload.images || []
  );

  await clickPost(
    dialog
  );

  return {
    ok: true
  };
}

chrome.runtime.onMessage.addListener(
  (
    msg,
    sender,
    sendResponse
  ) => {
    if (
      msg?.type !==
      "FBPOST_POST"
    ) {
      return;
    }

    postCurrentGroup(msg)
      .then(result => {
        sendResponse(
          result
        );
      })
      .catch(error => {
        console.error(
          "FB POST PRO RUNNER ERROR:",
          error
        );

        sendResponse({
          ok: false,
          error:
            error?.message ||
            String(error)
        });
      });

    return true;
  }
);

console.log(
  "FB POST PRO facebook_runner.js loaded"
);