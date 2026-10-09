"""Task-scoped search language policy, owned by the mutable web skill."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Callable

from webskill import context

_CYRILLIC = re.compile(r'[А-Яа-яЁё]')
_REJECTION_LIMIT = 2


def _history() -> tuple[Path | None, set[bool], int, bool, str]:
    if not context.web_cache_write_allowed(context.run_persistence_policy(context.run_id())):
        return None, set(), _REJECTION_LIMIT, False, ''
    try:
        directory = context.data_file('search-language')
        directory.mkdir(exist_ok=True)
        languages: set[bool] = set()
        rejected, reminded = 0, False
        # Only two booleans and a bounded counter are retained, never query text.
        for path in directory.glob('*.json'):
            if path.stat().st_size > 1024:
                raise ValueError('oversized search language record')
            row = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(row, dict) or not isinstance(row.get('languages'), list):
                raise ValueError('invalid search language record')
            if any(type(x) is not bool for x in row['languages']):
                raise ValueError('invalid language flag')
            languages.update(row['languages'])
            rejected += row.get('rejected') is True
            reminded = reminded or row.get('reminded') is True
        return directory, languages, rejected, reminded, ''
    except (OSError, ValueError, TypeError):
        # Unable to retain the retry count: warn instead of creating an endless rejection loop.
        return None, set(), _REJECTION_LIMIT, False, 'Состояние языков поиска недоступно; проверка будет рекомендательной.'


def _remember(directory: Path | None, languages: set[bool], *, rejected: bool, reminded: bool) -> bool:
    if directory is None:
        return False
    path = directory / (uuid.uuid4().hex + '.json')
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps({'languages': sorted(languages), 'rejected': rejected,
            'reminded': reminded}) + '\n', encoding='utf-8', newline='\n')
        temporary.replace(path)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def search_with_language_policy(queries: list[str], audience: str, execute: Callable[[], dict]) -> dict:
    directory, previous, rejections, reminded, warning = _history()
    current = {bool(_CYRILLIC.search(q)) for q in queries if q.strip()}
    languages = previous | current
    declared = str(audience or '').strip().lower()
    single = len(languages) == 1
    used, other = ('русском', 'английском') if True in languages else ('английском', 'русском')
    if declared.startswith(('global', 'общ')) and single and rejections < _REJECTION_LIMIT:
        denial = (f'Поиск не выполнен: audience="global" требует запросов и на русском, и на английском; '
                  f'сейчас запросы только на {used}. Повтори search, добавив через --query запросы на {other}. '
                  'Для конкретной страны или региона используй audience="regional:<страна>".')
        if _remember(directory, set(), rejected=True, reminded=False):
            return {'ok': False, 'error': 'audience_languages', 'text': denial, 'sources': []}
        warning = 'Не удалось сохранить число отказов; поиск продолжен с рекомендацией по языкам.'
    result = execute()
    if result.get('ok') is True and current:
        hint = ''
        if single and not reminded and not declared.startswith(('regional', 'регион')):
            hint = (f'[Языки поиска] Все запросы пока только на {used}. Для общей темы добавь запросы на {other}. '
                    'Для конкретной страны или региона можно оставить язык её аудитории.')
        _remember(directory, current, rejected=False, reminded=bool(hint))
        if hint:
            result = {**result, 'text': str(result.get('text') or '') + '\n\n' + hint}
    if warning:
        result = {**result, 'text': str(result.get('text') or '') + '\n\n' + warning}
    return result
