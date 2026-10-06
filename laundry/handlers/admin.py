"""Админ-панель: заявки старост, управление любым этажом, старосты, статистика, логи."""
from __future__ import annotations

import logging

from telebot import types

from .. import config, db, render, services, states
from .. import schedule as sched
from ..loader import bot
from ..logger import LOG_FILES, VK_PREFIX
from ..notify import send_to_user
from ..render import esc
from ..utils import answer_result, btn, edit, grid_kb, inline, safe, send

log = logging.getLogger(__name__)


def panel_view() -> tuple[str, types.InlineKeyboardMarkup]:
    pending = len(db.pending_starosta_requests())
    text = "🛠 <b>Админ-панель</b>\n\nВыберите раздел:"
    return text, inline(
        btn(f"📨 Заявки старост ({pending})", "adm:req"),
        btn(f"📝 Заявки на изменение данных ({len(db.pending_change_requests())})", "adm:chg"),
        btn("🏢 Управление этажом", "adm:floors"),
        btn("⭐ Старосты", "adm:st"),
        [btn("📊 Статистика", "adm:stats"), btn("📄 Логи", "adm:log")],
    )


def cmd_panel(message: types.Message) -> None:
    if not services.is_admin(db.get_user(message.from_user.id) or {"telegram_id": message.from_user.id}):
        send(message.chat.id, "Команда доступна только администратору.")
        return
    send(message.chat.id, *panel_view())


@bot.message_handler(commands=["admin"])
@safe
def cmd_admin(message: types.Message) -> None:
    states.clear(message.from_user.id)
    cmd_panel(message)


@bot.callback_query_handler(func=lambda c: c.data == "adm" or c.data.startswith("adm:"))
@safe
def admin_callbacks(call: types.CallbackQuery) -> None:
    admin = db.get_user(call.from_user.id)
    if not services.is_admin(admin or {"telegram_id": call.from_user.id}):
        answer_result(call, ("⛔ Только для администратора.", True))
        return
    if admin is None:  # аккаунт создаётся при запуске, но на всякий случай
        db.sync_admins(config.ADMIN_IDS, config.VK_ADMIN_IDS, sched.now())
        admin = db.get_user(call.from_user.id)
    parts = call.data.split(":")
    name = parts[1] if len(parts) > 1 else ""
    try:
        result = _dispatch(call, admin, name, parts[2:])
    except services.ServiceError as exc:
        result = (str(exc), True)
    answer_result(call, result)


def _dispatch(call: types.CallbackQuery, admin: dict, name: str, args: list[str]):
    if name == "":
        edit(call, *panel_view())
    elif name == "req":
        _requests(call)
    elif name in ("ok", "no"):
        return _decide(call, admin, int(args[0]), approve=name == "ok")
    elif name == "floors":
        states.clear(call.from_user.id)
        buttons = [btn(f"{f} этаж", f"m:{f}") for f in sched.bookable_floors()]
        edit(call, "🏢 Выберите этаж для управления:", grid_kb(buttons, 4, [btn("⬅️ Назад", "adm")]))
    elif name == "st":
        _starostas(call)
    elif name == "rm":
        target = services.remove_starosta(admin, int(args[0]))
        send_to_user(db.get_user_by_id(target["id"]) or target, render.STAROSTA_REMOVED_TEXT, menu=True)
        _starostas(call)
        return "Роль снята"
    elif name == "stats":
        _stats(call)
    elif name == "log":
        return _send_logs(call)
    elif name == "chg":
        from .common import change_label  # noqa: PLC0415
        items = db.pending_change_requests()
        if not items:
            edit(call, "📝 Заявок на изменение данных нет.", inline(btn("⬅️ Назад", "adm")))
            return None
        lines = ["📝 <b>Заявки на изменение данных</b>", ""]
        rows: list = []
        for i, r in enumerate(items, 1):
            lines.append(f"{i}. " + change_label(r).replace("\n", " → "))
            rows.append([btn(f"✅ {i}. Принять", f"chg:ok:{r['id']}"), btn(f"❌ {i}. Отклонить", f"chg:no:{r['id']}")])
        rows.append(btn("⬅️ Назад", "adm"))
        edit(call, "\n".join(lines), inline(*rows))
    elif name == "rp":
        target = db.get_user_by_id(int(args[0]))
        if not target:
            return "Пользователь не найден", True
        states.set_state(call.from_user.id, "admin_reply", target_id=target["id"])
        send(call.message.chat.id,
             f"↩️ Напишите ответ для <b>{esc(target['surname'] or '—')}</b> (к.{target['room'] or '—'}) "
             "одним сообщением.\n\n/cancel — отменить")
    return None


