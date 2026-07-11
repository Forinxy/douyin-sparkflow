(() => {
  const root = document.documentElement;
  const storageKey = "sparkflow-theme";

  const storedTheme = () => {
    try {
      return localStorage.getItem(storageKey);
    } catch {
      return null;
    }
  };

  const applyTheme = (theme) => {
    const value = theme === "light" ? "light" : "dark";
    root.dataset.theme = value;
    root.style.colorScheme = value;
    try {
      localStorage.setItem(storageKey, value);
    } catch {
      // The active page can still switch themes when storage is unavailable.
    }
  };

  applyTheme(storedTheme() || "dark");
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      applyTheme(root.dataset.theme === "light" ? "dark" : "light");
    });
  });
})();

(() => {
  const body = document.body;
  document.querySelectorAll("[data-nav-toggle]").forEach((button) => {
    button.addEventListener("click", () => body.classList.add("nav-open"));
  });
  document.querySelectorAll("[data-nav-close]").forEach((button) => {
    button.addEventListener("click", () => body.classList.remove("nav-open"));
  });
  document.querySelectorAll(".nav-item").forEach((link) => {
    link.addEventListener("click", () => body.classList.remove("nav-open"));
  });
})();

(() => {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog) return;
  const title = document.getElementById("confirm-title");
  const message = document.getElementById("confirm-message");
  const accept = dialog.querySelector("[data-confirm-accept]");
  const cancel = dialog.querySelector("[data-confirm-cancel]");
  let pendingForm = null;
  let pendingLink = "";
  let pendingButton = null;

  const openDialog = (node) => {
    const source = node.closest("[data-confirm]") || node;
    title.textContent = source.dataset.confirmTitle || "确认操作";
    message.textContent =
      source.dataset.confirm ||
      "该操作会立即影响续火花任务，请确认是否继续。";
    accept.textContent = source.dataset.confirmAccept || "确认执行";
    accept.className =
      source.dataset.confirmTone === "primary"
        ? "button button-primary"
        : "button button-danger";
    dialog.showModal();
  };

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      pendingForm = form;
      pendingLink = "";
      pendingButton = null;
      openDialog(form);
    });
  });

  document.querySelectorAll("a[data-confirm]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      pendingForm = null;
      pendingLink = link.href;
      pendingButton = null;
      openDialog(link);
    });
  });

  document.querySelectorAll("button[data-confirm]").forEach((button) => {
    button.addEventListener(
      "click",
      (event) => {
        if (button.dataset.confirmApproved === "1") {
          delete button.dataset.confirmApproved;
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        pendingForm = null;
        pendingLink = "";
        pendingButton = button;
        openDialog(button);
      },
      true,
    );
  });

  cancel.addEventListener("click", () => {
    pendingForm = null;
    pendingLink = "";
    pendingButton = null;
    dialog.close();
  });

  accept.addEventListener("click", () => {
    const form = pendingForm;
    const href = pendingLink;
    const button = pendingButton;
    pendingForm = null;
    pendingLink = "";
    pendingButton = null;
    dialog.close();
    if (form) {
      HTMLFormElement.prototype.submit.call(form);
    } else if (href) {
      window.location.assign(href);
    } else if (button) {
      button.dataset.confirmApproved = "1";
      button.click();
    }
  });

  dialog.addEventListener("cancel", () => {
    pendingForm = null;
    pendingLink = "";
    pendingButton = null;
  });
})();

(() => {
  document.querySelectorAll("[data-segment-group]").forEach((group) => {
    const buttons = [...group.querySelectorAll("[data-segment-target]")];
    const owner = group.closest("[data-segment-owner]") || document;
    const panels = [...owner.querySelectorAll("[data-segment-panel]")];
    const activate = (name) => {
      buttons.forEach((button) => {
        const active = button.dataset.segmentTarget === name;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.segmentPanel !== name;
      });
    };
    buttons.forEach((button) => {
      button.addEventListener("click", () =>
        activate(button.dataset.segmentTarget),
      );
    });
    const initial =
      buttons.find((button) => button.classList.contains("active")) ||
      buttons[0];
    if (initial) activate(initial.dataset.segmentTarget);
  });
})();

