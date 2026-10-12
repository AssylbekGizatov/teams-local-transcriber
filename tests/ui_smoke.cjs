const fs = require("node:fs");
const assert = require("node:assert/strict");
const { JSDOM, VirtualConsole } = require("jsdom");
const ROOT = require("node:path").join(__dirname, "../app/static/");
const token = "test-only-session-token-32-characters";
const requests = [],
  downloads = [],
  clipboard = [];
let calls = 0,
  nextId = 1,
  conflict = false,
  confirmResult = true;
let config = {
  asr_backend: "gigaam",
  model_configured: true,
  teams_enabled: false,
  max_upload_mb: 1,
  bot_name: "Authorized test bot",
};
const meetings = [];
const delays = {};
const pageErrors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => pageErrors.push(error.message));
const dom = new JSDOM(fs.readFileSync(ROOT + "index.html", "utf8"), {
  url: "https://test.local/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const w = dom.window;
const $ = (id) => w.document.getElementById(id);
w.Headers = Headers;
w.Response = Response;
w.confirm = () => confirmResult;
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
};
w.navigator.clipboard = { writeText: async (text) => clipboard.push(text) };
w.URL.createObjectURL = (blob) => {
  downloads.push(blob);
  return "blob:https://test.local/" + downloads.length;
};
w.URL.revokeObjectURL = () => {};
w.document.addEventListener("click", (event) => {
  if (event.target.tagName === "A" && event.target.download)
    event.preventDefault();
});
function response(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
function clone(x) {
  return JSON.parse(JSON.stringify(x));
}
function createMeeting(title, source = "upload", language = "auto") {
  const m = {
    id: String(nextId++),
    title,
    source,
    language,
    status: "queued",
    created_at: "2026-10-12T10:00:00Z",
    updated_at: "2026-10-12T10:00:00Z",
    version: 1,
    segments: [],
  };
  meetings.unshift(m);
  return m;
}
w.fetch = async (path, options = {}) => {
  calls++;
  requests.push({
    path,
    method: options.method || "GET",
    headers: options.headers,
    body: options.body,
  });
  assert(!path.includes(token));
  assert(path.startsWith("/api/"));
  if (options.headers.get("Authorization") !== "Bearer " + token)
    return response({ detail: "Invalid or missing Bearer token" }, 401);
  if (path === "/api/config") return response(config);
  if (path === "/api/meetings" && (!options.method || options.method === "GET"))
    return response({ items: clone(meetings) });
  if (path === "/api/uploads") {
    assert(options.body instanceof w.FormData);
    assert.equal(options.body.get("consent_confirmed"), "true");
    assert.equal(options.body.get("file").name, "test.wav");
    assert.equal(options.body.get("language"), "kk");
    return response(
      createMeeting(options.body.get("title"), "upload", "kk"),
      201,
    );
  }
  if (path === "/api/meetings" && options.method === "POST") {
    const body = JSON.parse(options.body);
    assert(body.consent_confirmed);
    assert.equal(body.language, "ru");
    assert.equal(
      body.meeting_url,
      "https://teams.microsoft.com/l/meetup-join/test",
    );
    return response(createMeeting(body.title, "teams", "ru"), 201);
  }
  const parts = path.split("/");
  const m = meetings.find((x) => x.id === parts[3]);
  assert(m, "Unknown test meeting " + path);
  if (delays[m.id])
    await new Promise((resolve) => setTimeout(resolve, delays[m.id]));
  if (parts[4] === "stop") {
    m.status = "cancelled";
    m.version++;
    return response(clone(m));
  }
  if (parts[4] === "retry") {
    m.status = "queued";
    m.version++;
    return response(clone(m));
  }
  if (parts[4] === "segments") {
    const body = JSON.parse(options.body);
    if (conflict) {
      conflict = false;
      m.version++;
      return response(
        { detail: "The transcript has changed. Reload before saving." },
        409,
      );
    }
    assert.equal(body.version, m.version);
    const segment = m.segments.find((x) => x.id === parts[5]);
    Object.assign(segment, { text: body.text, speaker: body.speaker });
    m.version++;
    return response(clone(m));
  }
  if (parts[4]?.startsWith("export?"))
    return new Response("Ә Ғ Қ Ң Ө Ұ Ү Һ І — Привет! English", {
      headers: {
        "Content-Disposition": 'attachment; filename="transcript.txt"',
      },
    });
  return response(clone(m));
};
w.eval(fs.readFileSync(ROOT + "app.js", "utf8"));
const event = (type) => new w.Event(type, { bubbles: true, cancelable: true });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(predicate, message) {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await sleep(5);
  }
  throw new Error(message);
}
function submit(id) {
  $(id).dispatchEvent(event("submit"));
}
function click(id) {
  $(id).click();
}
function findMeeting(id) {
  return [...w.document.querySelectorAll(".history-item")].find((el) =>
    el.textContent.includes(meetings.find((x) => x.id === id).title),
  );
}
function editFirst() {
  w.document.querySelector(".edit-button").click();
}
(async () => {
  assert($("start-button").disabled);
  assert.equal(calls, 0);
  assert(!$("model-warning").hidden);
  click("access-button");
  assert($("access-dialog").open);
  $("admin-token").value = "wrong";
  submit("access-form");
  await waitFor(() => !$("access-error").hidden, "invalid-token error");
  click("close-access");
  assert(!$("access-dialog").open);
  click("access-button");
  $("admin-token").value = token;
  submit("access-form");
  await waitFor(
    () => $("access-label").textContent === "Администратор",
    "login",
  );
  await sleep(10);
  assert.equal(w.sessionStorage.getItem("soz_admin_token"), token);
  assert.equal(w.localStorage.length, 0);
  assert.equal($("admin-token").value, "");
  assert(!$("start-button").disabled);
  $("auto-refresh").checked = false;
  click("teams-tab");
  assert($("start-button").disabled);
  assert($("teams-availability").textContent.includes("отключён"));
  $("teams-tab").dispatchEvent(
    new w.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }),
  );
  assert.equal(w.document.activeElement.id, "upload-tab");
  assert.equal($("upload-tab").getAttribute("aria-selected"), "true");
  const file = new w.File(["RIFF-audio-fixture"], "test.wav", {
    type: "audio/wav",
  });
  Object.defineProperty($("recording-file"), "files", {
    value: [file],
    configurable: true,
  });
  $("recording-file").dispatchEvent(event("change"));
  $("meeting-title").value = "Загруженная проверка";
  $("meeting-language").value = "kk";
  submit("create-form");
  assert($("form-error").textContent.includes("Подтвердите"));
  $("consent").checked = true;
  submit("create-form");
  submit("create-form");
  await waitFor(
    () => $("selected-title").textContent === "Загруженная проверка",
    "upload selection",
  );
  assert.equal(requests.filter((x) => x.path === "/api/uploads").length, 1);
  assert($("selected-status").textContent.includes("Ожидает обработки"));
  assert(!$("consent").checked);
  click("stop-button");
  await waitFor(() => $("selected-status").textContent === "Отменено", "stop");
  click("retry-button");
  await waitFor(
    () => $("selected-status").textContent === "Ожидает обработки",
    "retry",
  );
  const m = createMeeting("Жиналыс <img src=x onerror=alert(1)>");
  m.status = "completed";
  m.version = 5;
  m.segments = [
    {
      id: "s1",
      start: 0,
      end: 30,
      text: "Сәлем! Русский. English. <img src=x onerror=alert(1)>",
      speaker: "Әлия",
      final: false,
    },
  ];
  click("refresh");
  await waitFor(() => findMeeting(m.id), "history reload");
  findMeeting(m.id).click();
  await waitFor(
    () => w.document.querySelector(".segment-text"),
    "meeting select",
  );
  assert.equal(w.document.querySelectorAll("#segments img").length, 0);
  assert.equal(w.document.querySelectorAll("#selected-title img").length, 0);
  assert(
    w.document
      .querySelector(".segment-flag.preliminary")
      .textContent.includes("Предварительно"),
  );
  editFirst();
  $("edit-text").value = "Әліпби: Ә Ғ Қ Ң Ө Ұ Ү Һ І";
  $("edit-speaker").value = "Әлия";
  submitForm(".segment-editor");
  await waitFor(() => !w.document.querySelector(".segment-editor"), "save");
  assert.equal(m.segments[0].text, "Әліпби: Ә Ғ Қ Ң Ө Ұ Ү Һ І");
  editFirst();
  $("edit-text").value = "Несохранённые изменения";
  conflict = true;
  submitForm(".segment-editor");
  await waitFor(
    () =>
      w.document
        .querySelector(".segment-editor .form-error")
        ?.textContent.includes("Кто-то"),
    "conflict",
  );
  assert.equal($("edit-text").value, "Несохранённые изменения");
  confirmResult = false;
  findMeeting("1").click();
  assert.equal($("edit-text").value, "Несохранённые изменения");
  confirmResult = true;
  [...w.document.querySelectorAll(".editor-actions button")]
    .find((b) => b.textContent === "Отмена")
    .click();
  assert(!w.document.querySelector(".segment-editor"));
  click("copy-button");
  await sleep(5);
  assert(clipboard[0].includes("Ә Ғ Қ Ң Ө Ұ Ү Һ І"));
  for (const format of ["txt", "md", "docx", "pdf"]) {
    $("export-format").value = format;
    click("export-button");
    await waitFor(() => !$("export-button").disabled, "export " + format);
  }
  assert.equal(downloads.length, 4);
  assert.equal(requests.filter((x) => x.path.includes("/export?")).length, 4);
  $("history-search").value = "notfound";
  $("history-search").dispatchEvent(event("input"));
  assert($("history-list").textContent.includes("Ничего не найдено"));
  $("history-search").value = "";
  $("history-search").dispatchEvent(event("input"));
  // Slow previous selection must not overwrite newer navigation.
  delays["1"] = 70;
  findMeeting("1").click();
  findMeeting(m.id).click();
  await sleep(100);
  assert.equal($("selected-title").textContent, m.title);
  delete delays["1"];
  config.teams_enabled = true;
  click("refresh");
  await sleep(15);
  click("teams-tab");
  assert(!$("start-button").disabled);
  $("meeting-title").value = "Разрешённая Teams встреча";
  $("meeting-url").value = "https://teams.microsoft.com/l/meetup-join/test";
  $("meeting-language").value = "ru";
  $("consent").checked = true;
  submit("create-form");
  await waitFor(
    () => $("selected-title").textContent === "Разрешённая Teams встреча",
    "Teams creation",
  );
  assert($("selected-status").textContent.includes("Ожидает подключения"));
  assert($("retry-button").hidden);
  click("access-button");
  click("logout-button");
  assert.equal(w.sessionStorage.getItem("soz_admin_token"), null);
  assert($("transcript-content").hidden);
  assert($("start-button").disabled);
  assert.equal($("nav-count").textContent, "0");
  assert.equal(pageErrors.length, 0, pageErrors.join("; "));
  console.log(
    JSON.stringify(
      {
        passed: [
          "login and rejected token",
          "dialog close/reopen",
          "session-only token storage",
          "Teams availability gate",
          "keyboard source tabs",
          "explicit consent",
          "multipart upload payload",
          "double-submit prevention",
          "source-specific statuses",
          "stop/retry",
          "history selection/search",
          "XSS inert rendering",
          "provisional segment label",
          "Unicode edit",
          "optimistic conflict preserves draft",
          "unsaved-edit navigation cancellation",
          "copy",
          "four authorized exports",
          "selection race protection",
          "Teams create payload",
          "logout",
        ],
        requestCount: requests.length,
        jsdomErrors: pageErrors,
      },
      null,
      2,
    ),
  );
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});
function submitForm(selector) {
  w.document.querySelector(selector).dispatchEvent(event("submit"));
}
