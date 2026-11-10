// KIRO push: custom emoji picker (Core library endpoints) and media upload / library dialogs.
import { esc, fmtSize, fmtDate, MEDIA_RU } from "./logic.js";
import { api, API, modal, toast, fail, confirmBox, $, $$ } from "./ui.js";

// ---------------------------------------------------------------------------------------------- emoji
const COMMON = ["🎁", "🎂", "🎉", "🎊", "🎄", "🎆", "🔥", "⭐", "✨", "💎", "🚀", "❤️", "💙", "👋", "🙌", "👏", "🥳", "🎈", "🍀", "🏆", "💰", "⚡", "🛡", "🌍", "📅", "🔔", "🎯", "☀️", "❄️", "🌟"];

function firstGrapheme(text) {
  if (typeof Intl !== "undefined" && Intl.Segmenter) return [...new Intl.Segmenter().segment(text)][0]?.segment || text;
  return [...text][0];
}

export function emojiMediaUrl(caps, id) {
  const tpl = caps?.emoji_library?.media;
  return tpl ? tpl.replace("{emoji_id}", encodeURIComponent(id)) : null;
}

/**
 * Open the emoji picker. Resolves {id, fallback} (id is "" for an ordinary Unicode emoji) or null when closed.
 * Custom emoji come from Core's admin library (same admin session); if those endpoints fail the picker still lets
 * the admin type an emoji id and its fallback character by hand.
 */
