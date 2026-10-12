// Mock browser lifecycle tests, not a live microphone/Colab integration test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const notebook = JSON.parse(fs.readFileSync(path.join(__dirname, '../notebooks/gigaam_microphone_colab.ipynb')));
const source = notebook.cells.map(c => c.source.join('')).join('\n');
const script = source.match(/RECORDER_JS = r"""([\s\S]*?)"""/)[1];

function fixture(mode = 'normal') {
  const elements = [], timers = new Map();
  let id = 0, stopped = 0, resolvePermission;
  const track = {stop: () => { stopped++; }};
  const stream = {getTracks: () => [track]};
  class Recorder {
    static isTypeSupported() { return true; }
    constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
    start() { this.state = 'recording'; }
    stop() {
      if (this.state === 'inactive') throw Error('already inactive');
      this.state = 'inactive';
      queueMicrotask(() => {
        this.ondataavailable({data: {size: mode === 'oversize' ? 6 * 1024 * 1024 : 3}});
        this.onstop();
      });
    }
  }
  const context = {
    document: {createElement: tag => {
      const el = {tag, append() {}, style: {}}; elements.push(el); return el;
    }, body: {appendChild() {}}},
    navigator: {mediaDevices: {getUserMedia: () => {
      if (mode === 'denied') return Promise.reject(Object.assign(Error('denied'), {name: 'NotAllowedError'}));
      if (mode === 'pending') return new Promise(r => {resolvePermission = r;});
      return Promise.resolve(stream);
    }}},
    MediaRecorder: Recorder,
    Blob: class { constructor(parts) {this.size = parts.reduce((sum, p) => sum + p.size, 0);} },
    FileReader: class {readAsDataURL() {this.result = 'data:audio/webm;base64,AAAA'; this.onload();}},
    setTimeout: (fn, ms) => {timers.set(++id, {fn, ms}); return id;},
    clearTimeout: id => timers.delete(id),
    window: {MediaRecorder: Recorder, addEventListener() {}, removeEventListener() {}},
  };
  const promise = vm.runInNewContext(script, context);
  // Attach a rejection handler immediately so deliberately rejected fixtures stay handled.
  promise.catch(() => {});
  return {promise, elements, timers, start: () => elements[1].onclick(), stop: () => elements[2].onclick(),
    stopped: () => stopped, grant: () => resolvePermission(stream)};
}
(async () => {
  const normal = fixture(); await normal.start(); normal.stop();
  assert.equal((await normal.promise).bytes, 3); assert.ok(normal.stopped() > 0);
  assert.equal(normal.timers.size, 0);
  const denied = fixture('denied'); await denied.start();
  await assert.rejects(denied.promise, /разрешения/); assert.equal(denied.timers.size, 0);
  const cancelled = fixture('pending'); const starting = cancelled.start(); cancelled.stop();
  await assert.rejects(cancelled.promise, /отменена/);
  cancelled.grant(); await starting; assert.ok(cancelled.stopped() > 0);
  const large = fixture('oversize'); await large.start(); large.stop();
  await assert.rejects(large.promise, /5 МиБ/); assert.ok(large.stopped() > 0);
  const auto = fixture(); await auto.start();
  const autoStop = [...auto.timers.values()].find(t => t.ms === 30000); assert.ok(autoStop);
  autoStop.fn(); assert.equal((await auto.promise).bytes, 3); assert.ok(auto.stopped() > 0);
  const idle = fixture(); [...idle.timers.values()][0].fn();
  await assert.rejects(idle.promise, /Истекло/);
  console.log('Colab recorder: success, denied, late permission cancellation, size bound, auto-stop, idle timeout passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
