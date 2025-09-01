#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import logging
import requests
import schedule
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal, ROUND_DOWN
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Импорты для Bitget SDK
import bitget.bitget_api as baseApi
import bitget.v1.mix.order_api as orderApi
import bitget.v1.mix.account_api as accountApi
import bitget.v1.mix.market_api as marketApi
from bitget.exceptions import BitgetAPIException

LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "gpt-4o-mini.gguf")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        # logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BitgetTradingBot:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, lm_studio_url: str):
        """
        Инициализация торгового бота
        
        Args:
            api_key: API ключ Bitget
            secret_key: Секретный ключ Bitget
            passphrase: Пароль для API Bitget
            lm_studio_url: URL для LM Studio API
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.lm_studio_url = lm_studio_url
        
        # Инициализация API клиентов
        self.base_api = baseApi.BitgetApi(api_key, secret_key, passphrase)
        self.order_api = orderApi.OrderApi(api_key, secret_key, passphrase)
        self.account_api = accountApi.AccountApi(api_key, secret_key, passphrase)
        self.market_api = marketApi.MarketApi(api_key, secret_key, passphrase)
        
        # Настройки торговли из переменных окружения
        # self.symbol = "BTCUSDT_UMCBL"
        self.symbol = "ETHUSDT_UMCBL"
        self.margin_coin = "USDT"
        self.max_position_percent = float(os.getenv('MAX_POSITION_PERCENT', '5.0'))
        self.max_risk_percent = float(os.getenv('MAX_RISK_PERCENT', '30.0'))
        self.stop_loss_percent = float(os.getenv('STOP_LOSS_PERCENT', '2.0'))
        self.take_profit_percent = float(os.getenv('TAKE_PROFIT_PERCENT', '6.0'))
        self.confidence_threshold = int(os.getenv('CONFIDENCE_THRESHOLD', '6'))
        self.check_interval = int(os.getenv('CHECK_INTERVAL', '15'))
        
        # Дополнительные настройки безопасности
        self.min_balance = float(os.getenv('MIN_BALANCE', '10.0'))  # Минимальный баланс для торговли
        self.max_position_value = float(os.getenv('MAX_POSITION_VALUE', '1000.0'))  # Максимальная сумма позиции
        
        logger.info(f"Торговый бот инициализирован с настройками:")
        logger.info(f"- Максимальный размер позиции: {self.max_position_percent}%")
        logger.info(f"- Стоп-лосс: {self.stop_loss_percent}%")
        logger.info(f"- Тейк-профит: {self.take_profit_percent}%")
        logger.info(f"- Порог уверенности ИИ: {self.confidence_threshold}/10")
        logger.info(f"- Интервал проверки: {self.check_interval} минут")

    def get_account_balance(self) -> float:
        """Получение баланса аккаунта в USDT"""
        try:
            params = {"productType": "umcbl"}
            response = self.account_api.accounts(params)
            
            if response.get('code') == '00000' and response.get('data'):
                for account in response['data']:
                    if account.get('marginCoin') == self.margin_coin:
                        available = float(account.get('available', 0))
                        logger.info(f"Доступный баланс: {available} USDT")
                        return available
            
            logger.warning("Не удалось получить баланс аккаунта")
            return 0.0
            
        except BitgetAPIException as e:
            logger.error(f"Ошибка при получении баланса: {e.message}")
            return 0.0

    def get_current_positions(self) -> List[Dict]:
        """Получение текущих позиций"""
        try:
            params = {"productType": "umcbl", "marginCoin": self.margin_coin}
            response = self.account_api.allPosition(params)
            
            if response.get('code') == '00000':
                positions = response.get('data', [])
                active_positions = [pos for pos in positions if float(pos.get('total', 0)) != 0]
                logger.info(f"Активных позиций: {len(active_positions)}")
                return active_positions
            
            return []
            
        except BitgetAPIException as e:
            logger.error(f"Ошибка при получении позиций: {e.message}")
            return []

    def get_market_data(self) -> Dict:
        """Получение рыночных данных для анализа"""
        try:
            # Получаем текущую цену
            ticker_params = {"symbol": self.symbol}
            ticker_response = self.market_api.ticker(ticker_params)
            
            # Получаем исторические данные (свечи за последние 24 часа)

            # Получаем текущее время в миллисекундах
            timestamp_current_ms = int(time.time() * 1000)
            timestamp_24h_ago = timestamp_current_ms - 86400000
            kline_params = {
                "symbol": self.symbol,
                "granularity": "15m",  # 15-минутные свечи
                "startTime": timestamp_24h_ago,
                "endTime": timestamp_current_ms,
                "limit": "96"  # 24 часа * 4 свечи в час
            }
            kline_response = self.market_api.candles(kline_params)

            market_data = {
                "current_price": 0,
                "volume_24h": 0,
                "price_change_24h": 0,
                "klines": []
            }

            if ticker_response.get('code') == '00000' and ticker_response.get('data'):
                ticker_data = ticker_response['data']
                market_data["current_price"] = float(ticker_data.get('last', 0))
                market_data["volume_24h"] = float(ticker_data.get('baseVolume', 0))
                market_data["price_change_24h"] = float(ticker_data.get('chgUtc', 0))
            
            if kline_response:
                market_data["klines"] = kline_response
            
            logger.info(f"Получены рыночные данные. Цена: {market_data['current_price']}")
            return market_data
            
        except BitgetAPIException as e:
            logger.error(f"Ошибка при получении рыночных данных: {e.message}")
            return {}

    def analyze_with_ai(self, market_data: Dict) -> Dict:
        """Анализ рынка с помощью локального ИИ"""
        try:
            # Формируем промпт для ИИ
            prompt = self._create_analysis_prompt(market_data)
            
            # Отправляем запрос к LM Studio
            headers = {"Content-Type": "application/json"}
            payload = {
                "model": LM_STUDIO_MODEL,
                "messages": [
                    {"role": "system", "content": "Вы опытный трейдер-аналитик криптовалют. Анализируете данные и даете четкие рекомендации."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 500
            }
            
            response = requests.post(
                f"{self.lm_studio_url}/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                ai_response = response.json()
                analysis = ai_response['choices'][0]['message']['content']
                
                # Парсим рекомендации ИИ
                decision = self._parse_ai_response(analysis)
                logger.info(f"ИИ рекомендация: {decision}")
                return decision
            else:
                logger.error(f"Ошибка API LM Studio: {response.status_code}")
                return {"action": "hold", "confidence": 0}
                
        except Exception as e:
            logger.error(f"Ошибка при обращении к ИИ: {str(e)}")
            return {"action": "hold", "confidence": 0}

    def _create_analysis_prompt(self, market_data: Dict) -> str:
        """Создание промпта для анализа ИИ"""
        klines_text = ""
        if market_data.get("klines"):
            recent_klines = market_data["klines"][-20:]  # Последние 20 свечей
            klines_text = "Последние 20 свечей (время, открытие, максимум, минимум, закрытие, объем):\n"
            for kline in recent_klines:
                klines_text += f"{kline[0]}, {kline[1]}, {kline[2]}, {kline[3]}, {kline[4]}, {kline[5]}\n"
        
        prompt = f"""Проанализируйте рыночные данные для ETHUSDT:

