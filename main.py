import os
import time
import json
import logging
import threading
import requests
from flask import Flask, request, abort
import telebot
from telebot import types
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ============================================================
# --- ВАШ TELEGRAM ID ---
# ============================================================
MY_TELEGRAM_ID = 545995986        # <-- Ваш основной ID
# ============================================================

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8894348395:AAFrvb4tFvqM129UcaxrIE_j8icV93sGgmM"
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://kufar-bot-2.onrender.com")

# ============================================================
# --- КАТЕГОРИЯ АВТОМОБИЛЕЙ ---
# ============================================================
CAR_CATEGORY = "2010"  # Категория "Автомобили"

MAX_PRICE_BYN = 17050    # Максимальная цена
MIN_PRICE_BYN = 100      # Минимальная цена (отсеиваем мусор)

CHECK_INTERVAL = 300       # 5 минут
USERS_FILE = "subscribers.json"
SEEN_ADS_FILE = "seen_ads.json"
START_TIME_FILE = "start_time.json"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# --- 1. ХРАНИЛИЩЕ ДАННЫХ ---
def load_data(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка чтения {filename}: {e}")
    return default

def save_data(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения {filename}: {e}")

def get_all_subscribers():
    all_subs = {MY_TELEGRAM_ID}
    all_subs.update(set(load_data(USERS_FILE, [])))
    return all_subs

def get_start_time():
    data = load_data(START_TIME_FILE, {"time": 0})
    return data.get("time", 0)

def set_start_time():
    save_data(START_TIME_FILE, {"time": int(time.time())})
    logging.info(f"Время /start обновлено: {int(time.time())}")

# --- 2. ПАРСИНГ KUFAR (АВТО) ---
def fetch_kufar_cars():
    url = "https://cre-api.kufar.by/ads-search/v1/engine/v1/search/rendered-paginated"
    params = {
        "cat": CAR_CATEGORY,
        "lang": "ru",
        "size": "50",
        "sort": "lst.d",
        "rgn": "1",
        "prc": f"r:{MIN_PRICE_BYN*100},{MAX_PRICE_BYN*100}"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    found_ads = []
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logging.warning(f"Ошибка Kufar status={response.status_code}")
            return found_ads

        data = response.json()
        ads = data.get("ads", [])
        logging.info(f"Найдено {len(ads)} объявлений об авто")

        for ad in ads:
            ad_id = str(ad.get("ad_id"))
            
            if ad.get("company_ad", False):
                continue

            title = ad.get("subject", "Автомобиль")
            params_list = ad.get("ad_parameters", [])
            all_text = title.lower()
            for p in params_list:
                all_text += " " + str(p.get("pl", "")).lower()
                all_text += " " + str(p.get("vl", "")).lower()

            if "битый" in all_text or "на запчасти" in all_text or "не на ходу" in all_text or "разбор" in all_text:
                continue

            price_byn = ad.get("price_byn", "0")
            try:
                price_int = int(price_byn) // 100
                if price_int < MIN_PRICE_BYN or price_int > MAX_PRICE_BYN:
                    continue
                price = f"{price_int} BYN"
            except:
                price = "Цена не указана"

            ad_link = ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}")
            
            region = ""
            for p in params_list:
                if p.get("pl") == "Регион":
                    region = p.get("vl", "")
                    break
            if not region:
                region = "Беларусь"
            
            list_time_str = ad.get("list_time", "")
            list_time_ts = 0
            try:
                dt = datetime.strptime(list_time_str, "%Y-%m-%dT%H:%M:%SZ")
                list_time_ts = int(dt.timestamp())
            except Exception as e:
                logging.warning(f"Не удалось распарсить list_time '{list_time_str}': {e}")

            found_ads.append({
                "id": ad_id,
                "title": title,
                "price": price,
                "link": ad_link,
                "region": region,
                "list_time": list_time_ts
            })
    except Exception as e:
        logging.error(f"Ошибка при парсинге Куфара (авто): {e}")

    return found_ads

# --- 3. ФОНОВОЕ СКАНИРОВАНИЕ ---
def check_kufar_loop():
    logging.info("Сканер автомобилей запущен...")
    seen_ads = set(load_data(SEEN_ADS_FILE, []))
    
    if len(seen_ads) == 0:
        logging.info("Первый запуск: запоминаем все текущие объявления об авто...")
        ads = fetch_kufar_cars()
        for ad in ads:
            seen_ads.add(ad["id"])
        save_data(SEEN_ADS_FILE, list(seen_ads))
        logging.info(f"Запомнено {len(seen_ads)} объявлений при первом запуске")

    while True:
        subscribers = get_all_subscribers()
        start_time = get_start_time()
        logging.info(f"=== ЦИКЛ: Подписчиков: {len(subscribers)}. Увиденных: {len(seen_ads)} ===")

        try:
            total_sent = 0
            ads = fetch_kufar_cars()
            for ad in ads:
                ad_id = ad["id"]
                ad_time = ad.get("list_time", 0)
                
                if ad_time <= start_time:
                    continue
                
                if ad_id in seen_ads:
                    continue
                
                seen_ads.add(ad_id)
                save_data(SEEN_ADS_FILE, list(seen_ads))
                logging.info(f"НОВОЕ АВТО: {ad['title']} | {ad['price']} | {ad['region']}")
                
                if subscribers:
                    message_text = (
                        f"🚗 Новое объявление о продаже авто\n\n"
                        f"📌 {ad['title']}\n"
                        f"💰 Цена: {ad['price']}\n"
                        f"📍 Регион: {ad['region']}\n"
                        f"👤 Продавец: Частное лицо\n\n"
                        f"🔗 Открыть на Kufar: {ad['link']}"
                    )
                    
                    for user_id in list(subscribers):
                        try:
                            bot.send_message(user_id, message_text)
                            logging.info(f"-> ✅ УСПЕШНО отправлено {user_id}")
                            total_sent += 1
                        except Exception as err:
                            logging.error(f"-> ❌ ОШИБКА отправки {user_id}: {err}")
            
            logging.info(f"=== ЦИКЛ ЗАВЕРШЕН. Отправлено новых: {total_sent} ===")
        except Exception as e:
            logging.error(f"Ошибка сканера: {e}")
        time.sleep(CHECK_INTERVAL)

# --- 4. КОМАНДЫ БОТА ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    set_start_time()
    
    subs = set(load_data(USERS_FILE, []))
    subs.add(user_id)
    save_data(USERS_FILE, list(subs))
    logging.info(f"Новый подписчик: {user_id}. Всего в файле: {len(subs)}")
    
    text = (
        f"👋 Здравствуйте, {message.from_user.first_name}!\n\n"
        f"Вы подписались на уведомления о продаже автомобилей по всей Беларуси.\n\n"
        f"⚠️ ВАЖНО: Вы будете получать только те объявления, которые появятся ПОСЛЕ этой команды.\n\n"
        f"🚗 Категория: Автомобили\n"
        f"💰 Цена: от {MIN_PRICE_BYN} до {MAX_PRICE_BYN} BYN\n"
        f"📍 Регион: вся Беларусь\n"
        f"👤 Только частные лица\n"
        f"🚫 Исключаем: битые, на запчасти, не на ходу\n\n"
        f"Бот проверяет Kufar каждые 5 минут!"
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=['stop'])
def stop_subscription(message):
    user_id = message.chat.id
    subs = set(load_data(USERS_FILE, []))
    if user_id in subs:
        subs.remove(user_id)
        save_data(USERS_FILE, list(subs))
        bot.reply_to(message, "❌ Вы отписались от уведомлений.")
    else:
        bot.reply_to(message, "Вы не были подписаны.")

@bot.message_handler(commands=['status'])
def status_info(message):
    subs = get_all_subscribers()
    seen = set(load_data(SEEN_ADS_FILE, []))
    start_time = get_start_time()
    start_time_str = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S") if start_time else "не задано"
    text = (
        f"📊 Статус бота:\n"
        f"🚗 Категория: Автомобили\n"
        f"📍 Регион: вся Беларусь\n"
        f"👥 Подписчиков: {len(subs)}\n"
        f"📦 Увиденных объявлений: {len(seen)}\n"
        f"💰 Цена: {MIN_PRICE_BYN} - {MAX_PRICE_BYN} BYN\n"
        f"⏱ Проверка каждые: {CHECK_INTERVAL} сек.\n"
        f"🕐 Время /start: {start_time_str}"
    )
    bot.reply_to(message, text)

# --- 5. ВЕБХУК ---
@app.route(f"/{BOT_TOKEN}", methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/')
def index():
    return "Telegram Bot is active!", 200

# --- 6. ЗАПУСК ---
if __name__ == "__main__":
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}")
        logging.info(f"Вебхук установлен: {RENDER_EXTERNAL_URL}/{BOT_TOKEN}")
    except Exception as e:
        logging.error(f"Ошибка установки вебхука: {e}")

    threading.Thread(target=check_kufar_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Flask сервер запущен на порту {port}")
    app.run(host="0.0.0.0", port=port)
