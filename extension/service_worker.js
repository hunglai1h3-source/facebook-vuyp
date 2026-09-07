const VERSION =
  chrome.runtime
    .getManifest()
    .version;

let busy = false;
let currentJobId = '';
let currentJob = null;
let currentOwner = null;
let currentExecutionTab = null;
let pollPromise = null;
let workerState = 'idle';
let lastHeartbeatAt = 0;
let lastHeartbeatResult = null;

const sleep = ms =>
  new Promise(
    r => setTimeout(r, ms)
  );

async function cfg() {
  return await chrome.storage.local.get([
    'serverOrigin',
    'deviceId',
    'token',
    'customerId'
  ]);
}

function headers(
  c,
  json = true
) {
  const h = {
    'X-Device-ID':
      c.deviceId,
    'X-Agent-Token':
      c.token
  };

  if (json) {
    h['Content-Type'] =
      'application/json';
  }

  return h;
}

async function facebookLoggedIn() {
  try {
    const c =
      await chrome.cookies.get({
        url:
          'https://www.facebook.com/',
        name:
          'c_user'
      });

    return !!(
      c &&
      c.value
    );
  } catch (e) {
    return false;
  }
}

async function facebookSessionFingerprint() {
  const cookie = await chrome.cookies.get({url: 'https://www.facebook.com/', name: 'c_user'});
  const identity = String(cookie?.value || '');
  if (!/^[0-9]{1,30}$/.test(identity)) return '';
  const digest = await crypto.subtle.digest(
    'SHA-256', new TextEncoder().encode('fbpostpro:facebook-user:' + identity)
  );
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
}

async function verifyJobSession(c, job) {
  if (!job?.engine_task_id && !job?.account_id) return true;
  const profile = 'chrome-profile:' + c.deviceId;
  if (job.device_id !== c.deviceId || job.browser_profile_id !== profile || job.session_context !== profile) {
    throw new Error('Worker/Chrome profile không khớp account của task. Đã dừng trước khi đăng.');
  }
  const expected = String(job.expected_session_fingerprint || '');
  if (!/^[a-f0-9]{64}$/.test(expected)) {
    throw new Error('Account chưa được xác minh Facebook user ID/session. Hãy cấu hình đúng account trước khi chạy.');
  }
  if (await facebookSessionFingerprint() !== expected) {
    throw new Error('Facebook session hiện tại không đúng account được gán. Đã dừng trước khi đăng.');
  }
  return true;
}

async function verifyExecutionRequest(msg, sender) {
  if (!busy || !currentJob || msg.job_id !== currentJobId || sender.tab?.id !== currentExecutionTab || sender.tab?.incognito) {
    return {ok: false, error: 'Execution không còn active; đã chặn thao tác đăng.'};
  }
  try {
    const c = await cfg();
    if (!currentOwner || c.deviceId !== currentOwner.deviceId || c.serverOrigin !== currentOwner.serverOrigin || c.token !== currentOwner.token) {
      return {ok: false, error: 'Liên kết worker đã thay đổi; đã dừng execution cũ.'};
    }
    await verifyJobSession(c, currentJob);
    const ctl = await control(c);
    if (ctl.unavailable || ctl.stop_requested || ctl.pause_requested) {
      return {ok: false, error: 'Không thể xác nhận lệnh tiếp tục; đã dừng trước khi đăng.'};
    }
    return {ok: true};
  } catch (error) {
    return {ok: false, error: error.message};
  }
}

async function heartbeat(
  force = false
) {
  const c =
    await cfg();

  if (
    !c.serverOrigin ||
    !c.deviceId ||
    !c.token
  ) {
    return {
      ok: false,
      error:
        'Chưa liên kết'
    };
  }

  if (
    !force &&
    lastHeartbeatResult &&
    (
      Date.now() -
      lastHeartbeatAt
    ) < 15000
  ) {
    return lastHeartbeatResult;
  }

  const sessionFingerprint = await facebookSessionFingerprint();
  const fb = Boolean(sessionFingerprint);

  const r =
    await fetch(
      c.serverOrigin +
        '/api/agent/heartbeat',
      {
        method:
          'POST',

        headers:
          headers(c),

        body:
          JSON.stringify({
            device_name:
              'Google Chrome • FB POST PRO',

            extension_version:
              VERSION,

            facebook_logged_in:
              fb,

            worker_state:
              workerState,

            current_job_id:
              currentJobId,
            execution_token: currentJob?.execution_token || '',
            facebook_session_fingerprint: sessionFingerprint,
            session_verification_version: 1,
            browser_profile_id: 'chrome-profile:' + c.deviceId,
            session_context: 'chrome-profile:' + c.deviceId
          })
      }
    );

  if (!r.ok) {
    throw new Error(
      'Heartbeat ' +
      r.status
    );
  }

  lastHeartbeatAt =
    Date.now();

  lastHeartbeatResult = {
    ok: true,

    facebookLoggedIn:
      fb,

    deviceName:
      'Google Chrome'
  };

  return lastHeartbeatResult;
}

