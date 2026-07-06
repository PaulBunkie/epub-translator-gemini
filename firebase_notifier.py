"""
Модуль для отправки push-уведомлений через Firebase Cloud Messaging (FCM).
Отправляет data messages в топик 'matches' для обновления виджетов на Android.
"""

import os
import json
import traceback
from typing import Optional, Dict, Any

try:
    import firebase_admin
    from firebase_admin import messaging, credentials
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    print("[FirebaseNotifier] firebase-admin не установлен. Push-уведомления недоступны.")


class FirebaseNotifier:
    """
    Модуль для отправки push-уведомлений через Firebase Cloud Messaging (FCM).
    Отправляет data messages (тихие push) в топик 'matches'.
    """
    
    def __init__(self):
        self.initialized = False
        self.topic = "matches"
        self._init_firebase()
    
    def _init_firebase(self):
        """Инициализация Firebase Admin SDK."""
        if not FIREBASE_AVAILABLE:
            print("[FirebaseNotifier] Firebase недоступен (firebase-admin не установлен)")
            return
        
        try:
            # Пытаемся получить путь к ключу из переменной окружения
            service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
            
            if service_account_path and os.path.exists(service_account_path):
                # Инициализация через файл
                cred = credentials.Certificate(service_account_path)
                firebase_admin.initialize_app(cred)
                self.initialized = True
                print(f"[FirebaseNotifier] Firebase инициализирован из файла: {service_account_path}")
                return
            
            # Пытаемся найти ключ в корне проекта (локальная разработка)
            possible_paths = [
                "com-shrewd-bet-firebase-adminsdk-fbsvc-88a4ce351f.json",
                "firebase-service-account.json",
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    cred = credentials.Certificate(path)
                    firebase_admin.initialize_app(cred)
                    self.initialized = True
                    print(f"[FirebaseNotifier] Firebase инициализирован из файла: {path}")
                    return
            
            # Пытаемся инициализировать из переменной окружения (JSON строка)
            service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
            if service_account_json:
                try:
                    service_account_info = json.loads(service_account_json)
                    cred = credentials.Certificate(service_account_info)
                    firebase_admin.initialize_app(cred)
                    self.initialized = True
                    print("[FirebaseNotifier] Firebase инициализирован из переменной окружения FIREBASE_SERVICE_ACCOUNT")
                    return
                except json.JSONDecodeError as e:
                    print(f"[FirebaseNotifier] Ошибка парсинга JSON из FIREBASE_SERVICE_ACCOUNT: {e}")
            
            # Попытка инициализации по умолчанию (если уже инициализирован)
            try:
                if firebase_admin._apps:
                    self.initialized = True
                    print("[FirebaseNotifier] Firebase уже инициализирован")
                    return
            except:
                pass
            
            print("[FirebaseNotifier] Firebase Service Account не найден. Push-уведомления недоступны.")
            print("[FirebaseNotifier] Установите FIREBASE_SERVICE_ACCOUNT_PATH или FIREBASE_SERVICE_ACCOUNT")
            
        except Exception as e:
            print(f"[FirebaseNotifier] Ошибка инициализации Firebase: {e}")
            print(traceback.format_exc())
    
    def send_match_update(
        self,
        match_id: str,
        score_home: str,
        score_away: str,
        status: str,
        minute: str = "",
        k0: str = "",
        k1: str = "",
        k60: str = "",
        event_type: str = ""
    ) -> bool:
        """
        Отправляет тихий пуш для обновления виджетов.
        
        Args:
            match_id: ID матча
            score_home: Счет домашней команды
            score_away: Счет гостевой команды
            status: Статус матча (live, finished, notstarted)
            minute: Текущая минута матча
            k0: Начальный коэффициент фаворита
            k1: Текущий коэффициент фаворита
            k60: Коэффициент фаворита на 60-й минуте
            event_type: Тип события (pre_match, heartbeat, goal, favorite_trouble, postponed, match_end)
        
        Returns:
            bool: True если уведомление отправлено успешно
        """
        if not self.initialized:
            print("[FirebaseNotifier] Firebase не инициализирован, пропускаем отправку")
            return False
        
        try:
            data = {
                "match_id": str(match_id),
                "score_home": str(score_home),
                "score_away": str(score_away),
                "status": str(status),
                "minute": str(minute),
                "k0": str(k0),
                "k1": str(k1),
                "k60": str(k60),
                "event_type": str(event_type),
            }
            
            message = messaging.Message(
                data=data,
                topic=self.topic,
            )
            
            response = messaging.send(message)
            print(f"[FAVOURITE_TRACKING] 📲 PUSH SENT via Firebase | match_id={match_id} | status={status} | event_type={event_type} | score={score_home}-{score_away} | minute={minute} | k0={k0} | k1={k1} | k60={k60} | response={response}")
            return True
            
        except Exception as e:
            print(f"[FAVOURITE_TRACKING] ❌ PUSH FAILED via Firebase | match_id={match_id} | event_type={event_type} | error={e}")
            print(traceback.format_exc())
            return False
    
    def test_send(self) -> bool:
        """
        Тестовая отправка push-уведомления.
        
        Returns:
            bool: True если уведомление отправлено успешно
        """
        return self.send_match_update(
            match_id="test_0000001",
            score_home="1",
            score_away="0",
            status="live",
            minute="45",
            k0="1.30",
            k1="1.45",
            k60="1.80"
        )


# Глобальный экземпляр для использования в других модулях
firebase_notifier = FirebaseNotifier()