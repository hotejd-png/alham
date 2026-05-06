# spy_bot_v3

Улучшенная версия watcher-бота под один публичный Polymarket аккаунт.

Цель: **24/7 мониторинг активности по каждой 5-минутке**, чтобы понять:
- сколько BUY / SELL / MERGE действий он делает;
- как долго он накапливает позицию внутри одной 5-минутки;
- покупает ли обе стороны;
- сколько пар успевает собрать;
- когда именно делает MERGE;
- насколько поздно в окне он продолжает покупать.

## Что исправлено по сравнению с v2

- убран обязательный `profile`, потому что у тебя он давал 404;
- исправлен endpoint closed positions на `/closed-positions`;
- live режим заточен под **activity-first** слежение;
- добавлена группировка **по market_slug + event_slug**;
- добавлены `first_ts`, `last_ts`, `duration_sec`, `late_action`;
- добавлен отчёт по окнам и подробный timeline;
- добавлен экспорт в CSV;
- добавлен stop-on-error без падения всего процесса.

## Быстрый старт (Windows / VS Code)

```bash
pip install -r requirements.txt
python main.py historical --pages 5
python main.py windows-report --limit 50
python main.py timeline-report --limit 200
python main.py live
```

## Рекомендуемый режим

1. Сначала:
```bash
python main.py historical --pages 5
```

2. Затем проверь:
```bash
python main.py windows-report --limit 20
python main.py timeline-report --limit 100
```

3. Потом оставь работать:
```bash
python main.py live
```

## Что смотреть в windows-report

- `buys` / `merges`
- `first_ts` / `last_ts`
- `duration_sec`
- `late_action`

Если `duration_sec` почти весь интервал и есть поздние BUY, значит он действительно пылесосит весь рынок до самого конца окна.

## Важно

Этот бот работает только с публичными данными.