(() => {
  const overviewRoots = document.querySelectorAll("[data-overview-root]");
  if (!overviewRoots.length) return;
  let previousRunning = null;
  let timer = null;

  const formatTime = (raw) => {
    if (!raw) return "-";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw;
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed);
  };

  const setText = (selector, value) => {
    document.querySelectorAll(selector).forEach((node) => {
      node.textContent = String(value ?? "");
    });
  };

  const updateTaskBanner = (task) => {
    document.querySelectorAll("[data-task-banner]").forEach((banner) => {
      banner.className = "status-banner";
      if (task.running) {
        banner.classList.add("warning");
        banner.querySelector("[data-task-text]").textContent =
          `发送任务运行中，已运行约 ${task.ageSeconds || 0} 秒`;
      } else if (task.stale) {
        banner.classList.add("info");
        banner.querySelector("[data-task-text]").textContent =
          "检测到过期任务锁，下次启动任务时会自动清理";
      } else {
        banner.classList.add("success");
        banner.querySelector("[data-task-text]").textContent =
          "当前没有发送任务运行";
      }
    });
  };

  const updateAccounts = (accounts) => {
    accounts.forEach((account) => {
      const selector = `[data-account-overview="${CSS.escape(account.uniqueId)}"]`;
      document.querySelectorAll(selector).forEach((row) => {
        row.dataset.accountState = account.state;
        row.querySelectorAll("[data-account-confirmed]").forEach((node) => {
          node.textContent = account.confirmed;
        });
        row.querySelectorAll("[data-account-attention]").forEach((node) => {
          node.textContent = account.attention;
        });
        row.querySelectorAll("[data-account-pending]").forEach((node) => {
          node.textContent = account.pending;
        });
        row.querySelectorAll("[data-account-progress]").forEach((node) => {
          const pct = account.total
            ? Math.round((account.confirmed / account.total) * 100)
            : 0;
          node.style.width = `${pct}%`;
        });
        row.querySelectorAll("[data-account-progress-text]").forEach((node) => {
          node.textContent = `${account.confirmed}/${account.total}`;
        });
      });
    });
  };

  const updateActions = (summary, running) => {
    const counts = {
      attention: summary.attention,
      pending: summary.pending + summary.unprocessed,
      total: summary.total,
    };
    document.querySelectorAll("[data-action-count-source]").forEach((button) => {
      const count = counts[button.dataset.actionCountSource] || 0;
      button.disabled = running || count <= 0;
      const countNode = button.querySelector("[data-action-count]");
      if (countNode) countNode.textContent = count;
    });
    document.querySelectorAll("[data-disable-while-running]").forEach((button) => {
      if (!button.dataset.actionCountSource) {
        button.disabled = running;
      }
    });
  };

  const render = (data) => {
    const summary = data.summary || {};
    const task = data.task || {};
    updateTaskBanner(task);
    setText("[data-overview-value='total']", summary.total || 0);
    setText("[data-overview-value='confirmed']", summary.confirmed || 0);
    setText("[data-overview-value='attention']", summary.attention || 0);
    setText(
      "[data-overview-value='pending']",
      (summary.pending || 0) + (summary.unprocessed || 0),
    );
    setText("[data-overview-value='remaining']", summary.remaining || 0);
    setText(
      "[data-overview-value='progress']",
      `${summary.confirmed || 0}/${summary.total || 0}`,
    );
    setText(
      "[data-overview-value='progressPercent']",
      summary.total
        ? `${Math.round((summary.confirmed / summary.total) * 100)}%`
        : "0%",
    );
    setText(
      "[data-overview-value='lastConfirmedAt']",
      formatTime(summary.lastConfirmedAt),
    );
    setText(
      "[data-overview-value='nextTriggerAt']",
      formatTime(data.schedule?.nextTriggerAt),
    );
    setText(
      "[data-overview-value='scheduleLabel']",
      data.schedule?.label || "-",
    );
    updateAccounts(data.accounts || []);
    updateActions(summary, Boolean(task.running));

    if (previousRunning === true && !task.running) {
      document
        .querySelectorAll("[data-refresh-notice]")
        .forEach((node) => node.classList.add("visible"));
    }
    previousRunning = Boolean(task.running);
    document.querySelectorAll("[data-overview-live-state]").forEach((node) => {
      node.textContent = "实时";
      node.classList.remove("poll-stale");
    });
  };

  const refresh = async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const response = await fetch("/api/ops/overview", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch {
      document.querySelectorAll("[data-overview-live-state]").forEach((node) => {
        node.textContent = "更新延迟";
        node.classList.add("poll-stale");
      });
    }
  };

  document.querySelectorAll("[data-refresh-page]").forEach((button) => {
    button.addEventListener("click", () => window.location.reload());
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
  refresh();
  timer = window.setInterval(refresh, 10000);
  window.addEventListener("pagehide", () => window.clearInterval(timer));
})();

(() => {
  const root = document.getElementById("login-desktop-controls");
  if (!root) return;
  const section = document.getElementById("interactive-login-section");
  const csrfToken = root.dataset.csrfToken || "";
  const publicUrl = root.dataset.publicUrl || "";
  const runtimeState = document.getElementById(
    "login-desktop-runtime-state",
  );
  const statusText = document.getElementById("login-desktop-status-text");
  const frame = document.querySelector("[data-login-frame]");
  let timer = null;

  const setStatus = (text, tone = "") => {
    if (statusText) statusText.textContent = text;
    if (runtimeState) {
      runtimeState.className = `pill${tone ? ` ${tone}` : ""}`;
      runtimeState.textContent =
        tone === "success" ? "已登录" : tone === "danger" ? "异常" : "待登录";
    }
  };

  const postForm = async (url, payload = {}) => {
    const formData = new FormData();
    formData.set("csrf_token", csrfToken);
    Object.entries(payload).forEach(([key, value]) => {
      formData.set(key, String(value ?? ""));
    });
    const response = await fetch(url, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `请求失败：${response.status}`);
    }
    return data;
  };

  const loadFrame = () => {
    if (frame && frame.dataset.loaded !== "1" && frame.dataset.src) {
      frame.src = frame.dataset.src;
      frame.dataset.loaded = "1";
    }
  };

  const pollStatus = async () => {
    if (document.visibilityState !== "visible" || (section && !section.open)) {
      return;
    }
    try {
      const response = await fetch("/login-desktop/status", {
        credentials: "same-origin",
        cache: "no-store",
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        setStatus(
          data.error || "登录工作区不可用，请检查 login-desktop 服务。",
          "danger",
        );
        return;
      }
      if (data.logged_in) {
        setStatus(`当前浏览器已登录：${data.username}`, "success");
      } else {
        setStatus("当前浏览器尚未登录，可打开工作区开始人工登录。");
      }
    } catch (error) {
      setStatus(`状态检查失败：${error.message}`, "danger");
    }
  };

  document.querySelectorAll(".login-desktop-open").forEach((button) => {
    button.addEventListener("click", async () => {
      // Mobile browsers block window.open after an awaited request. Open the
      // authenticated same-origin workspace while the click gesture is active.
      const popup = publicUrl
        ? window.open(publicUrl, "_blank", "noopener")
        : null;
      try {
        await postForm("/login-desktop/open");
        loadFrame();
        if (!popup && frame) {
          frame.scrollIntoView({ behavior: "smooth", block: "start" });
          setStatus("???????????????????????");
        } else {
          setStatus("????????????????????????");
        }
      } catch (error) {
        setStatus(`??????????${error.message}`, "danger");
      }
    });
  });

  document.querySelectorAll(".login-desktop-save").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const data = await postForm("/login-desktop/save", {
          relogin_unique_id: button.dataset.reloginUniqueId || "",
        });
        setStatus(`已保存登录账号：${data.account?.username || ""}`, "success");
        window.setTimeout(() => window.location.reload(), 800);
      } catch (error) {
        setStatus(`保存登录账号失败：${error.message}`, "danger");
      }
    });
  });

  document.querySelectorAll(".login-desktop-reset").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await postForm("/login-desktop/reset");
        setStatus("登录工作区已重置，正在重新初始化。");
        await pollStatus();
      } catch (error) {
        setStatus(`重置登录工作区失败：${error.message}`, "danger");
      }
    });
  });

  document.querySelectorAll("[data-copy-login-url]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(publicUrl);
        setStatus("登录工作区地址已复制。", "success");
      } catch (error) {
        setStatus(`复制失败：${error.message}`, "danger");
      }
    });
  });

  if (section) {
    section.addEventListener("toggle", () => {
      if (section.open) {
        loadFrame();
        pollStatus();
      }
    });
  }
  timer = window.setInterval(pollStatus, 5000);
  window.addEventListener("pagehide", () => window.clearInterval(timer));
})();

