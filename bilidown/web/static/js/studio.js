/* BiliDown Studio 2.0 — Frontend Controller */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const PICKER_PAGE_SIZE = 60;

  const TAB_META = {
    workspace: ["解析下载", "支持 BV/av 视频、b23.tv 短链、番剧/课程 ep & ss、UP 合集与收藏夹"],
    queue: ["任务队列", "多任务自动排队、实时速率监控、分集状态与一键重试失败项"],
    account: ["我的 B 站", "直接浏览并批量下载「稍后再看」与「我的收藏夹」"],
    history: ["下载历史", "本地持久化归档记录，随时搜索、定位目录或重新下载"],
    settings: ["偏好设置", "画质编码默认项、弹幕渲染美学、命名模板与 Cloudreve 网盘同步"],
  };

  const TYPE_LABELS = {
    video: "视频",
    course: "课程",
    bangumi: "番剧",
    ugc_season: "UP 合集",
    series: "系列合集",
    favlist: "收藏夹",
    watchlater: "稍后再看",
  };

  const STATUS_LABELS = {
    queued: "排队中",
    resolving: "解析中",
    running: "进行中",
    downloading: "下载中",
    merging: "合并封装",
    uploading: "上传网盘",
    done: "已完成",
    completed: "已完成",
    partial: "部分完成",
    stopped: "已停止",
    cancelled: "已取消",
    error: "失败",
    failed: "失败",
  };

  const state = {
    tab: "workspace",
    sys: null,
    settings: null,
    profile: { logged_in: false },
    // Workspace
    inspected: null,
    inspectedUrl: "",
    selectedIds: new Set(),
    pickerQuery: "",
    pickerPage: 0,
    pickerView: "list",
    lastPickedIdx: -1,
    downloadMode: "video",
    batchMode: false,
    // Queue
    tasks: [],
    openEpisodes: new Set(),
    openLogs: new Set(),
    logFollow: new Map(),
    pollTimer: null,
    // Account
    accSub: "watchlater",
    accItems: [],
    accSelected: new Set(),
    favFolders: [],
    activeFavId: null,
    accLoaded: false,
    // Login modal
    loginKey: "",
    loginTimer: null,
  };

  // ─── Utilities ────────────────────────────────────────────────────────

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function coverProxy(url) {
    if (!url) return "";
    return "api/cover?url=" + encodeURIComponent(url);
  }

  function fmtCount(n) {
    n = Number(n) || 0;
    if (n >= 1e8) return (n / 1e8).toFixed(1) + "亿";
    if (n >= 1e4) return (n / 1e4).toFixed(1) + "万";
    return String(n);
  }

  function fmtDur(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return h
      ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
      : `${m}:${String(s).padStart(2, "0")}`;
  }

  function fmtBytes(bytes) {
    bytes = Number(bytes) || 0;
    if (bytes <= 0) return "0 MB";
    if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(2) + " GB";
    if (bytes >= 1024 ** 2) return (bytes / 1024 ** 2).toFixed(1) + " MB";
    return (bytes / 1024).toFixed(0) + " KB";
  }

  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const p = (v) => String(v).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function toast(msg, kind = "info") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("toast-stack").appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(4px)";
      el.style.transition = "all .2s ease";
      setTimeout(() => el.remove(), 220);
    }, 3200);
  }

  async function api(path, opts = {}) {
    const init = {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    };
    const resp = await fetch(path, init);
    let data = {};
    try {
      data = await resp.json();
    } catch (_) {}
    if (!resp.ok) {
      throw new Error(data.error || data.message || `HTTP ${resp.status}`);
    }
    return data;
  }

  // ─── Theme & Navigation ───────────────────────────────────────────────

  function applyTheme(theme) {
    const mode = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("bilidown_theme", mode);
    $("theme-icon-sun").classList.toggle("hidden", mode !== "dark");
    $("theme-icon-moon").classList.toggle("hidden", mode === "dark");
  }

  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === tab)
    );
    document.querySelectorAll(".tab-view").forEach((s) =>
      s.classList.toggle("active", s.id === `view-${tab}`)
    );
    const [title, sub] = TAB_META[tab] || ["BiliDown Studio", ""];
    $("page-title").textContent = title;
    $("page-subtitle").textContent = sub;

    if (tab === "queue") pollTasks();
    if (tab === "history") loadHistory();
    if (tab === "account") ensureAccountLoaded();
    if (tab === "settings") renderDiagnostics();
  }

  // ─── System & Settings Bootstrap ──────────────────────────────────────

  async function bootstrap() {
    applyTheme(localStorage.getItem("bilidown_theme") || "dark");
    try {
      const [sys, settings] = await Promise.all([
        api("api/system/info"),
        api("api/settings"),
      ]);
      state.sys = sys;
      state.settings = settings;
      applySystemToUI();
      applySettingsToWorkspace();
      applySettingsToForm();
    } catch (e) {
      toast("加载系统信息失败：" + e.message, "err");
    }
    loadLoginStatus();
    pollTasks();
  }

  function applySystemToUI() {
    const sys = state.sys;
    if (!sys) return;

    const pill = $("sys-mode-pill");
    if (sys.desktop_mode) {
      pill.textContent = "桌面本地";
      pill.classList.remove("cloud");
      $("mode-subtitle").textContent = "本地桌面工作台";
    } else {
      pill.textContent = "Cloudreve 网盘";
      pill.classList.add("cloud");
      $("mode-subtitle").textContent = "BiliDown → Cloudreve";
    }

    const ff = $("sys-ffmpeg-dot");
    ff.textContent = sys.ffmpeg_ok ? "已就绪" : "未安装";
    ff.className = "sys-dot " + (sys.ffmpeg_ok ? "ok" : "err");

    $("sys-disk-text").textContent = `${sys.disk_free_gb} GB`;
    const usedPct = sys.disk_total_gb
      ? Math.min(100, Math.round(((sys.disk_total_gb - sys.disk_free_gb) / sys.disk_total_gb) * 100))
      : 0;
    $("sys-disk-bar").style.width = usedPct + "%";

    const hCount = sys.history_stats?.total_tasks || 0;
    $("nav-history-count").textContent = hCount ? String(hCount) : "";

    // Show/hide desktop folder controls
    $("pick-folder-btn").classList.toggle("hidden", !sys.desktop_mode);
    $("open-output-dir-btn").classList.toggle("hidden", !sys.desktop_mode);
    $("set-pick-folder").classList.toggle("hidden", !sys.desktop_mode);
  }

  function applySettingsToWorkspace() {
    const s = state.settings;
    const sys = state.sys;
    if (!s || !sys) return;

    $("opt-quality").value = s.quality || "1080P";
    $("opt-codec").value = s.codec || "auto";
    $("opt-workers").value = s.workers || 2;
    $("opt-audio-fmt").value = s.audio_format || "m4a";
    $("opt-cover").checked = s.cover !== false;
    $("opt-danmaku").checked = s.danmaku !== false;
    $("opt-subtitle").checked = s.subtitle !== false;
    $("opt-embed").checked = s.embed_extras !== false;
    $("opt-output-dir").value = s.output_dir || sys.default_output_dir || "";
    $("opt-remote-dir").value = s.cloudreve_remote_dir || "/视频/B站";
    $("opt-keep-local").checked = Boolean(s.keep_local);

    const cloudAvailable = Boolean(sys.cloudreve_configured);
    const defaultCloud = !sys.desktop_mode && cloudAvailable;
    $("opt-upload-cloud").checked = defaultCloud;
    $("cloud-switch-wrap").classList.toggle("hidden", !(sys.desktop_mode && cloudAvailable));
    syncDestinationFields();
    setDownloadMode(s.download_mode || "video");
  }

  function syncDestinationFields() {
    const sys = state.sys;
    const useCloud = !sys?.desktop_mode
      ? Boolean(sys?.cloudreve_configured)
      : $("opt-upload-cloud").checked;
    $("local-dir-field").classList.toggle("hidden", useCloud);
    $("cloudreve-dir-field").classList.toggle("hidden", !useCloud);
  }

  function setDownloadMode(mode) {
    state.downloadMode = mode;
    document.querySelectorAll("#mode-segmented .seg-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === mode)
    );
    $("opt-video-row").classList.toggle("hidden", mode !== "video");
    $("opt-audio-row").classList.toggle("hidden", mode !== "audio");
    $("opt-extras-strip").classList.toggle("hidden", mode === "audio");
    $("pill-embed").classList.toggle("hidden", mode !== "video");
    updateSubmitSummary();
  }

  // ─── Login & Account Profile ──────────────────────────────────────────

  async function loadLoginStatus() {
    try {
      const p = await api("api/login/status");
      state.profile = p;
      const pill = $("account-btn");
      if (p.logged_in) {
        pill.classList.add("logged");
        $("user-name").textContent = p.name || "已登录";
        $("user-sub").textContent = `Lv.${p.level || 0} · ${p.vip_label || (p.vip ? "大会员" : "普通用户")}`;
        if (p.face) {
          $("user-avatar").innerHTML = `<img src="${coverProxy(p.face)}" alt="">`;
        }
        $("modal-logged-profile").classList.remove("hidden");
        $("modal-profile-text").textContent = `当前登录：${p.name} (Lv.${p.level}${p.vip ? " · 大会员" : ""})`;
      } else {
        pill.classList.remove("logged");
        $("user-name").textContent = "未登录";
        $("user-sub").textContent = "扫码解锁高清与 AI 字幕";
        $("user-avatar").innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M5 20a7 7 0 0114 0"/></svg>`;
        $("modal-logged-profile").classList.add("hidden");
      }
      if (state.tab === "account") ensureAccountLoaded(true);
    } catch (_) {}
  }

  async function openLoginModal() {
    $("login-modal").classList.remove("hidden");
    $("cookie-msg").textContent = "";
    await refreshQr();
    if (state.loginTimer) clearInterval(state.loginTimer);
    state.loginTimer = setInterval(pollQrLogin, 1600);
  }

  function closeModal(id) {
    $(id).classList.add("hidden");
    if (id === "login-modal" && state.loginTimer) {
      clearInterval(state.loginTimer);
      state.loginTimer = null;
    }
  }

  async function refreshQr() {
    $("qr-status").textContent = "正在生成二维码...";
    $("qr-img").classList.add("hidden");
    $("qr-placeholder").classList.remove("hidden");
    try {
      const d = await api("api/login/qr");
      state.loginKey = d.key;
      $("qr-img").src = d.image;
      $("qr-img").classList.remove("hidden");
      $("qr-placeholder").classList.add("hidden");
      $("qr-status").textContent = "请使用 Bilibili 手机 App 扫码";
    } catch (e) {
      $("qr-status").textContent = "获取失败：" + e.message;
      $("qr-placeholder").textContent = "加载失败";
    }
  }

  async function pollQrLogin() {
    if (!state.loginKey || $("login-modal").classList.contains("hidden")) return;
    try {
      const d = await api("api/login/poll?key=" + encodeURIComponent(state.loginKey));
      if (d.status === "success") {
        $("qr-status").textContent = "登录成功！";
        toast("B站账号登录成功", "ok");
        closeModal("login-modal");
        loadLoginStatus();
      } else if (d.status === "scanned") {
        $("qr-status").textContent = "已扫码，请在手机上确认登录...";
      } else if (d.status === "expired") {
        $("qr-status").textContent = "二维码已过期，请点击刷新";
      } else {
        $("qr-status").textContent = "等待扫码...";
      }
    } catch (_) {}
  }

  async function saveCookieLogin() {
    const cookie = $("cookie-textarea").value.trim();
    if (!cookie) {
      $("cookie-msg").textContent = "请先粘贴 Cookie";
      return;
    }
    $("cookie-msg").textContent = "正在校验...";
    try {
      const res = await api("api/login/cookie", {
        method: "POST",
        body: JSON.stringify({ cookie }),
      });
      toast(`已保存 Cookie：${res.name || "登录成功"}`, "ok");
      $("cookie-textarea").value = "";
      closeModal("login-modal");
      loadLoginStatus();
    } catch (e) {
      $("cookie-msg").textContent = e.message;
    }
  }

  async function logoutAccount() {
    try {
      await api("api/login/logout", { method: "POST" });
      toast("已退出 B 站账号", "info");
      state.accLoaded = false;
      closeModal("login-modal");
      loadLoginStatus();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // ─── Workspace: URL Inspection & Inline Picker ────────────────────────

  function clearInspection() {
    state.inspected = null;
    state.inspectedUrl = "";
    state.selectedIds = new Set();
    state.pickerQuery = "";
    state.pickerPage = 0;
    state.lastPickedIdx = -1;
    $("media-card").classList.add("hidden");
    $("picker-section").classList.add("hidden");
    updateSubmitSummary();
  }

  async function inspectUrl(rawInput) {
    const url = (rawInput !== undefined ? rawInput : $("url-input").value).trim();
    if (!url) {
      toast("请先输入或粘贴 B 站链接", "err");
      return;
    }
    $("url-input").value = url;
    $("url-clear").classList.remove("hidden");

    // Detect multi-line paste automatically
    if (url.includes("\n")) {
      setBatchMode(true, url);
      return;
    }

    const btn = $("inspect-btn");
    btn.disabled = true;
    btn.querySelector("span").textContent = "解析中...";
    clearInspection();

    try {
      const info = await api("api/info?url=" + encodeURIComponent(url));
      state.inspected = info;
      state.inspectedUrl = url;
      const defaults = info.default_selected_ids || info.default_ep_ids || [];
      state.selectedIds = new Set(defaults.map(String));

      // Jump picker page to current item if any
      const items = info.items || [];
      if (info.current_item_id != null && items.length > PICKER_PAGE_SIZE) {
        const curIdx = items.findIndex((it) => String(it.id) === String(info.current_item_id));
        if (curIdx >= 0) state.pickerPage = Math.floor(curIdx / PICKER_PAGE_SIZE);
      }

      renderInspected();
      toast(`解析成功：${info.title}`, "ok");
    } catch (e) {
      toast("解析失败：" + e.message, "err");
    } finally {
      btn.disabled = false;
      btn.querySelector("span").textContent = "解析链接";
    }
  }

  function renderInspected() {
    const info = state.inspected;
    if (!info) {
      $("media-card").classList.add("hidden");
      return;
    }
    $("media-card").classList.remove("hidden");

    const coverEl = $("media-cover");
    if (info.cover) {
      coverEl.src = coverProxy(info.cover);
      coverEl.style.display = "block";
    } else {
      coverEl.removeAttribute("src");
      coverEl.style.display = "none";
    }

    const items = info.items || [];
    let typeLabel = TYPE_LABELS[info.media_type] || "媒体";
    if (info.media_type === "video" && items.length > 1) typeLabel = `多P视频 (${items.length}P)`;
    $("media-type-pill").textContent = typeLabel;

    if (info.duration) {
      $("media-dur-pill").textContent = fmtDur(info.duration);
      $("media-dur-pill").classList.remove("hidden");
    } else {
      $("media-dur-pill").classList.add("hidden");
    }

    $("media-title").textContent = info.title || "未知标题";

    const metas = [];
    if (info.owner) metas.push(`UP主：${info.owner}`);
    if (info.bvid) metas.push(info.bvid);
    if (items.length > 1) metas.push(`共 ${items.length} 项`);
    if (info.stat?.view != null) metas.push(`${fmtCount(info.stat.view)} 播放`);
    if (info.stat?.danmaku != null) metas.push(`${fmtCount(info.stat.danmaku)} 弹幕`);
    if (info.stat?.like != null) metas.push(`${fmtCount(info.stat.like)} 点赞`);
    $("media-meta").innerHTML = metas.map((m) => `<span>${esc(m)}</span>`).join("");

    const cleanDesc = (info.desc || "").trim();
    if (cleanDesc && cleanDesc !== "-") {
      $("media-desc").textContent = cleanDesc;
      $("media-desc").classList.remove("hidden");
    } else {
      $("media-desc").classList.add("hidden");
    }

    // Stream badges
    const badges = [];
    const streams = info.streams || {};
    (streams.qualities || []).slice(0, 6).forEach((q) => {
      const cls = q.available ? (q.qn >= 112 ? "sbadge q-hi" : "sbadge") : "sbadge q-locked";
      badges.push(`<span class="${cls}" title="${q.available ? "当前账号可下载" : "需登录或大会员"}">${esc(q.name)}${q.available ? "" : " 🔒"}</span>`);
    });
    (streams.codecs || []).forEach((c) => {
      badges.push(`<span class="sbadge codec">${esc(c.label)}</span>`);
    });
    (streams.audios || []).slice(0, 2).forEach((a) => {
      badges.push(`<span class="sbadge">音频 ${esc(a.label)}</span>`);
    });
    (info.subtitles || []).slice(0, 2).forEach((s) => {
      badges.push(`<span class="sbadge sub">字幕 ${esc(s.label)}</span>`);
    });
    $("stream-badges").innerHTML = badges.join("");

    // Inline Picker
    if (items.length > 1) {
      $("picker-section").classList.remove("hidden");
      $("picker-label").textContent =
        info.media_type === "video"
          ? "分P选择"
          : info.kind === "collection"
          ? "选集下载"
          : "列表视频选择";
      $("pick-current-btn").classList.toggle("hidden", info.current_item_id == null);
      $("picker-search").value = state.pickerQuery;
      renderPickerList();
    } else {
      $("picker-section").classList.add("hidden");
    }
    updateSubmitSummary();
  }

  function filteredPickerItems() {
    const items = state.inspected?.items || [];
    const q = state.pickerQuery.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) =>
        (it.title || "").toLowerCase().includes(q) ||
        String(it.index).includes(q) ||
        String(it.bvid || "").toLowerCase().includes(q)
    );
  }

  function renderPickerList() {
    const info = state.inspected;
    if (!info) return;
    const allItems = info.items || [];
    const filtered = filteredPickerItems();
    const totalPages = Math.max(1, Math.ceil(filtered.length / PICKER_PAGE_SIZE));
    state.pickerPage = Math.min(Math.max(0, state.pickerPage), totalPages - 1);

    const start = state.pickerPage * PICKER_PAGE_SIZE;
    const slice = filtered.slice(start, start + PICKER_PAGE_SIZE);

    $("picker-counter").textContent = `已选 ${state.selectedIds.size} / ${allItems.length}`;
    const box = $("picker-list");
    box.className = "picker-body" + (state.pickerView === "grid" ? " grid-mode" : "");
    box.innerHTML = "";

    slice.forEach((it, offset) => {
      const idStr = String(it.id);
      const checked = state.selectedIds.has(idStr);
      const isCur = info.current_item_id != null && String(info.current_item_id) === idStr;
      const globalPos = start + offset;

      if (state.pickerView === "grid") {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = `pick-tile${checked ? " selected" : ""}`;
        tile.title = `#${it.index} ${it.title || ""}`;
        tile.innerHTML = `<span class="pick-tile-num">${info.media_type === "video" ? "P" : "#"}${it.index}${isCur ? " ★" : ""}</span><span class="pick-tile-sub">${esc(it.title || "")}</span>`;
        tile.addEventListener("click", (e) => togglePickedItem(filtered, globalPos, idStr, e.shiftKey));
        box.appendChild(tile);
      } else {
        const row = document.createElement("div");
        row.className = `pick-row${checked ? " selected" : ""}${isCur ? " is-current" : ""}`;
        row.innerHTML = `
          <input type="checkbox" ${checked ? "checked" : ""}>
          <span class="pick-idx">${info.media_type === "video" ? "P" : "第"}${it.index}${info.media_type === "video" ? "" : "集"}</span>
          <span class="pick-title" title="${esc(it.title || "")}">${esc(it.title || "")}</span>
          ${isCur ? '<span class="pick-tag">当前</span>' : ""}
          ${it.duration ? `<span class="pick-dur">${fmtDur(it.duration)}</span>` : ""}
        `;
        row.addEventListener("click", (e) => {
          if (e.target.tagName !== "INPUT") e.preventDefault();
          togglePickedItem(filtered, globalPos, idStr, e.shiftKey);
        });
        box.appendChild(row);
      }
    });

    const pager = $("picker-pager");
    pager.classList.toggle("hidden", totalPages <= 1);
    $("picker-page-info").textContent = `第 ${state.pickerPage + 1} / ${totalPages} 页（匹配 ${filtered.length} 项）`;
    $("picker-prev").disabled = state.pickerPage <= 0;
    $("picker-next").disabled = state.pickerPage >= totalPages - 1;
    updateSubmitSummary();
  }

  function togglePickedItem(filteredList, pos, idStr, shiftKey) {
    const nextState = !state.selectedIds.has(idStr);
    if (shiftKey && state.lastPickedIdx >= 0 && state.lastPickedIdx < filteredList.length) {
      const lo = Math.min(state.lastPickedIdx, pos);
      const hi = Math.max(state.lastPickedIdx, pos);
      for (let i = lo; i <= hi; i++) {
        const sid = String(filteredList[i].id);
        if (nextState) state.selectedIds.add(sid);
        else state.selectedIds.delete(sid);
      }
    } else {
      if (nextState) state.selectedIds.add(idStr);
      else state.selectedIds.delete(idStr);
    }
    state.lastPickedIdx = pos;
    renderPickerList();
  }

  function applyPickerRange() {
    const info = state.inspected;
    if (!info) return;
    const raw = $("picker-range").value.trim();
    if (!raw) {
      toast("请输入范围，例如 1-10, 15, 20-25", "err");
      return;
    }
    const items = info.items || [];
    const max = items.length;
    const indices = new Set();
    try {
      for (const segRaw of raw.replace(/[，、\s]+/g, ",").split(",")) {
        const seg = segRaw.trim();
        if (!seg) continue;
        if (seg.includes("-")) {
          const [a, b] = seg.split("-").map((x) => Number(x.trim()));
          if (!Number.isInteger(a) || !Number.isInteger(b) || a < 1 || b < a || b > max) {
            throw new Error(`范围无效：${seg}（可选 1-${max}）`);
          }
          for (let i = a; i <= b; i++) indices.add(i);
        } else {
          const n = Number(seg);
          if (!Number.isInteger(n) || n < 1 || n > max) {
            throw new Error(`序号无效：${seg}（可选 1-${max}）`);
          }
          indices.add(n);
        }
      }
      if (!indices.size) throw new Error("未解析到有效序号");
    } catch (e) {
      toast(e.message, "err");
      return;
    }
    state.selectedIds = new Set([...indices].map((i) => String(items[i - 1].id)));
    const first = Math.min(...indices) - 1;
    state.pickerPage = Math.floor(first / PICKER_PAGE_SIZE);
    renderPickerList();
    toast(`已选中 ${state.selectedIds.size} 项`, "ok");
  }

  function updateSubmitSummary() {
    const info = state.inspected;
    const modeName = { video: "视频+音频", audio: `仅音频 (${$("opt-audio-fmt").value.toUpperCase()})`, extras: "仅弹幕/字幕/封面" }[state.downloadMode];
    if (!info) {
      $("submit-summary").textContent = `当前模式：${modeName} · 粘贴链接后可直接加入队列`;
      $("enqueue-btn-text").textContent = "加入下载队列";
      return;
    }
    const items = info.items || [];
    const count = items.length > 1 ? state.selectedIds.size : 1;
    const qual = state.downloadMode === "video" ? ` · ${$("opt-quality").value}` : "";
    $("submit-summary").textContent = `将下载「${info.title}」共 ${count} 项 · ${modeName}${qual}`;
    $("enqueue-btn-text").textContent = count > 1 ? `加入队列 (${count} 项)` : "加入下载队列";
  }

  function buildTaskPayload(urlOverride, extra = {}) {
    const sys = state.sys;
    const useCloud = !sys?.desktop_mode
      ? Boolean(sys?.cloudreve_configured)
      : $("opt-upload-cloud").checked;
    return {
      url: urlOverride,
      quality: $("opt-quality").value,
      codec: $("opt-codec").value,
      download_mode: state.downloadMode,
      audio_format: $("opt-audio-fmt").value,
      workers: Math.max(1, parseInt($("opt-workers").value || "2", 10)),
      cover: $("opt-cover").checked,
      danmaku: $("opt-danmaku").checked,
      subtitle: $("opt-subtitle").checked,
      embed_extras: $("opt-embed").checked,
      output_dir: $("opt-output-dir").value.trim(),
      upload_cloudreve: useCloud,
      remote_dir: $("opt-remote-dir").value.trim(),
      keep_local: $("opt-keep-local").checked,
      ...extra,
    };
  }

  async function enqueueCurrent() {
    const url = $("url-input").value.trim();
    if (!url) {
      toast("请输入视频链接或 BV 号", "err");
      return;
    }
    const info = state.inspected && state.inspectedUrl === url ? state.inspected : null;
    let selectedIds = [];
    if (info && (info.items || []).length > 1) {
      if (state.selectedIds.size === 0) {
        toast("请至少选择 1 个分集或分P", "err");
        return;
      }
      // Preserve original order
      selectedIds = info.items
        .filter((it) => state.selectedIds.has(String(it.id)))
        .map((it) => it.id);
    }

    const payload = buildTaskPayload(info ? info.url : url, {
      title: info?.title || "",
      cover_url: info?.cover || "",
      media_type: info?.media_type || "video",
      selected_ids: selectedIds,
    });

    const btn = $("enqueue-btn");
    btn.disabled = true;
    try {
      await api("api/tasks", { method: "POST", body: JSON.stringify(payload) });
      toast("已加入下载队列", "ok");
      await pollTasks();
    } catch (e) {
      toast("提交失败：" + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  // ─── Batch Multi-Link Mode ────────────────────────────────────────────

  function setBatchMode(on, prefill = "") {
    state.batchMode = on;
    $("single-input-wrap").classList.toggle("hidden", on);
    $("batch-input-wrap").classList.toggle("hidden", !on);
    $("toggle-batch-btn").textContent = on ? "返回单链解析" : "批量多链粘贴";
    if (prefill) {
      $("batch-textarea").value = prefill;
      updateBatchCount();
    }
  }

  function extractBatchLines() {
    const lines = $("batch-textarea").value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const out = [];
    const seen = new Set();
    for (const line of lines) {
      const m = line.match(/https?:\/\/[^\s<>"]+/) || [line];
      const u = m[0].trim();
      if (u && !seen.has(u)) {
        seen.add(u);
        out.push(u);
      }
    }
    return out;
  }

  function updateBatchCount() {
    const urls = extractBatchLines();
    $("batch-count-hint").textContent = `已识别 ${urls.length} 条链接`;
  }

  async function enqueueBatchLines() {
    const urls = extractBatchLines();
    if (!urls.length) {
      toast("请至少粘贴一条链接", "err");
      return;
    }
    const tasks = urls.map((u) => buildTaskPayload(u));
    try {
      const res = await api("api/tasks", { method: "POST", body: JSON.stringify({ tasks }) });
      toast(`已批量加入 ${(res.tasks || []).length} 个任务到队列`, "ok");
      $("batch-textarea").value = "";
      setBatchMode(false);
      await pollTasks();
      switchTab("queue");
    } catch (e) {
      toast("批量提交失败：" + e.message, "err");
    }
  }

  // ─── Queue Rendering & Polling ────────────────────────────────────────

  function buildSparkline(history) {
    if (!history || history.length < 2) return "";
    const max = Math.max(1, ...history);
    const w = 110, h = 24;
    const pts = history.map((v, i) => {
      const x = (i / (history.length - 1)) * w;
      const y = h - 2 - (v / max) * (h - 5);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const area = `0,${h} ${pts.join(" ")} ${w},${h}`;
    return `
      <svg class="sparkline-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polygon points="${area}" fill="rgba(0,174,236,0.16)"></polygon>
        <polyline points="${pts.join(" ")}" fill="none" stroke="#00aeec" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></polyline>
      </svg>`;
  }

  async function pollTasks() {
    try {
      const d = await api("api/tasks");
      state.tasks = d.tasks || [];
      const counts = d.counts || {};
      const activeN = counts.active || 0;

      const badge = $("nav-queue-badge");
      badge.textContent = String(activeN);
      badge.classList.toggle("hidden", activeN === 0);

      // Update mini banner on Workspace
      const running = state.tasks.find((t) => t.status === "running");
      const miniCard = $("workspace-active-card");
      if (running) {
        miniCard.classList.remove("hidden");
        $("wa-title").textContent = running.title || running.url;
        const speedStr = running.speed_mbps > 0 ? `${running.speed_mbps.toFixed(1)} MB/s · ` : "";
        $("wa-meta").textContent = `${STATUS_LABELS[running.phase] || running.phase} · ${speedStr}${Math.round(running.progress)}%`;
        $("wa-bar").style.width = `${running.progress}%`;
      } else {
        miniCard.classList.add("hidden");
      }

      if (state.tab === "queue") renderQueue();

      // Adaptive polling frequency
      scheduleNextPoll(activeN > 0 ? 600 : 3500);
    } catch (_) {
      scheduleNextPoll(4000);
    }
  }

  function scheduleNextPoll(delayMs) {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    if (document.hidden) return;
    state.pollTimer = setTimeout(pollTasks, delayMs);
  }

  function createTaskCard(id) {
    const card = document.createElement("article");
    card.className = "task-card";
    card.dataset.tid = id;
    card.innerHTML = `
      <div class="task-head">
        <div class="task-thumb"></div>
        <div class="task-main">
          <div class="task-title-line">
            <span class="task-title"></span>
            <span class="status-chip"></span>
          </div>
          <div class="task-tags"></div>
          <div class="task-tele-host"></div>
          <div class="progress-track"><i></i></div>
          <div class="task-error hidden"></div>
        </div>
      </div>
      <div class="task-actions">
        <div class="btn-group task-actions-left"></div>
        <div class="btn-group task-actions-right"></div>
      </div>
      <div class="task-drawer ep-drawer-host hidden">
        <div class="ep-status-list"></div>
      </div>
      <div class="task-drawer log-drawer-host hidden">
        <div class="log-bar">
          <span class="log-follow-hint muted">自动跟随最新日志</span>
          <div class="btn-group">
            <button class="btn ghost xs" data-act="log-bottom" data-id="${esc(id)}">回到底部</button>
            <button class="btn ghost xs" data-act="log-copy" data-id="${esc(id)}">复制日志</button>
          </div>
        </div>
        <pre class="log-console"></pre>
      </div>
    `;

    const pre = card.querySelector(".log-console");
    pre.addEventListener("scroll", () => {
      const nearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight <= 24;
      state.logFollow.set(id, nearBottom);
      const hint = card.querySelector(".log-follow-hint");
      if (hint) {
        hint.textContent = nearBottom ? "自动跟随最新日志" : "已暂停滚动跟随（滑到底部恢复）";
      }
    });
    return card;
  }

  function updateTaskCard(card, t, isDesktop) {
    const isRunning = t.status === "running";
    const isQueued = t.status === "queued";
    const canRetry = ["partial", "error"].includes(t.status);
    const canOpen = isDesktop && !t.upload_cloudreve && (t.resolved_dir || (t.files && t.files.length));
    const firstMedia = (t.files || []).find((f) => /\.(mp4|mkv|flv|m4a|mp3|flac)$/i.test(f));
    const epOpen = state.openEpisodes.has(t.id);
    const logOpen = state.openLogs.has(t.id);

    card.classList.toggle("is-running", isRunning);

    // Thumbnail (only replace when cover URL actually changes to prevent flicker)
    const thumb = card.querySelector(".task-thumb");
    const coverKey = t.cover || "";
    if (thumb.dataset.cover !== coverKey) {
      thumb.dataset.cover = coverKey;
      thumb.innerHTML = coverKey
        ? `<img src="${coverProxy(coverKey)}" alt="">`
        : `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
    }

    const phaseLabel = isRunning ? STATUS_LABELS[t.phase] || "下载中" : STATUS_LABELS[t.status] || t.status;
    card.querySelector(".task-title").textContent = t.title || t.url;
    const chip = card.querySelector(".status-chip");
    chip.className = `status-chip ${t.status}`;
    chip.textContent = phaseLabel;

    const modeTag =
      t.download_mode === "audio"
        ? `音频 ${(t.audio_format || "m4a").toUpperCase()}`
        : t.download_mode === "extras"
        ? "仅附加内容"
        : `${t.quality} · ${(t.codec || "auto").toUpperCase()}`;

    const tagsHtml = `
      <span class="task-tag">${esc(TYPE_LABELS[t.media_type] || t.media_type)}</span>
      <span class="task-tag">${esc(modeTag)}</span>
      ${t.total_items > 1 ? `<span class="task-tag">并发 ${t.workers}</span>` : ""}
      <span class="task-tag">${t.upload_cloudreve ? "☁ " + esc(t.remote_dir) : "📁 " + esc(t.resolved_dir || t.output_dir)}</span>
    `;
    const tagsEl = card.querySelector(".task-tags");
    if (tagsEl.dataset.sig !== tagsHtml) {
      tagsEl.dataset.sig = tagsHtml;
      tagsEl.innerHTML = tagsHtml;
    }

    const teleHtml = isRunning
      ? `<div class="telemetry-row">
           <div class="tele-metrics">
             <span class="tele-speed">${t.speed_mbps > 0 ? t.speed_mbps.toFixed(2) + " MB/s" : "准备中..."}</span>
             ${t.eta_sec > 0 ? `<span>剩余 ${fmtDur(t.eta_sec)}</span>` : ""}
             ${t.total_bytes > 0 ? `<span>${fmtBytes(t.downloaded_bytes)} / ${fmtBytes(t.total_bytes)}</span>` : ""}
             ${t.cdn_host ? `<span class="muted">节点 ${esc(t.cdn_host)}</span>` : ""}
           </div>
           ${buildSparkline(t.speed_history)}
         </div>`
      : `<div class="telemetry-row">
           <div class="tele-metrics">
             <span>进度 ${Math.round(t.progress)}%</span>
             ${t.total_items > 1 ? `<span>成功 ${t.success_items}/${t.total_items}${t.failed_items ? ` · 失败 ${t.failed_items}` : ""}</span>` : ""}
             ${t.total_bytes > 0 ? `<span>体积 ${fmtBytes(t.total_bytes)}</span>` : ""}
           </div>
           <span class="muted">${fmtTime(t.finished_at || t.created_at)}</span>
         </div>`;
    card.querySelector(".task-tele-host").innerHTML = teleHtml;

    const barCls = t.status === "done" ? "done" : t.status === "error" ? "error" : t.status === "partial" ? "partial" : "";
    const track = card.querySelector(".progress-track");
    track.className = `progress-track ${barCls}`;
    track.firstElementChild.style.width = `${t.progress}%`;

    const errEl = card.querySelector(".task-error");
    if (t.error) {
      errEl.textContent = t.error;
      errEl.classList.remove("hidden");
    } else {
      errEl.classList.add("hidden");
    }

    const leftActionsHtml = `
      ${t.items && t.items.length > 1 ? `<button class="btn ghost xs" data-act="toggle-ep" data-id="${esc(t.id)}">${epOpen ? "收起分集" : `分集进度 (${t.items.length})`}</button>` : ""}
      <button class="btn ghost xs" data-act="toggle-log" data-id="${esc(t.id)}">${logOpen ? "收起日志" : `终端日志 (${t.log_total || 0})`}</button>
    `;
    const leftBox = card.querySelector(".task-actions-left");
    if (leftBox.dataset.sig !== leftActionsHtml) {
      leftBox.dataset.sig = leftActionsHtml;
      leftBox.innerHTML = leftActionsHtml;
    }

    const rightActionsHtml = `
      ${canOpen && firstMedia ? `<button class="btn ghost xs" data-act="play" data-path="${esc(firstMedia)}">▶ 播放</button>` : ""}
      ${canOpen ? `<button class="btn ghost xs" data-act="open-dir" data-path="${esc(firstMedia || t.resolved_dir)}">打开目录</button>` : ""}
      ${canRetry ? `<button class="btn primary xs" data-act="retry" data-id="${esc(t.id)}">重试失败项</button>` : ""}
      ${isRunning || isQueued ? `<button class="btn danger xs" data-act="stop" data-id="${esc(t.id)}">停止</button>` : `<button class="btn ghost xs" data-act="remove" data-id="${esc(t.id)}">移除</button>`}
    `;
    const rightBox = card.querySelector(".task-actions-right");
    if (rightBox.dataset.sig !== rightActionsHtml) {
      rightBox.dataset.sig = rightActionsHtml;
      rightBox.innerHTML = rightActionsHtml;
    }

    // Episode status drawer (preserve scrollTop!)
    const epHost = card.querySelector(".ep-drawer-host");
    const showEp = epOpen && t.items && t.items.length > 0;
    epHost.classList.toggle("hidden", !showEp);
    if (showEp) {
      const epList = epHost.querySelector(".ep-status-list");
      const prevScroll = epList.scrollTop;
      const epHtml = t.items
        .map(
          (it) => `
        <div class="ep-st-row" title="${esc(it.error || it.title)}">
          <span class="muted">#${it.index}</span>
          <span class="ep-st-title">${esc(it.title)}</span>
          <div class="ep-st-bar ${esc(it.status)}"><i style="width:${Math.round(it.progress || 0)}%"></i></div>
          <span class="muted" style="text-align:right">${esc(STATUS_LABELS[it.status] || it.status)}</span>
        </div>`
        )
        .join("");
      if (epList.dataset.sig !== epHtml) {
        epList.dataset.sig = epHtml;
        epList.innerHTML = epHtml;
        epList.scrollTop = prevScroll;
      }
    }

    // Log console drawer (DOM node preserved; zero jump-to-top when scrolling!)
    const logHost = card.querySelector(".log-drawer-host");
    const wasHidden = logHost.classList.contains("hidden");
    logHost.classList.toggle("hidden", !logOpen);
    if (logOpen) {
      const pre = logHost.querySelector(".log-console");
      const nextText = (t.log || []).join("\n") || "暂无日志";
      if (!state.logFollow.has(t.id)) state.logFollow.set(t.id, true);
      const shouldFollow = state.logFollow.get(t.id);
      const prevScroll = pre.scrollTop;

      if (pre.textContent !== nextText) {
        pre.textContent = nextText;
        if (shouldFollow) {
          pre.scrollTop = pre.scrollHeight;
        } else {
          pre.scrollTop = prevScroll;
        }
      } else if (wasHidden && shouldFollow) {
        pre.scrollTop = pre.scrollHeight;
      }

      const hint = logHost.querySelector(".log-follow-hint");
      if (hint) {
        hint.textContent = shouldFollow ? "自动跟随最新日志" : "已暂停滚动跟随（滑到底部恢复）";
      }
    }
  }

  function renderQueue() {
    const tasks = state.tasks;
    const list = $("queue-list");
    $("queue-empty").classList.toggle("hidden", tasks.length > 0);
    const active = tasks.filter((t) => ["running", "queued"].includes(t.status)).length;
    const done = tasks.filter((t) => t.status === "done").length;
    $("queue-stats").textContent = tasks.length
      ? `共 ${tasks.length} 个任务 · 进行/排队 ${active} · 已完成 ${done}`
      : "当前无任务";

    const isDesktop = Boolean(state.sys?.desktop_mode);
    const existing = new Map();
    list.querySelectorAll(":scope > article.task-card[data-tid]").forEach((el) => {
      existing.set(el.dataset.tid, el);
    });

    const keepIds = new Set(tasks.map((t) => t.id));
    existing.forEach((el, tid) => {
      if (!keepIds.has(tid)) {
        el.remove();
        state.logFollow.delete(tid);
      }
    });

    tasks.forEach((t, idx) => {
      let card = existing.get(t.id);
      if (!card) {
        card = createTaskCard(t.id);
      }
      updateTaskCard(card, t, isDesktop);
      const currentAtIdx = list.children[idx];
      if (currentAtIdx !== card) {
        list.insertBefore(card, currentAtIdx || null);
      }
    });
  }

  // ─── My Bilibili (Watch Later & Favorites) ────────────────────────────

  async function ensureAccountLoaded(force = false) {
    const logged = Boolean(state.profile?.logged_in);
    $("acc-need-login").classList.toggle("hidden", logged);
    $("acc-panel").classList.toggle("hidden", !logged);
    if (!logged) return;
    if (state.accLoaded && !force) return;
    state.accLoaded = true;
    await loadAccountTab();
  }

  async function loadAccountTab() {
    state.accSelected.clear();
    $("acc-grid").innerHTML = "";
    $("acc-empty").classList.add("hidden");
    $("fav-folder-bar").classList.toggle("hidden", state.accSub !== "favorites");

    try {
      if (state.accSub === "watchlater") {
        const d = await api("api/account/watchlater");
        state.accItems = d.items || [];
        renderAccountGrid();
      } else {
        const fd = await api("api/account/fav-folders");
        state.favFolders = fd.folders || [];
        renderFavFolders();
        if (state.favFolders.length) {
          if (!state.activeFavId || !state.favFolders.some((f) => f.id === state.activeFavId)) {
            state.activeFavId = state.favFolders[0].id;
          }
          await loadFavFolderItems(state.activeFavId);
        } else {
          state.accItems = [];
          renderAccountGrid();
        }
      }
    } catch (e) {
      toast("加载失败：" + e.message, "err");
      $("acc-empty").classList.remove("hidden");
      $("acc-empty-msg").textContent = e.message;
    }
  }

  function renderFavFolders() {
    $("fav-folder-bar").innerHTML = state.favFolders
      .map(
        (f) => `<button class="folder-pill${f.id === state.activeFavId ? " active" : ""}" data-fid="${f.id}">${esc(f.title)} (${f.count})</button>`
      )
      .join("");
  }

  async function loadFavFolderItems(fid) {
    state.activeFavId = fid;
    state.accSelected.clear();
    renderFavFolders();
    try {
      const d = await api("api/account/fav-items?media_id=" + encodeURIComponent(fid));
      state.accItems = d.items || [];
      renderAccountGrid();
    } catch (e) {
      toast("加载收藏夹失败：" + e.message, "err");
    }
  }

  function filteredAccItems() {
    const q = $("acc-search").value.trim().toLowerCase();
    if (!q) return state.accItems;
    return state.accItems.filter(
      (it) =>
        (it.title || "").toLowerCase().includes(q) ||
        (it.owner || "").toLowerCase().includes(q) ||
        (it.bvid || "").toLowerCase().includes(q)
    );
  }

  function renderAccountGrid() {
    const items = filteredAccItems();
    const grid = $("acc-grid");
    $("acc-empty").classList.toggle("hidden", items.length > 0);
    $("acc-empty-msg").textContent = state.accItems.length ? "没有匹配的搜索结果" : "当前列表为空";

    const bar = $("acc-selection-bar");
    bar.classList.toggle("hidden", items.length === 0);
    $("acc-sel-count").textContent = `已选 ${state.accSelected.size} / ${items.length} 个视频`;
    $("acc-download-sel").disabled = state.accSelected.size === 0;

    grid.innerHTML = items
      .map((it) => {
        const sel = state.accSelected.has(it.bvid);
        return `
          <div class="video-tile${sel ? " selected" : ""}" data-bvid="${esc(it.bvid)}">
            <div class="vt-thumb">
              ${it.cover ? `<img src="${coverProxy(it.cover)}" loading="lazy" alt="">` : ""}
              <span class="vt-check">${sel ? "✓" : ""}</span>
              ${it.duration ? `<span class="dur-pill">${fmtDur(it.duration)}</span>` : ""}
            </div>
            <div class="vt-body">
              <div class="vt-title" title="${esc(it.title)}">${esc(it.title)}</div>
              <div class="vt-meta">${esc(it.owner || "")}${it.pages > 1 ? ` · ${it.pages}P` : ""}${it.view ? ` · ${fmtCount(it.view)} 播放` : ""}</div>
            </div>
          </div>`;
      })
      .join("");
  }

  async function downloadSelectedAccountVideos() {
    const bvids = [...state.accSelected];
    if (!bvids.length) return;
    const byId = new Map(state.accItems.map((it) => [it.bvid, it]));
    const tasks = bvids.map((bv) => {
      const it = byId.get(bv);
      return buildTaskPayload(`https://www.bilibili.com/video/${bv}`, {
        title: it?.title || bv,
        cover_url: it?.cover || "",
        media_type: "video",
      });
    });
    try {
      const res = await api("api/tasks", { method: "POST", body: JSON.stringify({ tasks }) });
      toast(`已将 ${(res.tasks || []).length} 个视频加入下载队列`, "ok");
      state.accSelected.clear();
      renderAccountGrid();
      await pollTasks();
      switchTab("queue");
    } catch (e) {
      toast("加入队列失败：" + e.message, "err");
    }
  }

  // ─── History Library ──────────────────────────────────────────────────

  async function loadHistory() {
    const q = $("history-search").value.trim();
    try {
      const d = await api("api/history?q=" + encodeURIComponent(q));
      const records = d.records || [];
      const stats = d.stats || {};
      $("hstat-total").textContent = String(stats.total_tasks || 0);
      $("hstat-done").textContent = String(stats.done_tasks || 0);
      $("hstat-bytes").textContent = fmtBytes(stats.total_bytes || 0);
      $("nav-history-count").textContent = stats.total_tasks ? String(stats.total_tasks) : "";

      $("history-empty").classList.toggle("hidden", records.length > 0);
      const isDesktop = Boolean(state.sys?.desktop_mode);

      $("history-list").innerHTML = records
        .map((r) => {
          const firstLocal = (r.files || []).find((f) => !f.startsWith("/") || isDesktop);
          const openTarget = firstLocal || r.output_dir;
          return `
            <div class="history-card">
              <div class="task-thumb">
                ${r.cover ? `<img src="${coverProxy(r.cover)}" loading="lazy" alt="">` : `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6"><polygon points="5 3 19 12 5 21 5 3"/></svg>`}
              </div>
              <div class="history-body">
                <div class="history-title">${esc(r.title)}</div>
                <div class="history-sub">
                  <span class="status-chip ${esc(r.status)}">${esc(STATUS_LABELS[r.status] || r.status)}</span>
                  <span>${esc(TYPE_LABELS[r.media_type] || r.media_type)}</span>
                  <span>${esc(r.quality || "")}</span>
                  ${r.total_bytes ? `<span>${fmtBytes(r.total_bytes)}</span>` : ""}
                  <span>${fmtTime(r.finished_at)}</span>
                </div>
              </div>
              <div class="btn-group">
                ${isDesktop && !r.remote_dir && openTarget ? `<button class="btn ghost xs" data-hact="open" data-path="${esc(openTarget)}">打开目录</button>` : ""}
                <button class="btn ghost xs" data-hact="reparse" data-url="${esc(r.url)}">重新解析</button>
                <button class="btn ghost xs danger-text" data-hact="del" data-id="${esc(r.id)}">删除</button>
              </div>
            </div>`;
        })
        .join("");
    } catch (e) {
      toast("加载历史失败：" + e.message, "err");
    }
  }

  // ─── Settings & Danmaku Preview ───────────────────────────────────────

  function applySettingsToForm() {
    const s = state.settings;
    if (!s) return;
    $("set-output-dir").value = s.output_dir || "";
    $("set-quality").value = s.quality || "1080P";
    $("set-codec").value = s.codec || "auto";
    $("set-workers").value = s.workers || 2;
    $("set-subtitle-mode").value = s.subtitle_mode || "best_zh";
    $("set-template").value = s.filename_template || "{title}";
    $("set-cdn-probe").checked = s.cdn_probe !== false;

    $("set-dm-area").value = s.danmaku_area ?? 0.55;
    $("set-dm-opacity").value = s.danmaku_opacity ?? 0.85;
    $("set-dm-scale").value = s.danmaku_font_scale ?? 1.0;
    $("set-dm-speed").value = s.danmaku_speed ?? 1.0;
    updateDanmakuPreview();
    updateTemplatePreview();

    $("set-cloud-enabled").checked = Boolean(s.cloudreve_enabled);
    $("set-cloud-url").value = s.cloudreve_url || "";
    $("set-cloud-user").value = s.cloudreve_username || "";
    $("set-cloud-pass").value = "";
    $("set-cloud-pass").placeholder = s.has_cloudreve_password ? "已配置密码（留空保持不变）" : "输入 WebDAV 密码";
    $("set-cloud-dir").value = s.cloudreve_remote_dir || "/视频/B站";
    renderDiagnostics();
  }

  function updateDanmakuPreview() {
    const area = parseFloat($("set-dm-area").value || "0.55");
    const opacity = parseFloat($("set-dm-opacity").value || "0.85");
    const scale = parseFloat($("set-dm-scale").value || "1.0");
    const speed = parseFloat($("set-dm-speed").value || "1.0");

    $("val-dm-area").textContent = Math.round(area * 100) + "%";
    $("val-dm-opacity").textContent = Math.round(opacity * 100) + "%";
    $("val-dm-scale").textContent = scale.toFixed(2) + "x";
    $("val-dm-speed").textContent = speed.toFixed(2) + "x";

    const box = $("dm-area-box");
    box.style.height = Math.round(area * 82) + "%";
    box.querySelectorAll(".dm-sample").forEach((el) => {
      el.style.opacity = String(opacity);
      el.style.fontSize = Math.round(14 * scale) + "px";
    });
  }

  function updateTemplatePreview() {
    const tpl = $("set-template").value.trim() || "{title}";
    const preview = tpl
      .replace(/\{index\}/g, "001")
      .replace(/\{title\}/g, "B站示例视频")
      .replace(/\{part\}/g, "第一讲")
      .replace(/\{owner\}/g, "UP主名称")
      .replace(/\{bvid\}/g, "BV1xx411c7mD")
      .replace(/\{quality\}/g, "1080P");
    $("set-template-preview").textContent = (preview || "B站示例视频") + ".mp4";
  }

  function renderDiagnostics() {
    const sys = state.sys;
    if (!sys) return;
    $("diag-list").innerHTML = [
      ["BiliDown 版本", `v${sys.version}`],
      ["运行模式", sys.desktop_mode ? "本地桌面模式" : "Cloudreve 服务器模式"],
      ["FFmpeg 状态", sys.ffmpeg_ok ? sys.ffmpeg_version || "已就绪" : "未检测到 ffmpeg，请安装后重启"],
      ["可用磁盘空间", `${sys.disk_free_gb} GB / ${sys.disk_total_gb} GB`],
      ["Cloudreve 状态", sys.cloudreve_configured ? "已配置 WebDAV" : "未启用"],
    ]
      .map(([k, v]) => `<div class="diag-row"><span>${esc(k)}</span><code>${esc(v)}</code></div>`)
      .join("");
  }

  async function saveAllSettings() {
    const payload = {
      output_dir: $("set-output-dir").value.trim(),
      quality: $("set-quality").value,
      codec: $("set-codec").value,
      workers: Math.max(1, parseInt($("set-workers").value || "2", 10)),
      subtitle_mode: $("set-subtitle-mode").value,
      filename_template: $("set-template").value.trim() || "{title}",
      cdn_probe: $("set-cdn-probe").checked,
      danmaku_area: parseFloat($("set-dm-area").value),
      danmaku_opacity: parseFloat($("set-dm-opacity").value),
      danmaku_font_scale: parseFloat($("set-dm-scale").value),
      danmaku_speed: parseFloat($("set-dm-speed").value),
      cloudreve_enabled: $("set-cloud-enabled").checked,
      cloudreve_url: $("set-cloud-url").value.trim(),
      cloudreve_username: $("set-cloud-user").value.trim(),
      cloudreve_password: $("set-cloud-pass").value,
      cloudreve_remote_dir: $("set-cloud-dir").value.trim() || "/视频/B站",
    };
    try {
      state.settings = await api("api/settings", { method: "POST", body: JSON.stringify(payload) });
      state.sys = await api("api/system/info");
      applySystemToUI();
      applySettingsToWorkspace();
      applySettingsToForm();
      toast("设置已保存并立即生效", "ok");
    } catch (e) {
      toast("保存失败：" + e.message, "err");
    }
  }

  async function testCloudreveConn() {
    $("test-cloud-status").textContent = "测试中...";
    try {
      const res = await api("api/settings/test-cloudreve", {
        method: "POST",
        body: JSON.stringify({
          cloudreve_url: $("set-cloud-url").value.trim(),
          cloudreve_username: $("set-cloud-user").value.trim(),
          cloudreve_password: $("set-cloud-pass").value,
        }),
      });
      $("test-cloud-status").textContent = "✓ " + res.message;
      toast("Cloudreve WebDAV 连接正常", "ok");
    } catch (e) {
      $("test-cloud-status").textContent = "✗ " + e.message;
      toast("连接失败：" + e.message, "err");
    }
  }

  async function pickFolderFor(inputId) {
    try {
      const res = await api("api/system/pick-folder", {
        method: "POST",
        body: JSON.stringify({ initial_dir: $(inputId).value.trim() }),
      });
      if (res.path) {
        $(inputId).value = res.path;
      }
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function openLocalPath(pathStr) {
    try {
      await api("api/system/open-path", { method: "POST", body: JSON.stringify({ path: pathStr }) });
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function playLocalFile(pathStr) {
    try {
      await api("api/system/play-file", { method: "POST", body: JSON.stringify({ path: pathStr }) });
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // ─── Event Bindings ───────────────────────────────────────────────────

  function bindEvents() {
    // Nav tabs
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.addEventListener("click", () => switchTab(b.dataset.tab))
    );
    document.querySelectorAll("[data-jump]").forEach((b) =>
      b.addEventListener("click", () => switchTab(b.dataset.jump))
    );

    // Theme
    $("theme-toggle").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme");
      applyTheme(cur === "dark" ? "light" : "dark");
    });

    // Account login modal
    $("account-btn").addEventListener("click", openLoginModal);
    $("acc-open-login").addEventListener("click", openLoginModal);
    $("qr-refresh-btn").addEventListener("click", refreshQr);
    $("cookie-save-btn").addEventListener("click", saveCookieLogin);
    $("logout-btn").addEventListener("click", logoutAccount);
    document.querySelectorAll("[data-close]").forEach((b) =>
      b.addEventListener("click", () => closeModal(b.dataset.close))
    );
    $("login-modal").addEventListener("click", (e) => {
      if (e.target.id === "login-modal") closeModal("login-modal");
    });

    // Hero Input
    $("inspect-btn").addEventListener("click", () => inspectUrl());
    $("url-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") inspectUrl();
    });
    $("url-input").addEventListener("input", () => {
      const val = $("url-input").value.trim();
      $("url-clear").classList.toggle("hidden", !val);
      if (state.inspectedUrl && val !== state.inspectedUrl) clearInspection();
    });
    $("url-clear").addEventListener("click", () => {
      $("url-input").value = "";
      $("url-clear").classList.add("hidden");
      clearInspection();
      $("url-input").focus();
    });
    $("paste-btn").addEventListener("click", async () => {
      try {
        const text = (await navigator.clipboard.readText()).trim();
        if (!text) {
          toast("剪贴板为空", "info");
          return;
        }
        inspectUrl(text);
      } catch (_) {
        $("url-input").focus();
        toast("请按 Ctrl+V 粘贴链接", "info");
      }
    });

    // Batch mode
    $("toggle-batch-btn").addEventListener("click", () => setBatchMode(!state.batchMode));
    $("batch-cancel-btn").addEventListener("click", () => setBatchMode(false));
    $("batch-textarea").addEventListener("input", updateBatchCount);
    $("batch-enqueue-btn").addEventListener("click", enqueueBatchLines);
    $("quick-watchlater-btn").addEventListener("click", () => inspectUrl("toview"));

    // Drag & Drop URL onto window
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => {
      e.preventDefault();
      const text = e.dataTransfer?.getData("text/plain")?.trim();
      if (text && /bilibili\.com|b23\.tv|BV[0-9A-Za-z]{10}/i.test(text)) {
        switchTab("workspace");
        inspectUrl(text);
      }
    });

    // Picker controls
    $("picker-search").addEventListener("input", (e) => {
      state.pickerQuery = e.target.value;
      state.pickerPage = 0;
      renderPickerList();
    });
    $("picker-range-apply").addEventListener("click", applyPickerRange);
    $("picker-range").addEventListener("keydown", (e) => {
      if (e.key === "Enter") applyPickerRange();
    });
    document.querySelectorAll("[data-pick]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const info = state.inspected;
        if (!info) return;
        const filtered = filteredPickerItems();
        const act = btn.dataset.pick;
        if (act === "all") {
          filtered.forEach((it) => state.selectedIds.add(String(it.id)));
        } else if (act === "none") {
          state.selectedIds.clear();
        } else if (act === "invert") {
          filtered.forEach((it) => {
            const sid = String(it.id);
            if (state.selectedIds.has(sid)) state.selectedIds.delete(sid);
            else state.selectedIds.add(sid);
          });
        } else if (act === "current" && info.current_item_id != null) {
          state.selectedIds = new Set([String(info.current_item_id)]);
        }
        renderPickerList();
      });
    });
    document.querySelectorAll("#picker-view-toggle .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        state.pickerView = b.dataset.view;
        document.querySelectorAll("#picker-view-toggle .seg-btn").forEach((x) =>
          x.classList.toggle("active", x === b)
        );
        renderPickerList();
      });
    });
    $("picker-prev").addEventListener("click", () => {
      state.pickerPage = Math.max(0, state.pickerPage - 1);
      renderPickerList();
    });
    $("picker-next").addEventListener("click", () => {
      state.pickerPage += 1;
      renderPickerList();
    });

    // Mode & Options
    document.querySelectorAll("#mode-segmented .seg-btn").forEach((b) =>
      b.addEventListener("click", () => setDownloadMode(b.dataset.mode))
    );
    $("opt-quality").addEventListener("change", updateSubmitSummary);
    $("opt-audio-fmt").addEventListener("change", updateSubmitSummary);
    $("opt-upload-cloud").addEventListener("change", syncDestinationFields);
    $("workers-dec").addEventListener("click", () => {
      $("opt-workers").value = Math.max(1, parseInt($("opt-workers").value || "2", 10) - 1);
    });
    $("workers-inc").addEventListener("click", () => {
      $("opt-workers").value = Math.min(32, parseInt($("opt-workers").value || "2", 10) + 1);
    });
    $("pick-folder-btn").addEventListener("click", () => pickFolderFor("opt-output-dir"));
    $("open-output-dir-btn").addEventListener("click", () => openLocalPath($("opt-output-dir").value.trim()));
    $("enqueue-btn").addEventListener("click", enqueueCurrent);
    $("wa-goto-queue").addEventListener("click", () => switchTab("queue"));

    // Queue actions (event delegation)
    $("queue-clear-done").addEventListener("click", async () => {
      const r = await api("api/tasks/clear", { method: "POST" });
      toast(`已清理 ${r.removed || 0} 条记录`, "info");
      pollTasks();
    });
    $("queue-list").addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      const id = btn.dataset.id;
      try {
        if (act === "toggle-ep") {
          state.openEpisodes.has(id) ? state.openEpisodes.delete(id) : state.openEpisodes.add(id);
          renderQueue();
        } else if (act === "toggle-log") {
          if (state.openLogs.has(id)) {
            state.openLogs.delete(id);
          } else {
            state.openLogs.add(id);
            state.logFollow.set(id, true);
          }
          renderQueue();
        } else if (act === "log-bottom") {
          state.logFollow.set(id, true);
          const card = btn.closest(".task-card");
          const pre = card?.querySelector(".log-console");
          if (pre) pre.scrollTop = pre.scrollHeight;
          const hint = card?.querySelector(".log-follow-hint");
          if (hint) hint.textContent = "自动跟随最新日志";
        } else if (act === "log-copy") {
          const card = btn.closest(".task-card");
          const text = card?.querySelector(".log-console")?.textContent || "";
          await navigator.clipboard.writeText(text);
          toast("日志已复制到剪贴板", "ok");
        } else if (act === "stop") {
          await api(`api/tasks/${encodeURIComponent(id)}/stop`, { method: "POST" });
          toast("已发送停止信号", "info");
          pollTasks();
        } else if (act === "retry") {
          await api(`api/tasks/${encodeURIComponent(id)}/retry`, { method: "POST" });
          toast("已创建失败项重试任务", "ok");
          pollTasks();
        } else if (act === "remove") {
          await api(`api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
          pollTasks();
        } else if (act === "open-dir") {
          await openLocalPath(btn.dataset.path);
        } else if (act === "play") {
          await playLocalFile(btn.dataset.path);
        }
      } catch (err) {
        toast(err.message, "err");
      }
    });

    // Account tab
    document.querySelectorAll("#acc-subtabs .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        state.accSub = b.dataset.sub;
        document.querySelectorAll("#acc-subtabs .seg-btn").forEach((x) =>
          x.classList.toggle("active", x === b)
        );
        loadAccountTab();
      });
    });
    $("acc-refresh").addEventListener("click", loadAccountTab);
    $("acc-search").addEventListener("input", renderAccountGrid);
    $("fav-folder-bar").addEventListener("click", (e) => {
      const pill = e.target.closest("[data-fid]");
      if (pill) loadFavFolderItems(Number(pill.dataset.fid));
    });
    $("acc-grid").addEventListener("click", (e) => {
      const tile = e.target.closest(".video-tile");
      if (!tile) return;
      const bv = tile.dataset.bvid;
      state.accSelected.has(bv) ? state.accSelected.delete(bv) : state.accSelected.add(bv);
      renderAccountGrid();
    });
    $("acc-sel-all").addEventListener("click", () => {
      filteredAccItems().forEach((it) => state.accSelected.add(it.bvid));
      renderAccountGrid();
    });
    $("acc-sel-none").addEventListener("click", () => {
      state.accSelected.clear();
      renderAccountGrid();
    });
    $("acc-download-sel").addEventListener("click", downloadSelectedAccountVideos);

    // History tab
    let hTimer = null;
    $("history-search").addEventListener("input", () => {
      clearTimeout(hTimer);
      hTimer = setTimeout(loadHistory, 220);
    });
    $("history-clear-btn").addEventListener("click", async () => {
      if (!confirm("确定清空所有下载历史记录吗？（不会删除已下载的本地文件）")) return;
      await api("api/history", { method: "DELETE" });
      toast("历史记录已清空", "info");
      loadHistory();
    });
    $("history-list").addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-hact]");
      if (!btn) return;
      const act = btn.dataset.hact;
      if (act === "open") {
        await openLocalPath(btn.dataset.path);
      } else if (act === "reparse") {
        switchTab("workspace");
        inspectUrl(btn.dataset.url);
      } else if (act === "del") {
        await api(`api/history/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
        loadHistory();
      }
    });

    // Settings tab
    ["set-dm-area", "set-dm-opacity", "set-dm-scale", "set-dm-speed"].forEach((id) =>
      $(id).addEventListener("input", updateDanmakuPreview)
    );
    $("set-template").addEventListener("input", updateTemplatePreview);
    $("set-pick-folder").addEventListener("click", () => pickFolderFor("set-output-dir"));
    $("save-settings-btn").addEventListener("click", saveAllSettings);
    $("test-cloud-btn").addEventListener("click", testCloudreveConn);

    // Keyboard shortcuts & visibility sync
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal("login-modal");
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) pollTasks();
    });
  }

  bindEvents();
  bootstrap();
})();
