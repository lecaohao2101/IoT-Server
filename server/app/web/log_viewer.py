"""The /logs page: a live, filterable tail of the server's own log records.

Served as one self-contained document so it works from the deployed app with no
build step and no extra hosting. It holds no data of its own -- it asks for the
API key, keeps it in the browser, and polls ``/api/v1/logs``.
"""

from __future__ import annotations

LOG_VIEWER_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart Apartment — Log trực tiếp</title>
<style>
  :root {
    --bg:#0f172a; --card:#1e293b; --line:#334155; --text:#f8fafc; --muted:#94a3b8;
    --accent:#38bdf8; --green:#22c55e; --amber:#f59e0b; --red:#ef4444; --violet:#a78bfa;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--bg); color:var(--text);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif; font-size:14px;
  }
  header {
    position:sticky; top:0; z-index:10; background:var(--card);
    border-bottom:1px solid var(--line); padding:10px 14px;
  }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .row + .row { margin-top:8px; }
  h1 { font-size:15px; margin:0 14px 0 0; white-space:nowrap; }
  input[type=text], input[type=password], select {
    background:var(--bg); color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:6px 9px; font-size:13px; font-family:inherit;
  }
  input[type=text]:focus, input[type=password]:focus { outline:1px solid var(--accent); }
  #q { flex:1; min-width:150px; }
  button {
    background:var(--accent); color:#0f172a; border:0; border-radius:6px;
    padding:6px 12px; font-weight:600; font-size:13px; cursor:pointer;
  }
  button.ghost { background:transparent; color:var(--muted); border:1px solid var(--line); }
  button.ghost.on { color:var(--accent); border-color:var(--accent); }
  label.chk { color:var(--muted); display:flex; align-items:center; gap:5px; cursor:pointer; white-space:nowrap; }
  #status { color:var(--muted); font-size:12px; margin-left:auto; white-space:nowrap; }
  #status b { color:var(--text); font-weight:600; }
  .dot { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--muted); margin-right:5px; }
  .dot.live { background:var(--green); }
  .dot.err { background:var(--red); }

  main { padding:10px 14px 40px; }
  .rec {
    border-left:3px solid var(--line); background:var(--card);
    border-radius:0 6px 6px 0; padding:7px 10px; margin-bottom:5px;
  }
  .rec.INFO    { border-left-color:#475569; }
  .rec.WARNING { border-left-color:var(--amber); }
  .rec.ERROR, .rec.CRITICAL { border-left-color:var(--red); }
  .rec.DEBUG   { border-left-color:#3f3f46; }
  .rec.pipe    { border-left-color:var(--accent); background:#19293f; }
  .head { display:flex; gap:9px; align-items:baseline; flex-wrap:wrap; }
  .ts { color:var(--muted); font-family:ui-monospace,Consolas,monospace; font-size:12px; }
  .lvl { font-size:11px; font-weight:700; letter-spacing:.3px; }
  .lvl.INFO{color:var(--muted);} .lvl.WARNING{color:var(--amber);}
  .lvl.ERROR,.lvl.CRITICAL{color:var(--red);} .lvl.DEBUG{color:#71717a;}
  .src-badge {
    font-size:10px; font-weight:700; border-radius:4px; padding:1px 5px;
    font-family:ui-monospace,Consolas,monospace; letter-spacing:.2px;
  }
  .src-badge.mobile { background:rgba(56,189,248,0.18); color:var(--accent); border:1px solid rgba(56,189,248,0.4); }
  .src-badge.device { background:rgba(245,158,11,0.18); color:var(--amber); border:1px solid rgba(245,158,11,0.4); }
  .src-badge.system { background:rgba(148,163,184,0.12); color:var(--muted); border:1px solid rgba(148,163,184,0.3); }
  .lg { color:var(--violet); font-size:12px; font-family:ui-monospace,Consolas,monospace; }
  .msg { font-weight:600; }
  .tid { color:var(--muted); font-size:11px; font-family:ui-monospace,Consolas,monospace; margin-left:auto; }
  .fields { display:flex; gap:6px; flex-wrap:wrap; margin-top:5px; }
  .f {
    background:var(--bg); border:1px solid var(--line); border-radius:4px;
    padding:2px 7px; font-size:12px; font-family:ui-monospace,Consolas,monospace;
  }
  .f .k { color:var(--muted); }
  .f.hot { border-color:var(--accent); }
  .f.bad { border-color:var(--red); }
  .f.text { color:var(--green); max-width:100%; overflow-wrap:anywhere; }
  pre.exc {
    margin:6px 0 0; padding:8px; background:#111827; border-radius:5px;
    color:#fca5a5; font-size:12px; overflow-x:auto; white-space:pre-wrap;
  }
  .empty { color:var(--muted); text-align:center; padding:40px 10px; line-height:1.7; }
  code { background:var(--card); padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<header>
  <div class="row">
    <h1>Log trực tiếp</h1>
    <input type="text" id="base" placeholder="https://... (trống = chính server này)" size="30">
    <input type="password" id="key" placeholder="API key" size="20">
    <button id="go">Kết nối</button>
    <button class="ghost" id="pause">Tạm dừng</button>
    <button class="ghost" id="clear">Xoá màn hình</button>
    <span id="status"><span class="dot"></span>chưa kết nối</span>
  </div>
  <div class="row">
    <input type="text" id="q" placeholder="Lọc theo chữ: stt, tts, esp32_master, lỗi...">
    <select id="src">
      <option value="all">Tất cả nguồn (All)</option>
      <option value="mobile">📱 Ứng dụng di động (Mobile)</option>
      <option value="device">⚡ Thiết bị / ESP32 (Device)</option>
    </select>
    <select id="lvl">
      <option value="0">Mọi mức</option>
      <option value="20">INFO trở lên</option>
      <option value="30">WARNING trở lên</option>
      <option value="40">Chỉ ERROR</option>
    </select>
    <label class="chk"><input type="checkbox" id="noise" checked> Ẩn nhiễu</label>
    <label class="chk"><input type="checkbox" id="only"> Chỉ luồng thoại</label>
    <label class="chk"><input type="checkbox" id="tail" checked> Tự cuộn</label>
  </div>
</header>
<main><div id="list"></div>
  <div class="empty" id="empty">
    Nhập API key rồi bấm <b>Kết nối</b>.<br>
    Trang chỉ giữ khoá trong trình duyệt của bạn, không gửi đi đâu khác.
  </div>
</main>
<script>
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const list = $("list");

  // Hai nguồn nhiễu khiến console của Fly không đọc nổi: access log lặp lại
  // nguyên dòng vừa ghi, và mấy đường thăm dò đập liên tục mỗi giây.
  const NOISE_PATHS = ["/healthz", "/readyz", "/update-status", "/poll-commands"];
  const PIPELINE = /stt|tts|voice|upload-audio|transcript|turn complete|audio/i;
  // Trường nào đáng chú ý ngay: gần 0 hoặc khác 0 là có chuyện.
  const HOT = ["peak_level", "confidence", "audio_bytes", "tts_audio_bytes", "bytes",
               "after_ms", "duration_ms", "dropped_chunks", "hub_subscribers"];
  const BAD = ["dropped_chunks", "tts_error", "reason", "error"];

  let cursor = 0, timer = null, paused = false, records = [];

  const base = () => $("base").value.trim().replace(/\\/$/, "");
  const setStatus = (cls, text) => {
    $("status").innerHTML = "";
    const d = document.createElement("span");
    d.className = "dot " + cls;
    $("status").appendChild(d);
    $("status").appendChild(document.createTextNode(text));
  };

  const sessionTypes = new Map();

  function classify(r) {
    const f = r.fields || {};
    const sid = f.session_id || (r.trace_id && r.trace_id.startsWith("sess_") ? r.trace_id : null);

    if (r.msg === "voice session opened") {
      const isDev = Boolean(f.device_id || (typeof f.principal === "string" && f.principal.includes("kind='device'")));
      const cat = isDev ? "device" : "mobile";
      if (sid) sessionTypes.set(sid, cat);
      return cat;
    }

    if (
      r.logger.startsWith("app.mqtt") ||
      r.logger.startsWith("app.api.routes_hardware") ||
      (f.path && (f.path === "/update-status" || f.path === "/poll-commands" || f.path === "/upload-audio" || f.path.startsWith("/api/audio/stream"))) ||
      (f.device_id && String(f.device_id).startsWith("esp32")) ||
      sid === "esp32_hardware" ||
      (typeof f.principal === "string" && f.principal.includes("kind='device'"))
    ) {
      if (sid) sessionTypes.set(sid, "device");
      return "device";
    }

    if (sid && sessionTypes.has(sid)) {
      return sessionTypes.get(sid);
    }

    if (
      sid ||
      r.logger.startsWith("app.services.session") ||
      r.logger.startsWith("app.services.orchestrator") ||
      r.logger.startsWith("app.ai.") ||
      (f.path && (f.path.startsWith("/api/v1/chat") || f.path.startsWith("/api/v1/commands") || f.path.startsWith("/api/v1/devices") || f.path.startsWith("/voice-demo"))) ||
      /voice|stt|tts|transcript|turn complete/i.test(r.msg)
    ) {
      if (sid) sessionTypes.set(sid, "mobile");
      return "mobile";
    }

    return "system";
  }

  function isNoise(r) {
    if (r.logger === "uvicorn.access") return true;
    if (r.msg === "http request" && NOISE_PATHS.includes(r.fields.path)) return true;
    if (r.logger === "httpx") return true;
    return false;
  }

  function visible(r) {
    if ($("noise").checked && isNoise(r)) return false;
    if ($("only").checked && !PIPELINE.test(r.msg + " " + r.logger)) return false;
    const src = $("src").value;
    const cat = classify(r);
    if (src === "mobile" && cat !== "mobile") return false;
    if (src === "device" && cat !== "device") return false;
    const min = parseInt($("lvl").value, 10);
    const n = {DEBUG:10, INFO:20, WARNING:30, ERROR:40, CRITICAL:50}[r.level] || 20;
    if (n < min) return false;
    const q = $("q").value.trim().toLowerCase();
    if (q) {
      const hay = (r.msg + " " + r.logger + " " + r.trace_id + " " +
                   JSON.stringify(r.fields)).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  function chip(key, value) {
    const el = document.createElement("span");
    el.className = "f";
    if (key === "text") el.classList.add("text");
    else if (BAD.includes(key) && value !== 0 && value !== null && value !== false) el.classList.add("bad");
    else if (HOT.includes(key)) el.classList.add("hot");
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = key + "=";
    el.appendChild(k);
    // textContent, không phải innerHTML: bản ghi có thể chứa lời người dùng nói.
    el.appendChild(document.createTextNode(
      typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)
    ));
    return el;
  }

  function render(r) {
    const box = document.createElement("div");
    box.className = "rec " + r.level + (PIPELINE.test(r.msg) ? " pipe" : "");

    const head = document.createElement("div");
    head.className = "head";

    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = r.ts.slice(11, 23);
    head.appendChild(ts);

    const lvl = document.createElement("span");
    lvl.className = "lvl " + r.level;
    lvl.textContent = r.level;
    head.appendChild(lvl);

    const cat = classify(r);
    const badge = document.createElement("span");
    badge.className = "src-badge " + cat;
    badge.textContent = cat === "mobile" ? "📱 mobile" : cat === "device" ? "⚡ device" : "⚙️ system";
    head.appendChild(badge);

    const lg = document.createElement("span");
    lg.className = "lg";
    lg.textContent = r.logger.replace(/^app\\./, "");
    head.appendChild(lg);

    const msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = r.msg;
    head.appendChild(msg);

    if (r.trace_id && r.trace_id !== "-") {
      const tid = document.createElement("span");
      tid.className = "tid";
      tid.textContent = r.trace_id;
      head.appendChild(tid);
    }
    box.appendChild(head);

    const keys = Object.keys(r.fields || {});
    if (keys.length) {
      const wrap = document.createElement("div");
      wrap.className = "fields";
      keys.forEach((k) => wrap.appendChild(chip(k, r.fields[k])));
      box.appendChild(wrap);
    }
    if (r.exc) {
      const pre = document.createElement("pre");
      pre.className = "exc";
      pre.textContent = r.exc;
      box.appendChild(pre);
    }
    return box;
  }

  function redraw() {
    const shown = records.filter(visible);
    list.innerHTML = "";
    const frag = document.createDocumentFragment();
    shown.forEach((r) => frag.appendChild(render(r)));
    list.appendChild(frag);
    const empty = $("empty");
    empty.style.display = shown.length ? "none" : "block";
    if (!shown.length) {
      empty.textContent = records.length
        ? "Có " + records.length + " bản ghi nhưng bộ lọc đang giấu hết."
        : 'Chưa có bản ghi nào. Hãy thử nói với thiết bị, hoặc bỏ dấu "Ẩn nhiễu".';
    }
    if ($("tail").checked) window.scrollTo(0, document.body.scrollHeight);
  }

  async function poll() {
    try {
      const res = await fetch(base() + "/api/v1/logs?after=" + cursor + "&limit=500", {
        headers: { "X-API-Key": $("key").value }
      });
      if (res.status === 401 || res.status === 403) {
        setStatus("err", "API key sai hoặc thiếu");
        stop();
        return;
      }
      if (!res.ok) { setStatus("err", "HTTP " + res.status); return; }
      const data = await res.json();
      if (data.records.length) {
        records = records.concat(data.records).slice(-2000);
        cursor = data.last_seq;
        redraw();
      } else if (cursor === 0) {
        cursor = data.last_seq;
      }
      setStatus("live", "đang nghe · " + records.length + " bản ghi");
    } catch (e) {
      setStatus("err", "không gọi được server");
    }
  }

  function start() {
    stop();
    localStorage.setItem("logKey", $("key").value);
    localStorage.setItem("logBase", $("base").value);
    setStatus("", "đang kết nối...");
    poll();
    timer = setInterval(() => { if (!paused) poll(); }, 1000);
  }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  $("go").onclick = start;
  $("pause").onclick = function () {
    paused = !paused;
    this.classList.toggle("on", paused);
    this.textContent = paused ? "Tiếp tục" : "Tạm dừng";
    if (paused) setStatus("", "đã dừng · " + records.length + " bản ghi");
  };
  $("clear").onclick = () => { records = []; redraw(); };
  ["q", "lvl", "noise", "only", "src"].forEach((id) => {
    $(id).addEventListener("input", redraw);
    $(id).addEventListener("change", redraw);
  });

  $("src").value = localStorage.getItem("logSrc") || "all";
  $("src").addEventListener("change", () => {
    localStorage.setItem("logSrc", $("src").value);
  });

  $("key").value = localStorage.getItem("logKey") || "";
  $("base").value = localStorage.getItem("logBase") || "";
  $("key").addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });
  if ($("key").value) start();
})();
</script>
</body>
</html>
"""
