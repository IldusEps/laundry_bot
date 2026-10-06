"""Админ-панель: заявки старост, управление любым этажом, старосты, статистика, логи, ответы жильцам.

Команды те же, что в Telegram-боте: adm | adm:req | adm:ok:<id> | adm:no:<id> | adm:floors | adm:st |
adm:rm:<id> | adm:stats | adm:log | adm:chg | adm:rp:<id>.
"""
from __future__ import annotations

import logging

from ... import config, db, notify, render, services, states
from ... import schedule as sched
from ...logger import LOG_FILES, VK_PREFIX
from ...render import esc
from .. import keyboards as kb
from ..context import Ctx
from ..router import command, menu, state
from .common import cancel_kb
from .manage import change_list_view

log = logging.getLogger(__name__)

BACK = ("⬅️ Назад", "adm")
ADMIN_ONLY = ("⛔ Только для администратора.", True)


def _admin(ctx: Ctx) -> dict | None:
    """Строка администратора или None, если нажал не администратор."""
    user = ctx.user()
    if not services.is_admin(user or {"vk_id": ctx.vk_id}):
        return None
    if user is None:  # аккаунт создаётся при запуске, но на всякий случай
        db.sync_admins(config.ADMIN_IDS, config.VK_ADMIN_IDS, sched.now())
        user = ctx.user()
    return user


def panel_view() -> tuple[str, str]:
    pending = len(db.pending_starosta_requests())
    return "🛠 <b>Админ-панель</b>\n\nВыберите раздел:", kb.inline([
        [(f"📨 Заявки старост ({pending})", "adm:req")],
        [(f"📝 Заявки на изменение данных ({len(db.pending_change_requests())})", "adm:chg")],
        [("🏢 Управление этажом", "adm:floors")],
        [("⭐ Старосты", "adm:st")],
        [("📊 Статистика", "adm:stats"), ("📄 Логи", "adm:log")],
    ])


@menu("menu:admin")
def cmd_panel(ctx: Ctx) -> None:
    if _admin(ctx) is None:
        ctx.send("Команда доступна только администратору.")
        return
    ctx.send(*panel_view())


@command("adm")
def admin_buttons(ctx: Ctx, parts: list[str]):
    admin = _admin(ctx)
    if admin is None:
        return ADMIN_ONLY
    name = parts[1] if len(parts) > 1 else ""
    args = parts[2:]

    if name == "":
        ctx.edit(*panel_view())
    elif name == "req":
        _requests(ctx)
    elif name in ("ok", "no"):
        return _decide(ctx, admin, int(args[0]), approve=name == "ok")
    elif name == "floors":
        states.clear(ctx.key)
        buttons = [(f"{f} этаж", f"m:{f}") for f in sched.bookable_floors()]
        ctx.edit("🏢 Выберите этаж для управления:",
                 kb.inline_paged(buttons, 4, [[BACK]], nav="adm:floors", page=ctx.page))
    elif name == "st":
        _starostas(ctx)
    elif name == "rm":
        target = services.remove_starosta(admin, int(args[0]))
        notify.send_to_user(db.get_user_by_id(target["id"]) or target, render.STAROSTA_REMOVED_TEXT, menu=True)
        _starostas(ctx)
        return "Роль снята"
    elif name == "stats":
        _stats(ctx)
    elif name == "log":
        return _send_logs(ctx)
    elif name == "chg":
        ctx.edit(*change_list_view(db.pending_change_requests(), "📝 <b>Заявки на изменение данных</b>",
                                   "📝 Заявок на изменение данных нет.", BACK, "adm:chg", ctx.page))
    elif name == "rp":
        target = db.get_user_by_id(int(args[0]))
        if not target:
            return "Пользователь не найден", True
        states.set_state(ctx.key, "admin_reply", target_id=target["id"])
        ctx.send(f"↩️ Напишите ответ для <b>{esc(target['surname'] or '—')}</b> (к.{target['room'] or '—'}) "
                 "одним сообщением.", cancel_kb())
    return None


