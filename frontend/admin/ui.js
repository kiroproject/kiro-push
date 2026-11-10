// KIRO push: admin UI kit (styles, api helper, modal, confirm, toast, small widgets). All classes carry the kpu- prefix.
import { esc, errorText } from "./logic.js";

export const API = "/api/admin/kiro-push";

export const STYLE = `
.kpu{display:flex;flex-direction:column;gap:14px;color:var(--text);font-size:14px;min-width:0;line-height:1.4}
.kpu *{box-sizing:border-box}
.kpu h2,.kpu h3,.kpu h4,.kpu p{margin:0}
.kpu h2{font-size:20px}
.kpu-card{border:1px solid var(--border);border-radius:var(--radius-card,var(--radius,12px));padding:14px;background:var(--panel,var(--bg,transparent));min-width:0}
.kpu-card>h3,.kpu-h{font-size:15px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.kpu-stack{display:flex;flex-direction:column;gap:14px;min-width:0}
.kpu-muted{color:var(--muted);font-size:12.5px}
.kpu-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.kpu-grow{flex:1;min-width:0}
.kpu-btn{border:1px solid var(--border);background:transparent;color:var(--text);border-radius:8px;padding:7px 12px;cursor:pointer;font:inherit;white-space:nowrap;min-height:34px}
.kpu-btn:hover:not([disabled]){border-color:var(--accent)}
.kpu-btn:focus-visible,.kpu-tab:focus-visible,.kpu-chip:focus-visible,.kpu-sw:focus-within{outline:2px solid var(--accent);outline-offset:2px}
.kpu-btn[disabled]{opacity:.5;cursor:default}
.kpu-primary{background:var(--accent);border-color:var(--accent);color:var(--accent-contrast,#fff)}
.kpu-danger{color:#e5484d}
.kpu-sm{padding:4px 9px;min-height:28px;font-size:13px}
.kpu-tabs{display:flex;gap:6px;flex-wrap:wrap}
.kpu-tab{border:1px solid var(--border);background:transparent;color:var(--muted);border-radius:999px;padding:6px 14px;cursor:pointer;font:inherit}
.kpu-tab.on{color:var(--text);border-color:var(--accent);background:color-mix(in srgb,var(--accent) 14%,transparent)}
.kpu input:not([type=checkbox]):not([type=radio]):not([type=file]),.kpu select,.kpu textarea{font:inherit;color:var(--text);background-color:var(--panel-2,var(--panel,#1f2430));border:1px solid var(--border);border-radius:var(--radius-control,8px);padding:7px 9px;color-scheme:inherit;min-width:0;max-width:100%}
.kpu input:focus-visible,.kpu select:focus-visible,.kpu textarea:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.kpu select option{background-color:var(--panel-2,var(--panel,#1f2430));color:var(--text)}
.kpu input[type=checkbox],.kpu input[type=radio]{accent-color:var(--accent);width:auto;margin:0}
.kpu textarea{resize:vertical;width:100%;min-height:110px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.45}
.kpu label.f{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:12px;min-width:0}
.kpu label.f>input,.kpu label.f>select{width:100%}
.kpu label.chk{display:flex;align-items:center;gap:8px;color:var(--text);font-size:13.5px;cursor:pointer}
.kpu-table{width:100%;border-collapse:collapse}
.kpu-table th,.kpu-table td{padding:8px 6px;border-bottom:1px solid var(--border);text-align:left;vertical-align:middle}
.kpu-table th{color:var(--muted);font-weight:500;font-size:12px;white-space:nowrap}
.kpu-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.kpu-badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;border:1px solid currentColor;white-space:nowrap}
.kpu-banner{padding:10px 12px;border-radius:10px;border:1px solid;font-size:13px;line-height:1.45}
.kpu-banner.warn{border-color:#f59e0b;background:rgba(245,158,11,.1)}
.kpu-banner.ok{border-color:#22c55e;background:rgba(34,197,94,.08)}
.kpu-banner.bad{border-color:#e5484d;background:rgba(229,72,77,.08)}
.kpu-banner.info{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
.kpu-banner ul{margin:4px 0 0;padding-left:18px}
.kpu-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpu-stat{border:1px solid var(--border);border-radius:10px;padding:10px 12px;min-width:0}
.kpu-stat b{display:block;font-size:22px;margin-top:2px;line-height:1.15}
.kpu-stat small{color:var(--muted);font-size:12px}
.kpu-bar{height:10px;border-radius:999px;background:rgba(127,127,127,.2);overflow:hidden;display:flex}
.kpu-bar i{display:block;height:100%}
.kpu-spark{display:flex;align-items:flex-end;gap:2px;height:64px}
.kpu-spark i{flex:1;min-width:3px;background:var(--accent);border-radius:2px 2px 0 0;opacity:.85}
.kpu-spark i.z{opacity:.25}
.kpu-chips{display:flex;gap:6px;flex-wrap:wrap}
.kpu-chip{border:1px solid var(--border);background:transparent;color:var(--text);border-radius:999px;padding:5px 12px;cursor:pointer;font:inherit;font-size:13px}
.kpu-chip.on{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,transparent)}
.kpu-chip[disabled]{opacity:.5;cursor:not-allowed}
.kpu-sub{border:1px dashed var(--border);border-radius:10px;padding:10px;margin-top:10px}
.kpu-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px 14px}
.kpu-compose{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:14px;align-items:start}
.kpu-side{position:sticky;top:8px;display:flex;flex-direction:column;gap:12px;min-width:0}
.kpu-msg{padding:8px 12px;border-radius:8px;background:rgba(229,72,77,.12);color:#e5484d;font-size:13px}
.kpu-err{color:#e5484d;font-size:12.5px;margin-top:4px}
.kpu-warn{color:#d97706;font-size:12.5px;margin-top:4px}
.kpu-modal{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px}
.kpu-modal>div{background:var(--panel,#1b1f2a);color:var(--text);border:1px solid var(--border);border-radius:14px;padding:18px;max-width:560px;width:100%;max-height:90vh;overflow:auto}
.kpu-modal h3{margin-bottom:10px}
.kpu-item{display:flex;gap:10px;align-items:center;border:1px solid var(--border);border-radius:10px;padding:8px;margin-top:8px;flex-wrap:wrap}
.kpu-item img,.kpu-item .ph{width:54px;height:54px;border-radius:8px;object-fit:cover;background:rgba(127,127,127,.2);display:flex;align-items:center;justify-content:center;flex:none}
.kpu-prog{height:6px;border-radius:99px;background:rgba(127,127,127,.2);overflow:hidden;margin-top:4px}
.kpu-prog i{display:block;height:100%;background:var(--accent);transition:width .3s}
.kpu-prog.big{height:12px}
.kpu-sw{display:inline-flex;align-items:center;gap:8px;cursor:pointer}
.kpu-sw input{position:absolute;opacity:0;width:0;height:0}
.kpu-sw .t{width:38px;height:22px;border-radius:99px;background:rgba(127,127,127,.4);position:relative;transition:background .15s;flex:none}
.kpu-sw .t::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:left .15s}
.kpu-sw input:checked+.t{background:var(--accent)}
.kpu-sw input:checked+.t::after{left:19px}
.kpu-sw input:disabled+.t{opacity:.5}
.kpu-radio{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}
.kpu-radio label{border:1px solid var(--border);border-radius:10px;padding:9px 10px;cursor:pointer;display:flex;flex-direction:column;gap:2px;font-size:13.5px}
.kpu-radio label:has(input:checked){border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent)}
.kpu-radio label.dis{opacity:.55;cursor:not-allowed}
.kpu-radio label>span{display:flex;gap:7px;align-items:center}
.kpu-radio small{color:var(--muted);font-size:12px}
.kpu-toolbar{display:flex;gap:4px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.kpu-toolbar .kpu-btn{padding:3px 9px;min-height:30px;min-width:32px}
.kpu-ph{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.kpu-ph button{border:1px dashed var(--border);background:transparent;color:var(--text);border-radius:6px;padding:2px 7px;font:12px ui-monospace,Consolas,monospace;cursor:pointer}
.kpu-ph button:hover{border-color:var(--accent)}
.kpu-lang{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.kpu-lang button{border:0;background:transparent;color:var(--muted);padding:5px 14px;cursor:pointer;font:inherit}
.kpu-lang button.on{background:var(--accent);color:var(--accent-contrast,#fff)}
.kpu-lang button .d{display:inline-block;width:7px;height:7px;border-radius:50%;background:#22c55e;margin-left:6px}
.kpu-emoji-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(44px,1fr));gap:4px;max-height:280px;overflow:auto;margin-top:8px}
.kpu-emoji-grid button{border:1px solid transparent;background:transparent;border-radius:8px;height:44px;font-size:24px;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0}
.kpu-emoji-grid button:hover{border-color:var(--accent);background:rgba(127,127,127,.12)}
.kpu-emoji-grid img{width:30px;height:30px;object-fit:contain}
.kpu-title-input{font-weight:600;flex:1;min-width:180px}
.kpu-lock{opacity:.8}
.kpu-pager{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin-top:10px;flex-wrap:wrap}
.kpu-kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:13px}
.kpu-kv dt{color:var(--muted)}
.kpu-kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
.kpu-code{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.kpu-pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px ui-monospace,Consolas,monospace;margin:0}
.kpu code{overflow-wrap:anywhere}
.kpu-loading{opacity:.55;pointer-events:none}
/* Telegram-like preview bubble (dark on purpose, as Telegram clients show it in both admin themes) */
.tg{background:#0e1621;border-radius:14px;padding:12px;color:#fff;font-size:14px;min-width:0}
.tg-bubble{background:#182533;border-radius:12px 12px 12px 4px;padding:8px 10px;max-width:100%;overflow-wrap:anywhere;line-height:1.4}
.tg-bubble a{color:#6ab3f3}
.tg-bubble code,.tg-bubble pre{background:rgba(255,255,255,.08);border-radius:4px;padding:0 4px;font-family:ui-monospace,Consolas,monospace}
.tg-bubble pre{padding:6px 8px;white-space:pre-wrap;margin:4px 0}
.tg-bubble blockquote{margin:4px 0;padding:2px 0 2px 10px;border-left:3px solid #6ab3f3;background:rgba(106,179,243,.08);border-radius:0 6px 6px 0}
.kpu-spoil{background:#5a6a7a;color:transparent;border-radius:4px;cursor:pointer;user-select:none}
.kpu-spoil.open{background:rgba(255,255,255,.1);color:inherit}
.kpu-emoji{display:inline-block}
.kpu-emoji img{width:1.2em;height:1.2em;vertical-align:-.2em}
.tg-media{background:#243447;border-radius:8px;margin-bottom:6px;overflow:hidden;font-size:12px;color:#9fb3c8}
.tg-media img{width:100%;display:block;max-height:220px;object-fit:cover}
.tg-media .ph{padding:22px;text-align:center}
.tg-kb{display:flex;flex-direction:column;gap:4px;margin-top:6px}
.tg-kb>div{display:flex;gap:4px}
.tg-kb span{flex:1;background:#2b5278;border-radius:8px;text-align:center;padding:7px 6px;font-size:13px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tg-kb span.primary{background:#2f7bd6}.tg-kb span.success{background:#2f9e5b}.tg-kb span.danger{background:#c0392b}
.tg-meta{color:#7f91a4;font-size:11.5px;margin-top:6px}
@media (max-width:1100px){.kpu-compose{grid-template-columns:minmax(0,1fr)}.kpu-side{position:static}}
@media (max-width:560px){.kpu{font-size:14px}.kpu-card{padding:11px}.kpu-form{grid-template-columns:minmax(0,1fr)}.kpu-modal>div{padding:14px}.kpu-radio{grid-template-columns:minmax(0,1fr)}}
`;