async function pairFromWeb(
  serverOrigin,
  code
) {
  const linked = await cfg();
  if (busy) return {ok: false, error: 'Hãy dừng campaign trước khi liên kết lại Connector.'};
  const server =
    String(
      serverOrigin ||
      ''
    )
      .trim()
      .replace(
        /\/+$/,
        ''
      );

  const pairCode =
    String(
      code ||
      ''
    )
      .trim()
      .toUpperCase();

  // Content scripts also run on other Render apps. Only a site already paired
  // through the extension popup may rotate its own binding.
  if (!linked.deviceId || !linked.token || server !== linked.serverOrigin) {
    return {ok: false, error: 'Hãy mở popup Connector để xác nhận URL website và liên kết lần đầu.'};
  }

  if (
    !server ||
    !pairCode
  ) {
    return {
      ok: false,
      error:
        'Thiếu website hoặc mã liên kết'
    };
  }

  try {
    const r =
      await fetch(
        server +
          '/api/extension/pair',
        {
          method:
            'POST',

          headers: {
            'Content-Type':
              'application/json'
          },

          body:
            JSON.stringify({
              code:
                pairCode,

              device_name:
                'Google Chrome • FB POST PRO',

              extension_version:
                VERSION
            })
        }
      );

    const d =
      await r.json();

    if (!r.ok) {
      throw new Error(
        d.error ||
        'Liên kết thất bại'
      );
    }

    await chrome.storage.local.set({
      serverOrigin:
        server,

      deviceId:
        d.device_id,

      token:
        d.token,

      customerId:
        d.customer_id
    });

    lastHeartbeatAt = 0;
    lastHeartbeatResult = null;

    const hb =
      await heartbeat(true);

    return {
      ok: true,

      facebookLoggedIn:
        hb.facebookLoggedIn,

      deviceId:
        d.device_id
    };
  } catch (e) {
    return {
      ok: false,

      error:
        e?.message ||
        String(e)
    };
  }
}

async function report(
  c,
  data
) {
  try {
    const r = await fetch(
      c.serverOrigin +
        '/api/agent/status',
      {
        method:
          'POST',

        headers:
          headers(c),

        body:
          JSON.stringify({
            ...data,
            job_id:
              data.job_id || currentJobId,
            execution_token: currentJob?.execution_token || ''
          })
      }
    );
    if (!r.ok) {
      throw new Error('Status update ' + r.status);
    }
    return true;
  } catch (e) {
    console.warn(
      'report',
      e
    );
    return false;
  }
}

async function control(c) {
  try {
    const r =
      await fetch(
        c.serverOrigin +
          '/api/agent/control',
        {
          headers:
            headers(
              c,
              false
            )
        }
      );

    if (r.ok) {
      return await r.json();
    }
    if (r.status === 401 || r.status === 403) {
      return {unauthorized: true, unavailable: true};
    }
  } catch (e) {}

  return { unavailable: true };
}

async function acknowledgeControl(c, ctl, kind) {
  if (!ctl?.command_id) return;
  try {
    await fetch(c.serverOrigin + '/api/agent/control/ack', {
      method: 'POST',
      headers: headers(c),
      body: JSON.stringify({
        command_id: ctl.command_id,
        [`${kind}_ack`]: true
      })
    });
  } catch (e) {
    console.warn('control ack', e);
  }
}

