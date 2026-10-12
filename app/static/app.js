(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const TOKEN_KEY = "soz_admin_token";
  const ACTIVE = new Set([
    "queued",
    "joining",
    "waiting_room",
    "recording",
    "transcribing",
    "stopping",
  ]);
  const LANGUAGE = {
    auto: "Автоопределение",
    kk: "Қазақша",
    ru: "Русский",
    en: "English",
  };
  const STATUS = {
    queued: "В очереди",
    joining: "Подключение",
    waiting_room: "В зале ожидания",
    recording: "Идёт запись",
    transcribing: "Распознавание",
    stopping: "Остановка",
    completed: "Готово",
    failed: "Ошибка",
    cancelled: "Отменено",
    needs_review: "Нужна проверка",
  };
  const icons = {
    file: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h6"/></svg>',
    teams:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="8" width="12" height="12" rx="2"/><path d="M6 12h4M8 12v5M17 4a2 2 0 1 0 0 .1M16 10h4v7a3 3 0 0 1-4 2"/></svg>',
    edit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16 11-11 4 4L8 20H4v-4ZM13 7l4 4"/></svg>',
  };
  let storedToken = "";
  try {
    storedToken = sessionStorage.getItem(TOKEN_KEY) || "";
  } catch (_) {
    /* Private browsing may deny storage. */
  }
  const state = {
    token: storedToken,
    config: null,
    meetings: [],
    selectedId: null,
    meeting: null,
    source: "upload",
    file: null,
    editing: null,
    refreshBusy: false,
    submitting: false,
    connecting: false,
    selectionEpoch: 0,
    authEpoch: 0,
    toastTimer: null,
  };

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function showError(id, message) {
    $(id).textContent = message || "";
    $(id).hidden = !message;
  }
  function toast(message, error = false) {
    clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").classList.toggle("error", error);
    $("toast").hidden = false;
    state.toastTimer = setTimeout(
      () => {
        $("toast").hidden = true;
      },
      error ? 8000 : 4500,
    );
  }
  function dateTime(value, compact = false) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Дата не указана";
    return new Intl.DateTimeFormat(
      "ru-RU",
      compact
        ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
        : {
            day: "numeric",
            month: "long",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          },
    ).format(date);
  }
  function time(value) {
    let secs = Number(value);
    secs = Number.isFinite(secs) ? Math.max(0, Math.floor(secs)) : 0;
    const hh = Math.floor(secs / 3600);
    const mm = Math.floor((secs % 3600) / 60);
    return [hh, mm, secs % 60]
      .map((part) => String(part).padStart(2, "0"))
      .join(":");
  }
  function sourceName(source) {
    return source === "teams" ? "Microsoft Teams" : "Загруженная запись";
  }
  function statusLabel(meeting) {
    if (meeting.source === "upload" && meeting.status === "queued")
      return "Ожидает обработки";
    if (meeting.source === "teams" && meeting.status === "queued")
      return "Ожидает подключения";
    return STATUS[meeting.status] || "Неизвестный статус";
  }
  function errorMessage(data, fallback) {
    if (typeof data === "string") return data;
    if (Array.isArray(data))
      return data.map((entry) => entry.msg || "Некорректные данные").join("; ");
    if (data && typeof data === "object")
      return data.message || data.error || fallback;
    return fallback;
  }
  async function api(path, options = {}) {
    if (!state.token)
      throw Object.assign(
        new Error("Сначала подключитесь с токеном администратора."),
        { status: 401 },
      );
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${state.token}`);
    if (options.body && !(options.body instanceof FormData))
      headers.set("Content-Type", "application/json");
    let response;
    try {
      response = await fetch(path, {
        ...options,
        headers,
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new Error(
        "Не удалось связаться с сервером. Проверьте подключение и попробуйте снова.",
      );
    }
    if (!response.ok) {
      let data;
      try {
        data = await response.json();
      } catch (_) {
        data = null;
      }
      const fallback =
        response.status === 401
          ? "Токен не принят. Проверьте его и подключитесь снова."
          : response.status === 403
            ? "Доступ запрещён. Проверьте разрешения сервера."
            : response.status === 413
              ? "Файл слишком большой. Выберите запись меньшего размера."
              : response.status === 409
                ? "Транскрипция уже изменилась. Загрузите актуальную версию."
                : `Сервер вернул ошибку ${response.status}. Попробуйте ещё раз.`;
      throw Object.assign(
        new Error(errorMessage(data?.detail ?? data, fallback)),
        { status: response.status },
      );
    }
    return response;
  }
  async function json(path, options = {}) {
    const response = await api(path, options);
    return response.status === 204 ? null : response.json();
  }

  function renderConfig() {
    const config = state.config;
    const connected = Boolean(config && state.token);
    $("access-label").textContent = connected
      ? "Администратор"
      : "Подключиться";
    $("backend-dot").classList.toggle(
      "ready",
      connected && config.model_configured,
    );
    $("backend-dot").classList.toggle(
      "warning",
      connected && !config.model_configured,
    );
    $("backend-title").textContent = !connected
      ? "Ожидаем подключение"
      : config.model_configured
        ? "ASR настроен"
        : "ASR не настроен";
    $("backend-subtitle").textContent = connected
      ? `${config.asr_backend || "ASR"} · доступ по токену`
      : "Защищённое рабочее пространство";
    $("teams-availability").textContent = !connected
      ? "Подключитесь к серверу, чтобы проверить доступность Teams."
      : config.teams_enabled
        ? `Teams включён на сервере. Имя бота: ${config.bot_name || "не указано"}. Подключение зависит от политики встречи.`
        : "Teams отключён на сервере. Загрузка готовой записи доступна после настройки ASR.";
    if (config?.max_upload_mb)
      $("file-hint").textContent = state.file
        ? formatBytes(state.file.size)
        : `или выберите файл · до ${config.max_upload_mb} МБ`;
    $("model-warning").hidden = connected && Boolean(config.model_configured);
    $("model-warning").textContent = !connected
      ? "Для начала нажмите «Подключиться» вверху и введите токен администратора."
      : "Модель ASR не настроена. Администратору нужно настроить сервер перед обработкой.";
    $("logout-button").hidden = !state.token;
    renderStartButton();
  }
  function renderStartButton() {
    $("start-button").disabled =
      state.submitting ||
      !state.token ||
      !state.config?.model_configured ||
      (state.source === "teams" && !state.config?.teams_enabled);
    $("start-label").textContent = state.submitting
      ? state.source === "upload"
        ? "Загружаем запись…"
        : "Создаём подключение…"
      : state.source === "teams"
        ? "Подключиться к встрече"
        : "Создать транскрипцию";
  }
  function changeSource(source) {
    if (state.submitting) return;
    state.source = source;
    for (const name of ["upload", "teams"]) {
      const active = name === source;
      $(`${name}-tab`).classList.toggle("selected", active);
      $(`${name}-tab`).setAttribute("aria-selected", String(active));
      $(`${name}-tab`).tabIndex = active ? 0 : -1;
      $(`${name}-fields`).hidden = !active;
    }
    $("meeting-url").required = source === "teams";
    $("recording-file").required = source === "upload" && !state.file;
    showError("form-error", "");
    renderStartButton();
  }
  function formatBytes(value) {
    return value >= 1024 * 1024
      ? `${(value / 1024 / 1024).toFixed(1)} МБ`
      : `${Math.max(1, Math.round(value / 1024))} КБ`;
  }
  function setFile(file) {
    state.file = file || null;
    $("file-label").textContent = file ? file.name : "Перетащите запись сюда";
    $("file-hint").textContent = file
      ? `${formatBytes(file.size)} · нажмите, чтобы заменить`
      : state.config?.max_upload_mb
        ? `или выберите файл · до ${state.config.max_upload_mb} МБ`
        : "или выберите файл на компьютере";
    $("recording-file").required = state.source === "upload" && !file;
    if (
      file &&
      state.config?.max_upload_mb &&
      file.size > state.config.max_upload_mb * 1024 * 1024
    )
      showError(
        "form-error",
        `Максимальный размер записи: ${state.config.max_upload_mb} МБ.`,
      );
    else showError("form-error", "");
  }
  function renderHistory() {
    const search = $("history-search").value.toLocaleLowerCase("ru").trim();
    const meetings = state.meetings.filter((meeting) =>
      String(meeting.title || "")
        .toLocaleLowerCase("ru")
        .includes(search),
    );
    const list = $("history-list");
    const focusedId = wFocusedMeeting();
    const scrollTop = list.scrollTop;
    list.replaceChildren();
    $("nav-count").textContent = String(state.meetings.length);
    $("history-count").textContent = String(state.meetings.length);
    if (!meetings.length) {
      const empty = node("div", "history-empty");
      empty.append(
        node("p", "", search ? "Ничего не найдено" : "Пока ни одной встречи"),
        node(
          "span",
          "",
          search ? "Попробуйте другое название" : "Добавьте первую запись выше",
        ),
      );
      list.append(empty);
      return;
    }
    for (const meeting of meetings) {
      const button = node(
        "button",
        `history-item${meeting.id === state.selectedId ? " selected" : ""}`,
      );
      button.type = "button";
      button.dataset.meetingId = meeting.id;
      button.setAttribute(
        "aria-pressed",
        String(meeting.id === state.selectedId),
      );
      const icon = node("span", "history-icon");
      icon.innerHTML = meeting.source === "teams" ? icons.teams : icons.file;
      const body = node("span", "history-item-content");
      const meta = node("span", "history-item-meta");
      meta.append(
        node("span", "", dateTime(meeting.created_at, true)),
        node(
          "span",
          `status-pill ${knownStatus(meeting.status)}`,
          statusLabel(meeting),
        ),
      );
      body.append(
        node("span", "history-item-title", meeting.title || "Без названия"),
        meta,
      );
      button.append(icon, body);
      button.addEventListener("click", () => selectMeeting(meeting.id));
      list.append(button);
      if (focusedId === String(meeting.id))
        button.focus({ preventScroll: true });
    }
    list.scrollTop = scrollTop;
  }
  function wFocusedMeeting() {
    return document.activeElement?.closest(".history-item")?.dataset.meetingId;
  }
  function knownStatus(status) {
    return Object.hasOwn(STATUS, status) ? status : "unknown";
  }
  function statusNote(meeting) {
    const notes = {
      queued:
        meeting.source === "teams"
          ? "Ожидаем запуск подключения к Teams."
          : "Запись загружена. Ожидаем запуск распознавания.",
      joining: "Бот подключается к Teams. Запись ещё не началась.",
      waiting_room:
        "Бот ожидает в зале ожидания. Организатор должен допустить его на встречу.",
      recording:
        "Идёт запись разрешённой встречи. Текст появится после обработки аудио.",
      transcribing:
        "ASR обрабатывает запись. Длительность зависит от модели и длины разговора.",
      stopping: "Завершаем работу. Дождитесь обновления статуса.",
      needs_review:
        "Обработка требует проверки. Просмотрите результат и сообщение сервера.",
      cancelled:
        "Задача остановлена. Доступные сохранённые фрагменты остаются ниже.",
      failed:
        meeting.source === "teams"
          ? "Проверьте состояние бота в Teams или Vexa. Создавайте новое подключение только после подтверждения, что прежний бот остановлен."
          : "",
    };
    return notes[meeting.status] || "";
  }
  function renderMeeting() {
    const meeting = state.meeting;
    $("transcript-empty").hidden = Boolean(meeting);
    $("transcript-content").hidden = !meeting;
    if (!meeting) return;
    $("selected-title").textContent = meeting.title || "Без названия";
    $("selected-status").textContent = statusLabel(meeting);
    $("selected-status").className =
      `status-pill ${knownStatus(meeting.status)}`;
    $("selected-meta").textContent =
      `${sourceName(meeting.source)} · ${dateTime(meeting.created_at)} · ${LANGUAGE[meeting.language] || "Язык не указан"}`;
    showError("meeting-status-note", statusNote(meeting));
    showError("meeting-error", meeting.error ? String(meeting.error) : "");
    $("stop-button").hidden =
      !ACTIVE.has(meeting.status) || meeting.status === "stopping";
    $("stop-button").textContent =
      meeting.source === "teams" && meeting.status === "recording"
        ? "Завершить запись"
        : "Остановить задачу";
    $("retry-button").hidden =
      meeting.source !== "upload" ||
      !["failed", "cancelled"].includes(meeting.status);
    $("last-updated").textContent =
      `Обновлено: ${dateTime(meeting.updated_at || meeting.created_at, true)}`;
    const segments = meeting.segments || [];
    $("segment-count").textContent =
      `${segments.length} ${plural(segments.length, "фрагмент", "фрагмента", "фрагментов")}`;
    const duration = segments.reduce(
      (max, segment) => Math.max(max, Number(segment.end) || 0),
      0,
    );
    $("duration").textContent = segments.length ? `· ${time(duration)}` : "";
    $("copy-button").disabled = !segments.length;
    $("export-button").disabled = !segments.length;
    $("export-format").disabled = !segments.length;
    renderSegments();
  }
  function plural(count, one, few, many) {
    return count % 10 === 1 && count % 100 !== 11
      ? one
      : count % 10 >= 2 &&
          count % 10 <= 4 &&
          (count % 100 < 12 || count % 100 > 14)
        ? few
        : many;
  }
  function renderSegments() {
    const container = $("segments");
    container.replaceChildren();
    const segments = state.meeting?.segments || [];
    if (!segments.length) {
      const empty = node("div", "empty-segments");
      if (ACTIVE.has(state.meeting?.status))
        empty.append(node("div", "processing-ring"));
      empty.append(
        node(
          "p",
          "",
          ACTIVE.has(state.meeting?.status)
            ? "Текст ещё обрабатывается. Можно оставить эту страницу открытой."
            : "В этой встрече пока нет распознанного текста.",
        ),
      );
      container.append(empty);
      return;
    }
    for (const [index, segment] of segments.entries()) {
      const article = node("article", "segment");
      article.dataset.segmentId = segment.id;
      const heading = node("div", "segment-heading");
      const speaker = segment.speaker || "Спикер не указан";
      heading.append(
        node(
          "span",
          "speaker-avatar",
          segment.speaker
            ? String(segment.speaker).trim().slice(0, 2).toUpperCase()
            : "?",
        ),
        node("span", "segment-speaker", speaker),
      );
      if (segment.final === false || segment.final === 0)
        heading.append(
          node("span", "segment-flag preliminary", "Предварительно"),
        );
      if (segment.edited)
        heading.append(node("span", "segment-flag", "Изменено"));
      heading.append(
        node(
          "span",
          "segment-time",
          `${time(segment.start)} – ${time(segment.end)}`,
        ),
      );
      const edit = node("button", "edit-button");
      edit.type = "button";
      edit.innerHTML = icons.edit;
      edit.setAttribute("aria-label", `Редактировать фрагмент ${index + 1}`);
      edit.title = "Редактировать фрагмент";
      edit.disabled = ACTIVE.has(state.meeting.status);
      edit.addEventListener("click", () => beginEdit(segment.id));
      heading.append(edit);
      article.append(heading, node("p", "segment-text", segment.text || ""));
      container.append(article);
    }
  }
  async function selectMeeting(id, { force = false } = {}) {
    if (state.editing?.saving) {
      toast("Дождитесь сохранения фрагмента.");
      return;
    }
    if (!force && id === state.selectedId && state.meeting) return;
    if (
      state.editing &&
      !confirm(
        "Есть несохранённый текст. Отменить изменения и открыть встречу?",
      )
    )
      return;
    state.editing = null;
    const epoch = ++state.selectionEpoch;
    state.selectedId = id;
    state.meeting = null;
    renderHistory();
    renderMeeting();
    showError("global-error", "");
    try {
      const meeting = await json(`/api/meetings/${encodeURIComponent(id)}`);
      if (epoch !== state.selectionEpoch || id !== state.selectedId) return;
      state.meeting = meeting;
      renderMeeting();
    } catch (error) {
      if (epoch !== state.selectionEpoch) return;
      showError("global-error", `Не удалось открыть встречу: ${error.message}`);
    }
  }
  async function refresh({ config = false, quiet = false } = {}) {
    if (state.refreshBusy || !state.token || state.editing) return;
    state.refreshBusy = true;
    const authEpoch = state.authEpoch;
    const selectedId = state.selectedId;
    const selectionEpoch = state.selectionEpoch;
    $("refresh").classList.add("spinning");
    try {
      if (config || !state.config) {
        const result = await json("/api/config");
        if (authEpoch !== state.authEpoch) return;
        state.config = result;
        renderConfig();
      }
      const data = await json("/api/meetings");
      if (authEpoch !== state.authEpoch) return;
      const nextMeetings = Array.isArray(data) ? data : data.items || [];
      const listChanged =
        JSON.stringify(state.meetings) !== JSON.stringify(nextMeetings);
      state.meetings = nextMeetings;
      if (listChanged) renderHistory();
      if (
        selectedId &&
        selectionEpoch === state.selectionEpoch &&
        !state.editing
      ) {
        const meeting = await json(
          `/api/meetings/${encodeURIComponent(selectedId)}`,
        );
        if (
          authEpoch !== state.authEpoch ||
          selectionEpoch !== state.selectionEpoch ||
          state.editing
        )
          return;
        const changed =
          state.meeting?.id !== meeting.id ||
          state.meeting?.version !== meeting.version ||
          state.meeting?.status !== meeting.status;
        state.meeting = meeting;
        if (changed) renderMeeting();
      }
      showError("global-error", "");
    } catch (error) {
      if (authEpoch !== state.authEpoch) return;
      if (!quiet || error.status === 401)
        showError("global-error", error.message);
      else
        showError(
          "global-error",
          `Автообновление не удалось: ${error.message}`,
        );
    } finally {
      state.refreshBusy = false;
      $("refresh").classList.remove("spinning");
    }
  }
  async function beginEdit(id) {
    if (state.editing?.saving) {
      toast("Дождитесь сохранения фрагмента.");
      return;
    }
    if (
      state.editing &&
      !confirm("Отменить несохранённые изменения предыдущего фрагмента?")
    )
      return;
    const segment = state.meeting.segments.find(
      (item) => String(item.id) === String(id),
    );
    if (!segment) return;
    state.editing = { id, version: state.meeting.version };
    renderSegments();
    const article = [...$("segments").children].find(
      (el) => el.dataset.segmentId === String(id),
    );
    article.querySelector(".segment-text").hidden = true;
    const form = node("form", "segment-editor");
    const speakerLabel = node(
      "label",
      "",
      "Метка спикера (не подтверждённая личность)",
    );
    const speaker = node("input");
    speaker.type = "text";
    speaker.value = segment.speaker || "";
    speaker.maxLength = 200;
    speaker.id = "edit-speaker";
    speakerLabel.htmlFor = speaker.id;
    const textLabel = node("label", "", "Текст фрагмента");
    const text = node("textarea");
    text.value = segment.text || "";
    text.required = true;
    text.maxLength = 20000;
    text.id = "edit-text";
    textLabel.htmlFor = text.id;
    const errorBox = node("div", "form-error");
    errorBox.setAttribute("role", "alert");
    errorBox.hidden = true;
    const actions = node("div", "editor-actions");
    const cancel = node("button", "button", "Отмена");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      state.editing = null;
      renderSegments();
    });
    const reload = node("button", "button", "Загрузить актуальную версию");
    reload.type = "button";
    reload.hidden = true;
    reload.addEventListener("click", async () => {
      if (
        !confirm(
          "Загрузить актуальную версию? Ваши несохранённые изменения будут удалены.",
        )
      )
        return;
      state.editing = null;
      await selectMeeting(state.selectedId, { force: true });
    });
    const save = node("button", "primary-button", "Сохранить");
    save.type = "submit";
    actions.append(cancel, reload, save);
    form.append(speakerLabel, speaker, textLabel, text, errorBox, actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (save.disabled || !state.editing) return;
      if (!text.value.trim()) {
        errorBox.textContent = "Добавьте текст фрагмента.";
        errorBox.hidden = false;
        return;
      }
      state.editing.saving = true;
      save.disabled = true;
      cancel.disabled = true;
      save.textContent = "Сохраняем…";
      const meetingId = state.selectedId;
      try {
        await json(
          `/api/meetings/${encodeURIComponent(meetingId)}/segments/${encodeURIComponent(id)}`,
          {
            method: "PATCH",
            body: JSON.stringify({
              text: text.value,
              speaker: speaker.value.trim() || null,
              version: state.editing.version,
            }),
          },
        );
        state.editing = null;
        toast("Изменения сохранены");
        await selectMeeting(meetingId, { force: true });
      } catch (error) {
        errorBox.textContent =
          error.status === 409
            ? "Кто-то уже обновил транскрипцию. Ваш текст сохранён в этом поле. Скопируйте его перед загрузкой актуальной версии."
            : error.message;
        errorBox.hidden = false;
        reload.hidden = error.status !== 409;
      } finally {
        if (state.editing) state.editing.saving = false;
        save.disabled = false;
        cancel.disabled = false;
        save.textContent = "Сохранить";
      }
    });
    article.append(form);
    text.focus();
  }

  async function createMeeting(event) {
    event.preventDefault();
    if (state.submitting) return;
    showError("form-error", "");
    if (!state.token) {
      openAccess();
      return;
    }
    if (!$("consent").checked) {
      showError(
        "form-error",
        "Подтвердите разрешение на запись и обработку разговора.",
      );
      return;
    }
    const file = state.file || $("recording-file").files[0];
    if (state.source === "upload" && !file) {
      showError("form-error", "Выберите аудио- или видеозапись.");
      return;
    }
    if (state.source === "upload" && file.size === 0) {
      showError("form-error", "Выбранный файл пуст. Выберите другую запись.");
      return;
    }
    if (
      state.source === "upload" &&
      state.config?.max_upload_mb &&
      file.size > state.config.max_upload_mb * 1024 * 1024
    ) {
      showError(
        "form-error",
        `Максимальный размер записи: ${state.config.max_upload_mb} МБ.`,
      );
      return;
    }
    const source = state.source;
    const title =
      $("meeting-title").value.trim() ||
      (source === "upload"
        ? file.name.replace(/\.[^.]+$/, "").slice(0, 200)
        : "Встреча Microsoft Teams");
    const language = $("meeting-language").value;
    state.submitting = true;
    for (const id of [
      "upload-tab",
      "teams-tab",
      "recording-file",
      "meeting-url",
      "meeting-title",
      "meeting-language",
      "consent",
    ])
      $(id).disabled = true;
    renderStartButton();
    try {
      let meeting;
      if (source === "upload") {
        const body = new FormData();
        body.append("file", file);
        body.append("title", title);
        body.append("language", language);
        body.append("consent_confirmed", "true");
        meeting = await json("/api/uploads", { method: "POST", body });
      } else {
        meeting = await json("/api/meetings", {
          method: "POST",
          body: JSON.stringify({
            title,
            meeting_url: $("meeting-url").value.trim(),
            language,
            consent_confirmed: true,
          }),
        });
      }
      $("meeting-title").value = "";
      $("meeting-url").value = "";
      $("recording-file").value = "";
      $("consent").checked = false;
      setFile(null);
      toast(
        source === "upload"
          ? "Запись загружена и добавлена в очередь"
          : "Задача подключения к Teams создана",
      );
      await refresh();
      await selectMeeting(meeting.id);
    } catch (error) {
      showError("form-error", error.message);
    } finally {
      state.submitting = false;
      for (const id of [
        "upload-tab",
        "teams-tab",
        "recording-file",
        "meeting-url",
        "meeting-title",
        "meeting-language",
        "consent",
      ])
        $(id).disabled = false;
      renderStartButton();
    }
  }
  async function meetingAction(action, button) {
    if (!state.selectedId || button.disabled) return;
    const id = state.selectedId;
    const oldLabel = button.textContent;
    button.disabled = true;
    button.textContent = "Подождите…";
    try {
      const updated = await json(
        `/api/meetings/${encodeURIComponent(id)}/${action}`,
        { method: "POST" },
      );
      if (updated && state.selectedId === id) {
        state.meeting = updated;
        renderMeeting();
      }
      if (updated) {
        const item = state.meetings.find((entry) => entry.id === id);
        if (item) Object.assign(item, updated);
        renderHistory();
      }
      toast(
        action === "stop"
          ? "Запрос на остановку отправлен"
          : "Повторная обработка запущена",
      );
      await refresh();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = oldLabel;
    }
  }
  function transcriptText() {
    const meeting = state.meeting;
    return [
      meeting.title || "Транскрипция",
      "",
      ...(meeting.segments || []).flatMap((segment) => [
        `[${time(segment.start)} — ${time(segment.end)}] ${segment.speaker || "Спикер не указан"}${segment.final === false || segment.final === 0 ? " · предварительный фрагмент" : ""}`,
        segment.text || "",
        "",
      ]),
    ].join("\n");
  }
  async function exportMeeting() {
    if (!state.selectedId || $("export-button").disabled) return;
    const format = $("export-format").value;
    $("export-button").disabled = true;
    try {
      const response = await api(
        `/api/meetings/${encodeURIComponent(state.selectedId)}/export?format=${encodeURIComponent(format)}`,
      );
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = node("a");
      link.href = url;
      const disposition = response.headers.get("Content-Disposition") || "";
      const filename = disposition.match(/filename="([^"]+)"/i)?.[1];
      link.download =
        filename && !/[\\/\r\n]/.test(filename)
          ? filename
          : `transcript.${format}`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      toast(`Файл ${format.toUpperCase()} подготовлен к скачиванию`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      $("export-button").disabled = !(state.meeting?.segments || []).length;
    }
  }
  function openAccess() {
    if (state.submitting || state.editing?.saving) {
      toast("Дождитесь завершения текущего запроса.");
      return;
    }
    showError("access-error", "");
    $("admin-token").value = "";
    $("logout-button").hidden = !state.token;
    if (!$("access-dialog").open) $("access-dialog").showModal();
    $("admin-token").focus();
  }
  async function connect(event) {
    event.preventDefault();
    if (state.connecting) return;
    const token = $("admin-token").value.trim();
    if (!token) return;
    if (
      state.editing &&
      !confirm("Смена подключения отменит несохранённые изменения. Продолжить?")
    )
      return;
    const previousToken = state.token;
    state.connecting = true;
    state.token = token;
    state.authEpoch++;
    $("connect-button").disabled = true;
    $("close-access").disabled = true;
    $("connect-button").textContent = "Проверяем доступ…";
    showError("access-error", "");
    try {
      const config = await json("/api/config");
      state.config = config;
      try {
        sessionStorage.setItem(TOKEN_KEY, token);
      } catch (_) {
        toast(
          "Токен доступен до перезагрузки: хранилище сессии заблокировано браузером.",
        );
      }
      $("admin-token").value = "";
      $("access-dialog").close();
      state.meetings = [];
      state.meeting = null;
      state.selectedId = null;
      state.editing = null;
      state.selectionEpoch++;
      renderConfig();
      renderHistory();
      renderMeeting();
      await refresh();
    } catch (error) {
      state.token = previousToken;
      state.authEpoch++;
      showError("access-error", error.message);
    } finally {
      state.connecting = false;
      $("connect-button").disabled = false;
      $("close-access").disabled = false;
      $("connect-button").textContent = "Подключиться →";
    }
  }
  function logout() {
    if (state.submitting || state.connecting) {
      toast("Дождитесь завершения текущего запроса.", true);
      return;
    }
    if (state.editing && !confirm("Выйти и отменить несохранённые изменения?"))
      return;
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch (_) {
      /* No persistent token exists. */
    }
    state.token = "";
    state.authEpoch++;
    state.selectionEpoch++;
    state.config = null;
    state.meetings = [];
    state.selectedId = null;
    state.meeting = null;
    state.editing = null;
    $("admin-token").value = "";
    $("access-dialog").close();
    renderConfig();
    renderHistory();
    renderMeeting();
    showError("global-error", "");
    toast("Токен удалён из сессии");
  }

  $("upload-tab").addEventListener("click", () => changeSource("upload"));
  $("teams-tab").addEventListener("click", () => changeSource("teams"));
  for (const name of ["upload", "teams"])
    $(`${name}-tab`).addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
        return;
      event.preventDefault();
      const other =
        event.key === "Home"
          ? "upload"
          : event.key === "End"
            ? "teams"
            : name === "upload"
              ? "teams"
              : "upload";
      changeSource(other);
      $(`${other}-tab`).focus();
    });
  $("recording-file").addEventListener("change", () =>
    setFile($("recording-file").files[0]),
  );
  for (const type of ["dragenter", "dragover"])
    $("dropzone").addEventListener(type, (event) => {
      event.preventDefault();
      if (!state.submitting) $("dropzone").classList.add("dragging");
    });
  for (const type of ["dragleave", "drop"])
    $("dropzone").addEventListener(type, (event) => {
      event.preventDefault();
      $("dropzone").classList.remove("dragging");
    });
  $("dropzone").addEventListener("drop", (event) => {
    if (state.submitting) return;
    if (event.dataTransfer.files.length > 1) {
      showError("form-error", "Загрузите одну запись за раз.");
      return;
    }
    if (event.dataTransfer.files[0]) setFile(event.dataTransfer.files[0]);
  });
  $("create-form").addEventListener("submit", createMeeting);
  $("history-search").addEventListener("input", renderHistory);
  $("refresh").addEventListener("click", () => {
    if (state.editing) {
      toast("Сохраните или отмените редактирование перед обновлением.");
      return;
    }
    if (!state.token) {
      openAccess();
      return;
    }
    refresh({ config: true });
  });
  $("access-button").addEventListener("click", openAccess);
  $("close-access").addEventListener("click", () => $("access-dialog").close());
  $("access-dialog").addEventListener("cancel", (event) => {
    if (state.connecting) event.preventDefault();
  });
  $("access-form").addEventListener("submit", connect);
  $("logout-button").addEventListener("click", logout);
  $("stop-button").addEventListener("click", () =>
    meetingAction("stop", $("stop-button")),
  );
  $("retry-button").addEventListener("click", () =>
    meetingAction("retry", $("retry-button")),
  );
  $("copy-button").addEventListener("click", async () => {
    if (!state.meeting || $("copy-button").disabled) return;
    try {
      await navigator.clipboard.writeText(transcriptText());
      toast("Текст скопирован");
    } catch (_) {
      toast(
        "Буфер обмена недоступен. Скачайте TXT или скопируйте текст вручную.",
        true,
      );
    }
  });
  $("export-button").addEventListener("click", exportMeeting);
  $("auto-refresh").addEventListener("change", () => {
    if ($("auto-refresh").checked) refresh();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && $("auto-refresh").checked) refresh();
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.editing || state.submitting) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  renderConfig();
  changeSource("upload");
  renderHistory();
  renderMeeting();
  if (state.token) refresh({ config: true });
  const poll = async () => {
    if (
      !document.hidden &&
      $("auto-refresh").checked &&
      state.token &&
      !state.connecting
    )
      await refresh({ quiet: true });
    setTimeout(poll, 6000);
  };
  setTimeout(poll, 6000);
})();
