// 共通スクリプト: 軽量な強化のみ。SSR を壊さない progressive enhancement。
// - 週ストリップ: 初期表示で当日カードを中央にスクロール
// - 買い物リスト: チェックボックスを楽観的に切替（POST は通常送信に任せる）
// - reveal: IntersectionObserver で .is-stagger 要素を順次出現
// - reduced motion / 古いブラウザ には自動でフォールバック

(function () {
  "use strict";

  const prefersReducedMotion =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false;

  // ---- 1. 当日カードへ初期スクロール (横スクロール時のみ) -------------------
  function scrollWeekStripToToday() {
    document.querySelectorAll("[data-week-strip]").forEach((rail) => {
      // 横スクロール状態かをジオメトリで判定
      if (rail.scrollWidth <= rail.clientWidth + 1) return;
      const today = rail.querySelector('[data-today="true"]');
      if (!today) return;
      try {
        today.scrollIntoView({
          inline: "center",
          block: "nearest",
          behavior: prefersReducedMotion ? "auto" : "smooth",
        });
      } catch (_) {
        today.scrollIntoView();
      }
    });
  }

  // ---- 2. リビール (IntersectionObserver) ----------------------------------
  function setupReveal() {
    if (prefersReducedMotion || !("IntersectionObserver" in window)) {
      document.querySelectorAll(".is-stagger").forEach((el) => el.classList.add("is-in"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.08, rootMargin: "0px 0px -10% 0px" }
    );
    document.querySelectorAll(".is-stagger").forEach((el, idx) => {
      el.style.transitionDelay = Math.min(idx * 60, 360) + "ms";
      io.observe(el);
    });
  }

  // ---- 3. アクセシブルなメニュー / details 折りたたみのキーボード操作補助 ---
  // (現状ネイティブ details の挙動で十分なので軽く)
  function setupCheckRows() {
    // チェック行はフォーム送信のため click は browser に任せる
    // 楽観的 UI 更新は controller 側の form submit before に効果を出す
    document.querySelectorAll("[data-checkable]").forEach((row) => {
      const form = row.querySelector('form[data-action="toggle"]');
      if (!form) return;
      form.addEventListener("submit", () => {
        const checked = row.getAttribute("data-checked") === "true";
        row.setAttribute("data-checked", checked ? "false" : "true");
      });
    });
  }

  // ---- 4. テーマ切替 (system → light → dark → system) ----------------------
  const THEME_STORAGE_KEY = "theme";

  const THEME_ICONS = {
    auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
    light:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><path d="M21 13a9 9 0 1 1-10-10 7 7 0 0 0 10 10z"/></svg>',
  };

  const THEME_LABELS = {
    auto: "テーマ: システムに従う",
    light: "テーマ: ライト",
    dark: "テーマ: ダーク",
  };

  function readStoredTheme() {
    try {
      const v = localStorage.getItem(THEME_STORAGE_KEY);
      if (v === "light" || v === "dark") return v;
    } catch (_) {}
    return "auto";
  }

  function applyTheme(mode) {
    if (mode === "auto") {
      delete document.documentElement.dataset.theme;
      try {
        localStorage.removeItem(THEME_STORAGE_KEY);
      } catch (_) {}
    } else {
      document.documentElement.dataset.theme = mode;
      try {
        localStorage.setItem(THEME_STORAGE_KEY, mode);
      } catch (_) {}
    }
  }

  function updateThemeButton(btn, mode) {
    const iconHost = btn.querySelector("[data-theme-icon]");
    const labelHost = btn.querySelector("[data-theme-label]");
    if (iconHost) iconHost.innerHTML = THEME_ICONS[mode];
    if (labelHost) labelHost.textContent = THEME_LABELS[mode];
    btn.setAttribute("title", THEME_LABELS[mode] + " (タップで切替)");
    btn.setAttribute("aria-label", THEME_LABELS[mode] + ", クリックで切替");
  }

  function setupThemeToggle() {
    const btns = document.querySelectorAll("[data-theme-toggle]");
    if (!btns.length) return;
    let current = readStoredTheme();
    btns.forEach((btn) => updateThemeButton(btn, current));

    btns.forEach((btn) => {
      btn.addEventListener("click", () => {
        current = current === "auto" ? "light" : current === "light" ? "dark" : "auto";
        applyTheme(current);
        btns.forEach((b) => updateThemeButton(b, current));
      });
    });
  }

  // ---- 5. 起動 -------------------------------------------------------------
  function ready(cb) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", cb, { once: true });
    } else {
      cb();
    }
  }

  // ---- 6. アレルゲンチェックボックスのグループ一括切替 ---------------------
  function setupAllergenGroupToggles() {
    document.querySelectorAll("[data-allergen-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetId = btn.getAttribute("data-target");
        const mode = btn.getAttribute("data-allergen-toggle");
        if (!targetId) return;
        const scope = document.querySelector(`[data-allergen-group="${targetId}"]`);
        if (!scope) return;
        const inputs = scope.querySelectorAll('input[type="checkbox"][name="allergens"]');
        inputs.forEach((cb) => {
          cb.checked = mode === "all";
        });
      });
    });
  }

  // ---- 7. 生成中カードのポーリング (data-poll-url) --------------------------
  // meta refresh による全画面リロードの代わりに, JSON ステータスを軽量ポーリング
  // し, 完了時のみ 1 回だけリロードする。
  const POLL_INTERVAL_MS = 3000;
  const POLL_TIMEOUT_MS = 10 * 60 * 1000;

  function formatElapsed(ms) {
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return minutes > 0 ? `経過 ${minutes} 分 ${seconds} 秒` : `経過 ${seconds} 秒`;
  }

  function setupPolling() {
    document.querySelectorAll("[data-poll-url]").forEach((card) => {
      const url = card.getAttribute("data-poll-url");
      if (!url) return;

      const startedAt = Date.now();
      let active = true;

      function poll() {
        if (!active) return;
        if (Date.now() - startedAt >= POLL_TIMEOUT_MS) {
          active = false;
          const message = card.querySelector("[data-poll-timeout-message]");
          if (message) message.hidden = false;
          return;
        }
        if (document.hidden) {
          setTimeout(poll, POLL_INTERVAL_MS);
          return;
        }
        fetch(url, { headers: { Accept: "application/json" } })
          .then((res) => res.json())
          .then((body) => {
            if (body && body.done) {
              window.location.reload();
              return;
            }
            setTimeout(poll, POLL_INTERVAL_MS);
          })
          .catch(() => {
            // 一時的な通信エラーではポーリングを止めず, 次の周期へ進む
            setTimeout(poll, POLL_INTERVAL_MS);
          });
      }

      function tickElapsed() {
        const elapsedEl = card.querySelector("[data-poll-elapsed]");
        if (elapsedEl) {
          elapsedEl.textContent = formatElapsed(Date.now() - startedAt);
        }
        if (!active) return;
        setTimeout(tickElapsed, 1000);
      }

      setTimeout(poll, POLL_INTERVAL_MS);
      tickElapsed();
    });
  }

  ready(function () {
    scrollWeekStripToToday();
    setupReveal();
    setupCheckRows();
    setupThemeToggle();
    setupAllergenGroupToggles();
    setupPolling();
  });
})();
