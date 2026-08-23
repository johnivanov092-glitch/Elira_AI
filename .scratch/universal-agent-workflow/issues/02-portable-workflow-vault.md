# Portable workflow-managed vault and Windows bootstrap

Status: completed

## Implementation result

Portable AES-GCM vault, passphrase/recovery wrapping, Workflow secret cards and
explicit legacy WinCred migration are implemented. The permanent privileged
helper proposed below was deliberately not introduced: Tauri performs each
requested elevated command through the native UAC dialog and binds its result to
the Workflow `request_id`. This preserves full access through the current Windows
token without making restored application state depend on a service, SID, DPAPI
or Credential Manager.

The remaining text is the original planning record.

## Problem Statement

Текущий IT Ops secret vault хранит значения только в Windows Credential Manager
и принимает только Windows-specific backend. После переустановки Windows
application metadata может сохраниться, но Windows-bound credential values не
переносятся автоматически. Пользователь хочет полный доступ агента к Windows,
но не хочет, чтобы переносимость конфигурации, секретов и workflow зависела от
Windows Security state.

Все операции должны запускаться через workflow UI. После чистой установки
Windows допустим один явный UAC bootstrap для регистрации privileged helper, но
последующие agent tasks в режиме `bypass` не должны требовать отдельных product
approvals или elevation prompts.

## Solution

Заменить Windows-bound secret storage одним application-owned portable encrypted
vault. Secret records шифруются authenticated encryption. Random data key
защищается ключом, полученным из пользовательской passphrase через versioned
memory-hard KDF; отдельный recovery key может защитить тот же data key для
аварийного восстановления. Ни data key, ни secret value не сохраняются открыто.

Workflow предоставляет create, unlock, lock, status, backup, restore, rotate и
legacy migration operations. Sensitive values вводятся только через write-only
inline forms. Encrypted backup bundle содержит vault, необходимые metadata stores
и manifest/checksums. После восстановления на чистой Windows пользователь вводит
passphrase или recovery key, а прежние `secret_ref` продолжают работать.

Для административных Windows operations workflow использует существующий
privileged helper/change-executor. Если helper отсутствует после переустановки,
runtime возвращает structured `needs_elevation`, UI запускает один UAC bootstrap,
а затем workflow продолжает выполнение через authenticated loopback IPC.

## Commits

1. Добавить external-behavior tests текущего vault lifecycle и stable
   `secret_ref` contract.
2. Добавить portable vault interface и typed errors, не переключая production
   backend.
3. Добавить versioned envelope schema с authenticated metadata и forward-format
   rejection.
4. Добавить passphrase KDF policy с сохраняемыми versioned parameters и
   калибруемыми resource limits.
5. Добавить генерацию random data key и passphrase wrapping.
6. Добавить authenticated encryption/decryption secret records с уникальным
   nonce и AAD, связанным с `secret_ref`, kind и format version.
7. Добавить атомарную запись через temporary file, fsync/replace и
   восстановимую предыдущую версию.
8. Добавить locked/unlocked runtime state, inactivity timeout и удаление
   in-memory references на lock/shutdown.
9. Добавить create/unlock/lock/status workflow operations и inline write-only
   forms.
10. Добавить recovery-key wrapping и одноразовый recovery export flow.
11. Добавить encrypted backup manifest с allowlist необходимых stores и
    исключением logs, caches, model files и plaintext key material.
12. Добавить restore validation в staging directory до атомарного переключения
    active data.
13. Добавить fresh-install restore test без Windows Security state.
14. Добавить key/passphrase rotation без изменения `secret_ref` identities.
15. Добавить read-only legacy WinCred adapter только для явного migration flow.
16. Добавить поэлементную migration saga с проверенным portable round-trip до
    удаления legacy credential.
17. Добавить crash/retry tests для каждого шага migration saga.
18. Переключить новые secret writes на portable backend за rollback switch.
19. Переключить secret resolution на portable backend с telemetry legacy reads.
20. Провести backup/restore drill и reconciliation всех secret references.
21. Удалить WinCred backend после нулевой legacy telemetry и подтверждённого
    migration report.
