# RepoGitNEWS

Това е подготвената папка за GitHub repo.

## Какво има вътре

- `news.txt`
  Входният файл с новините.

- `review_latest.txt`
  Главният резултат, който ще се генерира.

- `make_review.py`
  Скриптът, който прави обзора.

- `review_scheduler.py`
  Скриптът, който го пуска по график.

- `review_automation.ini`
  Настройките.

- `PROMPT_RULES.md`
  Правилата за генериране.

- `news1.txt`
  Примерен изходен формат.

## Как е настроено

- вход: локалният `news.txt` в repo-то
- главен изход: `review_latest.txt` в repo-то
- първо огледално копие: `C:\OneDrive\VIN TV\NEWS\news1_test.txt`
- второ огледално копие: `C:\OneDrive\VIN TV\YT_INFO\news_bg_test.txt`

## Забележка

Ако искаш новият `news.txt` винаги да идва в repo-то, трябва или:

1. да го копираш там преди пускане, или
2. watcher-ът да записва директно в repo папката, или
3. да има малък helper скрипт, който копира файла в repo-то.