async function waitForSafeControl(c, stats) {
  let paused = false;
  while (true) {
    const ctl = await control(c);
    if (ctl.unauthorized) {
      throw new Error('Liên kết worker đã hết hạn hoặc bị thu hồi. Đã dừng; hãy liên kết lại Connector.');
    }
    if (ctl.unavailable) {
      await sleep(3000);
      continue;
    }
    if (ctl.stop_requested) {
      await acknowledgeControl(c, ctl, 'stop');
      return 'stop';
    }
    if (ctl.pause_requested) {
      if (!paused) {
        paused = true;
        workerState = 'paused';
        await report(c, {
          status: 'paused',
          message: 'Chiến dịch đang tạm dừng ở điểm an toàn.',
          ...stats
        });
        await acknowledgeControl(c, ctl, 'pause');
      }
      try { await heartbeat(); } catch (e) {}
      await sleep(2000);
      continue;
    }
    if (paused || ctl.resume_requested) {
      workerState = 'busy';
      await acknowledgeControl(c, ctl, 'resume');
      await report(c, {
        status: 'running',
        message: 'Desktop worker đã tiếp tục chiến dịch.',
        ...stats
      });
    }
    return 'continue';
  }
}

function rnd(
  min,
  max
) {
  min =
    Math.max(
      0,
      Number(min) ||
      0
    );

  max =
    Math.max(
      0,
      Number(max) ||
      0
    );

  if (min > max) {
    [
      min,
      max
    ] = [
      max,
      min
    ];
  }

  return (
    Math.floor(
      Math.random() *
      (
        max -
        min +
        1
      )
    ) +
    min
  );
}

async function imageData(
  c,
  name
) {
  const r =
    await fetch(
      c.serverOrigin +
        '/api/agent/image/' +
        encodeURIComponent(
          name
        ),
      {
        headers:
          headers(
            c,
            false
          )
      }
    );

  if (!r.ok) {
    throw new Error(
      'Không tải được ảnh ' +
      name
    );
  }

  const blob =
    await r.blob();

  if (
    blob.size >
    8 *
      1024 *
      1024
  ) {
    throw new Error(
      'Ảnh ' +
      name +
      ' lớn hơn 8MB; hãy nén ảnh.'
    );
  }

  const buf =
    new Uint8Array(
      await blob.arrayBuffer()
    );

  let binary = '';

  const chunk =
    0x8000;

  for (
    let i = 0;
    i < buf.length;
    i += chunk
  ) {
    binary +=
      String.fromCharCode(
        ...buf.subarray(
          i,
          i + chunk
        )
      );
  }

  return {
    name,

    mime:
      blob.type ||
      'application/octet-stream',

    base64:
      btoa(binary)
  };
}

async function loadImages(
  c,
  names
) {
  const out = [];
  let total = 0;

  for (
    const n of names ||
    []
  ) {
    const item =
      await imageData(
        c,
        n
      );

    total +=
      Math.ceil(
        item.base64.length *
        0.75
      );

    if (
      total >
      30 *
        1024 *
        1024
    ) {
      throw new Error(
        'Tổng ảnh vượt 30MB; hãy giảm dung lượng ảnh.'
      );
    }

    out.push(item);
  }

  return out;
}

function waitTab(
  tabId,
  timeout = 45000
) {
  return new Promise(
    (
      resolve,
      reject
    ) => {
      const end =
        Date.now() +
        timeout;

      let finished =
        false;

      const cleanup =
        () => {
          chrome.tabs.onUpdated
            .removeListener(
              onUpdated
            );

          clearInterval(
            timer
          );
        };

      const done =
        value => {
          if (finished) {
            return;
          }

          finished =
            true;

          cleanup();

          resolve(value);
        };

      const fail =
        error => {
          if (finished) {
            return;
          }

          finished =
            true;

          cleanup();

          reject(error);
        };

      const onUpdated =
        (
          id,
          info,
          tab
        ) => {
          if (
            id === tabId &&
            info.status ===
              'complete'
          ) {
            done(tab);
          }
        };

      chrome.tabs.onUpdated
        .addListener(
          onUpdated
        );

      const timer =
        setInterval(
          async () => {
            try {
              const tab =
                await chrome.tabs.get(
                  tabId
                );

              if (
                tab.status ===
                'complete'
              ) {
                done(tab);
                return;
              }
            } catch (e) {}

            if (
              Date.now() >
              end
            ) {
              fail(
                new Error(
                  'Facebook tải quá lâu.'
                )
              );
            }
          },
          500
        );
    }
  );
}