(() => {
  const parseJson = (id) => {
    const node = document.getElementById(id);
    if (!node) return [];
    try {
      return JSON.parse(node.textContent || "[]");
    } catch {
      return [];
    }
  };

  document.querySelectorAll(".friend-picker").forEach((picker) => {
    const accountId = picker.dataset.accountId;
    const refreshUrl = picker.dataset.refreshUrl;
    const csrfToken = picker.dataset.csrfToken;
    const form = picker.closest("form");
    const textarea = form?.querySelector(".targets-textarea");
    const search = picker.querySelector(".friend-search-input");
    const refreshButton = picker.querySelector(".friend-refresh-button");
    const list = picker.querySelector(".friend-picker-list");
    const summary = picker.querySelector(".friend-picker-summary");
    const status = picker.querySelector(".friend-picker-status");
    let friends = parseJson(`friends-cache-${accountId}`);
    let selected = new Set(parseJson(`selected-targets-${accountId}`));

    const parseTargets = (value) =>
      [...new Set(
        String(value || "")
          .replaceAll(",", "\n")
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean),
      )];

    const combined = () => [...new Set([...selected, ...friends])];

    const syncTextarea = () => {
      if (textarea) textarea.value = [...selected].join("\n");
    };

    const render = () => {
      const query = String(search?.value || "").trim().toLowerCase();
      const names = combined().filter((name) =>
        name.toLowerCase().includes(query),
      );
      if (summary) summary.textContent = `已选 ${selected.size} 人`;
      list.innerHTML = "";
      if (!names.length) {
        const empty = document.createElement("div");
        empty.className = "friend-picker-empty";
        empty.textContent = combined().length
          ? "没有匹配的好友。"
          : "点击“刷新好友列表”后再选择目标。";
        list.appendChild(empty);
        return;
      }
      names.forEach((name) => {
        const label = document.createElement("label");
        label.className = `friend-option${selected.has(name) ? " selected" : ""}`;
        const text = document.createElement("span");
        text.textContent = name;
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selected.has(name);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.add(name);
          else selected.delete(name);
          syncTextarea();
          render();
        });
        label.append(text, checkbox);
        list.appendChild(label);
      });
    };

    textarea?.addEventListener("input", () => {
      selected = new Set(parseTargets(textarea.value));
      render();
    });
    search?.addEventListener("input", render);
    refreshButton?.addEventListener("click", async () => {
      refreshButton.disabled = true;
      if (status) status.textContent = "正在读取好友列表...";
      try {
        const formData = new FormData();
        formData.set("csrf_token", csrfToken);
        const response = await fetch(refreshUrl, {
          method: "POST",
          body: formData,
          credentials: "same-origin",
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "刷新失败");
        friends = data.friends || [];
        if (status) status.textContent = data.message || "好友列表已刷新";
        render();
      } catch (error) {
        if (status) status.textContent = `刷新失败：${error.message}`;
      } finally {
        refreshButton.disabled = false;
      }
    });
    render();
  });
})();

window.addEventListener("DOMContentLoaded", () => {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
  }
});
