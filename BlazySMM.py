from __future__ import annotations
import json
import io
import logging
from logging.handlers import RotatingFileHandler
from functools import wraps
import os
import re
import shutil
import threading
import time
from html import escape
from os.path import exists
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
try:
    import tg_bot.CBT as CBT
except Exception:
    CBT = None
NAME = 'Blazy SMM'
VERSION = '1.1.1'
DESCRIPTION = 'Blazy SMM — автоматизация SMM-заказов FunPay через BlazySMM.'
CREDITS = '@tinechelovec'
DEV = '@tinechelovec'
UUID = '2f61d96e-8ae4-47bd-a924-c5f02b55a137'
SETTINGS_PAGE = True
BIND_TO_DELETE = None
CREATOR_URL = 'https://t.me/tinechelovec'
GROUP_URL = 'https://t.me/dev_thc_chat'
CHANNEL_URL = 'https://t.me/by_thc'
REFERRAL_URL = 'https://blazysmm.com/signup?ref=tinechxhwac1'
INSTRUCTION_URL = os.getenv('BLAZYSMM_INSTRUCTION_URL', 'https://teletype.in/@tinechelovec/Blazy-SMM').strip()
ALT_INSTRUCTION_URL = os.getenv('BLAZYSMM_GITHUB_INSTRUCTION_URL', 'https://github.com/tinechelovec/FPC-Blazy-SMM/blob/main/instructions.md').strip()
SERVICE_CHANNEL_URL = 'https://t.me/BlazySmm'
SERVICE_PERSON_URL = 'https://t.me/blazysmmy'
CB_PLUGINS_LIST_OPEN = f"{getattr(CBT, 'PLUGINS_LIST', '44')}:0" if CBT else '44:0'
SITE = 'https://blazysmm.com'
STORAGE_DIR = os.path.join('storage', 'plugins', 'blazysmm')
OLD_STORAGE_DIR = os.path.join('storage', 'blazysmm')
CFG = os.path.join(STORAGE_DIR, 'config.json')
OLD_CFG = os.path.join(OLD_STORAGE_DIR, 'config.json')
LEGACY_CFG = os.path.join('storage', 'plugins', 'blazysmm_lite.json')
LOG_FILE = os.path.join(STORAGE_DIR, 'logs', 'blazysmm.log')
OLD_LOG_FILE = os.path.join(OLD_STORAGE_DIR, 'logs', 'blazysmm.log')
_LOCK = threading.RLock()
_LOGGER = None
_WIZARDS = {}
PREFIX = f'{UUID}:'
PLUGIN_REPO = 'tinechelovec/FPC-Blazy-SMM'
PLUGIN_REPO_URL = 'https://github.com/' + PLUGIN_REPO
PLUGIN_UPDATE_URL = os.getenv('BLAZYSMM_PLUGIN_UPDATE_URL', '').strip()
BACKUP_DIR = os.path.join(STORAGE_DIR, 'backups')
_UPDATE_CACHE = {}
_AUTO_LOT_FLOW = {}
AUTO_TICKET_SUPPORT = 'https://support.funpay.com'
AUTO_TICKET_BATCH = 100
REMIND_AFTER = 15 * 60
EXPIRE_AFTER = 12 * 60 * 60
STATUS_RU = {'Completed': 'выполнен', 'Partial': 'частично выполнен', 'Canceled': 'отменён', 'Cancelled': 'отменён', 'In progress': 'в работе', 'Processing': 'обрабатывается', 'Pending': 'в очереди', 'Refilling': 'выполняется рефил', 'Awaiting': 'ожидает запуска', 'Unknown': 'статус неизвестен'}
def _migrate_storage_layout() -> None:
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        if exists(OLD_CFG) and (not exists(CFG)):
            shutil.copy2(OLD_CFG, CFG)
        old_logs_dir = os.path.join(OLD_STORAGE_DIR, 'logs')
        new_logs_dir = os.path.join(STORAGE_DIR, 'logs')
        if os.path.isdir(old_logs_dir):
            os.makedirs(new_logs_dir, exist_ok=True)
            for name in os.listdir(old_logs_dir):
                src = os.path.join(old_logs_dir, name)
                dst = os.path.join(new_logs_dir, name)
                if os.path.isfile(src) and (not os.path.exists(dst)):
                    shutil.copy2(src, dst)
        old_backups_dir = os.path.join(OLD_STORAGE_DIR, 'backups')
        new_backups_dir = os.path.join(STORAGE_DIR, 'backups')
        if os.path.isdir(old_backups_dir):
            os.makedirs(new_backups_dir, exist_ok=True)
            for name in os.listdir(old_backups_dir):
                src = os.path.join(old_backups_dir, name)
                dst = os.path.join(new_backups_dir, name)
                if os.path.isfile(src) and (not os.path.exists(dst)):
                    shutil.copy2(src, dst)
    except Exception:
        pass
def _audit(event, **fields):
    global _LOGGER
    with _LOCK:
        if _LOGGER is None:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            _LOGGER = logging.getLogger('FPC.BlazySMM.1_5')
            _LOGGER.setLevel(logging.INFO)
            _LOGGER.propagate = False
            handler = RotatingFileHandler(LOG_FILE, maxBytes=2000000, backupCount=4, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            _LOGGER.addHandler(handler)
        text = json.dumps(fields, ensure_ascii=False, default=str)
        text = re.sub('bz_[A-Za-z0-9_-]+', '[API KEY]', text)
        _LOGGER.info('%s %s', event, text)
def _serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapped
def _buyer_blocked(data, buyer):
    return bool(str(buyer).strip()) and str(buyer).strip().casefold() in {str(item).strip().casefold() for item in data.get('blacklist', [])}
def _balance_failure(cardinal, data, order, lot_id, sid):
    oid = str(order.id)
    cfg = data['settings']
    row = data['orders'].setdefault(oid, {'ts': time.time(), 'chat': str(order.chat_id), 'lot_id': lot_id, 'service': sid, 'buyer': _buyer(order), 'site_order': ''})
    row['failure'] = 'Недостаточно баланса до запуска'
    if cfg.get('auto_deactivate') and str(lot_id).isdigit():
        try:
            fields = cardinal.account.get_lot_fields(int(lot_id))
            fields.active = False
            cardinal.account.save_lot(fields)
            data.setdefault('auto_disabled', {})[lot_id] = time.time()
            _audit('lot_deactivated', lot=lot_id, order=oid)
            _notify_admins(cardinal, data, f'Лот {lot_id} снят с продажи: недостаточно баланса. Включите вручную после пополнения.', 'lot')
        except Exception as exc:
            _log_err(data, f'Лот {lot_id}: не удалось отключить: {exc}')
    if cfg.get('auto_refund') and (not row.get('refund_attempted')) and (not row.get('site_order')):
        row['refund_attempted'] = True
        _save(data)
        ok, error = _refund_fp(cardinal, oid)
        row['refunded'] = ok
        row['refund_error'] = error
        _audit('refund', order=oid, success=ok)
        _notify_admins(cardinal, data, f'Возврат #{oid}: ' + ('выполнен' if ok else f'не подтверждён: {error}'), 'refund')
    _save(data)
_NOTIFY_LABELS = {'new': 'Новый заказ — получена оплата и запрошены данные', 'started': 'Запуск — SMM вернул номер заказа', 'error': 'Ошибки — API, баланс и обработка заказов', 'ticket': 'Авто-тикеты — результат автоматической отправки', 'bonus': 'Бонус — результат выдачи за отзыв', 'refund': 'Возврат — результат возврата оплаты', 'lot': 'Лоты — автоматическое снятие с продажи'}
def _bonus_panel(data):
    cfg = data['settings']
    text = f"🎁 <b>Бонус за отзыв</b>\n\nСостояние: {('ВКЛ' if cfg['bonus_on'] else 'ВЫКЛ')}\nМинимальная оценка: {cfg['bonus_stars']}★\nУслуга BlazySMM: {escape(str(cfg['bonus_sid'] or 'не задана'))}\nКоличество: {cfg['bonus_qty']}\n\nПосле подходящего отзыва бот запрашивает ссылку и выдаёт бонус один раз. Оценка повторно проверяется перед выдачей. Проверка отзывов — в течение 14 дней после запуска."
    rows = []
    rows.append([('🔘 Включить / выключить', PREFIX + 'toggle:bonus_on')])
    rows.extend([[(f"{('✓ ' if cfg['bonus_stars'] == i else '')}{i}★", PREFIX + f'stars:{i}')] for i in range(1, 6)])
    rows.extend([[('🛍 Услуга', PREFIX + 'bonus_service')], [('🔢 Количество', PREFIX + 'bonus_quantity')], [('◀️ Назад', PREFIX + 'plugin_settings')]])
    return (text, _kb(rows))
@_serialized
def _extra_callback(cardinal, c, data, bit):
    bot, tg = (cardinal.telegram.bot, cardinal.telegram)
    chat, user = (c.message.chat.id, c.from_user.id)
    key = (str(chat), str(user))
    tg.clear_state(chat, user)
    _WIZARDS.pop(key, None)
    def show(text, kb):
        _safe_edit(bot, chat, c.message.id, text, kb)
    def wizard(mode, prompt, **values):
        parent = 'auto_tickets' if mode.startswith('auto_ticket_') else 'bonus' if mode.startswith('bonus_') else 'ban' if mode.startswith('ban_') else 'lot_settings'
        _WIZARDS[key] = {'mode': mode, 'return': parent, **values}
        tg.set_state(chat, c.message.id, user, 'bzl_wizard')
        bot.send_message(chat, prompt, reply_markup=_return_kb(parent, '❌ Отмена'))
    if bit == 'cancel_wizard':
        _WIZARDS.pop(key, None)
        tg.clear_state(chat, user)
        show(_plugin_settings_text(data), _plugin_settings_kb())
    elif bit == 'notifications' or bit.startswith('notify:'):
        if bit.startswith('notify:'):
            name = bit.split(':', 1)[1]
            if name not in _NOTIFY_LABELS:
                return True
            setting = 'notify_' + name
            data['settings'][setting] = not data['settings'].get(setting, True)
            _save(data)
        labels = {'new': '🛒 Новый заказ', 'started': '🚀 Запуск', 'error': '⚠️ Ошибки', 'ticket': '🎫 Авто-тикеты', 'bonus': '🎁 Бонус', 'refund': '💸 Возврат', 'lot': '🛒 Лоты'}
        buttons = [(('✅ ' if data['settings'].get('notify_' + name, True) else '➖ ') + label, PREFIX + 'notify:' + name) for name, label in labels.items()]
        rows = [[button] for button in buttons]
        rows.append([('◀️ Назад', PREFIX + 'plugin_settings')])
        show('🔔 <b>Уведомления продавцу</b>\n\nПриходят в Telegram-чаты, где открывали панель плагина. Нажмите кнопку, чтобы переключить событие.\n\n' + '\n'.join(('• ' + escape(label) for label in _NOTIFY_LABELS.values())), _kb(rows))
    elif bit == 'auto_tickets':
        show(_auto_ticket_text(data), _auto_ticket_kb(data))
    elif bit == 'auto_ticket_toggle':
        cfg = data.setdefault('settings', {})
        cfg['auto_ticket_enabled'] = not bool(cfg.get('auto_ticket_enabled'))
        if cfg['auto_ticket_enabled']:
            cfg['auto_ticket_last_check'] = 0
            cfg['auto_ticket_last_send'] = 0
        _save(data)
        show(_auto_ticket_text(data), _auto_ticket_kb(data))
    elif bit == 'auto_ticket_check_cd':
        wizard('auto_ticket_check_cd', 'Введите КД проверки статусов SMM в минутах: от 1 до 10080.')
    elif bit == 'auto_ticket_send_cd':
        wizard('auto_ticket_send_cd', 'Введите КД отправки тикетов в минутах: от 1 до 10080.')
    elif bit == 'auto_ticket_check_now':
        if not data.setdefault('settings', {}).get('auto_ticket_enabled'):
            show('🎫 <b>Авто-тикеты выключены.</b>\n\nСначала включите функцию.', _auto_ticket_kb(data))
        else:
            result = _auto_ticket_refresh(cardinal, data, force=True)
            _save(data)
            show(f"🎫 <b>Проверка завершена</b>\n\nПроверено заказов: <b>{result.get('checked', 0)}</b>\nГотовы к тикету: <b>{len(_auto_ticket_ready_rows(data))}</b>\nОшибок API: <b>{result.get('errors', 0)}</b>", _auto_ticket_kb(data))
    elif bit == 'auto_ticket_send_now':
        if not data.setdefault('settings', {}).get('auto_ticket_enabled'):
            show('🎫 <b>Авто-тикеты выключены.</b>\n\nСначала включите функцию.', _auto_ticket_kb(data))
        else:
            _auto_ticket_refresh(cardinal, data, force=True)
            result = _auto_ticket_send(cardinal, data, force=True)
            _save(data)
            errors = result.get('errors') or []
            note = f"🎫 <b>Отправка завершена</b>\n\nЗаказов отправлено: <b>{result.get('sent', 0)}</b>\nТикетов создано: <b>{result.get('tickets', 0)}</b>"
            if errors:
                note += '\n\n⚠️ ' + escape('; '.join(errors[:3]))
            show(note, _auto_ticket_kb(data))
    elif bit.startswith('toggle:'):
        name = bit.split(':', 1)[1]
        name = {'lc': 'link_confirm_enabled', 'sc': 'status_command_enabled'}.get(name, name)
        if name not in ('auto_refund', 'auto_deactivate', 'bonus_on', 'link_confirm_enabled', 'status_command_enabled'):
            return True
        data['settings'][name] = not data['settings'].get(name, False)
        _save(data)
        if name == 'bonus_on':
            show(*_bonus_panel(data))
        else:
            show(_order_settings_text(data), _order_settings_kb())
    elif bit == 'bonus' or bit.startswith('stars:'):
        if bit.startswith('stars:'):
            stars = int(bit.split(':', 1)[1])
            if stars not in range(1, 6):
                return True
            data['settings']['bonus_stars'] = stars
            _save(data)
        show(*_bonus_panel(data))
    elif bit == 'bonus_service':
        wizard('bonus_service', 'Введите ID услуги BlazySMM для бонуса.')
    elif bit == 'bonus_quantity':
        wizard('bonus_quantity', 'Введите количество единиц бонуса (целое число больше нуля).')
    elif bit in ('bindnew', 'lot_manual') or bit.startswith('Lbind:'):
        if bit.startswith('Lbind:'):
            wizard('service', 'Введите ID услуги BlazySMM.', lot=bit[6:])
        else:
            wizard('lot', 'Шаг 1/3. Введите ID вашего лота FunPay.')
    elif bit == 'ban':
        items = data.get('blacklist', [])
        text = '🛡 <b>Чёрный список</b>\n\nЭти покупатели не получают SMM-заказы и бонусы. Совпадение ника точное, без учёта регистра.\n\n'
        text += '\n'.join(('• ' + escape(str(item)) for item in items)) or 'Список пуст.'
        show(text[:3900], _kb([[('➕ Добавить', PREFIX + 'ban_add')], [('➖ Удалить', PREFIX + 'ban_remove')], [('◀️ Назад', PREFIX + 'plugin_settings')]]))
    elif bit in ('ban_add', 'ban_remove'):
        wizard(bit, 'Введите точный ник покупателя FunPay.')
    elif bit == 'logs':
        _audit('logs_export', actor=user)
        try:
            if _LOGGER is not None:
                for handler in list(getattr(_LOGGER, 'handlers', []) or []):
                    try:
                        handler.flush()
                    except Exception:
                        pass
        except Exception:
            pass
        chunks = []
        for n in (4, 3, 2, 1, 0):
            path = LOG_FILE + (f'.{n}' if n else '')
            if not os.path.exists(path):
                continue
            try:
                with open(path, 'rb') as src:
                    raw = src.read()
                if raw:
                    chunks.append(raw.rstrip(b'\r\n') + b'\n')
            except Exception:
                pass
        payload = io.BytesIO(b''.join(chunks))
        payload.name = 'BlazySMM.log'
        bot.send_document(chat, payload, caption='Логи BlazySMM (.log): этапы заказов, сообщения и ошибки.')
    elif bit == 'config':
        show('⚙️ <b>Конфиг</b>\n\nЭкспорт содержит API-ключ, настройки, привязки и чёрный список. История заказов и активные сессии при импорте сохраняются. Перед импортом создаётся резервная копия.', _kb([[('📥 Скачать', PREFIX + 'config_export')], [('📤 Импортировать', PREFIX + 'config_import')], [('◀️ Назад', PREFIX + 'maintenance')]]))
    elif bit == 'config_export':
        payload = {name: data.get(name) for name in _CONFIG_FIELDS}
        payload['schema_version'] = 1
        file = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode())
        file.name = 'BlazySMM_config.json'
        bot.send_document(chat, file, caption='Конфиг содержит API-ключ. Храните его у себя.')
        _audit('config_export', actor=user)
    elif bit == 'config_import':
        tg.set_state(chat, c.message.id, user, 'bzl_config_import')
        bot.send_message(chat, 'Пришлите JSON-конфиг файлом (до 2 МБ).', reply_markup=_return_kb('config', '❌ Отмена'))
    else:
        tg.clear_state(chat, user)
        _WIZARDS.pop(key, None)
        return False
    return True
_CONFIG_FIELDS = ('key', 'settings', 'lots', 'blacklist', 'off_lots', 'off_ids')
def _validated_config(raw):
    if len(raw) > 2000000:
        raise ValueError('Размер конфига превышает 2 МБ')
    source = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(source, dict):
        raise ValueError('Ожидается JSON-объект')
    if source.get('schema_version', 1) != 1:
        raise ValueError('Неподдерживаемая версия конфига')
    if not any((name in source for name in _CONFIG_FIELDS)):
        raise ValueError('В файле нет настроек BlazySMM')
    result = {}
    for name in _CONFIG_FIELDS:
        if name not in source:
            continue
        value = source[name]
        if name == 'key':
            if not isinstance(value, str) or (value and (not re.fullmatch('bz_[A-Za-z0-9_-]{8,}', value))):
                raise ValueError('Неверный формат API-ключа')
        elif name in ('blacklist', 'off_lots', 'off_ids'):
            if not isinstance(value, list) or len(value) > 1000 or any((not isinstance(x, str) or len(x) > 200 for x in value)):
                raise ValueError('Неверный список: ' + name)
        elif name == 'lots':
            if not isinstance(value, dict):
                raise ValueError('lots должен быть объектом')
            for lid, row in value.items():
                if not isinstance(row, dict) or not str(row.get('sid', '')).isdigit():
                    raise ValueError('Неверная привязка лота: ' + lid)
                if type(row.get('per', 1)) is not int or row.get('per', 1) < 1:
                    raise ValueError('Количество лота должно быть положительным целым')
                for field in ('bonus_qty',):
                    if field in row and (type(row[field]) is not int or row[field] < 1):
                        raise ValueError('Неверное количество бонуса лота')
        elif name == 'settings':
            if not isinstance(value, dict):
                raise ValueError('settings должен быть объектом')
            clean = {}
            bools = {'plugin_enabled', 'auto_refund', 'auto_deactivate', 'bonus_on', 'link_confirm_enabled', 'status_command_enabled', 'auto_ticket_enabled'} | {'notify_' + n for n in _NOTIFY_LABELS}
            for key, item in value.items():
                if key in bools:
                    if type(item) is not bool:
                        raise ValueError('Нужен true/false: ' + key)
                elif key in ('bonus_stars', 'bonus_qty', 'auto_ticket_check_minutes', 'auto_ticket_send_minutes'):
                    if type(item) is not int or item < 1 or (key == 'bonus_stars' and item > 5) or (key.startswith('auto_ticket_') and item > 10080):
                        raise ValueError('Неверное число: ' + key)
                elif key in ('bonus_sid', 'tpl_ask', 'tpl_done', 'tpl_err'):
                    if not isinstance(item, str) or len(item) > 1500:
                        raise ValueError('Неверный текст: ' + key)
                    if key == 'bonus_sid' and item and (not item.isdigit()):
                        raise ValueError('ID бонуса должен быть числом')
                else:
                    continue
                clean[key] = item
            value = clean
        result[name] = value
    return result
