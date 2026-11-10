// KIRO push: shared event actions (test send, audience check, manual run with a retry-safe batch id).
import { esc, num, plural, giftSummary, newBatchId, errorText } from "./logic.js";
import { api, modal, toast, fail, confirmBox, $ } from "./ui.js";

// One batch id per event until its run succeeds, so pressing "Запустить" again after a network error never sends twice.
const pendingBatches = new Map();

const sampleList = (sample) => (sample && sample.length ? `<div class="kpu-muted">Например: ${sample.map((s) => esc(s)).join(", ")}</div>` : "");

export async function testEvent(eventId, body = {}) {
  try {
    await api(`/events/${eventId}/test`, "POST", body);
    toast("Тест отправлен вам в Telegram. Подарок не выдаётся");
    return true;
  } catch (err) {
    fail(err);
    return false;
  }
}

export async function checkAudience(eventId, audience) {
  try {
    const r = await api(`/events/${eventId}/run`, "POST", { dry_run: true, ...(audience ? { audience } : {}) });
    modal(`<h3>Аудитория</h3><p style="font-size:26px;font-weight:700;margin:4px 0">${num(r.matched)}</p>
      <p class="kpu-muted">${plural(r.matched, "пользователь получит", "пользователя получат", "пользователей получат")} сообщение при запуске с этими условиями. Ничего не отправлено.</p>${sampleList(r.sample)}
      <div class="kpu-row" style="justify-content:flex-end;margin-top:12px"><button class="kpu-btn kpu-primary" data-ok>Понятно</button></div>`, (m, close) => { $(m, "[data-ok]").onclick = close; });
  } catch (err) {
    fail(err);
  }
}

/** Dry run -> confirmation with count and gift -> real run. `event.gift` is the API gift object of the saved event. */
export async function runEvent(event, currency) {
  let dry;
  try {
    dry = await api(`/events/${event.id}/run`, "POST", { dry_run: true });
  } catch (err) {
    return fail(err);
  }
  if (!dry.matched) return toast(errorText("no_recipients"), "bad");
  if (!pendingBatches.has(event.id)) pendingBatches.set(event.id, newBatchId());
  const gift = giftSummary(event.gift, currency);
  const hasGift = event.gift && event.gift.kind !== "none";
  const ok = await confirmBox(
    `<b>Запустить «${esc(event.name)}»?</b><br>Получателей: <b>${num(dry.matched)}</b>.<br>Подарок каждому: <b>${esc(gift)}</b>${hasGift ? " (выдаётся сразу, до отправки)" : ""}.${sampleList(dry.sample)}
     <div class="kpu-muted" style="margin-top:8px">Отменить отправленное нельзя. Повторный запуск с тем же идентификатором ничего не отправит дважды.</div>`,
    "Запустить",
  );
  if (!ok) return null;
  try {
    const r = await api(`/events/${event.id}/run`, "POST", { batch_id: pendingBatches.get(event.id) });
    pendingBatches.delete(event.id);
    toast(`Запущено: в очереди ${num(r.queued)}${r.already ? `, уже были в очереди ${num(r.already)}` : ""}. Ход виден в «Журнале»`);
    return r;
  } catch (err) {
    fail(err); // the batch id stays: pressing again is safe
    return null;
  }
}
