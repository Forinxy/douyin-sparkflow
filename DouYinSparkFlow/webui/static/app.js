(() => {
  const body = document.body;
  const navToggle = document.querySelector("[data-nav-toggle]");
  const navClose = document.querySelector("[data-nav-close]");

  navToggle?.addEventListener("click", () => body.classList.toggle("nav-open"));
  navClose?.addEventListener("click", () => body.classList.remove("nav-open"));
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => body.classList.remove("nav-open"));
  });
})();

(() => {
  document.querySelectorAll("[data-confirm]").forEach((node) => {
    const message = node.getAttribute("data-confirm") || "确认执行此操作？";
    const handler = (event) => {
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    if (node.tagName === "FORM") {
      node.addEventListener("submit", handler);
    } else {
      node.addEventListener("click", handler);
    }
  });
})();

(() => {
  document.querySelectorAll(".data-card__footer-button").forEach((button) => {
    button.addEventListener("click", () => {
      const targetId = button.dataset.expandTarget;
      if (!targetId) return;
      const panel = document.getElementById(targetId);
      if (!panel) return;
      const isOpen = panel.classList.toggle("is-open");
      button.textContent = isOpen
        ? "收起"
        : button.dataset.totalLabel || "查看全部";
    });
  });
})();

(() => {
  const root = document.getElementById("login-desktop-controls");
  if (!root) return;

  const csrfToken = root.dataset.csrfToken || "";
  const publicUrl = root.dataset.publicUrl || "";
  const runtimeStateEl = document.getElementById("login-desktop-runtime-state");
  const statusTextEl = document.getElementById("login-desktop-status-text");
  const openButtons = document.querySelectorAll(".login-desktop-open");
  const saveButtons = document.querySelectorAll(".login-desktop-save");
  const resetButtons = document.querySelectorAll(".login-desktop-reset");
  const copyPublicUrlButton = document.getElementById("copy-public-url");
  const statusMap = {
    checking: document.getElementById("desktop-status-checking"),
    pending: document.getElementById("desktop-status-pending"),
    success: document.getElementById("desktop-status-success"),
    error: document.getElementById("desktop-status-error"),
  };

  const setVisualStatus = (state) => {
    Object.values(statusMap).forEach((node) => {
      if (!node) return;
      node.style.opacity = "0.46";
    });
    if (statusMap[state]) {
      statusMap[state].style.opacity = "1";
    }
  };

  const setStatus = (text, tone = "", state = "checking") => {
    if (statusTextEl) statusTextEl.textContent = text;
    if (runtimeStateEl) {
      runtimeStateEl.className = `pill${tone ? ` ${tone}` : ""}`;
    }
    setVisualStatus(state);
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

  const openDesktopWindow = () => {
    const popup = window.open(publicUrl, "_blank");
    if (!popup) {
      setStatus(
        "浏览器阻止了登录工作区弹窗，请允许弹窗后再试。",
        "danger",
        "error",
      );
      return false;
    }
    return true;
  };

  const pollStatus = async () => {
    try {
      const response = await fetch("/login-desktop/status", {
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        if (runtimeStateEl) runtimeStateEl.textContent = "不可用";
        setStatus(
          data.error || "登录工作区不可用，请检查 login-desktop 服务。",
          "warning",
          "error",
        );
        return;
      }
      if (runtimeStateEl)
        runtimeStateEl.textContent = data.logged_in ? "已登录" : "待登录";
      if (data.logged_in) {
        setStatus(
          `当前浏览器已登录：${data.username}（${data.unique_id}）`,
          "success",
          "success",
        );
      } else {
        setStatus(
          "当前浏览器尚未登录，可打开登录工作区开始人工登录。",
          "",
          "pending",
        );
      }
    } catch (error) {
      if (runtimeStateEl) runtimeStateEl.textContent = "异常";
      setStatus(`登录工作区状态检查失败：${error.message}`, "danger", "error");
    }
  };

  copyPublicUrlButton?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(publicUrl);
      setStatus("登录工作区地址已复制。", "success", "pending");
    } catch (error) {
      setStatus(`复制失败：${error.message}`, "warning", "error");
    }
  });

  openButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const reloginUniqueId = String(
        button.dataset.reloginUniqueId || "",
      ).trim();
      const accountName = String(button.dataset.accountName || "").trim();
      try {
        await postForm("/login-desktop/open");
        openDesktopWindow();
        setStatus(
          reloginUniqueId
            ? `请使用账号 ${accountName || reloginUniqueId} 完成登录，然后保存登录态。`
            : "请在远端浏览器中完成抖音创作者中心登录，然后保存账号。",
          "",
          "pending",
        );
      } catch (error) {
        setStatus(`打开登录工作区失败：${error.message}`, "danger", "error");
      }
    });
  });

  saveButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const reloginUniqueId = String(
        button.dataset.reloginUniqueId || "",
      ).trim();
      const accountName = String(button.dataset.accountName || "").trim();
      try {
        const data = await postForm("/login-desktop/save", {
          relogin_unique_id: reloginUniqueId,
        });
        setStatus(
          reloginUniqueId
            ? `已把当前浏览器登录保存到账号：${accountName || reloginUniqueId}`
            : `已保存当前登录账号：${data.account?.username || ""}`,
          "success",
          "success",
        );
        window.setTimeout(() => window.location.reload(), 900);
      } catch (error) {
        setStatus(`保存当前登录账号失败：${error.message}`, "danger", "error");
      }
    });
  });

  resetButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await postForm("/login-desktop/reset");
        setStatus(
          "登录工作区已重置，正在重新初始化浏览器。",
          "warning",
          "checking",
        );
        await pollStatus();
      } catch (error) {
        setStatus(`重置登录工作区失败：${error.message}`, "danger", "error");
      }
    });
  });

  setVisualStatus("checking");
  pollStatus();
  window.setInterval(pollStatus, 5000);
})();

