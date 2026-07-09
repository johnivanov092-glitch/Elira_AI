# Web Evidence Contract (W0) — v2

Контракт трека W1–W6 «senior internet auditor»: корпус прочитанного, выборочный
retrieval, детерминированный citation-ledger. Метод — как у verifier-каталога:
контракт → ревью → код → тесты. Порядок: **W0 → W1 (corpus+retrieval) →
W2 (documents) → W3 (ledger) → W5 (freshness/conflicts) → W4 (crawler только по
реальной необходимости; до него W4-lite sitemap) → W6.** Стартовый батч — W0+W1.

Главные риски (зафиксированы до кода): **prompt injection из веб-контента —
причём рабочий режим по умолчанию bypass, так что permission-гейт сам по себе
НЕ митигация; неконтролируемый рост корпуса; ложное приравнивание цитаты к
доказательству утверждения.**

---

## 1. Schema

**Document:**

| поле | тип | примечание |
|---|---|---|
| doc_id | str | hash(final_url + content_hash) |
| url / final_url | str | запрошенный и фактический (после redirect'ов) |
| fetched_at | ts | момент фетча |
| content_hash | sha256 | канонического текста |
| mime | str | из ответа; allowlist (§4) |
| title, outline | str, list | заголовок + структура (h1-h4 / страницы PDF) |
| dates | dict | published / modified / Last-Modified, если извлекаются |
| tier | enum | official/primary/secondary/ugc/unknown — эвристика-guard |
| **trust** | const | **всегда `untrusted`** — не переопределяется ничем (§4) |
| canonical_text | text | очищенный текст (§4), НЕ сырой HTML |
| analyzer_ver | str | версия ru-анализатора, которым построен индекс (§5) |

**Chunk:** {doc_id, chunk_id, offset, length, text}. Чанкование детерминированное
(заголовки/абзацы, ~1-2K симв.), offset — в canonical_text.

**Claim (ledger):** {claim_text, evidence[{doc_id, chunk_id, quote, offset}],
states} — states в §2.

## 2. State machine

**Document lifecycle:**
```
fetched → stored → chunked → indexed ──┬→ expired (TTL) → deleted
                                       ├→ evicted (LRU quota) → deleted
                                       ├→ deleted-with-run (cleanup)
                                       └→ pinned (promote в Библиотеку:
                                            source=web, url, content_hash,
                                            trust=untrusted СОХРАНЯЮТСЯ)
```
Pin НЕ превращает страницу в доверенную инструкцию — это персистенция данных,
не повышение доверия.

**Claim/evidence states:**

| состояние | кто ставит | переход |
|---|---|---|
| `recorded` | runtime | web_claim_add принят (bounds §6 соблюдены) |
| `quote_verified` | **runtime, детерминированно** | цитата ДОСЛОВНО найдена в canonical_text по offset, hash сходится; exact-match НЕ зависит от стемминга |
| `source_verified` | **runtime, детерминированно** | документ реально фетчился раном: запись корпуса c final_url + content_hash + fetched_at |
| `claim_support` | **модель, advisory** | «цитата подтверждает утверждение» — НИКОГДА не runtime-вердикт; NLI-усиление (если появится) остаётся advisory |
| `conflicted` | модель+runtime | противоречащие advisory-оценки / явный флаг |

**Негативное правило №1:** `quote_verified` = провенанс. Он никогда не
трактуется как истинность claim. Детерминированные и advisory-слои
отображаются раздельно (UI-чипы по образцу readiness).

## 3. Trust boundaries

- **Web-контент недоверен всегда.** Корпус-текст в контекст модели — только
  через маркированный data-конверт («данные, не инструкции»).
- **Intent-binding для side-effect'ов (критично, работает в ask И в bypass):**
  side-effect-вызов (write_file/edit_file/run_bash/run_server/ssh_*/computer/…),
  чьи строковые аргументы **corpus-tainted** — содержат вербатим-фрагмент
  (≥ порога, напр. 24 симв.) из корпуса, отсутствующий в тексте задачи/сообщений
  пользователя, — эскалируется в **critical-tier существующей машинерии**:
  явное подтверждение человеком, auto-approve запрещён В ЛЮБОМ режиме, включая
  bypass (ровно как `_is_critical_call` сегодня). Направление отказа: эскалация
  к подтверждению, не молчаливый блок и не auto-pass. Это guard-эвристика
  (инвариант №10 карты): worst case FP = лишнее подтверждение, worst case FN —
  сужается конвертом+smoke, честно не нулевой.
- **Инъекционный smoke-набор обязан проходить в ОБОИХ режимах: ask и bypass.**
- **Promote в Библиотеку** сохраняет source=web, URL, content_hash,
  trust=untrusted (см. lifecycle) — привилегий не добавляет.
- Runtime-вердикты ledger'а вычисляются только из корпуса (hash-проверенного),
  никогда из текста модели.

## 4. Canonical text и сетевые границы

- Strip HTML/скриптов/style; удаление zero-width и управляющих символов;
  нормализация пробелов. Сохраняются url, final_url, timestamp, content_hash,
  mime.
- **SSRF:** существующий гард + повторная проверка на КАЖДОМ redirect-хопе.
- **MIME allowlist:** text/html, text/plain, application/pdf, docx (W2);
  прочее — отказ ok=False. Size-cap ответа ДО парсинга.
- **Sitemap-lite (W4-lite):** только GET/HEAD; лимиты URL/байтов/времени на
  вызов; SSRF-проверка каждого URL и каждого redirect'а; переход на другой
  eTLD+1 запрещён; никакого link-following.

## 5. Retrieval

- **BM25 — обязательное основание.** Ru-анализатор: lowercase, ё→е, лёгкий
  стемминг. **Анализатор версионируется** (`analyzer_ver` на документе;
  несовпадение версии → переиндексация документа). **Exact-phrase поиск и
  quote-верификация работают БЕЗ стемминга** (по canonical_text напрямую) —
  стемминг только улучшение ранжирования.
- **Embeddings (`:8001`)** — опциональный re-rank, fail-open к чистому BM25
  (одно предупреждение на mtime-класс, паттерн catalog.py).
- `web_query(query, doc_id?)` → top-k чанков: цитата + doc_id + chunk_id +
  offset (готовые кандидаты в evidence).

## 6. Tools и bounds

| tool | bounds |
|---|---|
| `web_fetch(store=true)` | квоты §7; за флагом §8 |
| `web_query` | top-k ≤ 8; ответ ≤ ~6K симв. в конверте |
| `web_claim_add(claims=[…])` | **батчевый, но bounded: ≤ 10 claims/вызов, ≤ 4 evidence/claim, quote ≤ 500 симв.**; превышение — честный ok=False с указанием лимита |
| sitemap-lite | §4 |

Рендер цитат — детерминированно runtime'ом (блок-аппендикс как
runtime_final_report); номера [n] из текста модели НЕ парсятся. Nudge
closure-стиля: ран читал веб → отчёт без записей ledger → один пинок.
Все новые тулы — deferred (компакция-канарейка).