export function pickEmoji(caps, { allowCustom = true } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (value, close) => { if (done) return; done = true; close(); resolve(value); };
    modal(
      `<h3>Эмодзи</h3>
      <div class="kpu-tabs" role="tablist" style="margin-bottom:8px"><button class="kpu-tab on" data-t="common" role="tab">Обычные</button>${allowCustom ? `<button class="kpu-tab" data-t="custom" role="tab">Custom emoji</button>` : ""}</div>
      <div data-pane="common"><div class="kpu-emoji-grid">${COMMON.map((e) => `<button type="button" data-char="${esc(e)}" aria-label="${esc(e)}">${esc(e)}</button>`).join("")}</div>
        <label class="f" style="margin-top:10px">Свой символ<input type="text" data-own maxlength="8" placeholder="Вставьте любой эмодзи"></label></div>
      <div data-pane="custom" hidden>
        <div class="kpu-row"><select data-set aria-label="Набор" class="kpu-grow"></select><input type="search" data-q placeholder="Поиск" aria-label="Поиск" style="width:140px"></div>
        <div data-lib class="kpu-muted" style="margin-top:8px">Загрузка библиотеки…</div>
        <div class="kpu-emoji-grid" data-grid></div>
        <div class="kpu-row" style="margin-top:6px"><button class="kpu-btn kpu-sm" data-more hidden>Показать ещё</button><span class="kpu-muted" data-count></span></div>
        <div class="kpu-sub"><div class="kpu-muted" style="margin-bottom:6px">Или введите вручную (ID из Telegram и обычный эмодзи-замену)</div>
          <div class="kpu-row"><input type="text" data-id inputmode="numeric" placeholder="ID, например 5368324170671202286" style="flex:2;min-width:140px"><input type="text" data-fb maxlength="8" placeholder="🎁" style="width:70px"><button class="kpu-btn kpu-primary kpu-sm" data-manual>Вставить</button></div></div>
      </div>
      <div class="kpu-row" style="justify-content:flex-end;margin-top:12px"><button class="kpu-btn" data-cancel>Закрыть</button></div>`,
      (m, close) => {
        const state = { set: "", q: "", offset: 0, total: 0, loaded: false };
        m.addEventListener("pointerdown", (e) => { if (e.target === m) finish(null, close); });
        $(m, "[data-cancel]").onclick = () => finish(null, close);
        $$(m, "[data-t]").forEach((b) => b.addEventListener("click", () => {
          $$(m, "[data-t]").forEach((x) => x.classList.toggle("on", x === b));
          $$(m, "[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== b.dataset.t; });
          if (b.dataset.t === "custom" && !state.loaded) loadLibrary();
        }));
        $$(m, "[data-char]").forEach((b) => b.addEventListener("click", () => finish({ id: "", fallback: b.dataset.char }, close)));
        $(m, "[data-own]").addEventListener("input", (e) => {
          const v = e.target.value.trim();
          if (v) finish({ id: "", fallback: firstGrapheme(v) }, close);
        });
        $(m, "[data-manual]").onclick = () => {
          const id = $(m, "[data-id]").value.replace(/\D/g, "");
          const fb = $(m, "[data-fb]").value.trim() || "⭐";
          if (!/^[1-9]\d{0,19}$/.test(id)) return toast("ID custom emoji: число", "bad");
          finish({ id, fallback: fb }, close);
        };
        const grid = $(m, "[data-grid]");
        grid.addEventListener("error", (e) => {
          const img = e.target;
          if (img.tagName === "IMG") img.replaceWith(document.createTextNode(img.closest("button")?.dataset.fb || "⭐"));
        }, true);
        grid.addEventListener("click", (e) => {
          const b = e.target.closest("button[data-id]");
          if (b) finish({ id: b.dataset.id, fallback: b.dataset.fb || "⭐" }, close);
        });
        async function loadLibrary() {
          state.loaded = true;
          const libUrl = caps?.emoji_library?.library;
          const note = $(m, "[data-lib]");
          if (!libUrl) { note.textContent = "Библиотека эмодзи недоступна: введите ID вручную."; $(m, "[data-set]").hidden = true; $(m, "[data-q]").hidden = true; return; }
          try {
            const lib = await api(libUrl);
            const sets = lib.sets || [];
            $(m, "[data-set]").innerHTML = `<option value="">Все наборы</option>` + sets.map((s) => `<option value="${esc(s.name)}">${esc(s.title || s.name)} (${s.count})</option>`).join("");
            note.textContent = sets.length ? "" : "Библиотека пуста. Добавьте набор в настройках Minishop (Настройки → Меню Telegram → Библиотека эмодзи) или введите ID вручную.";
            if (sets.length) page(true);
          } catch (err) {
            note.textContent = `Библиотека эмодзи недоступна (${err.message}). Введите ID вручную.`;
            $(m, "[data-set]").hidden = true;
            $(m, "[data-q]").hidden = true;
          }
        }
        async function page(reset) {
          if (reset) { state.offset = 0; grid.innerHTML = ""; }
          const url = new URL(caps.emoji_library.catalog, location.origin);
          if (state.set) url.searchParams.set("set", state.set);
          if (state.q) url.searchParams.set("q", state.q);
          url.searchParams.set("offset", String(state.offset));
          url.searchParams.set("limit", "60");
          try {
            const data = await api(url.pathname + url.search);
            const items = data.items || [];
            grid.insertAdjacentHTML("beforeend", items.map((it) => {
              const src = it.thumbnail_url || emojiMediaUrl(caps, it.id);
              return `<button type="button" data-id="${esc(it.id)}" data-fb="${esc(it.fallback)}" title="${esc(it.fallback)}">${src && it.id ? `<img src="${esc(src)}" alt="" loading="lazy">` : esc(it.fallback)}</button>`;
            }).join(""));
            state.offset += items.length;
            state.total = data.total || 0;
            $(m, "[data-more]").hidden = state.offset >= state.total;
            $(m, "[data-count]").textContent = state.total ? `${state.offset} из ${state.total}` : "Ничего не найдено";
          } catch (err) {
            $(m, "[data-count]").textContent = `Не удалось загрузить: ${err.message}`;
          }
        }
        $(m, "[data-set]").addEventListener("change", (e) => { state.set = e.target.value; page(true); });
        let timer = 0;
        $(m, "[data-q]").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value.trim(); page(true); }, 350); });
        $(m, "[data-more]").onclick = () => page(false);
      },
    );
  });
}

