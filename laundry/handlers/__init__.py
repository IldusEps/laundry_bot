"""Регистрация обработчиков. Порядок импорта важен: messages — последним."""
from . import common, booking, manage, admin  # noqa: F401  (команды и inline-кнопки)
from . import messages  # noqa: F401  (кнопки меню, ввод по шагам, прочий текст)
