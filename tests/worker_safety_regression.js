/* Isolated V8 mocks only: no browser, Facebook navigation, or publication. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const path = require('node:path');

async function main() {
  let identity = '81001';
  const requests = [];
  let tabCreates = 0;
  let incognitoTab = false;
  let sends = 0;
  let sendFailure = '';
  const configuration = {deviceId: 'ext_test', token: 'test-only', serverOrigin: 'http://localhost', customerId: 'test'};
  const context = vm.createContext({
    crypto: webcrypto, TextEncoder, Uint8Array, URL,
    console: {log() {}, warn() {}, error() {}},
    setTimeout: callback => {callback();}, clearTimeout() {},
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      return {ok: true, status: 200, json: async () => url.endsWith('/job') ? {has_job: false} : {ok: true}};
    },
    chrome: {
      runtime: {getManifest: () => ({version: 'test'}), onInstalled: {addListener() {}}, onStartup: {addListener() {}}, onMessage: {addListener() {}}},
      storage: {local: {get: async () => configuration, set: async () => {}, remove: async () => {}}},
      cookies: {get: async () => ({value: identity})},
      alarms: {create() {}, onAlarm: {addListener() {}}},
      tabs: {
        create: async () => {tabCreates++; return {id: 10, incognito: incognitoTab};},
        get: async () => ({id: 10, status: 'complete', url: 'https://www.facebook.com/groups/test'}),
        update: async () => ({}),
        sendMessage: async () => {sends++; if (sendFailure) throw Error(sendFailure); return {ok: true};},
        onUpdated: {addListener() {}, removeListener() {}}
      }
    }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../extension/service_worker.js'), 'utf8'), context);
  vm.runInContext('waitTab = async () => {};', context);
  const fingerprint = await context.facebookSessionFingerprint();
  assert.match(fingerprint, /^[a-f0-9]{64}$/);
  const job = {
    job_id: 'job_test', engine_task_id: 'task_test', account_id: 'a', device_id: 'ext_test',
    browser_profile_id: 'chrome-profile:ext_test', session_context: 'chrome-profile:ext_test',
    expected_session_fingerprint: fingerprint, execution_token: 'attempt1', groups: []
  };
  await context.verifyJobSession(configuration, job);
  identity = '81002';
  await assert.rejects(context.verifyJobSession(configuration, job), /session/);
  identity = '81001';
  await assert.rejects(context.verifyJobSession(configuration, {...job, device_id: 'other'}), /profile/);
  await assert.rejects(context.verifyJobSession(configuration, {...job, expected_session_fingerprint: ''}), /xác minh/);
  await context.heartbeat(true);
  const payload = JSON.parse(requests.at(-1).options.body);
  assert.equal(payload.facebook_session_fingerprint, fingerprint);
  assert.equal(payload.session_verification_version, 1);
  assert.equal(payload.browser_profile_id, job.browser_profile_id);
  assert.equal(JSON.stringify(payload).includes(identity), false, 'raw cookie identity leaked');
  console.log('PASS worker identity: local digest, exact account/profile binding, raw cookie stays local');

  const requestCount = requests.length;
  const hijack = await context.pairFromWeb('https://other.onrender.com', 'ABC123');
  assert.equal(hijack.ok, false);
  assert.equal(requests.length, requestCount, 'a different website cannot replace an existing pairing');
  const priorDevice = configuration.deviceId;
  configuration.deviceId = '';
  assert.equal((await context.pairFromWeb(configuration.serverOrigin, 'ABC123')).ok, false);
  configuration.deviceId = priorDevice;
  assert.equal((await context.postGroup(configuration, 'https://attacker.test/groups/test', '', [], job, 0)).code, 'invalid_group');
  assert.equal(tabCreates, 0);
  console.log('PASS connector origin: cross-site re-pair and non-Facebook navigation blocked');

  await Promise.all([context.poll(), context.poll(), context.poll()]);
  assert.equal(requests.filter(item => item.url.endsWith('/api/agent/job')).length, 1);
  console.log('PASS worker concurrent poll: one claim request');

  incognitoTab = true;
  const incognito = await context.postGroup(configuration, 'https://www.facebook.com/groups/test', '', [], job, 0);
  assert.equal(incognito.code, 'requires_review');
  assert.equal(sends, 0, 'regular-profile identity must not authorize an incognito runner');
  incognitoTab = false;
  console.log('PASS incognito isolation: regular cookie identity cannot authorize a different cookie store');

  sendFailure = 'The message port closed before a response was received.';
  const uncertain = await context.postGroup(configuration, 'https://www.facebook.com/groups/test', '', [], job, 0);
  assert.equal(uncertain.code, 'requires_review');
  assert.equal(sends, 1);
  sendFailure = 'Could not establish connection. Receiving end does not exist.';
  sends = 0;
  await context.postGroup(configuration, 'https://www.facebook.com/groups/test', '', [], job, 0);
  assert.equal(sends, 15, 'only a missing receiver is safe to retry');
  console.log('PASS delivery uncertainty: a lost runner reply is never redispatched');

  context.fetch = async () => ({ok: false, status: 409, json: async () => ({error: 'stale'})});
  const createsBefore = tabCreates;
  await context.processJob(configuration, {...job, groups: ['https://www.facebook.com/groups/test']});
  assert.equal(tabCreates, createsBefore, 'rejected backend status must stop before opening Facebook');
  console.log('PASS rejected backend claim/status: no automation starts');
  for (const status of [401, 403]) {
    let controlRequests = 0;
    context.fetch = async () => {controlRequests++; return {ok: false, status};};
    await assert.rejects(context.waitForSafeControl(configuration, {}), /hết hạn|thu hồi/);
    assert.equal(controlRequests, 1, 'invalid/revoked token must not spin in a retry loop');
  }
  console.log('PASS revoked/expired authentication: control rejects once and safely stops');

  let runnerCalls = 0;
  const stored = new Map();
  const notices = [];
  const runner = vm.createContext({
    console, setTimeout, clearTimeout,
    sessionStorage: {getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value)},
    document: {querySelectorAll: () => notices},
    window: {getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: '1'})},
    chrome: {runtime: {sendMessage: async () => ({ok: true}), onMessage: {addListener() {}}}}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../extension/facebook_runner.js'), 'utf8'), runner);
  runner.postCurrentGroup = async () => {runnerCalls++; return {ok: true};};
  const payloadRunner = {job_id: 'job_test', execution_id: 'attempt1:0'};
  await Promise.all([runner.runPostOnce(payloadRunner), runner.runPostOnce(payloadRunner)]);
  assert.equal(runnerCalls, 1);
  await runner.runPostOnce(payloadRunner);
  assert.equal(runnerCalls, 1);
  stored.set('fbpost-execution:uncertain', JSON.stringify({phase: 'publish_attempted'}));
  assert.equal((await runner.runPostOnce({job_id: 'job_test', execution_id: 'uncertain'})).code, 'requires_review');
  runner.postCurrentGroup = async (_payload, state) => {state.publishAttempted = true; throw Error('lost confirmation');};
  assert.equal((await runner.runPostOnce({job_id: 'job_test', execution_id: 'attempt2:0'})).code, 'requires_review');
  assert.equal(runner.publicationConfirmed(new Map()), false);
  const notice = {innerText: 'Your post has been published', getBoundingClientRect: () => ({width: 10, height: 10})};
  notices.push(notice);
  assert.equal(runner.publicationConfirmed(new Map([[notice, notice.innerText]])), false, 'stale notice cannot confirm a new post');
  assert.equal(runner.publicationConfirmed(new Map()), true);
  console.log('PASS runner: duplicate delivery blocked; uncertain clicks require review; stale/missing notices are not success');
}

main().catch(error => {console.error(error); process.exitCode = 1;});