@state("admin_reply")
def step_admin_reply(ctx: Ctx, st: states.State) -> None:
    states.clear(ctx.key)
    if _admin(ctx) is None:
        return
    target = db.get_user_by_id(st.data["target_id"])
    text = ctx.text.strip()[:3000]
    if not target:
        ctx.send("Пользователь не найден.")
        return
    ok = notify.send_to_user(target, render.admin_reply_text(text), render.ADMIN_REPLY_BUTTONS)
    services.actions.info("SUPPORT_REPLY для %s: %r", services.who(target), text[:200])
    ctx.send("✅ Ответ отправлен." if ok
             else "Не удалось доставить ответ — возможно, пользователь запретил сообщения от бота.")


def _requests(ctx: Ctx) -> None:
    pending = db.pending_starosta_requests()
    if not pending:
        ctx.edit("📨 Новых заявок нет.", kb.inline([[BACK]]))
        return
    lines = ["📨 <b>Заявки на старосту</b>", ""]
    buttons = []
    for u in pending:
        current = db.starostas(u["floor"])
        cur = (" · сейчас староста: " + ", ".join(f"{esc(s['surname'])} (к.{s['room']})" for s in current)) if current else ""
        username = f" {render.tg_username(u)}" if u.get("username") else ""
        lines.append(f"• {render.name(u)}, к.{u['room']} (этаж {u['floor']}){username}{cur}")
        buttons += [(f"✅ {u['surname']} {u['room']}", f"adm:ok:{u['id']}"),
                    (f"❌ {u['surname']} {u['room']}", f"adm:no:{u['id']}")]
    ctx.edit("\n".join(lines), kb.inline_paged(buttons, 2, [[BACK]], nav="adm:req", page=ctx.page))


def _decide(ctx: Ctx, admin: dict, user_id: int, approve: bool):
    target = services.decide_starosta(admin, user_id, approve)
    notify.send_to_user(target, render.starosta_decided_text(target, approve), menu=approve)
    verdict = "одобрена ✅" if approve else "отклонена ❌"
    ctx.edit(f"Заявка {esc(target['surname'])} (к.{target['room']}) {verdict}",
             kb.inline([[("📨 Остальные заявки", "adm:req")], [("🛠 Админ-панель", "adm")]]))
    return f"Заявка {verdict}"


def _starostas(ctx: Ctx) -> None:
    items = db.starostas()
    if not items:
        ctx.edit("⭐ Старосты пока не назначены.", kb.inline([[BACK]]))
        return
    lines = ["⭐ <b>Старосты этажей</b>", ""]
    buttons = []
    for u in items:
        lines.append(f"• {u['floor']} этаж — {render.name(u)} (к.{u['room']})")
        buttons.append((f"Снять: {u['surname']} ({u['floor']} эт.)", f"adm:rm:{u['id']}"))
    ctx.edit("\n".join(lines), kb.inline_paged(buttons, 1, [[BACK]], nav="adm:st", page=ctx.page))


def _stats(ctx: Ctx) -> None:
    s = db.stats(sched.now().date())
    floors = "\n".join(f"  {f} этаж: {n}" for f, n in s["by_floor"].items()) or "  —"
    text = ("📊 <b>Статистика</b>\n\n"
            f"Зарегистрировано жильцов: {s['users']}\n{floors}\n\n"
            f"Старост: {s['starostas']} (заявок: {s['pending']})\n"
            f"Предстоящих записей: {s['upcoming']}\n"
            f"Исключено жильцов: {s['banned_users']}, комнат: {s['banned_rooms']}")
    ctx.edit(text, kb.inline([[("🔄 Обновить", "adm:stats"), BACK]]))


def _send_logs(ctx: Ctx):
    """Логи VK-бота и (если есть) Telegram-бота — файлами. Ключу доступа нужно право «Документы»."""
    sent = failed = 0
    for name in (*(VK_PREFIX + n for n in LOG_FILES), *LOG_FILES):
        path = config.LOG_DIR / name
        if not path.exists() or path.stat().st_size == 0:
            continue
        attachment = ctx.client.upload_document(ctx.peer_id, path, title=f"{name}.txt")
        if attachment and ctx.client.send(ctx.peer_id, f"📄 {name}", attachment=attachment):
            sent += 1
        else:
            failed += 1
    if failed:
        return "Не все логи удалось отправить — проверьте право «Документы» у ключа доступа.", True
    return "Логи отправлены" if sent else ("Логи пока пустые.", True)