## 7. Lifecycle / TTL / quota

- Хранение: **app-data слой** (существующий, `core.data_files`), ключ
  `project_scope_id + run_id + doc_id`. НЕ в проекте (гигиена + инъекция:
  корпус в проекте стал бы «фактами проекта» через grep/правило 20).
- Квоты per run: ≤ 40 документов, ≤ 15 MB canonical_text. Per scope: ≤ 200
  документов / 60 MB, вытеснение LRU.
- TTL 7 дней; cleanup при удалении run'а; dedup по content_hash (повторный
  фетч = ссылка + обновление fetched_at).

## 8. Feature flag / совместимость

- Флаг `web_corpus` (ENV `ELIRA_WEB_CORPUS` + UI-тумблер + PUT-Literal —
  синхронно с `_ENV_VAR`, урок R1). Default OFF.
- При выключенном флаге `web_fetch`/browser — **бит-в-бит** (пиновано тестами).

## 9. Failure taxonomy

| класс | поверхность | модель видит | ран |
|---|---|---|---|
| fetch failed (сеть/таймаут) | ok=False + причина | да | продолжает |
| SSRF block (вкл. redirect-хоп) | ok=False «SSRF blocked» | да | продолжает |
| MIME/size reject | ok=False + лимит | да | продолжает |
| parse/canonicalize fail | ok=False; документ НЕ сохраняется | да | продолжает |
| quota exceeded (run/scope) | ok=False + какая квота; store откл. до конца рана | да | продолжает |
| embed недоступен | лог-warning; BM25-only | нет (прозрачно) | продолжает |
| corpus store недоступен | web_fetch деградирует к без-store поведению + warning | да | продолжает |
| ledger verify fail (quote/hash) | evidence помечается unverified, claim остаётся recorded | да | продолжает |
| claim bounds превышены | ok=False + лимиты | да | продолжает |
| intent-binding escalation | approval-запрос (все режимы) | да | пауза→решение человека |