Текущая цена: {market_data.get('current_price', 0)}
Изменение за 24ч: {market_data.get('price_change_24h', 0)}%
Объем за 24ч: {market_data.get('volume_24h', 0)}

{klines_text}

На основе технического анализа дайте рекомендацию в следующем формате:
ACTION: [BUY/SELL/HOLD]
CONFIDENCE: [1-10]
REASON: [краткое обоснование]

Учитывайте:
- Текущий тренд
- Уровни поддержки и сопротивления
- Объемы торгов
- Технические индикаторы
"""
        return prompt

    def _parse_ai_response(self, response: str) -> Dict:
        """Парсинг ответа ИИ"""
        decision = {"action": "hold", "confidence": 0, "reason": ""}
        
        lines = response.upper().split('\n')
        for line in lines:
            if 'ACTION:' in line:
                action_part = line.split('ACTION:')[1].strip()
                if 'BUY' in action_part:
                    decision["action"] = "buy"
                elif 'SELL' in action_part:
                    decision["action"] = "sell"
                else:
                    decision["action"] = "hold"
            
            elif 'CONFIDENCE:' in line:
                try:
                    conf_part = line.split('CONFIDENCE:')[1].strip()
                    confidence = int(''.join(filter(str.isdigit, conf_part)))
                    decision["confidence"] = min(max(confidence, 0), 10)
                except:
                    decision["confidence"] = 0
            
            elif 'REASON:' in line:
                decision["reason"] = line.split('REASON:')[1].strip()
        
        return decision

    def calculate_position_size(self, balance: float, current_price: float) -> float:
        """Расчет размера позиции с учетом риск-менеджмента"""
        # Максимальная сумма для позиции (% от баланса)
        max_position_value = balance * (self.max_position_percent / 100)
        
        # Дополнительное ограничение по абсолютной сумме
        max_position_value = min(max_position_value, self.max_position_value)
        
        # Размер позиции в BTC
        position_size = max_position_value / current_price
        
        # Округляем до допустимого количества знаков (минимальный лот на Bitget обычно 0.001)
        position_size = float(Decimal(str(position_size)).quantize(Decimal('0.001'), rounding=ROUND_DOWN))
        
        # Дополнительная проверка минимального размера
        min_position_size = 10.0 / current_price  # Минимум $10
        if position_size < min_position_size:
            logger.warning(f"Размер позиции слишком мал: {position_size}, минимум: {min_position_size}")
            return 0.0
        
        logger.info(f"Расчетный размер позиции: {position_size} BTC (${max_position_value:.2f})")
        return position_size

    def place_order_with_stops(self, side: str, size: float, current_price: float) -> bool:
        """Размещение ордера с автоматическими стопами"""
        try:
            # Параметры основного ордера
            order_params = {
                "symbol": self.symbol,
                "marginCoin": self.margin_coin,
                "side": f"open_{side}",
                "orderType": "market",
                "size": str(size),
                "timeInForceValue": "normal"
            }
            
            # Размещаем основной ордер
            response = self.order_api.placeOrder(order_params)
            
            if response.get('code') != '00000':
                logger.error(f"Ошибка размещения ордера: {response}")
                return False
            
            order_id = response['data']['orderId']
            logger.info(f"Ордер размещен: {order_id}, сторона: {side}, размер: {size}")
            
            # Ждем исполнения ордера
            time.sleep(2)
            
            # Устанавливаем стоп-лосс и тейк-профит
            self._set_stop_orders(side, size, current_price)
            
            return True
            
        except BitgetAPIException as e:
            logger.error(f"Ошибка при размещении ордера: {e.message}")
            return False

    def _set_stop_orders(self, side: str, size: float, entry_price: float):
        """Установка стоп-лосс и тейк-профит ордеров"""
        try:
            if side == "long":
                # Для лонг позиции
                stop_price = entry_price * (1 - self.stop_loss_percent / 100)
                take_profit_price = entry_price * (1 + self.take_profit_percent / 100)
                stop_side = "close_long"
            else:
                # Для шорт позиции
                stop_price = entry_price * (1 + self.stop_loss_percent / 100)
                take_profit_price = entry_price * (1 - self.take_profit_percent / 100)
                stop_side = "close_short"
            
            # Стоп-лосс ордер
            stop_loss_params = {
                "symbol": self.symbol,
                "marginCoin": self.margin_coin,
                "side": stop_side,
                "orderType": "stop_market",
                "size": str(size),
                "triggerPrice": str(round(stop_price, 2)),
                "timeInForceValue": "normal"
            }
            
            # Тейк-профит ордер
            take_profit_params = {
                "symbol": self.symbol,
                "marginCoin": self.margin_coin,
                "side": stop_side,
                "orderType": "take_profit_market", 
                "size": str(size),
                "triggerPrice": str(round(take_profit_price, 2)),
                "timeInForceValue": "normal"
            }
            
            # Размещаем стоп-ордера
            stop_response = self.order_api.placeOrder(stop_loss_params)
            profit_response = self.order_api.placeOrder(take_profit_params)
            
            if stop_response.get('code') == '00000':
                logger.info(f"Стоп-лосс установлен: {stop_price}")
            
            if profit_response.get('code') == '00000':
                logger.info(f"Тейк-профит установлен: {take_profit_price}")
                
        except BitgetAPIException as e:
            logger.error(f"Ошибка при установке стоп-ордеров: {e.message}")

    def close_existing_positions(self):
        """Закрытие существующих позиций при смене направления"""
        positions = self.get_current_positions()
        
        for position in positions:
            if position['symbol'] == self.symbol:
                size = abs(float(position['total']))
                if size > 0:
                    side = "close_long" if float(position['total']) > 0 else "close_short"
                    
                    close_params = {
                        "symbol": self.symbol,
                        "marginCoin": self.margin_coin,
                        "side": side,
                        "orderType": "market",
                        "size": str(size),
                        "timeInForceValue": "normal"
                    }
                    
                    try:
                        response = self.order_api.placeOrder(close_params)
                        if response.get('code') == '00000':
                            logger.info(f"Позиция закрыта: {side}, размер: {size}")
                    except BitgetAPIException as e:
                        logger.error(f"Ошибка закрытия позиции: {e.message}")

    def trading_cycle(self):
        """Основной цикл торговли"""
        try:
            logger.info("=== Начало торгового цикла ===")
            
            # Получаем данные
            balance = self.get_account_balance()
            if balance < self.min_balance:
                logger.warning(f"Недостаточно средств для торговли: ${balance:.2f} < ${self.min_balance}")
                return
            
            positions = self.get_current_positions()
            market_data = self.get_market_data()
            
            if not market_data or not market_data.get('current_price'):
                logger.error("Не удалось получить рыночные данные")
                return
            
            current_price = market_data['current_price']
            
            # Получаем рекомендацию от ИИ
            ai_decision = self.analyze_with_ai(market_data)
            
            if ai_decision['confidence'] < self.confidence_threshold:
                logger.info(f"ИИ не уверен в рекомендации: {ai_decision['confidence']}/{self.confidence_threshold}, пропускаем торговлю")
                return
            
            # Проверяем текущие позиции
            has_long_position = any(float(pos['total']) > 0 for pos in positions if pos['symbol'] == self.symbol)
            has_short_position = any(float(pos['total']) < 0 for pos in positions if pos['symbol'] == self.symbol)
            
            action = ai_decision['action']
            
            if action == "buy" and not has_long_position:
                if has_short_position:
                    logger.info("Закрываем шорт позицию перед открытием лонг")
                    self.close_existing_positions()
                    time.sleep(2)
                
                # Открываем лонг позицию
                position_size = self.calculate_position_size(balance, current_price)
                if position_size > 0:
                    success = self.place_order_with_stops("long", position_size, current_price)
                    if success:
                        logger.info(f"Лонг позиция открыта: {position_size} BTC по цене ${current_price}")
            
            elif action == "sell" and not has_short_position:
                if has_long_position:
                    logger.info("Закрываем лонг позицию перед открытием шорт")
                    self.close_existing_positions()
                    time.sleep(2)
                
                # Открываем шорт позицию
                position_size = self.calculate_position_size(balance, current_price)
                if position_size > 0:
                    success = self.place_order_with_stops("short", position_size, current_price)
                    if success:
                        logger.info(f"Шорт позиция открыта: {position_size} BTC по цене ${current_price}")
            
            elif action == "hold":
                logger.info("ИИ рекомендует удерживать текущие позиции")
            
            else:
                logger.info(f"ИИ рекомендует {action}, но соответствующая позиция уже открыта")
            
            logger.info("=== Цикл торговли завершен ===")
            
        except Exception as e:
            logger.error(f"Ошибка в торговом цикле: {str(e)}")

    def start_bot(self):
        """Запуск бота"""
        logger.info("Запуск торгового бота...")
        
        # Тестируем подключение к LM Studio
        try:
            response = requests.get(f"{self.lm_studio_url}/v1/models", timeout=5)
            if response.status_code == 200:
                logger.info("✅ Подключение к LM Studio успешно")
            else:
                logger.error("❌ LM Studio не отвечает")
                return
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к LM Studio: {str(e)}")
            return
        
        # Тестируем подключение к Bitget
        try:
            balance = self.get_account_balance()
            if balance >= 0:
                logger.info(f"✅ Подключение к Bitget успешно. Баланс: ${balance:.2f}")
            else:
                logger.error("❌ Ошибка получения данных с Bitget")
                return
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Bitget: {str(e)}")
            return
        
        # Настройка расписания с настраиваемым интервалом
        schedule.every(self.check_interval).minutes.do(self.trading_cycle)
        logger.info(f"Бот будет проверять рынок каждые {self.check_interval} минут")
        
        # Первый запуск
        logger.info("Выполняем первоначальный анализ...")
        self.trading_cycle()
        
        # Основной цикл
        logger.info("Бот запущен и работает. Нажмите Ctrl+C для остановки.")
        while True:
            try:
                schedule.run_pending()
                time.sleep(60)  # Проверяем каждую минуту
            except KeyboardInterrupt:
                logger.info("🛑 Бот остановлен пользователем")
                break
            except Exception as e:
                logger.error(f"Ошибка в основном цикле: {str(e)}")
                time.sleep(60)

# Пример использования
if __name__ == "__main__":
    # ВНИМАНИЕ! Обязательно замените на ваши реальные ключи API
    BITGET_API_KEY       = os.getenv("BITGET_API_KEY")
    BITGET_API_SECRET    = os.getenv("BITGET_API_SECRET")
    BITGET_API_PASSPHRASE= os.getenv("BITGET_API_PASSPHRASE")

    # URL для LM Studio (по умолчанию localhost:1234)
    LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234")
    
    # Создание и запуск бота
    bot = BitgetTradingBot(
        api_key=BITGET_API_KEY,
        secret_key=BITGET_API_SECRET,
        passphrase=BITGET_API_PASSPHRASE,
        lm_studio_url=LM_STUDIO_URL
    )
    
    # Запуск бота
    bot.start_bot()