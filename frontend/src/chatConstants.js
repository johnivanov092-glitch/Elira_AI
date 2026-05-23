// chatConstants.js — shared constants for Elira Chat UI

export const LIBRARY_KEY = "elira_library_files_v7";
export const CHAT_CONTEXT_KEY = "elira_chat_context_map_v7";

export const MAX_HISTORY_PAIRS = 10;

export const PROFILE_DESCRIPTIONS = {
  "Универсальный": "Ясный, структурированный и профессиональный тон.",
  "Программист": "Код, исправления, архитектура, рефакторинг.",
  "Исследователь": "Факты, источники, web-поиск.",
  "Аналитик": "Выводы, риски, декомпозиция.",
  "Сократ": "Обучение через наводящие вопросы.",
};

export const SKILLS = [
  { id: "web_search", label: "Веб-поиск", desc: "Поиск в интернете" },
  { id: "code_analysis", label: "Анализ кода", desc: "Разбор структуры кода" },
  { id: "file_context", label: "Контекст файлов", desc: "Загруженные файлы в ответах" },
  { id: "memory", label: "Память", desc: "Запоминание между чатами" },
  { id: "python_exec", label: "Python", desc: "Выполнение скриптов" },
  { id: "project_patch", label: "Патчинг", desc: "Изменение файлов проекта" },
  { id: "pdf_reader", label: "PDF", desc: "Извлечение текста из PDF" },
  { id: "reflection", label: "Рефлексия", desc: "Двойная проверка ответов" },
  { id: "http_api", label: "HTTP/API", desc: "GET/POST запросы к API" },
  { id: "sql_query", label: "SQL", desc: "Запросы к базе данных" },
  { id: "file_gen", label: "Word/Excel", desc: "Генерация документов" },
  { id: "screenshot", label: "Скриншот", desc: "Снимок веб-страницы" },
  { id: "encrypt", label: "Шифрование", desc: "AES шифрование заметок" },
  { id: "archiver", label: "Архиватор", desc: "ZIP создание/распаковка" },
  { id: "converter", label: "Конвертер", desc: "CSV→XLSX, MD→DOCX, JSON→CSV" },
  { id: "regex", label: "Regex", desc: "Тестирование регулярок" },
  { id: "translator", label: "Переводчик", desc: "Перевод через LLM" },
  { id: "csv_analysis", label: "CSV анализ", desc: "Статистика и агрегации" },
  { id: "webhook", label: "Webhook", desc: "Приём входящих вебхуков" },
  { id: "plugins", label: "Плагины", desc: "Пользовательские .py скрипты" },
  { id: "image_gen", label: "Картинки", desc: "FLUX.1 генерация изображений" },
  { id: "git", label: "Git", desc: "Статус, log, diff репозитория" },
];
