/* IN-GPS Web — 프런트엔드 로직 (쿠키 세션 기반) */
const INGPS = (() => {
  const $ = (id) => document.getElementById(id);

  function showMsg(text, kind) {
    const el = $("msg");
    if (!el) return;
    el.textContent = text;
    el.className = "msg " + (kind || "error");
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    let body = null;
    try { body = await res.json(); } catch (_) {}
    return { ok: res.ok, status: res.status, body };
  }

  // ── 로그인 페이지 ──
  function initLogin() {
    $("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("submit-btn");
      btn.disabled = true;
      const r = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: $("username").value, password: $("password").value }),
      });
      btn.disabled = false;
      if (r.ok) {
        window.location.href = "index.html";
      } else {
        showMsg((r.body && r.body.detail) || "로그인에 실패했습니다.", "error");
      }
    });
  }

  // ── 회원가입 페이지 ──
  function initSignup() {
    $("signup-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("submit-btn");
      btn.disabled = true;
      const r = await api("/auth/signup", {
        method: "POST",
        body: JSON.stringify({ username: $("username").value, password: $("password").value }),
      });
      btn.disabled = false;
      if (r.ok) {
        showMsg("회원가입 완료! 로그인 페이지로 이동합니다…", "ok");
        setTimeout(() => (window.location.href = "login.html"), 1200);
      } else {
        showMsg((r.body && r.body.detail) || "회원가입에 실패했습니다.", "error");
      }
    });
  }

  // ── 대시보드 ──
  async function initDashboard() {
    // 인증 확인 (미로그인 시 로그인 페이지로)
    const me = await api("/auth/me");
    if (!me.ok) { window.location.href = "login.html"; return; }
    $("user-label").textContent = me.body.username + " 님";

    // 디바이스 목록 채우기
    const dev = await api("/devices");
    if (dev.ok && dev.body && dev.body.items) {
      const sel = $("device-select");
      dev.body.items.forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.device_id;
        opt.textContent = d.device_id;
        sel.appendChild(opt);
      });
    }

    $("logout-btn").addEventListener("click", async () => {
      await api("/auth/logout", { method: "POST" });
      window.location.href = "login.html";
    });

    $("download-btn").addEventListener("click", () => {
      const did = $("device-select").value;
      const qs = new URLSearchParams({ limit: "1000" });
      if (did) qs.set("device_id", did);
      // 쿠키가 자동 전송되므로 직접 내비게이션으로 다운로드
      window.location.href = "/export/sensor.csv?" + qs.toString();
      showMsg("다운로드를 시작했습니다.", "ok");
    });
  }

  return { initLogin, initSignup, initDashboard };
})();