async function postGroup(
  c,
  url,
  content,
  images,
  job,
  groupIndex
) {
  const groupUrl = new URL(url);
  if (groupUrl.protocol !== 'https:' || !['facebook.com', 'www.facebook.com', 'm.facebook.com'].includes(groupUrl.hostname)
      || groupUrl.username || groupUrl.password || !/^\/groups\/[^/]+\/?$/.test(groupUrl.pathname)) {
    return {ok: false, code: 'invalid_group', error: 'URL Group không hợp lệ; đã chặn điều hướng.'};
  }
  /*
   * QUAN TRỌNG:
   * active:true
   *
   * Bản cũ của bạn dùng active:false.
   */
  const tab =
    await chrome.tabs.create({
      url,
      active: true
    });
  currentExecutionTab = tab.id;
  // Cookies are read from the regular profile. Incognito has a separate store
  // and must never be treated as the same Facebook session.
  if (tab.incognito) {
    return {ok: false, code: 'requires_review', error: 'Không hỗ trợ chạy bằng cửa sổ ẩn danh; hãy dùng Chrome profile đã gắn account.'};
  }

  try {
    await waitTab(
      tab.id
    );

    /*
     * Facebook là SPA.
     * Tab complete chưa có nghĩa DOM đã render.
     */
    await sleep(5000);

    const latest =
      await chrome.tabs.get(
        tab.id
      );

    const u =
      (
        latest.url ||
        ''
      ).toLowerCase();

    if (
      u.includes(
        '/checkpoint'
      )
    ) {
      await chrome.tabs.update(
        tab.id,
        {
          active:
            true
        }
      );

      return {
        ok: false,

        code:
          'checkpoint',

        error:
          'Facebook yêu cầu checkpoint.'
      };
    }

    if (
      u.includes(
        '/login'
      )
    ) {
      await chrome.tabs.update(
        tab.id,
        {
          active:
            true
        }
      );

      return {
        ok: false,

        code:
          'login',

        error:
          'Facebook chưa đăng nhập.'
      };
    }

    /*
     * Đảm bảo tab vẫn đang active.
     */
    await chrome.tabs.update(
      tab.id,
      {
        active:
          true
      }
    );

    await sleep(1500);

    let res = null;
    let lastError = null;

    /*
     * Thử kết nối content script nhiều lần.
     */
    for (
      let i = 0;
      i < 15;
      i++
    ) {
      try {
        await verifyJobSession(c, job);
        res =
          await chrome.tabs.sendMessage(
            tab.id,
            {
              type:
                'FBPOST_POST',

              content:
                content ||
                '',

              images:
                images || [],
              job_id: job.job_id,
              execution_id: (job.execution_token || job.job_id) + ':' + groupIndex
            }
          );

        if (res) {
          break;
        }
      } catch (e) {
        lastError =
          e;

        // Only a missing receiver proves that the runner has not started.
        // A closed message port can mean Facebook already accepted the post.
        if (!String(e?.message || '').includes('Receiving end does not exist')) {
          return {ok: false, code: 'requires_review', error: 'Kết quả chưa xác định; không tự gửi lại thao tác đăng.'};
        }

        console.log(
          'Chờ facebook_runner.js:',
          i + 1,
          e
        );
      }

      await sleep(
        1000
      );
    }

    if (!res) {
      throw new Error(
        'Không kết nối được script trên Facebook. ' +
        (
          lastError?.message ||
          ''
        )
      );
    }

    if (
      res.code ===
        'login' ||
      res.code ===
        'checkpoint'
    ) {
      await chrome.tabs.update(
        tab.id,
        {
          active:
            true
        }
      );

      return res;
    }

    /*
     * DEBUG:
     * khi lỗi KHÔNG đóng tab.
     */
    if (!res.ok) {
      console.error(
        'FB POST PRO ERROR:',
        res
      );

      await chrome.tabs.update(
        tab.id,
        {
          active:
            true
        }
      );

      return res;
    }

    console.log(
      'FB POST PRO SUCCESS:',
      res
    );

    /*
     * Trong lúc test:
     * thành công cũng giữ tab.
     */
    return res;

  } catch (e) {
    console.error(
      'postGroup error:',
      e
    );

    try {
      await chrome.tabs.update(
        tab.id,
        {
          active:
            true
        }
      );
    } catch (err) {}

    return {
      ok: false,

      error:
        e?.message ||
        String(e)
    };
  }

  /*
   * CỐ Ý KHÔNG CÓ finally chrome.tabs.remove()
   *
   * để tab Facebook không đóng khi lỗi.
   */
}

