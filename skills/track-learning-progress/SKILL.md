---
name: track-learning-progress
description: Фиксировать, проверять и показывать доказательный прогресс по учебному треку Senior AI Agent Engineer. Использовать, когда нужно начать тему, продолжить обучение после новой сессии, отметить завершение теории/практики/code tour/проверки понимания, обновить критерий модульного Gate, показать текущий статус или подтвердить прохождение модуля.
---

# Прогресс учебного трека

Использовать `progress/progress.json` как единственный источник истины.
Статус конспекта, наличие commit или успешный тест по отдельности не означают,
что тема пройдена.

## Перед работой

1. Прочитать `AGENTS.md` и секцию текущего модуля в основном плане.
2. Показать и проверить текущую запись:

```bash
uv run --frozen python \
  skills/track-learning-progress/scripts/progress_tracker.py show

uv run --frozen python \
  skills/track-learning-progress/scripts/progress_tracker.py validate
```

3. Продолжать текущую незавершённую тему. Не начинать следующую тему или
   модуль, пока текущий обязательный этап либо модульный `Gate` не завершён.

## Правила доказательств

- Повышать статус только после наблюдаемого результата.
- Для `complete` указывать доказательство подходящего типа: зафиксированный в
  Git непустой артефакт, Git commit, результат выполненной проверки или
  проверенный итог разбора.
- `--artifact` принимается только для файла, который совпадает с версией в
  текущем `HEAD`; трекер сохраняет путь, SHA-256 и полный Git revision.
  Сначала зафиксировать артефакт, затем обновлять прогресс.
- Не считать план, намерение, созданный пустой файл или сообщение ИИ
  доказательством выполнения.
- `verification=complete` записывать только после фактического запуска команды
  и сохранять точный итог.
- `understanding_check=complete` записывать после ответа студента и проверки
  конкретного предсказания, diff, ошибки или результата теста.
- `notes_finalized=complete` записывать только после обновления Markdown,
  пересборки PDF и их проверки.
- При опровержении доказательства переоткрывать этап с объяснением; не
  переписывать историю вручную.

## Обновление стадии

Сначала получить `revision` через `show`, затем передать его как
`--expected-revision`. Примеры:

```bash
uv run --frozen python \
  skills/track-learning-progress/scripts/progress_tracker.py \
  record-stage --expected-revision 1 \
  --module 01 --topic 1.1 --stage guided_code_tour \
  --status complete \
  --review "Разобраны Workflow и DistributedExecutor по одной трассе" \
  --review-result passed

uv run --frozen python \
  skills/track-learning-progress/scripts/progress_tracker.py \
  record-stage --expected-revision 2 \
  --module 01 --topic 1.1 --stage verification \
  --status complete \
  --test-command "uv run --frozen pytest -q lessons/module_01_model_vs_agent/tests" \
  --test-result "44 passed" \
  --test-exit-code 0
```

Для зафиксированных файлов использовать `--artifact`, для commit —
`--commit`. Все пути должны быть относительными корню репозитория.

Чтобы исправить ошибочно закрытый этап, использовать `--reopen` со статусом
`in_progress` или `blocked` и обязательным `--review` с причиной. Старые
доказательства этапа становятся `superseded`; зависимые последующие стадии
атомарно возвращаются в `pending`, а их доказательства также становятся
`superseded`. При переоткрытии критерия старые доказательства критерия
становятся `superseded`. В ответе перечислить все сброшенные стадии.

## Переход к следующей теме

`start-topic` разрешён только после завершения всех обязательных стадий
текущей темы и только для непосредственной следующей темы в `expected_topics`:

```bash
uv run --frozen python \
  skills/track-learning-progress/scripts/progress_tracker.py \
  start-topic --expected-revision 3 \
  --module 01 --topic 1.2 \
  --title "Независимый от поставщика контракт"
```

Команда создаёт только текущую тему, а не пустой каркас будущих модулей.

После прохождения модульного `Gate` начать следующий по номеру двухзначный
модуль через `start-module`. Передать в порядке прохождения все идентификаторы
тем и все критерии из основного плана; команда создаст только первую текущую
тему:

```bash
uv run --frozen python \
  skills/track-learning-progress/scripts/progress_tracker.py \
  start-module --expected-revision 42 \
  --module 02 --title "Структурированные ответы и вызовы tool" \
  --expected-topic 2.1 --expected-topic 2.2 --expected-topic 2.3 \
  --expected-topic 2.4 --expected-topic 2.5 --expected-topic 2.6 \
  --expected-topic 2.7 --expected-topic 2.8 --expected-topic 2.9 \
  --expected-topic 2.10 \
  --first-topic-title "Протокол tool с нуля" \
  --criterion "three_adapters=Три адаптера проходят общие тесты" \
  --criterion "validate_before_execute=Схема и права проверяются первыми" \
  --criterion "secrets_hidden=Секреты не видны LLM" \
  --criterion "parallel_correlation=Результаты сопоставляются по ID" \
  --criterion "observable_once=Тесты отказов подтверждают заявленную границу"
```

## Модульный Gate

Обновлять критерии через `record-criterion`. Отмечать `Gate` пройденным,
только когда:

- все `expected_topics` существуют и все шесть стадий каждой темы имеют
  `status=complete`;
- каждый критерий имеет `status=complete` и активное доказательство;
- ADR и отчёт об оценке существуют, непусты и зафиксированы в Git;
- указанный git tag существует.

Последовательность: зафиксировать ADR и отчёт, создать tag на этом commit,
затем вызвать `pass-gate` с `--adr`, `--evaluation-report`, `--git-tag` и
`--git-revision` этого же commit. После проверки зафиксировать обновление
трекера отдельным commit.

## После изменения

Всегда выполнить:

```bash
uv run --frozen python \
  skills/track-learning-progress/scripts/progress_tracker.py validate

uv run --frozen pytest -q \
  skills/track-learning-progress/tests
```

В ответе сообщить новый `revision`, текущую стадию, добавленные доказательства
и каскадно сброшенные стадии. Не объявлять тему или модуль завершёнными шире,
чем это подтверждает трекер.
