Chainway network probe

1) pip install -r requirements-chainway-network-probe.txt
2) playwright install chromium
3) python chainway_network_probe.py

Pak:
- ručně login
- otevřít 文档下载 / Document Download
- zadat C61
- Search
- Next page
- jeden Download

Po ukončení pošli zpět:
- network_log.txt
nebo
- chainway_probe.har

Podle toho půjde udělat přesný downloader přes backend requesty.