async function stoppableDelay(
  c,
  seconds,
  nextIndex,
  stats
) {
  while (
    seconds >
    0
  ) {
    const action = await waitForSafeControl(c, stats);
    if (action === 'stop') {
      return false;
    }

    const m =
      Math.floor(
        seconds /
        60
      );

    const s =
      seconds %
      60;

    await report(
      c,
      {
        status:
          'delay',

        message:
          `Chờ ${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')} → Group ${nextIndex}`,

        processed:
          stats.processed,

        success:
          stats.success,

        errors:
          stats.errors
      }
    );

    await sleep(
      1000
    );

    seconds--;
  }

  return true;
}

async function processJob(
  c,
  job
) {
  busy = true;
  currentJobId = String(job?.job_id || '');
  currentJob = job;
  currentOwner = c;
  workerState = 'busy';

  let processed = 0;
  let success = 0;
  let errors = 0;

  try {
    await verifyJobSession(c, job);
    const fb =
      await facebookLoggedIn();

    if (!fb) {
      await chrome.tabs.create({
        url:
          'https://www.facebook.com/',

        active:
          true
      });

      await report(
        c,
        {
          status:
            'needs_facebook_login',

          message:
            'Facebook chưa đăng nhập. Hãy đăng nhập trên Chrome rồi chạy lại.',

          processed,
          success,
          errors
        }
      );

      return;
    }

    const accepted = await report(
      c,
      {
        status:
          'running',

        message:
          'Connector đã nhận chiến dịch. Đang chuẩn bị...',

        processed,
        success,
        errors
      }
    );
    if (!accepted) throw new Error('Backend không xác nhận task; đã dừng trước khi đăng.');

    const images =
      await loadImages(
        c,
        job.images ||
        []
      );

    const groups =
      job.groups ||
      [];

    for (
      let i = 0;
      i < groups.length;
      i++
    ) {
      const action = await waitForSafeControl(c, {
        processed,
        success,
        errors
      });

      if (action === 'stop') {
        await report(
          c,
          {
            status:
              'stopped',

            message:
              'Chiến dịch đã dừng.',

            processed,
            success,
            errors
          }
        );

        return;
      }

      await verifyJobSession(c, job);
      const postingAccepted = await report(
        c,
        {
          status:
            'posting',

          message:
            `Đang đăng Group ${i + 1}/${groups.length}`,

          processed,
          success,
          errors
        }
      );
      if (!postingAccepted) throw new Error('Backend không xác nhận task; đã dừng trước khi đăng.');

      let res;

      try {
        res =
          await postGroup(
            c,
            groups[i],
            job.content ||
              '',
            images,
            job,
            i
          );
      } catch (e) {
        res = {
          ok: false,

          error:
            e?.message ||
            String(e)
        };
      }

      processed++;

      if (res?.ok) {
        success++;

        await report(
          c,
          {
            status:
              'posting',

            event:
              'group_success',

            event_id:
              `${job.job_id}:group:${i}:success`,

            message:
              `Đăng thành công • ${job.campaign_name || 'Chiến dịch'}`,

            group_url:
              groups[i],

            processed,
            success,
            errors
          }
        );
      } else {
        errors++;

        if (res?.code === 'requires_review') {
          await report(c, {status: 'error', message: 'Result uncertain; requires review; automatic replay blocked. ' + (res.error || ''), processed, success, errors});
          return;
        }

        await report(
          c,
          {
            status:
              'posting',

            event:
              'group_error',

            event_id:
              `${job.job_id}:group:${i}:error`,

            message:
              `Lỗi đăng bài • ${job.campaign_name || 'Chiến dịch'}`,

            group_url:
              groups[i],

            detail:
              res?.error ||
              'Lỗi không xác định',

            processed,
            success,
            errors
          }
        );

        if (
          res?.code ===
          'login'
        ) {
          await report(
            c,
            {
              status:
                'needs_facebook_login',

              message:
                'Facebook đã mất phiên đăng nhập.',

              processed,
              success,
              errors
            }
          );

          return;
        }

        if (
          res?.code ===
          'checkpoint'
        ) {
          await report(
            c,
            {
              status:
                'facebook_checkpoint',

              message:
                'Facebook yêu cầu checkpoint/xác minh. Hãy xử lý trên tab vừa mở.',

              processed,
              success,
              errors
            }
          );

          return;
        }
      }

      if (
        i <
        groups.length -
          1
      ) {
        const seconds =
          rnd(
            job.min_delay,
            job.max_delay
          ) *
          60;

        if (
          seconds >
          0
        ) {
          const ok =
            await stoppableDelay(
              c,
              seconds,
              i + 2,
              {
                processed,
                success,
                errors
              }
            );

          if (!ok) {
            await report(
              c,
              {
                status:
                  'stopped',

                message:
                  'Chiến dịch đã dừng.',

                processed,
                success,
                errors
              }
            );

            return;
          }
        }
      }
    }

    const status =
      errors
        ? 'finished_with_errors'
        : 'finished';

    await report(
      c,
      {
        status,

        message:
          errors
            ? `Hoàn tất. Thành công ${success}, lỗi ${errors}.`
            : `Hoàn tất. Đăng thành công ${success}/${groups.length} Group.`,

        processed,
        success,
        errors
      }
    );
  } catch (e) {
    await report(
      c,
      {
        status:
          'error',

        message:
          e?.message ||
          String(e),

        processed,
        success,

        errors:
          errors + 1
      }
    );
  } finally {
    busy =
      false;
    currentJobId = '';
    currentJob = null;
    currentOwner = null;
    currentExecutionTab = null;
    workerState = 'idle';
    lastHeartbeatAt = 0;
  }
}

