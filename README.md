# RepoGitNEWS

Това е подготвената папка за GitHub repo и Codex app automation workflow без `OPENAI_API_KEY`.

## Какво има вътре

- `news.txt`
  Входният файл с новините.

- `review_latest.txt`
  Главният резултат, който ще се генерира.

- `repo_sync.py`
  Локален скрипт за sync на `news.txt` към repo-то и за sync на `review_latest.txt` обратно към локалните файлове.

- `repo_sync_scheduler.py`
  Локален scheduler за автоматичните часове и периодичния review pull.

- `review_automation.ini`
  Настройките.

- `PROMPT_RULES.md`
  Правилата за генериране.

- `news1.txt`
  Примерен изходен формат.

- `CODEX_AUTOMATION_PROMPT.md`
  Готовият текст, който трябва да сложиш в Codex app Automation.

## Как е настроено

- източник на новини: `C:\OneDrive\VIN TV\YT_INFO\news.txt`
- repo вход: `RepoGitNEWS\news.txt`
- главен изход: `review_latest.txt` в repo-то
- първо огледално копие: `C:\OneDrive\VIN TV\NEWS\news1_test.txt`
- второ огледално копие: `C:\OneDrive\VIN TV\YT_INFO\news_bg_test.txt`
- автоматични часове: `07:00` и `17:30`

## Какво прави scheduler-ът

При всеки планиран час:

1. копира свежия `news.txt` от `C:\OneDrive\VIN TV\YT_INFO\news.txt` в repo-то;
2. commit/push-ва `news.txt` в GitHub;
3. Codex app automation взима repo-то и обновява `review_latest.txt`;
4. локалният scheduler периодично прави pull;
5. копира `review_latest.txt` и в допълнителните файлове.

## Ръчен тест

Пускане на news sync тест:

```bash
python repo_sync_scheduler.py --config review_automation.ini --once-push
```

Пускане на review pull тест:

```bash
python repo_sync_scheduler.py --config review_automation.ini --once-pull
```