22. Добавить privileged-helper status/install/repair/re-register runtime
    operations и structured `needs_elevation` result.
23. Добавить inline UAC bootstrap card, доступную только из локального workflow
    UI с явным user gesture.
24. Добавить fresh-install test: restore portable state, bootstrap helper один
    раз, выполнить административную Windows operation в `bypass` без повторного
    approval/elevation prompt.
25. Заменить Windows-only vault tests platform-independent crypto, migration и
    restore tests; Windows-specific оставить только для helper bootstrap.

## Decision Document

- Единственный целевой secret backend — portable application-owned vault.
- Vault не использует Windows Credential Manager, DPAPI, Windows account SID или
  machine-bound Windows key storage.
- Используется уже присутствующая core crypto dependency; новый crypto framework
  не вводится.
- Рекомендуемые primitives: AES-256-GCM для authenticated encryption и scrypt
  для passphrase-derived wrapping key.
- Crypto format и KDF parameters versioned; неизвестная версия не дешифруется
  best-effort.
- Random data key отделён от passphrase, поэтому смена passphrase не меняет
  secret references.
- Recovery key хранится пользователем отдельно и никогда не включается в тот же
  backup bundle в открытом виде.
- Decrypted key и secret values существуют только в runtime memory.
- Модель видит только opaque `secret_ref` и lifecycle state.
- Все vault и migration actions запускаются workflow и используют inline secure
  UI cards.
- Workflow UI является единственным источником product-level approval; UAC —
  отдельное OS-level подтверждение только для bootstrap/repair helper.
- Restore сначала полностью валидируется в staging location и только затем
  атомарно становится active.
- Existing WinCred value можно мигрировать только до переустановки Windows, пока
  старый credential доступен.
- Полный Windows access технически обеспечивается privileged Windows service
  token, а не обходом Windows kernel security.
- `bypass` снимает product-level approvals/scopes после bootstrap, но не подделывает
  и не обходит OS security token.
- После чистой установки нужен один UAC bootstrap; дальше workflow использует
  authenticated loopback IPC.

## Testing Decisions

- Тестировать observable vault behavior, backup compatibility, restore result и
  отсутствие утечек, а не внутреннее расположение crypto helpers.
- Проверять правильную passphrase, неправильную passphrase, modified ciphertext,
  modified AAD, truncated file, duplicate nonce prevention и unknown format
  version.
- Проверять отсутствие plaintext secret и unwrapped key в vault, backup,
  metadata stores, logs, transcript, events, journal и model context.
- Проверять atomic write и restore interruption на каждой filesystem boundary.
- Проверять restore в пустом data directory без Credential Manager entries и на
  platform-neutral CI.
- Проверять, что backup/restore и passphrase rotation сохраняют все
  `secret_ref`, asset и connection bindings.
- Проверять migration ordering и idempotent retry после сбоя каждого шага.
- Проверять локальность, authentication и replay resistance helper IPC.
- Проверять один UAC bootstrap после fresh install и отсутствие повторных UAC/
  product approvals для зарегистрированных Windows operations в `bypass`.
- Использовать существующие IT Ops vault, redaction, secure-intake, migration,
  change-executor и startup-recovery suites как prior art.

## Out of Scope

- Cloud-hosted secret manager.
- Синхронизация decrypted secrets между устройствами.
- Хранение passphrase или recovery key рядом с encrypted vault.
- Восстановление WinCred value после потери исходной Windows installation без
  заранее созданного portable backup.
- Передача секретов модели или управление vault через обычное текстовое поле
  чата.
- Удаление privileged IT Ops executor boundary.
- Обход Windows UAC, service ACL или process token средствами exploit/bypass.

## Further Notes

Это обязательная ранняя миграция до удаления старых Settings surfaces. Сначала
нужно дать пользователю workflow «перенести секреты и создать backup», иначе
удаление WinCred backend сделает существующие credentials недоступными.