@states.handler("admin_reply")
def step_admin_reply(message: types.Message, state: states.State) -> None:
    states.clear(message.from_user.id)
    if not services.is_admin(db.get_user(message.from_user.id)):
        return
    target = db.get_user_by_id(state.data["target_id"])
    text = (message.text or "").strip()[:3000]
    if not target:
        send(message.chat.id, "Пользователь не найден.")
        return
    ok = send_to_user(target, render.admin_reply_text(text), render.ADMIN_REPLY_BUTTONS)
    services.actions.info("SUPPORT_REPLY для %s: %r", services.who(target), text[:200])
    send(message.chat.id, "✅ Ответ отправлен." if ok
         else "Не удалось доставить ответ — возможно, пользователь заблокировал бота.")


def _requests(call: types.CallbackQuery) -> None:
    pending = db.pending_starosta_requests()
    if not pending:
        edit(call, "📨 Новых заявок нет.", inline(btn("⬅️ Назад", "adm")))
        return
    lines = ["📨 <b>Заявки на старосту</b>", ""]
    rows: list = []
    for u in pending:
        current = db.starostas(u["floor"])
        cur = (" · сейчас староста: " + ", ".join(f"{esc(s['surname'])} (к.{s['room']})" for s in current)) if current else ""
        username = f" {render.tg_username(u)}" if u.get("username") else ""
        lines.append(f"• {render.name(u)}, к.{u['room']} (этаж {u['floor']}){username}{cur}")
        rows.append([btn(f"✅ {u['surname']} {u['room']}", f"adm:ok:{u['id']}"), btn("❌", f"adm:no:{u['id']}")])
    rows.append(btn("⬅️ Назад", "adm"))
    edit(call, "\n".join(lines), inline(*rows))


def _decide(call: types.CallbackQuery, admin: dict, user_id: int, approve: bool):
    target = services.decide_starosta(admin, user_id, approve)
    send_to_user(target, render.starosta_decided_text(target, approve), menu=approve)
    verdict = "одобрена ✅" if approve else "отклонена ❌"
    if call.message.text and call.message.text.startswith("📨 Заявка на старосту"):
        # нажали кнопку прямо в уведомлении о заявке
        edit(call, f"Заявка {esc(target['surname'])} (к.{target['room']}) {verdict}",
             inline(btn("📨 Остальные заявки", "adm:req")))
    else:
        _requests(call)
    return f"Заявка {verdict}"


def _starostas(call: types.CallbackQuery) -> None:
    items = db.starostas()
    if not items:
        edit(call, "⭐ Старосты пока не назначены.", inline(btn("⬅️ Назад", "adm")))
        return
    lines = ["⭐ <b>Старосты этажей</b>", ""]
    rows: list = []
    for u in items:
        lines.append(f"• {u['floor']} этаж — {esc(u['surname'])} (к.{u['room']})")
        rows.append(btn(f"Снять: {u['surname']} ({u['floor']} эт.)", f"adm:rm:{u['id']}"))
    rows.append(btn("⬅️ Назад", "adm"))
    edit(call, "\n".join(lines), inline(*rows))


def _stats(call: types.CallbackQuery) -> None:
    s = db.stats(sched.now().date())
    floors = "\n".join(f"  {f} этаж: {n}" for f, n in s["by_floor"].items()) or "  —"
    text = ("📊 <b>Статистика</b>\n\n"
            f"Зарегистрировано жильцов: {s['users']}\n{floors}\n\n"
            f"Старост: {s['starostas']} (заявок: {s['pending']})\n"
            f"Предстоящих записей: {s['upcoming']}\n"
            f"Исключено жильцов: {s['banned_users']}, комнат: {s['banned_rooms']}")
    edit(call, text, inline(btn("🔄 Обновить", "adm:stats"), btn("⬅️ Назад", "adm")))


def _send_logs(call: types.CallbackQuery):
    """Логи Telegram-бота и (если есть) VK-бота."""
    sent = 0
    for name in (*LOG_FILES, *(VK_PREFIX + n for n in LOG_FILES)):
        path = config.LOG_DIR / name
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb") as fh:
                bot.send_document(call.message.chat.id, fh, caption=f"📄 {name}")
            sent += 1
    return "Логи отправлены" if sent else ("Логи пока пустые.", True)