// ---------------------------------------------------------------------------------------------- media upload
async function prepareImage(file, photoLimit) {
  if (/gif$/i.test(file.type)) return { blob: file, kind: "animation" };
  if (file.size <= photoLimit * 0.9 && /^image\/(jpeg|png|webp)$/.test(file.type)) return { blob: file, kind: "photo" };
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 2560 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.9));
  return { blob, kind: "photo" };
}

/** Chunked upload: POST /media -> PUT /media/{id}/chunk (X-Offset) -> POST /media/{id}/finish. Resolves {id, kind, name, size}. */
export async function uploadFile(file, limits, onProgress) {
  const byKind = limits.media_bytes;
  let blob = file;
  let kind = "document";
  if (file.type.startsWith("image/")) ({ blob, kind } = await prepareImage(file, byKind.photo));
  else if (file.type.startsWith("video/")) kind = "video";
  if (blob.size > byKind[kind]) throw new Error(`Файл «${file.name}» больше ${fmtSize(byKind[kind])}`);
  const init = await api("/media", "POST", { kind, filename: file.name, content_type: blob.type || file.type, size: blob.size });
  const chunk = Math.min(init.chunk || limits.media_chunk, 3 * 1048576);
  for (let offset = 0; offset < blob.size; offset += chunk) {
    const part = new Uint8Array(await blob.slice(offset, offset + chunk).arrayBuffer());
    await api(`/media/${init.id}/chunk`, "PUT", part, { "X-Offset": String(offset), "Content-Type": "application/octet-stream" });
    onProgress?.(Math.min(1, (offset + chunk) / blob.size));
  }
  await api(`/media/${init.id}/finish`, "POST", {});
  return { id: init.id, kind, name: file.name, size: blob.size };
}

export const mediaUrl = (id) => `${API}/media/${encodeURIComponent(id)}`;

/** Dialog with files uploaded earlier. Resolves the chosen item {id, kind, ...} or null. Deleting is allowed here. */
export function pickStoredMedia() {
  return new Promise((resolve) => {
    let chosen = null;
    modal(`<h3>Загруженные файлы</h3><div data-body class="kpu-muted">Загрузка…</div><div class="kpu-row" style="justify-content:flex-end;margin-top:12px"><button class="kpu-btn" data-close>Закрыть</button></div>`, (m, close) => {
      const end = (v) => { chosen = v; close(); resolve(chosen); };
      m.addEventListener("pointerdown", (e) => { if (e.target === m) resolve(null); });
      $(m, "[data-close]").onclick = () => end(null);
      const draw = async () => {
        try {
          const { media } = await api("/media");
          $(m, "[data-body]").innerHTML = media.length
            ? media.map((x) => `<div class="kpu-item">${x.kind === "photo" ? `<img src="${esc(mediaUrl(x.id))}" alt="">` : `<div class="ph">${x.kind === "video" ? "🎬" : x.kind === "animation" ? "🎞" : "📄"}</div>`}
                <div class="kpu-grow"><b>${esc(MEDIA_RU[x.kind] || x.kind)}</b> <span class="kpu-muted">${esc(x.filename || "")} · ${fmtSize(x.size)} · ${esc(fmtDate(x.created_at))}</span></div>
                <button class="kpu-btn kpu-sm kpu-primary" data-use="${esc(x.id)}">Выбрать</button><button class="kpu-btn kpu-sm kpu-danger" data-del="${esc(x.id)}" aria-label="Удалить файл">✕</button></div>`).join("")
            : `<p class="kpu-muted">Загруженных файлов пока нет.</p>`;
          $$(m, "[data-use]").forEach((b) => b.addEventListener("click", () => end(media.find((x) => x.id === b.dataset.use))));
          $$(m, "[data-del]").forEach((b) => b.addEventListener("click", async () => {
            if (!(await confirmBox("Удалить файл из хранилища плагина? Это нельзя отменить.", "Удалить", { danger: true }))) return;
            try { await api(`/media/${b.dataset.del}`, "DELETE"); draw(); } catch (err) { fail(err); }
          }));
        } catch (err) { $(m, "[data-body]").innerHTML = `<div class="kpu-msg">${esc(err.message)}</div>`; }
      };
      draw();
    });
  });
}
