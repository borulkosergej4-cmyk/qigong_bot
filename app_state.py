"""Глобальное состояние, разделяемое между run_all.py и dashboard.py."""
admin_application = None   # telegram.ext.Application
webhook_secret: str = ""   # секрет для валидации Telegram-запросов
