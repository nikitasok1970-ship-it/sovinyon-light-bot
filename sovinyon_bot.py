# sovinyon_bot.py
import requests
from bs4 import BeautifulSoup
import time
import json
import logging
from datetime import datetime, timedelta
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import os

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
ADMIN_ID = 205371760
CHECK_INTERVAL_SECONDS = 30
STATE_FILE = 'dtek_state.json'
HISTORY_FILE = 'dtek_history.json'
BASE_URL = 'https://www.dtek-oem.com.ua/ua/shutdowns'

# Адреса и группы
MONITORED_ADDRESSES = ["Совіньйон", "Совіньйон 1", "Ольгіївська"]
ADDRESS_GROUPS = {"Совіньйон": "4.2", "Совіньйон 1": "4.2", "Ольгіївська": "4.2"}

# Логи
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
bot = Bot(token=BOT_TOKEN)

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def parse_time(time_str):
    try:
        return datetime.strptime(time_str.strip(), "%H:%M").time()
    except:
        return None

def time_diff(start, end):
    delta = end - start
    total_sec = int(delta.total_seconds())
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h:02d}г {m:02d}хв {s:02d}с"

def load_json(file):
    try:
        with open(file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_json(file, data):
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def parse_dtek():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(BASE_URL, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table')
        if not table:
            return []
        rows = []
        for row in table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 7:
                address = cols[2].text.strip()
                if any(mon in address for mon in MONITORED_ADDRESSES):
                    rows.append({
                        'address': address,
                        'type': cols[3].text.strip(),
                        'start': cols[4].text.strip(),
                        'end': cols[5].text.strip(),
                        'status': cols[6].text.strip(),
                        'updated': datetime.now().strftime("%H:%M:%S %d.%m.%Y")
                    })
        return rows
    except Exception as e:
        logger.error(f"Парсинг: {e}")
        return []

def create_daily_graph(addr, history):
    if addr not in history or not history[addr]['events']:
        return None
    events = history[addr]['events']
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_title(f"Совіньйон: відключення (група 4.2)")
    now = datetime.now()
    recent = [e for e in events if (now - datetime.strptime(e['time'], "%H:%M:%S %d.%m.%Y")) <= timedelta(hours=24)]
    if not recent:
        plt.close()
        return None
    times = [datetime.strptime(e['time'], "%H:%M:%S %d.%m.%Y") for e in recent]
    status = [1 if 'off' in e else 0 for e in recent]
    ax.plot(times, status, 'o-', color='red', linewidth=2, markersize=4)
    ax.fill_between(times, status, alpha=0.3, color='red')
    ax.set_ylim(-0.1, 1.1)
    ax.set_ylabel('Статус')
    ax.set_yticks([0, 1])
    ax.set_yticklabels(['Світло є', 'Відключено'])
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    plt.xticks(rotation=45)
    plt.tight_layout()
    img = BytesIO()
    plt.savefig(img, format='png', dpi=100, bbox_inches='tight')
    img.seek(0)
    plt.close()
    return img

# === ОТПРАВКА ===
def send_notification(text):
    try:
        bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode='HTML')
        logger.info("Повідомлення в канал")
    except Exception as e:
        logger.error(f"Помилка: {e}")

def send_photo_with_caption(caption, photo):
    try:
        bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=caption, parse_mode='HTML')
        logger.info("Графік відправлено")
    except Exception as e:
        logger.error(f"Помилка графіка: {e}")

# === МОНИТОРИНГ ===
def monitor_dtek():
    current_data = parse_dtek()
    if not current_data:
        send_notification("ТЕСТ: Бот працює!\nСовіньйон (4.2)\nКожні 30 сек")
        return

    prev_state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)
    now = datetime.now()

    for item in current_data:
        addr = item['address']
        status = item['status']
        start_str = item['start']
        end_str = item['end']
        group = ADDRESS_GROUPS.get(addr.split(',')[0].strip(), "невідома")

        if addr not in history:
            history[addr] = {'events': []}
        events = history[addr]['events']

        is_off_now = "Активне" in status or "відключено" in status.lower()
        current_time = now.strftime("%H:%M:%S %d.%m.%Y")

        message_parts = [f"<b>Світло {addr}</b>", f"<i>Група: {group} (ДТЕК)</i>"]

        # История
        last_off = next((e for e in reversed(events) if 'off' in e), None)
        last_on = next((e for e in reversed(events) if 'on' in e), None)

        if last_off and is_off_now:
            off_start = datetime.strptime(last_off['off'], "%H:%M:%S")
            duration = time_diff(off_start, now)
            message_parts.append(f"\n<b>Світла немає!</b>\nВідсутнє: {duration}\nВимкнено о {start_str}")

        if last_on and not is_off_now:
            on_start = datetime.strptime(last_on['on'], "%H:%M:%S")
            duration = time_diff(on_start, now)
            message_parts.append(f"\n<b>Світло є!</b>\nУвімкнено о {end_str}\nБуло: {duration}")

        message_parts.append(f"\n<i>ДТЕК:</i> {item['type']}\nОновлено: {item['updated']}\nВимкн.: {start_str} | Увімкн.: {end_str}")
        full_message = "\n".join(message_parts)

        prev_msg = prev_state.get(addr, "")
        if full_message != prev_msg:
            graph = create_daily_graph(addr, history)
            if graph:
                send_photo_with_caption(f"{full_message}\n\nГрафік за добу", graph)
            else:
                send_notification(full_message)
            prev_state[addr] = full_message

        # Запись события
        if is_off_now and (not events or events[-1].get('off') != start_str + ":00"):
            events.append({'off': start_str + ":00", 'time': current_time})
        elif not is_off_now and (not events or events[-1].get('on') != end_str + ":00"):
            events.append({'on': end_str + ":00", 'time': current_time})

    save_json(STATE_FILE, prev_state)
    save_json(HISTORY_FILE, history)

# === КОМАНДЫ ===
async def start(update, context):
    keyboard = [[InlineKeyboardButton("Перевірити зараз", callback_data='check_now')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        'Совіньйон — світло (група 4.2)\nОновлення: кожні 30 секунд',
        reply_markup=reply_markup
    )

async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == 'check_now':
        await query.edit_message_text('Перевіряю...')
        monitor_dtek()
        await query.edit_message_text('Готово! Див. канал')

# === ЗАПУСК ===
async def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN або CHANNEL_ID не задано!")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("БОТ ЗАПУЩЕНО. Моніторинг кожні 30 секунд...")
    monitor_dtek()  # Первая проверка

    # Запуск polling
    await application.run_polling()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())