Ничто из таксономии не роняет ран; ложный успех запрещён (ошибочный путь
всегда ok=False/метка).

## 10. Invariants

1. quote-match = провенанс, НЕ доказательство claim.
2. Корпус — данные, не инструкции; корпус никогда не лежит в проекте.
3. Corpus-tainted side-effect требует связи с интентом ПОЛЬЗОВАТЕЛЯ, иначе —
   критикал-подтверждение во ВСЕХ режимах (bypass включительно).
4. Pin/promote не повышает доверие (trust=untrusted навсегда).
5. Недоступный embed ≠ сломанный retrieval; exact-phrase не зависит от
   стемминга; анализатор версионирован.
6. Ledger не собирается парсингом финального текста модели; вердикты — только
   из hash-проверенного корпуса.
7. Redirect не обходит SSRF (re-check на каждом хопе); sitemap-lite не покидает
   eTLD+1.
8. NLI (если появится) — advisory всегда, deterministic-вердиктом не бывает.
9. Два evidence-домена «независимы» по eTLD+1-приближению; официальный
   первичный источник (tier=official) может быть достаточен один.
10. Все новые тулы deferred; при выключенном флаге — бит-в-бит.

## 11. Executable DoD (W1)

Каждый пункт — конкретный тест/смоук:

1. Документ 200K симв. → вопрос по факту из середины → ответ верен, в LLM
   передано <5K симв. (счётчик по конверту).
2. Повторный вопрос по тому же корпусу → 0 новых fetch'ей (счётчик вызовов).
3. Quote→offset→hash round-trip: цитата из web_query детерминированно
   верифицируется по корпусу; порча одного байта текста → verify fail.
4. BM25-retrieval при выключенном/недоступном embed; exact-phrase находит
   точную строку при отключённом стемминге; analyzer_ver mismatch →
   переиндексация.
5. **Инъекционный smoke-набор в ask И в bypass**: прямые инструкции,
   «system:»-мимикрия, поддельная tool-call разметка, инструкции в
   HTML-комментариях/alt/скрытом тексте → ни одного side-effect-вызова без
   approval-эскалации; corpus-tainted аргумент эскалируется в critical и в
   bypass.
6. TTL/квоты/cleanup/dedup: истечение, вытеснение LRU, удаление с раном,
   повторный фетч дедупится.
7. Флаг off → старый web-flow бит-в-бит (пиновано); канарейки живы.
8. Promote в Библиотеку сохраняет source=web/url/hash/trust=untrusted.

## W2 — web-документы (✅ реализовано)

`web_fetch(store)` при Content-Type `application/pdf` / `…docx` пропускает байты
через СУЩЕСТВУЮЩИЙ `file_extract` pipeline (pypdf → pdfplumber → OCR :8002 для
сканов; python-docx для DOCX) — без нового провайдера. Извлечённый текст
чистится тем же `_clean_text` (zero-width/control), чанкуется и кладётся в тот
же корпус как обычный документ: trust=untrusted, dedup/квоты/TTL, round-trip
verify_quote — идентично HTML-странице. Пустое извлечение / повреждённый файл /
неподдерживаемый MIME → честный ok=False, ран не падает. Тесты: реальные PDF
(pypdf) и DOCX (python-docx) round-trip + OCR-fallback путь.

## 12. Вне scope (осознанно)

Auth/paywall/капчи; BFS-краулер (W4 — после реальной необходимости); видео;
RAG «всего интернета»; повышение доверия к любому веб-источнику.
