Chainway MIS downloader v13

Oprava proti chybě:
- R1 už nematchuje UR1A ani SR160 jen proto, že obsahují substring "R1"
- model se teď páruje jako samostatný token zařízení

Zůstává:
- stahování podle vybraných skupin
- skip videí a audia
- watchdog na session/login page
- progress stahování
- keepalive proti expirované session
- auto-recovery bez ručního mačkání Enter
- volitelné auto-vyplnění loginu z env proměnných

Volitelné env pro login helper:
- CHAINWAY_MIS_USERNAME
- CHAINWAY_MIS_PASSWORD

Captcha zůstává ruční.

Použití:
pip install -r requirements-chainway-downloader-v13.txt
playwright install chromium
python chainway_cert_downloader_v13.py

UI:
python chainway_downloader_ui.py

Co umí UI:
- vyplnit login
- vybrat download složku
- zadat hledané výrazy
- multiselect skupin z MIS dropdownu
- upravit filtry a runtime nastavení
- uložit config a spustit downloader na pozadí
- sledovat log v prohlížeči

Poznámka:
- `session_recovery_timeout_seconds = 0` znamená čekat bez timeoutu na ruční captcha/login