(() => {
  const pickers = document.querySelectorAll(".friend-picker");
  if (!pickers.length) return;

  const parseJsonScript = (id) => {
    const el = document.getElementById(id);
    if (!el) return [];
    try {
      return JSON.parse(el.textContent || "[]");
    } catch (error) {
      console.error("Failed to parse friend picker JSON", id, error);
      return [];
    }
  };

  const escapeHtml = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  pickers.forEach((picker) => {
    const accountId = picker.dataset.accountId;
    const refreshUrl = picker.dataset.refreshUrl;
    const csrfToken = picker.dataset.csrfToken;
    const searchInput = picker.querySelector(".friend-search-input");
    const refreshButton = picker.querySelector(".friend-refresh-button");
    const listEl = picker.querySelector(".friend-picker-list");
    const summaryEl = picker.querySelector(".friend-picker-summary");
    const statusEl = picker.querySelector(".friend-picker-status");
    const hiddenInputsEl = picker.querySelector(".friend-selected-inputs");
    const formEl = picker.closest("form");
    const targetsTextarea = formEl?.querySelector(".targets-textarea");
    const currentTargetsEl = picker.querySelector(
      ".friend-picker-current-targets span",
    );

    let friends = parseJsonScript(`friends-cache-${accountId}`);
    let selected = new Set(parseJsonScript(`selected-targets-${accountId}`));

    const splitTargetText = (value) => {
      const seen = new Set();
      return String(value || "")
        .replaceAll(",", "\n")
        .split(/\r?\n/)
        .map((name) => name.trim())
        .filter((name) => {
          if (!name || seen.has(name)) return false;
          seen.add(name);
          return true;
        });
    };

    const combinedFriends = () => {
      const merged = [];
      const seen = new Set();
      [...selected, ...friends].forEach((name) => {
        if (!name || seen.has(name)) return;
        seen.add(name);
        merged.push(name);
      });
      return merged;
    };

    const syncTextareaFromSelected = () => {
      if (targetsTextarea) {
        targetsTextarea.value = [...selected].join("\n");
      }
    };

    const syncSelectedFromTextarea = () => {
      if (targetsTextarea) {
        selected = new Set(splitTargetText(targetsTextarea.value));
      }
    };

    const renderHiddenInputs = () => {
      hiddenInputsEl.innerHTML = "";
      [...selected].forEach((name) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "targets";
        input.value = name;
        hiddenInputsEl.appendChild(input);
      });
    };

    const updateSummary = () => {
      summaryEl.textContent = `已选 ${selected.size} 人`;
      if (currentTargetsEl) {
        currentTargetsEl.textContent = selected.size
          ? [...selected].join("、")
          : "未选择";
      }
    };

    const renderList = () => {
      const query = (searchInput.value || "").trim().toLowerCase();
      const allNames = combinedFriends();
      const displayNames = allNames.filter((name) =>
        name.toLowerCase().includes(query),
      );
      renderHiddenInputs();
      updateSummary();

      if (!allNames.length) {
        listEl.innerHTML =
          '<div class="friend-picker-empty">点击“刷新好友列表”后再勾选目标好友。</div>';
        return;
      }

      if (!displayNames.length) {
        listEl.innerHTML =
          '<div class="friend-picker-empty">没有匹配的好友。</div>';
        return;
      }

      listEl.innerHTML = displayNames
        .map(
          (name) => `
          <label class="friend-option ${selected.has(name) ? "selected" : ""}">
            <span>${escapeHtml(name)}</span>
            <input type="checkbox" value="${escapeHtml(name)}" ${selected.has(name) ? "checked" : ""}>
          </label>
        `,
        )
        .join("");

      listEl.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
          const option = checkbox.closest(".friend-option");
          const value = checkbox.value;
          if (checkbox.checked) {
            selected.add(value);
            option?.classList.add("selected");
          } else {
            selected.delete(value);
            option?.classList.remove("selected");
          }
          syncTextareaFromSelected();
          renderHiddenInputs();
          updateSummary();
        });
      });
    };

    refreshButton.addEventListener("click", async () => {
      refreshButton.disabled = true;
      const originalText = refreshButton.textContent;
      refreshButton.textContent = "刷新中...";
      statusEl.textContent = "正在读取好友列表...";
      try {
        const formData = new FormData();
        formData.set("csrf_token", csrfToken);
        const response = await fetch(refreshUrl, {
          method: "POST",
          body: formData,
          credentials: "same-origin",
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || "刷新好友列表失败");
        }
        friends = Array.isArray(payload.friends) ? payload.friends : [];
        statusEl.textContent =
          payload.message || `已刷新 ${friends.length} 个好友`;
        renderList();
      } catch (error) {
        statusEl.textContent = error.message || "刷新好友列表失败";
      } finally {
        refreshButton.disabled = false;
        refreshButton.textContent = originalText;
      }
    });

    searchInput.addEventListener("input", renderList);
    targetsTextarea?.addEventListener("input", () => {
      syncSelectedFromTextarea();
      renderList();
    });

    syncSelectedFromTextarea();
    renderList();
  });
})();