function poll() {
  if (!pollPromise) pollPromise = pollOnce().finally(() => { pollPromise = null; });
  return pollPromise;
}

async function pollOnce() {
  if (busy) {
    return {
      ok: true,
      busy:
        true
    };
  }

  const c =
    await cfg();

  if (
    !c.serverOrigin ||
    !c.deviceId ||
    !c.token
  ) {
    return {
      ok: false,
      error:
        'Chưa liên kết'
    };
  }

  let hb;

  try {
    hb =
      await heartbeat();
  } catch (e) {
    return {
      ok: false,

      error:
        e?.message ||
        String(e)
    };
  }

  try {
    const r =
      await fetch(
        c.serverOrigin +
          '/api/agent/job',
        {
          headers:
            headers(
              c,
              false
            )
        }
      );

    if (
      r.status ===
      401
    ) {
      await chrome.storage.local.remove([
        'deviceId',
        'token',
        'customerId'
      ]);

      return {
        ok: false,
        error:
          'Liên kết đã hết hiệu lực'
      };
    }

    const d =
      await r.json();

    if (d.has_job) {
      processJob(
        c,
        d.job
      );

      return {
        ok: true,

        job:
          true,

        facebookLoggedIn:
          hb.facebookLoggedIn
      };
    }

    return {
      ok: true,

      job:
        false,

      facebookLoggedIn:
        hb.facebookLoggedIn,

      deviceName:
        'Google Chrome'
    };
  } catch (e) {
    return {
      ok: false,

      error:
        e?.message ||
        String(e)
    };
  }
}

chrome.runtime.onInstalled.addListener(
  () => {
    chrome.alarms.create(
      'fbpost-poll',
      {
        periodInMinutes:
          0.5
      }
    );

    poll();
  }
);

chrome.runtime.onStartup.addListener(
  () => {
    poll();
  }
);

chrome.alarms.onAlarm.addListener(
  a => {
    if (
      a.name ===
      'fbpost-poll'
    ) {
      poll();
    }
  }
);

chrome.runtime.onMessage.addListener(
  (
    msg,
    sender,
    sendResponse
  ) => {
    if (msg?.type === 'VERIFY_EXECUTION') {
      verifyExecutionRequest(msg, sender).then(sendResponse);
      return true;
    }
    if (
      msg?.type ===
      'POLL_NOW'
    ) {
      poll()
        .then(
          sendResponse
        );

      return true;
    }

    if (
      msg?.type ===
      'PAIR_FROM_WEB'
    ) {
      try {
        const senderOrigin = new URL(sender.url || sender.tab?.url || '').origin;
        if (senderOrigin !== new URL(msg.serverOrigin).origin) throw new Error('origin');
      } catch (error) {
        sendResponse({ok: false, error: 'Website origin không hợp lệ.'});
        return;
      }
      pairFromWeb(
        msg.serverOrigin,
        msg.code
      )
        .then(
          sendResponse
        );

      return true;
    }

    if (
      msg?.type ===
      'GET_STATUS'
    ) {
      Promise.all([
        cfg(),
        facebookLoggedIn()
      ])
        .then(
          (
            [
              c,
              fb
            ]
          ) =>
            sendResponse({
              ok:
                !!(
                  c.deviceId &&
                  c.token
                ),

              facebookLoggedIn:
                fb,

              deviceName:
                'Google Chrome'
            })
        );

      return true;
    }
  }
);
