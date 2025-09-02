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
from decimal import Decimal, ROUND_DOWN, ROUND_UP
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
        self.symbols = [s.strip() for s in os.getenv('SYMBOLS', 'ETHUSDT_UMCBL,BTCUSDT_UMCBL').split(',') if s.strip()]
        self.margin_coin = "USDT"
        self.max_position_percent = float(os.getenv('MAX_POSITION_PERCENT', '10.0'))  # Увеличили до 10%
        self.max_risk_percent = float(os.getenv('MAX_RISK_PERCENT', '30.0'))
        self.stop_loss_percent = float(os.getenv('STOP_LOSS_PERCENT', '2.0'))
        self.take_profit_percent = float(os.getenv('TAKE_PROFIT_PERCENT', '6.0'))
        self.confidence_threshold = int(os.getenv('CONFIDENCE_THRESHOLD', '6'))
        self.check_interval = int(os.getenv('CHECK_INTERVAL', '15'))
        
        # Дополнительные настройки безопасности
        self.min_balance = float(os.getenv('MIN_BALANCE', '10.0'))
        self.max_position_value = float(os.getenv('MAX_POSITION_VALUE', '1000.0'))
        
        # Кэш для параметров контрактов
        self.contract_info_cache = {}
        self.cache_update_time = {}
        self.cache_ttl = 3600  # 1 час
        
        logger.info(f"Торговый бот инициализирован с настройками:")
        logger.info(f"- Максимальный размер позиции: {self.max_position_percent}%")
        logger.info(f"- Стоп-лосс: {self.stop_loss_percent}%")
        logger.info(f"- Тейк-профит: {self.take_profit_percent}%")
        logger.info(f"- Порог уверенности ИИ: {self.confidence_threshold}/10")
        logger.info(f"- Интервал проверки: {self.check_interval} минут")

    def get_contract_info(self, symbol: str) -> Dict:
        """Получение информации о контракте из API с кэшированием"""
        current_time = time.time()
        
        # Проверяем кэш
        if (symbol in self.contract_info_cache and 
            symbol in self.cache_update_time and 
            current_time - self.cache_update_time[symbol] < self.cache_ttl):
            return self.contract_info_cache[symbol]
        
        try:
            params = {"productType": "umcbl"}
            response = self.market_api.contracts(params)
            
            if response.get('code') == '00000' and response.get('data'):
                for contract in response['data']:
                    if contract.get('symbol') == symbol:
                        # Получаем минимальную сумму торговли или используем дефолт
                        min_trade_usdt = contract.get('minTradeUSDT')
                        if min_trade_usdt is None or min_trade_usdt == 0:
                            # Используем минимум по умолчанию, характерный для Bitget
                            min_trade_usdt = 5.0
                        
                        contract_info = {
                            'symbol': contract.get('symbol'),
                            'minTradeNum': float(contract.get('minTradeNum', 0.001)),
                            'priceEndStep': int(contract.get('priceEndStep', 2)),
                            'volumePlace': int(contract.get('volumePlace', 3)),
                            'sizeMultiplier': float(contract.get('sizeMultiplier', 1)),
                            'minTradeUSDT': float(min_trade_usdt),
                            'quoteCoin': contract.get('quoteCoin', 'USDT'),
                            'baseCoin': contract.get('baseCoin', symbol.replace('USDT_UMCBL', ''))
                        }
                        
                        # Сохраняем в кэш
                        self.contract_info_cache[symbol] = contract_info
                        self.cache_update_time[symbol] = current_time
                        
                        logger.info(f"[{symbol}] Параметры контракта: минТорги={contract_info['minTradeNum']}, "
                                  f"минUSDT={contract_info['minTradeUSDT']}, точность={contract_info['volumePlace']}")
                        return contract_info
            
            logger.error(f"[{symbol}] Не найден контракт в ответе API")
            return {}
            
        except BitgetAPIException as e:
            logger.error(f"[{symbol}] Ошибка при получении информации о контракте: {e.message}")
            return {}

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

    def get_current_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Получение текущих позиций. При указании symbol вернёт только позиции по этому инструменту."""
        try:
            params = {"productType": "umcbl", "marginCoin": self.margin_coin}
            response = self.account_api.allPosition(params)

            if response.get('code') == '00000':
                positions = response.get('data', [])
                active_positions = [pos for pos in positions if float(pos.get('total', 0)) != 0]
                if symbol:
                    active_positions = [pos for pos in active_positions if pos.get('symbol') == symbol]
                logger.info(f"Активных позиций: {len(active_positions)}" + (f" (фильтр: {symbol})" if symbol else ""))
                return active_positions

            return []

        except BitgetAPIException as e:
            logger.error(f"Ошибка при получении позиций: {e.message}")
            return []

    def get_market_data(self, symbol: str) -> Dict:
        """Получение рыночных данных для анализа по конкретному инструменту"""
        try:
            # Получаем текущую цену
            ticker_params = {"symbol": symbol}
            ticker_response = self.market_api.ticker(ticker_params)

            # Получаем исторические данные (свечи за последние 24 часа)
            timestamp_current_ms = int(time.time() * 1000)
            timestamp_24h_ago = timestamp_current_ms - 86400000
            kline_params = {
                "symbol": symbol,
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

            logger.info(f"[{symbol}] Получены рыночные данные. Цена: {market_data['current_price']}")
            return market_data

        except BitgetAPIException as e:
            logger.error(f"[{symbol}] Ошибка при получении рыночных данных: {e.message}")
            return {}

    def analyze_with_ai(self, market_data: Dict, symbol: str) -> Dict:
        """Анализ рынка с помощью локального ИИ"""
        try:
            prompt = self._create_analysis_prompt(market_data, symbol)

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
                decision = self._parse_ai_response(analysis)
                logger.info(f"[{symbol}] ИИ рекомендация: {decision}")
                return decision
            else:
                logger.error(f"[{symbol}] Ошибка API LM Studio: {response.status_code}")
                return {"action": "hold", "confidence": 0}

        except Exception as e:
            logger.error(f"[{symbol}] Ошибка при обращении к ИИ: {str(e)}")
            return {"action": "hold", "confidence": 0}

    def _create_analysis_prompt(self, market_data: Dict, symbol: str) -> str:
        """Создание промпта для анализа ИИ"""
        klines_text = ""
        if market_data.get("klines"):
            recent_klines = market_data["klines"][-20:]  # Последние 20 свечей
            klines_text = "Последние 20 свечей (время, открытие, максимум, минимум, закрытие, объем):\n"
            for kline in recent_klines:
                klines_text += f"{kline[0]}, {kline[1]}, {kline[2]}, {kline[3]}, {kline[4]}, {kline[5]}\n"

        prompt = f"""Проанализируйте рыночные данные для {symbol}:

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

    def calculate_position_size(self, balance: float, current_price: float, symbol: str) -> float:
        """Расчет размера позиции с учетом реальных ограничений биржи"""
        
        # Получаем информацию о контракте
        contract_info = self.get_contract_info(symbol)
        if not contract_info:
            logger.error(f"[{symbol}] Не удалось получить информацию о контракте")
            return 0.0
        
        # Параметры контракта
        min_trade_num = contract_info.get('minTradeNum', 0.001)
        min_trade_usdt_from_api = contract_info.get('minTradeUSDT', 5.0)
        volume_place = contract_info.get('volumePlace', 3)
        
        # Расчитываем минимальную стоимость в USD на основе minTradeNum и текущей цены
        min_trade_usdt_calculated = min_trade_num * current_price
        
        # Используем максимальное из двух значений для безопасности
        min_trade_usdt = max(min_trade_usdt_from_api, min_trade_usdt_calculated, 5.0)
        
        logger.info(f"[{symbol}] Минимальные требования: "
                   f"размер={min_trade_num}, "
                   f"USDT_API={min_trade_usdt_from_api}, "
                   f"USDT_calc={min_trade_usdt_calculated:.2f}, "
                   f"итого_мин=${min_trade_usdt:.2f}")
        
        # Расчет максимального размера позиции в USD
        max_position_value = balance * (self.max_position_percent / 100)
        max_position_value = min(max_position_value, self.max_position_value)
        
        # Проверяем, что у нас достаточно средств для минимальной торговли
        if max_position_value < min_trade_usdt:
            logger.warning(f"[{symbol}] Недостаточно средств: {max_position_value:.2f} < {min_trade_usdt:.2f}")
            # Попробуем с меньшими требованиями
            if max_position_value >= min_trade_usdt_calculated and min_trade_usdt_calculated >= 1.0:
                logger.info(f"[{symbol}] Используем расчетный минимум: {min_trade_usdt_calculated:.2f}")
                min_trade_usdt = min_trade_usdt_calculated
            else:
                return 0.0
        
        # Расчет размера позиции в базовой валюте
        position_size = max_position_value / current_price
        
        # Округляем до правильной точности согласно volumePlace
        decimal_places = f"0.{'0' * volume_place}"
        position_size = float(Decimal(str(position_size)).quantize(Decimal(decimal_places), rounding=ROUND_DOWN))
        
        # Проверяем минимальный размер торговли
        if position_size < min_trade_num:
            logger.warning(f"[{symbol}] Размер позиции {position_size} меньше минимального {min_trade_num}")
            # Попробуем использовать минимально допустимый размер
            position_size = min_trade_num
            position_size = float(Decimal(str(position_size)).quantize(Decimal(decimal_places), rounding=ROUND_UP))
        
        # Финальная проверка стоимости в USD
        final_position_value_usdt = position_size * current_price
        if final_position_value_usdt < min_trade_usdt:
            logger.warning(f"[{symbol}] Итоговая стоимость позиции {final_position_value_usdt:.2f} меньше минимальной {min_trade_usdt:.2f}")
            return 0.0
        
        # Проверяем, не превышаем ли баланс
        if final_position_value_usdt > balance * 0.95:  # Оставляем 5% на комиссии
            logger.warning(f"[{symbol}] Позиция слишком большая относительно баланса")
            return 0.0
        
        logger.info(f"[{symbol}] ✅ Финальный размер позиции: {position_size} ({final_position_value_usdt:.2f} USD)")
        logger.info(f"[{symbol}] Параметры: минРазмер={min_trade_num}, минUSDT={min_trade_usdt:.2f}, точность={volume_place}")
        
        return position_size

    def place_order_with_stops(self, side: str, size: float, current_price: float, symbol: str) -> bool:
        """Размещение ордера с автоматическими стопами по конкретному инструменту"""
        try:
            order_params = {
                "symbol": symbol,
                "marginCoin": self.margin_coin,
                "side": f"open_{side}",
                "orderType": "market",
                "size": str(size),
                "timeInForceValue": "normal"
            }

            response = self.order_api.placeOrder(order_params)

            if response.get('code') != '00000':
                logger.error(f"[{symbol}] Ошибка размещения ордера: {response}")
                return False

            order_id = response['data']['orderId']
            logger.info(f"[{symbol}] Ордер размещен: {order_id}, сторона: {side}, размер: {size}")

            time.sleep(2)
            self._set_stop_orders(side, size, current_price, symbol)
            return True

        except BitgetAPIException as e:
            logger.error(f"[{symbol}] Ошибка при размещении ордера: {e.message}")
            return False

    def _set_stop_orders(self, side: str, size: float, entry_price: float, symbol: str):
        """Установка стоп-лосс и тейк-профит ордеров"""
        try:
            if side == "long":
                stop_price = entry_price * (1 - self.stop_loss_percent / 100)
                take_profit_price = entry_price * (1 + self.take_profit_percent / 100)
                stop_side = "close_long"
            else:
                stop_price = entry_price * (1 + self.stop_loss_percent / 100)
                take_profit_price = entry_price * (1 - self.take_profit_percent / 100)
                stop_side = "close_short"

            # Получаем точность цены для правильного округления
            contract_info = self.get_contract_info(symbol)
            price_precision = contract_info.get('priceEndStep', 2) if contract_info else 2

            stop_loss_params = {
                "symbol": symbol,
                "marginCoin": self.margin_coin,
                "side": stop_side,
                "orderType": "stop_market",
                "size": str(size),
                "triggerPrice": str(round(stop_price, price_precision)),
                "timeInForceValue": "normal"
            }

            take_profit_params = {
                "symbol": symbol,
                "marginCoin": self.margin_coin,
                "side": stop_side,
                "orderType": "take_profit_market",
                "size": str(size),
                "triggerPrice": str(round(take_profit_price, price_precision)),
                "timeInForceValue": "normal"
            }

            stop_response = self.order_api.placeOrder(stop_loss_params)
            profit_response = self.order_api.placeOrder(take_profit_params)

            if stop_response.get('code') == '00000':
                logger.info(f"[{symbol}] Стоп-лосс установлен: {round(stop_price, price_precision)}")

            if profit_response.get('code') == '00000':
                logger.info(f"[{symbol}] Тейк-профит установлен: {round(take_profit_price, price_precision)}")

        except BitgetAPIException as e:
            logger.error(f"[{symbol}] Ошибка при установке стоп-ордеров: {e.message}")

    def close_existing_positions(self, symbol: str):
        """Закрытие существующих позиций по конкретному инструменту при смене направления"""
        positions = self.get_current_positions(symbol)

        for position in positions:
            if position.get('symbol') == symbol:
                size = abs(float(position.get('total', 0)))
                if size > 0:
                    side = "close_long" if float(position.get('total', 0)) > 0 else "close_short"

                    close_params = {
                        "symbol": symbol,
                        "marginCoin": self.margin_coin,
                        "side": side,
                        "orderType": "market",
                        "size": str(size),
                        "timeInForceValue": "normal"
                    }

                    try:
                        response = self.order_api.placeOrder(close_params)
                        if response.get('code') == '00000':
                            logger.info(f"[{symbol}] Позиция закрыта: {side}, размер: {size}")
                    except BitgetAPIException as e:
                        logger.error(f"[{symbol}] Ошибка закрытия позиции: {e.message}")

    def trading_cycle(self):
        """Основной цикл торговли по всем указанным инструментам"""
        try:
            logger.info("=== Начало торгового цикла ===")

            balance = self.get_account_balance()
            if balance < self.min_balance:
                logger.warning(f"Недостаточно средств для торговли: ${balance:.2f} < ${self.min_balance}")
                return

            # Кэшируем все позиции один раз
            all_positions = self.get_current_positions()

            for symbol in self.symbols:
                logger.info(f"--- Обработка {symbol} ---")
                market_data = self.get_market_data(symbol)
                if not market_data or not market_data.get('current_price'):
                    logger.error(f"[{symbol}] Не удалось получить рыночные данные")
                    continue

                current_price = market_data['current_price']
                ai_decision = self.analyze_with_ai(market_data, symbol)

                if ai_decision['confidence'] < self.confidence_threshold:
                    logger.info(f"[{symbol}] ИИ не уверен: {ai_decision['confidence']}/{self.confidence_threshold}, пропускаем")
                    continue

                has_long_position = any(float(pos['total']) > 0 for pos in all_positions if pos.get('symbol') == symbol)
                has_short_position = any(float(pos['total']) < 0 for pos in all_positions if pos.get('symbol') == symbol)

                action = ai_decision['action']

                if action == "buy" and not has_long_position:
                    if has_short_position:
                        logger.info(f"[{symbol}] Закрываем шорт перед открытием лонг")
                        self.close_existing_positions(symbol)
                        time.sleep(2)

                    position_size = self.calculate_position_size(balance, current_price, symbol)
                    if position_size > 0:
                        success = self.place_order_with_stops("long", position_size, current_price, symbol)
                        if success:
                            logger.info(f"[{symbol}] Лонг открыт: {position_size} по цене ${current_price}")

                elif action == "sell" and not has_short_position:
                    if has_long_position:
                        logger.info(f"[{symbol}] Закрываем лонг перед открытием шорт")
                        self.close_existing_positions(symbol)
                        time.sleep(2)

                    position_size = self.calculate_position_size(balance, current_price, symbol)
                    if position_size > 0:
                        success = self.place_order_with_stops("short", position_size, current_price, symbol)
                        if success:
                            logger.info(f"[{symbol}] Шорт открыт: {position_size} по цене ${current_price}")

                elif action == "hold":
                    logger.info(f"[{symbol}] ИИ рекомендует удерживать текущие позиции")
                else:
                    logger.info(f"[{symbol}] ИИ рекомендует {action}, но соответствующая позиция уже открыта")

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
        
        # Предзагружаем информацию о контрактах
        logger.info("Загружаем информацию о контрактах...")
        for symbol in self.symbols:
            contract_info = self.get_contract_info(symbol)
            if contract_info:
                logger.info(f"✅ {symbol}: минРазмер={contract_info['minTradeNum']}, "
                          f"минUSDT={contract_info['minTradeUSDT']}, точность={contract_info['volumePlace']}")
                
                # Получаем текущую цену для демонстрации минимальных требований
                try:
                    market_data = self.get_market_data(symbol)
                    if market_data.get('current_price'):
                        current_price = market_data['current_price']
                        min_value_calc = contract_info['minTradeNum'] * current_price
                        logger.info(f"   Текущая цена: ${current_price}, минимум в USD: ${min_value_calc:.2f}")
                except:
                    pass
            else:
                logger.warning(f"⚠️ {symbol}: не удалось получить информацию о контракте")
        
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