// ---------------------------------------------------------------------------------------------- DOM helpers
export const h = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
export const $ = (root, sel) => root.querySelector(sel);
export const $$ = (root, sel) => [...root.querySelectorAll(sel)];

export function badge(label, color) {
  return `<span class="kpu-badge" style="color:${color}">${esc(label)}</span>`;
}

export function kpuSwitch(attrs, checked, label, disabled = false) {
  return `<label class="kpu-sw"><input type="checkbox" ${attrs} ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}><span class="t"></span><span>${esc(label)}</span></label>`;
}

export function skeleton(text = "Загрузка…") {
  return `<div class="kpu-muted" role="status">${esc(text)}</div>`;
}

// ---------------------------------------------------------------------------------------------- network
function csrf() {
  const m = document.cookie.match(/(?:^|;\s*)rw_webapp_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/** One door to the plugin API. `path` is relative to /api/admin/kiro-push; absolute paths (Core endpoints) start with /api/. */
export async function api(path, method = "GET", body, extraHeaders) {
  const url = path.startsWith("/api/") ? path : API + path;
  const headers = { Accept: "application/json", ...(extraHeaders || {}) };
  const raw = body instanceof Uint8Array || body instanceof Blob;
  if (body !== undefined && !raw) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf();
  let res;
  try {
    res = await fetch(url, { method, credentials: "same-origin", headers, body: body === undefined ? undefined : raw ? body : JSON.stringify(body) });
  } catch {
    const err = new Error("Нет связи с сервером");
    err.code = "network";
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    const err = new Error(errorText(data.error, data) || `HTTP ${res.status}`);
    err.code = data.error;
    err.data = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function downloadFile(path, fallbackName) {
  const res = await fetch(API + path, { credentials: "same-origin", headers: { Accept: "*/*" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const name = (/filename="?([^";]+)"?/.exec(cd) || [])[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

// ---------------------------------------------------------------------------------------------- dialogs
export function modal(html, onMount) {
  const m = h(`<div class="kpu-modal kpu" role="dialog" aria-modal="true"><div>${html}</div></div>`);
  const previous = document.activeElement;
  document.body.appendChild(m);
  const close = () => { m.remove(); document.removeEventListener("keydown", onKey, true); previous?.focus?.(); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey, true);
  m.addEventListener("pointerdown", (e) => { if (e.target === m) close(); });
  onMount?.(m, close);
  (m.querySelector("[autofocus],input,textarea,select,button") || m).focus?.();
  return { el: m, close };
}

/** htmlText is trusted markup built with esc(); returns a Promise<boolean>. */
export function confirmBox(htmlText, okLabel = "Продолжить", { danger = false } = {}) {
  return new Promise((resolve) => {
    modal(
      `<div style="margin:0 0 14px;line-height:1.5">${htmlText}</div><div class="kpu-row" style="justify-content:flex-end"><button class="kpu-btn" data-no>Отмена</button><button class="kpu-btn ${danger ? "kpu-danger" : "kpu-primary"}" data-ok>${esc(okLabel)}</button></div>`,
      (m, close) => {
        m.querySelector("[data-no]").onclick = () => { close(); resolve(false); };
        m.querySelector("[data-ok]").onclick = () => { close(); resolve(true); };
      },
    );
  });
}

export function toast(text, kind = "ok") {
  const t = h(`<div class="kpu-banner kpu ${kind}" role="status" style="position:fixed;right:16px;bottom:16px;z-index:2147483001;max-width:min(380px,calc(100vw - 32px));background:var(--panel,#1b1f2a)">${esc(text)}</div>`);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), kind === "bad" ? 7000 : 4000);
}

export function fail(err) {
  toast(err && err.message ? err.message : String(err), "bad");
}

/** Disable a button while an async job runs; always restores. */
export async function busy(button, job) {
  if (button) button.disabled = true;
  try {
    return await job();
  } finally {
    if (button && button.isConnected) button.disabled = false;
  }
}