def _import_config(cardinal, m):
    bot = cardinal.telegram.bot
    try:
        if not str(m.document.file_name).lower().endswith('.json'):
            raise ValueError('Нужен файл .json')
        if (m.document.file_size or 0) > 2000000:
            raise ValueError('Файл больше 2 МБ')
        info = bot.get_file(m.document.file_id)
        payload = _validated_config(bot.download_file(info.file_path))
        with _LOCK:
            data = _load()
            os.makedirs(BACKUP_DIR, exist_ok=True)
            backup = os.path.join(BACKUP_DIR, f'config_{time.time_ns()}.json')
            with open(backup, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            for name, value in payload.items():
                if name == 'settings':
                    data['settings'].update(value)
                else:
                    data[name] = value
            _save(data)
        cardinal.telegram.clear_state(m.chat.id, m.from_user.id)
        _audit('config_import', actor=m.from_user.id)
        bot.send_message(m.chat.id, '✅ Конфиг импортирован. История заказов и активные сессии сохранены.', reply_markup=_return_kb('config'))
    except Exception as exc:
        _audit('config_import_failed', error=type(exc).__name__)
        bot.send_message(m.chat.id, '❌ Импорт не выполнен: ' + str(exc)[:300])
@_serialized
def _wizard_message(cardinal, m):
    bot = cardinal.telegram.bot
    key = (str(m.chat.id), str(m.from_user.id))
    flow = _WIZARDS.get(key)
    if not flow:
        cardinal.telegram.clear_state(m.chat.id, m.from_user.id)
        bot.send_message(m.chat.id, 'Форма устарела. Откройте настройку заново.')
        return
    raw = str(m.text or '').strip()
    data = _load()
    mode = flow['mode']
    if mode == 'fp_categories':
        try:
            category_ids = _parse_funpay_category_ids(raw)
            result = _discover_selected_funpay_lots(cardinal, data, category_ids)
        except Exception as exc:
            bot.send_message(m.chat.id, '❌ ' + str(exc)[:250], reply_markup=_return_kb('lot_settings', '❌ Отмена'))
            return
        _FP_RESULTS[key] = result
        _WIZARDS.pop(key, None)
        cardinal.telegram.clear_state(m.chat.id, m.from_user.id)
        text, markup = _fp_results_panel(data, result)
        bot.send_message(m.chat.id, text, parse_mode='HTML', reply_markup=markup)
        return
    if mode == 'lot_bonus':
        lots = _lots(data)
        row = lots.setdefault(flow['lot'], {'sid': '', 'title': flow['lot'], 'by': 'id'})
        if raw.lower() == 'выкл':
            row['bonus_off'] = True
        elif raw == '0':
            row.pop('bonus_sid', None)
            row.pop('bonus_off', None)
        else:
            if not re.fullmatch('[1-9][0-9]{0,11}', raw):
                bot.send_message(m.chat.id, 'Нужен ID услуги, 0 или выкл.')
                return
            info = _api(data, 'info', service=raw)
            if not info.get('ok'):
                bot.send_message(m.chat.id, 'Услуга не найдена или API недоступен. Попробуйте ещё раз.')
                return
            row['bonus_sid'] = raw
            row.pop('bonus_off', None)
        data['lots'] = lots
        note = '✅ Бонус лота сохранён.'
    elif mode in ('ban_add', 'ban_remove'):
        if not raw or len(raw) > 80 or any((c.isspace() for c in raw)):
            bot.send_message(m.chat.id, 'Введите один ник без пробелов, до 80 символов.')
            return
        if mode == 'ban_add':
            if not _buyer_blocked(data, raw):
                data['blacklist'].append(raw)
        else:
            data['blacklist'] = [x for x in data['blacklist'] if x.casefold() != raw.casefold()]
        note = '✅ Чёрный список обновлён.'
    elif mode in ('auto_ticket_check_cd', 'auto_ticket_send_cd'):
        if not re.fullmatch(r'\d{1,5}', raw):
            bot.send_message(m.chat.id, 'Нужно целое число минут от 1 до 10080.')
            return
        value = int(raw)
        if not 1 <= value <= 10080:
            bot.send_message(m.chat.id, 'Нужно число от 1 до 10080 минут.')
            return
        setting = 'auto_ticket_check_minutes' if mode == 'auto_ticket_check_cd' else 'auto_ticket_send_minutes'
        data.setdefault('settings', {})[setting] = value
        note = f"✅ {'КД проверки' if mode == 'auto_ticket_check_cd' else 'КД отправки'}: {value} мин."
    else:
        if not re.fullmatch('[1-9][0-9]{0,11}', raw):
            bot.send_message(m.chat.id, 'Нужно целое положительное число (до 12 цифр).')
            return
        if mode == 'lot':
            try:
                lot_item = _validate_funpay_lot(data, raw)
            except Exception as exc:
                bot.send_message(m.chat.id, 'Не удалось проверить ваш лот: ' + str(exc)[:200])
                return
            flow.update(mode='service', lot=raw, lot_title=str((lot_item or {}).get('title') or raw), **{'return': 'lc:' + raw})
            _UI_CONTEXT.setdefault(key, {})['lot_back'] = 'lot_settings'
            bot.send_message(m.chat.id, 'Шаг 2/3. Введите ID услуги BlazySMM.', reply_markup=_return_kb('lc:' + raw, '❌ Отмена'))
            return
        if mode in ('service', 'bonus_service'):
            got = _api(data, 'info', service=raw)
            if not got.get('ok') or not isinstance(got.get('raw'), dict):
                bot.send_message(m.chat.id, 'Не удалось проверить услугу: ' + str(got.get('error', 'пустой ответ'))[:200])
                return
            if mode == 'service':
                flow.update(mode='quantity', sid=raw)
                bot.send_message(m.chat.id, 'Шаг 3/3. Сколько единиц SMM выдавать за 1 единицу FunPay? Например: 100.', reply_markup=_return_kb(flow.get('return', 'lot_settings'), '❌ Отмена'))
                return
            data['settings']['bonus_sid'] = raw
            note = '✅ Услуга бонуса сохранена.'
        elif mode == 'bonus_quantity':
            data['settings']['bonus_qty'] = int(raw)
            note = '✅ Количество бонуса сохранено.'
        elif mode in ('quantity', 'lot_quantity'):
            lot = flow['lot']
            lots = _lots(data)
            row = lots.setdefault(lot, {'title': lot, 'by': 'id'})
            if mode == 'quantity':
                row['sid'] = flow['sid']
                lot_title = str(flow.get('lot_title') or '').strip()
                if lot_title and lot_title != lot:
                    row['title'] = lot_title
            row['per'] = int(raw)
            data['lots'] = lots
            note = f"✅ Лот {lot} → услуга {row.get('sid') or 'не выбрана'}. За 1 единицу FunPay: {raw} единиц SMM."
        else:
            return
    _save(data)
    _audit('setting_saved', actor=m.from_user.id, setting=mode)
    _WIZARDS.pop(key, None)
    cardinal.telegram.clear_state(m.chat.id, m.from_user.id)
    parent = flow.get('return', 'settings')
    markup = _lot_kb(data, flow['lot'], _lot_back(_UI_CONTEXT.get(key, {}))) if mode in ('quantity', 'lot_quantity', 'lot_bonus') else _return_kb(parent)
    bot.send_message(m.chat.id, note, reply_markup=markup)
_UI_CONTEXT = {}
_FP_RESULTS = {}
_STATS_PERIODS = {'24h': ('24 часа', 86400), '7d': ('7 дней', 604800), '30d': ('30 дней', 2592000), 'all': ('Всё время', None)}
def _parse_funpay_category_ids(raw):
    text = str(raw or '').strip()
    if not re.fullmatch('\\d+(?:[\\s,;]+\\d+)*', text):
        raise ValueError('Нужны только ID категорий FunPay через пробел или запятую. Например: 123, 456.')
    ids = list(dict.fromkeys((int(x) for x in re.findall('\\d+', text))))
    if not ids or len(ids) > 20 or any((x <= 0 or x > 10 ** 9 for x in ids)):
        raise ValueError('Укажите от 1 до 20 положительных ID категорий.')
    return ids
def _discover_selected_funpay_lots(cardinal, data, category_ids):
    from FunPayAPI.common.enums import SubCategoryTypes
    wanted = set(category_ids)
    account = cardinal.account
    profile = account.get_user(int(account.id))
    if int(getattr(profile, 'id', 0)) != int(account.id):
        raise ValueError('Не удалось подтвердить профиль продавца FunPay.')
    grouped = profile.get_sorted_lots(2)
    if not isinstance(grouped, dict):
        raise ValueError('FunPay вернул неизвестный формат списка лотов.')
    found, matched = ({}, set())
    for subcategory, lots in grouped.items():
        category_id = getattr(subcategory, 'id', None)
        if category_id not in wanted or getattr(subcategory, 'type', None) != SubCategoryTypes.COMMON:
            continue
        matched.add(category_id)
        iterable = lots.items() if isinstance(lots, dict) else [(getattr(lot, 'id', ''), lot) for lot in lots]
        for lot_id, lot in iterable:
            lot_id = str(lot_id)
            if not lot_id.isdigit():
                continue
            found[lot_id] = {'lot_id': lot_id, 'title': _lot_field_title(lot, lot_id), 'active': bool(getattr(lot, 'active', True)), 'category_id': category_id}
    if len(found) > 500:
        raise ValueError('Найдено больше 500 лотов. Выберите меньше категорий за один поиск.')
    cached = {str(x['lot_id']): x for x in _cached_funpay_lots(data)}
    cached.update(found)
    _save_funpay_lot_cache(data, list(cached.values()))
    _audit('funpay_categories_scanned', categories=category_ids, found=len(found))
    return {'rows': sorted(found.values(), key=lambda x: (x['category_id'], x['title'].casefold(), int(x['lot_id']))), 'categories': list(category_ids), 'empty': sorted(wanted - matched), 'page': 0}
def _fp_results_panel(data, result, page=0):
    lots = result['rows']
    pages = max(1, (len(lots) + 7) // 8)
    page = max(0, min(int(page), pages - 1))
    result['page'] = page
    text = f"✨ <b>Автодобавление лотов</b>\n\n📂 Категории FunPay: <code>{', '.join(map(str, result['categories']))}</code>\n📦 Найдено ваших лотов: <b>{len(lots)}</b>\n\nВыберите лот, чтобы указать услугу BlazySMM и количество.\nПоказаны доступные лоты из вашего профиля, только выбранных категорий."
    if result.get('empty'):
        text += '\n\n🔎 Нет лотов в профиле для ID: ' + ', '.join(map(str, result['empty']))
    text += f'\n\n📄 Страница {page + 1} / {pages}'
    mappings = _lots(data)
    rows = [[(f"{('✅' if mappings.get(item['lot_id'], {}).get('sid') else '➕')} {item['title'][:30]} · {item['lot_id']}", PREFIX + 'fp_pick:' + item['lot_id'])] for item in lots[page * 8:(page + 1) * 8]]
    nav = []
    if page:
        nav.append(('⬅️', PREFIX + f'fp_page:{page - 1}'))
    if page + 1 < pages:
        nav.append(('➡️', PREFIX + f'fp_page:{page + 1}'))
    if nav:
        rows.append(nav)
    rows.extend([[('📂 Другие категории', PREFIX + 'ac')], [('◀️ К настройке лотов', PREFIX + 'lot_settings')]])
    return (text, _kb(rows))
def _status_bucket(oid, row, data):
    if row.get('refunded'):
        return 'refunded'
    if row.get('site_order'):
        values = [str(s).strip().lower() for s in row.get('statuses', [])]
        expected = len([x for x in str(row['site_order']).split(',') if x.strip()])
        if len(values) != expected or not values:
            return 'unknown'
        if all((s == 'completed' for s in values)):
            return 'completed'
        if all((s in ('canceled', 'cancelled') for s in values)):
            return 'canceled'
        if any((s in ('pending', 'in progress', 'processing') for s in values)):
            return 'active'
        if 'partial' in values or ('completed' in values and any((s in ('canceled', 'cancelled') for s in values))):
            return 'partial'
        return 'unknown'
    if row.get('submission_attempted') or row.get('refund_attempted'):
        return 'review'
    if row.get('failure'):
        return 'failed'
    if any((str(s.get('order')) == str(oid) for s in data.get('sessions', {}).values())):
        return 'waiting'
    return 'stopped'
def _stats_text(data, period='all'):
    label, seconds = _STATS_PERIODS.get(period, _STATS_PERIODS['all'])
    now = time.time()
    orders = [(oid, row) for oid, row in data.get('orders', {}).items() if seconds is None or 0 <= now - float(row.get('ts') or 0) <= seconds]
    counts = {}
    for oid, row in orders:
        status = _status_bucket(oid, row, data)
        counts[status] = counts.get(status, 0) + 1
    started = sum((bool(r.get('site_order')) for _, r in orders))
    bonuses = sum((bool(r.get('bonus_order')) for _, r in orders))
    lines = ['📊 <b>Статистика BlazySMM</b>', f'🗓 Период: <b>{label}</b>', '', f'🛒 Заказов FunPay: <b>{len(orders)}</b>', f'🚀 Запущено в SMM: <b>{started}</b>', f'🎁 Бонусов выдано: <b>{bonuses}</b>', '', '<b>Состояние заказов</b>']
    for state, title in [('completed', '✅ Выполнены'), ('active', '⏳ В работе / очереди'), ('partial', '🟠 Частично выполнены'), ('canceled', '⛔ Отменены в SMM'), ('refunded', '💸 Возврат на FunPay'), ('waiting', '💬 Ждут покупателя'), ('review', '🔎 Нужна проверка продавца'), ('failed', '⚠️ Ошибка до запуска'), ('stopped', '⏹ Не запущены / ожидание окончено'), ('unknown', '❔ Статус не подтверждён')]:
        if counts.get(state) or state in ('completed', 'active', 'refunded'):
            lines.append(f'{title}: <b>{counts.get(state, 0)}</b>')
    if orders:
        lines += ['', f"🎯 Выполнены: <b>{counts.get('completed', 0) / len(orders):.0%}</b> от заказов периода"]
    top = {}
    for _, row in orders:
        sid = str(row.get('service') or '')
        if sid:
            top[sid] = top.get(sid, 0) + 1
    if top:
        lines += ['', '<b>🏆 Частые услуги</b>']
        lines += [f'• #{escape(sid)} — {count} заказов' for sid, count in sorted(top.items(), key=lambda x: (-x[1], x[0]))[:3]]
    return '\n'.join(lines)
def _stats_kb(period='all'):
    buttons = [(('✓ ' if key == period else '') + label, PREFIX + 'stats:' + key) for key, (label, _) in _STATS_PERIODS.items()]
    return _kb([buttons[:2], buttons[2:], [('📋 Заказы периода', PREFIX + 'orders:stats:' + period), ('🔄 Обновить', PREFIX + 'stats:' + period)], [('◀️ К настройкам', PREFIX + 'settings')]])
def _orders_panel(data, parent='order_settings', page=0, period='all'):
    seconds = _STATS_PERIODS.get(period, _STATS_PERIODS['all'])[1]
    now = time.time()
    rows = [(oid, row) for oid, row in data.get('orders', {}).items() if seconds is None or 0 <= now - float(row.get('ts') or 0) <= seconds]
    rows.sort(key=lambda x: float(x[1].get('ts') or 0), reverse=True)
    pages = max(1, (len(rows) + 7) // 8)
    page = max(0, min(page, pages - 1))
    labels = {'completed': '✅ Выполнен', 'active': '⏳ В работе', 'partial': '🟠 Частично', 'canceled': '⛔ Отменён', 'refunded': '💸 Возврат', 'waiting': '💬 Ждёт данных', 'review': '🔎 Нужна проверка', 'failed': '⚠️ Ошибка', 'stopped': '⏹ Не запущен', 'unknown': '❔ Не подтверждён'}
    lines = ['📋 <b>Заказы</b>', f'📄 Страница {page + 1} / {pages}', '']
    for oid, row in rows[page * 8:(page + 1) * 8]:
        lines.append(f"<b>#{escape(str(oid))}</b> · {labels[_status_bucket(oid, row, data)]}\n🛍 Услуга {escape(str(row.get('service') or '—'))} · {_fmt_when(row.get('ts') or 0)}")
    if not rows:
        lines.append('Заказов за этот период пока нет.')
    scope = 'stats:' + period if parent.startswith('stats:') else 'order'
    nav = []
    if page:
        nav.append(('⬅️', PREFIX + f'orders:{scope}:{page - 1}'))
    if page + 1 < pages:
        nav.append(('➡️', PREFIX + f'orders:{scope}:{page + 1}'))
    keyboard = [nav] if nav else []
    keyboard += [[('🔄 Обновить список', PREFIX + f'orders:{scope}:{page}')], [('◀️ Назад', PREFIX + parent)]]
    return ('\n\n'.join(lines), _kb(keyboard))
def _return_kb(action, label='◀️ Назад'):
    return _kb([[(label, PREFIX + action)]])
def _lot_back(context):
    return context.get('lot_back', 'lot_settings')
def _navigation_callback(cardinal, c, data, bit):
    key = (str(c.message.chat.id), str(c.from_user.id))
    ctx = _UI_CONTEXT.setdefault(key, {})
    tg, bot = (cardinal.telegram, cardinal.telegram.bot)
    chat = c.message.chat.id
    def show(text, kb):
        _safe_edit(bot, chat, c.message.id, text, kb)
    def clear():
        tg.clear_state(chat, c.from_user.id)
        _WIZARDS.pop(key, None)
    def wizard(mode, prompt, parent, **kw):
        clear()
        _WIZARDS[key] = {'mode': mode, 'return': parent, **kw}
        tg.set_state(chat, c.message.id, c.from_user.id, 'bzl_wizard')
        show(prompt, _return_kb(parent, '❌ Отмена'))
    if bit.startswith(('as:', 'av:', 'al:', 'ab:')):
        clear()
        show('Автодобавление обновлено. Сначала введите ID категорий FunPay.', _return_kb('ac'))
    elif bit in ('ac', 'lot_discover', 'lot_discover_all'):
        wizard('fp_categories', '✨ <b>Автодобавление лотов</b>\n\nВведите ID категорий FunPay через пробел или запятую.\nНапример: <code>123, 456</code>\n\nID — число из адреса <code>funpay.com/lots/123/</code>.\nБудут найдены ваши лоты только в указанных категориях.', 'lot_settings')
    elif bit.startswith('fp_page:'):
        clear()
        result = _FP_RESULTS.get(key)
        if result is None:
            show('Список поиска устарел. Введите категории заново.', _return_kb('ac'))
        else:
            show(*_fp_results_panel(data, result, _int(bit.split(':')[1])))
    elif bit.startswith(('fp_pick:', 'lot_pick:', 'lc:', 'L:')):
        clear()
        lid = bit.split(':', 1)[1]
        if not lid.isdigit():
            return True
        if bit.startswith('fp_pick:'):
            result = _FP_RESULTS.get(key)
            if not result or lid not in {r['lot_id'] for r in result['rows']}:
                show('Список поиска устарел. Выполните поиск ещё раз.', _return_kb('ac'))
                return True
            ctx['lot_back'] = 'fp_page:' + str(result['page'])
        elif bit.startswith(('lot_pick:', 'L:')):
            ctx['lot_back'] = 'lot_settings:' + str(ctx.get('lot_page', 0))
        ctx['lot'] = lid
        show(_lot_card(data, lid), _lot_kb(data, lid, _lot_back(ctx)))
    elif bit == 'lot_manual' or bit == 'bindnew':
        wizard('lot', '➕ <b>Добавить LOT ID</b>\n\nШаг 1/3. Введите ID вашего лота FunPay.', 'lot_settings')
    elif bit.startswith(('Lbind:', 'Lp:', 'Lb:')):
        action, lid = bit.split(':', 1)
        if not lid.isdigit():
            return True
        ctx['lot'] = lid
        if action == 'Lbind':
            wizard('service', '🔗 Введите ID услуги BlazySMM.', 'lc:' + lid, lot=lid)
        elif action == 'Lp':
            wizard('lot_quantity', '🔢 Сколько единиц SMM выдавать за 1 единицу FunPay?', 'lc:' + lid, lot=lid)
        else:
            wizard('lot_bonus', '🎁 Введите ID бонусной услуги.\n0 — общий бонус; выкл — отключить бонус лота.', 'lc:' + lid, lot=lid)
    elif bit.startswith(('Lon:', 'Loff:', 'Ldel:', 'Lfp:')):
        clear()
        action, lid = bit.split(':', 1)
        if action == 'Ldel':
            data['lots'].pop(lid, None)
            _save(data)
        elif action == 'Lon':
            data['off_ids'] = list(dict.fromkeys(data.get('off_ids', []) + [lid]))
            _save(data)
        elif action == 'Loff':
            data['off_ids'] = [x for x in data.get('off_ids', []) if str(x) != lid]
            _save(data)
        elif action == 'Lfp':
            ok, msg, _ = _toggle_funpay_lot_active(data, lid)
            if not ok:
                try:
                    bot.send_message(chat, f'❌ <b>Не удалось изменить состояние лота</b>\n\n<code>{escape(str(msg))}</code>', parse_mode='HTML')
                except Exception:
                    pass
        show(_lot_card(data, lid), _lot_kb(data, lid, _lot_back(ctx)))
    elif bit == 'cancel_wizard':
        flow = _WIZARDS.get(key, {})
        parent = flow.get('return', 'settings')
        clear()
        c.data = PREFIX + parent
        return _navigation_callback(cardinal, c, data, parent) or _extra_callback(cardinal, c, data, parent)
    elif bit == 'stats' or bit.startswith('stats:'):
        clear()
        period = bit.split(':', 1)[1] if ':' in bit else 'all'
        if period not in _STATS_PERIODS:
            period = 'all'
        show(_stats_text(data, period), _stats_kb(period))
    elif bit == 'orders' or bit.startswith('orders:'):
        clear()
        parts = bit.split(':')
        period = parts[2] if len(parts) > 2 and parts[1] == 'stats' else 'all'
        parent = 'stats:' + period if len(parts) > 1 and parts[1] == 'stats' else 'order_settings'
        page = _int(parts[3]) if len(parts) > 3 else _int(parts[2]) if len(parts) > 2 and parts[1] == 'order' else 0
        show(*_orders_panel(data, parent, page, period))
    elif bit in ('settings', 'home', 'plugin_settings', 'lot_settings', 'order_settings', 'maintenance', 'state', 'info', 'update', 'account_settings') or bit.startswith('lot_settings:'):
        clear()
        if bit.startswith('lot_settings'):
            page = _int(bit.split(':')[1]) if ':' in bit else 0
            ctx['lot_page'] = page
            show(_lot_settings_text(data, page), _lot_settings_kb(data, page))
        elif bit == 'settings':
            show(_settings_text(data), _settings_kb())
        elif bit == 'home':
            show(_home_text(data), _home_kb())
        elif bit == 'plugin_settings':
            show(_plugin_settings_text(data), _plugin_settings_kb())
        elif bit == 'order_settings':
            show(_order_settings_text(data), _order_settings_kb(data))
        elif bit == 'maintenance':
            show(_maintenance_text(data), _maintenance_kb())
        elif bit == 'state':
            show(_state_text(data), _state_kb(data))
        elif bit == 'info':
            show(_info_text(), _info_kb())
        elif bit == 'update':
            show(_update_text(), _update_kb())
        elif bit == 'account_settings':
            show(_account_settings_text(data), _account_settings_kb(data))
    else:
        return False
    return True
def _load() -> dict:
    data = {}
    _migrate_storage_layout()
    os.makedirs(STORAGE_DIR, exist_ok=True)
    if not exists(CFG) and exists(LEGACY_CFG):
        shutil.copy2(LEGACY_CFG, CFG)
        _audit('migration', source='legacy_config')
    if exists(CFG):
        try:
            with open(CFG, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as exc:
            _audit('config_read_failed', error=type(exc).__name__)
            raise RuntimeError('Конфиг повреждён; восстановите резервную копию') from exc
    data.setdefault('url', SITE)
    data.setdefault('key', '')
    data.setdefault('sessions', {})
    data.setdefault('orders', {})
    data.setdefault('lots', {})
    data.setdefault('off_lots', [])
    data.setdefault('off_ids', [])
    data.setdefault('seen', [])
    data.setdefault('lot_cache', [])
    data.setdefault('blacklist', [])
    data.setdefault('errors', [])
    data.setdefault('admin_chats', [])
    settings = data.setdefault('settings', {})
    settings.setdefault('plugin_enabled', True)
    for old_key in ('ticket_on', 'ticket_hours'):
        settings.pop(old_key, None)
    for row in data['orders'].values():
        row.pop('ticket_due', None)
        row.pop('ticket_sent', None)
    settings.setdefault('bonus_stars', 5)
    settings.setdefault('auto_refund', False)
    settings.setdefault('auto_deactivate', False)
    settings.setdefault('link_confirm_enabled', True)
    settings.setdefault('status_command_enabled', True)
    settings.setdefault('auto_ticket_enabled', False)
    settings.setdefault('auto_ticket_check_minutes', 10)
    settings.setdefault('auto_ticket_send_minutes', 60)
    settings.setdefault('auto_ticket_last_check', 0)
    settings.setdefault('auto_ticket_last_send', 0)
    for kind in ('new', 'started', 'error', 'ticket', 'bonus', 'refund', 'lot'):
        settings.setdefault('notify_' + kind, True)
    settings.setdefault('bonus_on', False)
    settings.setdefault('bonus_sid', '')
    settings.setdefault('bonus_qty', 10)
    settings.setdefault('tpl_ask', '')
    settings.setdefault('tpl_done', '')
    settings.setdefault('tpl_err', '')
    return data
def _save(data: dict) -> None:
    with _LOCK:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        tmp = CFG + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CFG)
def _api(data: dict, action: str, **params) -> dict:
    _audit('api_request', action=action, service=params.get('service'), order=params.get('order'))
    result = _api_request(data, action, **params)
    _audit('api_result', action=action, success=bool(result.get('ok')))
    return result
def _api_request(data: dict, action: str, **params) -> dict:
    key = str(data.get('key') or '').strip()
    url = SITE.rstrip('/')
    if not key.startswith('bz_'):
        return {'ok': False, 'error': 'Сначала подключите API-ключ BlazySMM в настройках аккаунта'}
    body = {'key': key, 'action': action}
    for k, v in params.items():
        if v is None or v == '':
            continue
        body[k] = v
    endpoint = f'{url}/api/v2'
    safe_retry = action not in ('add',)
    attempts = 3 if safe_retry else 1
    last_error = ''
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(endpoint, json=body, headers={'Accept': 'application/json'}, timeout=(15, 45))
        except requests.RequestException as e:
            last_error = f'Ошибка соединения с BlazySMM: {type(e).__name__}: {e}'
            _audit('api_transport_error', action=action, attempt=attempt, attempts=attempts, error=type(e).__name__)
            if attempt < attempts:
                time.sleep(attempt * 2)
                continue
            return {'ok': False, 'error': last_error}
        content_type = str(r.headers.get('Content-Type') or '')
        raw_text = str(r.text or '')
        if not raw_text.strip():
            last_error = f'BlazySMM вернул пустой ответ (HTTP {r.status_code})'
            _audit('api_invalid_response', action=action, attempt=attempt, attempts=attempts, status=r.status_code, content_type=content_type, body='empty')
            if safe_retry and attempt < attempts:
                time.sleep(attempt * 2)
                continue
            return {'ok': False, 'error': last_error}
        try:
            payload = r.json()
        except ValueError:
            snippet = re.sub(r'\s+', ' ', raw_text).strip()[:300]
            snippet = re.sub(r'bz_[A-Za-z0-9_-]+', '[API KEY]', snippet)
            last_error = f'BlazySMM вернул не JSON (HTTP {r.status_code})'
            _audit('api_invalid_response', action=action, attempt=attempt, attempts=attempts, status=r.status_code, content_type=content_type, body=snippet)
            if safe_retry and attempt < attempts:
                time.sleep(attempt * 2)
                continue
            return {'ok': False, 'error': last_error}
        if r.status_code >= 500 or r.status_code == 429:
            message = str(payload.get('error') or f'HTTP {r.status_code}') if isinstance(payload, dict) else f'HTTP {r.status_code}'
            last_error = message
            _audit('api_http_retry', action=action, attempt=attempt, attempts=attempts, status=r.status_code)
            if safe_retry and attempt < attempts:
                time.sleep(attempt * 2)
                continue
            return {'ok': False, 'error': message, 'raw': payload}
        if isinstance(payload, list):
            if r.status_code >= 400:
                return {'ok': False, 'error': f'HTTP {r.status_code}', 'raw': payload}
            return {'ok': True, 'raw': payload}
        if not isinstance(payload, dict):
            return {'ok': False, 'error': f'BlazySMM вернул неизвестный JSON-формат (HTTP {r.status_code})'}
        if payload.get('error'):
            return {'ok': False, 'error': str(payload.get('error')), 'raw': payload}
        if r.status_code >= 400:
            return {'ok': False, 'error': f'HTTP {r.status_code}', 'raw': payload}
        return {'ok': True, 'raw': payload}
    return {'ok': False, 'error': last_error or 'BlazySMM временно недоступен'}
def _auto_ticket_extract_phpsessid(account) -> str:
    session = requests.Session()
    if getattr(account, 'user_agent', None):
        session.headers['User-Agent'] = account.user_agent
    timeout = int(getattr(account, 'requests_timeout', 20) or 20)
    golden_key = str(getattr(account, 'golden_key', '') or '')
    if not golden_key:
        raise RuntimeError('golden_key отсутствует')
    response = session.get('https://funpay.com/support/sso?return_to=%2Ftickets%2Fnew', headers={'cookie': f'golden_key={golden_key}; cookie_prefs=1'}, allow_redirects=False, timeout=timeout)
    if response.status_code not in (301, 302, 303, 307, 308):
        raise RuntimeError(f'SSO FunPay вернул HTTP {response.status_code}')
    location = str(response.headers.get('Location') or '')
    if not location:
        raise RuntimeError('SSO не вернул адрес авторизации')
    if not location.startswith('http'):
        location = urljoin('https://funpay.com', location)
    response = session.get(location, allow_redirects=False, timeout=timeout)
    for source in (response.cookies, session.cookies):
        for cookie in source:
            if cookie.name == 'PHPSESSID' and cookie.value:
                return str(cookie.value)
    raise RuntimeError('PHPSESSID поддержки не получен')

class _AutoTicketSupportAPI:
    def __init__(self, account):
        self.account = account
        self.session = requests.Session()
        if getattr(account, 'user_agent', None):
            self.session.headers['User-Agent'] = account.user_agent
        self.phpsessid = ''
        self.csrf_token = ''
    @property
    def timeout(self):
        return int(getattr(self.account, 'requests_timeout', 20) or 20)
    def _request(self, method, url, *, headers=None, data=None):
        merged = {'cookie': f'PHPSESSID={self.phpsessid}'}
        merged.update(headers or {})
        response = self.session.request(method, url, headers=merged, data=data or {}, allow_redirects=False, timeout=self.timeout)
        if response.is_redirect or response.status_code in (401, 403):
            raise RuntimeError('сессия поддержки FunPay недействительна')
        if response.status_code >= 500:
            raise RuntimeError(f'support.funpay.com вернул HTTP {response.status_code}')
        return response
    def initialize(self):
        self.phpsessid = _auto_ticket_extract_phpsessid(self.account)
        response = self._request('GET', AUTO_TICKET_SUPPORT + '/')
        soup = BeautifulSoup(response.text, 'html.parser')
        body = soup.find('body')
        raw = body.get('data-app-config') if body else None
        if not raw:
            raise RuntimeError('не найден data-app-config поддержки FunPay')
        try:
            app_data = json.loads(raw)
        except Exception as exc:
            raise RuntimeError('не удалось разобрать конфигурацию поддержки FunPay') from exc
        self.csrf_token = str(app_data.get('csrfToken') or '')
        if not self.csrf_token:
            raise RuntimeError('csrfToken поддержки FunPay отсутствует')
        return self
    def _ticket_token(self):
        response = self._request('GET', AUTO_TICKET_SUPPORT + '/tickets/new/1', headers={'X-CSRF-Token': self.csrf_token, 'Referer': AUTO_TICKET_SUPPORT + '/'})
        soup = BeautifulSoup(response.text, 'html.parser')
        token = soup.find('input', attrs={'name': 'ticket[_token]'})
        if token is None or not token.get('value'):
            raise RuntimeError('ticket[_token] не найден')
        return str(token['value'])
    def create_ticket(self, order_ids, comment):
        ids = [str(x).lstrip('#') for x in order_ids if str(x).strip()]
        if not ids:
            raise RuntimeError('список заказов для тикета пуст')
        token = self._ticket_token()
        body_html = escape(str(comment), quote=False).replace('\n', '<br>')
        payload = {'ticket[fields][1]': str(getattr(self.account, 'username', '') or ''), 'ticket[fields][2]': ', '.join(ids), 'ticket[fields][3]': '2', 'ticket[fields][5]': '201', 'ticket[comment][body_html]': f'<p>{body_html}</p>', 'ticket[comment][attachments]': '', 'ticket[_token]': token}
        response = self._request('POST', AUTO_TICKET_SUPPORT + '/tickets/create/1', headers={'Origin': AUTO_TICKET_SUPPORT, 'Referer': AUTO_TICKET_SUPPORT + '/tickets/new/1', 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest'}, data=payload)
        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError(f'поддержка вернула не JSON, HTTP {response.status_code}') from exc
    def close(self):
        self.session.close()

def _auto_ticket_success(response: dict) -> tuple[bool, str]:
    if not isinstance(response, dict):
        return (False, 'неизвестный ответ поддержки')
    if response.get('error'):
        return (False, str(response.get('error')))
    action = response.get('action')
    if isinstance(action, dict):
        message = str(action.get('message') or '')
        url = str(action.get('url') or '')
        if 'заявка отправлена' in message.lower() or '/tickets/' in url:
            return (True, url or message or 'тикет создан')
    return (False, 'неожиданный ответ support.funpay.com')

def _auto_ticket_status_complete(values, expected: int) -> bool:
    cleaned = [str(x or '').strip().casefold() for x in values or []]
    return expected > 0 and len(cleaned) == expected and all(x == 'completed' for x in cleaned)

def _auto_ticket_ready_rows(data: dict):
    rows = []
    for oid, row in (data.get('orders') or {}).items():
        if not isinstance(row, dict) or row.get('refunded') or row.get('auto_ticket_sent_at'):
            continue
        ids = [x.strip() for x in str(row.get('site_order') or '').split(',') if x.strip()]
        if not ids:
            continue
        if _auto_ticket_status_complete(row.get('auto_ticket_statuses'), len(ids)):
            rows.append((str(oid), row))
    return sorted(rows, key=lambda item: float((item[1] or {}).get('ts') or 0))

def _auto_ticket_refresh(cardinal, data: dict, force: bool=False) -> dict:
    cfg = data.setdefault('settings', {})
    if not cfg.get('auto_ticket_enabled') and not force:
        return {'checked': 0, 'completed': 0, 'errors': 0, 'changed': False}
    now = time.time()
    cooldown = max(1, _int(cfg.get('auto_ticket_check_minutes'), 10)) * 60
    if not force and now - float(cfg.get('auto_ticket_last_check') or 0) < cooldown:
        return {'checked': 0, 'completed': len(_auto_ticket_ready_rows(data)), 'errors': 0, 'changed': False}
    checked = completed = errors = 0
    for oid, row in sorted((data.get('orders') or {}).items(), key=lambda item: float((item[1] or {}).get('auto_ticket_checked_at') or 0)):
        if not isinstance(row, dict) or row.get('refunded') or row.get('auto_ticket_sent_at'):
            continue
        ids = [x.strip() for x in str(row.get('site_order') or '').split(',') if x.strip()]
        if not ids:
            continue
        statuses = []
        failed = False
        for site_id in ids:
            got = _api(data, 'status', order=site_id)
            if not got.get('ok'):
                errors += 1
                failed = True
                _audit('auto_ticket_status_failed', order=oid, site_order=site_id, error=str(got.get('error') or '')[:180])
                break
            statuses.append(str((got.get('raw') or {}).get('status') or 'Unknown'))
        if failed:
            row['auto_ticket_checked_at'] = now
            continue
        row['auto_ticket_statuses'] = statuses
        row['auto_ticket_checked_at'] = now
        checked += 1
        if _auto_ticket_status_complete(statuses, len(ids)):
            completed += 1
    cfg['auto_ticket_last_check'] = now
    _audit('auto_ticket_check', checked=checked, completed=completed, errors=errors)
    return {'checked': checked, 'completed': completed, 'errors': errors, 'changed': True}

def _auto_ticket_send(cardinal, data: dict, force: bool=False) -> dict:
    cfg = data.setdefault('settings', {})
    if not cfg.get('auto_ticket_enabled') and not force:
        return {'sent': 0, 'tickets': 0, 'errors': ['авто-тикеты выключены'], 'changed': False}
    now = time.time()
    cooldown = max(1, _int(cfg.get('auto_ticket_send_minutes'), 60)) * 60
    if not force and now - float(cfg.get('auto_ticket_last_send') or 0) < cooldown:
        return {'sent': 0, 'tickets': 0, 'errors': [], 'changed': False}
    rows = _auto_ticket_ready_rows(data)
    cfg['auto_ticket_last_send'] = now
    if not rows:
        _audit('auto_ticket_send_skipped', reason='no_completed_orders')
        return {'sent': 0, 'tickets': 0, 'errors': [], 'changed': True}
    api = _AutoTicketSupportAPI(cardinal.account)
    sent = tickets = 0
    errors = []
    try:
        api.initialize()
        for offset in range(0, len(rows), AUTO_TICKET_BATCH):
            batch = rows[offset:offset + AUTO_TICKET_BATCH]
            ids = [oid for oid, _ in batch]
            username = str(getattr(cardinal.account, 'username', '') or '')
            listed = ', '.join('#' + oid for oid in ids)
            comment = f'Здравствуйте!\n\nПрошу подтвердить выполнение следующих заказов. Связанные SMM-заказы выполнены сервисом:\n{listed}\n\nЗаранее благодарю.\nС уважением, {username}.'
            try:
                response = api.create_ticket(ids, comment)
                ok, detail = _auto_ticket_success(response)
            except Exception as exc:
                ok, detail = (False, str(exc)[:300])
            if not ok:
                errors.append(detail)
                _audit('auto_ticket_failed', orders=ids, error=detail)
                break
            sent_at = int(time.time())
            for oid, row in batch:
                row['auto_ticket_sent_at'] = sent_at
                row['auto_ticket_result'] = detail
            sent += len(batch)
            tickets += 1
            _audit('auto_ticket_sent', orders=ids, ticket=detail)
            if offset + AUTO_TICKET_BATCH < len(rows):
                time.sleep(2)
    except Exception as exc:
        errors.append(str(exc)[:300])
        _audit('auto_ticket_auth_failed', error=type(exc).__name__, detail=str(exc)[:180])
    finally:
        api.close()
    return {'sent': sent, 'tickets': tickets, 'errors': errors, 'changed': True}

def _auto_ticket_tick(cardinal, data: dict) -> tuple[list[str], bool]:
    cfg = data.setdefault('settings', {})
    if not cfg.get('plugin_enabled', True) or not cfg.get('auto_ticket_enabled'):
        return ([], False)
    notes = []
    changed = False
    checked = _auto_ticket_refresh(cardinal, data)
    changed = changed or checked.get('changed', False)
    sent = _auto_ticket_send(cardinal, data)
    changed = changed or sent.get('changed', False)
    if sent.get('sent'):
        _notify_admins(cardinal, data, f"🎫 Авто-тикеты: отправлено заказов {sent['sent']}, тикетов {sent['tickets']}.", 'ticket')
    elif sent.get('errors'):
        _notify_admins(cardinal, data, '🎫 Авто-тикеты: ошибка отправки: ' + '; '.join(sent['errors'][:3]), 'ticket')
    return (notes, changed)

def _hash_lines(text: str) -> list[str]:
    out = []
    for line in str(text or '').splitlines():
        s = line.strip()
        if s.startswith('#'):
            body = s[1:].strip()
            if body:
                out.append(body)
    return out
def _service_id(*chunks: str) -> str:
    blob = '\n'.join((str(x or '') for x in chunks))
    m = re.search('(?:blazy|услуга|service)\\s*[:#]?\\s*(\\d{2,})', blob, re.I)
    return m.group(1) if m else ''
def _link(text: str) -> str:
    m = re.search('https?://\\S+', str(text or ''), re.I)
    return m.group(0).rstrip(').,]') if m else ''
def _kb(rows: list[list[tuple[str, str]]]):
    from telebot.types import InlineKeyboardButton as B
    from telebot.types import InlineKeyboardMarkup as K
    kb = K()
    for row in rows:
        buttons = []
        for text, data in row:
            if str(data).startswith('url:'):
                buttons.append(B(text, url=str(data)[4:]))
            else:
                buttons.append(B(text, callback_data=data))
        if buttons:
            kb.row(*buttons)
    return kb
def _home_text(data: dict) -> str:
    return f'🧩 <b>Плагин:</b> {escape(NAME)}\n📦 <b>Версия:</b> <code>{escape(VERSION)}</code>\n👤 <b>Автор:</b> <a href="{CREATOR_URL}">{escape(CREDITS)}</a>\n\nВыберите раздел ниже.'
def _home_kb():
    rows = [[('⚙️ Настройки', PREFIX + 'settings'), ('ℹ️ Информация', PREFIX + 'info')], [('⬆️ Обновить плагин', PREFIX + 'update'), ('🗑 Удалить', PREFIX + 'delete')]]
    if CBT:
        rows.append([('🔙 К списку плагинов', CB_PLUGINS_LIST_OPEN)])
    return _kb(rows)
def _settings_text(data: dict) -> str:
    lots = len(_lots(data))
    orders = len(data.get('orders') or {})
    active_sessions = len(data.get('sessions') or {})
    return f'⚙️ <b>Панель настроек {escape(NAME)}</b>\n\n• Привязано лотов: <b>{lots}</b>\n• Заказов в истории: <b>{orders}</b>\n• Активных ожиданий: <b>{active_sessions}</b>\n\nВыберите категорию:'
def _settings_kb():
    return _kb([[('👤 Аккаунт', PREFIX + 'account_settings')], [('⚙️ Плагин', PREFIX + 'plugin_settings')], [('🛒 Лоты', PREFIX + 'lot_settings')], [('📊 Статистика', PREFIX + 'stats')], [('◀️ Главное меню', PREFIX + 'home')]])
def _plugin_settings_text(data):
    cfg = data.get('settings', {})
    badge = lambda name: '✅ вкл' if cfg.get(name) else '➖ выкл'
    state = '🟢 Работает' if cfg.get('plugin_enabled', True) else '🔴 Остановлен'
    return f"⚙️ <b>Настройки плагина</b>\n{state}\n\n💸 Автовозврат: <b>{badge('auto_refund')}</b>\n⏸ Автодеактивация: <b>{badge('auto_deactivate')}</b>\n🎫 Авто-тикеты: <b>{badge('auto_ticket_enabled')}</b>\n🎁 Бонус за отзыв: <b>{badge('bonus_on')}</b> · от {cfg.get('bonus_stars', 5)}★\n🛡 Чёрный список: <b>{len(data.get('blacklist', []))}</b>"
def _plugin_settings_kb():
    return _kb([[('🟢 Состояние', PREFIX + 'state')], [('📦 Заказы', PREFIX + 'order_settings')], [('🔔 Уведомления', PREFIX + 'notifications')], [('🎫 Авто-тикеты', PREFIX + 'auto_tickets')], [('🎁 Бонус за отзыв', PREFIX + 'bonus')], [('🛡 Чёрный список', PREFIX + 'ban')], [('🧰 Обслуживание', PREFIX + 'maintenance')], [('◀️ К настройкам', PREFIX + 'settings')]])
def _auto_ticket_text(data: dict) -> str:
    cfg = data.setdefault('settings', {})
    enabled = bool(cfg.get('auto_ticket_enabled'))
    ready = len(_auto_ticket_ready_rows(data))
    last_check = _fmt_when(cfg.get('auto_ticket_last_check') or 0) if cfg.get('auto_ticket_last_check') else 'ещё не было'
    last_send = _fmt_when(cfg.get('auto_ticket_last_send') or 0) if cfg.get('auto_ticket_last_send') else 'ещё не было'
    return f"🎫 <b>Авто-тикеты</b>\n\nСостояние: <b>{('✅ ВКЛ' if enabled else '➖ ВЫКЛ')}</b>\n🔎 КД проверки SMM: <b>{cfg.get('auto_ticket_check_minutes', 10)} мин.</b>\n📨 КД отправки тикетов: <b>{cfg.get('auto_ticket_send_minutes', 60)} мин.</b>\n✅ Сейчас готовы к тикету: <b>{ready}</b>\n🕒 Последняя проверка: <b>{last_check}</b>\n🕒 Последняя отправка: <b>{last_send}</b>\n\nВ тикет попадают только заказы FunPay, созданные этим плагином, у которых <b>все</b> связанные SMM-заказы получили статус <code>Completed</code>. Повторно один и тот же FunPay-заказ не отправляется."
def _auto_ticket_kb(data: dict):
    cfg = data.setdefault('settings', {})
    return _kb([[('🔴 Выключить' if cfg.get('auto_ticket_enabled') else '🟢 Включить', PREFIX + 'auto_ticket_toggle')], [(f"🔎 Проверка: {cfg.get('auto_ticket_check_minutes', 10)} мин.", PREFIX + 'auto_ticket_check_cd')], [(f"📨 Отправка: {cfg.get('auto_ticket_send_minutes', 60)} мин.", PREFIX + 'auto_ticket_send_cd')], [('🔄 Проверить сейчас', PREFIX + 'auto_ticket_check_now')], [('📨 Отправить сейчас', PREFIX + 'auto_ticket_send_now')], [('◀️ Назад', PREFIX + 'plugin_settings')]])
def _order_settings_text(data: dict) -> str:
    cfg = data['settings']
    confirm_state = '✅ ВКЛ' if cfg.get('link_confirm_enabled', True) else '➖ ВЫКЛ'
    status_state = '✅ ВКЛ' if cfg.get('status_command_enabled', True) else '➖ ВЫКЛ'
    refund_state = '✅ ВКЛ' if cfg.get('auto_refund') else '➖ ВЫКЛ'
    deactivate_state = '✅ ВКЛ' if cfg.get('auto_deactivate') else '➖ ВЫКЛ'
    return f'📦 <b>Заказы</b>\n\n<b>🔎 Подтверждение ссылки</b>: {confirm_state}\nПосле ссылки покупатель подтверждает, что она указана верно.\n\n<b>📌 Команда «статус»</b>: {status_state}\nПокупатель может написать «статус НОМЕР_ЗАКАЗА» и получить состояние SMM-заказа.\n\n<b>💸 Автовозврат</b>: {refund_state}\nВозвращает оплату, если не хватило баланса или покупатель отменил заказ до отправки в SMM.\n\n<b>⏸ Автодеактивация лота</b>: {deactivate_state}\nЕсли на балансе не хватает средств, соответствующий лот FunPay снимается с продажи. После пополнения его можно включить обратно кнопкой.'
def _order_settings_kb(data=None):
    cfg = (data if data is not None else _load()).get('settings', {})
    return _kb([[(('✅' if cfg.get('link_confirm_enabled', True) else '➖') + ' Подтверждение ссылки', PREFIX + 'toggle:lc')], [(('✅' if cfg.get('status_command_enabled', True) else '➖') + ' Команда «статус»', PREFIX + 'toggle:sc')], [(('✅' if cfg.get('auto_refund') else '➖') + ' Автовозврат', PREFIX + 'toggle:auto_refund')], [(('✅' if cfg.get('auto_deactivate') else '➖') + ' Автодеактивация', PREFIX + 'toggle:auto_deactivate')], [('📋 История заказов', PREFIX + 'orders:order')], [('💬 Сообщения покупателю', PREFIX + 'tpls')], [('◀️ К настройкам плагина', PREFIX + 'plugin_settings')]])
def _maintenance_text(data: dict) -> str:
    return f"🧰 <b>Обслуживание</b>\n\nОшибок в журнале: <b>{len(data.get('errors') or [])}</b>\nАктивных ожиданий: <b>{len(data.get('sessions') or {})}</b>\nЗаписей заказов: <b>{len(data.get('orders') or {})}</b>\n\nЗдесь собраны диагностические данные плагина."
def _maintenance_kb():
    return _kb([[('📄 Скачать логи', PREFIX + 'logs')], [('⚙️ Конфиг', PREFIX + 'config')], [('◀️ К настройкам плагина', PREFIX + 'plugin_settings')]])
def _lot_settings_text(data, page=0):
    rows = _cached_funpay_lots(data)
    pages = max(1, (len(rows) + 7) // 8)
    page = max(0, min(int(page), pages - 1))
    mapped = sum((bool(row.get('sid')) for row in _lots(data).values()))
    return f'🛒 <b>Настройка лотов</b>\n\n📦 В списке: <b>{len(rows)}</b> · 🔗 С услугой SMM: <b>{mapped}</b>\n\n✨ <b>Автодобавление</b> — введите ID категорий FunPay, затем выберите найденный лот.\n➕ <b>Добавить LOT ID</b> — настройте отдельный лот вручную.\n\n📄 Страница {page + 1} / {pages}'
def _lot_settings_kb(data: dict, page: int=0):
    rows_data = _cached_funpay_lots(data)
    mappings = _lots(data)
    pages = max(1, (len(rows_data) + 7) // 8)
    page = max(0, min(int(page), pages - 1))
    rows = [[('✨ Автодобавление лотов', PREFIX + 'ac')], [('➕ Добавить LOT ID', PREFIX + 'lot_manual')]]
    quiet = {str(x) for x in data.get('off_ids') or []}
    for lot in rows_data[page * 8:(page + 1) * 8]:
        lot_id = str(lot.get('lot_id') or '')
        if not lot_id:
            continue
        state = '🔕' if lot_id in quiet else '🟢' if lot.get('active', True) else '🔴'
        mapping = mappings.get(lot_id) or {}
        mark = '✅' if mapping.get('sid') else '➕'
        title = str(lot.get('title') or f'LOT {lot_id}')[:32]
        rows.append([(f'{state} {mark} {title} · {lot_id}', PREFIX + 'lot_pick:' + lot_id)])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(('⬅️', PREFIX + f'lot_settings:{page - 1}'))
        nav.append((f'{page + 1}/{pages}', PREFIX + 'noop'))
        if page < pages - 1:
            nav.append(('➡️', PREFIX + f'lot_settings:{page + 1}'))
        rows.append(nav)
    rows.append([('◀️ Назад', PREFIX + 'settings')])
    return _kb(rows)
def _service_category_names(data: dict) -> list[str]:
    names = []
    seen = set()
    for row in _services(data):
        category = re.sub('\\s+', ' ', str((row or {}).get('category') or '')).strip()
        if category and category.casefold() not in seen:
            seen.add(category.casefold())
            names.append(category)
    return sorted(names, key=str.casefold)
def _match_service_categories(data: dict, raw: str) -> tuple[list[str], list[dict]]:
    tokens = [re.sub('\\s+', ' ', x).strip().casefold() for x in re.split('[,;\\n]+', str(raw or '')) if x.strip()]
    categories = _service_category_names(data)
    picked = []
    for category in categories:
        folded = category.casefold()
        if any((token == folded or token in folded for token in tokens)):
            picked.append(category)
    chosen = {x.casefold() for x in picked}
    rows = [row for row in _services(data) if str((row or {}).get('category') or '').casefold() in chosen]
    return (picked, rows)
def _auto_services_text(flow: dict, page: int=0) -> str:
    rows = list(flow.get('services') or [])
    pages = max(1, (len(rows) + 7) // 8)
    page = max(0, min(int(page), pages - 1))
    cats = ', '.join(flow.get('categories') or []) or '—'
    return f'✨ <b>Автодобавление · услуги</b>\n\nКатегории: <b>{escape(cats)}</b>\nПодходящих услуг: <b>{len(rows)}</b>\n\nВыберите услугу. После этого плагин покажет ваши FunPay-лоты для автоматической привязки.\nСтраница {page + 1}/{pages}.'
def _auto_services_kb(flow: dict, page: int=0):
    rows_data = list(flow.get('services') or [])
    pages = max(1, (len(rows_data) + 7) // 8)
    page = max(0, min(int(page), pages - 1))
    rows = []
    for item in rows_data[page * 8:(page + 1) * 8]:
        sid = str((item or {}).get('service') or '')
        name = re.sub('\\s+', ' ', str((item or {}).get('name') or f'Услуга {sid}')).strip()[:34]
        rate = str((item or {}).get('rate') or '')
        rows.append([(f'{name} · {sid}' + (f' · ${rate}' if rate else ''), PREFIX + 'av:' + sid)])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(('⬅️', PREFIX + f'as:{page - 1}'))
        nav.append((f'{page + 1}/{pages}', PREFIX + 'noop'))
        if page < pages - 1:
            nav.append(('➡️', PREFIX + f'as:{page + 1}'))
        rows.append(nav)
    rows.append([('✏️ Другие категории', PREFIX + 'ac')])
    rows.append([('◀️ К лотам', PREFIX + 'lot_settings')])
    return _kb(rows)
def _auto_lots_text(flow: dict, data: dict, page: int=0) -> str:
    lots = _cached_funpay_lots(data)
    pages = max(1, (len(lots) + 7) // 8)
    page = max(0, min(int(page), pages - 1))
    service = flow.get('service') or {}
    return f"✨ <b>Автодобавление · FunPay-лот</b>\n\nУслуга: <b>{escape(str(service.get('name') or '—'))}</b>\nID: <code>{escape(str(service.get('service') or '—'))}</code> · Категория: <b>{escape(str(service.get('category') or '—'))}</b>\nMin/Max: <b>{escape(str(service.get('min') or '—'))} / {escape(str(service.get('max') or '—'))}</b>\n\nТеперь выберите FunPay-лот. Плагин сам сохранит привязку к этой услуге.\nСтраница {page + 1}/{pages}."
def _auto_lots_kb(flow: dict, data: dict, page: int=0):
    lots = _cached_funpay_lots(data)
    pages = max(1, (len(lots) + 7) // 8)
    page = max(0, min(int(page), pages - 1))
    rows = []
    mappings = _lots(data)
    for item in lots[page * 8:(page + 1) * 8]:
        lot_id = str((item or {}).get('lot_id') or '')
        title = re.sub('\\s+', ' ', str((item or {}).get('title') or f'LOT {lot_id}')).strip()[:34]
        mark = '🔁' if (mappings.get(lot_id) or {}).get('sid') else '➕'
        rows.append([(f'{mark} {title} · {lot_id}', PREFIX + 'ab:' + lot_id)])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(('⬅️', PREFIX + f'al:{page - 1}'))
        nav.append((f'{page + 1}/{pages}', PREFIX + 'noop'))
        if page < pages - 1:
            nav.append(('➡️', PREFIX + f'al:{page + 1}'))
        rows.append(nav)
    rows.append([('◀️ К услугам', PREFIX + 'as:0')])
    rows.append([('◀️ К лотам', PREFIX + 'lot_settings')])
    return _kb(rows)
def _lot_field_title(fields, lot_id: str) -> str:
    for key in ('title_ru', 'title_en', 'title', 'name', 'short_description'):
        value = getattr(fields, key, None)
        if value:
            return re.sub('\\s+', ' ', str(value)).strip()[:120]
    return f'LOT {lot_id}'
def _cached_funpay_lots(data: dict) -> list[dict]:
    found = {}
    for item in data.get('lot_cache') or []:
        if not isinstance(item, dict):
            continue
        lot_id = str(item.get('lot_id') or '')
        if lot_id.isdigit():
            found[lot_id] = {'lot_id': lot_id, 'title': str(item.get('title') or f'LOT {lot_id}')[:120], 'active': bool(item.get('active', True)), 'category_id': item.get('category_id')}
    for item in data.get('seen') or []:
        if not isinstance(item, dict):
            continue
        lot_id = str(item.get('id') or '')
        if lot_id.isdigit() and lot_id not in found:
            found[lot_id] = {'lot_id': lot_id, 'title': str(item.get('title') or f'LOT {lot_id}')[:120], 'active': True}
    for key, row in _lots(data).items():
        if not str(key).isdigit():
            continue
        if str(key) not in found:
            found[str(key)] = {'lot_id': str(key), 'title': str((row or {}).get('title') or f'LOT {key}')[:120], 'active': True}
    return sorted(found.values(), key=lambda x: (str(x.get('title') or '').casefold(), int(x.get('lot_id') or 0)))
def _save_funpay_lot_cache(data: dict, rows: list[dict]) -> None:
    clean = []
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        lot_id = str(item.get('lot_id') or '')
        if not lot_id.isdigit() or lot_id in seen:
            continue
        seen.add(lot_id)
        clean.append({'lot_id': lot_id, 'title': str(item.get('title') or f'LOT {lot_id}')[:120], 'active': bool(item.get('active', True)), 'category_id': item.get('category_id')})
    data['lot_cache'] = clean[-500:]
    _save(data)
def _cache_funpay_lot(data: dict, item: dict) -> None:
    rows = {str(x.get('lot_id')): dict(x) for x in _cached_funpay_lots(data) if str(x.get('lot_id') or '').isdigit()}
    lot_id = str(item.get('lot_id') or '')
    if lot_id.isdigit():
        rows[lot_id] = dict(item)
    _save_funpay_lot_cache(data, list(rows.values()))
def _toggle_funpay_lot_active(data: dict, lot_id: str) -> tuple[bool, str, bool | None]:
    cardinal = _CARD.get('c')
    if cardinal is None or not str(lot_id).isdigit():
        return (False, 'Cardinal ещё не инициализирован или LOT ID некорректен.', None)
    try:
        fields = cardinal.account.get_lot_fields(int(lot_id))
        new_active = not bool(getattr(fields, 'active', True))
        fields.active = new_active
        cardinal.account.save_lot(fields)
        _cache_funpay_lot(data, {'lot_id': str(lot_id), 'title': _lot_field_title(fields, str(lot_id)), 'active': new_active})
        return (True, 'Лот включён и снова продаётся.' if new_active else 'Лот снят с продажи.', new_active)
    except Exception as exc:
        return (False, str(exc)[:300], None)
def _validate_funpay_lot(data: dict, lot_id: str) -> dict:
    cardinal = _CARD.get('c')
    if cardinal is None or not str(lot_id).isdigit():
        raise ValueError('Некорректный LOT ID')
    fields = cardinal.account.get_lot_fields(int(lot_id))
    item = {'lot_id': str(lot_id), 'title': _lot_field_title(fields, str(lot_id)), 'active': bool(getattr(fields, 'active', True))}
    _cache_funpay_lot(data, item)
    return item
def _discover_funpay_lots(data: dict, all_categories: bool=False) -> dict:
    cardinal = _CARD.get('c')
    if cardinal is None:
        raise ValueError('Cardinal ещё не инициализирован')
    account = cardinal.account
    ids = set()
    objects = {}
    def collect(value):
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
            return
        if isinstance(value, (list, tuple, set)):
            for child in value:
                collect(child)
            return
        try:
            lot_id = int(getattr(value, 'id'))
        except Exception:
            lot_id = 0
        if lot_id > 0:
            ids.add(str(lot_id))
            objects[str(lot_id)] = value
    try:
        updater = getattr(cardinal, 'update_lots_and_categories', None)
        if callable(updater):
            updater()
    except Exception:
        pass
    profile = getattr(cardinal, 'tg_profile', None) or getattr(cardinal, 'profile', None)
    if profile and callable(getattr(profile, 'get_sorted_lots', None)):
        for mode in (2, 1, 0, 3):
            try:
                collect(profile.get_sorted_lots(mode) or {})
            except Exception:
                pass
    if all_categories or not ids:
        try:
            for category in account.get_categories() or []:
                collect(getattr(category, 'lots', None) or [])
                for subcategory in getattr(category, 'subcategories', None) or []:
                    collect(getattr(subcategory, 'lots', None) or [])
                    sub_id = getattr(subcategory, 'id', None)
                    if sub_id is not None:
                        try:
                            collect(account.get_my_subcategory_lots(int(sub_id)) or [])
                        except Exception:
                            pass
        except Exception:
            pass
    for item in _cached_funpay_lots(data):
        lot_id = str(item.get('lot_id') or '')
        if lot_id.isdigit():
            ids.add(lot_id)
    for row in data.get('seen') or []:
        lot_id = str((row or {}).get('id') or '')
        if lot_id.isdigit():
            ids.add(lot_id)
    for key in _lots(data):
        if str(key).isdigit():
            ids.add(str(key))
    found = []
    errors = 0
    for lot_id in sorted(ids, key=lambda x: int(x))[:500]:
        try:
            fields = account.get_lot_fields(int(lot_id))
            found.append({'lot_id': lot_id, 'title': _lot_field_title(fields, lot_id), 'active': bool(getattr(fields, 'active', True))})
        except Exception:
            obj = objects.get(lot_id)
            if obj is not None:
                found.append({'lot_id': lot_id, 'title': _lot_field_title(obj, lot_id), 'active': bool(getattr(obj, 'active', True))})
            else:
                errors += 1
    _save_funpay_lot_cache(data, found)
    return {'found': len(found), 'errors': errors, 'scope': 'all' if all_categories else 'auto'}
def _account_settings_text(data: dict) -> str:
    key = str(data.get('key') or '').strip()
    if not key.startswith('bz_'):
        return '👤 <b>Настройка аккаунта BlazySMM</b>\n\nAPI-ключ: <b>❌ не подключён</b>\n\nПодключите API-ключ кабинета BlazySMM. После успешной проверки здесь появится текущий баланс.'
    masked = key[:3] + '…' + key[-4:] if len(key) > 8 else 'bz_***'
    got = _api(data, 'balance')
    if got.get('ok'):
        raw = got.get('raw') or {}
        balance = f"{raw.get('balance')} {raw.get('currency') or 'USD'}"
        status = '✅ подключён'
    else:
        balance = 'недоступен'
        status = '⚠️ ключ сохранён, API не отвечает'
    return f'👤 <b>Настройка аккаунта BlazySMM</b>\n\nAPI: <b>{status}</b>\n🔑 Ключ: <code>{escape(masked)}</code>\n💰 Баланс: <b>{escape(str(balance))}</b>'
def _account_settings_kb(data: dict):
    key = str(data.get('key') or '').strip()
    rows = [[('🔄 Изменить API-ключ' if key.startswith('bz_') else '🔑 Подключить API-ключ', PREFIX + 'key')]]
    if key.startswith('bz_'):
        rows.append([('🗑 Удалить API-ключ', PREFIX + 'key_delete')])
    rows.append([('◀️ Назад', PREFIX + 'settings')])
    return _kb(rows)
def _menu_kb():
    return _settings_kb()
def _text_menu(data: dict) -> str:
    return _home_text(data)
def _info_text() -> str:
    return 'ℹ️ <b>Информация</b>\n\nСверху — инструкции. Слева расположены ссылки разработчика плагина, справа — ссылки BlazySMM.'
def _info_kb():
    return _kb([[('📖 Инструкция', 'url:' + INSTRUCTION_URL), ('📚 GitHub-инструкция', 'url:' + ALT_INSTRUCTION_URL)], [('👤 Мой Telegram', 'url:' + CREATOR_URL), ('🌐 Сайт сервиса', 'url:' + REFERRAL_URL)], [('💬 Чат разработчика', 'url:' + GROUP_URL), ('📢 Канал сервиса', 'url:' + SERVICE_CHANNEL_URL)], [('📢 Канал разработчика', 'url:' + CHANNEL_URL), ('👤 Автор сервиса', 'url:' + SERVICE_PERSON_URL)], [('◀️ Главное меню', PREFIX + 'home')]])
def _state_text(data: dict) -> str:
    enabled = bool(data.setdefault('settings', {}).get('plugin_enabled', True))
    return f"🧩 <b>Состояние плагина</b>\n\nСейчас плагин <b>{('🟢 включён' if enabled else '🔴 выключен')}</b>."
def _state_kb(data: dict):
    enabled = bool(data.setdefault('settings', {}).get('plugin_enabled', True))
    return _kb([[('🔴 Выключить плагин' if enabled else '🟢 Включить плагин', PREFIX + 'state_toggle')], [('◀️ Назад', PREFIX + 'plugin_settings')]])
def _update_text():
    return f'⬆️ <b>Обновление BlazySMM</b>\n\n📦 Версия: <b>{VERSION}</b>\n🐙 Источник: <b>tinechelovec/FPC-Blazy-SMM</b>\n\n🌐 Онлайн — найти Python-файл плагина в репозитории.\n📥 Из файла — отправить новую версию .py.\n\nПеред установкой создаётся резервная копия. Настройки сохраняются.'
def _update_kb():
    return _kb([[('🌐 Проверить GitHub', PREFIX + 'update_online'), ('📥 Из файла', PREFIX + 'update_local')], [('🐙 Скачать с GitHub', 'url:' + PLUGIN_REPO_URL)], [('◀️ Главное меню', PREFIX + 'home')]])
def _delete_text() -> str:
    return f'🗑 <b>Удаление плагина</b>\n\nВы действительно хотите удалить <b>{escape(NAME)}</b>?\n\nБудет удалён только текущий <code>.py</code>-файл плагина. Настройки и история в <code>storage/blazysmm</code> останутся на диске.'
def _delete_kb():
    return _kb([[('🗑 Да, удалить', PREFIX + 'delete_yes'), ('❌ Отмена', PREFIX + 'delete_no')]])
def _safe_edit(bot, chat_id, message_id, text: str, reply_markup=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)
        return True
    except Exception:
        try:
            bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)
            return True
        except Exception:
            return False
def _version_key(value: str):
    nums = [int(x) for x in re.findall('\\d+', str(value or ''))[:4]]
    nums.extend([0] * (4 - len(nums)))
    return tuple(nums[:4])
def _validate_plugin_bytes(raw: bytes) -> tuple[bool, str, str]:
    try:
        text = raw.decode('utf-8-sig')
    except Exception:
        return (False, 'Файл должен быть UTF-8 Python-файлом.', '')
    try:
        compile(text, 'BlazySMM-update.py', 'exec')
    except Exception as e:
        return (False, f'Синтаксическая ошибка: {e}', '')
    required = ('BIND_TO_PRE_INIT', 'BIND_TO_NEW_ORDER', 'BIND_TO_NEW_MESSAGE')
    if any((token not in text for token in required)):
        return (False, 'Это не похоже на плагин BlazySMM/Cardinal.', '')
    uuid_match = re.search('^UUID\\s*=\\s*[\\"\\\']([^\\"\\\']+)[\\"\\\']', text, re.M)
    if not uuid_match or uuid_match.group(1) != UUID:
        return (False, 'UUID файла обновления не совпадает с этим плагином.', '')
    m = re.search('^VERSION\\s*=\\s*[\\"\\\']([^\\"\\\']+)[\\"\\\']', text, re.M)
    return (True, '', m.group(1) if m else 'не определена')
def _install_plugin_bytes(raw: bytes) -> tuple[bool, str]:
    ok, err, remote_version = _validate_plugin_bytes(raw)
    if not ok:
        return (False, err)
    try:
        current = os.path.abspath(__file__)
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup = os.path.join(BACKUP_DIR, f'BlazySMM-{VERSION}-{stamp}.py')
        shutil.copy2(current, backup)
        tmp = current + '.update.tmp'
        with open(tmp, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, current)
        return (True, f'Установлена версия {remote_version}. Резервная копия: {backup}')
    except Exception as e:
        return (False, str(e)[:300])
def _download_online_update():
    try:
        if PLUGIN_UPDATE_URL:
            urls = [PLUGIN_UPDATE_URL]
        else:
            response = requests.get('https://api.github.com/repos/' + PLUGIN_REPO + '/contents/', timeout=20)
            if response.status_code == 404:
                return {'ok': False, 'error': 'Репозиторий GitHub не найден или закрыт. Проверьте доступность FPC-Blazy-SMM. Можно установить обновление файлом.'}
            response.raise_for_status()
            entries = response.json()
            if not isinstance(entries, list):
                return {'ok': False, 'error': 'GitHub вернул неизвестный список файлов.'}
            urls = [row.get('download_url') for row in entries if row.get('type') == 'file' and str(row.get('name', '')).endswith('.py') and row.get('download_url')]
        candidates = []
        for url in urls[:20]:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            raw = response.content
            ok, error, version = _validate_plugin_bytes(raw)
            if ok:
                candidates.append((version, raw))
        if not candidates:
            return {'ok': False, 'error': 'Не найден подходящий .py с UUID BlazySMM в корне репозитория. Доступна установка файлом.'}
        version, raw = max(candidates, key=lambda item: _version_key(item[0]))
        return {'ok': True, 'raw': raw, 'remote_version': version, 'changed': _version_key(version) > _version_key(VERSION)}
    except Exception as exc:
        return {'ok': False, 'error': 'Не удалось получить обновление: ' + str(exc)[:180]}
def _fill(tpl: str, **kw) -> str:
    out = tpl
    for k, v in kw.items():
        out = out.replace('{' + k + '}', str(v if v is not None else ''))
    return re.sub('\\n{3,}', '\n\n', out).strip()
def _en(lang: str) -> bool:
    return False
def _lang_of(text: str) -> str:
    return 'ru'
def _log_err(data: dict, text: str) -> None:
    text = re.sub('bz_[A-Za-z0-9_-]+', '[API KEY]', str(text))
    _audit('error', message=text)
    _notify_admins(_CARD.get('c'), data, text, 'error')
    rows = list(data.get('errors') or [])
    rows.append({'t': int(time.time()), 'text': text[:240]})
    data['errors'] = rows[-30:]
    _save(data)
def _buyer(order) -> str:
    return str(getattr(order, 'buyer_username', None) or getattr(order, 'buyer', None) or '').strip()
def _blocked(data: dict, order, desc: str, lot_id: str='') -> str:
    name = _buyer(order).lower()
    for item in data.get('blacklist') or []:
        token = str(item or '').strip().lower()
        if token and token == name:
            return 'ban'
    if lot_id and str(lot_id) in {str(x) for x in data.get('off_ids') or []}:
        return 'off'
    low = desc.lower()
    for item in data.get('off_lots') or []:
        token = str(item or '').strip().lower()
        if token and token in low:
            return 'off'
    return ''
def _order_lot_id(order) -> str:
    for name in ('lot_id', 'offer_id', 'node_id', 'lotId'):
        val = getattr(order, name, None)
        if val and str(val).isdigit():
            return str(val)
    blob = ' '.join((str(getattr(order, k, '') or '') for k in ('html', 'description', 'lot_params', 'url', 'link')))
    m = re.search('(?:offer(?:\\?|&amp;|&)id=|offer_id[=:\\s"\\\']+|data-offer(?:-id)?=["\\\']?|lot[_-]?id[=:\\s"\\\']+)(\\d{4,})', blob, re.I)
    return m.group(1) if m else ''
def _pull_lot_id(cardinal, order) -> str:
    return _order_lot_id(order)
def _norm_lot_text(value) -> str:
    value = str(value or '').casefold().replace('ё', 'е')
    value = re.sub('[^a-zа-я0-9]+', ' ', value, flags=re.I)
    return re.sub('\\s+', ' ', value).strip()
def _configured_lot_candidates(data: dict, order) -> tuple[dict, list[str]]:
    lots = _lots(data)
    configured = {str(lid): row for lid, row in lots.items() if str(lid).isdigit() and str((row or {}).get('sid') or '').strip()}
    raw_candidates = [getattr(order, 'lot_name', None), getattr(order, 'title', None), getattr(order, 'description', None), getattr(order, 'short_description', None), getattr(order, 'full_description', None)]
    candidates = list(dict.fromkeys((x for x in (_norm_lot_text(v) for v in raw_candidates) if x)))
    return (configured, candidates)
def _score_configured_lots(data: dict, configured: dict, candidates: list[str]) -> list[tuple[int, str, str]]:
    cached = {str(x.get('lot_id') or ''): x for x in _cached_funpay_lots(data)}
    scores = []
    for lid, row in configured.items():
        titles = []
        for value in ((cached.get(lid) or {}).get('title'), (row or {}).get('title')):
            norm = _norm_lot_text(value)
            if norm and norm not in titles and (norm != lid) and (norm != 'lot ' + lid):
                titles.append(norm)
        best = 0
        best_title = ''
        for lot_title in titles:
            lot_tokens = [t for t in lot_title.split() if len(t) >= 2]
            for candidate in candidates:
                score = 0
                if lot_title == candidate:
                    score = 1200 + len(lot_title)
                elif len(lot_title) >= 3 and (candidate.startswith(lot_title + ' ') or candidate.startswith(lot_title + ' ') or candidate.split(' ', 1)[0] == lot_title):
                    score = 900 + len(lot_title)
                elif len(lot_title) >= 8 and (lot_title in candidate or candidate in lot_title):
                    score = 700 + min(len(lot_title), len(candidate))
                else:
                    candidate_tokens = set(candidate.split())
                    common = sum((1 for t in lot_tokens if t in candidate_tokens))
                    if len(lot_tokens) >= 2 and common == len(lot_tokens):
                        score = 500 + common * 10 + len(lot_title)
                if score > best:
                    best = score
                    best_title = lot_title
        if best:
            scores.append((best, lid, best_title))
    return sorted(scores, reverse=True)
def _infer_configured_lot_id(data: dict, order, cardinal=None) -> str:
    configured, candidates = _configured_lot_candidates(data, order)
    if not configured or not candidates:
        return ''
    scores = _score_configured_lots(data, configured, candidates)
    if scores and (not (len(scores) > 1 and scores[0][0] == scores[1][0])):
        _audit('lot_resolved_local', lot=scores[0][1], score=scores[0][0], matched_title=scores[0][2])
        return scores[0][1]
    if cardinal is None:
        if scores:
            _audit('lot_resolve_ambiguous', candidates=candidates[:4], matches=[x[1] for x in scores[:4]])
        return ''
    refreshed = []
    for lid, row in configured.items():
        try:
            fields = cardinal.account.get_lot_fields(int(lid))
            title = _lot_field_title(fields, lid)
            if title and _norm_lot_text(title) not in (lid, 'lot ' + lid):
                _cache_funpay_lot(data, {'lot_id': lid, 'title': title, 'active': bool(getattr(fields, 'active', True))})
                if _norm_lot_text((row or {}).get('title')) in ('', lid, 'lot ' + lid):
                    row['title'] = title
                refreshed.append(lid)
        except Exception as exc:
            _audit('configured_lot_refresh_failed', lot=lid, error=type(exc).__name__)
    if refreshed:
        data['lots'] = configured | {str(k): v for k, v in _lots(data).items() if str(k) not in configured}
        _save(data)
    configured, candidates = _configured_lot_candidates(data, order)
    scores = _score_configured_lots(data, configured, candidates)
    if not scores:
        _audit('lot_resolve_miss', candidates=candidates[:4], configured=list(configured)[:30], refreshed=refreshed)
        return ''
    if len(scores) > 1 and scores[0][0] == scores[1][0]:
        _audit('lot_resolve_ambiguous', candidates=candidates[:4], matches=[x[1] for x in scores[:4]], refreshed=refreshed)
        return ''
    _audit('lot_resolved_refreshed', lot=scores[0][1], score=scores[0][0], matched_title=scores[0][2], refreshed=refreshed)
    return scores[0][1]
def _lots(data: dict) -> dict:
    out = {}
    for key, val in (data.get('lots') or {}).items():
        token = str(key)
        if isinstance(val, dict):
            out[token] = {**val, 'sid': str(val.get('sid') or ''), 'title': str(val.get('title') or token), 'by': str(val.get('by') or ('id' if token.isdigit() else 'name'))}
        else:
            out[token] = {'sid': str(val), 'title': token, 'by': 'id' if token.isdigit() else 'name'}
    return out
def _note_seen(data: dict, lot_id: str, title: str) -> None:
    title = str(title or '').strip()[:80]
    lot_id = str(lot_id or '')
    if not lot_id and (not title):
        return
    rows = []
    for row in data.get('seen') or []:
        if lot_id and str(row.get('id') or '') == lot_id:
            continue
        if not lot_id and title and (str(row.get('title') or '') == title):
            continue
        rows.append(row)
    rows.insert(0, {'id': lot_id, 'title': title, 't': int(time.time())})
    data['seen'] = rows[:40]
_SVC = {'at': 0.0, 'rows': []}
_CARD = {'c': None}
_REMIND = {'on': False}
def _services(data: dict) -> list:
    now = time.time()
    if _SVC['rows'] and now - _SVC['at'] < 600:
        return _SVC['rows']
    got = _api(data, 'services')
    rows = got['raw'] if got['ok'] and isinstance(got['raw'], list) else []
    _SVC['rows'] = rows
    _SVC['at'] = now
    return rows
def _row_by_id(data: dict, sid: str) -> dict:
    want = str(sid)
    for row in _services(data):
        if str((row or {}).get('service') or '') == want:
            return row or {}
    return {}
def _row_by_id_cached(sid: str) -> dict:
    want = str(sid)
    for row in _SVC.get('rows') or []:
        if str((row or {}).get('service') or '') == want:
            return row or {}
    return {}
def _remember_service_row(row: dict) -> None:
    if not isinstance(row, dict):
        return
    sid = str(row.get('service') or '')
    if not sid:
        return
    rows = [x for x in _SVC.get('rows') or [] if str((x or {}).get('service') or '') != sid]
    rows.append(row)
    _SVC['rows'] = rows[-2000:]
    _SVC['at'] = time.time()
def _lot_sid(data: dict, title: str, lot_id: str='') -> str:
    lots = _lots(data)
    if lot_id and lot_id in lots and lots[lot_id].get('sid'):
        return lots[lot_id]['sid']
    blob = title.lower()
    best, n = ('', 0)
    for key, row in lots.items():
        if row.get('by') == 'id':
            continue
        token = str(row.get('title') or key).strip().lower()
        if token and token in blob and (len(token) > n):
            best, n = (str(row.get('sid') or ''), len(token))
    return best
def _find_service(data: dict, title: str) -> str:
    words = [w for w in re.findall('[a-zа-я0-9]{3,}', title.lower()) if w not in ('для', 'the', 'and', 'шт', 'pcs')]
    if len(words) < 2:
        return ''
    best, score = ('', 0)
    for row in _services(data):
        name = str((row or {}).get('name') or '').lower()
        hit = sum((1 for w in words if w in name))
        if hit > score:
            score = hit
            best = str((row or {}).get('service') or '')
    return best if score >= 2 else ''
def _kind_of(name: str, typ: str) -> str:
    blob = f'{name} {typ}'.lower()
    if 'comment' in blob or 'коммент' in blob:
        return 'comments'
    if 'хештег' in blob or 'hashtag' in blob:
        plural = 'хештеги' in blob or 'hashtags' in blob
        single = 'один' in blob or 'one hashtag' in blob or (not plural and 'hashtag' in blob)
        return 'hashtag' if single and (not plural) else 'hashtags'
    if 'голос' in blob or 'vote' in blob or 'опрос' in blob or ('poll' in blob):
        return 'vote'
    if 'реакц' in blob or 'reaction' in blob:
        return 'reaction'
    if 'групп' in blob or 'group' in blob:
        return 'groups'
    if 'ключев' in blob or 'keyword' in blob:
        return 'keywords'
    if 'упомина' in blob or 'mention' in blob:
        return 'mentions'
    if 'подписк' in blob or 'subscription' in blob:
        return 'subscriptions'
    if 'медиа' in blob or re.search('\\bmedia\\b', blob):
        return 'media'
    if 'подпис' in blob or 'follower' in blob or 'member' in blob:
        return 'followers'
    if 'лайк' in blob or 'like' in blob:
        return 'likes'
    if 'просмотр' in blob or 'view' in blob:
        return 'views'
    return 'other'
def _need_extra(kind: str) -> str:
    return {'comments': 'comments', 'vote': 'vote', 'reaction': 'reaction', 'hashtag': 'hashtag', 'hashtags': 'hashtags', 'groups': 'groups', 'keywords': 'keywords', 'mentions': 'mentions', 'subscriptions': 'subscriptions', 'media': 'media'}.get(kind, '')
def _send_fp(cardinal, chat_id, text: str) -> None:
    if not text:
        return
    try:
        cardinal.send_message(chat_id, text)
        _audit('buyer_message_sent', chat=chat_id)
    except Exception as exc:
        _audit('buyer_message_failed', chat=chat_id, error=type(exc).__name__)
def _link_hint(name: str, kind: str, lang: str) -> str:
    blob = name.lower()
    en = _en(lang)
    if 'telegram' in blob or 'телег' in blob:
        if kind == 'followers':
            return 'Need a channel link: https://t.me/name' if en else 'Нужна ссылка на канал: https://t.me/канал'
        if kind == 'vote':
            return 'Need a poll post: https://t.me/name/123' if en else 'Нужна ссылка на пост с опросом: https://t.me/канал/123'
        return 'Need a post link: https://t.me/name/123' if en else 'Нужна ссылка на пост: https://t.me/канал/123'
    if 'tiktok' in blob or 'тикток' in blob:
        return 'Need a video link: tiktok.com/@user/video/…' if en else 'Нужна ссылка на ролик: tiktok.com/@user/video/…'
    if 'youtube' in blob or 'ютуб' in blob:
        return 'Need a video link: youtube.com/watch?v=…' if en else 'Нужна ссылка на видео: youtube.com/watch?v=…'
    if 'instagram' in blob or 'инста' in blob:
        return 'Need a post link: instagram.com/p/…' if en else 'Нужна ссылка на пост: instagram.com/p/…'
    if kind == 'followers':
        return 'Need a profile or channel link.' if en else 'Нужна ссылка на профиль или канал.'
    if kind == 'comments':
        return 'Need a post link for the comments.' if en else 'Нужна ссылка на пост, куда написать комментарии.'
    if kind == 'vote':
        return 'Need a poll post link.' if en else 'Нужна ссылка на пост с опросом.'
    if kind == 'reaction':
        return 'Need the post link for the reaction.' if en else 'Нужна ссылка на пост, куда поставить реакцию.'
    return 'Need a profile, post, or channel link.' if en else 'Нужна ссылка на профиль, пост или канал.'
def _extra_hint(kind: str, lang: str) -> str:
    en = _en(lang)
    return {'comments': 'Then send comments. Each line starts with #.' if en else 'Потом пришлите комментарии. Каждая строка с #.', 'vote': 'Then send the poll option number, for example 1.' if en else 'Потом напишите номер варианта, например: 1.', 'reaction': 'Then send one emoji, for example ❤️. List: list' if en else 'Потом пришлите 1 эмодзи, например ❤️.\nДоступные — команда: список', 'hashtag': 'Then send one hashtag, for example #music.' if en else 'Потом пришлите один хештег, например #music.', 'hashtags': 'Then send hashtags, each line with #.' if en else 'Потом пришлите хештеги, каждая строка с #.', 'groups': 'Then send group links, each line with #.' if en else 'Потом пришлите группы, каждая строка с #.', 'keywords': 'Then send keywords, each line with #.' if en else 'Потом пришлите ключевые слова, каждая строка с #.', 'mentions': 'Then send usernames, each line with #.' if en else 'Потом пришлите ники, каждая строка с #.', 'subscriptions': 'Then send subscription details.' if en else 'Потом пришлите параметры подписки.', 'media': 'Then send a direct link to the photo or video.' if en else 'Потом пришлите прямую ссылку на фото или видео.'}.get(kind, '')
def _ask_text(sess: dict) -> str:
    en = _en(sess.get('lang') or 'ru')
    qty = int(sess.get('qty') or 1)
    mx = int(sess.get('max') or 0)
    split = ''
    if mx and qty > mx:
        n = (qty + mx - 1) // mx
        split = f'\n➗ Quantity {qty} is above the max {mx}. I will start {n} parts.' if en else f'\n➗ Количество {qty} больше максимума {mx}. Запущу {n} частями.'
    kw = dict(order_id=sess.get('order') or '', service_name=sess.get('name') or '', quantity=qty, link=sess.get('link') or '', site_id='', error='', qty_hint='', link_hint=_link_hint(str(sess.get('name') or ''), str(sess.get('kind') or ''), sess.get('lang') or 'ru'), extra_hint=_extra_hint(str(sess.get('kind') or ''), sess.get('lang') or 'ru'), split_hint=split, promo_hint='')
    if str(sess.get('tpl_ask') or '').strip():
        return _tpl(sess, 'tpl_ask', '', **kw)
    body = '✅ Payment received\n\n📦 {service_name}\n🧾 #{order_id}\n📊 {quantity} pcs\n{qty_hint}\n\n👉 Link needed now\n{link_hint}\n\n{extra_hint}\n\nSend the link in one message.\n«разделить» — several links\ncancel — stop before start; refund depends on seller settings.\n{split_hint}\n{promo_hint}' if en else '✅ Оплата прошла\n\n📦 {service_name}\n🧾 #{order_id}\n📊 {quantity} шт\n{qty_hint}\n\n👉 Сейчас нужна ссылка\n{link_hint}\n\n{extra_hint}\n\nПришлите ссылку одним сообщением.\n«разделить» — несколько ссылок\n«отмена» — вернуть деньги до запуска.\n«статус НОМЕР» — проверить статус SMM-заказа.\n{split_hint}\n{promo_hint}'
    return _fill(body, **kw)
def _prompt_text(sess: dict) -> str:
    en = _en(sess.get('lang') or 'ru')
    kind = str(sess.get('kind') or '')
    kw = dict(order_id=sess.get('order') or '', service_name=sess.get('name') or '', quantity=int(sess.get('qty') or 1), qty_hint='', link=sess.get('link') or '')
    ru = {'comments': '💬 Отправьте комментарии\n\n🧾 ID: #{order_id}\n📦 {service_name}\n📊 Нужно строк: {quantity}\n🔗 {link}\n\nКаждая строка с #. Пример:\n#Классный пост\n#Огонь', 'vote': '🗳 Укажите вариант ответа\n\n🧾 ID: #{order_id}\n📦 {service_name}\n📊 {quantity} шт\n🔗 {link}\n\nОтправьте номер варианта, за который голосовать.\nНапример: 1', 'reaction': '😍 Укажите реакцию\n\n🧾 ID: #{order_id}\n📦 {service_name}\n📊 {quantity} шт\n{qty_hint}\n🔗 {link}\n\nНапишите эмодзи или слово, например ❤️ или сердечко\nДоступные — команда: список', 'hashtag': '#️⃣ Хештег\n\n🧾 #{order_id}\n📦 {service_name}\n🔗 {link}\n\nНапишите один хештег.\nНапример: #music', 'hashtags': '#️⃣ Хештеги\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Нужно строк: {quantity}\n🔗 {link}\n\nКаждая строка с #.\n\nПример:\n#music\n#viral', 'groups': '👥 Группы\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Нужно строк: {quantity}\n🔗 {link}\n\nКаждая строка — ссылка на группу с #.\n\nПример:\n#https://vk.com/club123', 'keywords': '🔎 Ключевые слова\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Нужно строк: {quantity}\n🔗 {link}\n\nКаждая строка с #.\n\nПример:\n#купить\n#скидка', 'mentions': '👤 Username\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Нужно строк: {quantity}\n🔗 {link}\n\nКаждая строка с #. Знак # потом уберём.\n\nПример:\n#user1\n#user2', 'subscriptions': '⚙️ Подписка: сколько постов\n\n🧾 #{order_id}\n🔗 {link}\n\nНапишите минимум максимум пауза(мин).\nМожно добавить число постов и дату.\n\nПример: 1 3 60\nили: 1 3 60 10 2026-12-31', 'media': '🖼 Ссылка на медиа\n\n🧾 #{order_id}\n📦 {service_name}\n🔗 {link}\n\nПришлите прямую ссылку на фото или видео.'}
    en_map = {'comments': '💬 Send comments\n\n🧾 ID: #{order_id}\n📦 {service_name}\n📊 Lines needed: {quantity}\n🔗 {link}\n\nEach line starts with #. Example:\n#Nice post\n#Fire', 'vote': '🗳 Choose a poll option\n\n🧾 ID: #{order_id}\n📦 {service_name}\n📊 {quantity} pcs\n🔗 {link}\n\nSend the option number to vote for.\nExample: 1', 'reaction': '😍 Choose a reaction\n\n🧾 ID: #{order_id}\n📦 {service_name}\n📊 {quantity} pcs\n{qty_hint}\n🔗 {link}\n\nSend an emoji or a word, e.g. ❤️\nAvailable reactions — command: list', 'hashtag': '#️⃣ Hashtag\n\n🧾 #{order_id}\n📦 {service_name}\n🔗 {link}\n\nSend one hashtag.\nExample: #music', 'hashtags': '#️⃣ Hashtags\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Lines needed: {quantity}\n🔗 {link}\n\nEach line starts with #.\n\nExample:\n#music\n#viral', 'groups': '👥 Groups\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Lines needed: {quantity}\n🔗 {link}\n\nEach line is a group link with #.\n\nExample:\n#https://vk.com/club123', 'keywords': '🔎 Keywords\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Lines needed: {quantity}\n🔗 {link}\n\nEach line starts with #.\n\nExample:\n#buy\n#sale', 'mentions': "👤 Usernames\n\n🧾 #{order_id}\n📦 {service_name}\n📊 Lines needed: {quantity}\n🔗 {link}\n\nEach line starts with #. We'll strip # afterwards.\n\nExample:\n#user1\n#user2", 'subscriptions': '⚙️ Subscription posts\n\n🧾 #{order_id}\n🔗 {link}\n\nSend min max delay(min).\nYou can add posts and a date.\n\nExample: 1 3 60\nor: 1 3 60 10 2026-12-31', 'media': '🖼 Media link\n\n🧾 #{order_id}\n📦 {service_name}\n🔗 {link}\n\nSend a direct link to a photo or video.'}
    body = (en_map if en else ru).get(kind) or (en_map if en else ru)['comments']
    return _fill(body, **kw)
def _confirm_text(sess: dict) -> str:
    en = _en(sess.get('lang') or 'ru')
    extra = str(sess.get('extra_val') or '').strip()
    line = ''
    if extra:
        line = ('Data: ' if en else 'Данные: ') + extra[:400]
    parts = sess.get('parts') or []
    link = sess.get('link') or ''
    if parts:
        link = '\n'.join((f"{p.get('link')} · {p.get('qty')}" for p in parts))
    body = '✅ Check before start\n\n🧾 #{order_id}\n📦 {service_name}\n📊 {quantity} pcs\n{qty_hint}\n🔗 {link}\n{extra_line}\n\nyes — start\nno — send the data again\ncancel — stop; refund depends on settings' if en else '✅ Проверьте перед запуском\n\n🧾 #{order_id}\n📦 {service_name}\n📊 {quantity} шт\n{qty_hint}\n🔗 {link}\n{extra_line}\n\n«да» — запустить\n«нет» — отправить данные заново\n«отмена» — вернуть деньги'
    return _fill(body, order_id=sess.get('order') or '', service_name=sess.get('name') or '', quantity=int(sess.get('qty') or 1), qty_hint='', link=link, extra_line=line)
def _done_text(sess: dict, ids: list[str]) -> str:
    en = _en(sess.get('lang') or 'ru')
    kw = dict(order_id=sess.get('order') or '', service_name=sess.get('name') or '', quantity=int(sess.get('qty') or 1), link=sess.get('link') or '', site_id=', '.join(ids) if ids else '—', error='')
    if str(sess.get('tpl_done') or '').strip():
        return _tpl(sess, 'tpl_done', '', **kw)
    body = '✅ Order started\n\n🧾 #{order_id}\n📦 {service_name}\n📊 {quantity} pcs\n🔗 {link}\n🆔 On the site: {site_id}' if en else '✅ Заказ запущен\n\n🧾 #{order_id}\n📦 {service_name}\n📊 {quantity} шт\n🔗 {link}\n🆔 На сайте: {site_id}'
    return _fill(body, **kw)
def _int(value, fallback: int=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback
def _chunks(qty: int, limit: int) -> list[int]:
    if limit <= 0 or qty <= limit:
        return [qty]
    out = []
    left = qty
    while left > 0:
        part = min(limit, left)
        out.append(part)
        left -= part
    return out
def _split_lines(lines: list[str], limit: int) -> list[list[str]]:
    if limit <= 0 or len(lines) <= limit:
        return [lines]
    return [lines[i:i + limit] for i in range(0, len(lines), limit)]
def _bad_link(err: str) -> bool:
    low = err.lower()
    return any((w in low for w in ('link', 'url', 'ссыл', 'incorrect link')))
def _reaction_service(data: dict, base_name: str, token: str) -> str:
    needle = token.strip().lower()
    if not needle:
        return ''
    words = [w for w in re.findall('[a-zа-я0-9]{4,}', base_name.lower()) if w not in ('реакц', 'reaction')]
    best, score = ('', 0)
    for row in _services(data):
        name = str((row or {}).get('name') or '')
        low = name.lower()
        if needle not in low and needle not in name:
            continue
        hit = sum((1 for w in words if w in low))
        if hit > score:
            score = hit
            best = str((row or {}).get('service') or '')
    return best if score >= 1 else ''
def _link_confirm_text(sess: dict) -> str:
    link = str(sess.get('link') or '').strip()
    parts = sess.get('parts') or []
    if parts:
        link = '\n'.join((f"{p.get('link')} · {p.get('qty')}" for p in parts))
    return f"🔎 Проверьте ссылку перед запуском\n\n🧾 Заказ: #{sess.get('order') or '—'}\n🔗 {link or '—'}\n\nСсылка верная?\n«да» — продолжить\n«нет» — отправить другую ссылку\n«отмена» — остановить заказ"
def _reaction_list(data: dict, base_name: str) -> list[str]:
    words = [w for w in re.findall('[a-zа-я0-9]{4,}', base_name.lower())][:4]
    out = []
    for row in _services(data):
        name = str((row or {}).get('name') or '')
        low = name.lower()
        if 'реакц' not in low and 'reaction' not in low:
            continue
        if words and (not any((w in low for w in words))):
            continue
        out.append(name[:80])
        if len(out) >= 12:
            break
    return out
def _parse_extra(kind: str, text: str) -> str:
    raw = str(text or '').strip()
    if kind == 'vote':
        m = re.search('\\d{1,3}', raw)
        return m.group(0) if m else ''
    if kind == 'reaction':
        return raw.splitlines()[0].strip()[:32] if raw else ''
    if kind == 'hashtag':
        m = re.search('#\\S+', raw)
        if m:
            return m.group(0)
        word = raw.split()[0] if raw else ''
        return f"#{word.lstrip('#')}" if word else ''
    lines = _hash_lines(raw)
    if kind == 'hashtags':
        tags = lines or [x.strip() for x in raw.splitlines() if x.strip()]
        return '\n'.join((t if t.startswith('#') else f'#{t}' for t in tags))
    if kind in ('comments', 'groups', 'keywords', 'mentions'):
        return '\n'.join(lines)
    if kind == 'media':
        m = re.search('https?://\\S+', raw, re.I)
        return m.group(0).rstrip(').,]') if m else ''
    if kind == 'subscriptions':
        fields = {}
        for key, pat in (('min', '(?:мин|min)\\s*[:=]?\\s*(\\d+)'), ('max', '(?:макс|max)\\s*[:=]?\\s*(\\d+)'), ('delay', '(?:пауза|delay)\\s*[:=]?\\s*(\\d+)'), ('posts', '(?:пост\\w*|posts)\\s*[:=]?\\s*(\\d+)'), ('expiry', '(?:до|expiry|until)\\s*[:=]?\\s*(\\d{4}-\\d{2}-\\d{2})')):
            m = re.search(pat, raw, re.I)
            if m:
                fields[key] = m.group(1)
        if not fields.get('min') or not fields.get('max'):
            date = re.search('\\d{4}-\\d{2}-\\d{2}', raw)
            rest = raw.replace(date.group(0), ' ') if date else raw
            nums = re.findall('\\d+', rest)
            if len(nums) >= 3:
                fields = {'min': nums[0], 'max': nums[1], 'delay': nums[2]}
                if len(nums) >= 4:
                    fields['posts'] = nums[3]
                if date:
                    fields['expiry'] = date.group(0)
        if not fields.get('min') or not fields.get('max'):
            return ''
        return '\n'.join((f'{k}={v}' for k, v in fields.items()))
    return ''
def _order_body(sess: dict, qty: int, lines: list[str] | None, service: str) -> dict:
    kind = str(sess.get('kind') or '')
    body = {'service': service, 'link': sess.get('link') or '', 'quantity': qty}
    extra = str(sess.get('extra_val') or '')
    chunk = lines if lines is not None else [x for x in extra.splitlines() if x.strip()]
    if kind == 'comments':
        body['comments'] = '\n'.join(chunk)
        body['quantity'] = len(chunk) or qty
    elif kind == 'mentions':
        body['usernames'] = '\n'.join(chunk)
        body['quantity'] = len(chunk) or qty
    elif kind == 'hashtags':
        body['hashtags'] = '\n'.join(chunk)
        body['quantity'] = len(chunk) or qty
    elif kind == 'hashtag':
        body['hashtag'] = extra.splitlines()[0] if extra else ''
    elif kind == 'groups':
        body['groups'] = '\n'.join(chunk)
        body['quantity'] = len(chunk) or qty
    elif kind == 'keywords':
        body['keywords'] = '\n'.join(chunk)
        body['quantity'] = len(chunk) or qty
    elif kind == 'vote':
        body['answer_number'] = extra.strip()
    elif kind == 'media':
        body['media'] = extra.strip()
    elif kind == 'subscriptions':
        for line in extra.splitlines():
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            if k in ('min', 'max', 'delay', 'posts', 'expiry') and v.strip():
                body[k] = v.strip()
    return body
def _refill_days(name: str, flag: bool) -> int:
    blob = str(name or '')
    low = blob.lower()
    if re.search('no[\\s-]?refill|без\\s*рефил|\\[nr\\]', low):
        return 0
    if re.search('пожизн|lifetime|\\[r\\s*∞\\]', low):
        return 9999
    m = re.search('\\[(?:r|ar)\\s*(\\d+)\\]', blob, re.I)
    if m:
        return int(m.group(1))
    m = re.search('(\\d+)\\s*(?:дн|день|дня|дней|day|days)', low)
    if m:
        return int(m.group(1))
    return 9999 if flag else 0
def _tpl(sess: dict, key: str, default: str, **kw) -> str:
    custom = str(sess.get(key) or '').strip()
    if not custom:
        return default
    return _fill(custom, **kw)
def _refund_fp(cardinal, order_id: str) -> tuple[bool, str]:
    fn = getattr(getattr(cardinal, 'account', None), 'refund', None)
    if not fn:
        return (False, 'в этой сборке Cardinal нет возврата')
    try:
        fn(str(order_id))
        return (True, '')
    except Exception as e:
        return (False, str(e)[:180])
def _do_refill(cardinal, data: dict, chat_id: str) -> None:
    rows = [(k, v) for k, v in (data.get('orders') or {}).items() if str(v.get('chat')) == str(chat_id) and v.get('site_order')]
    if not rows:
        _send_fp(cardinal, chat_id, 'Запущенного заказа в этом чате нет.')
        return
    oid, row = sorted(rows, key=lambda kv: float(kv[1].get('done_at') or 0))[-1]
    days = _int(row.get('refill_days'), 0)
    done = float(row.get('done_at') or 0)
    if days <= 0:
        _send_fp(cardinal, chat_id, f'У заказа #{oid} нет рефила.')
        return
    if days < 9999 and done and (time.time() > done + days * 86400):
        _send_fp(cardinal, chat_id, f'Срок гарантии заказа #{oid} вышел ({days} дн.).')
        return
    notes = []
    for site_id in [x.strip() for x in str(row.get('site_order') or '').split(',') if x.strip()]:
        got = _api(data, 'refill', order=site_id)
        if got['ok']:
            notes.append(f'🆔 {site_id}: рефил отправлен')
        else:
            notes.append(f"🆔 {site_id}: {got['error']}")
            _log_err(data, f"#{oid} рефил {site_id}: {got['error']}")
    _send_fp(cardinal, chat_id, f'♻️ Заказ #{oid}\n' + '\n'.join(notes))
def _parse_split(text: str) -> list[dict]:
    parts = []
    for line in str(text or '').splitlines():
        m = re.search('(https?://\\S+)\\s*[|xх×*]\\s*(\\d+)', line, re.I)
        if not m:
            continue
        parts.append({'link': m.group(1).rstrip(').,]'), 'qty': int(m.group(2))})
    return parts
def _remember(cardinal) -> None:
    _CARD['c'] = cardinal
    if _REMIND['on']:
        return
    _REMIND['on'] = True
    def loop():
        while True:
            time.sleep(45)
            cardinal_ref = _CARD.get('c')
            if cardinal_ref is None:
                continue
            try:
                with _LOCK:
                    data = _load()
                    sends = []
                    changed = False
                    now = time.time()
                    for chat, sess in list((data.get('sessions') or {}).items()):
                        age = now - float(sess.get('ts') or now)
                        en = _en(sess.get('lang') or 'ru')
                        oid = sess.get('order') or ''
                        if age >= EXPIRE_AFTER:
                            data['sessions'].pop(chat, None)
                            changed = True
                            sends.append((chat, f'⏹ Waiting time is over for #{oid}. The order was not started.' if en else f'⏹ Время ожидания по заказу #{oid} вышло.\nСсылку я больше не жду, заказ не запущен.\nЕсли оплата прошла — напишите продавцу.'))
                        elif age >= REMIND_AFTER and (not sess.get('reminded')):
                            sess['reminded'] = True
                            changed = True
                            stage = sess.get('stage') or 'link'
                            if stage == 'confirm':
                                text = f'⏰ Still waiting for yes or no on #{oid}.' if en else f'⏰ Заказ #{oid} всё ещё ждёт «да» или «нет».'
                            elif stage == 'extra':
                                text = f'⏰ Order #{oid} still needs the details after the link.' if en else f'⏰ Заказ #{oid} всё ещё ждёт данные после ссылки.'
                            else:
                                text = f'⏰ Order #{oid} is still waiting for the link.\nSend it in one message.' if en else f'⏰ Заказ #{oid} всё ещё ждёт ссылку.\nПришлите её одним сообщением.\n«отмена» — остановить ожидание.'
                            sends.append((chat, text))
                    extra_fp, notes, extra_changed = _sweep(cardinal_ref, data)
                    sends.extend(extra_fp)
                    ticket_notes, ticket_changed = _auto_ticket_tick(cardinal_ref, data)
                    notes.extend(ticket_notes)
                    if changed or extra_changed or ticket_changed:
                        _save(data)
                    for chat, text in sends:
                        _send_fp(cardinal_ref, chat, text)
                    for note in notes:
                        _notify_admins(cardinal_ref, data, note)
            except Exception as exc:
                _audit('worker_error', error=type(exc).__name__)
    threading.Thread(target=loop, name='blazysmm-lite-remind', daemon=True).start()
def _ru_status(value: str) -> str:
    raw = str(value or '').strip()
    if not raw:
        return 'неизвестен'
    if raw in STATUS_RU:
        return STATUS_RU[raw]
    low = raw.casefold()
    for key, translated in STATUS_RU.items():
        if key.casefold() == low:
            return translated
    return raw
def _status_lines(data: dict, oid: str, lang: str='ru') -> str:
    row = (data.get('orders') or {}).get(oid) or {}
    ids = [x.strip() for x in str(row.get('site_order') or '').split(',') if x.strip()]
    if not ids:
        return f'🧾 Заказ #{oid}\n⏳ Заказ ещё не запущен в SMM. Сначала нужно отправить и подтвердить ссылку.'
    lines = [f'🧾 Заказ FunPay: #{oid}']
    for site_id in ids[:6]:
        got = _api(data, 'status', order=site_id)
        if not got.get('ok'):
            lines.append(f"\n🆔 SMM-заказ: {site_id}\n⚠️ Не удалось получить статус: {got.get('error') or 'неизвестная ошибка'}")
            continue
        raw = got.get('raw') or {}
        st = _ru_status(raw.get('status') or '')
        remains = raw.get('remains', '—')
        start_count = raw.get('start_count', raw.get('start', '—'))
        lines.append(f'\n🆔 SMM-заказ: {site_id}\n📌 Статус: {st}\n📉 Осталось: {remains}\n📈 Стартовое значение: {start_count}')
    return '\n'.join(lines)
def _status_by_number(data: dict, number: str) -> str:
    token = str(number or '').strip().lstrip('#')
    if not token:
        return 'Напишите: статус НОМЕР_ЗАКАЗА'
    orders = data.get('orders') or {}
    for fp_id in orders:
        if str(fp_id).casefold() == token.casefold():
            return _status_lines(data, str(fp_id), 'ru')
    owner_fp = ''
    for fp_id, row in orders.items():
        ids = [x.strip() for x in str((row or {}).get('site_order') or '').split(',') if x.strip()]
        if any((x.casefold() == token.casefold() for x in ids)):
            owner_fp = str(fp_id)
            break
    got = _api(data, 'status', order=token)
    if not got.get('ok'):
        suffix = f'\nFunPay-заказ: #{owner_fp}' if owner_fp else ''
        return f"❌ Не удалось получить статус заказа {token}.\n{got.get('error') or 'Неизвестная ошибка.'}{suffix}"
    raw = got.get('raw') or {}
    st = _ru_status(raw.get('status') or '')
    remains = raw.get('remains', '—')
    start_count = raw.get('start_count', raw.get('start', '—'))
    lines = [f'🆔 SMM-заказ: {token}', f'📌 Статус: {st}', f'📉 Осталось: {remains}', f'📈 Стартовое значение: {start_count}']
    if owner_fp:
        lines.insert(0, f'🧾 FunPay-заказ: #{owner_fp}')
    return '\n'.join(lines)
def _on_new_order(cardinal, event, *args):
    order = getattr(event, 'order', None)
    if order is None:
        return
    oid = str(getattr(order, 'id', '') or '')
    try:
        _audit('new_order_queued', order=oid, order_type=type(order).__name__)
    except Exception:
        pass
    try:
        threading.Thread(target=_process_new_order, args=(cardinal, event, *args), daemon=True, name=('BlazySMM-order-' + (oid or 'unknown'))[:64]).start()
    except Exception as exc:
        _audit('new_order_thread_failed', order=oid, error=type(exc).__name__)
@_serialized
def _process_new_order(cardinal, event, *args):
    _remember(cardinal)
    order = getattr(event, 'order', None)
    if order is None:
        return
    data = _load()
    if not data.setdefault('settings', {}).get('plugin_enabled', True):
        return
    desc = ' '.join((str(getattr(order, k, '') or '') for k in ('description', 'title', 'lot_name', 'short_description', 'full_description')))
    title = str(getattr(order, 'description', None) or getattr(order, 'title', None) or '')
    oid = str(getattr(order, 'id', '') or '')
    chat_id = getattr(order, 'chat_id', None)
    if not oid or chat_id is None:
        _audit('new_order_invalid', order=oid, has_chat=chat_id is not None, order_type=type(order).__name__)
        return
    _audit('new_order_received', order=oid, order_type=type(order).__name__, description=title[:180], subcategory=str(getattr(order, 'subcategory_name', '') or '')[:120], direct_lot=_order_lot_id(order))
    explicit_sid = _service_id(desc)
    lot_id = _pull_lot_id(cardinal, order)
    inferred_lot = False
    if not lot_id:
        lot_id = _infer_configured_lot_id(data, order, cardinal)
        inferred_lot = bool(lot_id)
    if oid in data['orders']:
        _audit('duplicate_order_ignored', order=oid)
        return
    if _blocked(data, order, f'{title}\n{desc}', lot_id):
        _audit('order_blocked', order=oid, lot=lot_id)
        return
    sid = explicit_sid or _lot_sid(data, f'{title}\n{desc}', lot_id)
    if not sid:
        _audit('order_ignored_unbound', order=oid, lot=lot_id, reason='no_configured_binding_or_blazy_id', title=title[:160])
        return
    _audit('new_order_target', order=oid, lot=lot_id, service=sid, inferred_lot=inferred_lot)
    _note_seen(data, lot_id, title)
    row = _row_by_id_cached(sid)
    if not row:
        info = _api(data, 'info', service=sid)
        if not info['ok']:
            _log_err(data, f"#{oid} услуга {sid}: {info['error']}")
            _send_fp(cardinal, chat_id, f"❌ Не могу взять услугу {sid}\n{info['error']}")
            return
        row = info['raw'] or {}
        _remember_service_row(row)
    name = str(row.get('name') or f'Услуга {sid}')
    kind = _kind_of(name, str(row.get('type') or '') + ' ' + str(row.get('category') or ''))
    mn = _int(row.get('min'), 1)
    mx = _int(row.get('max'), 0)
    amount = _int(getattr(order, 'amount', None) or getattr(order, 'count', None) or getattr(order, 'quantity', None), 0)
    per = _int((_lots(data).get(lot_id) or {}).get('per'), 1) or 1
    qty = (amount or 1) * per
    if qty < mn:
        qty = mn
    bal = _api(data, 'balance')
    rate = float(row.get('rate') or 0)
    if bal['ok'] and rate:
        have = float((bal['raw'] or {}).get('balance') or 0)
        need = rate * qty / 1000.0
        if have + 1e-06 < need:
            _log_err(data, f'#{oid} не хватает баланса: нужно ~{need:.4f}, есть {have:.4f}')
            _balance_failure(cardinal, data, order, lot_id, sid)
            if data.get('orders', {}).get(oid, {}).get('refunded'):
                _send_fp(cardinal, chat_id, 'Оплата возвращена: на панели недостаточно баланса для запуска.')
                return
            lang = _lang_of(title)
            text = '⏸ Start is paused: the panel balance is too low.\nThe seller will top up and message you.' if _en(lang) else '⏸ Запуск пока недоступен: на панели не хватает баланса.\nПродавец пополнит кабинет и напишет вам.'
            _send_fp(cardinal, chat_id, text)
            return
    lang = 'ru'
    sess = {'order': oid, 'buyer': _buyer(order), 'service': str(sid), 'name': name, 'kind': kind, 'extra': _need_extra(kind), 'extra_val': '', 'stage': 'link', 'qty': qty, 'min': mn, 'max': mx, 'link': '', 'lot_id': lot_id, 'refill_days': _refill_days(name, bool(row.get('refill'))), 'tpl_ask': str(data.setdefault('settings', {}).get('tpl_ask') or ''), 'tpl_done': str(data['settings'].get('tpl_done') or ''), 'tpl_err': str(data['settings'].get('tpl_err') or ''), 'lang': lang, 'reminded': False, 'ts': time.time()}
    data['sessions'][str(chat_id)] = sess
    data['orders'][oid] = {'service': str(sid), 'name': name, 'chat': str(chat_id), 'ts': time.time(), 'site_order': '', 'lang': lang, 'lot_id': lot_id, 'buyer': _buyer(order), 'qty': qty}
    _save(data)
    _notify_admins(cardinal, data, f"🛒 Новый заказ FunPay #{oid}\n📦 LOT ID: {lot_id or 'не указан'}\n🔗 BlazySMM: {sid}\n🔢 Количество: {qty}", 'new')
    _send_fp(cardinal, chat_id, _ask_text(sess))
def _submit(cardinal, data: dict, chat_id: str, sess: dict) -> None:
    en = _en(sess.get('lang') or 'ru')
    kind = str(sess.get('kind') or '')
    extra = str(sess.get('extra_val') or '')
    lines = [x for x in extra.splitlines() if x.strip()]
    service = str(sess.get('service') or '')
    if kind == 'reaction':
        found = _reaction_service(data, str(sess.get('name') or ''), extra)
        if not found:
            _log_err(data, f"#{sess.get('order')} нет услуги под реакцию {extra}")
            _send_fp(cardinal, chat_id, f'❌ No service for reaction {extra}. Command: list' if en else f'❌ Нет услуги под реакцию {extra}.\nКоманда: список')
            return
        service = found
        lines = []
    limit = _int(sess.get('max'), 0)
    if kind in ('comments', 'mentions', 'hashtags', 'groups', 'keywords') and lines:
        parts = _split_lines(lines, limit or len(lines))
        bodies = [_order_body(sess, len(part), part, service) for part in parts]
    elif sess.get('parts'):
        bodies = []
        for part in sess.get('parts') or []:
            one = dict(sess)
            one['link'] = part.get('link') or ''
            bodies.append(_order_body(one, _int(part.get('qty'), 1), None, service))
    else:
        bodies = [_order_body(sess, part, None, service) for part in _chunks(_int(sess.get('qty'), 1), limit)]
    ids = []
    oid = str(sess.get('order') or '')
    record = data['orders'].setdefault(oid, {})
    if record.get('submission_attempted'):
        _send_fp(cardinal, chat_id, 'Запрос уже отправлялся. Продавец проверит его результат перед повторным запуском.')
        return
    record['submission_attempted'] = True
    _save(data)
    for body in bodies:
        got = _api(data, 'add', **body)
        if not got['ok']:
            err = got['error']
            _log_err(data, f'#{oid} не создался: {err}')
            if not ids and _bad_link(err):
                record['submission_attempted'] = False
                sess['link'] = ''
                sess['stage'] = 'link'
                sess['reminded'] = False
                sess['ts'] = time.time()
                data['sessions'][str(chat_id)] = sess
                _save(data)
                retry = f'❌ The site rejected the link.\n{err}\n\nSend another link.' if en else f'❌ Сайт не принял ссылку.\n{err}\n\nПришлите другую ссылку одним сообщением.'
                _send_fp(cardinal, chat_id, retry)
                return
            done = f" Уже запущены SMM-заказы: {', '.join(ids)}." if ids else ''
            err_text = _tpl(sess, 'tpl_err', f'❌ Заказ не создался\n{err}{done}' if not en else f'❌ Order was not created\n{err}{done}', order_id=oid, service_name=sess.get('name') or '', quantity=int(sess.get('qty') or 1), link=sess.get('link') or '', site_id=', '.join(ids), error=err)
            _send_fp(cardinal, chat_id, err_text)
            if ids:
                data['orders'].setdefault(oid, {})['site_order'] = ','.join(ids)
                data['sessions'].pop(str(chat_id), None)
                _save(data)
            return
        site_id = str((got['raw'] or {}).get('order') or '')
        if not site_id:
            _log_err(data, f'#{oid}: API не вернул ID; запрос мог выполниться. Требуется ручная проверка.')
            _send_fp(cardinal, chat_id, 'Ответ SMM требует проверки продавцом. Повторный запуск заблокирован.')
            return
        if site_id:
            ids.append(site_id)
            record['site_order'] = ','.join(ids)
            _save(data)
            _audit('smm_order_created', order=oid, site_order=site_id, service=service)
    if oid and ids:
        row = data['orders'].setdefault(oid, {})
        row['site_order'] = ','.join(ids)
        row['link'] = sess.get('link') or ''
        row['chat'] = str(chat_id)
        row['lang'] = sess.get('lang') or 'ru'
        row['lot_id'] = str(sess.get('lot_id') or '')
        row['refill_days'] = _int(sess.get('refill_days'), 0)
        row['done_at'] = time.time()
        cfg = data.setdefault('settings', {})
    data['sessions'].pop(str(chat_id), None)
    _save(data)
    if ids:
        _notify_admins(cardinal, data, f"Заказ #{oid} запущен: {', '.join(ids)}.", 'started')
        _send_fp(cardinal, chat_id, _done_text(sess, ids))
    else:
        _log_err(data, f'#{oid}: API не вернул номер заказа; проверьте кабинет перед повторным запуском.')
def _place(cardinal, data: dict, chat_id: str, sess: dict, text: str) -> None:
    word = str(text or '').strip().lower()
    if word in ('подтвердил', 'подтверждаю', 'подтвердить', 'confirmed'):
        _mark_confirmed(data, str(chat_id))
        _save(data)
        en = _en(sess.get('lang') or 'ru')
        _send_fp(cardinal, chat_id, 'Got it. I will not remind you to confirm.' if en else 'Спасибо, подтверждение отмечено.')
        return
    if word in ('статус', 'status'):
        if data.get('settings', {}).get('status_command_enabled', True):
            _send_fp(cardinal, chat_id, _status_lines(data, str(sess.get('order') or ''), 'ru'))
        return
    if word in ('рефил', 'refill'):
        _do_refill(cardinal, data, str(chat_id))
        return
    if word in ('разделить', 'split') and (sess.get('stage') or 'link') in ('link', 'link_confirm', 'confirm'):
        if sess.get('extra') in ('comments', 'mentions', 'hashtags', 'groups', 'keywords'):
            _send_fp(cardinal, chat_id, 'Этот тип услуги не делится по ссылкам. Пришлите данные как просили.')
            return
        sess['stage'] = 'split'
        data['sessions'][str(chat_id)] = sess
        _save(data)
        _send_fp(cardinal, chat_id, f"Пришлите ссылки. Сумма количеств должна быть {int(sess.get('qty') or 1)}.\nКаждая строка: ссылка | количество\nhttps://example.com/1 | 100\nhttps://example.com/2 | 50")
        return
    if (sess.get('stage') or '') == 'split':
        parts = _parse_split(text)
        want = int(sess.get('qty') or 1)
        got = sum((int(p.get('qty') or 0) for p in parts))
        if not parts or got != want:
            _send_fp(cardinal, chat_id, f'Нужны строки «ссылка | количество». Сейчас сумма {got}, в заказе {want}.')
            return
        sess['parts'] = parts
        sess['link'] = parts[0]['link']
        if data.get('settings', {}).get('link_confirm_enabled', True):
            sess['stage'] = 'link_confirm'
            data['sessions'][str(chat_id)] = sess
            _save(data)
            _send_fp(cardinal, chat_id, _link_confirm_text(sess))
        elif sess.get('extra'):
            sess['stage'] = 'extra'
            data['sessions'][str(chat_id)] = sess
            _save(data)
            _send_fp(cardinal, chat_id, _prompt_text(sess))
        else:
            _submit(cardinal, data, chat_id, sess)
        return
    if word in ('отмена', 'cancel'):
        oid = str(sess.get('order') or '')
        row = data['orders'].setdefault(oid, {})
        if row.get('submission_attempted') or row.get('site_order'):
            _send_fp(cardinal, chat_id, 'Запрос уже отправлялся в SMM. Для отмены и возврата нужна проверка продавца.')
            return
        data['sessions'].pop(str(chat_id), None)
        if not data['settings'].get('auto_refund'):
            _save(data)
            _send_fp(cardinal, chat_id, 'Ожидание остановлено. Для возврата оплаты обратитесь к продавцу.')
            return
        if row.get('refund_attempted'):
            _send_fp(cardinal, chat_id, 'Возврат уже запрашивался. Продавец проверит его результат.')
            return
        row['refund_attempted'] = True
        _save(data)
        ok, error = _refund_fp(cardinal, oid)
        row['refunded'] = ok
        row['refund_error'] = error
        _save(data)
        _audit('refund', order=oid, success=ok)
        _notify_admins(cardinal, data, f"Возврат #{oid}: {('выполнен' if ok else error)}", 'refund')
        _send_fp(cardinal, chat_id, 'Оплата возвращена.' if ok else 'Возврат не подтверждён. Обратитесь к продавцу.')
        return
    if word in ('список', 'list') and sess.get('kind') == 'reaction':
        names = _reaction_list(data, str(sess.get('name') or ''))
        en = _en(sess.get('lang') or 'ru')
        if not names:
            _send_fp(cardinal, chat_id, 'No reaction list from the site.' if en else 'Список реакций с сайта пуст. Напишите эмодзи, например ❤️')
        else:
            head = 'Available reactions:' if en else 'Доступные реакции:'
            _send_fp(cardinal, chat_id, head + '\n' + '\n'.join((f'• {n}' for n in names)))
        return
    sess['lang'] = 'ru'
    stage = sess.get('stage') or 'link'
    if word in ('нет', 'no') and stage in ('confirm', 'link_confirm'):
        sess['link'] = ''
        sess['extra_val'] = ''
        sess.pop('parts', None)
        sess['stage'] = 'link'
        sess['reminded'] = False
        data['sessions'][str(chat_id)] = sess
        _save(data)
        _send_fp(cardinal, chat_id, _ask_text(sess))
        return
    if word in ('да', 'yes') and stage == 'link_confirm':
        if sess.get('extra'):
            sess['stage'] = 'extra'
            data['sessions'][str(chat_id)] = sess
            _save(data)
            _send_fp(cardinal, chat_id, _prompt_text(sess))
        else:
            _submit(cardinal, data, chat_id, sess)
        return
    if word in ('да', 'yes') and stage == 'confirm':
        _submit(cardinal, data, chat_id, sess)
        return
    if stage == 'link':
        link = _link(text)
        if not link:
            _audit('buyer_link_missing', order=sess.get('order'), chat=str(chat_id), text=str(text)[:180])
            _send_fp(cardinal, chat_id, _ask_text(sess) + ("\n\nI don't see a link." if _en(sess.get('lang') or 'ru') else '\n\nСсылку в сообщении не вижу.'))
            return
        _audit('buyer_link_received', order=sess.get('order'), chat=str(chat_id), link=link[:220])
        sess['link'] = link
        sess['lang'] = 'ru'
        if data.get('settings', {}).get('link_confirm_enabled', True):
            sess['stage'] = 'link_confirm'
            data['sessions'][str(chat_id)] = sess
            _save(data)
            _send_fp(cardinal, chat_id, _link_confirm_text(sess))
        elif sess.get('extra'):
            sess['stage'] = 'extra'
            data['sessions'][str(chat_id)] = sess
            _save(data)
            _send_fp(cardinal, chat_id, _prompt_text(sess))
        else:
            _submit(cardinal, data, chat_id, sess)
        return
    if stage == 'extra':
        val = _parse_extra(str(sess.get('kind') or ''), text)
        if not val:
            _send_fp(cardinal, chat_id, _prompt_text(sess))
            return
        sess['extra_val'] = val
        if sess.get('kind') in ('comments', 'mentions', 'hashtags', 'groups', 'keywords'):
            n = len([x for x in val.splitlines() if x.strip()])
            if n:
                sess['qty'] = n
        sess['stage'] = 'confirm'
        data['sessions'][str(chat_id)] = sess
        _save(data)
        _send_fp(cardinal, chat_id, _confirm_text(sess))
        return
    link = _link(text)
    if link:
        sess['link'] = link
        sess['lang'] = 'ru'
        if data.get('settings', {}).get('link_confirm_enabled', True):
            sess['stage'] = 'link_confirm'
            data['sessions'][str(chat_id)] = sess
            _save(data)
            _send_fp(cardinal, chat_id, _link_confirm_text(sess))
        elif sess.get('extra'):
            sess['stage'] = 'extra'
            data['sessions'][str(chat_id)] = sess
            _save(data)
            _send_fp(cardinal, chat_id, _prompt_text(sess))
        else:
            _submit(cardinal, data, chat_id, sess)
        return
    _send_fp(cardinal, chat_id, _confirm_text(sess))
def _clean_username(value) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        for attr in ('username', 'name', 'login'):
            candidate = getattr(value, attr, None)
            if candidate:
                value = candidate
                break
    value = str(value or '').strip()
    return re.sub('[\\u200b-\\u200f\\u2060\\ufeff]', '', value).strip()
def _message_author(msg) -> str:
    for attr in ('author', 'author_name', 'username'):
        value = getattr(msg, attr, None)
        cleaned = _clean_username(value)
        if cleaned:
            return cleaned
    return ''
def _resolve_message_session(data: dict, chat_id, author: str):
    sessions = data.setdefault('sessions', {})
    key = str(chat_id)
    direct = sessions.get(key)
    if direct:
        return (key, direct, 'chat_id')
    who = _clean_username(author).casefold()
    if not who:
        return (key, None, 'none')
    matches = []
    for old_key, sess in list(sessions.items()):
        if not isinstance(sess, dict):
            continue
        buyer = _clean_username(sess.get('buyer')).casefold()
        if buyer and buyer == who:
            matches.append((old_key, sess))
    if not matches:
        return (key, None, 'none')
    matches.sort(key=lambda item: float((item[1] or {}).get('ts') or 0), reverse=True)
    old_key, sess = matches[0]
    if old_key != key:
        sessions.pop(old_key, None)
        sessions[key] = sess
        oid = str(sess.get('order') or '')
        if oid and oid in (data.get('orders') or {}):
            data['orders'][oid]['chat'] = key
        sess['chat_id'] = key
        _save(data)
        _audit('message_session_rebound', order=oid, buyer=author, old_chat=old_key, new_chat=key)
    return (key, sess, 'buyer')
@_serialized
def _on_message(cardinal, event, *args):
    _remember(cardinal)
    msg = getattr(event, 'message', None)
    if msg is None:
        return
    text = str(getattr(msg, 'text', '') or '')
    chat_id = getattr(msg, 'chat_id', None)
    if not text or chat_id is None:
        return
    author = _message_author(msg)
    me = _clean_username(getattr(getattr(cardinal, 'account', None), 'username', ''))
    author_norm = _clean_username(author).casefold()
    if author_norm and me and (author_norm == me.casefold()):
        return
    author_id = getattr(msg, 'author_id', None)
    if author_id == 0 or author_norm == 'funpay':
        return
    data = _load()
    if not data.setdefault('settings', {}).get('plugin_enabled', True):
        return
    if _buyer_blocked(data, author):
        return
    session_key, sess, resolved_by = _resolve_message_session(data, chat_id, author)
    word = text.strip().lower()
    status_match = re.fullmatch('(?:статус|status)\\s+#?([A-Za-z0-9_-]+)', text.strip(), re.I)
    if status_match:
        if not data.get('settings', {}).get('status_command_enabled', True):
            return
        number = status_match.group(1)
        _audit('buyer_status_command', chat=str(chat_id), buyer=author, order=number)
        _send_fp(cardinal, chat_id, _status_by_number(data, number))
        return
    if sess:
        _audit('buyer_message_received', order=sess.get('order'), stage=sess.get('stage'), chat=str(chat_id), buyer=author, resolved_by=resolved_by, text=text[:220])
    elif word in ('рефил', 'refill', 'подтвердил', 'подтверждаю', 'подтвердить', 'confirmed', 'статус', 'status'):
        _audit('buyer_command_without_session', chat=str(chat_id), buyer=author, command=word[:40])
    if not sess:
        if word in ('рефил', 'refill'):
            _do_refill(cardinal, data, str(chat_id))
            return
        if word in ('подтвердил', 'подтверждаю', 'подтвердить', 'confirmed'):
            _mark_confirmed(data, str(chat_id))
            _save(data)
            _send_fp(cardinal, chat_id, 'Спасибо, подтверждение отмечено.')
            return
        if _place_bonus(cardinal, data, str(chat_id), text):
            return
        if word in ('статус', 'status'):
            if not data.get('settings', {}).get('status_command_enabled', True):
                return
            rows = [(k, v) for k, v in (data.get('orders') or {}).items() if str(v.get('chat')) == str(chat_id)]
            if not rows:
                return
            last = sorted(rows, key=lambda kv: float((kv[1] or {}).get('ts') or 0))[-1]
            _send_fp(cardinal, chat_id, _status_lines(data, last[0], 'ru'))
        return
    if _buyer_blocked(data, sess.get('buyer', '')):
        return
    sess['lang'] = 'ru'
    _audit('buyer_step', order=sess.get('order'), stage=sess.get('stage'), chat=str(chat_id))
    for template in ('tpl_ask', 'tpl_done', 'tpl_err'):
        sess[template] = data['settings'].get(template, '')
    _place(cardinal, data, str(chat_id), sess, text)
def _fmt_when(ts: int) -> str:
    try:
        return time.strftime('%d.%m %H:%M', time.localtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return '—'
def _seen_rows(data: dict) -> list:
    lots = _lots(data)
    rows = []
    used = set()
    for row in data.get('seen') or []:
        lid = str(row.get('id') or '')
        title = str(row.get('title') or '')
        key = lid or title
        if not key or key in used:
            continue
        used.add(key)
        rows.append({'id': lid, 'title': title or lid})
    for key, row in lots.items():
        if key in used or row.get('title') in used:
            continue
        used.add(key)
        rows.append({'id': key if row.get('by') == 'id' else '', 'title': row.get('title') or key, 'key': key})
    return rows[:20]
def _lots_text(data: dict) -> str:
    rows = _seen_rows(data)
    if not rows:
        return '<b>Лоты</b>\nПока пусто. Когда придёт оплата, лот появится здесь кнопкой.\nНомер лота FunPay важнее слова в описании. Можно сразу прислать <code>номер_лота | номер_услуги</code>.'
    return '<b>Лоты</b>\nНажмите лот. Привязка по номеру лота, описание можно не менять.\nИли пришлите <code>49482123 | 12345</code> — номер лота и номер услуги.'
def _lots_kb(data: dict):
    offs = {str(x) for x in data.get('off_ids') or []}
    lots = _lots(data)
    rows = []
    for row in _seen_rows(data)[:12]:
        lid = str(row.get('id') or '')
        title = str(row.get('title') or lid or 'лот')[:32]
        bound = lots.get(lid) or {}
        if lid and lid in offs:
            mark = 'молчит'
        elif bound.get('sid'):
            mark = '→ ' + bound['sid']
        else:
            mark = 'нет услуги'
        token = lid or str(row.get('key') or '')
        if not token:
            continue
        rows.append([(f'{title} · {mark}', PREFIX + 'L:' + token[:40])])
    rows.append([('➕ Добавить по номеру', PREFIX + 'bindnew')])
    rows.append([('◀️ Назад', PREFIX + 'lot_settings')])
    return _kb(rows)
def _lot_card(data: dict, token: str) -> str:
    lots = _lots(data)
    row = lots.get(token) or {}
    seen = next((s for s in data.get('seen') or [] if str(s.get('id') or '') == token or str(s.get('title') or '') == token), {})
    cached = next((s for s in _cached_funpay_lots(data) if str(s.get('lot_id') or '') == token), {})
    title = row.get('title') or seen.get('title') or cached.get('title') or token
    sid = row.get('sid') or 'не привязана'
    quiet = token in {str(x) for x in data.get('off_ids') or []}
    active = bool(cached.get('active', True))
    bonus_sid = row.get('bonus_sid') or '—'
    return f"📦 <b>{escape(str(title))}</b>\n\n🆔 LOT ID: <code>{escape(str(token))}</code>\n🔗 Услуга BlazySMM: <code>{escape(str(sid))}</code>\n🔢 Количество: <b>{row.get('per') or 1}</b> шт. SMM за 1 шт. FunPay\n🎁 Бонус лота: <code>{escape(str(bonus_sid))}</code>\n🛍 Лот на FunPay: <b>{('🟢 активен' if active else '🔴 выключен')}</b>\n🤖 Обработка плагином: <b>{('⏸ выключена' if quiet else '🟢 включена')}</b>"
def _lot_kb(data: dict, token: str, back: str='lot_settings'):
    quiet = token in {str(x) for x in data.get('off_ids') or []}
    cached = next((s for s in _cached_funpay_lots(data) if str(s.get('lot_id') or '') == token), {})
    active = bool(cached.get('active', True))
    rows = [[('🔗 Привязать услугу', PREFIX + 'Lbind:' + token[:36])], [('🎁 Бонус лота', PREFIX + 'Lb:' + token[:15]), ('🔢 Количество', PREFIX + 'Lp:' + token[:15])], [('🔴 Выключить лот' if active else '🟢 Включить лот', PREFIX + 'Lfp:' + token[:36])], [('▶️ Включить обработку' if quiet else '⏸ Выключить обработку', PREFIX + ('Loff:' if quiet else 'Lon:') + token[:36])], [('🗑 Отвязать услугу', PREFIX + 'Ldel:' + token[:36])], [('◀️ К лотам', PREFIX + back)]]
    return _kb(rows)
def _notify_admins(cardinal, data: dict, text: str, kind: str='bonus') -> None:
    if not data.get('settings', {}).get('notify_' + kind, True):
        return
    bot = getattr(getattr(cardinal, 'telegram', None), 'bot', None)
    if not bot or not text:
        return
    for chat in data.get('admin_chats') or []:
        try:
            bot.send_message(chat, text)
        except Exception:
            pass
def _bonus_offer(data: dict, lot_id: str) -> tuple[bool, str, int]:
    cfg = data.setdefault('settings', {})
    lot = _lots(data).get(str(lot_id or ''), {})
    if lot.get('bonus_off'):
        return (False, '', 0)
    sid = str(lot.get('bonus_sid') or cfg.get('bonus_sid') or '').strip()
    qty = _int(lot.get('bonus_qty') or cfg.get('bonus_qty'), 10)
    if qty <= 0:
        qty = 10
    return (bool(cfg.get('bonus_on')) and bool(sid), sid, qty)
def _mark_confirmed(data: dict, chat_id: str) -> int:
    n = 0
    for row in (data.get('orders') or {}).values():
        if str(row.get('chat') or '') != str(chat_id):
            continue
        if row.get('confirmed'):
            continue
        row['confirmed'] = True
        n += 1
    return n
def _stars_of(cardinal, fp_order_id: str) -> int:
    getter = getattr(getattr(cardinal, 'account', None), 'get_order', None)
    if not getter or not fp_order_id:
        return -1
    try:
        order = getter(fp_order_id)
    except Exception:
        return -1
    review = getattr(order, 'review', None)
    if review is None:
        return -1
    stars = getattr(review, 'stars', None)
    if stars is None:
        stars = getattr(review, 'rating', None)
    if stars is None and isinstance(review, dict):
        stars = review.get('stars') or review.get('rating')
    try:
        return max(0, min(5, int(stars or 0)))
    except (TypeError, ValueError):
        return -1
def _sweep(cardinal, data: dict) -> tuple[list, list, bool]:
    if not data['settings'].get('plugin_enabled', True):
        return ([], [], False)
    sends, notes = ([], [])
    changed = False
    now = time.time()
    cfg = data['settings']
    minimum = int(cfg.get('bonus_stars', 5))
    rows = sorted(((oid, row) for oid, row in data['orders'].items() if row.get('site_order')), key=lambda item: item[1].get('checked_at', 0))
    for oid, row in rows[:15]:
        if not row.get('site_order') or now - row.get('checked_at', 0) < 120:
            continue
        row['checked_at'] = now
        changed = True
        statuses = []
        for sid in str(row['site_order']).split(','):
            got = _api(data, 'status', order=sid)
            if got.get('ok'):
                statuses.append(str((got.get('raw') or {}).get('status') or 'Unknown'))
        if statuses:
            previous = row.get('statuses')
            row['statuses'] = statuses
            if previous != statuses:
                _audit('order_status', order=oid, statuses=statuses)
        if not cfg.get('bonus_on') or row.get('bonus_sent') or row.get('bonus_skipped') or row.get('refunded') or _buyer_blocked(data, row.get('buyer', '')):
            continue
        if now - float(row.get('done_at') or row.get('ts') or now) > 14 * 86400:
            continue
        stars = _stars_of(cardinal, str(oid))
        if stars < minimum:
            if row.get('bonus_wait') and stars >= 0:
                row['bonus_wait'] = False
            continue
        ok, sid, qty = _bonus_offer(data, str(row.get('lot_id') or ''))
        if ok and (not row.get('bonus_wait')) and row.get('chat'):
            row['bonus_wait'] = True
            sends.append((row['chat'], f'🎁 Спасибо за отзыв {stars}★ по заказу #{oid}.\nБонус: {qty} шт. услуги #{sid}. Пришлите ссылку или напишите «без бонуса».'))
            _audit('bonus_offered', order=oid, stars=stars, service=sid, quantity=qty)
    return (sends, notes, changed)
def _waiting_bonus(data: dict, chat_id: str):
    for oid, row in (data.get('orders') or {}).items():
        if str(row.get('chat') or '') == str(chat_id) and row.get('bonus_wait') and (not row.get('bonus_sent')):
            return (str(oid), row)
    return ('', None)
def _place_bonus(cardinal, data: dict, chat_id: str, text: str) -> bool:
    oid, row = _waiting_bonus(data, chat_id)
    if not row:
        return False
    if _buyer_blocked(data, row.get('buyer', '')):
        return True
    word = str(text or '').strip().lower()
    en = _en(row.get('lang') or 'ru')
    if word in ('без бонуса', 'не нужен', 'skip', 'no bonus'):
        row['bonus_wait'] = False
        row['bonus_skipped'] = True
        _save(data)
        _send_fp(cardinal, chat_id, 'Bonus skipped.' if en else 'Бонус не запускаю.')
        return True
    link = _link(text)
    if not link:
        _send_fp(cardinal, chat_id, 'Send the bonus link, or «без бонуса».' if en else 'Пришлите ссылку для бонуса или напишите «без бонуса».')
        return True
    ok, sid, qty = _bonus_offer(data, str(row.get('lot_id') or ''))
    if not ok:
        row['bonus_wait'] = False
        row['bonus_sent'] = True
        _save(data)
        _notify_admins(cardinal, data, f'#{oid}: бонус, бонус не настроен.')
        _send_fp(cardinal, chat_id, 'Bonus is not set up yet.' if en else 'Бонус ещё не настроен. Продавец увидит отзыв.')
        return True
    info = _row_by_id(data, sid)
    rate = float((info or {}).get('rate') or 0)
    bal = _api(data, 'balance')
    if bal['ok'] and rate:
        have = float((bal['raw'] or {}).get('balance') or 0)
        need = rate * qty / 1000.0
        if have + 1e-06 < need:
            _log_err(data, f'#{oid} бонус не запущен: не хватает баланса')
            _notify_admins(cardinal, data, f'#{oid}: бонус, на бонус не хватает баланса.')
            _send_fp(cardinal, chat_id, 'Bonus is paused: the seller will start it.' if en else 'Бонус пока на паузе: продавец запустит его сам.')
            return True
    stars = _stars_of(cardinal, oid)
    if stars < int(data['settings'].get('bonus_stars', 5)):
        _send_fp(cardinal, chat_id, 'Не удалось подтвердить подходящий отзыв. Попробуйте позже.')
        return True
    if row.get('bonus_attempted'):
        _send_fp(cardinal, chat_id, 'Бонус уже отправлялся. Продавец проверит его результат.')
        return True
    row['bonus_attempted'] = True
    _save(data)
    got = _api(data, 'add', service=sid, link=link, quantity=qty)
    if not got['ok']:
        _log_err(data, f"#{oid} бонус не создался: {got['error']}")
        _send_fp(cardinal, chat_id, f"❌ Бонус не создался\n{got['error']}\nПришлите другую ссылку или «без бонуса».")
        return True
    site_id = str((got['raw'] or {}).get('order') or '')
    if not site_id:
        _log_err(data, f'#{oid}: бонус отправлен, но API не вернул ID; нужна ручная проверка')
        return True
    _audit('bonus_created', order=oid, site_order=site_id)
    row['bonus_wait'] = False
    row['bonus_sent'] = True
    row['bonus_order'] = site_id
    row['bonus_link'] = link
    _save(data)
    _send_fp(cardinal, chat_id, f"🎁 Бонус запущен.\n🆔 {site_id or '—'}")
    _notify_admins(cardinal, data, f"#{oid}: бонус за отзыв запущен, заказ сайта {site_id or '—'}.")
    return True
def _bind(cardinal, *args):
    _remember(cardinal)
    _migrate_storage_layout()
    _audit('plugin_started', version=VERSION)
    _save(_load())
    if not getattr(cardinal, 'telegram', None):
        return
    tg = cardinal.telegram
    bot = tg.bot
    @_serialized
    def _remember_admin(chat_id):
        data = _load()
        chats = [str(x) for x in data.get('admin_chats') or []]
        if str(chat_id) not in chats:
            chats.append(str(chat_id))
            data['admin_chats'] = chats[-5:]
            _save(data)
        return data
    @_serialized
    def _show_home(chat_id, message_id=None):
        data = _remember_admin(chat_id)
        if message_id:
            _safe_edit(bot, chat_id, message_id, _home_text(data), _home_kb())
        else:
            bot.send_message(chat_id, _home_text(data), parse_mode='HTML', reply_markup=_home_kb(), disable_web_page_preview=True)
    @_serialized
    def _show_settings(chat_id, message_id=None):
        data = _remember_admin(chat_id)
        if message_id:
            _safe_edit(bot, chat_id, message_id, _settings_text(data), _settings_kb())
        else:
            bot.send_message(chat_id, _settings_text(data), parse_mode='HTML', reply_markup=_settings_kb(), disable_web_page_preview=True)
    def open_menu(m):
        _audit('open_panel', actor=m.from_user.id, chat=m.chat.id)
        _show_home(m.chat.id)
    @_serialized
    def on_cb(c):
        data = _load()
        bit = str(c.data or '')[len(PREFIX):]
        chat = c.message.chat.id
        _audit('menu', actor=c.from_user.id, chat=chat, section=bit)
        if _navigation_callback(cardinal, c, data, bit) or _extra_callback(cardinal, c, data, bit):
            try:
                bot.answer_callback_query(c.id)
            except Exception:
                pass
            return
        if bit == 'home':
            _show_home(chat, c.message.id)
        elif bit == 'settings':
            _show_settings(chat, c.message.id)
        elif bit == 'plugin_settings':
            try:
                cardinal.telegram.clear_state(chat, c.from_user.id)
            except Exception:
                pass
            _safe_edit(bot, chat, c.message.id, _plugin_settings_text(data), _plugin_settings_kb())
        elif bit == 'lot_settings':
            try:
                cardinal.telegram.clear_state(chat, c.from_user.id)
            except Exception:
                pass
            _safe_edit(bot, chat, c.message.id, _lot_settings_text(data, 0), _lot_settings_kb(data, 0))
        elif bit.startswith('lot_settings:'):
            try:
                page = max(0, int(bit.split(':', 1)[1]))
            except Exception:
                page = 0
            _safe_edit(bot, chat, c.message.id, _lot_settings_text(data, page), _lot_settings_kb(data, page))
        elif bit == 'account_settings':
            try:
                cardinal.telegram.clear_state(chat, c.from_user.id)
            except Exception:
                pass
            _safe_edit(bot, chat, c.message.id, _account_settings_text(data), _account_settings_kb(data))
        elif bit == 'order_settings':
            _safe_edit(bot, chat, c.message.id, _order_settings_text(data), _order_settings_kb())
        elif bit == 'maintenance':
            _safe_edit(bot, chat, c.message.id, _maintenance_text(data), _maintenance_kb())
        elif bit == 'stats':
            _safe_edit(bot, chat, c.message.id, _stats_text(data), _stats_kb())
        elif bit == 'info':
            _safe_edit(bot, chat, c.message.id, _info_text(), _info_kb())
        elif bit == 'state':
            _safe_edit(bot, chat, c.message.id, _state_text(data), _state_kb(data))
        elif bit == 'state_toggle':
            cfg = data.setdefault('settings', {})
            cfg['plugin_enabled'] = not bool(cfg.get('plugin_enabled', True))
            _save(data)
            _safe_edit(bot, chat, c.message.id, _state_text(data), _state_kb(data))
        elif bit == 'update':
            _safe_edit(bot, chat, c.message.id, _update_text(), _update_kb())
        elif bit == 'update_local':
            _safe_edit(bot, chat, c.message.id, '📥 <b>Локальное обновление</b>\n\nОтправьте новый файл BlazySMM с расширением <code>.py</code>.\nФайл будет проверен до установки.', _kb([[('◀️ Назад', PREFIX + 'update')]]))
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_update_local')
        elif bit == 'update_online':
            result = _download_online_update()
            if not result.get('ok'):
                _safe_edit(bot, chat, c.message.id, f"❌ <b>Не удалось проверить обновление</b>\n\n<code>{escape(str(result.get('error') or 'неизвестная ошибка'))}</code>", _update_kb())
            elif not result.get('changed'):
                _safe_edit(bot, chat, c.message.id, f"✅ <b>Обновление не требуется</b>\n\nТекущая версия: <code>{VERSION}</code>\nВерсия по ссылке: <code>{escape(str(result.get('remote_version')))}</code>", _update_kb())
            else:
                _UPDATE_CACHE[str(chat)] = result.get('raw')
                _safe_edit(bot, chat, c.message.id, f"🆕 <b>Найдена новая версия</b>\n\nТекущая: <code>{VERSION}</code>\nНовая: <code>{escape(str(result.get('remote_version')))}</code>\n\nУстановить обновление?", _kb([[('✅ Обновить', PREFIX + 'update_yes'), ('❌ Отмена', PREFIX + 'update_no')], [('◀️ Назад', PREFIX + 'update')]]))
        elif bit == 'update_yes':
            raw = _UPDATE_CACHE.pop(str(chat), None)
            if not raw:
                _safe_edit(bot, chat, c.message.id, '❌ Файл обновления больше не находится в памяти. Проверьте обновление заново.', _update_kb())
            else:
                ok, note = _install_plugin_bytes(raw)
                text = ('✅ <b>Плагин обновлён</b>\n\n' if ok else '❌ <b>Обновление не установлено</b>\n\n') + escape(note)
                if ok:
                    text += '\n\nПерезапустите Cardinal, чтобы загрузилась новая версия.'
                _safe_edit(bot, chat, c.message.id, text, _home_kb() if ok else _update_kb())
        elif bit == 'update_no':
            _UPDATE_CACHE.pop(str(chat), None)
            _safe_edit(bot, chat, c.message.id, _update_text(), _update_kb())
        elif bit == 'delete':
            _safe_edit(bot, chat, c.message.id, _delete_text(), _delete_kb())
        elif bit == 'delete_no':
            _show_home(chat, c.message.id)
        elif bit == 'delete_yes':
            try:
                path = os.path.abspath(__file__)
                os.remove(path)
                _safe_edit(bot, chat, c.message.id, '✅ <b>Файл плагина удалён.</b>\n\nДанные BlazySMM оставлены в storage/blazysmm. Перезапустите Cardinal.', None)
            except Exception as e:
                _safe_edit(bot, chat, c.message.id, f'❌ <b>Не удалось удалить плагин</b>\n\n<code>{escape(str(e)[:300])}</code>', _delete_kb())
        elif bit == 'orders':
            rows = sorted((data.get('orders') or {}).items(), key=lambda kv: float((kv[1] or {}).get('ts') or 0))
            rows = rows[-15:]
            if not rows:
                bot.send_message(chat, 'Заказов через плагин ещё нет.', reply_markup=_stats_kb())
            else:
                lines = ['<b>Заказы с живым статусом</b>']
                for fp_id, row in reversed(rows):
                    ids = [x.strip() for x in str(row.get('site_order') or '').split(',') if x.strip()]
                    if not ids:
                        lines.append(f'FunPay #{fp_id} — ждёт ссылку')
                        continue
                    bits = []
                    for site_id in ids[:4]:
                        got = _api(data, 'status', order=site_id)
                        if not got['ok']:
                            bits.append(f"{site_id}: {got['error'][:80]}")
                        else:
                            st = str((got['raw'] or {}).get('status') or '—')
                            bits.append(f'{site_id}: {STATUS_RU.get(st, st)}')
                    lines.append(f'FunPay #{fp_id} — ' + '; '.join(bits))
                bot.send_message(chat, '\n'.join(lines)[:3900], parse_mode='HTML', reply_markup=_stats_kb())
        elif bit == 'key':
            bot.send_message(chat, '🔑 <b>API-ключ BlazySMM</b>\n\nПришлите ключ одним сообщением в формате <code>bz_...</code>. Ключ будет проверен перед сохранением.', parse_mode='HTML', reply_markup=_kb([[('❌ Отмена', PREFIX + 'account_settings')]]))
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_key')
        elif bit == 'key_delete':
            _safe_edit(bot, chat, c.message.id, '⚠️ <b>Удалить API-ключ?</b>\n\nНовые заказы BlazySMM не смогут запускаться, пока вы не подключите ключ снова. Привязки лотов и история сохранятся.', _kb([[('✅ Удалить', PREFIX + 'key_delete_yes')], [('❌ Отмена', PREFIX + 'account_settings')]]))
        elif bit == 'key_delete_yes':
            data['key'] = ''
            data['url'] = SITE
            _save(data)
            _safe_edit(bot, chat, c.message.id, _account_settings_text(data), _account_settings_kb(data))
        elif bit == 'tpls':
            cfg = data.setdefault('settings', {})
            bot.send_message(chat, f"<b>Тексты покупателю</b>\nПустое поле — стандартный текст.\nМетки: {{order_id}} {{service_name}} {{quantity}} {{link}} {{site_id}} {{error}}\n\nОплата: {('свой текст' if cfg.get('tpl_ask') else 'стандарт')}\nЗапущен: {('свой текст' if cfg.get('tpl_done') else 'стандарт')}\nОшибка: {('свой текст' if cfg.get('tpl_err') else 'стандарт')}", parse_mode='HTML', reply_markup=_kb([[('Оплата прошла', PREFIX + 'tp:ask')], [('Заказ запущен', PREFIX + 'tp:done')], [('Ошибка', PREFIX + 'tp:err')], [('Сбросить', PREFIX + 'tp:reset')], [('◀️ Назад', PREFIX + 'order_settings')]]))
        elif bit.startswith('tp:'):
            which = bit.split(':', 1)[1]
            if which == 'reset':
                for key in ('tpl_ask', 'tpl_done', 'tpl_err'):
                    data.setdefault('settings', {})[key] = ''
                _save(data)
                bot.send_message(chat, 'Тексты снова стандартные.', reply_markup=_order_settings_kb())
            else:
                data['tpl_edit'] = {'ask': 'tpl_ask', 'done': 'tpl_done', 'err': 'tpl_err'}.get(which, '')
                _save(data)
                bot.send_message(chat, 'Пришлите новый текст одним сообщением. Метки можно оставить как есть.', reply_markup=_return_kb('tpls', '❌ Отмена'))
                cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_tpl')
        elif bit.startswith('Lp:'):
            data['bind_per_lot'] = bit[3:]
            _save(data)
            bot.send_message(chat, f'Лот <code>{bit[3:]}</code>. Сколько штук услуги на сайте за 1 штуку FunPay? Пришлите число, например <code>100</code>.', parse_mode='HTML')
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_per')
        elif bit == 'dev':
            bot.send_message(chat, f'👨\u200d💻 <b>Разработчик</b>\n\nАвтор сборки: <b>{DEV}</b>\nВерсия: <code>{VERSION}</code>\nUUID: <code>{UUID}</code>\n\nНиже — мои ссылки и регистрация в BlazySMM по моей реферальной ссылке.', parse_mode='HTML', reply_markup=_kb([[('👤 Telegram', 'url:' + CREATOR_URL), ('💬 Чат', 'url:' + GROUP_URL)], [('📢 Канал', 'url:' + CHANNEL_URL)], [('🌐 Сайт сервиса', 'url:' + REFERRAL_URL)], [('◀️ Назад', PREFIX + 'home')]]))
        elif bit.startswith('bn:'):
            data.setdefault('settings', {})['bonus_on'] = bit.endswith('1')
            _save(data)
            bot.send_message(chat, 'Бонус включён.' if bit.endswith('1') else 'Бонус выключен.', reply_markup=_plugin_settings_kb())
        elif bit.startswith('bq:'):
            data.setdefault('settings', {})['bonus_qty'] = _int(bit[3:], 10) or 10
            _save(data)
            bot.send_message(chat, f"Количество бонуса: {data['settings']['bonus_qty']}.", reply_markup=_plugin_settings_kb())
        elif bit == 'bset':
            bot.send_message(chat, 'Пришлите номер услуги для бонуса, например <code>12345</code>.', parse_mode='HTML')
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_bonus')
        elif bit.startswith('Lb:'):
            data['bind_bonus_lot'] = bit[3:]
            _save(data)
            bot.send_message(chat, f'Бонус лота <code>{bit[3:]}</code>.\nПришлите номер услуги или <code>0</code>, чтобы брать общий бонус.\n<code>выкл</code> — на этом лоте бонуса нет.', parse_mode='HTML')
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_lbonus')
        elif bit == 'lot_manual':
            bot.send_message(chat, '➕ <b>Добавить LOT ID</b>\n\nОтправьте числовой LOT ID FunPay или ссылку на лот.', parse_mode='HTML', reply_markup=_kb([[('❌ Отмена', PREFIX + 'lot_settings')]]))
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_lot_manual')
        elif bit.startswith('lot_pick:'):
            lot_id = bit.split(':', 1)[1]
            try:
                _validate_funpay_lot(data, lot_id)
                data = _load()
            except Exception:
                pass
            _safe_edit(bot, chat, c.message.id, _lot_card(data, lot_id), _lot_kb(data, lot_id))
        elif bit == 'noop':
            pass
        elif bit == 'bindnew':
            data.pop('bind_lot', None)
            _save(data)
            bot.send_message(chat, 'Пришлите номер лота FunPay и номер услуги:\n<code>49482123 | 12345</code>\nЛибо дождитесь заказа: лот сам появится кнопкой.', parse_mode='HTML')
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_bind')
        elif bit == 'bind' or bit == 'lots':
            bot.send_message(chat, _lots_text(data), parse_mode='HTML', reply_markup=_lot_settings_kb(data, 0))
        elif bit.startswith('L:'):
            bot.send_message(chat, _lot_card(data, bit[2:]), parse_mode='HTML', reply_markup=_lot_kb(data, bit[2:]))
        elif bit.startswith('Lon:'):
            lid = bit[4:]
            offs = [str(x) for x in data.get('off_ids') or []]
            if lid not in offs:
                offs.append(lid)
            data['off_ids'] = offs[-80:]
            _save(data)
            bot.send_message(chat, f'Лот {lid}: бот молчит.', reply_markup=_lot_kb(data, lid))
        elif bit.startswith('Loff:'):
            lid = bit[5:]
            data['off_ids'] = [str(x) for x in data.get('off_ids') or [] if str(x) != lid]
            _save(data)
            bot.send_message(chat, f'Лот {lid}: бот снова отвечает, если услуга привязана.', reply_markup=_lot_kb(data, lid))
        elif bit.startswith('Lfp:'):
            lid = bit[4:]
            ok, msg, state = _toggle_funpay_lot_active(data, lid)
            if ok:
                status = 'включён' if state else 'выключен'
                bot.send_message(chat, f'Лот {lid}: {status}.', reply_markup=_lot_kb(data, lid))
            else:
                bot.send_message(chat, f'Не удалось изменить состояние лота {lid}: {msg}', reply_markup=_lot_kb(data, lid))
        elif bit.startswith('Lbind:'):
            lid = bit[6:]
            data['bind_lot'] = lid
            _save(data)
            bot.send_message(chat, f'Лот <code>{lid}</code>. Пришлите номер услуги с сайта, например <code>12345</code>.\nСлово в описании лота не нужно.', parse_mode='HTML')
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_bind')
        elif bit.startswith('Ldel:'):
            lid = bit[5:]
            lots = _lots(data)
            lots.pop(lid, None)
            data['lots'] = lots
            _save(data)
            bot.send_message(chat, f'Привязку лота {lid} снял.', reply_markup=_lot_settings_kb(data, 0))
        elif bit == 'off':
            items = data.get('off_lots') or []
            shown = '\n'.join((f'• {x}' for x in items[:30])) or 'пока пусто'
            bot.send_message(chat, f'Плагин молчит на лоты, в названии которых есть эта фраза.\nСейчас:\n{shown}\n\nДобавить: пришлите фразу из названия лота.\nУбрать: <code>-фраза</code>', parse_mode='HTML')
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_off')
        elif bit == 'ban':
            items = data.get('blacklist') or []
            shown = '\n'.join((f'• {x}' for x in items[:30])) or 'пока пусто'
            bot.send_message(chat, f'Этим покупателям плагин не пишет.\nСейчас:\n{shown}\n\nДобавить: ник FunPay.\nУбрать: <code>-ник</code>', parse_mode='HTML')
            cardinal.telegram.set_state(chat, c.message.id, c.from_user.id, 'bzl_ban')
        elif bit == 'clear_errs':
            data['errors'] = []
            _save(data)
            _safe_edit(bot, chat, c.message.id, _maintenance_text(data), _maintenance_kb())
        elif bit == 'errs':
            rows = list(data.get('errors') or [])[-15:]
            if not rows:
                bot.send_message(chat, 'Ошибок пока нет.', reply_markup=_maintenance_kb())
            else:
                lines = ['<b>Почему заказ не создался</b>']
                for row in reversed(rows):
                    lines.append(f"{_fmt_when(row.get('t') or 0)} — {escape(str(row.get('text') or ''))}")
                bot.send_message(chat, '\n'.join(lines)[:3900], parse_mode='HTML', reply_markup=_maintenance_kb())
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
    def _drop_state(m):
        cardinal.telegram.clear_state(m.chat.id, m.from_user.id)
    @_serialized
    def on_auto_categories(m):
        data = _load()
        raw = (m.text or '').strip()
        picked, rows = _match_service_categories(data, raw)
        if not picked or not rows:
            cats = _service_category_names(data)
            sample = ', '.join(cats[:20]) if cats else 'категории не загрузились'
            bot.send_message(m.chat.id, '❌ Не нашёл такие категории. Попробуйте ещё раз.\n\nПримеры: ' + sample, reply_markup=_kb([[('❌ Отмена', PREFIX + 'lot_settings')]]))
            return
        _drop_state(m)
        flow = {'categories': picked, 'services': rows}
        _AUTO_LOT_FLOW[str(m.chat.id)] = flow
        bot.send_message(m.chat.id, _auto_services_text(flow, 0), parse_mode='HTML', reply_markup=_auto_services_kb(flow, 0))
    @_serialized
    def on_key(m):
        data = _load()
        text = (m.text or '').strip()
        match = re.search('bz_[A-Za-z0-9_\\-]{8,}', text)
        if not match:
            bot.send_message(m.chat.id, '❌ API-ключ не найден. Пришлите ключ в формате <code>bz_...</code>.', parse_mode='HTML', reply_markup=_account_settings_kb(data))
            return
        candidate = match.group(0)
        probe = dict(data)
        probe['key'] = candidate
        probe['url'] = SITE
        got = _api(probe, 'balance')
        if not got.get('ok'):
            bot.send_message(m.chat.id, f"❌ <b>Ключ не прошёл проверку</b>\n\n<code>{escape(str(got.get('error') or 'API недоступен')[:300])}</code>", parse_mode='HTML', reply_markup=_account_settings_kb(data))
            return
        data['key'] = candidate
        data['url'] = SITE
        _save(data)
        _drop_state(m)
        raw = got.get('raw') or {}
        bot.send_message(m.chat.id, f"✅ <b>API-ключ подключён.</b>\n\n💰 Баланс: <b>{escape(str(raw.get('balance')))} {escape(str(raw.get('currency') or 'USD'))}</b>", parse_mode='HTML', reply_markup=_account_settings_kb(data))
    def _toggle_list(data: dict, field: str, text: str) -> str:
        raw = text.strip()
        items = [str(x) for x in data.get(field) or []]
        if raw.startswith('-'):
            token = raw[1:].strip().lower()
            items = [x for x in items if x.lower() != token]
            data[field] = items
            return 'Убрал.'
        if not raw:
            return 'Пусто, не сохранил.'
        if raw.lower() not in [x.lower() for x in items]:
            items.append(raw[:80])
        data[field] = items[-50:]
        return 'Добавил.'
    @_serialized
    def on_lot_manual(m):
        _drop_state(m)
        data = _load()
        text = (m.text or '').strip()
        match = re.search('(?:offer=|id=)?(\\d{4,})', text)
        if not match:
            bot.send_message(m.chat.id, '❌ Не нашёл LOT ID. Пришлите число или ссылку на лот.', reply_markup=_lot_settings_kb(data, 0))
            return
        lot_id = match.group(1)
        try:
            item = _validate_funpay_lot(data, lot_id)
        except Exception as e:
            bot.send_message(m.chat.id, f'❌ Лот {lot_id} не найден: {str(e)[:180]}', reply_markup=_lot_settings_kb(data, 0))
            return
        data = _load()
        bot.send_message(m.chat.id, f"✅ Добавлен лот <b>{escape(str(item.get('title') or lot_id))}</b> · <code>{lot_id}</code>", parse_mode='HTML', reply_markup=_lot_kb(data, lot_id))
    @_serialized
    def on_bind(m):
        _drop_state(m)
        data = _load()
        text = (m.text or '').strip()
        if text.startswith('-'):
            token = text[1:].strip()
            lots = _lots(data)
            lots.pop(token, None)
            lots.pop(token.lower(), None)
            data['lots'] = lots
            _save(data)
            bot.send_message(m.chat.id, 'Привязку снял.', reply_markup=_lot_settings_kb(data, 0))
            return
        only_sid = re.fullmatch('\\d{2,}', text)
        if only_sid and data.get('bind_lot'):
            lid = str(data.get('bind_lot'))
            seen = next((s for s in data.get('seen') or [] if str(s.get('id') or '') == lid), {})
            lots = _lots(data)
            lots[lid] = {'sid': text, 'title': seen.get('title') or lid, 'by': 'id'}
            data['lots'] = lots
            data.pop('bind_lot', None)
            _save(data)
            bot.send_message(m.chat.id, f'Лот {lid} → услуга {text}. Описание менять не нужно.', reply_markup=_lot_kb(data, lid))
            return
        sid = ''
        title = ''
        if '|' in text:
            title, sid = [x.strip() for x in text.split('|', 1)]
        else:
            found = re.search('(\\d{2,})\\s*$', text)
            if found:
                sid = found.group(1)
                title = text[:found.start()].strip(' |-')
        if not title or not re.fullmatch('\\d{2,}', sid):
            bot.send_message(m.chat.id, 'Нужны номер лота и номер услуги. Пример: 49482123 | 12345', reply_markup=_menu_kb())
            return
        lots = _lots(data)
        if title.isdigit():
            lots[title] = {'sid': sid, 'title': title, 'by': 'id'}
            note = f'Лот {title} → услуга {sid}. Слово в описании не нужно.'
        else:
            lots[title.lower()] = {'sid': sid, 'title': title, 'by': 'name'}
            note = f'Лот «{title}» → услуга {sid}. Сработает и по номеру, когда заказ придёт.'
        data['lots'] = lots
        data.pop('bind_lot', None)
        _save(data)
        bot.send_message(m.chat.id, note, reply_markup=_lot_settings_kb(data, 0))
    @_serialized
    def on_off(m):
        _drop_state(m)
        data = _load()
        note = _toggle_list(data, 'off_lots', m.text or '')
        _save(data)
        bot.send_message(m.chat.id, note, reply_markup=_lot_settings_kb(data))
    @_serialized
    def on_ban(m):
        _drop_state(m)
        data = _load()
        note = _toggle_list(data, 'blacklist', m.text or '')
        _save(data)
        bot.send_message(m.chat.id, note, reply_markup=_plugin_settings_kb())
    @_serialized
    def on_bonus(m):
        _drop_state(m)
        data = _load()
        sid = re.search('\\d{2,}', m.text or '')
        if not sid:
            bot.send_message(m.chat.id, 'Нужен номер услуги.', reply_markup=_menu_kb())
            return
        data.setdefault('settings', {})['bonus_sid'] = sid.group(0)
        _save(data)
        bot.send_message(m.chat.id, f'Бонус за отзыв → услуга {sid.group(0)}.', reply_markup=_plugin_settings_kb())
    @_serialized
    def on_lbonus(m):
        _drop_state(m)
        data = _load()
        lid = str(data.get('bind_bonus_lot') or '')
        text = (m.text or '').strip().lower()
        lots = _lots(data)
        row = lots.get(lid) or {'sid': '', 'title': lid, 'by': 'id'}
        if text == 'выкл':
            row['bonus_off'] = True
        elif text == '0':
            row.pop('bonus_sid', None)
            row.pop('bonus_off', None)
        else:
            sid = re.search('\\d{2,}', text)
            if not sid or not lid:
                bot.send_message(m.chat.id, 'Нужен номер услуги.', reply_markup=_menu_kb())
                return
            row['bonus_sid'] = sid.group(0)
            row.pop('bonus_off', None)
        if lid:
            lots[lid] = row
            data['lots'] = lots
        data.pop('bind_bonus_lot', None)
        _save(data)
        bot.send_message(m.chat.id, 'Бонус лота сохранил.', reply_markup=_lot_kb(data, lid) if lid else _menu_kb())
    @_serialized
    def on_per(m):
        _drop_state(m)
        data = _load()
        lid = str(data.get('bind_per_lot') or '')
        per = _int(re.search('\\d+', m.text or '').group(0) if re.search('\\d+', m.text or '') else 0, 0)
        if not lid or per <= 0:
            bot.send_message(m.chat.id, 'Нужно число больше нуля.', reply_markup=_menu_kb())
            return
        lots = _lots(data)
        row = lots.get(lid) or {'sid': '', 'title': lid, 'by': 'id'}
        row['per'] = per
        lots[lid] = row
        data['lots'] = lots
        data.pop('bind_per_lot', None)
        _save(data)
        bot.send_message(m.chat.id, f'1 штука FunPay = {per} шт. на сайте.', reply_markup=_lot_kb(data, lid))
    @_serialized
    def on_tpl(m):
        _drop_state(m)
        data = _load()
        key = str(data.get('tpl_edit') or '')
        text = (m.text or '').strip()
        if key not in ('tpl_ask', 'tpl_done', 'tpl_err') or not text:
            bot.send_message(m.chat.id, 'Текст пустой, не сохранил.', reply_markup=_return_kb('tpls'))
            return
        data.setdefault('settings', {})[key] = text[:1500]
        data.pop('tpl_edit', None)
        _save(data)
        bot.send_message(m.chat.id, 'Текст сохранил. Новые заказы пойдут с ним.', reply_markup=_return_kb('tpls'))
    def on_update_file(m):
        data_state = str(getattr(getattr(m, 'document', None), 'file_name', '') or '')
        if not data_state.lower().endswith('.py'):
            bot.send_message(m.chat.id, '❌ Нужен файл с расширением .py', reply_markup=_update_kb())
            return
        try:
            info = bot.get_file(m.document.file_id)
            raw = bot.download_file(info.file_path)
        except Exception as e:
            _drop_state(m)
            bot.send_message(m.chat.id, f'❌ Не удалось скачать файл: {escape(str(e)[:250])}', parse_mode='HTML', reply_markup=_update_kb())
            return
        ok, err, remote_version = _validate_plugin_bytes(raw)
        if not ok:
            _drop_state(m)
            bot.send_message(m.chat.id, f'❌ <b>Файл не прошёл проверку</b>\n\n<code>{escape(err)}</code>', parse_mode='HTML', reply_markup=_update_kb())
            return
        _drop_state(m)
        _UPDATE_CACHE[str(m.chat.id)] = raw
        bot.send_message(m.chat.id, f'📥 <b>Файл проверен</b>\n\nВерсия в файле: <code>{escape(remote_version)}</code>\nТекущая версия: <code>{VERSION}</code>\n\nУстановить его?', parse_mode='HTML', reply_markup=_kb([[('✅ Установить', PREFIX + 'update_yes'), ('❌ Отмена', PREFIX + 'update_no')]]))
    tg.msg_handler(lambda m: _wizard_message(cardinal, m), func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_wizard'))
    tg.msg_handler(lambda m: _import_config(cardinal, m), func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_config_import'), content_types=['document'])
    tg.msg_handler(open_menu, commands=['blazy_smm'])
    tg.msg_handler(on_update_file, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_update_local'), content_types=['document'])
    tg.msg_handler(on_key, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_key'))
    tg.msg_handler(on_auto_categories, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_auto_categories'))
    tg.msg_handler(on_bonus, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_bonus'))
    tg.msg_handler(on_lbonus, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_lbonus'))
    tg.msg_handler(on_per, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_per'))
    tg.msg_handler(on_tpl, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_tpl'))
    tg.msg_handler(on_lot_manual, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_lot_manual'))
    tg.msg_handler(on_bind, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_bind'))
    tg.msg_handler(on_off, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_off'))
    tg.msg_handler(on_ban, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, 'bzl_ban'))
    tg.cbq_handler(on_cb, lambda c: str(c.data or '').startswith(PREFIX) and str(c.data or '') != f'{UUID}:0')
    try:
        cardinal.add_telegram_commands(UUID, [('blazy_smm', 'Открыть панель BlazySMM', True)])
    except Exception:
        pass
    if CBT:
        def _plugin_entry(data):
            data = str(data or '')
            settings = getattr(CBT, 'PLUGIN_SETTINGS', None)
            edit = getattr(CBT, 'EDIT_PLUGIN', None)
            if data == f'{UUID}:0':
                return True
            if settings is not None and data.startswith(f'{settings}:{UUID}'):
                return True
            if edit is not None and data.startswith(f'{edit}:{UUID}'):
                return True
            return False
        def _open_plugin_home(call):
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            chat_id = getattr(getattr(getattr(call, 'message', None), 'chat', None), 'id', None)
            message_id = getattr(getattr(call, 'message', None), 'message_id', None)
            if chat_id is not None:
                _show_home(chat_id, message_id)
        try:
            tg.cbq_handler(_open_plugin_home, lambda call: _plugin_entry(getattr(call, 'data', None)))
        except Exception:
            pass
BIND_TO_PRE_INIT = [_bind]
BIND_TO_NEW_ORDER = [_on_new_order]
BIND_TO_NEW_MESSAGE = [_on_message]